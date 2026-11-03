"""Local server for the Whenever prototype.

The page sends frames here. Sensors turn frames into events, and each rule's own code reacts
to those events in its sandbox. The API key stays in this process and never reaches the
browser or a rule. Frames stay in memory and are not saved.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

import actions
import sandbox
import writer
from camera_control import ControlError, Controls
from connectors import ACTIONS_FILE, ActionError, Connectors, check_draft
from journal import JOURNAL_FILE, Journal
from engine import SensorState, pick_frames, spread
from evidence import Evidence
from meta_api import ApiError, Client, load_key
from PIL import Image, ImageFilter, ImageStat  # noqa: E402  (meta_api adds vendor/ to the path)
from voice import VoiceError, VoiceSession

PORT = int(os.environ.get("WHENEVER_PORT", 8787))
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
LOGS_DIR = ROOT / "logs"
# A hosted server names its public host, such as wheneverwhatever.si. Caddy brings HTTPS traffic here.
HOSTS = tuple(h.strip() for h in os.environ.get("WHENEVER_HOST", "").split(",") if h.strip())
# The access code of a hosted server. It is a file, not an environment value, because a rule could
# read the environment of a process of the same user. With no file, as on a Mac, nobody logs in.
ACCESS_FILE = ROOT / "access.env"
SESSIONS_FILE = ROOT / "data" / "sessions.json"
SESSION_S = 30 * 86_400
MAX_LOGINS_PER_MINUTE = 10

DATA_DIR = ROOT / "data"
RULES_FILE = DATA_DIR / "rules.json"
MAX_RULES = 5
MAX_SCHEDULES = 4
MIN_SCHEDULE_S = 60
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Schedules use the person's own clock. A hosted server sets WHENEVER_TZ, such as Asia/Kolkata.
ZONE = ZoneInfo(os.environ["WHENEVER_TZ"]) if os.environ.get("WHENEVER_TZ") else datetime.now().astimezone().tzinfo
MAX_ACTS_PER_MINUTE = 12
MAX_ACTION_WORDS = 6_000
MAX_DRAFTS = 10
MAX_FRAME_BYTES = 1_000_000
MAX_SHARP_BYTES = 3_000_000
MAX_FULL_BYTES = 8_000_000
MAX_CAMERAS = 16
MAX_JSON_BYTES = 24_000_000
MAX_LABEL_BYTES = 1_000
MAX_CLAIM_BYTES = 8_000
MAX_SENTENCE = 2_000
MAX_AUDIO_BYTES = 64_000
MAX_POINTS = 20

# Limits on rule code. Rules can ask models anything, so a mistake must not cost much.
MAX_CALLS_PER_MINUTE = 60
MAX_ASK_FRAMES = 12
MAX_CHECK_THREADS = 6
MAX_RULE_FRAMES = 24
MAX_BEFORE_S = 900
MAX_AFTER_S = 30
MAX_ARM_S = 900
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
MAX_GALLERY_FRAMES = 24
MAX_GALLERIES_PER_MINUTE = 6
MAX_GALLERY_SHOTS = 12
GALLERY_MIN_CONFIDENCE = 0.5
# Pictures in a gallery must differ, so they stay this far apart in time.
GALLERY_GAP_S = 0.7
GALLERY_ZOOM_PAD = 0.8
GALLERY_ZOOM_SIZE = 480
GALLERY_SCORE_SIZE = 64
GALLERY_USEFUL_WIDTH = 60
# A moving lens has 2 times the pixels on the same thing, and the score below cannot see this.
MOVING_LENS_BONUS = 1.5
MAX_CONTROLS_PER_MINUTE = 12
PAGE_MOVE_S = 0.8
PAGE_LIGHT_S = 30

# 20 seconds at the highest capture rate.
FRAME_BUFFER = 240
MAX_CAPTURE_FPS = 12
DEFAULT_CAPTURE_FPS = 3
# Every saved frame stays on disk. This much of the list stays in memory: 1 hour at 12 frames a second.
TIMELINE_FRAMES = 43_200
MIN_CHECK_EVERY_S = 0.8
MOTION_MIN = 6
# Checks overlap, so a slow model answer cannot delay the next check.
MAX_SENSOR_CHECKS = 3
STRONG_HIT = 0.85
DETECTION_THREADS = 12
ANALYSIS_THREADS = 32

# A near miss goes to a review of the recent past. Each sure "no" doubles the wait, so an idle camera
# and a thing that only looks close cost little. The wait stops at the span, so no frame goes unread.
REVIEW_SPAN_S = 24
MOMENT_REVIEW_SPAN_S = 8
MIN_REVIEW_SPAN_S = 5
MOMENT_MIN_REVIEW_SPAN_S = 2
REVIEW_FRAMES = 10
REVIEW_PASS = 0.8
# One weak yes is not enough, but two in a row are, as with two yes answers on a check.
REVIEW_WEAK = 0.6
REVIEW_EVERY_S = 6
REVIEW_MAX_WAIT_S = REVIEW_SPAN_S
# While a state is on, a review runs this often, to find anyone new who is doing the same thing.
# It must run before the state clears, so a short clear time gets a shorter beat.
WATCH_EVERY_S = 8
MIN_WATCH_S = 4
SUBJECT_PASS = 0.8
MAX_SUBJECTS = 6

TICK_S = 1.0
CAMERA_LOST_S = 25
# A security camera sends the page a live picture, so the page shows it in place of the Mac's camera.
CAMERA_PAGE_PREFIX = "camera-"
LIVE_LOST_S = 5.0
LIVE_WAIT_S = 2.0
MAX_PENDING = 30
FEED_LENGTH = 200

# Estimates from the published prices. Spark and Wonder use the real token counts.
PRICE_SEE = 0.0025
# The spend limit is for each day, in the person's own time.
SPEND_LIMIT_USD = float(os.environ.get("WHENEVER_SPEND_LIMIT") or 5.00)

STATIC = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/mic.js": ("web/mic.js", "text/javascript; charset=utf-8"),
    "/style.css": ("web/style.css", "text/css; charset=utf-8"),
    "/testdata/clip.webm": ("testdata/clip.webm", "video/webm"),
}


Frame = namedtuple("Frame", "t jpeg name motion")
OUTLINE_FIELDS = ("x1", "y1", "x2", "y2", "w", "h", "path", "mask_w", "mask_h")


def number(value, low, high, default):
    try:
        return min(max(float(value), low), high)
    except (TypeError, ValueError):
        return default


def proof_frame(result):
    """The frame that best shows the event. Spark numbers its frames from 1."""
    if result.get("proof"):
        return result["proof"]
    names = result.get("frames") or []
    answers = result.get("answers") or []
    best = answers[0].get("best_frame", 1) if answers else 1
    if 1 <= best <= len(names):
        return names[best - 1]
    return names[0] if names else None


def centre(box):
    return (box["x1"] + box["x2"]) / 2, (box["y1"] + box["y2"]) / 2


def zoom(picture, box):
    """Cuts out a box with space around it. It uses only real pixels, and invents no detail."""
    scale = picture.width / box["w"]
    x1, y1, x2, y2 = (box[key] * scale for key in ("x1", "y1", "x2", "y2"))
    pad = max(x2 - x1, y2 - y1) * GALLERY_ZOOM_PAD
    cut = picture.crop((max(0, int(x1 - pad)), max(0, int(y1 - pad)),
                        min(picture.width, int(x2 + pad)), min(picture.height, int(y2 + pad))))
    grow = GALLERY_ZOOM_SIZE / max(cut.size)
    return cut.resize((round(cut.width * grow), round(cut.height * grow)), Image.LANCZOS)


def sharpness(picture, box):
    """Scores a view on this computer, at no cost. Blur counts most, then size up to a useful width.

    A fixed size makes the blur score fair: a large blurred face would otherwise beat a small sharp one.
    """
    scale = picture.width / box["w"]
    tight = picture.crop(tuple(int(box[key] * scale) for key in ("x1", "y1", "x2", "y2"))).convert("L")
    edges = tight.resize((GALLERY_SCORE_SIZE, GALLERY_SCORE_SIZE)).filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0] ** 0.5 * min(1.0, tight.width / GALLERY_USEFUL_WIDTH)


def pick_shots(shots, count):
    """The best shots, spaced apart in time, so each picture adds something new."""
    chosen = []
    for shot in sorted(shots, key=lambda item: item["score"], reverse=True):
        if all(abs(shot["t"] - other["t"]) >= GALLERY_GAP_S for other in chosen):
            chosen.append(shot)
        if len(chosen) == count:
            break
    return chosen


def rule_picture(folder, path):
    """Returns a picture inside a rule's folder, or None. Rule code chooses the path."""
    root = Path(folder).resolve()
    file = (root / str(path)).resolve()
    if root in file.parents and file.suffix.lower() in IMAGE_TYPES and file.is_file():
        return file
    return None


