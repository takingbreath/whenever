"""Saves what happened, so a wrong event can be inspected and replayed later.

This is a development tool. It saves every frame the server receives and every model
answer. Everything goes to logs/, on this Mac only.
"""

import json
import os
import re
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
# A restart starts a new session. The Why view still opens an older check, so a few old sessions
# are read back when someone asks.
OLD_SESSIONS = 3
SESSION = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")


class Evidence:
    def __init__(self, root):
        self.root = Path(root)
        self.session = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.folder = self.root / self.session
        self.frames_dir = self.folder / "frames"
        self.frames_dir.mkdir(parents=True)
        self.lock = threading.Lock()
        self.bytes = 0
        self.next_id = 0
        self.checks = OrderedDict()
        self.old = OrderedDict()
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

    def save_focus(self, name, jpeg):
        """Keeps the picture that a check's model saw, which can be a few cameras and a close-up."""
        if not self.frame_path(self.session, str(name)):
            return False
        self._store(self.frames_dir / "focus" / name, jpeg)
        return True

    def focus_path(self, session, name):
        path = self.frame_path(session, str(name))
        focus = path.parent / "focus" / path.name if path else None
        return focus if focus and focus.exists() else None

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

    def sharp_path(self, name, session=None):
        """Returns the larger copy of a frame if it exists, or else the frame."""
        path = self.frame_path(session or self.session, str(name))
        if not path:
            return None
        sharp = path.parent / "sharp" / path.name
        return sharp if sharp.exists() else path

    def read_sharp(self, name):
        path = self.sharp_path(name)
        return path.read_bytes() if path else None

    def save_check(self, record):
        with self.lock:
            self.next_id += 1
            # The session is part of the name, because each session counts from 1 again.
            check_id = f"c{self.next_id}-{self.session}"
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

    def check(self, check_id, t=None):
        """A check of this session, or of an older one. An old name has no session in it, so t, the
        time of its event, finds the session."""
        check_id = str(check_id)
        with self.lock:
            found = self.checks.get(check_id)
        if found is not None:
            return found
        _, _, session = check_id.partition("-")
        if not session and t is not None:
            session = self.session_at(float(t))
        if not session or not SESSION.fullmatch(session):
            return None
        # This session's file grows, so it is read again each time. Only a very old check gets here.
        return self._old_session(session, keep=session != self.session).get(check_id)

    def session_at(self, t):
        """The session that was running at time t."""
        when = datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S")
        started = sorted(p.name for p in self.root.iterdir() if p.is_dir() and SESSION.fullmatch(p.name))
        before = [name for name in started if name[:15] <= when]
        return before[-1] if before else None

    def _old_session(self, session, keep=True):
        with self.lock:
            if keep and session in self.old:
                self.old.move_to_end(session)
                return self.old[session]
        checks = {}
        folder = self.root / session
        for name in ("checks.jsonl", "updates.jsonl"):
            try:
                lines = (folder / name).read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if name == "checks.jsonl" and record.get("id"):
                    checks[record["id"]] = record
                elif record.get("check") in checks:
                    stored = checks[record["check"]]
                    if "step" in record:
                        stored.setdefault("steps", []).append(record["step"])
                    else:
                        stored.update({k: v for k, v in record.items() if k != "check"})
        if keep:
            with self.lock:
                self.old[session] = checks
                while len(self.old) > OLD_SESSIONS:
                    self.old.popitem(last=False)
        return checks

    def read_frame(self, name):
        """Returns a saved frame's bytes, or None. Rule code can choose the name."""
        path = self.frame_path(self.session, str(name))
        return path.read_bytes() if path else None

    def frame_path(self, session, name):
        """Returns a saved frame, or None. Both parts come from the URL, so they are checked."""
        if not (name.endswith(".jpg") and name[:-4].isdigit()):
            return None
        if session == self.session:
            path = self.frames_dir / name
        elif SESSION.fullmatch(str(session)):
            path = self.root / session / "frames" / name
        else:
            return None
        return path if path.exists() else None
