"""Rewrites a saved rule from its real recording, then compares old and new facts on the same real frames.

Pass the rule folder name, the log folder and the rule's name in that log. It uses the paid API.
"""

import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import sandbox
import server
from meta_api import Client, load_key
from writer import write_rule

OLD, LOG, NAME = ROOT / "rules" / sys.argv[1], ROOT / sys.argv[2], sys.argv[3]
client = Client(load_key(ROOT / ".env"))
old = json.loads((OLD / "rule.json").read_text())
timing = json.loads((OLD / "recording/timing.json").read_text())
example = [p.read_bytes() for p in sorted((OLD / "recording/example").glob("*.jpg"))]
idle = [p.read_bytes() for p in sorted((OLD / "recording/idle").glob("*.jpg"))]

folder = sandbox.create("redo-" + uuid.uuid4().hex[:6])
steps = []
try:
    fields, cost = write_rule(client, folder, old["sentence"], example, idle, steps.append, timing["motions"], timing["gap_s"])
except Exception as e:
    for step in steps:
        print("  -", step.replace("\n", " / ")[:420])
    sys.exit(f"writer failed: {e}")
for step in steps:
    if step.startswith(("Spark saw", "Reviewer", "Plain code", "Sensor")):
        print("  -", step.replace("\n", " / ")[:300])
print(f"writer cost ${cost:.3f}")
if fields.get("unsupported"):
    sys.exit("unsupported: " + fields["unsupported"])
new = fields["sensors"][0]
print("OLD facts:", old["sensors"][0]["requires"])
print("NEW facts:", new["requires"], "| target", repr(new["target"]))
print("NEW plan:", {k: new[k] for k in ("pattern", "frames_per_second", "check_every_s", "frames_per_check", "clear_after_s", "min_motion")})
print("reason:", new["reason"])

checks = [json.loads(l) for l in (LOG / "checks.jsonl").open()]
checks = [c for c in checks if c["rule_name"] == NAME]
t0, t1 = checks[0]["t"], checks[-1]["t"]
frames = [json.loads(l) for l in (LOG / "frames.jsonl").open()]
window = [f for f in frames if t0 - 2 <= f["t"] <= t1]


class Sink:
    def __init__(self, app, sensor):
        self.id, self.name = "r", NAME
        self.sensors = [server.Sensor(sensor)]
        self.events = []
        self.send = self.events.append
        app.entries[self.id] = self


tmp = tempfile.mkdtemp()
app = server.App(client, tmp, inline=True)
sink = Sink(app, new)
rows = []
for f in window:
    rows += app.frame((LOG / "frames" / f["name"]).read_bytes(), f["t"], motion=f.get("motion", 0))["results"]
print(f"\nreplay of {len(window)} real frames over {t1 - t0:.0f} s through the live logic, {len(rows)} model checks")
print(f"old rule fired {sum(c['event'] == 'moment' for c in checks)} times, new rule fired {sum(r.get('event') == 'moment' for r in rows)} times")
for r in rows:
    if r.get("event") == "moment":
        a = r["answers"][0]
        print(f"  {a.get('note', '')[:96]}")
print("near misses (one answer yes, other no, or one fact short):")
for r in rows:
    for a in r.get("answers", [])[:1]:
        facts = a.get("facts") or []
        if not r.get("hit") and sum(facts) == len(facts) - 1 and facts:
            print(f"  {''.join('Y' if v else 'n' for v in facts)} {a.get('note', '')[:88]}")
print("spend", round(app.spend(), 3))
shutil.rmtree(tmp)
shutil.rmtree(folder)
