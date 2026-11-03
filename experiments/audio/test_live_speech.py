"""Streams clips to Voice Transcribe at real-time pace and measures how soon the words appear."""

import asyncio
import array
import json
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meta_api import load_key

OUT = Path(sys.argv[1])
KEY = load_key(ROOT / ".env")
RATE = 24000
FRAME_BYTES = RATE * 2 * 80 // 1000


def speak(text, voice=None):
    with tempfile.NamedTemporaryFile(suffix=".aiff") as f:
        subprocess.run(["say", *(["-v", voice] if voice else []), "-o", f.name, text], check=True)
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f.name, "-ac", "1", "-ar", str(RATE),
                              "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return array.array("h", raw)


def build(name, speech, lead_s=2.0, tail_s=3.0):
    """Quiet lead, then the speech, then a quiet tail. Returns the moment that speech ends."""
    pcm = array.array("h", [0] * int(lead_s * RATE)) + speech + array.array("h", [0] * int(tail_s * RATE))
    ends_at = lead_s + len(speech) / RATE
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm.tobytes())
    return pcm.tobytes(), ends_at


async def stream(pcm, word):
    uri = f"wss://api.meta.ai/v1/asr/realtime?sessionId=test-{uuid.uuid4()}"
    seen = {"word_at": None, "final_at": None, "text": "", "partials": 0}
    async with websockets.connect(uri, open_timeout=30) as ws:
        await ws.send(json.dumps({
            "authorization": {"accessToken": f"Bearer {KEY}"}, "audioEncoding": "PCM_24KHZ",
            "model": "muse-voice-transcribe-1.0", "mode": "ENDPOINTING",
            "partialMode": "CUMULATIVE", "emitAudioProgress": False,
        }))
        hello = json.loads(await ws.recv())
        if "sessionId" not in hello:
            raise RuntimeError(f"handshake failed: {str(hello)[:200]}")
        started = time.perf_counter()

        async def receive():
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                if event.get("type") == "error":
                    raise RuntimeError(str(event)[:200])
                if event.get("type") != "transcript":
                    continue
                now = time.perf_counter() - started
                text = event.get("transcript", "")
                seen["partials"] += 1
                seen["text"] = text or seen["text"]
                if word and seen["word_at"] is None and word in text.lower():
                    seen["word_at"] = now
                if event.get("final") and seen["final_at"] is None:
                    seen["final_at"] = now

        task = asyncio.create_task(receive())
        for i in range(0, len(pcm), FRAME_BYTES):
            await ws.send(pcm[i:i + FRAME_BYTES])
            wait = (i + FRAME_BYTES) / (RATE * 2) - (time.perf_counter() - started)
            if wait > 0:
                await asyncio.sleep(wait)
        await ws.send(json.dumps({"type": "endStream"}))
        await asyncio.wait_for(task, timeout=15)
    return seen


async def main():
    voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    hindi = next((l.split()[0] for l in voices.splitlines() if "hi_IN" in l), None)
    cases = [("dinner", speak("Dinner is ready"), "ready"),
             ("approve", speak("Approve this change"), "approve")]
    if hindi:
        cases.append(("hindi", speak("खाना तैयार है", hindi), "तैयार"))
    for name, speech, word in cases:
        pcm, ends_at = build(name, speech)
        word_lags, final_lags, texts = [], [], []
        for _ in range(4):
            seen = await stream(pcm, word)
            texts.append(seen["text"])
            if seen["word_at"] is not None:
                word_lags.append(seen["word_at"] - ends_at)
            if seen["final_at"] is not None:
                final_lags.append(seen["final_at"] - ends_at)
        print(f"{name}: speech ends at {ends_at:.2f} s, word seen in {len(word_lags)} of 4")
        if word_lags:
            print(f"  word appears {statistics.median(word_lags):+.2f} s after speech ends (range {min(word_lags):+.2f} to {max(word_lags):+.2f})")
        if final_lags:
            print(f"  final turn {statistics.median(final_lags):+.2f} s after speech ends")
        print(f"  text: {sorted(set(texts))}")
    sounds = Path(sys.argv[2])
    for name in ["whistle_one", "bell_one"]:
        with wave.open(str(sounds / f"{name}.wav")) as w:
            pcm = w.readframes(w.getnframes())
        seen = await stream(pcm, None)
        print(f"{name}: partials {seen['partials']}, text {seen['text']!r}")


asyncio.run(main())
