"""Tests every route on one real pressure cooker whistle, taken from a short web clip.

The clip is not ours to publish, so it stays outside this repo. Pass its folder as the first argument.
The look-alike noises are synthetic.
"""

import array
import base64
import cmath
import concurrent.futures as cf
import json
import math
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import Client, load_key

HERE = Path(sys.argv[1])
OLD = Path(sys.argv[2])
KEY = load_key(ROOT / ".env")
RATE = 24000
WINDOW = int(0.02 * RATE)
FFT_SIZE, HOP, BANDS = 1024, 512, 32
HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / FFT_SIZE) for i in range(FFT_SIZE)]
EDGES = [200 * (11000 / 200) ** (i / BANDS) for i in range(BANDS + 1)]
random.seed(11)


def read(path):
    with wave.open(str(path)) as w:
        return [v / 32768 for v in array.array("h", w.readframes(w.getnframes()))]


def write(path, samples):
    data = array.array("h", (int(max(-1, min(1, s)) * 32767) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(data.tobytes())


def find_events(samples, join_s=0.25, min_s=0.03):
    levels = []
    for i in range(0, len(samples) - WINDOW, WINDOW):
        chunk = samples[i:i + WINDOW]
        levels.append((sum(v * v for v in chunk) / WINDOW) ** 0.5)
    # A long sound fills much of a short clip, so the room level must come from the quiet part.
    floor = sorted(levels)[len(levels) // 8]
    limit = max(floor * 4, 0.01)
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
    a, b = int(start * RATE), max(int(end * RATE), int(start * RATE) + FFT_SIZE)
    totals, frames = [0.0] * BANDS, 0
    for i in range(a, min(b, len(samples) - FFT_SIZE), HOP * 4):
        spectrum = fft([complex(samples[i + j] * 32768 * HANN[j]) for j in range(FFT_SIZE)])
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


def room(seconds, level=0.03):
    out, last = [], 0.0
    for _ in range(int(seconds * RATE)):
        last = 0.985 * last + 0.015 * random.uniform(-1, 1)
        out.append(last * level * 8)
    return out


def fade(samples, seconds=0.08):
    n = int(seconds * RATE)
    return [s * min(i / n, (len(samples) - 1 - i) / n, 1) for i, s in enumerate(samples)]


def repitch(samples, factor):
    return [samples[int(i * factor)] for i in range(int(len(samples) / factor))]


def place(length_s, events):
    mix = room(length_s)
    for start, samples, gain in events:
        at = int(start * RATE)
        for i, value in enumerate(samples):
            if at + i < len(mix):
                mix[at + i] += value * gain
    return mix


def noise_burst(seconds, kind):
    out, low = [], 0.0
    n = int(seconds * RATE)
    for i in range(n):
        x = random.uniform(-1, 1)
        low = 0.75 * low + 0.25 * x
        t = i / RATE
        env = min(t / 0.1, 1) * min((seconds - t) / 0.2, 1)
        if kind == "tap":
            out.append(env * low * 0.7)
        elif kind == "sizzle":
            crackle = 1 if random.random() < 0.02 else 0.25
            out.append(env * (x - low) * crackle * 0.7)
        else:
            buzz = sum(math.sin(2 * math.pi * 290 * h * t) / h for h in range(1, 7))
            out.append(env * (0.22 * buzz + 0.15 * low))
    return out


def chat(model, content, retries=2):
    body = {"model": model, "max_tokens": 900, "reasoning_effort": "minimal",
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request("https://api.meta.ai/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429 and retries:
            time.sleep(2)
            return chat(model, content, retries - 1)
        return f"ERROR {e.code}", 0.0
    return (data["choices"][0]["message"].get("content") or "").strip(), time.perf_counter() - started


def ask_audio(model, name, text):
    audio = base64.b64encode((HERE / f"{name}.wav").read_bytes()).decode()
    return chat(model, [{"type": "text", "text": text},
                        {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}])


def many(jobs):
    with cf.ThreadPoolExecutor(6) as pool:
        return list(pool.map(lambda j: ask_audio(*j), jobs))


real = read(HERE / "real.wav")
events = find_events(real)
print("=== A. Plain code on the real clip")
print("events:", [(round(a, 2), round(b, 2)) for a, b in events])
start, end = max(events, key=lambda e: e[1] - e[0])
whistle = fade(real[int(start * RATE):int(end * RATE)])
print(f"whistle length {len(whistle) / RATE:.2f} s")

speech = read(OLD / "speech.wav")
bell = read(OLD / "bell_one.wav")
low, high = repitch(whistle, 0.96), repitch(whistle, 1.04)
T = len(whistle) / RATE
truth = {
    "real": 1,
    "real_x3": 3, "real_x3_speech": 3, "real_x5": 5,
    "room": 0, "speech_only": 0, "bell_only": 0, "tap": 0, "sizzle": 0, "mixer": 0,
}
write(HERE / "real_x3.wav", place(25, [(2, whistle, 1), (9, low, 0.7), (17, high, 0.85)]))
write(HERE / "real_x3_speech.wav", place(28, [(2, whistle, 1), (7.5, speech, 1), (15, low, 0.7), (22, high, 0.85)]))
write(HERE / "real_x5.wav", place(40, [(1, whistle, 1), (8, low, 0.8), (15, high, 0.6), (23, whistle, 0.9), (32, low, 1)]))
write(HERE / "room.wav", place(8, []))
write(HERE / "speech_only.wav", speech)
write(HERE / "bell_only.wav", bell)
for kind in ("tap", "sizzle", "mixer"):
    write(HERE / f"{kind}.wav", place(8, [(2, noise_burst(3.4, kind), 1)]))

print("\n=== B. Sound events found by code")
found = {n: find_events(read(HERE / f"{n}.wav")) for n in truth}
print({n: f"{len(e)} (whistles {truth[n]})" for n, e in found.items()})

print("\n=== C. Match against the real whistle as the one example")
example = fingerprint(real, start, end)
scores = {}
for n, ev in found.items():
    samples = read(HERE / f"{n}.wav")
    scores[n] = [round(sum(x * y for x, y in zip(example, fingerprint(samples, a, b))), 2) for a, b in ev]
    print(f"{n} (whistles {truth[n]}): {scores[n]}")
synthetic = read(OLD / "whistle_one.wav")
ev = find_events(synthetic)
print("synthetic tone whistle:", [round(sum(x * y for x, y in zip(example, fingerprint(synthetic, a, b))), 2) for a, b in ev])

print("\n=== D. Spark: open question on the real clip")
OPEN = "List every sound you hear in this audio. Name non-speech sounds too. One short line."
for model in ["muse-spark-1.2", "muse-spark-1.3"]:
    res = many([(model, "real", OPEN)] * 3)
    print(model, [a[:120] for a, _ in res])

print("\n=== E. Spark: yes or no, 3 tries each")
Q = "Is a pressure cooker whistle heard in this audio? Answer with one word: yes or no."
names = list(truth)
for model in ["muse-spark-1.2", "muse-spark-1.3"]:
    jobs = [(model, n, Q) for n in names for _ in range(3)]
    res = many(jobs)
    row = {}
    for (m, n, q), (answer, seconds) in zip(jobs, res):
        row.setdefault(n, []).append(answer[:3].lower().strip(".,"))
    print(model, {f"{n} ({'yes' if truth[n] else 'no'})": "/".join(v) for n, v in row.items()})

print("\n=== F. Spark: how many, 3 tries each")
Q = "How many times does a pressure cooker whistle blow in this audio? Answer with a number only."
for model in ["muse-spark-1.2", "muse-spark-1.3"]:
    jobs = [(model, n, Q) for n in ["real", "real_x3", "real_x3_speech", "real_x5", "tap", "speech_only"] for _ in range(3)]
    res = many(jobs)
    row = {}
    for (m, n, q), (answer, seconds) in zip(jobs, res):
        hit = re.search(r"\d+", answer)
        row.setdefault(n, []).append(int(hit.group()) if hit else "x")
    print(model, {f"{n} (true {truth[n]})": v for n, v in row.items()})

print("\n=== G. Voice Transcribe on the real clip")
boundary = uuid.uuid4().hex
settings = json.dumps({"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"})
wav = (HERE / "real.wav").read_bytes()
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n{settings}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + wav + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request("https://api.meta.ai/v1/asr/transcribe", data=body, headers={
    "Authorization": "Bearer " + KEY, "Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(req, timeout=60) as resp:
    print(repr(json.loads(resp.read()).get("transcript")))

print("\n=== H. Spark reads a spectrogram picture, 3 tries each")
client = Client(KEY)
Q = ("This image is a spectrogram of a kitchen recording. Time runs left to right. A pressure cooker whistle shows as "
     "a tall bright block that spans most pitches and lasts a few seconds. Count the separate tall bright blocks. "
     "Answer with a number only.")
def picture(name):
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(HERE / f"{name}.wav"), "-lavfi",
                        "showspectrumpic=s=768x256:legend=0:scale=log:stop=12000", f.name], check=True)
        return Path(f.name).read_bytes()
def number(jpeg):
    raw = client._post({"model": "muse-spark-1.3", "max_output_tokens": 3000, "reasoning": {"effort": "minimal"},
        "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": Q},
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}]}]}, timeout=30)
    text = "".join(c.get("text", "") for o in json.loads(raw).get("output", []) if o.get("type") == "message" for c in o.get("content", []))
    hit = re.search(r"\d+", text)
    return int(hit.group()) if hit else "x"
for name in ["real", "real_x3", "real_x3_speech", "real_x5", "tap", "sizzle", "mixer", "speech_only"]:
    jpeg = picture(name)
    with cf.ThreadPoolExecutor(3) as pool:
        print(f"{name} (true {truth[name]}): {list(pool.map(lambda _: number(jpeg), range(3)))}")
