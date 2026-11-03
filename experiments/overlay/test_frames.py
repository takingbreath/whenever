"""Tests clicks, names and outlines on camera-like frames, with the real models.

The frames in testdata/frames are AI-made pictures of a person at a desk. Saves the answers
in results.json, so the page test can draw them.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import Client, load_key

FRAMES = ROOT / "testdata" / "frames"
# (frame, what a person clicks, click x, click y). The frames are 384 x 288.
CLICKS = [
    ("mug_384.jpg", "mug", 228, 170),
    ("mug_384.jpg", "laptop", 85, 165),
    ("mug_384.jpg", "plant", 24, 195),
    ("thumbs_up_384.jpg", "thumb", 285, 100),
    ("thumbs_up_384.jpg", "mug on the desk", 92, 232),
    ("thumbs_up_384.jpg", "calendar", 322, 52),
]

client = Client(load_key(ROOT / ".env"))
results = []
for name, wanted, x, y in CLICKS:
    jpeg = (FRAMES / name).read_bytes()
    started = time.perf_counter()
    found = client.mark(jpeg, 384, 288, point=(x, y))
    marked_s = time.perf_counter() - started
    if not found:
        print(name, wanted, "nothing found", flush=True)
        continue
    box = found[0]
    started = time.perf_counter()
    noun, _ = client.name_object(jpeg, box)
    named_s = time.perf_counter() - started
    started = time.perf_counter()
    again = client.find(noun, jpeg)
    find_s = time.perf_counter() - started
    print(f"{wanted}: box {box['x1']},{box['y1']},{box['x2']},{box['y2']} mask {box['mask_w']}x{box['mask_h']} "
          f"path {len(box['path'])} | name '{noun}' | find {len(again)} | "
          f"{marked_s:.2f}s {named_s:.2f}s {find_s:.2f}s", flush=True)
    results.append({"frame": name, "wanted": wanted, "click": [x, y], "box": box, "noun": noun,
                    "matches": [{k: v for k, v in s.items() if k != "path"} for s in again]})
(Path(__file__).parent / "results.json").write_text(json.dumps(results, indent=1))
