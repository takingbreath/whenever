"""Replays saved frames through the live logic, with a sensor plan from Wonder.

The frames keep their real timing. A rule's result depends on the capture rate of the log.
Pass the log folder and the rule name in that log. It uses the paid API.
"""

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import server
from meta_api import Client, load_key

LOG, RULE = Path(sys.argv[1]), sys.argv[2]
W, H, CELL = 64, 36, 8
PLAN = {
    "id": "slap", "target": "", "pattern": "moment", "frames_per_second": 8, "check_every_s": 0.8,
    "frames_per_check": 6, "clear_after_s": 0.7, "min_count": 1,
    "question": "Is one person slapping another person?",
    "requires": ["A hand touches another person's cheek or face",
                 "The arm reaching to that face belongs to a different person than the face",
                 "The struck person's head turns or recoils away from the hand"],
}


def grey(path):
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", f"scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return out


def motion(a, b):
    best = 0.0
    for top in range(0, H - CELL + 1, CELL):
        for left in range(0, W - CELL + 1, CELL):
            total = sum(abs(a[y * W + x] - b[y * W + x])
                        for y in range(top, top + CELL) for x in range(left, left + CELL))
            best = max(best, total / (CELL * CELL))
    return best


class Sink:
    def __init__(self, app):
        self.id, self.name = "r", RULE
        self.sensors = [server.Sensor(PLAN)]
        self.events = []
        self.send = self.events.append
        app.entries[self.id] = self


checks = [json.loads(l) for l in (LOG / "checks.jsonl").open()]
frames = [json.loads(l) for l in (LOG / "frames.jsonl").open()]
rule_checks = [c for c in checks if c["rule_name"] == RULE]
t0, t1 = rule_checks[0]["t"], rule_checks[-1]["t"]
window = [f for f in frames if t0 - 2.5 <= f["t"] <= t1]

tmp = tempfile.mkdtemp()
app = server.App(Client(load_key(ROOT / ".env")), tmp, inline=True)
sink = Sink(app)
previous = None
motions = []
results = []
for f in window:
    path = LOG / "frames" / f["name"]
    image = grey(path)
    level = motion(image, previous) if previous else 0.0
    previous = image
    motions.append(level)
    results += app.frame(path.read_bytes(), f["t"], motion=level)["results"]

print(f"{len(window)} frames, {window[-1]['t'] - window[0]['t']:.0f} s, motion p50 {statistics.median(motions):.1f}, max {max(motions):.1f}")
print(f"model checks {len([r for r in results if not r.get('skipped')])}, with no call on still periods")
moments = [r for r in results if r.get("event") == "moment"]
print(f"occurrences: {len(moments)} at {[round(r['frames_covered'] and (sink.events[i * 2]['t'] - t0)) for i, r in enumerate(moments)]} s")
print("old live count was 2")
for r in moments:
    a = r["answers"][0]
    print(f"  {a.get('note', '')[:90]}")
from collections import Counter
patterns = Counter()
contact = []
for r in results:
    for a in r.get("answers", [])[:1]:
        facts = a.get("facts") or []
        patterns["".join("Y" if v else "n" for v in facts)] += 1
        if len(facts) >= 2 and facts[0] and facts[1]:
            contact.append((round(r["t"] - t0) if "t" in r else None, a.get("note", "")[:70]))
print("fact patterns (touch, different person, recoil):", patterns.most_common())
print(f"checks with touch and a different person, ignoring recoil: {len(contact)}")
for row in contact[:14]:
    print("  ", row[1])
print("spend", round(app.spend(), 3))
shutil.rmtree(tmp)
