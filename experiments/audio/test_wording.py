"""Asks Spark about the real whistle with different words, to see if the wording changes the answer."""

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

HERE = Path(sys.argv[1])
KEY = load_key(ROOT / ".env")
WHISTLE = ["real", "real_x3", "real_x5"]
OTHERS = ["tap", "sizzle", "mixer", "speech_only", "bell_only", "room"]
MODELS = ["muse-spark-1.2", "muse-spark-1.3"]
NOTE = "An audio clip is attached and you can process it. "
END = " Answer with one word: yes or no."
QUESTIONS = {
    "whistle": "Is a whistle heard in this audio?" + END,
    "high whistle": "Is a high-pitched whistle heard in this audio?" + END,
    "hissing": "Is a loud hissing sound heard in this audio?" + END,
    "steam": "Is the sound of steam escaping heard in this audio?" + END,
    "loud noise": "Is a loud continuous sound heard in this audio, other than speech?" + END,
    "whistle+note": NOTE + "Is a whistle heard?" + END,
    "hissing+note": NOTE + "Is a loud hissing sound heard?" + END,
}
LABELS = ["whistle", "hissing", "steam", "water", "frying", "mixer", "speech", "bell", "silence", "other"]
LABEL_Q = NOTE + f"Choose the one best label for the main sound: {', '.join(LABELS)}. Answer with the label only."
COUNT_Q = NOTE + "How many separate loud bursts of hissing are in this audio? Answer with a number only."


def ask(model, name, text, retries=2):
    audio = base64.b64encode((HERE / f"{name}.wav").read_bytes()).decode()
    body = {"model": model, "max_tokens": 900, "reasoning_effort": "minimal", "messages": [{"role": "user", "content": [
        {"type": "text", "text": text}, {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}]}]}
    req = urllib.request.Request("https://api.meta.ai/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return (json.loads(resp.read())["choices"][0]["message"].get("content") or "").strip()
    except urllib.error.HTTPError as e:
        if e.code == 429 and retries:
            time.sleep(2)
            return ask(model, name, text, retries - 1)
        return "error"
    except Exception:
        return "error"


def many(jobs):
    with cf.ThreadPoolExecutor(6) as pool:
        return list(pool.map(lambda j: ask(*j), jobs))


def short(answer):
    low = answer.lower().strip(" .")
    return "yes" if low.startswith("yes") else "no" if low.startswith("no") else "refused"


print("=== yes or no: found = yes on whistle files, false = yes on other sounds")
for model in MODELS:
    for key, question in QUESTIONS.items():
        jobs = [(model, n, question) for n in WHISTLE for _ in range(3)]
        jobs += [(model, n, question) for n in OTHERS for _ in range(2)]
        res = [short(a) for a in many(jobs)]
        found = sum(r == "yes" for (m, n, q), r in zip(jobs, res) if n in WHISTLE)
        refused = sum(r == "refused" for r in res)
        false = {}
        for (m, n, q), r in zip(jobs, res):
            if n in OTHERS and r == "yes":
                false[n] = false.get(n, 0) + 1
        print(f"{model} '{key}': found {found} of 9, refused {refused} of {len(jobs)}, false yes {false or 'none'}")

print("\n=== choose a label, 3 tries each")
for model in MODELS:
    jobs = [(model, n, LABEL_Q) for n in WHISTLE + OTHERS for _ in range(3)]
    row = {}
    for (m, n, q), answer in zip(jobs, many(jobs)):
        low = answer.lower()
        row.setdefault(n, []).append(next((l for l in LABELS if l in low), "refused"))
    print(model, row)

print("\n=== how many bursts of hissing, 3 tries each")
for model in MODELS:
    jobs = [(model, n, COUNT_Q) for n in ["real", "real_x3", "real_x3_speech", "real_x5", "speech_only"] for _ in range(3)]
    row = {}
    for (m, n, q), answer in zip(jobs, many(jobs)):
        hit = re.search(r"\d+", answer)
        row.setdefault(n, []).append(int(hit.group()) if hit else "x")
    print(model, row)
