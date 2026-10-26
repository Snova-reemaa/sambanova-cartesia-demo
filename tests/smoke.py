"""End to end against a running build, the way a tester would use it.

Point it at a deployment:  VOXNOVA=https://host VOXNOVA_PASSPHRASE=... \\
      venv/bin/python tests/smoke.py

Needs a real SambaNova key on the server and the demo inbox switched
on. Not part of the default run — it checks a deployment, not the code.
"""

import os
import asyncio, json, re, sys
import aiohttp
B = os.getenv("VOXNOVA", "http://127.0.0.1:8086").rstrip("/")
P = os.getenv("VOXNOVA_PASSPHRASE", "smoke-test")
async def say(ws, text):
    await ws.send_json({"type":"prompt","text":text,"call":False,"search":False})
    out={"reply":None,"caller":None,"demo":None}
    while True:
        try: m=await asyncio.wait_for(ws.receive(), timeout=70)
        except asyncio.TimeoutError: break
        if m.type is not aiohttp.WSMsgType.TEXT: continue
        e=json.loads(m.data)
        if e["type"]=="caller":
            out["caller"]=(e.get("status"), e.get("name")); out["demo"]=e.get("demo_code") or out["demo"]
        if e["type"] in ("done","error"): out["reply"]=e.get("reply") or e.get("message"); break
    return out
def line(name, ok, extra=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — '+extra) if extra else ''}")
    return ok
async def main():
    results=[]
    jar=aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(cookie_jar=jar) as http:
        # login
        async with http.post(f"{B}/login", data={"passphrase":P,"consent":"yes"}, allow_redirects=False) as r:
            ok = r.status==302 and any("session" in c for c in r.cookies)
        results.append(line("login", ok))
        # verify via demo inbox
        async with http.ws_connect(f"{B}/ws") as ws:
            await ws.receive()
            a = await say(ws, "hi it's Patel")
            code = a["demo"]
            b = await say(ws, f"the code is {code}") if code else {"caller":None}
            ok = bool(code) and b["caller"] and b["caller"][0]=="verified"
        results.append(line("verify via demo inbox", ok, f"code shown, {b['caller']}" if ok else "no code or not verified"))
        # hang up, call back, remembered
        async with http.ws_connect(f"{B}/ws") as ws:
            await ws.receive()
            c = await say(ws, "hi it's Patel")
            d = await say(ws, f"{c['demo']}") if c["demo"] else {"reply":""}
            e = await say(ws, "what did we talk about last time")
            ok = bool(re.search(r"44812|headphone|order", (e["reply"] or ""), re.I))
        results.append(line("call back and it remembers me", ok, (e["reply"] or "")[:60]))
        # forget me
        async with http.ws_connect(f"{B}/ws") as ws:
            await ws.receive()
            f1 = await say(ws, "hi it's Patel")
            f2 = await say(ws, f"{f1['demo']}") if f1["demo"] else {}
            f3 = await say(ws, "delete my data please")
            ok = f3["caller"] and f3["caller"][0]=="forgotten"
        results.append(line("forget me works", ok, (f3["reply"] or "")[:50]))
        # fonts local + no external hosts
        bad=[]
        for path in ("/","/login","/terms","/privacy","/static/style.css","/static/fonts/fonts.css","/static/app.js"):
            async with http.get(f"{B}{path}") as r: body=await r.text()
            for hit in set(re.findall(r"https?://[\w.-]+", body)):
                if not re.search(r"127\.0\.0\.1|localhost|github|schibsted|googlefonts|sil\.org|openfontlicense", hit):
                    bad.append(f"{path}:{hit}")
        results.append(line("no external hosts", not bad, "; ".join(bad[:2])))
        async with http.get(f"{B}/static/fonts/schibsted-grotesk-400-latin.woff2") as r:
            ok = r.status==200 and int(r.headers.get("Content-Length","0"))>1000
        results.append(line("fonts served locally", ok))
    print(f"\n  {sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1
raise SystemExit(asyncio.run(main()))
