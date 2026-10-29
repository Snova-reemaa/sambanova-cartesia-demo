"""The Helm chart, rendered with Auth0 off (the default) and on.

Off has to be exactly what is deployed now: no proxy, no auth annotations,
no new settings. On has to wire the sign-in proxy so that every path a
sign-in needs is open, take its credentials only from the key Secret, and
never put a value of one in the chart.

Offline: helm template renders locally and talks to no cluster.
"""

import os
import shutil
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = os.path.join(ROOT, "deploy", "helm", "voxnova")
HOST = "aiskshowcase1.cloud.snova.ai"
BASE = ["--set", "image.tag=test", "--set", f"ingress.host={HOST}"]
ON = BASE + ["--set", "auth.enabled=true",
             "--set", "auth.issuerUrl=https://example-tenant.us.auth0.com/"]


def render(args):
    done = subprocess.run(["helm", "template", "voxnova", CHART, *args],
                          capture_output=True, text=True)
    docs = [d for d in yaml.safe_load_all(done.stdout) if d] if not done.returncode else []
    return done.returncode, docs, done.stderr


def find(docs, kind, name):
    return next((d for d in docs if d["kind"] == kind
                 and d["metadata"]["name"] == name), None)


def ports(rule):
    return {p["port"] for p in rule.get("ports", [])}


def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    if not shutil.which("helm"):
        print("  FAIL helm is not installed\n\n  0/1 correct")
        return 1

    code, off, _ = render(BASE)
    check("renders with Auth0 off", code == 0 and off)
    check("off: no sign-in proxy of any kind",
          not any("oauth2" in d["metadata"]["name"] for d in off))
    app = find(off, "Deployment", "voxnova")
    env = {e["name"] for e in app["spec"]["template"]["spec"]["containers"][0]["env"]}
    check("off: the app is not told to expect Auth0",
          not {"AUTH_PROXY", "AUTH_CHECK_URL"} & env)
    notes = find(off, "Ingress", "voxnova")["metadata"]["annotations"]
    check("off: the ingress has no auth annotations",
          not any("auth-" in k for k in notes))

    code, on, _ = render(ON)
    check("renders with Auth0 on", code == 0 and on)
    proxy = find(on, "Deployment", "voxnova-oauth2-proxy")
    check("on: there is a sign-in proxy", proxy)
    box = proxy["spec"]["template"]["spec"]["containers"][0]
    refs = {e["name"]: e.get("valueFrom", {}).get("secretKeyRef")
            for e in box["env"]}
    check("on: client id, secret and cookie secret come from voxnova-keys",
          refs.get("OAUTH2_PROXY_CLIENT_ID") == {"name": "voxnova-keys", "key": "AUTH0_CLIENT_ID"}
          and refs.get("OAUTH2_PROXY_CLIENT_SECRET") == {"name": "voxnova-keys", "key": "AUTH0_CLIENT_SECRET"}
          and refs.get("OAUTH2_PROXY_COOKIE_SECRET") == {"name": "voxnova-keys", "key": "AUTH0_COOKIE_SECRET"})
    values = open(os.path.join(CHART, "values.yaml")).read().lower()
    check("on: no credential is a value in the chart",
          not any(k in values for k in ("client_secret:", "clientsecret:",
                                         "cookie_secret:", "cookiesecret:",
                                         "client_id:", "clientid:")))
    check("on: the callback is the one registered with Auth0",
          f"--redirect-url=https://{HOST}/oauth2/callback" in box["args"])

    app = find(on, "Deployment", "voxnova")
    env = {e["name"]: e.get("value") for e in
           app["spec"]["template"]["spec"]["containers"][0]["env"]}
    check("on: the app knows to expect Auth0", env.get("AUTH_PROXY") == "1")
    check("on: and asks the proxy's Service on 4180 who signed in",
          env.get("AUTH_CHECK_URL") == "http://voxnova-oauth2-proxy:4180/oauth2/auth")
    notes = find(on, "Ingress", "voxnova")["metadata"]["annotations"]
    check("on: every request is checked with the proxy first",
          notes.get("nginx.ingress.kubernetes.io/auth-url") == "https://$host/oauth2/auth"
          and "oauth2/start" in notes.get("nginx.ingress.kubernetes.io/auth-signin", ""))
    route = find(on, "Ingress", "voxnova-oauth2-proxy")
    check("on: /oauth2 on the same host goes to the proxy",
          route and route["spec"]["rules"][0]["host"] == HOST
          and route["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/oauth2")

    # The paths a sign-in needs, through the NetworkPolicies.
    app_policy = find(on, "NetworkPolicy", "voxnova")
    check("on: the app's outbound traffic is not restricted, so it reaches the proxy",
          "Egress" not in app_policy["spec"]["policyTypes"])
    gate = find(on, "NetworkPolicy", "voxnova-oauth2-proxy")
    check("on: the proxy has its own policy", gate)
    rules = gate["spec"]["ingress"]
    from_app = [r for r in rules if 4180 in ports(r) and any(
        f.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name")
        == "voxnova" for f in r.get("from", []))]
    check("on: the proxy admits the app on 4180", from_app)
    check("on: and the ingress controller on 4180 (any source: it is on the host)",
          any(4180 in ports(r) and not r.get("from") for r in rules))
    check("on: the proxy's outbound is not restricted, so it reaches Auth0 on 443",
          "Egress" not in gate["spec"]["policyTypes"])
    check("on: the app's policy does not select the proxy",
          app_policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
          == "voxnova"
          and proxy["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"]
          == "voxnova-oauth2-proxy")

    code, _, why = render(BASE + ["--set", "auth.enabled=true"])
    check("on without an issuer URL refuses to render",
          code != 0 and "auth.issuerUrl" in why)

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