class Sensor:
    def __init__(self, fields):
        self.id = str(fields["id"])[:40]
        self.target = str(fields.get("target") or "").strip()[:40]
        self.outline = str(fields.get("outline") or "").strip()[:40]
        self.question = str(fields.get("question") or "").strip()[:300]
        self.requires = [str(f)[:200] for f in fields.get("requires") or []][:4] or [self.question]
        self.min_count = max(1, int(fields.get("min_count") or 1))
        # Wonder chooses the timing for each sensor. These limits only protect the system.
        self.pattern = "moment" if fields.get("pattern") == "moment" else "state"
        self.frames_per_second = number(fields.get("frames_per_second"), 1, MAX_CAPTURE_FPS, DEFAULT_CAPTURE_FPS)
        self.check_every_s = number(fields.get("check_every_s"), MIN_CHECK_EVERY_S, 60, 1.5)
        self.frames_per_check = int(number(fields.get("frames_per_check"), 1, 8, 6))
        self.clear_after_s = number(fields.get("clear_after_s"), 0.3, 600, 3)
        self.watch_s = min(WATCH_EVERY_S, max(MIN_WATCH_S, self.clear_after_s / 2))
        self.min_motion = number(fields.get("min_motion"), MOTION_MIN, 200, MOTION_MIN)
        self.reason = str(fields.get("reason") or "")[:200]
        # None means that the sensor always runs. Otherwise it runs only until this time.
        self.armed_until = 0.0 if fields.get("starts_off") else None
        self.state = SensorState(self.pattern, self.clear_after_s)
        self.lock = threading.Lock()
        self.running = 0
        self.next_turn = 0
        self.settled_turn = 0
        self.finished = {}
        self.last_check = 0.0
        self.covered_until = 0.0
        # A review never reads frames before this time, so an old occurrence cannot fire again.
        self.review_from = 0.0
        self.review_after = 0.0
        self.review_wait = REVIEW_EVERY_S
        self.reviewing = False
        self.weak_yes = False
        # The proof frames of each person or thing in the state that is on.
        self.subjects = []

    def awake(self, t):
        return self.armed_until is None or t <= self.armed_until

    def arm(self, now, seconds, since=None):
        """A new wait starts clean. since keeps the frames that came while the rule was busy."""
        with self.lock:
            self.armed_until = now + seconds
            self.state = SensorState(self.pattern, self.clear_after_s)
            self.covered_until = now if since is None else since
            self.last_check = 0.0

    def reset(self, now):
        """Forgets the current event and every frame before now. A waiting job ends."""
        with self.lock:
            if self.armed_until is not None:
                self.armed_until = 0.0
            self.state = SensorState(self.pattern, self.clear_after_s)
            self.covered_until = self.review_from = now
            self.last_check = self.review_after = 0.0
            self.review_wait = REVIEW_EVERY_S
            self.weak_yes = False
            self.subjects = []

    def public(self):
        return {"id": self.id, "target": self.target, "outline": self.outline, "question": self.question,
                "starts_off": self.armed_until is not None,
                "requires": self.requires, "min_count": self.min_count, "on": self.state.on,
                "pattern": self.pattern, "frames_per_second": self.frames_per_second,
                "check_every_s": self.check_every_s, "frames_per_check": self.frames_per_check,
                "clear_after_s": self.clear_after_s, "min_motion": self.min_motion, "reason": self.reason}


def parse_at(text):
    """"09:30" becomes (9, 30). Anything else is None."""
    hour, sep, minute = str(text or "").strip().partition(":")
    if sep and hour.isdigit() and minute.isdigit() and int(hour) < 24 and int(minute) < 60:
        return int(hour), int(minute)
    return None


class Schedule:
    """A trigger at a time, with no camera. A report is a rule with a schedule."""

    def __init__(self, fields, now=None):
        self.id = str(fields["id"])[:40]
        self.at = parse_at(fields.get("at"))
        self.every_s = max(MIN_SCHEDULE_S, float(fields.get("every_minutes") or 60) * 60)
        self.days = [day for day in fields.get("days") or [] if day in DAYS]
        self.next_t = self._next(time.time() if now is None else now)

    def _next(self, now):
        if not self.at:
            return now + self.every_s
        today = datetime.fromtimestamp(now, ZONE)
        for add in range(8):
            day = today + timedelta(days=add)
            due = day.replace(hour=self.at[0], minute=self.at[1], second=0, microsecond=0)
            if due.timestamp() > now and (not self.days or DAYS[due.weekday()] in self.days):
                return due.timestamp()
        return now + 86_400

    def due(self, now):
        """True once for each time it comes. A time missed while the server was off is skipped."""
        if now < self.next_t:
            return False
        self.next_t = self._next(now)
        return True

    def public(self):
        when = f"{self.at[0]:02d}:{self.at[1]:02d}" if self.at else f"every {self.every_s / 60:g} min"
        return {"id": self.id, "when": when, "days": self.days, "next": self.next_t}


