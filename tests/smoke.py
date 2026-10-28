"""End to end against a running build, the way a tester would use it.

Point it at a deployment:  VOXNOVA=https://host VOXNOVA_PASSPHRASE=... \\
      venv/bin/python tests/smoke.py

Needs a real SambaNova key on the server and the demo inbox switched
on. Not part of the default run — it checks a deployment, not the code.

It calls as a test-only customer, seeded before the first check and
removed with memory.forget() after the last, so the "forget me" check
deletes nobody real. It used to call as r.patel@example.com, and against
a deployment that deleted the customer the demo is built around. Seeding
needs Python beside the server's database, which VOXNOVA_EXEC says how to
reach:

  (unset)                                   server.py run from this checkout
  docker exec voxnova-local                 a local container
  kubectl --context default -n default exec deploy/voxnova --    the pod
"""

import os
import asyncio, json, re, shlex, subprocess, sys
import aiohttp
B = os.getenv("VOXNOVA", "http://127.0.0.1:8086").rstrip("/")
P = os.getenv("VOXNOVA_PASSPHRASE", "smoke-test")
EXEC = shlex.split(os.getenv("VOXNOVA_EXEC", ""))
# .invalid can never be registered, so this address belongs to nobody.
WHO = "smoke-test@example.invalid"
SEED = f"""
import time, memory
memory.forget({WHO!r})
with memory._lock, memory._connect() as db:
    db.execute("INSERT INTO customers (email, name, verified) VALUES (?, 'Smoke Test', 1)", ({WHO!r},))
    db.execute("INSERT INTO calls (email, at, summary, outcome, actions, sentiment) "
               "VALUES (?, ?, 'Asked whether the Kelo travel kettle comes in blue.', "
               "'resolved', 'none', 'friendly')", ({WHO!r}, time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() - 86400))))
print("seeded", {WHO!r})
"""
CLEAN = f"import memory; print('cleaned up', memory.forget({WHO!r}))"
def beside_server(code):
    """Run Python where the server's database is. Raises if it fails."""
    cmd = EXEC + ["python", "-c", code] if EXEC else [sys.executable, "-c", code]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(cmd, cwd=here, capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise SystemExit(f"  could not reach the server's database: {out.stderr.strip()[-300:]}")
    return out.stdout.strip()
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
    print(" ", beside_server(SEED))
    try:
        return await checks()
    finally:
        print(" ", beside_server(CLEAN))
async def checks():
    results=[]
    jar=aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(cookie_jar=jar) as http:
        # login
        async with http.post(f"{B}/login", data={"passphrase":P,"consent":"yes"}, allow_redirects=False) as r:
            ok = r.status==302 and any("session" in c for c in r.cookies)
        results.append(line("login", ok))
        # verify via demo inbox
        async with http.ws_connect(f"{B}/ws") as ws:
            ready = json.loads((await ws.receive()).data)
            # Every other check here is text, so an image that could not
            # speak at all (cartesia without its websockets extra) passed
            # 7/7 and went live as "ready, text only".
            results.append(line("voice is on", ready.get("audio") is True,
                                (ready.get("audio_trouble") or "")[:90]))
            a = await say(ws, f"hi it's {WHO}")
            code = a["demo"]
            b = await say(ws, f"the code is {code}") if code else {"caller":None}
            ok = bool(code) and b["caller"] and b["caller"][0]=="verified"
        results.append(line("verify via demo inbox", ok, f"code shown, {b['caller']}" if ok else "no code or not verified"))
        # hang up, call back, remembered
        async with http.ws_connect(f"{B}/ws") as ws:
            await ws.receive()
            c = await say(ws, f"hi it's {WHO}")
            d = await say(ws, f"{c['demo']}") if c["demo"] else {"reply":""}
            e = await say(ws, "what did we talk about last time")
            ok = bool(re.search(r"kettle|blue", (e["reply"] or ""), re.I))
        results.append(line("call back and it remembers me", ok, (e["reply"] or "")[:60]))
        # forget me
        async with http.ws_connect(f"{B}/ws") as ws:
            await ws.receive()
            f1 = await say(ws, f"hi it's {WHO}")
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
        # The call screen pins itself to the window with overflow:hidden on
        # body, and the policy pages inherited it: everything below the fold
        # was unreachable, with no scrollbar and nothing to drag. No browser
        # here, so this checks the contract that made it work rather than the
        # rendering — the pages still carry the class, and the class still
        # releases the lock. A real browser at 1280x460 and 375x812 is what
        # proved the pixels.
        async with http.get(f"{B}/static/style.css") as r: css = await r.text()
        rule = re.search(r"body\.policy\s*\{[^}]*\}", css)
        why = []
        if not rule:
            why.append("no body.policy rule")
        else:
            for need in ("overflow: visible", "height: auto"):
                if need not in rule.group(0):
                    why.append(f"body.policy lacks {need}")
        for path in ("/terms", "/privacy"):
            async with http.get(f"{B}{path}") as r: page = await r.text()
            if 'class="policy"' not in page:
                why.append(f"{path} is not class=policy")
        results.append(line("policy pages can scroll", not why, "; ".join(why[:2])))
    print(f"\n  {sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1
raise SystemExit(asyncio.run(main()))
