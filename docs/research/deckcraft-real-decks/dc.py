import socket, json, sys
def call(method, params=None, port=7991, timeout=300):
    s=socket.create_connection(("127.0.0.1",port),timeout=timeout); f=s.makefile("rw")
    f.write(json.dumps({"id":1,"method":method,"params":params or {}})+"\n"); f.flush()
    r=f.readline(); s.close(); return json.loads(r)
if __name__=="__main__":
    print(json.dumps(call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv)>2 else {}), indent=1)[:4000])
def cmd(cid, params=None, **kw):
    return call("command", {"id":cid, "params":params or {}}, **kw)
