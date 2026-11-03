"""Replays a saved run with no gaps, to separate what the harness lost from what the model missed.

Pass the log folder and the rule name. It uses the paid API.
"""

import concurrent.futures as cf
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import Client, load_key

LOG, RULE = Path(sys.argv[1]), sys.argv[2]
BLOCK = 6
checks = [json.loads(l) for l in (LOG / "checks.jsonl").open()]
frames = [json.loads(l) for l in (LOG / "frames.jsonl").open()]
rule_checks = [c for c in checks if c["rule_name"] == RULE]
question, facts = rule_checks[0]["question"], rule_checks[0]["requires"]
t0, t1 = rule_checks[0]["t"], rule_checks[-1]["t"]
window = [f for f in frames if t0 - 2.5 <= f["t"] <= t1]
blocks = [window[i:i + BLOCK] for i in range(0, len(window), BLOCK)]
client = Client(load_key(ROOT / ".env"))

SOFT = "A person's hand is moving fast toward, or is touching, the other person's face, head or body"


def run(block):
    jpegs = [(LOG / "frames" / f["name"]).read_bytes() for f in block]
    strict, _ = client.check_facts(question, facts, jpegs)
    soft, _ = client.check_facts(question, [SOFT], jpegs)
    return block[0]["t"] - t0, block[-1]["t"] - t0, strict, soft


with cf.ThreadPoolExecutor(5) as pool:
    results = list(pool.map(run, blocks))

print(f"{len(blocks)} blocks with no gaps, over {t1 - t0:.0f} s")
strict_hits = [(a, b) for a, b, s, _ in results if s["seen"]]
soft_hits = [(a, b) for a, b, _, s in results if s["seen"]]
print(f"all facts required: {len(strict_hits)} blocks yes at {[round(a) for a, _ in strict_hits]}")
print(f"one softer fact: {len(soft_hits)} blocks yes at {[round(a) for a, _ in soft_hits]}")
print("\nblocks where the strict facts failed but contact was seen:")
for a, b, strict, soft in results:
    if soft["seen"] and not strict["seen"]:
        print(f"  {a:5.1f}-{b:5.1f}s strict {''.join('Y' if v else 'n' for v in strict.get('facts', []))} | {strict.get('note', '')[:80]}")
