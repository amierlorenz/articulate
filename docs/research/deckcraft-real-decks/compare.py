import sys, os, re, json, collections, pypdfium2 as pdfium
T=sys.argv[1]
def words(s): return re.findall(r'[a-z0-9]+', s.lower().replace('ﬁ','fi').replace('ﬂ','fl'))
def rec(ref,hyp):
    r,h=collections.Counter(ref),collections.Counter(hyp); t=sum(r.values())
    return sum(min(c,h[w]) for w,c in r.items())/t if t else None
def pages(p):
    d=pdfium.PdfDocument(p); out=[]
    for pg in d:
        out.append((pg.get_textpage().get_text_range(), pg.get_size()))
    return out
rows=[]
for name in sorted(os.listdir(f"{T}/out/cold")):
    dcp=f"{T}/out/cold/{name}/dc.pdf"; kp=f"{T}/out/keynote/{name}.pdf"
    if not os.path.exists(dcp): continue
    a=pages(dcp); b=pages(kp) if os.path.exists(kp) else []
    ins=json.load(open(f"{T}/out/cold/{name}/inspect.json"))
    insw=[words("\n".join([sh.get("text","") for sh in (s or {}).get("shapes",[]) if isinstance(sh,dict)])) for s in ins["slides"]]
    lowk=[];lowd=[]; kr=[];dr=[]; ir=[]
    for i in range(min(len(a),len(b))):
        wa,wb=words(a[i][0]),words(b[i][0])
        r1=rec(wb,wa); r2=rec(wa,wb)
        if r1 is not None: dr.append(r1); 
        if r1 is not None and r1<0.8: lowd.append((i+1,round(r1,2),len(wb)))
        if r2 is not None: kr.append(r2)
        if r2 is not None and r2<0.8: lowk.append((i+1,round(r2,2),len(wa)))
    for i in range(min(len(a),len(insw))):
        r=rec(words(a[i][0]),insw[i])
        if r is not None: ir.append(r)
    m=lambda x: round(sum(x)/len(x),3) if x else None
    print(f"== {name}: dc pages={len(a)} size={a[0][1] if a else None} | keynote pages={len(b)} size={b[0][1] if b else None}")
    print(f"   mean recall of Keynote words in DC text layer={m(dr)}; DC words in Keynote={m(kr)}; DC pdf words in slide.inspect text={m(ir)}")
    print(f"   pages where DC misses >20% of Keynote words: {lowd[:12]}")
    print(f"   pages where Keynote misses >20% of DC words: {lowk[:12]}")
