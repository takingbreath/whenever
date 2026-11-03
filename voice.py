"""Live speech to text, for rules that a person speaks.

The page sends raw audio here, and this process forwards it to Meta, so the API key never
reaches the browser.
"""

import json
import threading
import uuid

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

URL = "wss://api.meta.ai/v1/asr/realtime?sessionId={}"
MODEL = "muse-voice-transcribe-1.0"
CONNECT_TIMEOUT_S = 10
# The final text can differ from the last partial, so wait for it after the key is released.
FINAL_WAIT_S = 4


class VoiceError(Exception):
    pass


class VoiceSession:
    def __init__(self, key):
        self.id = uuid.uuid4().hex[:8]
        self.text = ""
        self.error = ""
        self.finished = threading.Event()
        try:
            self.socket = connect(URL.format(self.id), open_timeout=CONNECT_TIMEOUT_S)
            self.socket.send(json.dumps({
                "authorization": {"accessToken": f"Bearer {key}"},
                "audioEncoding": "PCM_24KHZ", "model": MODEL, "mode": "PUSH_TO_TALK",
                "partialMode": "CUMULATIVE", "emitAudioProgress": False,
            }))
            reply = json.loads(self.socket.recv(timeout=CONNECT_TIMEOUT_S))
        except (OSError, TimeoutError, ValueError, WebSocketException) as e:
            raise VoiceError(f"Voice Transcribe did not connect: {e}") from None
        if "sessionId" not in reply:
            raise VoiceError(f"Voice Transcribe refused the session: {str(reply)[:200]}")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for message in self.socket:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                if event.get("type") == "error":
                    self.error = str(event.get("message"))[:200]
                elif event.get("transcript"):
                    self.text = event["transcript"]
        except (OSError, ValueError, WebSocketException) as e:
            self.error = str(e)[:200]
        finally:
            self.finished.set()

    def audio(self, chunk):
        """chunk is 16-bit mono PCM at 24 kHz. Returns the words so far."""
        try:
            self.socket.send(chunk)
        except (OSError, WebSocketException) as e:
            self.error = str(e)[:200]
        return self.text

    def finish(self):
        try:
            self.socket.send(json.dumps({"type": "endStream"}))
            self.finished.wait(FINAL_WAIT_S)
            self.socket.close()
        except (OSError, WebSocketException):
            pass
        return self.text
