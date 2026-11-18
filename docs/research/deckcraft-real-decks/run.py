import sys, os, time, json, subprocess, socket, datetime
sys.path.insert(0, sys.argv[1]+"/scripts"); import dc
LA=sys.argv[1]+"/la"; tag=sys.argv[2]; out=f"{LA}/out/{tag}"; os.makedirs(out,exist_ok=True)
log={"tag":tag,"start":datetime.datetime.now().isoformat(),"env_has_tty":os.isatty(0),"pid":os.getpid(),"ppid":os.getppid()}
def sh(*a,timeout=30):
    try: r=subprocess.run(list(a),capture_output=True,text=True,timeout=timeout); return {"rc":r.returncode,"out":r.stdout.strip()[-400:],"err":r.stderr.strip()[-400:]}
    except subprocess.TimeoutExpired: return {"rc":"timeout"}
log["console"]=sh("/bin/sh","-c","ioreg -n Root -d1 | grep -E 'IOConsoleLocked|IOConsoleUsers' | head -c 600")
log["idle"]=sh("/bin/sh","-c","ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print int($NF/1e9); exit}'")
PORT=7992
def up():
    try: socket.create_connection(("127.0.0.1",PORT),timeout=0.2).close(); return True
    except OSError: return False
dcres={}
try:
    t=time.time(); log["dc_launch"]=sh("/usr/bin/open","-g","-j","-n","--env","DECKCRAFT_NO_PREFS=1","--env","DECKCRAFT_NO_RECOVERY=1","-a","/Applications/DeckCraft.app","--args","--control",str(PORT))
    while not up() and time.time()-t<30: time.sleep(0.05)
    dcres["launch_s"]=round(time.time()-t,2); dcres["port_up"]=up()
    for deck in ["chapter25_4e","Cryptography","risk"]:
        t=time.time(); r=dc.cmd("file.open",{"path":f"{LA}/work/{deck}.pptx"},port=PORT)
        e=dc.cmd("file.export",{"path":f"{out}/{deck}.dc.pdf","includeHidden":True,"dpi":96},port=PORT)
        p=dc.cmd("file.export",{"path":f"{out}/{deck}.png","all":True},port=PORT)
        dc.cmd("file.close",port=PORT)
        dcres[deck]={"open_ok":r.get("ok"),"lost":r.get("result",{}).get("lostSlides"),"dmg":r.get("result",{}).get("damagedMedia"),"pdf":e.get("ok"),"err":e.get("error"),"png":len(p.get("result",{}).get("files",[])),"s":round(time.time()-t,2)}
    dcres["quit"]=dc.call("app.quit",port=PORT).get("ok")
except Exception as ex: dcres["exception"]=repr(ex)
log["deckcraft"]=dcres
kn={}
for deck in ["chapter25_4e"]:
    o=f"{out}/{deck}.key.pdf"; open(o,"w").close(); t=time.time()
    kn[deck]=sh("/usr/bin/osascript",f"{sys.argv[1]}/scripts/keynote-export.applescript",f"{LA}/work/{deck}.pptx",o,timeout=180); kn[deck]["s"]=round(time.time()-t,2); kn[deck]["bytes"]=os.path.getsize(o)
log["keynote"]=kn; log["end"]=datetime.datetime.now().isoformat()
json.dump(log,open(f"{out}/result.json","w"),indent=1)
