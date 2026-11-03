"""Asks a coding model to turn each sentence into sensors plus a small Python script."""

import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES
from meta_api import Client, _output_text, load_key

OUT = Path(sys.argv[1])
MODELS = sys.argv[2:] or ["muse-wonder-1.0"]
client = Client(load_key(ROOT / ".env"))

PROMPT = """\
You write small programs for a camera and microphone. People describe what they want in one sentence.

Sensors only say what is true right now. A sensor never counts, measures time or remembers. Your code does all of that.
Return the sensors and the code.

Sensor fields:
- id: a short name.
- kind: "see" for the camera, "hear_speech" for spoken words, "hear_sound" for other sounds.
- look_for: a description of the physical thing or sound, for the sensor model.
- question: a yes/no question about the present moment.

The runtime calls these Python functions. Define only the ones you need.
- on_event(sensor, t, state, api): the sensor just turned on.
- on_end(sensor, t, duration, state, api): the sensor just turned off. duration is in seconds.
- on_tick(t, state, api): runs once each second.

t is in seconds since midnight on the first day, so t % 86400 is the time of day. An event is one on-off pair.
state is a dict that keeps its values between calls. It starts empty, so use state.setdefault.
api.notify(text) shows a notification. api.say(text) speaks.

Rules: plain Python only. No imports. No open, eval, exec or names that start with an underscore.
Allowed builtins: len, min, max, sum, abs, round, int, float, str, bool, list, dict, set, tuple, range, sorted, enumerate, any, all.
Fire an action once for each time the condition is met. Do not repeat it while the same condition continues.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sensors", "code"],
    "properties": {
        "sensors": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "kind", "look_for", "question"],
            "properties": {
                "id": {"type": "string"},
                "kind": {"type": "string", "enum": ["see", "hear_speech", "hear_sound"]},
                "look_for": {"type": "string"}, "question": {"type": "string"}}}},
        "code": {"type": "string"},
    },
}


def write(model, case):
    started = time.perf_counter()
    raw = client._post({
        "model": model, "max_output_tokens": 6000, "reasoning": {"effort": "low"},
        "instructions": PROMPT, "input": case["sentence"],
        "text": {"format": {"type": "json_schema", "name": "rule_program", "schema": SCHEMA, "strict": True}},
    }, timeout=120)
    data = json.loads(raw)
    program = json.loads(_output_text(data))
    usage = data.get("usage") or {}
    return {"name": case["name"], "seconds": round(time.perf_counter() - started, 1),
            "tokens": [usage.get("input_tokens"), usage.get("output_tokens")], **program}


for model in MODELS:
    with cf.ThreadPoolExecutor(4) as pool:
        programs = list(pool.map(lambda c: write(model, c), CASES))
    (OUT / f"{model}.json").write_text(json.dumps(programs, indent=1, ensure_ascii=False))
    print(model, [(p["name"], p["seconds"]) for p in programs])
