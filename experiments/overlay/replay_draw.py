"""Replays saved frames through the real outline path, and draws each outline on its frame.

Usage: replay_draw.py <log folder> <output folder> <outline word> <question> <frame> [<frame> ...]
"""

import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw  # noqa: E402

import server  # noqa: E402
from meta_api import Client, load_key  # noqa: E402

logs, out, word, question = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
out.mkdir(parents=True, exist_ok=True)
scratch = tempfile.mkdtemp()
app = server.App(Client(load_key(ROOT / ".env")), scratch, inline=True)


class SavedRule:
    pointed = None


sensor = server.Sensor({"id": "s", "question": question, "outline": word})

for name in sys.argv[5:]:
    jpeg = (logs / "frames" / name).read_bytes()
    saved = app.evidence.save_frame(1.0, jpeg)
    started = time.perf_counter()
    outline = app._outline(SavedRule(), sensor, saved)
    seconds = time.perf_counter() - started
    if not outline:
        print(f"{name}: no outline, {seconds:.2f}s")
        continue
    print(f"{name}: box {outline['x1']},{outline['y1']},{outline['x2']},{outline['y2']} "
          f"path {len(outline['path'])}, {seconds:.2f}s")
    picture = Image.open(logs / "frames" / name).convert("RGB")
    pen = ImageDraw.Draw(picture)
    scale_x = (outline["x2"] - outline["x1"]) / outline["mask_w"]
    scale_y = (outline["y2"] - outline["y1"]) / outline["mask_h"]
    for shape in outline["path"].split("M")[1:]:
        numbers = [float(n) for n in re.findall(r"-?\d+\.?\d*", shape)]
        points = [(outline["x1"] + x * scale_x, outline["y1"] + y * scale_y)
                  for x, y in zip(numbers[::2], numbers[1::2])]
        if len(points) > 2:
            pen.line(points + points[:1], fill=(63, 255, 180), width=3)
    picture.save(out / name)
print(f"spend ${app.spend():.4f}")
shutil.rmtree(scratch)
