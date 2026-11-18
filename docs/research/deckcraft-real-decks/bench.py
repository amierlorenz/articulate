import sys, os, time, json, subprocess, socket
sys.path.insert(0, os.path.dirname(__file__)); import dc
PORT=7991; OUT=sys.argv[1]; MODE=sys.argv[2]; decks=sys.argv[3:]
def up():
    try: socket.create_connection(("127.0.0.1",PORT),timeout=0.2).close(); return True
    except OSError: return False
def launch():
    t=time.time()
    subprocess.run(["open","-g","-j","-n","--env","DECKCRAFT_NO_PREFS=1","--env","DECKCRAFT_NO_RECOVERY=1","-a","/Applications/DeckCraft.app","--args","--control",str(PORT)],check=True)
    while not up(): time.sleep(0.05)
    return time.time()-t
def running(): return subprocess.run(["pgrep","-x","DeckCraft"],capture_output=True).returncode==0
def quit_():
    r=dc.call("app.quit"); t=time.time()
    while running() and time.time()-t<15: time.sleep(0.05)
    r["exit_s"]=round(time.time()-t,2); r["still_running"]=running(); return r
res=[]
for deck in decks:
    name=os.path.splitext(os.path.basename(deck))[0].replace(" ","_")
    d=os.path.join(OUT,name); os.makedirs(d+"/png",exist_ok=True)
    rec={"deck":deck,"mode":MODE}
    T0=time.time()
    if MODE=="cold":
        if up(): rec["prequit"]=quit_()
        assert not running()
        rec["launch_s"]=round(launch(),2)
    t=time.time(); r=dc.cmd("file.open",{"path":deck}); rec["open_s"]=round(time.time()-t,2); rec["open"]=r
    if not r.get("ok"): res.append(rec); continue
    t=time.time(); ins=dc.call("document.inspect")["result"]; rec["inspect_s"]=round(time.time()-t,2)
    n=len(ins["slides"]); slides=[]
    t=time.time()
    for i in range(n): slides.append(dc.cmd("slide.inspect",{"index":i}).get("result"))
    rec["slide_inspect_s"]=round(time.time()-t,2)
    json.dump({"document":ins,"slides":slides},open(d+"/inspect.json","w"),indent=1)
    t=time.time(); rec["pdf"]=dc.cmd("file.export",{"path":d+"/dc.pdf"}); rec["pdf_s"]=round(time.time()-t,2)
    t=time.time(); r=dc.cmd("file.export",{"path":d+"/png/s.png","all":True}); rec["png_s"]=round(time.time()-t,2)
    rec["png_n"]=len(r.get("result",{}).get("files",[])) if r.get("ok") else r
    rec["close"]=dc.cmd("file.close")
    if MODE=="cold": rec["quit"]=quit_()
    rec["total_s"]=round(time.time()-T0,2); rec["slides"]=n
    rec["hidden"]=[s["index"]+1 for s in ins["slides"] if s["hidden"]]
    rec["builds"]={s["index"]+1:s["animations"] for s in ins["slides"] if s["animations"]}
    res.append(rec); print(json.dumps({k:v for k,v in rec.items() if k not in("open",)})[:600], flush=True)
json.dump(res,open(os.path.join(OUT,f"bench-{MODE}.json"),"w"),indent=1)
