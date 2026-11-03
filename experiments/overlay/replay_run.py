"""Plays saved camera frames through the real server, at their original pace.

A rule written for one run can then be tested on real frames, with no camera and no person.
Usage: replay_run.py <log folder> <first frame> <last frame> <sentence, or rule:<id>>
Or give parts to join: replay_run.py <log folder> 100-255,321-433 <sentence, or rule:<id>>
The server must run on port 8787. This takes over as the active page.
"""

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

BASE = f"http://127.0.0.1:{os.environ.get('WHENEVER_PORT', 8787)}"
SPEED_LIMIT_S = 3.0

logs, sentence = Path(sys.argv[1]), sys.argv[-1]
parts = sys.argv[2].split(",") if "-" in sys.argv[2] else [f"{sys.argv[2]}-{sys.argv[3]}"]
spans = [tuple(int(n) for n in part.split("-")) for part in parts]


def call(path, body=None, headers=None, method=None):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


if sentence.startswith("rule:"):
    rule_id = sentence[5:]
else:
    print("writing the rule:", sentence, flush=True)
    started = time.time()
    job = call("/api/rule", {"sentence": sentence})["job"]
    while True:
        status = call(f"/api/job/{job}")
        if status["status"] != "working":
            break
        time.sleep(2)
    if status["status"] == "failed":
        sys.exit("rule failed: " + status["error"])
    rule_id = status["rule"]
    print(f"rule ready in {time.time() - started:.0f}s, cost ${status['cost']}", flush=True)
state = call("/api/state?after=99999&results=99999")
rule = next(r for r in state["rules"] if r["id"] == rule_id)
print("plan:", rule["plan"], flush=True)
for sensor in rule["sensors"]:
    print("sensor:", {k: sensor[k] for k in ("id", "target", "outline", "pattern", "requires", "min_motion")}, flush=True)
with urllib.request.urlopen(f"{BASE}/api/rule/{rule_id}/code") as response:
    print("----- rule.py -----\n" + response.read().decode() + "\n-------------------", flush=True)

rows = [json.loads(line) for line in (logs / "frames.jsonl").open()]
rows = [row for first, last in spans for row in rows if first <= int(row["name"][:-4]) <= last]
print(f"playing {len(rows)} frames over {rows[-1]['t'] - rows[0]['t']:.0f}s", flush=True)
# Claim the camera only now. An open page can take it back while Wonder writes.
page = uuid.uuid4().hex[:12]
call("/api/claim", {"page": page})
for row, following in zip(rows, rows[1:] + [None]):
    jpeg = (logs / "frames" / row["name"]).read_bytes()
    sent = call("/api/frame", jpeg, {"Content-Type": "image/jpeg", "X-Frame-Time": str(time.time()),
                                     "X-Page": page, "X-Motion": str(row["motion"])})
    if sent.get("inactive"):
        sys.exit("Another page took the camera, so the replay stopped.")
    sharp = logs / "frames" / "sharp" / row["name"]
    if sharp.exists() and sent.get("name"):
        call(f"/api/frame/{sent['name']}/sharp", sharp.read_bytes(), {"Content-Type": "image/jpeg", "X-Page": page})
    if following:
        time.sleep(max(min(following["t"] - row["t"], SPEED_LIMIT_S), 0))

# A rule may wait for later frames and models after the last frame.
for _ in range(45):
    time.sleep(2)
    state = call("/api/state?after=0&results=99999")
    latest = next((r for r in state["rules"] if r["id"] == rule_id), None)
    if not any(item["kind"] == "step" for item in state["feed"][-1:]):
        break
print("values:", latest["values"] if latest else None)
for item in state["feed"]:
    if item["rule"] != rule["name"]:
        continue
    shown = {k: v for k, v in item.items() if k not in ("id", "rule", "outline")}
    if item["kind"] == "outline":
        outline = item.get("outline")
        shown["box"] = [outline[k] for k in ("x1", "y1", "x2", "y2")] if outline else None
    print(shown, flush=True)
print("spend", state["spend_usd"], "SAM", state["see_calls"], "asks", state["ask_calls"], flush=True)
