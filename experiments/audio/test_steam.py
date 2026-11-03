"""Confirms the "steam escaping" wording, then tests code events with a Spark check on each event."""

import array
import base64
import concurrent.futures as cf
import json
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import load_key

HERE = Path(sys.argv[1])
KEY = load_key(ROOT / ".env")
RATE = 24000
WINDOW = int(0.02 * RATE)
MODEL = "muse-spark-1.2"
Q = "Is the sound of steam escaping heard in this audio? Answer with one word: yes or no."
truth = {"real": 1, "real_x3": 3, "real_x3_speech": 3, "real_x5": 5, "tap": 0, "sizzle": 0, "mixer": 0,
         "speech_only": 0, "bell_only": 0, "room": 0}


def ask(wav, retries=2):
    audio = base64.b64encode(wav).decode()
    body = {"model": MODEL, "max_tokens": 900, "reasoning_effort": "minimal", "messages": [{"role": "user", "content": [
        {"type": "text", "text": Q}, {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}]}]}
    req = urllib.request.Request("https://api.meta.ai/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = (json.loads(resp.read())["choices"][0]["message"].get("content") or "").strip().lower()
    except urllib.error.HTTPError as e:
        if e.code == 429 and retries:
            time.sleep(2)
            return ask(wav, retries - 1)
        return "error", 0.0
    except Exception:
        return "error", 0.0
    answer = "yes" if text.startswith("yes") else "no" if text.startswith("no") else "refused"
    return answer, time.perf_counter() - started


def many(wavs):
    with cf.ThreadPoolExecutor(6) as pool:
        return list(pool.map(ask, wavs))


def read(name):
    with wave.open(str(HERE / f"{name}.wav")) as w:
        return array.array("h", w.readframes(w.getnframes()))


def find_events(samples, join_s=0.25, min_s=0.03):
    levels = []
    for i in range(0, len(samples) - WINDOW, WINDOW):
        chunk = samples[i:i + WINDOW]
        levels.append((sum(v * v for v in chunk) / WINDOW) ** 0.5)
    limit = max(sorted(levels)[len(levels) // 8] * 4, 300)
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


def clip(samples, start, end, pad=0.15):
    a, b = max(0, int((start - pad) * RATE)), min(len(samples), int((end + pad) * RATE))
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(samples[a:b].tobytes())
        return Path(f.name).read_bytes()


print("=== Confirm on whole files: 10 tries on whistle files, 8 on the others")
times = []
for name, count in truth.items():
    wav = (HERE / f"{name}.wav").read_bytes()
    res = many([wav] * (10 if count else 8))
    answers = [a for a, _ in res]
    times += [s for _, s in res if s]
    print(f"{name} ({'yes' if count else 'no'}): yes {answers.count('yes')}, no {answers.count('no')}, refused {answers.count('refused')}, error {answers.count('error')}")
print(f"p50 {statistics.median(times):.2f} s")

print("\n=== Code finds events, Spark checks each event 5 times, most votes wins")
right = 0
for name, count in truth.items():
    samples = read(name)
    events = find_events(samples)
    confirmed, detail = 0, []
    for start, end in events:
        res = many([clip(samples, start, end)] * 5)
        yes = sum(a == "yes" for a, _ in res)
        detail.append(f"{end - start:.1f}s:{yes}/5")
        confirmed += yes >= 3
    right += confirmed == count
    print(f"{name} (true {count}): events {len(events)}, confirmed {confirmed}, {detail}")
print(f"exact counts: {right} of {len(truth)} files")
