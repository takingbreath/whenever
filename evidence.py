"""Saves what happened, so a wrong event can be inspected and replayed later.

This is a development tool. It saves every frame the server receives and every model
answer. Everything goes to logs/, on this Mac only.
"""

import json
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path

# The oldest pictures go first, so a camera can run for days and the newest footage always stays.
# A hosted server keeps less, with WHENEVER_LOGS_GB.
MAX_BYTES = int(float(os.environ.get("WHENEVER_LOGS_GB") or 20) * 1_000_000_000)
RECENT_CHECKS = 2000


class Evidence:
    def __init__(self, root):
        self.session = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.folder = Path(root) / self.session
        self.frames_dir = self.folder / "frames"
        self.frames_dir.mkdir(parents=True)
        self.lock = threading.Lock()
        self.bytes = 0
        self.next_id = 0
        self.checks = OrderedDict()
        self.stored = deque()

    def _append(self, name, record):
        with self.lock:
            with (self.folder / name).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _store(self, path, jpeg):
        """Writes a picture, and removes the oldest ones when the size limit is reached."""
        with self.lock:
            self.bytes += len(jpeg)
            self.stored.append((path, len(jpeg)))
            old = []
            while self.bytes > MAX_BYTES and len(self.stored) > 1:
                gone, size = self.stored.popleft()
                self.bytes -= size
                old.append(gone)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(jpeg)
        for gone in old:
            gone.unlink(missing_ok=True)

    def save_frame(self, t, jpeg, motion=0.0):
        """Returns the frame's file name."""
        with self.lock:
            self.next_id += 1
            name = f"{self.next_id:07d}.jpg"
        self._store(self.frames_dir / name, jpeg)
        self._append("frames.jsonl", {"name": name, "t": t, "motion": motion})
        return name

    def save_sharp(self, name, jpeg):
        """Keeps a larger copy of a saved frame. Rule code and pictures use it, and sensors do not."""
        if not self.frame_path(self.session, str(name)):
            return False
        self._store(self.frames_dir / "sharp" / name, jpeg)
        return True

    def save_full(self, name, camera, jpeg):
        """Keeps one camera's own full frame for this moment, so zoom has real pixels."""
        if not self.frame_path(self.session, str(name)):
            return False
        self._store(self.frames_dir / "full" / f"{name[:-4]}_{int(camera)}.jpg", jpeg)
        return True

    def full_copies(self, name):
        """Returns [(camera, path)] for the full frames kept at this moment."""
        if not self.frame_path(self.session, str(name)):
            return []
        found = sorted((self.frames_dir / "full").glob(f"{name[:-4]}_*.jpg"))
        return [(int(path.stem.rsplit("_", 1)[1]), path) for path in found]

    def full_path(self, name, camera):
        for index, path in self.full_copies(name):
            if index == camera:
                return path
        return None

    def sharp_path(self, name):
        """Returns the larger copy of a frame if it exists, or else the frame."""
        path = self.frame_path(self.session, str(name))
        if not path:
            return None
        sharp = self.frames_dir / "sharp" / path.name
        return sharp if sharp.exists() else path

    def read_sharp(self, name):
        path = self.sharp_path(name)
        return path.read_bytes() if path else None

    def save_check(self, record):
        with self.lock:
            self.next_id += 1
            check_id = f"c{self.next_id}"
            self.checks[check_id] = {"id": check_id, "session": self.session, "saved_at": time.time(), **record}
            while len(self.checks) > RECENT_CHECKS:
                self.checks.popitem(last=False)
            stored = self.checks[check_id]
        self._append("checks.jsonl", stored)
        return check_id

    def update_check(self, check_id, fields):
        with self.lock:
            if check_id in self.checks:
                self.checks[check_id].update(fields)
        self._append("updates.jsonl", {"check": check_id, **fields})

    def add_step(self, check_id, step):
        """Keeps what a rule did after its event, for the Why view."""
        with self.lock:
            check = self.checks.get(check_id)
            if check is not None:
                check.setdefault("steps", []).append(step)
        self._append("updates.jsonl", {"check": check_id, "step": step})

    def save_feed(self, record):
        self._append("feed.jsonl", {"saved_at": time.time(), **record})

    def label(self, check_id, label):
        self._append("labels.jsonl", {"check": check_id, "label": label, "saved_at": time.time()})

    def check(self, check_id):
        with self.lock:
            return self.checks.get(check_id)

    def read_frame(self, name):
        """Returns a saved frame's bytes, or None. Rule code can choose the name."""
        path = self.frame_path(self.session, str(name))
        return path.read_bytes() if path else None

    def frame_path(self, session, name):
        """Returns a saved frame, or None. Both parts come from the URL, so they are checked."""
        if session != self.session or not (name.endswith(".jpg") and name[:-4].isdigit()):
            return None
        path = self.frames_dir / name
        return path if path.exists() else None
