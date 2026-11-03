"""Streams a spoken sentence through VoiceSession at real-time pace, as the page does."""

import array
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import load_key
from voice import VoiceSession

RATE = 24000
CHUNK_BYTES = 4800  # 100 ms, the same size as the page sends
SENTENCE = sys.argv[1] if len(sys.argv) > 1 else "Count how many times I drink water from this bottle."

with tempfile.NamedTemporaryFile(suffix=".aiff") as f:
    subprocess.run(["say", "-o", f.name, SENTENCE], check=True)
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f.name, "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
pcm += bytes(array.array("h", [0] * (RATE // 2)).tobytes())

started = time.perf_counter()
session = VoiceSession(load_key(ROOT / ".env"))
print(f"connected {time.perf_counter() - started:.2f}s")
last = ""
# The first 1.5 s of speech waits for the connection, so it arrives in one burst.
backlog = 15 * CHUNK_BYTES
for index in range(0, backlog, CHUNK_BYTES):
    session.audio(pcm[index:index + CHUNK_BYTES])
started = time.perf_counter() - 1.5
for index in range(backlog, len(pcm), CHUNK_BYTES):
    text = session.audio(pcm[index:index + CHUNK_BYTES])
    if text != last:
        print(f"{time.perf_counter() - started:5.2f}s {text}", flush=True)
        last = text
    wait = (index + CHUNK_BYTES) / (RATE * 2) - (time.perf_counter() - started)
    if wait > 0:
        time.sleep(wait)
ended = time.perf_counter()
final = session.finish()
print(f"final {time.perf_counter() - ended:.2f}s after release: {final!r} error={session.error!r}")
