"""Asks Muse Spark about the test sounds and scores the answers against truth.json."""

import base64
import concurrent.futures as cf
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import load_key

SOUNDS = Path(sys.argv[1])
KEY = load_key(ROOT / ".env")
truth = json.loads((SOUNDS / "truth.json").read_text())
MODELS = ["muse-spark-1.2", "muse-spark-1.3"]


def ask(model, name, text, retries=2):
    audio = base64.b64encode((SOUNDS / f"{name}.wav").read_bytes()).decode()
    body = {
        "model": model, "max_tokens": 700, "reasoning_effort": "minimal",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
        ]}],
    }
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
            return ask(model, name, text, retries - 1)
        return f"ERROR {e.code}: {e.read()[:160]!r}", 0.0
    answer = (data["choices"][0]["message"].get("content") or "").strip()
    return answer, time.perf_counter() - started


def many(jobs):
    with cf.ThreadPoolExecutor(6) as pool:
        return list(pool.map(lambda j: ask(*j), jobs))


print("=== 1. What does Spark hear? (open question)")
OPEN = "List every sound you hear in this audio, in order. Name non-speech sounds too. Keep it to one short line."
files = ["room_only", "speech", "whistle_one", "bell_one", "submarine", "claps_two"]
for model in MODELS:
    for name, (answer, seconds) in zip(files, many([(model, n, OPEN) for n in files])):
        print(f"{model} {name} ({seconds:.1f}s): {answer[:170]}")

print("\n=== 2. Yes or no")
QUESTIONS = {
    "whistles": "Is a high-pitched whistle heard in this audio? Answer with one word: yes or no.",
    "bells": "Is a bell or chime heard in this audio? Answer with one word: yes or no.",
    "claps": "Is hand clapping heard in this audio? Answer with one word: yes or no.",
}
TRIES = 2
for model in MODELS:
    for key, question in QUESTIONS.items():
        names = list(truth)
        jobs = [(model, n, question) for n in names for _ in range(TRIES)]
        res = many(jobs)
        correct = false_yes = missed = errors = 0
        times = []
        wrong = {}
        for (m, n, q), (answer, seconds) in zip(jobs, res):
            if answer.startswith("ERROR"):
                errors += 1
                continue
            times.append(seconds)
            said = answer.lower().startswith("yes")
            real = truth[n][key] > 0
            if said == real:
                correct += 1
            else:
                wrong[n] = wrong.get(n, 0) + 1
                if said:
                    false_yes += 1
                else:
                    missed += 1
        p50 = statistics.median(times) if times else 0
        print(f"{model} {key}: {correct}/{len(jobs)-errors} correct, missed {missed}, false yes {false_yes}, "
              f"errors {errors}, p50 {p50:.2f}s, wrong on {wrong}")

print("\n=== 3. How many?")
COUNTS = [
    ("whistles", "How many separate times does a high-pitched whistle sound in this audio? Answer with a number only.",
     ["speech", "whistle_one", "whistles_2", "whistles_3", "whistles_3_speech", "whistles_5"]),
    ("bells", "How many separate times does a bell or chime ring in this audio? Answer with a number only.",
     ["bell_one", "bells_3", "bells_5"]),
    ("claps", "How many hand claps are in this audio? Answer with a number only.",
     ["claps_two", "claps_3"]),
]
TRIES = 3
for model in MODELS:
    for key, question, names in COUNTS:
        jobs = [(model, n, question) for n in names for _ in range(TRIES)]
        res = many(jobs)
        by_file = {}
        for (m, n, q), (answer, seconds) in zip(jobs, res):
            found = re.search(r"\d+", answer)
            by_file.setdefault(n, []).append(int(found.group()) if found else answer[:12])
        print(model, key, {f"{n} (true {truth[n][key]})": v for n, v in by_file.items()})
