"""Tests hold-to-talk through the real page, with a spoken file as a fake microphone.

Needs the server on port 8787. Chrome plays the file into the page, this script holds Space,
and the sentence box must fill. The page claims the camera, so reload any other open page after.
"""

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import meta_api  # noqa: F401  (adds vendor/ to the path)
from websockets.sync.client import connect

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
SENTENCE = "Count how many times I drink water from this bottle."
HOLD_S = 6


def main():
    work = Path(tempfile.mkdtemp())
    speech = work / "speech.wav"
    spoken = work / "spoken.aiff"
    subprocess.run(["say", "-o", str(spoken), SENTENCE], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(spoken), "-ar", "48000", "-ac", "1",
                    "-c:a", "pcm_s16le", "-bitexact", "-map_metadata", "-1", str(speech)], check=True)
    chrome = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={work / 'profile'}",
        "--no-first-run", "--disable-features=AudioServiceSandbox", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={speech}", "--autoplay-policy=no-user-gesture-required",
        "http://127.0.0.1:8787/?clip=1",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        run()
    finally:
        chrome.terminate()


def run():
    page = None
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as response:
                pages = [t for t in json.load(response) if t["type"] == "page" and "8787" in t["url"]]
            if pages:
                page = pages[0]
                break
        except OSError:
            pass
        time.sleep(0.25)
    if not page:
        sys.exit("Chrome did not open the page.")

    with connect(page["webSocketDebuggerUrl"], max_size=None) as socket:
        counter = {"id": 0}

        def send(method, **params):
            counter["id"] += 1
            socket.send(json.dumps({"id": counter["id"], "method": method, "params": params}))
            while True:
                reply = json.loads(socket.recv(timeout=20))
                if reply.get("id") == counter["id"]:
                    return reply.get("result", {})

        def value(expression):
            return send("Runtime.evaluate", expression=expression, returnByValue=True)["result"].get("value")

        def key(kind):
            send("Input.dispatchKeyEvent", type=kind, code="Space", key=" ", windowsVirtualKeyCode=32)

        time.sleep(2)
        value("""window.sentChunks = 0; window.failures = [];
                 const realSend = sendAudio;
                 sendAudio = async (session, chunk) => {
                   window.sentChunks++;
                   window.peak = Math.max(window.peak || 0, ...new Int16Array(chunk));
                   try { await realSend(session, chunk); }
                   catch (error) { window.failures.push(String(error)); throw error; }
                 }; true""")
        key("keyDown")
        started = time.time()
        seen = ""
        while time.time() - started < HOLD_S:
            text = value("document.getElementById('sentence').value")
            if text != seen:
                print(f"{time.time() - started:5.2f}s {text}", flush=True)
                seen = text
            time.sleep(0.2)
        print("button:", value("document.getElementById('talk').textContent"))
        print("audio:", value("JSON.stringify({state: audioContext?.state, rate: audioContext?.sampleRate, "
                              "chunks: window.sentChunks, peak: window.peak, failures: window.failures.slice(0, 2), "
                              "track: micStream?.getAudioTracks()[0]?.readyState})"))
        key("keyUp")
        time.sleep(2)
        print("final:", repr(value("document.getElementById('sentence').value")))
        print("note:", repr(value("document.getElementById('form-note').textContent")))


main()