class Rule:
    def __init__(self, app, rule_id, sentence, fields, folder, pointed=None):
        self.app = app
        self.id = rule_id
        self.sentence = sentence
        self.name = str(fields.get("name") or "Rule")[:60]
        self.plan = [str(step)[:90] for step in fields.get("plan") or []][:6]
        self.sensors = [Sensor(s) for s in fields.get("sensors") or []][:6]
        self.schedules = [Schedule(s) for s in fields.get("schedules") or []][:MAX_SCHEDULES]
        self.folder = folder
        self.pointed = pointed
        self.calls = deque()
        self.galleries = deque()
        self.controls = deque()
        self.acts = deque()
        self.lit = set()
        self.values = {}
        self.fired = 0
        self.pending = 0
        self.worker = None
        self.paused = False
        self.send_lock = threading.Lock()

    def start(self):
        self.worker = sandbox.start_worker(self.folder)
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, message):
        if not self.worker or self.worker.poll() is not None:
            return
        if self.pending > MAX_PENDING:
            self.app.add_feed(self, "error", "The rule's code stopped responding, so it was stopped.")
            self.stop()
            return
        with self.send_lock:
            try:
                self.worker.stdin.write(json.dumps(message) + "\n")
                self.worker.stdin.flush()
                self.pending += 1
            except OSError:
                pass

    def reply(self, message):
        """Answers a call from the rule's code. This does not count as pending work."""
        if not self.worker or self.worker.poll() is not None:
            return
        with self.send_lock:
            try:
                self.worker.stdin.write(json.dumps(message) + "\n")
                self.worker.stdin.flush()
            except OSError:
                pass

    def allow_call(self):
        now = time.time()
        while self.calls and now - self.calls[0] > 60:
            self.calls.popleft()
        if len(self.calls) >= MAX_CALLS_PER_MINUTE:
            raise ValueError(f"This rule made more than {MAX_CALLS_PER_MINUTE} model calls in a minute.")
        self.calls.append(now)

    def allow_gallery(self):
        now = time.time()
        while self.galleries and now - self.galleries[0] > 60:
            self.galleries.popleft()
        if len(self.galleries) >= MAX_GALLERIES_PER_MINUTE:
            raise ValueError(f"This rule asked for more than {MAX_GALLERIES_PER_MINUTE} galleries in a minute.")
        self.galleries.append(now)

    def allow_control(self):
        now = time.time()
        while self.controls and now - self.controls[0] > 60:
            self.controls.popleft()
        if len(self.controls) >= MAX_CONTROLS_PER_MINUTE:
            raise ValueError(f"This rule used a camera more than {MAX_CONTROLS_PER_MINUTE} times in a minute.")
        self.controls.append(now)

    def allow_act(self):
        now = time.time()
        while self.acts and now - self.acts[0] > 60:
            self.acts.popleft()
        if len(self.acts) >= MAX_ACTS_PER_MINUTE:
            raise ValueError(f"This rule ran more than {MAX_ACTS_PER_MINUTE} actions in a minute.")
        self.acts.append(now)

    def _read(self):
        for line in self.worker.stdout:
            try:
                self._handle(json.loads(line))
            except ValueError:
                continue
        if self.app.entries.get(self.id) is self:
            trace = self.worker.stderr.read()[-400:] if self.worker.stderr else ""
            self.app.add_feed(self, "error", "The rule's code stopped. " + trace)

    def _handle(self, message):
        kind = message.get("do")
        if kind == "done":
            self.pending = max(0, self.pending - 1)
        elif kind == "call":
            self.app.start_call(self, message)
        elif self.paused:
            # A job that was running when the person pressed Stop must not act afterwards.
            return
        elif kind == "show":
            self.values[message["name"]] = message["value"]
        elif kind == "log":
            self.app.add_feed(self, "log", message["text"])
        elif kind == "error":
            self.app.add_feed(self, "error", message["trace"][-300:])
        elif kind == "draw":
            self.fired += 1
            self.app.draw(self, message["text"], message.get("frame"), message.get("check"), message.get("target"))
        elif kind == "image":
            self.fired += 1
            self.app.image(self, message["path"], message.get("caption", ""), message.get("check"))
        elif kind == "record":
            self.app.record(self, message["name"], message.get("values") or {})
        elif kind in ("notify", "say"):
            self.fired += 1
            actions.run({"action": kind, "text": message["text"]})
            self.app.add_feed(self, kind, message["text"])
            if kind == "notify":
                self.app.alert(self, message["text"], message.get("picture"))

    def stop(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.kill()

    def public(self):
        return {
            "id": self.id, "sentence": self.sentence, "name": self.name, "plan": self.plan,
            "sensors": [s.public() for s in self.sensors], "values": self.values, "fired": self.fired,
            "schedules": [s.public() for s in self.schedules],
            "pointed": (self.pointed or {}).get("noun", ""), "paused": self.paused,
        }


class App:
    def __init__(self, client, logs_dir=LOGS_DIR, inline=False, journal=None, connectors=None, rules_file=None):
        """inline runs checks inside frame(), for tests. Live checks run in the background.

        With no rules_file, rules do not return after a restart, and the journal and the actions
        live in memory. main() passes the real files.
        """
        self.client = client
        self.inline = inline
        self.journal = journal or Journal()
        self.connectors = connectors or Connectors()
        self.rules_file = rules_file
        self.drafts = OrderedDict()
        self.evidence = Evidence(logs_dir)
        self.entries = {}
        self.jobs = {}
        self.lock = threading.Lock()
        # Detection and analysis never share threads, so a busy job cannot delay a check.
        self.detection = ThreadPoolExecutor(max_workers=DETECTION_THREADS)
        self.analysis = ThreadPoolExecutor(max_workers=ANALYSIS_THREADS)
        self.frames = deque(maxlen=FRAME_BUFFER)
        self.timeline = deque(maxlen=TIMELINE_FRAMES)
        self.feed = deque(maxlen=FEED_LENGTH)
        self.feed_id = 0
        self.results = deque(maxlen=FEED_LENGTH)
        self.result_id = 0
        self.points = OrderedDict()
        self.voices = {}
        self.open_voice = VoiceSession
        self.hold_until = 0.0
        self.last_frame = 0.0
        self.active_page = None
        self.live = threading.Condition()
        self.live_jpeg = None
        self.live_seq = 0
        self.live_at = 0.0
        self.cameras = []
        self.wall = (1, 1)
        self.controls = Controls()
        self.see_calls = 0
        self.ask_calls = 0
        self.spark_usd = 0.0
        self.writer_usd = 0.0
        self.spend_day = datetime.now(ZONE).date()

    def _new_day(self):
        """The spend starts again at midnight, so a hosted server works the next day."""
        today = datetime.now(ZONE).date()
        if today != self.spend_day:
            # No lock: status() calls this while it holds self.lock.
            self.spend_day = today
            self.see_calls = self.ask_calls = 0
            self.spark_usd = self.writer_usd = 0.0

    def spend(self):
        return self.see_calls * PRICE_SEE + self.spark_usd + self.writer_usd

    def stopped(self):
        self._new_day()
        return self.spend() >= SPEND_LIMIT_USD

    def add_feed(self, rule, kind, text, **extra):
        with self.lock:
            self.feed_id += 1
            item = {"id": self.feed_id, "rule": rule.name, "kind": kind, "text": text, **extra}
            self.feed.append(item)
        self.evidence.save_feed(item)
        if kind in ("notify", "say", "draw", "image", "log", "error"):
            self.journal.add(kind, rule.id, rule.name, text=text)
        return item

    def capture_fps(self):
        """The fastest rate that any awake sensor asked for."""
        now = time.time()
        rates = [s.frames_per_second for rule in self.entries.values() if not rule.paused
                 for s in rule.sensors if s.awake(now)]
        return max(rates, default=DEFAULT_CAPTURE_FPS)

    def status(self, after=0, after_result=0):
        with self.lock:
            return {
                "rules": [rule.public() for rule in self.entries.values()],
                "feed": [item for item in self.feed if item["id"] > after],
                "results": [item for item in self.results if item["id"] > after_result],
                "capture_fps": self.capture_fps(),
                "hold_capture": time.time() < self.hold_until,
                "live_camera": self.camera_live(),
                "cameras": self.cameras,
                "controls": self.controls.public(),
                "actions": self.connectors.public(),
                "wall": self.wall,
                "see_calls": self.see_calls,
                "ask_calls": self.ask_calls,
                "spend_usd": round(self.spend(), 4),
                "spend_limit_usd": SPEND_LIMIT_USD,
                "stopped": self.stopped(),
            }

    def start_job(self, sentence, example, idle, motions=None, gap_s=None, point=None, edit=None):
        """edit is the id of a live rule to replace. That rule keeps running until the new one passes."""
        with self.lock:
            old = self.entries.get(edit)
            if edit and not old:
                raise writer.WriterError("That rule is no longer running.")
            if not edit and len(self.entries) >= MAX_RULES:
                raise writer.WriterError(f"This prototype runs up to {MAX_RULES} rules.")
            job = {"id": uuid.uuid4().hex[:8], "status": "working", "steps": [], "error": ""}
            self.jobs[job["id"]] = job
        args = (job, sentence, example, idle, motions, gap_s, point, old)
        threading.Thread(target=self._write, args=args, daemon=True).start()
        return job["id"]

    def _write(self, job, sentence, example, idle, motions, gap_s, point=None, old=None):
        rule_id = uuid.uuid4().hex[:8]
        steps = []

        def progress(text):
            job["steps"].append(text[:200])
            steps.append(text)
        try:
            if not sentence:
                progress("Spark is reading your recording")
                sentence = writer.describe_example(self.client, example).strip()[:300]
                progress(f"Understood: {sentence}")
            pointed = old.pointed if old else None
            if point in self.points:
                pointed = {"noun": self.name_point(point), "box": self.points[point]["box"]}
                progress(f"You pointed at: {pointed['noun']}")
            progress("Wonder is changing the rule" if old else "Wonder is writing the rule")
            folder = sandbox.copy_rule(old.folder, rule_id) if old else sandbox.create(rule_id)
            previous = json.loads((Path(old.folder) / "rule.json").read_text()) if old else None
            if example:
                save_recording(folder, example, idle, motions, gap_s)
            elif old:
                # The person's old recording still shows the event, so it still tests the sensors.
                example, idle, motions, gap_s = load_recording(folder)
            fields, cost = writer.write_rule(
                self.client, folder, sentence, example, idle, progress, motions, gap_s, pointed, previous,
                self.controls.describe(self.cameras), self.connectors.describe(), self.journal.summary())
            with self.lock:
                self.writer_usd += cost
            if fields.get("unsupported"):
                raise writer.WriterError(fields["unsupported"])
            (folder / "rule.json").write_text(json.dumps(
                {"sentence": sentence, **fields, "writer_steps": steps}, indent=1, ensure_ascii=False))
            rule = Rule(self, rule_id, sentence, fields, folder, pointed)
            rule.paused = bool(old and old.paused)
            rule.start()
            with self.lock:
                self.entries[rule_id] = rule
                if old:
                    self.entries.pop(old.id, None)
            if old:
                old.stop()
                self.add_feed(rule, "log", "Rule updated. It now follows your new words.")
            self.save_rules()
            job.update(status="done", rule=rule_id, cost=round(cost, 3))
        except (writer.WriterError, ApiError, OSError, ValueError, KeyError) as e:
            job.update(status="failed", error=str(e)[:300])

    def remove_rule(self, rule_id):
        with self.lock:
            rule = self.entries.pop(rule_id, None)
        if rule:
            rule.stop()
            self._lights_off(rule)
            self.save_rules()
        return rule is not None

    def save_rules(self):
        """Lists the live rules, so they return after a restart."""
        if not self.rules_file:
            return
        with self.lock:
            items = [{"id": rule.id, "pointed": rule.pointed, "paused": rule.paused} for rule in self.entries.values()]
        Path(self.rules_file).parent.mkdir(parents=True, exist_ok=True)
        Path(self.rules_file).write_text(json.dumps({"rules": items}, ensure_ascii=False))

    def load_rules(self):
        """Starts the rules that were live when the server stopped. Each keeps its ctx.state."""
        try:
            items = json.loads(Path(self.rules_file).read_text())["rules"]
        except (OSError, TypeError, ValueError, KeyError):
            return 0
        for item in items[:MAX_RULES]:
            rule_id = str(item.get("id") or "")
            folder = sandbox.RULES_DIR / rule_id
            if not rule_id.isalnum() or not (folder / "rule.json").exists():
                continue
            fields = json.loads((folder / "rule.json").read_text())
            rule = Rule(self, rule_id, fields.get("sentence", ""), fields, folder, item.get("pointed"))
            rule.paused = bool(item.get("paused"))
            rule.start()
            with self.lock:
                self.entries[rule_id] = rule
            self.add_feed(rule, "log", f"{rule.name} is back after a restart.")
        return len(self.entries)

    def pause_rule(self, rule_id):
        """Stop: the rule stays, but it watches nothing and calls no model until Resume."""
        rule = self.entries.get(rule_id)
        if not rule:
            return False
        if not rule.paused:
            now = time.time()
            rule.paused = True
            for sensor in rule.sensors:
                if sensor.state.on:
                    rule.send({"type": "off", "sensor": sensor.id, "t": now, "duration": now - sensor.state.on_since})
                sensor.reset(now)
            self._lights_off(rule)
            self.add_feed(rule, "log", f"{rule.name} is stopped.")
            self.save_rules()
        return True

    def resume_rule(self, rule_id):
        """Resume starts from now. Frames from the stopped time are not read."""
        rule = self.entries.get(rule_id)
        if not rule:
            return False
        if rule.paused:
            for sensor in rule.sensors:
                sensor.reset(time.time())
            rule.paused = False
            self.add_feed(rule, "log", f"{rule.name} is running again.")
            self.save_rules()
        return True

    def code(self, rule_id):
        rule = self.entries.get(rule_id)
        return (rule.folder / "rule.py").read_text() if rule else None

    def tick(self):
        """A schedule runs with no camera. on_tick needs the camera, or a schedule in the rule."""
        now = time.time()
        camera = now - self.last_frame <= CAMERA_LOST_S
        with self.lock:
            rules = list(self.entries.values())
        for rule in rules:
            if rule.paused:
                continue
            for schedule in rule.schedules:
                if schedule.due(now):
                    self.journal.add("event", rule.id, rule.name, schedule.id, text="scheduled", t=now)
                    rule.send({"type": "on", "sensor": schedule.id, "t": now, "event": {"note": "It is time."}})
                    rule.send({"type": "off", "sensor": schedule.id, "t": now, "duration": 0.0})
            if camera or rule.schedules:
                rule.send({"type": "tick", "t": now})

    def _tick_loop(self):
        while True:
            time.sleep(TICK_S)
            self.tick()

    def start_ticks(self):
        threading.Thread(target=self._tick_loop, daemon=True).start()

    def point(self, jpeg, x, y, width, height):
        """A click on the view. SAM finds the one object under it."""
        if self.stopped():
            return {"found": False, "stopped": True}
        with self.lock:
            self.see_calls += 1
        found = self.client.mark(jpeg, width, height, point=(x, y))
        if not found:
            return {"found": False}
        point_id = uuid.uuid4().hex[:8]
        self.evidence.save_frame(time.time(), jpeg)
        with self.lock:
            self.points[point_id] = {"jpeg": jpeg, "box": found[0], "noun": ""}
            while len(self.points) > MAX_POINTS:
                self.points.popitem(last=False)
        return {"found": True, "id": point_id, "box": found[0]}

    def name_point(self, point_id):
        point = self.points.get(point_id)
        if not point:
            return ""
        if not point["noun"]:
            point["noun"], cost = self.client.name_object(point["jpeg"], point["box"])
            with self.lock:
                self.spark_usd += cost
        return point["noun"]

    def start_voice(self):
        session = self.open_voice(self.client.key)
        with self.lock:
            self.voices[session.id] = session
        return session.id

    def voice_audio(self, voice_id, chunk):
        session = self.voices.get(voice_id)
        return session.audio(chunk) if session else ""

    def end_voice(self, voice_id):
        with self.lock:
            session = self.voices.pop(voice_id, None)
        return session.finish() if session else ""

    def _later(self, work, *args):
        """Runs analysis beside detection. Nothing that called this waits for it."""
        if self.inline:
            work(*args)
        else:
            self.analysis.submit(work, *args)

    def start_call(self, rule, message):
        self._later(self._rule_call, rule, message)

    def _lights_off(self, rule):
        """A light must not outlast the rule that turned it on."""
        for camera in rule.lit:
            try:
                self.controls.light(camera, False)
            except ControlError as e:
                self.add_feed(rule, "error", str(e))
        rule.lit.clear()

    def _rule_call(self, rule, message):
        """Runs one building block for a rule's code, and always sends an answer back."""
        reply = {"type": "reply", "id": message.get("id")}
        blocks = {"ask": self._rule_ask, "check": self._rule_check, "find": self._rule_find,
                  "frames": self._rule_frames, "gallery": self._rule_gallery,
                  "arm": self._rule_arm, "disarm": self._rule_disarm,
                  "aim": self._rule_aim, "move": self._rule_move, "light": self._rule_light,
                  "act": self._rule_act, "history": self._rule_history}
        try:
            # An action or the journal calls no model, so a report still works at the spend limit.
            if self.stopped() and message.get("name") not in ("act", "history", "disarm"):
                raise ValueError("The spend limit stopped this call.")
            if rule.paused:
                raise ValueError("This rule is stopped.")
            reply["value"] = blocks[message["name"]](rule, **(message.get("args") or {}))
        except (ApiError, KeyError, TypeError, ValueError) as e:
            reply["error"] = str(e)[:300]
        rule.reply(reply)

    def _step(self, rule, check, step, text):
        if check:
            self.evidence.add_step(check, step)
        self.add_feed(rule, "step", text)

    def _picture(self, rule, path):
        """A picture that rule code chose. It must be inside the rule's folder."""
        if not path:
            return None
        file = rule_picture(rule.folder, path) if rule.folder else None
        if not file:
            raise ValueError(f"The rule cannot send {str(path)[:80]}.")
        return file.read_bytes()

    def _rule_act(self, rule, name, inputs=None, picture=None, check=None):
        """Runs one of the person's actions for a rule. The server sends the request, not the rule."""
        rule.allow_act()
        inputs = dict(inputs or {})
        try:
            result = self.connectors.run(name, inputs, self._picture(rule, picture))
        except ActionError as e:
            self.journal.add("action", rule.id, rule.name, name, text=str(e), values={"ok": False})
            raise
        self.journal.add("action", rule.id, rule.name, name, text=f"status {result['status']}",
                         values={"ok": result["ok"], "inputs": inputs})
        word = "done" if result["ok"] else f"failed with status {result['status']}"
        self._step(rule, check, {"act": name, "inputs": inputs, **result}, f"{name}: {word}")
        return result

    def _rule_history(self, rule, kind=None, name=None, rule_name=None, since=None, until=None, limit=2000):
        """Reads the journal of all the person's rules, not only this one. A report reads this."""
        return self.journal.history(kind, name, rule_name, since, until, limit)

    def record(self, rule, name, values):
        try:
            self.journal.add("record", rule.id, rule.name, name, values=values)
        except ValueError as e:
            self.add_feed(rule, "error", str(e))

    def alert(self, rule, text, picture=None):
        """ctx.notify also goes to the person's phone. The request runs beside the rule."""
        if any(item["alerts"] for item in self.connectors.ready()):
            self._later(self._alert, rule, text, picture)

    def _alert(self, rule, text, picture):
        try:
            for name, result in self.connectors.alert(text, self._picture(rule, picture)):
                if not result["ok"]:
                    self.add_feed(rule, "error", f"{name} answered with status {result['status']}.")
        except (ActionError, ValueError, OSError) as e:
            self.add_feed(rule, "error", str(e)[:300])

    def draft_action(self, words):
        """Wonder turns the person's words into one web request. Nothing runs until they test it."""
        if self.stopped():
            raise ValueError("The spend limit stopped this call.")
        draft, cost = writer.write_action(self.client, words, [a["name"] for a in self.connectors.all()])
        with self.lock:
            self.writer_usd += cost
        if draft.get("unsupported"):
            raise writer.WriterError(draft["unsupported"])
        action = check_draft(draft, [a["name"] for a in self.connectors.items])
        draft_id = uuid.uuid4().hex[:8]
        with self.lock:
            self.drafts[draft_id] = action
            while len(self.drafts) > MAX_DRAFTS:
                self.drafts.popitem(last=False)
        return {"id": draft_id, "action": self._show_draft(action), "cost": round(cost, 3)}

    def _show_draft(self, action):
        """The page sees the request with its blanks. The keys stay in actions.env."""
        return {**action, "missing": self.connectors.missing(action)}

    def test_draft(self, draft_id, inputs):
        action = self.drafts.get(draft_id)
        if not action:
            raise ActionError("That draft is gone. Describe the action again.")
        return self.connectors.send(action, inputs)

    def save_draft(self, draft_id):
        with self.lock:
            action = self.drafts.pop(draft_id, None)
        if not action:
            raise ActionError("That draft is gone. Describe the action again.")
        return self.connectors.add(action)

    def _rule_ask(self, rule, question, frames=(), model="spark", check=None):
        rule.allow_call()
        jpegs = [self.evidence.read_sharp(name) for name in list(frames)[:MAX_ASK_FRAMES]]
        jpegs = [jpeg for jpeg in jpegs if jpeg]
        if not jpegs:
            raise ValueError("Those frames are not available.")
        answer, cost = self.client.look(question, jpegs, model)
        with self.lock:
            self.ask_calls += 1
            if model == "wonder":
                self.writer_usd += cost
            else:
                self.spark_usd += cost
        self._step(rule, check, {"model": model, "question": question, "frames": len(jpegs), **answer},
                   f"{model.capitalize()} says: {answer['answer']} ({answer['confidence']:.2f} sure)")
        return answer

    def _rule_check(self, rule, claim, frames=(), model="spark", reference=None, check=None):
        """A model judges each frame alone. A search or a claim about a frame uses this."""
        pictures = [(name, self.evidence.read_sharp(name)) for name in list(frames)[:MAX_ASK_FRAMES]]
        pictures = [(name, jpeg) for name, jpeg in pictures if jpeg]
        if not pictures:
            raise ValueError("Those frames are not available.")
        for _ in pictures:
            rule.allow_call()
        example = self.evidence.read_sharp(reference) if reference else None

        def judge(picture):
            return picture[0], *self.client.verify(claim, picture[1], model, example)

        with ThreadPoolExecutor(max_workers=MAX_CHECK_THREADS) as pool:
            judged = list(pool.map(judge, pictures))
        cost = sum(item[2] for item in judged)
        with self.lock:
            self.ask_calls += len(judged)
            if model == "wonder":
                self.writer_usd += cost
            else:
                self.spark_usd += cost
        found = [{"name": name, **result} for name, result, _ in judged]
        found.sort(key=lambda item: (item["passed"], item["confidence"]), reverse=True)
        best = found[0]
        passed = sum(item["passed"] for item in found)
        answer = f"{passed} of {len(found)} frames passed. Best: {best['seen']}"
        self._step(rule, check, {"model": model, "question": claim, "answer": answer,
                                 "confidence": best["confidence"], "frames": len(found)},
                   f"{model.capitalize()} checked {len(found)} frames alone, {passed} passed: {best['seen']}")
        return found

    def _rule_find(self, rule, thing, frame=None, check=None):
        rule.allow_call()
        jpeg = self.evidence.read_sharp(frame)
        if not jpeg:
            raise ValueError("That frame is not available.")
        with self.lock:
            self.see_calls += 1
        boxes = self.client.see(thing, jpeg, lane="analysis")
        boxes = [{**{k: b[k] for k in ("x1", "y1", "x2", "y2", "w", "h")}, "confidence": b["c"]} for b in boxes]
        boxes.sort(key=lambda b: b["confidence"] or 0, reverse=True)
        self._step(rule, check, {"model": "sam", "question": thing, "answer": f"{len(boxes)} found"},
                   f"SAM found {len(boxes)} {thing}")
        return boxes

    def _shots_in(self, thing, name, t):
        """Finds a thing in the best copies of one moment. Each camera's own frame has the most pixels."""
        full = self.evidence.full_copies(name)
        sources = [(camera, path.read_bytes()) for camera, path in full]
        if not sources:
            jpeg = self.evidence.read_sharp(name)
            sources = [(None, jpeg)] if jpeg else []
        shots = []
        for camera, jpeg in sources:
            with self.lock:
                self.see_calls += 1
            boxes = self.client.see(thing, jpeg, lane="analysis")
            good = [box for box in boxes if (box["c"] or 0) >= GALLERY_MIN_CONFIDENCE]
            if not good:
                continue
            picture = Image.open(BytesIO(jpeg)).convert("RGB")
            for box in good:
                shots.append({"name": name, "t": t, "camera": camera, "box": box,
                              "score": sharpness(picture, box) * self._lens_bonus(camera),
                              "zoom": zoom(picture, box)})
        return shots

    def _rule_gallery(self, rule, thing, frames=(), count=6, caption="", check=None):
        """Finds a thing across frames and shows a row of zoomed pictures, the best first.

        A click on a picture opens its full frame, so a person can judge it. One sharpest frame can
        be the wrong one, and a model's "clear" is only an opinion.
        """
        names = list(dict.fromkeys(frames))[:MAX_GALLERY_FRAMES]
        count = int(number(count, 1, MAX_GALLERY_SHOTS, 6))
        # This has its own limit, because it ends a job. A rule's rechecks must not cost it the final pictures.
        rule.allow_gallery()
        with self.lock:
            times = {f.name: f.t for f in self.timeline if f.name in names}
        with ThreadPoolExecutor(max_workers=MAX_CHECK_THREADS) as pool:
            found = list(pool.map(lambda name: self._shots_in(thing, name, times.get(name, 0.0)), names))
        chosen = pick_shots([shot for shots in found for shot in shots], count)
        pictures = []
        for index, shot in enumerate(chosen):
            camera = shot["camera"]
            path = f"pictures/{shot['name'][:-4]}_{camera}_{index}.jpg"
            pictures.append({
                "name": shot["name"], "t": shot["t"], "path": path, "score": round(shot["score"], 1),
                "camera": self.cameras[camera] if camera is not None and camera < len(self.cameras) else "",
                "zoom": f"/rule-file/{rule.id}/{path}" if rule.folder else "",
                "frame": f"/evidence/{self.evidence.session}/{shot['name']}"
                         + (f"?camera={camera}" if camera is not None else ""),
                "box": {key: shot["box"][key] for key in ("x1", "y1", "x2", "y2", "w", "h")},
            })
            if rule.folder:
                target = Path(rule.folder) / path
                target.parent.mkdir(exist_ok=True)
                shot["zoom"].save(target, "JPEG", quality=92)
        text = caption or f"Best pictures of {thing}"
        if check and pictures:
            self.evidence.add_step(check, {"pictures": pictures, "caption": text})
        self.add_feed(rule, "gallery", f"{text} ({len(pictures)})", check=check, pictures=pictures)
        return pictures

    def _lens_bonus(self, camera):
        names = {item["name"] for item in self.controls.public() if item["moves"]}
        moving = camera is not None and camera < len(self.cameras) and self.cameras[camera] in names
        return MOVING_LENS_BONUS if moving else 1.0

    def _tile(self, name):
        """Returns (left, top, right, bottom) of a camera's place on the wall, from 0 to 1."""
        columns, rows = self.wall
        index = self.cameras.index(name)
        left, top = index % columns, index // columns
        return left / columns, top / rows, (left + 1) / columns, (top + 1) / rows

    def _find_in_guide(self, camera, thing, frame=None):
        """Finds a thing in the fixed lens's picture. Returns its centre from 0 to 1, or None."""
        with self.lock:
            name = frame or (self.timeline[-1].name if self.timeline else None)
        wall = self.evidence.read_sharp(name) if name else None
        if not wall:
            raise ValueError("No frame is available.")
        picture = Image.open(BytesIO(wall)).convert("RGB")
        left, top, right, bottom = self._tile(self.controls.guide(camera))
        tile = picture.crop((round(left * picture.width), round(top * picture.height),
                             round(right * picture.width), round(bottom * picture.height)))
        out = BytesIO()
        tile.save(out, "JPEG", quality=90)
        with self.lock:
            self.see_calls += 1
        boxes = [box for box in self.client.see(thing, out.getvalue(), lane="analysis") if box["w"] and box["h"]]
        if not boxes:
            return None
        best = max(boxes, key=lambda box: box["c"] or 0)
        x, y = centre(best)
        return x / best["w"], y / best["h"]

    def _rule_aim(self, rule, camera, thing=None, x=None, y=None, frame=None, check=None):
        """The fixed lens finds, and the moving lens goes to look closer."""
        rule.allow_control()
        if thing:
            rule.allow_call()
            point = self._find_in_guide(camera, thing, frame)
            if not point:
                self._step(rule, check, {"model": "sam", "question": thing, "answer": "0 found"},
                           f"SAM did not find {thing} in {self.controls.guide(camera)}")
                return None
            x, y = point
        self.controls.aim(camera, x, y)
        text = f"{camera} turned to {thing or 'a point'}"
        self._step(rule, check, {"model": "camera", "question": text, "answer": f"x {x:.2f}, y {y:.2f}"}, text)
        return {"x": x, "y": y}

    def _rule_move(self, rule, camera, direction, seconds=1.0, check=None):
        rule.allow_control()
        self.controls.move(camera, direction, seconds)
        text = f"{camera} moved {direction}"
        self._step(rule, check, {"model": "camera", "question": text, "answer": "Done"}, text)
        return True

    def _rule_light(self, rule, camera, on=True, seconds=20.0, check=None):
        rule.allow_control()
        self.controls.light(camera, on, seconds)
        (rule.lit.add if on else rule.lit.discard)(camera)
        text = f"{camera} light {'on' if on else 'off'}"
        self._step(rule, check, {"model": "camera", "question": text, "answer": "Done"}, text)
        return True

    def camera_command(self, body):
        """A person's press on the page. It waits in its own request, so no check waits for it."""
        camera, action = str(body["camera"]), body["do"]
        if action == "move":
            self.controls.move(camera, str(body["direction"]), PAGE_MOVE_S)
        elif action == "light":
            self.controls.light(camera, bool(body["on"]), PAGE_LIGHT_S)
        elif action == "aim":
            self.controls.aim(camera, *self._point_in_guide(camera, float(body["x"]), float(body["y"])))
        else:
            raise KeyError(action)
        return {"controls": self.controls.public()}

    def _point_in_guide(self, camera, x, y):
        """Turns a place on the wall into a point in the fixed lens's picture."""
        guide = self.controls.guide(camera)
        left, top, right, bottom = self._tile(guide)
        if not (left <= x < right and top <= y < bottom):
            raise ControlError(f"Click inside {guide}.")
        return (x - left) / (right - left), (y - top) / (bottom - top)

    def _rule_frames(self, rule, t=None, before=2.0, after=0.0, count=8):
        """Frames around a moment, for a rule's code. It waits if the rule asks for later frames."""
        t = time.time() if t is None else float(t)
        before = number(before, 0, MAX_BEFORE_S, 2.0)
        end = t + number(after, 0, MAX_AFTER_S, 0.0)
        count = int(number(count, 1, MAX_RULE_FRAMES, 8))
        # The page sends few frames in a still picture, so it must keep capturing for this wait.
        self.hold_until = max(self.hold_until, end)
        wait = end - time.time()
        if wait > 0 and not self.inline:
            time.sleep(wait + 0.3)
        frames = self._between(t - before, end)
        # A still picture repeats itself, so moments with movement come first.
        moving = [f for f in frames if f.motion >= MOTION_MIN]
        frames = moving if len(moving) >= count else frames
        with self.lock:
            nearest = next((f for f in reversed(self.timeline) if f.t <= end), None)
        return self._share(rule, spread(frames, count) if frames else [nearest] if nearest else [])

    def _sensor(self, rule, sensor_id):
        for sensor in rule.sensors:
            if sensor.id == sensor_id:
                return sensor
        raise ValueError(f"This rule has no sensor named {sensor_id}.")

    def _rule_arm(self, rule, sensor, seconds=120, since=None):
        """Lets a rule wait for a later moment. A job can outlast the event that started it."""
        target = self._sensor(rule, sensor)
        seconds = number(seconds, 1, MAX_ARM_S, 120)
        now = time.time()
        target.arm(now, seconds, since)
        # The page sends few frames in a still picture, and this wait needs them.
        self.hold_until = max(self.hold_until, now + seconds)
        self.add_feed(rule, "step", f"Now waiting for {sensor}, for up to {seconds:.0f} s")
        return True

    def _rule_disarm(self, rule, sensor):
        target = self._sensor(rule, sensor)
        target.armed_until = 0.0
        self.hold_until = time.time() if not self._waiting() else self.hold_until
        self.add_feed(rule, "step", f"Stopped waiting for {sensor}")
        return True

    def _waiting(self):
        now = time.time()
        return any(s.armed_until and s.armed_until > now for r in self.entries.values() for s in r.sensors)

    def _between(self, start, end):
        """Saved frames in a time span, oldest first. A job that runs late still gets its evidence."""
        found = []
        with self.lock:
            for frame in reversed(self.timeline):
                if frame.t < start:
                    break
                if frame.t <= end:
                    found.append(frame)
        return found[::-1]

    def _share(self, rule, frames):
        """Copies frames into the rule's folder, because its sandbox cannot read the logs."""
        shared = []
        for frame in frames:
            path = ""
            if rule.folder:
                target = Path(rule.folder) / "frames" / frame.name
                target.parent.mkdir(exist_ok=True)
                # A sharp copy can arrive just after its frame, so replace an older small copy.
                target.write_bytes(self.evidence.read_sharp(frame.name) or frame.jpeg or b"")
                path = f"frames/{frame.name}"
            shared.append({"name": frame.name, "t": frame.t, "motion": frame.motion, "path": path})
        return shared

    def _event_detail(self, rule, check, result, proof):
        """What a rule's code gets with its event, so it can look at the same frames."""
        names = set(result.get("frames") or [])
        with self.lock:
            frames = [f for f in self.timeline if f.name in names]
        answers = result.get("answers") or []
        review = result.get("review")
        note = answers[0].get("note", "") if answers else ""
        if review:
            note = "; ".join([result.get("who", ""), *review["clues_for"][:3]]).strip("; ")
        return {
            "check": check, "proof": proof, "frames": self._share(rule, frames), "note": note,
            "facts": answers[0].get("facts", []) if answers else [],
            "boxes": [{k: b[k] for k in ("x1", "y1", "x2", "y2", "w", "h")} for b in result.get("boxes") or []],
        }

    def image(self, rule, path, caption="", check=None):
        """Shows a frame, a crop or a chart that a rule made."""
        file = rule_picture(rule.folder, path)
        if not file:
            self.add_feed(rule, "error", f"The rule cannot show {str(path)[:80]}.")
            return
        relative = file.relative_to(Path(rule.folder).resolve())
        url = f"/rule-file/{rule.id}/{quote(str(relative))}"
        self.add_feed(rule, "image", caption, url=url, check=check)
        if check:
            self.evidence.add_step(check, {"image": url, "caption": caption})

    def draw(self, rule, text, frame=None, check=None, target=None):
        """Freezes the page on a frame, and puts text on it.

        The rule can choose the frame and what to outline. Otherwise it is the frame that
        proved the event. The freeze shows at once and the outline follows, so a slow model
        does not delay it. A rule can also draw from on_tick, when no event just fired.
        """
        stored = self.evidence.check(check) if check else None
        with self.lock:
            newest = self.timeline[-1].name if self.timeline else None
        name = frame if self.evidence.read_frame(frame) else (stored or {}).get("proof_frame") or newest
        sensor = next((s for s in rule.sensors if stored and s.id == stored["sensor"]), rule.sensors[0])
        url = f"/evidence/{self.evidence.session}/{name}" if name else None
        item = self.add_feed(rule, "draw", text, frame=url, check=check)
        if not name:
            return
        self._later(self._outline, rule, sensor, name, check, item["id"], target)

    def _find_outline(self, rule, sensor, jpeg, chosen=None):
        """Returns the one object to outline on a frame, or None.

        It tries the fastest way first. A name fails if SAM does not know that word, so the
        last way lets Spark find the object from the event, and SAM cuts out that box.
        """
        pointed = {} if chosen else rule.pointed or {}
        box = pointed.get("box")
        target = chosen or pointed.get("noun") or sensor.target or sensor.outline
        found = []
        if target:
            with self.lock:
                self.see_calls += 1
            found = self.client.find(target, jpeg)
        if not found and box:
            # A fixed object stays in its place, even if its name does not match this frame.
            with self.lock:
                self.see_calls += 1
            found = self.client.mark(jpeg, box["w"], box["h"], box=box)
        if not found:
            located, cost = self.client.locate(f"Where is the {chosen}?" if chosen else sensor.question, jpeg)
            with self.lock:
                self.spark_usd += cost
                self.see_calls += 1 if located else 0
            if located:
                found = self.client.mark(jpeg, located["w"], located["h"], box=located)
        if not found:
            return None
        if box:
            x, y = centre(box)
            return min(found, key=lambda s: abs(centre(s)[0] - x) + abs(centre(s)[1] - y))
        return max(found, key=lambda s: s["c"] or 0)

    def _outline(self, rule, sensor, name, check=None, draw=None, target=None):
        """The page keeps its freeze until this answers, even when no outline is found."""
        jpeg = self.evidence.read_frame(name)
        outline = None
        try:
            found = self._find_outline(rule, sensor, jpeg, target) if jpeg and not self.stopped() else None
        except ApiError as e:
            found = None
            self.add_feed(rule, "log", "The outline failed: " + str(e)[:160])
        if found:
            outline = {key: found[key] for key in OUTLINE_FIELDS if key in found}
            # A rule can draw on a later frame. The Why view paints this on the proof frame.
            if check and name == (self.evidence.check(check) or {}).get("proof_frame"):
                self.evidence.update_check(check, {"outline": outline})
        if draw:
            self.add_feed(rule, "outline", "", draw=draw, outline=outline)
        return outline

    def check_outline(self, check_id):
        """For the Why view. An event that drew nothing has no outline yet."""
        check = self.evidence.check(check_id)
        if not check:
            return None
        if check.get("outline"):
            return check["outline"]
        rule = self.entries.get(check["rule"])
        sensor = next((s for s in rule.sensors if s.id == check["sensor"]), None) if rule else None
        name = check.get("proof_frame")
        return self._outline(rule, sensor, name, check_id) if sensor and name else None

    def _facts(self, sensor, jpegs):
        started = time.perf_counter()
        answer, cost = self.client.check_facts(
            sensor.question, sensor.requires, jpegs, sensor.pattern == "moment")
        with self.lock:
            self.ask_calls += 1
            self.spark_usd += cost
        answer["seconds"] = round(time.perf_counter() - started, 3)
        return answer

    def _detect(self, sensor, frame):
        """SAM checks a solid thing in one frame."""
        started = time.perf_counter()
        with self.lock:
            self.see_calls += 1
        boxes = self.client.see(sensor.target, frame.jpeg)
        confidences = [b["c"] for b in boxes if b["c"] is not None]
        hit = len(boxes) >= sensor.min_count
        strong = hit and bool(confidences) and max(confidences) >= STRONG_HIT
        return hit, strong, {
            "boxes": boxes,
            "conf": max(confidences) if confidences else None,
            "sam_s": round(time.perf_counter() - started, 3),
            "frames": [frame.name],
        }

    def _judge(self, sensor, frames):
        """Spark judges chosen frames. It handles actions and things with no clear shape.

        A new event needs a second yes on the same frames. One random yes must not start
        an event, and the same frames still catch an event that lasts for one frame.
        """
        jpegs = [frame.jpeg for frame in frames]
        first = self._facts(sensor, jpegs)
        answers = [first]
        if first["seen"] and (sensor.pattern == "moment" or not sensor.state.on):
            answers.append(self._facts(sensor, jpegs))
        hit = all(a["seen"] for a in answers)
        return hit, hit, {
            "answer": hit,
            "answers": answers,
            "spark_frames": len(frames),
            "spark_s": round(sum(a["seconds"] for a in answers), 3),
            "frames": [frame.name for frame in frames],
        }

    def _watch(self, rule, sensor, t, result):
        """Queues a review if one is due, and returns at once. Detection never waits for analysis.

        A sleeping sensor waits for something that one frame must prove, such as a clear face.
        Several frames can say yes to that while each frame alone says no, so it gets no review.
        A moment that is under way is already one occurrence. A check that failed counts as a
        near miss, because a review can read the frames that it lost.
        """
        if sensor.target:
            return
        near = result is None or any(a["seen"] or a.get("part_seen") for a in result.get("answers", []))
        with sensor.lock:
            if (sensor.reviewing or rule.paused or sensor.armed_until is not None or sensor.state.in_moment
                    or t < sensor.review_after or not (near or sensor.state.on)):
                return
            if t - sensor.review_after > REVIEW_MAX_WAIT_S:
                sensor.review_wait = REVIEW_EVERY_S
            sensor.reviewing = True
        self._later(self._review, rule, sensor, t)

    def _review(self, rule, sensor, t):
        try:
            self._read_recent(rule, sensor, t)
        finally:
            sensor.reviewing = False

    def _read_recent(self, rule, sensor, t):
        """Spark watches the recent past, for an action that no single frame proves.

        It reads saved frames, so it can start late and still see everything. While a state is
        on, this also finds a second person who starts straight after the first one.
        """
        moment = sensor.pattern == "moment"
        span = MOMENT_REVIEW_SPAN_S if moment else REVIEW_SPAN_S
        # The rolling store can remove the oldest pictures of a long run.
        frames = [f for f in self._between(max(t - span, sensor.review_from), t) if self.evidence.frame_path(
            self.evidence.session, f.name)]
        frames = spread(frames, REVIEW_FRAMES)
        # A review of a few seconds proves little, so it waits for more frames and costs nothing.
        if not frames or frames[-1].t - frames[0].t < (MOMENT_MIN_REVIEW_SPAN_S if moment else MIN_REVIEW_SPAN_S):
            return
        if self.stopped():
            return
        started = time.perf_counter()
        try:
            review, cost = self.client.review(
                sensor.question, sensor.requires, [self.evidence.read_frame(f.name) for f in frames],
                [t - f.t for f in frames])
        except (ApiError, KeyError, ValueError):
            return
        with self.lock:
            self.ask_calls += 1
            self.spark_usd += cost
        sure = review["confidence"] >= REVIEW_PASS
        weak = review["passed"] and not sure and review["confidence"] >= REVIEW_WEAK
        with sensor.lock:
            passed = review["passed"] and (sure or (weak and sensor.weak_yes)) and not rule.paused
            sensor.weak_yes = weak and not passed
            sensor.review_after = t + (sensor.watch_s if sensor.state.on or passed else sensor.review_wait)
            # Only a sure "no" slows down. An unsure answer means that the evidence is still growing.
            sensor.review_wait = (min(sensor.review_wait * 2, REVIEW_MAX_WAIT_S)
                                  if sure and not review["passed"] else REVIEW_EVERY_S)
        record = {
            "t": t, "rule": rule.id, "rule_name": rule.name, "sensor": sensor.id, "target": "",
            "question": sensor.question, "requires": sensor.requires, "pattern": sensor.pattern,
            "frames_covered": len(frames), "min_motion": sensor.min_motion, "hit": passed, "answer": passed,
            "frames": [f.name for f in frames],
            "review": {**review, "passed": passed, "span_s": round(t - frames[0].t),
                       "seconds": round(time.perf_counter() - started, 3)},
        }
        if not passed:
            self.evidence.save_check({**record, "event": None, "proof_frame": frames[-1].name})
            return
        doers = [d for d in review["doers"] if 1 <= d["best_frame"] <= len(frames)]
        for doer in doers or [{"who": "", "best_frame": len(frames), "last_frame": len(frames)}]:
            newest = doer.get("last_frame", 0)
            until = frames[newest - 1].t if 1 <= newest <= len(frames) else None
            self._meet(rule, sensor, frames[doer["best_frame"] - 1], until, doer["who"], record)

    def _is_new(self, sensor, known, frame):
        """Only a sure "different" counts: a wrong split repeats an alert, and a wrong merge loses one."""
        jpeg = self.evidence.read_frame(frame.name)
        for name in known:
            try:
                answer, cost = self.client.same_subject(sensor.question, self.evidence.read_frame(name), jpeg)
            except (ApiError, KeyError, ValueError):
                return False
            with self.lock:
                self.ask_calls += 1
                self.spark_usd += cost
            if answer["same"] or answer["confidence"] < SUBJECT_PASS:
                return False
        return True

    def _meet(self, rule, sensor, frame, until, who, record):
        """One proof from a review. A new person gets a new event, and a known one keeps theirs on."""
        with sensor.lock:
            known = list(sensor.subjects)
        stranger = bool(known) and sensor.pattern == "state" and self._is_new(sensor, known, frame)
        with sensor.lock:
            # The occurrence can end while a review runs. Its old frames must not fire it again.
            if frame.t < sensor.review_from or rule.paused:
                return
            started_at = sensor.state.on_since
            if stranger and sensor.state.on:
                event = sensor.state.next_subject(frame.t, until)
            else:
                event = sensor.state.late_hit(frame.t, until)
        result = {**record, "who": who}
        check = self.evidence.save_check({**result, "t": frame.t, "event": event, "proof_frame": frame.name})
        if event:
            self._act(rule, sensor, check, frame.t, result, frame.name, event, started_at)
            self._publish({"rule": rule.id, "sensor": sensor.id, "check": check, "hit": True, "event": event,
                           "frames_covered": len(record["frames"]), "frames": record["frames"]})

    def _due(self, sensor, t):
        """Returns all frames since this sensor's last check, or None if no check is due."""
        if not sensor.awake(t):
            return None
        with sensor.lock:
            if sensor.running >= MAX_SENSOR_CHECKS or t - sensor.last_check < sensor.check_every_s:
                return None
            with self.lock:
                frames = [f for f in self.frames if f.t > sensor.covered_until and f.t <= t]
            if not frames:
                return None
            sensor.last_check = t
            sensor.covered_until = frames[-1].t
            # A moment needs movement, so a still picture costs no model call.
            if sensor.pattern == "moment" and max(f.motion for f in frames) < sensor.min_motion:
                return None
            sensor.running += 1
            turn = sensor.next_turn
            sensor.next_turn += 1
            return frames, turn

    def _check(self, rule, sensor, frames, turn):
        # One burst can span two checks. Its peak gives both checks nearly the same time,
        # so clear_after_s treats them as one occurrence.
        t = max(frames, key=lambda f: f.motion).t if sensor.pattern == "moment" else frames[-1].t
        base = {"rule": rule.id, "sensor": sensor.id}
        try:
            if sensor.target:
                outcome = self._detect(sensor, pick_frames(frames, 1, sensor.pattern)[0])
            else:
                outcome = self._judge(sensor, pick_frames(frames, sensor.frames_per_check, sensor.pattern))
            error = None
            outcome[2]["peak_motion"] = max(frame.motion for frame in frames)
        except ApiError as e:
            outcome, error = None, str(e)[:160]
        with sensor.lock:
            sensor.running -= 1
            sensor.finished[turn] = (t, len(frames), outcome, error)
        return self._settle(rule, sensor, base)

    def _settle(self, rule, sensor, base):
        """Applies finished checks in time order. A later check can finish first."""
        results = []
        while True:
            with sensor.lock:
                ready = sensor.finished.pop(sensor.settled_turn, None)
                if ready is None:
                    return results
                sensor.settled_turn += 1
                t, covered, outcome, error = ready
                if outcome is None or rule.paused:
                    event = None
                else:
                    started_at = sensor.state.on_since
                    event = sensor.state.observe(t, outcome[0], outcome[1], sensor.reviewing)
            if outcome is None:
                results.append(self._publish({**base, "skipped": True, "error": error}))
            else:
                results.append(self._record(rule, sensor, base, t, covered, outcome, event, started_at))
            self._watch(rule, sensor, t, outcome[2] if outcome else None)

    def _record(self, rule, sensor, base, t, covered, outcome, event, started_at):
        hit, strong, result = outcome
        proof = proof_frame(result)
        check = self.evidence.save_check({
            "t": t, "rule": rule.id, "rule_name": rule.name, "sensor": sensor.id,
            "target": sensor.target, "question": sensor.question, "requires": sensor.requires,
            "pattern": sensor.pattern, "frames_covered": covered, "min_motion": sensor.min_motion,
            "hit": hit, "strong": strong, "event": event, "proof_frame": proof, **result,
        })
        self._act(rule, sensor, check, t, result, proof, event, started_at)
        return self._publish({
            **base, "check": check, "hit": hit, "event": event, "frames_covered": covered, **result,
        })

    def _act(self, rule, sensor, check, t, result, proof, event, started_at):
        """Tells the rule's code. It runs each event in its own job, so this never waits for one."""
        if event == "next":
            rule.send({"type": "off", "sensor": sensor.id, "t": t, "duration": t - started_at})
        if event == "next":
            self.journal.add("end", rule.id, rule.name, sensor.id, seconds=t - started_at, t=t)
        if event in ("on", "moment", "next"):
            with sensor.lock:
                sensor.subjects = [*sensor.subjects, proof][-MAX_SUBJECTS:]
                sensor.review_after = max(sensor.review_after, t + sensor.watch_s)
            detail = self._event_detail(rule, check, result, proof)
            self.journal.add("event", rule.id, rule.name, sensor.id, text=detail["note"], t=t)
            rule.send({"type": "on", "sensor": sensor.id, "t": t, "event": detail})
        if event == "moment":
            self.journal.add("end", rule.id, rule.name, sensor.id, seconds=0.0, t=t)
            rule.send({"type": "off", "sensor": sensor.id, "t": t, "duration": 0.0})
        elif event == "off":
            self.journal.add("end", rule.id, rule.name, sensor.id, seconds=t - started_at, t=t)
            rule.send({"type": "off", "sensor": sensor.id, "t": t, "duration": t - started_at})
        if event in ("off", "moment"):
            with sensor.lock:
                sensor.subjects = []
                sensor.review_from = t
                sensor.review_after = 0.0 if event == "off" else sensor.review_after

    def _publish(self, result):
        with self.lock:
            self.result_id += 1
            result = {**result, "id": self.result_id}
            self.results.append(result)
        return result

    def camera_live(self):
        return time.time() - self.live_at < LIVE_LOST_S

    def claim(self, page, cameras=None, controls=(), wall=(1, 1)):
        """The newest page owns the camera, so two tabs cannot mix frames in one check.

        A security camera keeps it while it runs, so a page that opens or reloads only watches.
        """
        if self.camera_live() and not str(page).startswith(CAMERA_PAGE_PREFIX):
            return
        self.active_page = page
        self.cameras = list(cameras or [])
        self.wall = tuple(wall)
        self.controls.set_cameras(controls)

    def show_live(self, jpeg, page):
        """Keeps the newest picture for the page. This is the view, and no model reads it."""
        if page != self.active_page:
            return False
        with self.live:
            self.live_jpeg = jpeg
            self.live_seq += 1
            self.live_at = time.time()
            self.live.notify_all()
        return True

    def read_live(self, after):
        """Waits for a picture newer than after. Returns (seq, jpeg), or None."""
        with self.live:
            self.live.wait_for(lambda: self.live_seq > after, LIVE_WAIT_S)
            return (self.live_seq, self.live_jpeg) if self.live_seq > after else None

    def frame_full(self, name, camera, jpeg, page=None):
        """Keeps one camera's own frame. Zoom cuts from this, because a wall has fewer pixels."""
        if page != self.active_page:
            return False
        return self.evidence.save_full(name, camera, jpeg)

    def frame_sharp(self, name, jpeg, page=None):
        """Keeps the larger copy of a frame that the server already has."""
        if page != self.active_page:
            return False
        return self.evidence.save_sharp(name, jpeg)

    def frame(self, jpeg, t, page=None, motion=0.0):
        """Stores one frame and starts any checks that are due.

        Frames never wait for a model, so a slow check cannot slow the camera.
        """
        if page != self.active_page:
            return {"results": [], "inactive": True}
        if self.stopped():
            return {"results": [], "stopped": True}
        self.last_frame = time.time()
        name = self.evidence.save_frame(t, jpeg, motion)
        with self.lock:
            self.frames.append(Frame(t, jpeg, name, motion))
            if name:
                self.timeline.append(Frame(t, None, name, motion))
            pairs = [(rule, sensor) for rule in self.entries.values() if not rule.paused for sensor in rule.sensors]
        results = []
        for rule, sensor in pairs:
            due = self._due(sensor, t)
            if not due:
                continue
            if self.inline:
                results.extend(self._check(rule, sensor, *due))
            else:
                self.detection.submit(self._check, rule, sensor, *due)
        return {"results": results, "stopped": self.stopped(), "name": name}


def load_recording(folder):
    """Returns what save_recording kept: (example, idle, motions, gap_s)."""
    def frames(label):
        return [file.read_bytes() for file in sorted((folder / "recording" / label).glob("*.jpg"))]
    timing = folder / "recording" / "timing.json"
    saved = json.loads(timing.read_text()) if timing.exists() else {}
    return frames("example"), frames("idle"), saved.get("motions"), saved.get("gap_s")


def save_recording(folder, example, idle, motions=None, gap_s=None):
    """Keeps a person's recording with the rule, for later tests."""
    for label, frames in (("example", example), ("idle", idle)):
        if frames:
            target = folder / "recording" / label
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            for index, jpeg in enumerate(frames):
                (target / f"{index:03d}.jpg").write_bytes(jpeg)
    if example:
        (folder / "recording" / "timing.json").write_text(json.dumps({"motions": motions, "gap_s": gap_s}))


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Whenever</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header>
    <h1>Whenever</h1>
    <p>Point at it, say it, or show it. Your camera runs it.</p>
  </header>
  <main class="login">
    <form id="login">
      <label for="code">Access code</label>
      <input id="code" type="password" autocomplete="current-password" required>
      <div class="form-row"><button type="submit">Open Whenever</button></div>
      <p id="note" class="note">This demo spends real model credit, so it needs the code from the submission.</p>
    </form>
  </main>
  <script>
    document.getElementById("login").addEventListener("submit", async (event) => {
      event.preventDefault();
      const response = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: document.getElementById("code").value }),
      });
      if (response.ok) location.reload();
      else {
        const note = document.getElementById("note");
        note.className = "note error";
        note.textContent = (await response.json()).error || "That code did not work.";
      }
    });
  </script>
