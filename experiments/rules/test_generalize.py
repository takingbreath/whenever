"""Wonder writes a drink rule from a recording of one bottle. Does it generalize, and does it reject an empty hand?

All frames are AI-made. Pass the folder of frames as the first argument.
"""

import concurrent.futures as cf
import json
import shutil
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import sandbox
from meta_api import Client, load_key
from writer import write_rule

D = Path(sys.argv[1])
client = Client(load_key(ROOT / ".env"))
TRIES = 8


def jpg(name):
    return (D / f"{name}_384.jpg").read_bytes()


N = "none"
folder = sandbox.create("gen-" + uuid.uuid4().hex[:6])
started = time.perf_counter()
fields, cost = write_rule(client, folder, "Count how many times I drink water.",
                          [jpg("none"), jpg("bottle_low"), jpg("bottle"), jpg("bottle"), jpg("bottle"), jpg("none")],
                          [jpg("none")] * 3, progress=lambda text: print("  -", text[:150], flush=True))
print(f"writer: {time.perf_counter() - started:.0f} s, ${cost:.3f}")
print(json.dumps(fields["sensors"], indent=1, ensure_ascii=False))
sensor = fields["sensors"][0]
words = " ".join([sensor["question"], *sensor.get("requires", [])]).lower()
print("names a colour:", any(c in words for c in ("green", "red ", "blue", "steel")))

STRIPS = {
    "SHOULD COUNT": {"green bottle": "bottle", "red bottle": "red_bottle", "steel cup": "steel_cup"},
    "SHOULD NOT COUNT": {"empty hand at the lips": "mime", "empty hand, head back": "mime_tilt",
                         "hand on chin": "hand_chin", "phone at the ear": "phone_ear",
                         "bottle on the desk": "bottle_table", "nothing": "none"},
}
facts = sensor.get("requires") or [sensor["question"]]
times = []


def one(strip):
    t0 = time.perf_counter()
    answer, _ = client.check_facts(sensor["question"], facts, strip)
    times.append(time.perf_counter() - t0)
    return answer


for group, items in STRIPS.items():
    print("\n" + group)
    for label, name in items.items():
        strip = [jpg(N), jpg(N), jpg(name), jpg(name), jpg(name), jpg(N)]
        with cf.ThreadPoolExecutor(4) as pool:
            res = list(pool.map(lambda _: one(strip), range(TRIES)))
        yes = sum(a["seen"] for a in res)
        confirmed = sum(res[i]["seen"] and res[i + 1]["seen"] for i in range(0, TRIES - 1, 2))
        print(f"  {label}: one check {yes} of {TRIES} | two checks {confirmed} of {TRIES // 2} | {res[0].get('note', '')[:80]}")
print(f"\ncheck time p50 {statistics.median(times):.2f} s, max {max(times):.2f} s")
shutil.rmtree(folder)
