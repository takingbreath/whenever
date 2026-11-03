"""Does Wonder choose sensible timing for different events, with no recording?"""

import concurrent.futures as cf
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import sandbox
from meta_api import Client, load_key
from writer import write_rule

client = Client(load_key(ROOT / ".env"))
SENTENCES = [
    "Count how many times I slapped my friend.",
    "Count how many times I drink water.",
    "Tell me if the door stays open for more than 5 minutes.",
    "Count how many times I clap my hands.",
    "Tell me when I fall asleep at my desk.",
]


def one(sentence):
    folder = sandbox.create("time-" + uuid.uuid4().hex[:6])
    try:
        fields, cost = write_rule(client, folder, sentence)
        return sentence, fields, cost
    finally:
        shutil.rmtree(folder)


with cf.ThreadPoolExecutor(5) as pool:
    for sentence, fields, cost in pool.map(one, SENTENCES):
        print(f"\n{sentence}  (${cost:.3f})")
        if fields.get("unsupported"):
            print("  unsupported:", fields["unsupported"])
            continue
        for s in fields["sensors"]:
            print(f"  {s['id']}: {s['pattern']}, shortest {s['shortest_event_s']} s, {s['frames_per_second']} fps, "
                  f"check {s['check_every_s']} s, {s['frames_per_check']} frames, clear {s['clear_after_s']} s, target {s['target']!r}")
            print(f"    why: {s['reason']}")
            print(f"    needs: {s['requires']}")
