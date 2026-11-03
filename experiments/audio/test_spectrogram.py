"""Shows Spark a spectrogram picture of each test sound and asks for a count."""

import base64
import concurrent.futures as cf
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import Client, load_key

SOUNDS = Path(sys.argv[1])
truth = json.loads((SOUNDS / "truth.json").read_text())
client = Client(load_key(ROOT / ".env"))
Q = ("This image is a spectrogram of a sound recording. Time runs left to right. Low pitch is at the bottom. "
     "Count the separate bright horizontal streaks in the upper half, each one a sustained high tone. "
     "Answer with a number only.")


def picture(name):
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(SOUNDS / f"{name}.wav"), "-lavfi",
                        "showspectrumpic=s=768x256:legend=0:scale=log:stop=12000", f.name], check=True)
        return Path(f.name).read_bytes()


def number(jpeg):
    started = time.perf_counter()
    raw = client._post({"model": "muse-spark-1.3", "max_output_tokens": 3000, "reasoning": {"effort": "minimal"},
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": Q},
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}]}]}, timeout=30)
    data = json.loads(raw)
    text = "".join(c.get("text", "") for o in data.get("output", []) if o.get("type") == "message" for c in o.get("content", []))
    found = re.search(r"\d+", text)
    return (int(found.group()) if found else f"none:{data.get('status')}"), time.perf_counter() - started


for name in ["room_only", "speech", "whistle_one", "whistles_2", "whistles_3", "whistles_3_speech", "whistles_5", "bells_3"]:
    jpeg = picture(name)
    with cf.ThreadPoolExecutor(3) as pool:
        res = list(pool.map(lambda _: number(jpeg), range(3)))
    print(f"{name} (true {truth[name]['whistles']}): {[r[0] for r in res]} p50 {statistics.median([r[1] for r in res]):.2f} s")
