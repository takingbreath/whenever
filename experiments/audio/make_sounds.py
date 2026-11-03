"""Builds test sounds with known answers. Nothing here is a real recording of a cooker.

The whistle is synthetic: a pitched tone with steam-like noise. The bell is the Mac Glass sound.
"""

import array
import json
import math
import random
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

OUT = Path(sys.argv[1])
RATE = 24000
random.seed(7)


def silence(seconds):
    return [0.0] * int(seconds * RATE)


def room(seconds, level):
    """Soft, low-pitched noise, like a fan in a kitchen."""
    out, last = [], 0.0
    for _ in range(int(seconds * RATE)):
        last = 0.985 * last + 0.015 * random.uniform(-1, 1)
        out.append(last * level * 8)
    return out


def whistle(seconds=2.4, base=2900):
    n = int(seconds * RATE)
    out, phase, low = [], 0.0, 0.0
    for i in range(n):
        t = i / RATE
        sweep = 400 * math.sin(math.pi * min(t / seconds, 1))
        wobble = 35 * math.sin(2 * math.pi * 28 * t)
        phase += 2 * math.pi * (base + sweep + wobble) / RATE
        tone = 0.6 * math.sin(phase) + 0.2 * math.sin(2 * phase)
        noise = random.uniform(-1, 1)
        low = 0.7 * low + 0.3 * noise
        hiss = noise - low
        envelope = min(t / 0.15, 1) * min((seconds - t) / 0.35, 1)
        out.append(envelope * (0.55 * tone + 0.45 * hiss) * 0.8)
    return out


def clap():
    n = int(0.09 * RATE)
    return [random.uniform(-1, 1) * 0.9 * math.exp(-i / (0.018 * RATE)) for i in range(n)]


def from_file(path):
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
             "-c:a", "pcm_s16le", f.name], check=True)
        with wave.open(f.name) as w:
            data = array.array("h", w.readframes(w.getnframes()))
    return [v / 32768 for v in data]


def speak(text):
    with tempfile.NamedTemporaryFile(suffix=".aiff") as f:
        subprocess.run(["say", "-o", f.name, text], check=True)
        return from_file(f.name)


def place(length_s, events, background=0.03):
    """Mix events into room noise. events is a list of (start second, samples)."""
    mix = room(length_s, background)
    for start, samples in events:
        at = int(start * RATE)
        for i, value in enumerate(samples):
            if at + i < len(mix):
                mix[at + i] += value
    return mix


def save(name, samples):
    data = array.array("h", (int(max(-1, min(1, s)) * 32767) for s in samples))
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


GLASS = from_file("/System/Library/Sounds/Glass.aiff")
SUBMARINE = from_file("/System/Library/Sounds/Submarine.aiff")
FUNK = from_file("/System/Library/Sounds/Funk.aiff")
SPEECH = speak("Please check the pressure cooker on the stove. Dinner is almost ready.")

truth = {}


def add(name, samples, **answers):
    save(name, samples)
    truth[name] = answers


# Files for "what is this sound": one thing each.
add("room_only", place(6, []), kind="room", whistles=0, bells=0, claps=0)
add("speech", place(6, [(0.5, SPEECH)]), kind="speech", whistles=0, bells=0, claps=0)
add("whistle_one", place(6, [(1.5, whistle())]), kind="whistle", whistles=1, bells=0, claps=0)
add("bell_one", place(6, [(1.5, GLASS)]), kind="bell", whistles=0, bells=1, claps=0)
add("submarine", place(6, [(1.5, SUBMARINE)]), kind="other", whistles=0, bells=0, claps=0)
add("funk", place(6, [(1.5, FUNK)]), kind="other", whistles=0, bells=0, claps=0)
add("claps_two", place(6, [(1.5, clap()), (2.4, clap())]), kind="clap", whistles=0, bells=0, claps=2)

# Files for "how many": gaps are uneven, and speech and other sounds share the room.
add("whistles_2", place(20, [(2, whistle()), (11, whistle(2.0, 2750))]), whistles=2, bells=0, claps=0)
add("whistles_3", place(24, [(1.5, whistle()), (8, whistle(2.0, 2750)), (17, whistle(2.8, 3000))]),
    whistles=3, bells=0, claps=0)
add("whistles_3_speech", place(24, [(1.5, whistle()), (5, SPEECH), (12, whistle(2.0, 2750)), (18, whistle(2.8, 3000))]),
    whistles=3, bells=0, claps=0)
add("whistles_5", place(30, [(1, whistle()), (6, whistle(2.0, 2750)), (12, whistle(2.2, 3000)),
                              (18, whistle(2.4, 2850)), (25, whistle(2.0, 2950))]), whistles=5, bells=0, claps=0)
add("bells_3", place(10, [(1, GLASS), (4, GLASS), (7.5, GLASS)]), whistles=0, bells=3, claps=0)
add("bells_5", place(14, [(1, GLASS), (3.2, GLASS), (5.5, GLASS), (8.5, GLASS), (11, GLASS)]),
    whistles=0, bells=5, claps=0)
add("claps_3", place(6, [(1, clap()), (1.8, clap()), (3.1, clap())]), whistles=0, bells=0, claps=3)

(OUT / "truth.json").write_text(json.dumps(truth, indent=1))
print(len(truth), "files")
