"""Plain code finds each sound event. Spark then names each event. Voice Transcribe and video are also checked."""

import array
import base64
import concurrent.futures as cf
import json
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
from meta_api import load_key

SOUNDS = Path(sys.argv[1])
KEY = load_key(ROOT / ".env")
truth = json.loads((SOUNDS / "truth.json").read_text())
RATE = 24000
WINDOW = int(0.02 * RATE)
LABELS = ["whistle", "bell", "clap", "speech", "other"]


def read(name):
    with wave.open(str(SOUNDS / f"{name}.wav")) as w:
        return array.array("h", w.readframes(w.getnframes()))


def find_events(samples, join_s=0.25, min_s=0.05):
    """Returns (start, end) in seconds for each stretch that rises well above the room level."""
    levels = []
    for i in range(0, len(samples) - WINDOW, WINDOW):
        chunk = samples[i:i + WINDOW]
        levels.append((sum(v * v for v in chunk) / WINDOW) ** 0.5)
    floor = statistics.median(levels)
    limit = max(floor * 4, 300)
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


def clip_wav(samples, start, end, pad=0.15):
    a = max(0, int((start - pad) * RATE))
    b = min(len(samples), int((end + pad) * RATE))
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(samples[a:b].tobytes())
        return Path(f.name).read_bytes()


def chat(model, content, retries=2):
    body = {"model": model, "max_tokens": 700, "reasoning_effort": "minimal",
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(
        "https://api.meta.ai/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429 and retries:
            time.sleep(2)
            return chat(model, content, retries - 1)
        return f"ERROR {e.code}: {e.read()[:120]!r}", 0.0
    return (data["choices"][0]["message"].get("content") or "").strip(), time.perf_counter() - started


def label(model, wav):
    text = ("An audio clip is attached and you can process it. It holds one sound. "
            f"Choose the best label: {', '.join(LABELS)}. A whistle is a sustained high tone. "
            "Answer with the label only.")
    audio = base64.b64encode(wav).decode()
    answer, seconds = chat(model, [
        {"type": "text", "text": text},
        {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
    ])
    answer = answer.lower()
    return next((l for l in LABELS if l in answer), "refused"), seconds


print("=== 4. Plain code: how many sound events are found?")
all_events = {}
for name, answers in truth.items():
    samples = read(name)
    started = time.perf_counter()
    events = find_events(samples)
    all_events[name] = (samples, events)
    expected = sum(answers.get(k, 0) for k in ("whistles", "bells", "claps"))
    print(f"{name}: found {len(events)}, sound events placed {expected}, "
          f"{[(round(a, 1), round(b, 1)) for a, b in events]} in {1000 * (time.perf_counter() - started):.0f} ms")

print("\n=== 5. Spark names each event found by code (3 tries each)")
for model in ["muse-spark-1.2", "muse-spark-1.3"]:
    jobs, keys = [], []
    for name, (samples, events) in all_events.items():
        for index, (a, b) in enumerate(events):
            wav = clip_wav(samples, a, b)
            for _ in range(3):
                jobs.append(wav)
                keys.append((name, index))
    with cf.ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda w: label(model, w), jobs))
    votes = {}
    times = [s for _, s in results if s]
    for key, (lab, _) in zip(keys, results):
        votes.setdefault(key, []).append(lab)
    print(f"{model}: {len(jobs)} calls, p50 {statistics.median(times):.2f} s")
    per_file = {}
    for (name, index), labs in votes.items():
        best = max(set(labs), key=labs.count)
        per_file.setdefault(name, []).append(best)
    for name, labs in per_file.items():
        counts = {k: labs.count(k) for k in sorted(set(labs))}
        answers = truth[name]
        want = {"whistle": answers.get("whistles", 0), "bell": answers.get("bells", 0), "clap": answers.get("claps", 0)}
        print(f"  {name}: got {counts}, true {({k: v for k, v in want.items() if v}) or answers.get('kind')}")

print("\n=== 6. Spark 1.2 with the sound inside a video file")
def as_mp4(name):
    out = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=2",
                    "-i", str(SOUNDS / f"{name}.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)], check=True)
    data = out.read_bytes()
    out.unlink()
    return data
QUESTION = "Listen to the soundtrack of this video. How many separate times does a high-pitched whistle sound? Answer with a number only."
for name in ["speech", "whistle_one", "whistles_3", "whistles_5"]:
    url = "data:video/mp4;base64," + base64.b64encode(as_mp4(name)).decode()
    content = [{"type": "text", "text": QUESTION}, {"type": "video_url", "video_url": {"url": url}}]
    with cf.ThreadPoolExecutor(3) as pool:
        res = list(pool.map(lambda _: chat("muse-spark-1.2", content), range(3)))
    print(f"{name} (true {truth[name]['whistles']}): {[a[:14] for a, _ in res]} p50 {statistics.median([s for _, s in res]):.2f} s")

print("\n=== 7. Voice Transcribe on non-speech sounds")
def transcribe(name):
    boundary = uuid.uuid4().hex
    settings = json.dumps({"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"})
    wav = (SOUNDS / f"{name}.wav").read_bytes()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n{settings}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + wav + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.meta.ai/v1/asr/transcribe", data=body, headers={
        "Authorization": "Bearer " + KEY, "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.dumps(json.loads(resp.read()))[:260]
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code}: {e.read()[:200]!r}"
for name in ["speech", "whistle_one", "bell_one", "claps_two"]:
    print(name, "->", transcribe(name))
