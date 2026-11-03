"""Runs the whole path on AI-made frames, with all three ways to make a rule.

Wonder writes the rules, the models check frames, and the rule code reacts in its sandbox.
This uses the paid API. Actions are recorded, not run.
"""

import base64
import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import actions
import server

PORT = 8799
GAP_S = 0.35

ran = []
actions.run = ran.append
server.PORT = PORT
app = server.App(server.Client(server.load_key(server.ENV_FILE)))
server.Handler.app = app
app.start_ticks()
http = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=http.serve_forever, daemon=True).start()


def frame(name):
    return (ROOT / "testdata/frames" / f"{name}_384.jpg").read_bytes()


def b64(name, count):
    return [base64.b64encode(frame(name)).decode()] * count


def call(path, data=None, headers=None, method=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def start(sentence, example=None, idle=None):
    body = json.dumps({"sentence": sentence, "example": example, "idle": idle}).encode()
    return call("/api/rule", body, {"Content-Type": "application/json"})["job"]


jobs = {
    "sentence only": start("Count how many times I show a thumbs up."),
    "sentence + recording": start("Tell me each time I raise an open palm, and show how many seconds passed since the last time.",
                                  b64("open_palm", 6), b64("keyboard", 3)),
    "recording only": start("", b64("mug", 6), b64("keyboard", 3)),
}
started = time.time()
results = {}
while len(results) < len(jobs) and time.time() - started < 500:
    for label, job_id in jobs.items():
        if label not in results:
            job = call(f"/api/job/{job_id}")
            if job["status"] != "working":
                results[label] = job
    time.sleep(2)
for label, job in results.items():
    print(f"\n### {label}: {job['status']}, ${job.get('cost')}, error: {job.get('error')!r}")
    for step in job["steps"]:
        print("  -", step[:110])
print(f"\nwriting took {time.time() - started:.0f} s")

state = call("/api/state")
for rule in state["rules"]:
    print(f"\nrule: {rule['name']} | {rule['sentence'][:80]}\n  plan: {rule['plan']}\n  sensors: {[(s['id'], s['target'], s['question'][:70]) for s in rule['sensors']]}")

SCRIPT = [("keyboard", 3), ("thumbs_up", 4), ("keyboard", 4), ("open_palm", 4), ("keyboard", 4),
          ("thumbs_up", 4), ("keyboard", 4), ("open_palm", 4), ("keyboard", 4), ("mug", 5), ("keyboard", 5)]
t = time.time()
for name, count in SCRIPT:
    data = frame(name)
    for _ in range(count):
        t = max(t + GAP_S, time.time())
        call("/api/frame", data, {"Content-Type": "image/jpeg", "X-Frame-Time": str(t)})
time.sleep(2)
state = call("/api/state")
print("\nafter the frames:")
for rule in state["rules"]:
    print(f"  {rule['name']}: values {rule['values']}, fired {rule['fired']}")
print("feed:", [(f["rule"], f["kind"], f["text"][:70]) for f in state["feed"]][:14])
print("actions run:", len(ran), "| spend", state["spend_usd"])
for rule in state["rules"]:
    call(f"/api/rule/{rule['id']}", method="DELETE")
http.shutdown()
