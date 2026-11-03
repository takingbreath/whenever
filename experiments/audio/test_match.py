"""Two last routes: match a sound against one example with plain code, and show Spark a picture of the sound."""

import array
import base64
import cmath
import concurrent.futures as cf
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import Client, load_key

SOUNDS = Path(sys.argv[1])
truth = json.loads((SOUNDS / "truth.json").read_text())
RATE = 24000
WINDOW = int(0.02 * RATE)
FFT_SIZE = 1024
HOP = 512
BANDS = 32
HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / FFT_SIZE) for i in range(FFT_SIZE)]
EDGES = [200 * (10000 / 200) ** (i / BANDS) for i in range(BANDS + 1)]


def read(name):
    with wave.open(str(SOUNDS / f"{name}.wav")) as w:
        return array.array("h", w.readframes(w.getnframes()))


def find_events(samples, join_s=0.25, min_s=0.03):
    levels = []
    for i in range(0, len(samples) - WINDOW, WINDOW):
        chunk = samples[i:i + WINDOW]
        levels.append((sum(v * v for v in chunk) / WINDOW) ** 0.5)
    limit = max(statistics.median(levels) * 4, 300)
    events, start, last = [], None, None
    for i, level in enumerate(levels):
        t = i * 0.02
        if level >= limit:
            if start is None:
                start = t
            elif t - last > join_s:
                events.append((start, last + 0.02))
                start = t
            last = t
    if start is not None:
        events.append((start, last + 0.02))
    return [e for e in events if e[1] - e[0] >= min_s]


def fft(x):
    n = len(x)
    if n == 1:
        return x
    even, odd = fft(x[0::2]), fft(x[1::2])
    twiddle = [cmath.exp(-2j * math.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + twiddle[k] for k in range(n // 2)] + [even[k] - twiddle[k] for k in range(n // 2)]


def fingerprint(samples, start, end):
    """Mean log energy in 32 bands, with the loudness removed."""
    a, b = int(start * RATE), max(int(end * RATE), int(start * RATE) + FFT_SIZE)
    totals, frames = [0.0] * BANDS, 0
    for i in range(a, min(b, len(samples) - FFT_SIZE), HOP):
        spectrum = fft([complex(samples[i + j] * HANN[j]) for j in range(FFT_SIZE)])
        power = [abs(v) ** 2 for v in spectrum[:FFT_SIZE // 2]]
        for band in range(BANDS):
            low = int(EDGES[band] * FFT_SIZE / RATE)
            high = max(low + 1, int(EDGES[band + 1] * FFT_SIZE / RATE))
            totals[band] += math.log10(sum(power[low:high]) / (high - low) + 1)
        frames += 1
    vector = [t / max(frames, 1) for t in totals]
    mean = sum(vector) / BANDS
    vector = [v - mean for v in vector]
    size = math.sqrt(sum(v * v for v in vector)) or 1
    return [v / size for v in vector]


def similarity(a, b):
    return sum(x * y for x, y in zip(a, b))


print("=== 8. Events found after the fix for short sounds")
events = {n: find_events(read(n)) for n in truth}
print({n: len(e) for n, e in events.items()})

started = time.perf_counter()
prints = {n: [fingerprint(read(n), a, b) for a, b in e] for n, e in events.items()}
total_s = sum(b - a for e in events.values() for a, b in e)
print(f"fingerprint time: {time.perf_counter() - started:.1f} s for {total_s:.0f} s of sound in plain Python")

print("\n=== 9. Match against one example (the example comes from a different file)")
EXAMPLES = {"whistles": ("whistle_one", 0), "bells": ("bell_one", 0), "claps": ("claps_two", 0)}
LIMIT = 0.85
right = wrong = 0
for key, (name, index) in EXAMPLES.items():
    example = prints[name][index]
    row = {}
    for n, fps in prints.items():
        if n == name:
            continue
        scores = [similarity(example, fp) for fp in fps]
        got = sum(s >= LIMIT for s in scores)
        want = truth[n][key]
        ok = got == want
        right += ok
        wrong += not ok
        if want or got:
            row[n] = f"got {got}, true {want}"
    print(f"{key}: {row}")
    others = [similarity(example, fp) for n, fps in prints.items() for fp in fps if truth[n][key] == 0 and n != name]
    matches = [similarity(example, fp) for n, fps in prints.items() for fp in fps if truth[n][key] > 0 and n != name]
    print(f"   similarity to the right sound: min {min(matches):.2f}. To other sounds: max {max(others):.2f}")
print(f"files counted correctly: {right} of {right + wrong}")

print("\n=== 10. Spark reads a picture of the sound (spectrogram)")
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
    raw = client._post({"model": "muse-spark-1.3", "max_output_tokens": 500, "reasoning": {"effort": "low"},
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": Q},
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}]}]})
    text = "".join(c.get("text", "") for o in json.loads(raw).get("output", []) if o.get("type") == "message" for c in o.get("content", []))
    found = re.search(r"\d+", text)
    return (int(found.group()) if found else text[:10]), time.perf_counter() - started
for name in ["room_only", "speech", "whistle_one", "whistles_2", "whistles_3", "whistles_3_speech", "whistles_5", "bells_3"]:
    jpeg = picture(name)
    with cf.ThreadPoolExecutor(3) as pool:
        res = list(pool.map(lambda _: number(jpeg), range(3)))
    print(f"{name} (true {truth[name]['whistles']}): {[r[0] for r in res]} p50 {statistics.median([r[1] for r in res]):.2f} s")