</body>
</html>
"""


class Access:
    """The access code of a hosted server, and the sessions of the people who gave it."""

    def __init__(self, path=ACCESS_FILE, sessions_path=SESSIONS_FILE):
        try:
            self.code = Path(path).read_text().strip()
        except OSError:
            self.code = ""
        self.sessions_path = Path(sessions_path)
        self.lock = threading.Lock()
        self.failures = deque()
        try:
            self.sessions = {k: float(v) for k, v in json.loads(self.sessions_path.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            self.sessions = {}

    def needed(self):
        return bool(self.code)

    @staticmethod
    def _hash(token):
        # Only a hash is saved, so the sessions file cannot log anyone in.
        return hashlib.sha256(token.encode()).hexdigest()

    def valid(self, token):
        return self.sessions.get(self._hash(str(token)), 0) > time.time()

    def login(self, code):
        """Returns a new session token, or None. Wrong codes are limited, so guessing is slow."""
        now = time.time()
        with self.lock:
            while self.failures and now - self.failures[0] > 60:
                self.failures.popleft()
            if len(self.failures) >= MAX_LOGINS_PER_MINUTE:
                raise ValueError("Too many tries. Wait one minute.")
            if not self.code or not hmac.compare_digest(str(code).encode(), self.code.encode()):
                self.failures.append(now)
                return None
            token = secrets.token_urlsafe(32)
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            self.sessions[self._hash(token)] = now + SESSION_S
            self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
            self.sessions_path.write_text(json.dumps(self.sessions))
        return token


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app = None
    access = Access(path="", sessions_path=os.devnull)

    def log_message(self, fmt, *args):
        if "/api/" not in str(args[0]):
            super().log_message(fmt, *args)

    def _send(self, status, body, content_type, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value):
        self._send(status, json.dumps(value).encode(), "application/json")

    def _allowed(self):
        # Only this page may use the server, so another website cannot trigger actions or spend.
        local = (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        host = self.headers.get("Host")
        if host in local:
            origins = tuple(f"http://{h}" for h in local)
        elif host in HOSTS:
            origins = (f"https://{host}",)
        else:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in origins

    def _logged_in(self):
        if not self.access.needed():
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "").get("whenever")
        return bool(cookie) and self.access.valid(cookie.value)

    def _login(self):
        raw = self._read(MAX_LABEL_BYTES)
        try:
            token = self.access.login(json.loads(raw or b"{}").get("code") or "")
        except ValueError as e:
            return self._json(429, {"error": str(e)})
        if not token:
            return self._json(403, {"error": "That code did not work."})
        secure = "; Secure" if self.headers.get("Host") in HOSTS else ""
        cookie = f"whenever={token}; Path=/; Max-Age={SESSION_S}; HttpOnly; SameSite=Strict{secure}"
        self._send(200, b'{"ok": true}', "application/json", [("Set-Cookie", cookie)])

    def _read(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def do_GET(self):
        if not self._allowed():
            return self._json(403, {"error": "Forbidden."})
        path, _, query = self.path.partition("?")
        if not self._logged_in():
            if path in ("/", "/index.html"):
                return self._send(200, LOGIN_PAGE.encode(), "text/html; charset=utf-8")
            if path != "/style.css":
                return self._json(401, {"error": "Log in first."})
        if path == "/api/state":
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            return self._json(200, self.app.status(int(params.get("after", 0)), int(params.get("results", 0))))
        if path == "/api/live.jpg":
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            picture = self.app.read_live(int(params.get("after", 0)))
            if not picture:
                return self._send(204, b"", "image/jpeg")
            return self._send(200, picture[1], "image/jpeg", [("X-Seq", str(picture[0]))])
        if path.startswith("/api/job/"):
            job = self.app.jobs.get(path.rsplit("/", 1)[1])
            return self._json(200 if job else 404, job or {"error": "Not found."})
        if path.startswith("/api/check/"):
            check = self.app.evidence.check(path.rsplit("/", 1)[1])
            return self._json(200 if check else 404, check or {"error": "Not found."})
        if path.startswith("/rule-file/"):
            parts = unquote(path).split("/", 3)
            folder = sandbox.RULES_DIR / parts[2] if len(parts) == 4 and parts[2].isalnum() else None
            picture = rule_picture(folder, parts[3]) if folder else None
            if picture:
                return self._send(200, picture.read_bytes(), IMAGE_TYPES[picture.suffix.lower()])
            return self._json(404, {"error": "Not found."})
        if path.startswith("/evidence/"):
            parts = path.split("/")
            frame = None
            if len(parts) == 4 and parts[2] == self.app.evidence.session:
                params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                camera = params.get("camera", "")
                frame = self.app.evidence.full_path(parts[3], int(camera)) if camera.isdigit() else None
                frame = frame or self.app.evidence.sharp_path(parts[3])
            if frame:
                return self._send(200, frame.read_bytes(), "image/jpeg")
            return self._json(404, {"error": "Not found."})
        if path.startswith("/api/rule/") and path.endswith("/code"):
            code = self.app.code(path.split("/")[3])
            if code is None:
                return self._json(404, {"error": "Not found."})
            return self._send(200, code.encode(), "text/plain; charset=utf-8")
        if path in STATIC:
            name, content_type = STATIC[path]
            file = ROOT / name
            if file.exists():
                return self._send(200, file.read_bytes(), content_type)
        self._json(404, {"error": "Not found."})

    def do_POST(self):
        if not self._allowed():
            return self._json(403, {"error": "Forbidden."})
        if self.path == "/api/login":
            return self._login()
        if not self._logged_in():
            return self._json(401, {"error": "Log in first."})
        try:
            if self.path == "/api/frame":
                jpeg = self._read(MAX_FRAME_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                t = float(self.headers.get("X-Frame-Time", "0"))
                motion = float(self.headers.get("X-Motion", "0"))
                return self._json(200, self.app.frame(jpeg, t, self.headers.get("X-Page"), motion))
            if self.path == "/api/live":
                jpeg = self._read(MAX_SHARP_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                return self._json(200, {"shown": self.app.show_live(jpeg, self.headers.get("X-Page"))})
            if self.path.startswith("/api/frame/") and "/full/" in self.path:
                jpeg = self._read(MAX_FULL_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                parts = self.path.split("/")
                saved = self.app.frame_full(parts[3], int(parts[5]), jpeg, self.headers.get("X-Page"))
                return self._json(200, {"saved": saved})
            if self.path.startswith("/api/frame/") and self.path.endswith("/sharp"):
                jpeg = self._read(MAX_SHARP_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                saved = self.app.frame_sharp(self.path.split("/")[3], jpeg, self.headers.get("X-Page"))
                return self._json(200, {"saved": saved})
            if self.path == "/api/claim":
                claim = json.loads(self._read(MAX_CLAIM_BYTES))
                names = [str(name)[:40] for name in claim.get("cameras") or []][:MAX_CAMERAS]
                columns, rows = claim.get("wall") or (1, 1)
                wall = (int(number(columns, 1, MAX_CAMERAS, 1)), int(number(rows, 1, MAX_CAMERAS, 1)))
                self.app.claim(str(claim.get("page"))[:40], names, list(claim.get("controls") or []), wall)
                return self._json(200, {"active": True})
            if self.path == "/api/camera":
                return self._json(200, self.app.camera_command(json.loads(self._read(MAX_LABEL_BYTES))))
            if self.path == "/api/rule" or (self.path.startswith("/api/rule/") and self.path.endswith("/edit")):
                edit = self.path.split("/")[3] if self.path != "/api/rule" else None
                raw = self._read(MAX_JSON_BYTES)
                if raw is None:
                    return self._json(413, {"error": "The recording is too large."})
                body = json.loads(raw)
                sentence = str(body.get("sentence") or "").strip()[:MAX_SENTENCE]
                example = [base64.b64decode(f) for f in body.get("example") or []]
                idle = [base64.b64decode(f) for f in body.get("idle") or []]
                if not sentence and not (example and not edit):
                    return self._json(400, {"error": "Write a sentence or record an example."})
                motions = [float(m) for m in body.get("motions") or []]
                gap_s = number(body.get("gap_s"), 0.02, 2, None)
                point = str(body.get("point") or "")[:20]
                return self._json(200, {"job": self.app.start_job(sentence, example, idle, motions, gap_s, point, edit)})
            if self.path == "/api/point":
                raw = self._read(MAX_FRAME_BYTES * 2)
                if raw is None:
                    return self._json(413, {"error": "Frame is too large."})
                body = json.loads(raw)
                return self._json(200, self.app.point(
                    base64.b64decode(body["frame"]), int(body["x"]), int(body["y"]), int(body["w"]), int(body["h"])))
            if self.path.startswith("/api/point/") and self.path.endswith("/name"):
                return self._json(200, {"noun": self.app.name_point(self.path.split("/")[3])})
            if self.path.startswith("/api/rule/") and self.path.rsplit("/", 1)[1] in ("stop", "resume"):
                rule_id, action = self.path.split("/")[3:5]
                done = (self.app.pause_rule if action == "stop" else self.app.resume_rule)(rule_id)
                return self._json(200 if done else 404, {"done": done})
            if self.path.startswith("/api/check/") and self.path.endswith("/outline"):
                return self._json(200, {"outline": self.app.check_outline(self.path.split("/")[3])})
            if self.path == "/api/voice":
                return self._json(200, {"id": self.app.start_voice()})
            if self.path.startswith("/api/voice/") and self.path.endswith("/audio"):
                chunk = self._read(MAX_AUDIO_BYTES)
                if chunk is None:
                    return self._json(413, {"error": "Audio is too large."})
                return self._json(200, {"text": self.app.voice_audio(self.path.split("/")[3], chunk)})
            if self.path.startswith("/api/voice/") and self.path.endswith("/end"):
                return self._json(200, {"text": self.app.end_voice(self.path.split("/")[3])})
            if self.path == "/api/label":
                raw = self._read(MAX_LABEL_BYTES)
                body = json.loads(raw)
                self.app.evidence.label(str(body.get("check"))[:20], str(body.get("label"))[:40])
                return self._json(200, {"saved": True})
            if self.path == "/api/action/draft":
                raw = self._read(MAX_ACTION_WORDS * 4)
                if raw is None:
                    return self._json(413, {"error": "The description is too long."})
                words = str(json.loads(raw).get("words") or "").strip()[:MAX_ACTION_WORDS]
                if not words:
                    return self._json(400, {"error": "Describe the action."})
                return self._json(200, self.app.draft_action(words))
            if self.path.startswith("/api/action/") and self.path.rsplit("/", 1)[1] in ("test", "save"):
                draft_id, step = self.path.split("/")[3:5]
                if step == "save":
                    return self._json(200, {"saved": self.app.save_draft(draft_id)["name"]})
                inputs = json.loads(self._read(MAX_LABEL_BYTES * 4) or b"{}").get("inputs") or {}
                return self._json(200, self.app.test_draft(draft_id, inputs))
            self._json(404, {"error": "Not found."})
        except (writer.WriterError, ControlError, ActionError) as e:
            self._json(422, {"error": str(e)})
        except (ApiError, VoiceError) as e:
            self._json(502, {"error": str(e)[:300]})
        except (KeyError, ValueError):
            self._json(400, {"error": "Bad request."})

    def do_DELETE(self):
        if not self._allowed() or not self._logged_in():
            return self._json(403, {"error": "Forbidden."})
        if self.path.startswith("/api/rule/"):
            removed = self.app.remove_rule(self.path.rsplit("/", 1)[1])
            return self._json(200 if removed else 404, {"removed": removed})
        if self.path.startswith("/api/action/"):
            removed = self.app.connectors.remove(unquote(self.path.rsplit("/", 1)[1]))
            return self._json(200 if removed else 404, {"removed": removed})
        self._json(404, {"error": "Not found."})


def main():
    if not ENV_FILE.exists() or not ENV_FILE.read_text().strip():
        sys.exit(f"Add your Meta API key to {ENV_FILE}")
    Handler.app = App(Client(load_key(ENV_FILE)), journal=Journal(JOURNAL_FILE),
                      connectors=Connectors(ACTIONS_FILE), rules_file=RULES_FILE)
    Handler.access = Access()
    if Handler.access.needed():
        print("The page asks for the access code in access.env.", flush=True)
    back = Handler.app.load_rules()
    if back:
        print(f"{back} rules are back after the restart.", flush=True)
    Handler.app.start_ticks()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Whenever is running at http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
