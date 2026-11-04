"""The free live analyzer: who is where, for how long, at which table, for every camera, all the time.

It runs on the server's CPU and calls no paid model. For each camera it reads that camera's own picture
of the newest frame, two times a second for a camera that a page watches and every few seconds for the
others, and finds people, cars, motorcycles and bicycles with the local detector (detect.py). It follows
each one across looks (rule_runtime.Tracker), and for each person keeps:

- how long they stayed, and the last 20 points where their feet were;
- the table they sit at, and since when (tables come from the detector's "dining table" boxes over the
  first looks, or from a rule's places, named T1, T2 ... by rows from the top left);
- a raised hand, held on 2 looks, from the body points (detect.poses);
- who they are: the clearest faces of the track (size x YuNet's score x sharpness), matched to the tagged
  people or to an anonymous visitor (people.py). The name stays on the whole track, and their clothes
  carry it across a gap in the track and to other cameras the same day, a little less sure.

Each table is free, busy (people sit there; a visit ends after 30 s with nobody), waiting (busy 10 min and
no staff came to it since the guests sat down) or help (a hand is up). Staff are tagged people with the role
"staff", or else a standing person who comes to the table for 2 s and is not one of its guests.

A small SQLite file keeps each analysed frame for 24 hours, for boxes over clips, the events (seated,
left, back, hand_up, told, alert) for 24 hours, and each track's best picture and look numbers for 48
hours, for a search in words. Pictures go in the same folder, served only to a logged-in page.

The page asks for overlay() about every half second, so it reads only what the threads already found.
"""

import itertools
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import detect
import people
from rule_runtime import Found, Tracker, foot, place_of, seat, share_inside

THINGS = ("person", "car", "motorcycle", "bicycle")
TABLE = "dining table"
MIN_CONFIDENCE = 0.35
WATCHED_S = 60.0          # a camera a page asked about this recently gets the fast rate
FAST_EVERY_S = 0.5        # two looks a second
MOVING_EVERY_S = 1.0      # a camera with movement that no page watches
SLOW_EVERY_S = 3.0
FRESH_S = 30.0            # timers run on to now while a camera's last look is this recent: a still camera
                          # sends a frame only every 10 s
SETTLE_S = 0.6           # a frame this old has its sharp and full copies
WORKERS = int(os.environ.get("WHENEVER_LIVE_WORKERS") or 2)
MAX_POSES = 12
TRACK_GAP_S = 15.0        # a person hidden this long is still the same track
KEEP_TRACK_S = 180.0      # an ended track stays this long, for an alert's evidence
TRAIL = 20
SEAT_GAP_S = 30.0         # a guest who stands up for less than this still sits there
VISIT_GAP_S = 30.0        # a table with nobody for less than this keeps its visit, as ctx.visit does
WAIT_S = 600.0
STAFF_STAY_S = 2.0
STAYED_TO_SIT_S = 4.0     # a person who sits here for this long sits at the table, not walks past
SITTING_TALL = 2.3        # a box less tall than this over its width is a person who sits
HAND_LOOKS = 2
TOLD_S = 30.0
FACE_EVERY_S = 1.0
FACE_BETTER = 1.1         # a face this much clearer than the track's best is worth the face model
FACE_KEEP = 3
SURE_ENOUGH = 0.95        # a track this sure looks for faces only now and then
MIN_PERSON_PX = 60        # a smaller person cannot show a face of MIN_FACE pixels
CLEAR_FACE_PX, CLEAR_FACE_SCORE, CLEAR_SHARP = 28, 0.85, 0.3   # a new visitor needs such a face
LOOK_STABLE_S = 4.0
LOOK_EVERY_S = 60.0
LOOK_QUEUE = 8
BACK_GAP_S = 1800.0       # a person seen again after this is back
TABLE_LOOKS = 24          # looks that vote for the tables of a camera
TABLE_SHARE = 0.3         # a table must be found in this share of them
TABLE_MIN_AREA = 0.004
TABLE_SAME = 0.3
TABLE_RETRY_S = 600.0
FRAME_HOURS, EVENT_HOURS, TRACK_HOURS = 24, 24, 48
GRID_ROWS, GRID_COLUMNS = 18, 32
MAX_HEAT_STEP_S = 3.0
PRUNE_EVERY_S = 600.0
EVENTS_SHOWN = 50
CROP_SIDE = 160
EVENT_SIDE = 360


def _round(box):
    return [round(float(v), 3) for v in box]


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "camera"


def _jpeg(picture, side, quality=82):
    from io import BytesIO
    picture = picture.copy()
    picture.thumbnail((side, side))
    out = BytesIO()
    picture.convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue()


def _crop(picture, box, pad=0.15):
    """A part of a PIL picture around a box from 0 to 1, with some room."""
    w, h = picture.size
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    return picture.crop((max(0, int((x1 - px) * w)), max(0, int((y1 - py) * h)),
                         min(w, int((x2 + px) * w)), min(h, int((y2 + py) * h))))


def sharpness(crop):
    """How sharp a face crop is, from 0.2 to 1.2: the spread of its edges."""
    import cv2
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return 0.2 + min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 300.0)


def group_tables(votes, looks, share=TABLE_SHARE):
    """The tables that many looks agree on: boxes that overlap join one group, and a group found in at
    least share of the looks is a table, at its median box. Sorted by rows from the top, then from the left."""
    groups = []
    for box in sorted(votes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True):
        home = next((g for g in groups if _overlap(g["box"], box) >= TABLE_SAME), None)
        if home is None:
            groups.append({"box": box, "boxes": [box]})
        else:
            home["boxes"].append(box)
    tables = []
    for group in groups:
        if len(group["boxes"]) < max(2, share * looks):
            continue
        middle = [sorted(b[i] for b in group["boxes"])[len(group["boxes"]) // 2] for i in range(4)]
        if all(share_inside(middle, other) < 0.8 and share_inside(other, middle) < 0.8 for other in tables):
            tables.append(middle)
    return sorted(tables, key=lambda b: (round((b[1] + b[3]) * 2), (b[0] + b[2]) / 2))


def _overlap(a, b):
    common = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - common
    return common / union if union > 0 else 0.0


class Track:
    """One person or thing that one camera follows."""

    def __init__(self, number, camera, label, t):
        self.id, self.camera, self.label = number, camera, label
        self.first = self.last = t
        self.box, self.tall, self.stayed = (0, 0, 0, 0), 0.0, 0.0
        self.trail = deque(maxlen=TRAIL)
        self.place = None                       # the table near its seat point
        self.seated_at, self.seated_since, self.seated_seen = None, None, 0.0
        self.at_table, self.at_table_since, self.helper_t = None, 0.0, 0.0
        self.hand_streak, self.hand_down, self.hand_first, self.hand_since, self.hand_end = 0, 0, 0.0, None, 0.0
        self.faces = []                         # the clearest faces: {q, size, score, t, crop}
        self.face_t = -1e9
        self.hits = {}                          # identity: [sure of each face match]
        self.who = None
        self.who_face_t = 0.0
        self.look, self.look_t, self.look_area = None, -1e9, 0.0
        self.told, self.told_t = None, 0.0
        self.sighting = None
        self.crop = None
        self.saved = 0.0


class Table:
    def __init__(self, place_id, name, box):
        self.id, self.name, self.box = place_id, name, tuple(box)
        self.count, self.visit_start, self.visit_seen = 0, None, 0.0
        self.staff_t = None
        self.state, self.note = "free", ""


class Camera:
    def __init__(self, name):
        self.name = name
        self.size = [0, 0]
        self.t = 0.0
        self.frame = None
        self.looked = -1e9
        self.busy = False
        self.tracks = {}
        self.tables = []
        self.table_votes, self.table_looks, self.table_retry = [], 0, 0.0
        self.snapshot = None
        self.heat, self.heat_minute, self.heat_t = None, None, None


class Analyzer:
    """See the module's words. app is the server's App: its cameras, frames, people and spend."""

    def __init__(self, app, folder, zone=None, clock=time.time):
        self.app = app
        self.folder = Path(folder)
        self.zone = zone
        self.clock = clock
        self.lock = threading.RLock()
        self.db_lock = threading.Lock()
        self.track_lock = threading.Lock()
        self.cameras = {}
        self.watched = {}
        self.events = deque(maxlen=500)
        self.day_looks = {}       # identity: [{v, camera, sure, t, who}], today only
        self.day = None
        self.last_seen = {}       # identity: time, for "back"
        self.looks = deque()
        self.looks_ready = threading.Event()
        self.done = deque(maxlen=400)
        self.spend_marks = deque()
        self.errors = deque(maxlen=20)
        self.running = False
        self.pool = None
        self.pruned = 0.0
        self.db = None
        self._open()
        self.places = self._read_places()
        self.tracker = Tracker(min_overlap=0.3, max_gap_s=TRACK_GAP_S, max_step=0.5)
        self.tracker.numbers = itertools.count(self._next_track())

    # ----- storage -----
    def _open(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        for part in ("crops", "faces", "events"):
            (self.folder / part).mkdir(exist_ok=True)
        self.db = sqlite3.connect(str(self.folder / "live.db"), check_same_thread=False)
        with self.db_lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS frames (camera TEXT, t REAL, data TEXT);"
                "CREATE INDEX IF NOT EXISTS frames_at ON frames (camera, t);"
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, t REAL, camera TEXT, kind TEXT, text TEXT,"
                " person TEXT, picture TEXT);"
                "CREATE INDEX IF NOT EXISTS events_at ON events (t);"
                "CREATE TABLE IF NOT EXISTS tracks (camera TEXT, track INTEGER, first REAL, last REAL, name TEXT,"
                " person_id TEXT, role TEXT, sure REAL, by TEXT, visitor TEXT, look BLOB, crop TEXT, face TEXT,"
                " PRIMARY KEY (camera, track));"
                "CREATE INDEX IF NOT EXISTS tracks_at ON tracks (last);"
                "CREATE TABLE IF NOT EXISTS heat (camera TEXT, minute INTEGER, grid BLOB, PRIMARY KEY (camera, minute));")
            rows = self.db.execute("SELECT t, camera, kind, text, person, picture FROM events WHERE t >= ? ORDER BY t",
                                   (self.clock() - 3600,)).fetchall()
        for t, camera, kind, text, person, picture in rows:
            self.events.append({"t": t, "camera": camera, "kind": kind, "text": text, "person": person,
                                "picture": picture})

    def _next_track(self):
        """Track numbers go on after a restart, so old frames keep their own people."""
        with self.db_lock:
            row = self.db.execute("SELECT MAX(track) FROM tracks").fetchone()
        return int(row[0] or 0) + 1

    def _read_places(self):
        try:
            return json.loads((self.folder / "places.json").read_text())
        except (OSError, ValueError):
            return {}

    def _save_places(self):
        part = self.folder / "places.json.part"
        part.write_text(json.dumps(self.places, indent=1))
        part.replace(self.folder / "places.json")

    def file(self, path):
        """A picture of this folder by its address after /live-file/, or None."""
        parts = str(path).split("/")
        if len(parts) != 2 or parts[0] not in ("crops", "faces", "events") or not re.fullmatch(r"[\w.-]+\.jpg", parts[1]):
            return None
        file = self.folder / parts[0] / parts[1]
        return file if file.is_file() else None

    @staticmethod
    def url(path):
        return f"/live-file/{path}" if path else None

    # ----- the loop -----
    def start(self):
        """Starts the threads. The server calls this once; tests call look() themselves."""
        if self.running or not detect.available():
            return False
        self.running = True
        self.pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="live")
        threading.Thread(target=self._loop, daemon=True, name="live-loop").start()
        threading.Thread(target=self._look_loop, daemon=True, name="live-looks").start()
        return True

    def watch(self, names):
        until = self.clock() + WATCHED_S
        with self.lock:
            for name in names:
                self.watched[name] = until

    def _names(self):
        return list(self.app.cameras) or ["Camera"]

    def _newest(self):
        """The newest frame whose larger copies have surely arrived."""
        with self.app.lock:
            recent = list(self.app.timeline)[-2:]
        if not recent:
            return None
        newest = recent[-1]
        sharp = self.app.evidence.frames_dir / "sharp" / newest.name
        if self.clock() - newest.t >= SETTLE_S or sharp.exists() or len(recent) == 1:
            return newest
        return recent[0]

    def _moving(self, frame, index):
        """True if this camera has movement in this frame: camera.py keeps its full frame only then."""
        return (self.app.evidence.frames_dir / "full" / f"{frame.name[:-4]}_{index}.jpg").exists()

    def _loop(self):
        while self.running:
            try:
                self._schedule()
                now = self.clock()
                if now - self.pruned > PRUNE_EVERY_S:
                    self.pruned = now
                    self.prune()
                if not self.spend_marks or now - self.spend_marks[-1][0] >= 60:
                    self.spend_marks.append((now, self.app.spend()))
                    while self.spend_marks and now - self.spend_marks[0][0] > 3700:
                        self.spend_marks.popleft()
            except Exception as e:  # the loop must never stop
                self.errors.append(f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {e}")
            time.sleep(0.1)

    def _idle(self):
        """True while the server says nobody watches a demo. A real site has no idle(), or says False."""
        idle = getattr(self.app, "idle", None)
        try:
            return bool(idle()) if callable(idle) else False
        except Exception:
            return False

    def _schedule(self):
        if self._idle():
            return
        frame = self._newest()
        if frame is None:
            return
        now = self.clock()
        for index, name in enumerate(self._names()):
            with self.lock:
                cam = self.cameras.setdefault(name, Camera(name))
                if cam.busy:
                    continue
                if self.watched.get(name, 0) > now:
                    every = FAST_EVERY_S
                else:
                    every = MOVING_EVERY_S if self._moving(frame, index) else SLOW_EVERY_S
                if frame.name == cam.frame or now - cam.looked < every:
                    continue
                cam.busy, cam.looked = True, now
            self.pool.submit(self._job, name, index, frame)

    def _job(self, name, index, frame):
        try:
            picture = self.source(frame.name, index)
            if picture is not None:
                self.look(name, frame, picture)
        except Exception as e:
            self.errors.append(f"{time.strftime('%H:%M:%S')} {name}: {type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.cameras[name].busy = False

    def source(self, frame_name, index):
        """One camera's own picture of a frame: its full frame when camera.py kept one, or its tile of the
        sharp wall. It checks the full frame's file directly, which is cheaper than a folder listing."""
        from PIL import Image
        full = self.app.evidence.frames_dir / "full" / f"{frame_name[:-4]}_{index}.jpg"
        if full.exists():
            try:
                picture = Image.open(full)
                picture.draft("RGB", (1440, 810))
                return picture.convert("RGB")
            except OSError:
                pass
        wall = self.app._sharp_wall(frame_name)
        if wall is None:
            return None
        return wall.crop(self.app._tile_box(wall.size, index)) if self.app.cameras else wall

    # ----- one look at one camera -----
    def look(self, name, frame, picture):
        """Reads one camera's picture of one frame, and updates its tracks, tables, events and history."""
        started = time.monotonic()
        t = float(frame.t)
        with self.lock:
            cam = self.cameras.setdefault(name, Camera(name))
            learning = self._learning(cam, t)
        labels = list(THINGS) + ([TABLE] if learning else [])
        items = detect.detect(picture, labels, MIN_CONFIDENCE)
        found = [Found(None, name, item["label"], item["confidence"], item["box"], tall=item["tall"])
                 for item in items if item["label"] != TABLE]
        with self.track_lock:
            self.tracker.number(found, t)
        persons = [f for f in found if f.label == "person"]
        bodies = {}
        if persons and detect.pose_available():
            chosen = sorted(persons, key=lambda f: -(f.box[3] - f.box[1]))[:MAX_POSES]
            bodies = {f.id: body for f, body in zip(chosen, detect.poses(picture, [f.box for f in chosen]))}
        with self.lock:
            cam.size = list(picture.size)
            if learning:
                self._learn_tables(cam, [item["box"] for item in items if item["label"] == TABLE], t)
            faces = self._update(cam, found, bodies, t, picture)
        self._faces(cam, faces, picture, t, frame.name)
        with self.lock:
            self._tables(cam, t, picture)
            self._heat(cam, t)
            cam.t, cam.frame = t, frame.name
            cam.snapshot = self._snapshot(cam, t)
            row = {"t": round(t, 2), "people": [{"id": p["id"], "box": p["box"], "name": p["name"] or p["visitor"],
                                                  "sure": p["sure"], "role": p["role"]} for p in cam.snapshot["people"]],
                   "things": [{"id": x["id"], "label": x["label"], "box": x["box"]} for x in cam.snapshot["things"]]}
            dirty = [tr for tr in cam.tracks.values() if tr.label == "person" and tr.last == t and t - tr.saved >= 10]
            for tr in dirty:
                tr.saved = t
            saves = [self._track_row(tr) for tr in dirty]
            self.done.append((self.clock(), time.monotonic() - started))
        with self.db_lock:
            self.db.execute("INSERT INTO frames (camera, t, data) VALUES (?, ?, ?)", (name, t, json.dumps(row)))
            self._save_tracks(saves)
            self.db.commit()

    def _update(self, cam, found, bodies, t, picture):
        """Updates each track from one look, and returns the person tracks that may show a face."""
        width, height = picture.size
        names = {table.name: table.box for table in cam.tables}
        wanted = []
        for f in found:
            track = cam.tracks.get(f.id)
            if track is None:
                track = cam.tracks[f.id] = Track(f.id, cam.name, f.label, t)
            track.box, track.tall, track.stayed, track.last = _round(f.box), f.tall, f.stayed, t
            track.trail.append([round(v, 3) for v in foot(f)])
            if f.label != "person":
                continue
            body = bodies.get(f.id) or {}
            self._sitting(cam, track, body, names, t)
            self._hand(cam, track, body, t, picture)
            box_px = (f.box[2] - f.box[0]) * width, (f.box[3] - f.box[1]) * height
            if people.available() and box_px[1] >= MIN_PERSON_PX and self._wants_face(track, t):
                wanted.append(track)
            if t - track.first >= LOOK_STABLE_S and t - track.look_t >= LOOK_EVERY_S and len(self.looks) < LOOK_QUEUE:
                track.look_t = t
                self.looks.append((cam.name, track.id, _crop(picture, f.box, 0.05), t, box_px[0] * box_px[1]))
                self.looks_ready.set()
        for number in [n for n, tr in cam.tracks.items() if t - tr.last > KEEP_TRACK_S]:
            del cam.tracks[number]
        return wanted

    def _sitting(self, cam, track, body, names, t):
        """Where a person sits, and whether they are staff who came to a table."""
        track.place = place_of(track.box, names, point=seat, near=0.5) if names else None
        sitting = body.get("sitting")
        if sitting is None:
            sitting = track.tall < SITTING_TALL
        if track.place and sitting and track.stayed >= STAYED_TO_SIT_S:
            if track.seated_at != track.place or t - track.seated_seen > SEAT_GAP_S:
                track.seated_at, track.seated_since = track.place, t - track.stayed
            track.seated_seen = t
        elif track.seated_at and t - track.seated_seen > SEAT_GAP_S:
            track.seated_at = track.seated_since = None
        tagged_staff = bool(track.who and track.who.get("role") == "staff")
        guest = track.seated_at == track.place and track.seated_at is not None
        if track.place and not guest and (tagged_staff or not sitting):
            if track.at_table != track.place:
                track.at_table, track.at_table_since = track.place, t
            if t - track.at_table_since >= STAFF_STAY_S:
                track.helper_t = t
                table = next(tb for tb in cam.tables if tb.name == track.place)
                table.staff_t = t
        else:
            track.at_table = None

    def _hand(self, cam, track, body, t, picture):
        """A raised hand counts when two looks in a row see it, and ends after two looks without it."""
        if body.get("hand_up") is True:
            track.hand_streak += 1
            track.hand_down = 0
            if track.hand_streak == 1:
                track.hand_first = t
            if track.hand_streak >= HAND_LOOKS and track.hand_since is None:
                track.hand_since = track.hand_first
                where = track.seated_at or track.place
                self._event(cam.name, "hand_up", f"{where} hand up" if where else "Hand up", self._name(track),
                            picture, track.box, t)
        else:
            track.hand_down += 1
            if track.hand_down >= 2:
                if track.hand_since is not None:
                    track.hand_end = t
                track.hand_streak, track.hand_since = 0, None

    # ----- faces and looks -----
    def _wants_face(self, track, t):
        if t - track.face_t < FACE_EVERY_S:
            return False
        sure = (track.who or {}).get("sure") or 0
        if (track.who or {}).get("by") == "face" and sure >= SURE_ENOUGH and len(track.faces) >= FACE_KEEP:
            return t - track.face_t >= 10 * FACE_EVERY_S
        return True

    def _faces(self, cam, tracks, picture, t, frame_name):
        """Finds the face of each track that may show one, and runs the face model only on a face clearer
        than the track's best, so most looks cost a few milliseconds."""
        if not tracks:
            return
        book = self.app.people
        bgr = people.picture_array(picture)
        for track in tracks:
            track.face_t = t
            face = book.faces.find(bgr, track.box)
            if not face:
                continue
            crop = book.faces.crop(bgr, face)
            if crop is None:
                continue
            sharp = sharpness(crop)
            quality = face["size"] * face["score"] * sharp
            best = max((f["q"] for f in track.faces), default=0.0)
            if track.faces and quality <= best * FACE_BETTER:
                continue
            vector = book.faces.vector(bgr, face)
            track.faces = sorted(track.faces + [{"q": quality, "size": face["size"], "score": face["score"], "t": t}],
                                 key=lambda f: f["q"])[-FACE_KEEP:]
            found = {"face": vector, "look": None, "face_size": face["size"], "crop": crop}
            clear = face["size"] >= CLEAR_FACE_PX and face["score"] >= CLEAR_FACE_SCORE and sharp >= CLEAR_SHARP
            match = book.identify(found, cam.name, t, clear)
            self._save_face(track, crop)
            if match is None:
                continue
            match["sighting"] = book.saw(match, found, cam.name, where=frame_name, now=t)
            with self.lock:
                self._named(cam, track, match, t, picture)

    def _save_face(self, track, crop):
        import cv2
        name = f"faces/{track.id}.jpg"
        cv2.imwrite(str(self.folder / name), crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
        track.face_file = name

    def _named(self, cam, track, match, t, picture):
        """A face match on a track: the surest identity of its faces names the whole track."""
        track.hits.setdefault(match["id"], []).append(match.get("sure") or 0.0)
        best = max(track.hits, key=lambda key: people.combine(track.hits[key]))
        if best != match["id"]:
            return
        sure = people.combine(track.hits[best])
        # One person is in one place: of two tracks in view that claim them, the less sure one lets go.
        for other in cam.tracks.values():
            if other is track or not other.who or other.who.get("id") != best or t - other.last > 2:
                continue
            if (other.who.get("sure") or 0) >= sure:
                track.hits.pop(best, None)
                return
            other.who = None
            other.hits.pop(best, None)
        first = track.who is None
        track.who = {k: match.get(k) for k in ("person_id", "name", "role", "visitor", "visits", "score", "face_px")}
        track.who.update(id=match["id"], by="face", sure=people.combine(track.hits[best]))
        track.who_face_t = t
        track.sighting = match.get("sighting") or track.sighting
        if first:
            self._arrived(cam, track, t, picture)

    def _arrived(self, cam, track, t, picture):
        """A named person or a returning visitor came: one "back" event a visit."""
        key = track.who["id"]
        before = self.last_seen.get(key)
        self.last_seen[key] = t
        if before is not None and t - before < BACK_GAP_S:
            return
        record = self.app.people.get(key) or {}
        count = len(people.visits(list(record.get("seen") or [])))
        if track.who.get("visitor"):
            if count < 2:
                return
            text = f"{track.who['visitor']} is back, visit {count}"
        else:
            text = f"{track.who['name']} is back" if count >= 2 else f"{track.who['name']} came in"
        self._event(cam.name, "back", text, self._name(track), picture, track.box, t)

    def _look_loop(self):
        """Clothes and build of each settled track, on one thread, so faces and boxes never wait for it."""
        while self.running:
            self.looks_ready.wait(1.0)
            self.looks_ready.clear()
            while self.looks:
                try:
                    self.add_look(*self.looks.popleft())
                except Exception as e:
                    self.errors.append(f"{time.strftime('%H:%M:%S')} look: {type(e).__name__}: {e}")

    def add_look(self, camera, number, crop, t, area):
        """The look of one track: kept for a search in words, and used to carry a name across cameras."""
        from PIL import ImageOps
        embedder = self.app.embedder
        if embedder is None or not embedder.available():
            return
        vector = embedder.images([ImageOps.pad(crop, (384, 384), color=(0, 0, 0))])[0]
        with self.lock:
            cam = self.cameras.get(camera)
            track = cam.tracks.get(number) if cam else None
            if track is None:
                return
            track.look = vector
            if area >= track.look_area:
                track.look_area = area
                track.crop = f"crops/{number}.jpg"
                (self.folder / track.crop).write_bytes(_jpeg(crop, CROP_SIDE))
            self._new_day(t)
            if track.who and track.who.get("by") == "face" and (track.who.get("sure") or 0) >= people.MIN_SURE:
                self.day_looks.setdefault(track.who["id"], []).append(
                    {"v": vector, "camera": camera, "sure": track.who["sure"], "t": t, "who": dict(track.who)})
                self.day_looks[track.who["id"]] = self.day_looks[track.who["id"]][-people.MAX_LOOKS:]
            elif track.who is None:
                self._carry(cam, track, vector, t)
            row = self._track_row(track)
        with self.db_lock:
            self._save_tracks([row])
            self.db.commit()

    def _new_day(self, t):
        day = datetime.fromtimestamp(t, self.zone).date()
        if day != self.day:
            self.day, self.day_looks = day, {}

    def _carry(self, cam, track, vector, t):
        """A track with no face takes the name of a person whose face was seen today and who wears the
        same clothes, when no other such person comes close: less sure than the face, and less again on
        another camera."""
        import numpy as np
        here = {tr.who["id"] for tr in cam.tracks.values() if tr.who and tr is not track and t - tr.last <= 2}
        scores = []
        for key, items in self.day_looks.items():
            if key in here:
                continue   # that person is in view on another track
            recent = [item for item in items if t - item["t"] < people.LOOK_HOURS * 3600]
            if recent:
                likeness = np.array([item["v"] for item in recent], np.float32) @ vector
                best = int(likeness.argmax())
                scores.append((float(likeness[best]), key, recent[best]))
        scores.sort(key=lambda s: s[0], reverse=True)
        if not scores or scores[0][0] < people.LOOK_MATCH:
            return
        if len(scores) > 1 and scores[0][0] - scores[1][0] < people.LOOK_MARGIN:
            return
        likeness, key, item = scores[0]
        factor = people.LOOK_SAME_CAMERA if item["camera"] == cam.name else people.LOOK_OTHER_CAMERA
        track.who = dict(item["who"], by="look", score=round(likeness, 3), sure=round(item["sure"] * factor, 3))
        self._arrived(cam, track, t, None)

    # ----- tables -----
    def _learning(self, cam, t):
        """True while a camera that stays still still votes for its tables."""
        if cam.tables or t < cam.table_retry:
            return False
        if self.app.camera_kind(cam.name) not in ("fixed", "") and self.app.cameras:
            return False
        saved = self.places.get(cam.name)
        boxes = (saved or {}).get("boxes") or self._rule_tables(cam.name)
        if boxes:
            self._set_tables(cam, boxes, (saved or {}).get("names") or {})
            return False
        return True

    def _rule_tables(self, name):
        """Tables that a rule's ctx.places already found with SAM in this camera, if any."""
        for rule in list(self.app.entries.values()):
            cache = getattr(rule, "places", None) or {}
            for key, value in cache.items():
                camera, _, word = key.partition("|")
                if camera == name and detect.label_for(word) == TABLE and value.get("boxes"):
                    return sorted([list(b) for b in value["boxes"]],
                                  key=lambda b: (round((b[1] + b[3]) * 2), (b[0] + b[2]) / 2))
        return None

    def _learn_tables(self, cam, boxes, t):
        cam.table_looks += 1
        cam.table_votes += [list(b) for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= TABLE_MIN_AREA]
        if cam.table_looks < TABLE_LOOKS:
            return
        tables = group_tables(cam.table_votes, cam.table_looks)
        cam.table_votes, cam.table_looks = [], 0
        if not tables:
            cam.table_retry = t + TABLE_RETRY_S
            return
        self.places[cam.name] = {"boxes": [_round(b) for b in tables], "names": {}, "t": t}
        self._set_tables(cam, tables, {})
        self._save_places()

    def _set_tables(self, cam, boxes, names):
        slug = _slug(cam.name)
        cam.tables = []
        for index, box in enumerate(boxes):
            place_id = f"{slug}-t{index + 1}"
            cam.tables.append(Table(place_id, names.get(place_id) or f"T{index + 1}", box))

    def rename_place(self, place_id, name):
        """A new name for a table, such as "Window 2". Returns the place, or None if no table has this id."""
        name = " ".join(str(name or "").split())[:30]
        with self.lock:
            for cam in self.cameras.values():
                for table in cam.tables:
                    if table.id == place_id:
                        if not name:
                            raise ValueError("A table needs a name.")
                        for track in cam.tracks.values():
                            for field in ("place", "seated_at", "at_table"):
                                if getattr(track, field) == table.name:
                                    setattr(track, field, name)
                        table.name = name
                        self.places.setdefault(cam.name, {"boxes": [_round(tb.box) for tb in cam.tables]})
                        self.places[cam.name].setdefault("names", {})[place_id] = name
                        self._save_places()
                        return self._place(table, cam.name, self.clock())
        return None

    def _tables(self, cam, t, picture):
        """Each table's guests, visit, and state from this look."""
        for table in cam.tables:
            guests = [tr for tr in cam.tracks.values() if tr.label == "person" and tr.last == t and tr.seated_at == table.name
                      and not self._staff(tr, t)]
            table.count = len(guests)
            if guests:
                if table.visit_start is None or t - table.visit_seen > VISIT_GAP_S:
                    table.visit_start = min(tr.seated_since or t for tr in guests)
                    table.staff_t = None
                    named = next((self._name(tr) for tr in guests if self._name(tr)), None)
                    self._event(cam.name, "seated", f"{table.name} seated", named, picture, table.box, t)
                table.visit_seen = t
            elif table.visit_start is not None and t - table.visit_seen >= VISIT_GAP_S:
                minutes = max(1, round((table.visit_seen - table.visit_start) / 60))
                self._event(cam.name, "left", f"{table.name} left after {minutes} min", None, picture, table.box, t)
                table.visit_start = None
            hand = any(tr.hand_since is not None and (tr.seated_at == table.name or tr.place == table.name)
                       for tr in cam.tracks.values() if tr.last == t)
            since = t - table.visit_start if table.visit_start is not None else None
            served = table.staff_t is not None and table.visit_start is not None and table.staff_t >= table.visit_start
            if hand:
                table.state, table.note = "help", "hand up"
            elif since is not None and since >= WAIT_S and not served:
                table.state, table.note = "waiting", f"no staff {int(since // 60)} min"
            elif since is not None:
                table.state, table.note = "busy", ""
            else:
                table.state, table.note = "free", ""

    def _staff(self, track, t):
        """Tagged staff, or a standing person who came to a table and is not one of its guests."""
        return bool(track.who and track.who.get("role") == "staff") or (t - track.helper_t < 60 and not track.seated_at)

    # ----- what the page reads -----
    def _name(self, track):
        if not track.who:
            return None
        return track.who.get("visitor") or track.who.get("name")

    def _person(self, track, t):
        who = track.who or {}
        by = who.get("by")
        if by == "face" and t - track.who_face_t > 5:
            by = "track"   # the face was seen earlier on this track, not in this look
        return {"id": track.id, "box": list(track.box),
                "name": None if who.get("visitor") else who.get("name"), "person_id": who.get("person_id"),
                "role": who.get("role") or "", "visitor": who.get("visitor"), "visits": who.get("visits"),
                "sure": who.get("sure"), "by": by, "first": track.first, "place": track.place,
                "seated_since": track.seated_since if track.seated_at else None,
                "hand_since": track.hand_since, "told": track.told if track.told and t - track.told_t < 600 else None,
                "trail": [list(p) for p in track.trail], "staff": self._staff(track, t)}

    def _place(self, table, camera, t):
        since = t - table.visit_start if table.visit_start is not None else None
        return {"id": table.id, "name": table.name, "box": _round(table.box), "kind": "table", "count": table.count,
                "since_s": round(since) if since is not None else None, "state": table.state, "note": table.note,
                "camera": camera}

    def _snapshot(self, cam, t):
        now_tracks = [tr for tr in cam.tracks.values() if tr.last == t]
        return {"t": t, "size": list(cam.size),
                "people": [self._person(tr, t) for tr in now_tracks if tr.label == "person"],
                "things": [{"id": tr.id, "label": tr.label, "box": list(tr.box), "first": t - tr.stayed}
                           for tr in now_tracks if tr.label != "person"],
                "places": [self._place(table, cam.name, t) for table in cam.tables]}

    def overlay(self, names=None, after=None):
        """Boxes, names, timers and tables of each camera, as its last look found them, with timers counted
        to now. Asking marks the cameras as watched, so they get two looks a second for a minute."""
        now = self.clock()
        known = self._names()
        names = [n for n in (names or known) if n in known]
        self.watch(names)
        cameras, everyone, staff, busy, tables, longest = {}, 0, 0, 0, 0, 0
        with self.lock:
            snapshots = {n: self.cameras[n].snapshot for n in names if n in self.cameras and self.cameras[n].snapshot}
            events = [e for e in self.events if now - e["t"] <= 3600][-EVENTS_SHOWN:]
        for name, snap in snapshots.items():
            # Timers run on to now while the camera's last look is fresh, so they rise smoothly.
            clock = now if now - snap["t"] < FRESH_S else snap["t"]
            out = []
            for p in snap["people"]:
                out.append({"id": p["id"], "box": p["box"], "name": p["name"], "person_id": p["person_id"],
                            "role": p["role"], "visitor": p["visitor"], "visits": p["visits"], "sure": p["sure"],
                            "by": p["by"], "stayed_s": round(max(0.0, clock - p["first"])), "place": p["place"],
                            "seated_s": round(clock - p["seated_since"]) if p["seated_since"] is not None else None,
                            "hand_up_s": round(clock - p["hand_since"]) if p["hand_since"] is not None else None,
                            "told": p["told"], "trail": p["trail"]})
                everyone += 1
                staff += 1 if p["staff"] else 0
            places = []
            for place in snap["places"]:
                since = place["since_s"] + round(clock - snap["t"]) if place["since_s"] is not None else None
                places.append({k: place[k] for k in ("id", "name", "box", "kind", "count", "state", "note")})
                places[-1]["since_s"] = since
                if place["state"] == "waiting" and since is not None:
                    places[-1]["note"] = f"no staff {since // 60} min"
                tables += 1
                if place["state"] != "free":
                    busy += 1
                    longest = max(longest, since or 0)
            cameras[name] = {"t": snap["t"], "size": snap["size"], "people": out,
                             "things": [{"id": x["id"], "label": x["label"], "box": x["box"],
                                         "stayed_s": round(max(0.0, clock - x["first"]))} for x in snap["things"]],
                             "places": places}
        totals = {"people": everyone, "guests": everyone - staff, "staff": staff, "busy": busy, "tables": tables,
                  "longest_s": longest, "spend_hour": self.spend_hour()}
        return {"t": now, "cameras": cameras, "totals": totals,
                "events": [{k: e[k] for k in ("t", "camera", "kind", "text", "person")} for e in events]}

    def spend_hour(self):
        """What the paid models cost in the last hour, from samples of the day's spend."""
        now, spent = self.clock(), self.app.spend()
        older = [value for at, value in self.spend_marks if now - at <= 3600]
        return round(max(0.0, spent - older[0]), 4) if older else 0.0

    def status(self):
        now = self.clock()
        with self.lock:
            recent = [ms for at, ms in self.done if now - at <= 10]
            return {"cameras": sum(1 for cam in self.cameras.values() if now - cam.t < 30),
                    "per_s": round(len(recent) / 10, 1),
                    "ms": round(1000 * sum(recent) / len(recent)) if recent else None,
                    "running": self.running, "errors": list(self.errors)[-3:]}

    def floor(self):
        now = self.clock()
        with self.lock:
            return {"places": [self._place(table, cam.name, now) for cam in self.cameras.values() for table in cam.tables]}

    # ----- events -----
    def _event(self, camera, kind, text, person=None, picture=None, box=None, t=None):
        """Keeps one event, with a small picture of where it happened."""
        t = self.clock() if t is None else t
        path = None
        if picture is not None:
            path = f"events/{int(t * 1000)}-{kind}.jpg"
            part = _crop(picture, box, 0.6) if box else picture
            try:
                (self.folder / path).write_bytes(_jpeg(part, EVENT_SIDE))
            except (OSError, ValueError):
                path = None
        event = {"t": round(t, 2), "camera": camera, "kind": kind, "text": str(text)[:120], "person": person,
                 "picture": path}
        self.events.append(event)
        with self.db_lock:
            self.db.execute("INSERT INTO events (t, camera, kind, text, person, picture) VALUES (?, ?, ?, ?, ?, ?)",
                            (event["t"], camera, kind, event["text"], person, path))
            self.db.commit()
        return event

    def note_alert(self, cameras, text, t=None):
        """A rule's alert, as one event of the camera it is about."""
        with self.lock:
            self._event((cameras or self._names() or [""])[0], "alert", text, None, None, None, t)

    def told(self, to, cameras, t=None):
        """An alert to named places, such as "Waiter", while a hand is up (or was, in the last TOLD_S) on
        these cameras: that person was told, and the page shows it."""
        t = self.clock() if t is None else t
        marked = []
        with self.lock:
            for name in cameras or self._names():
                cam = self.cameras.get(name)
                for track in (cam.tracks.values() if cam else []):
                    if track.hand_since is not None or (track.hand_end and t - track.hand_end <= TOLD_S):
                        track.told, track.told_t = str(to)[:40], t
                        where = track.seated_at or track.place
                        marked.append(track.id)
                        self._event(name, "told", f"{to} told" + (f" for {where}" if where else ""), self._name(track),
                                    None, None, t)
        return marked

    # ----- tracks for search, alerts and history -----
    def _track_row(self, track):
        who = track.who or {}
        return (track.camera, track.id, track.first, track.last, who.get("name") if not who.get("visitor") else None,
                who.get("person_id"), who.get("role") or "", who.get("sure"), who.get("by"), who.get("visitor"),
                None if track.look is None else track.look.astype("float16").tobytes(), track.crop,
                getattr(track, "face_file", None))

    def _save_tracks(self, rows):
        if rows:
            self.db.executemany("INSERT OR REPLACE INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def history(self, camera, start, end, max_rows=7200):
        """The analysed frames of one camera between two times, at most about two a second, with each
        track's name filled in, also for the frames before its face was seen."""
        with self.db_lock:
            rows = self.db.execute("SELECT t, data FROM frames WHERE camera = ? AND t >= ? AND t <= ? ORDER BY t",
                                   (camera, float(start), float(end))).fetchall()
            names = {track: (name or visitor, sure, role) for track, name, visitor, sure, role in self.db.execute(
                "SELECT track, name, visitor, sure, role FROM tracks WHERE camera = ? AND last >= ? AND first <= ?"
                " AND (name IS NOT NULL OR visitor IS NOT NULL)", (camera, float(start), float(end)))}
        with self.lock:
            cam = self.cameras.get(camera)
            size = list(cam.size) if cam else [0, 0]
            for track in (cam.tracks.values() if cam else []):
                if track.who:
                    names[track.id] = (self._name(track), track.who.get("sure"), track.who.get("role") or "")
        frames, last = [], -1e9
        for t, data in rows:
            if t - last < 0.45:
                continue
            last = t
            frame = json.loads(data)
            for p in frame["people"]:
                if not p.get("name") and p["id"] in names:
                    p["name"], p["sure"], p["role"] = names[p["id"]]
            frames.append(frame)
            if len(frames) >= max_rows:
                break
        return {"camera": camera, "size": size, "frames": frames}

    def search(self, words, hours=24, limit=20):
        """Tracks whose look is most like some words, such as "red shirt" or "child with a backpack"."""
        import numpy as np
        embedder = self.app.embedder
        if not embedder or not embedder.available():
            raise ValueError("The look model is not installed on this server.")
        text = embedder.text(str(words)[:200])
        with self.db_lock:
            rows = self.db.execute("SELECT camera, track, first, name, visitor, look, crop FROM tracks"
                                   " WHERE last >= ? AND look IS NOT NULL", (self.clock() - float(hours) * 3600,)).fetchall()
        if not rows:
            return []
        looks = np.stack([np.frombuffer(row[5], np.float16).astype(np.float32) for row in rows])
        scores = looks @ text
        order = np.argsort(-scores)[:limit]
        return [{"track": rows[i][1], "camera": rows[i][0], "t": rows[i][2], "picture": self.url(rows[i][6]),
                 "score": round(float(scores[i]), 3), "name": rows[i][3] or rows[i][4]} for i in order]

    # ----- heat -----
    def _heat(self, cam, t):
        """Seconds of presence at each foot point, in a grid, one grid a minute."""
        import numpy as np
        minute = int(t // 60)
        if cam.heat_minute is not None and minute != cam.heat_minute:
            self._flush_heat(cam)
        if cam.heat is None:
            cam.heat, cam.heat_minute = np.zeros((GRID_ROWS, GRID_COLUMNS), np.float32), minute
        step = min(MAX_HEAT_STEP_S, t - cam.heat_t) if cam.heat_t is not None and t > cam.heat_t else 0.0
        cam.heat_t = t
        for track in cam.tracks.values():
            if track.label == "person" and track.last == t and track.trail:
                x, y = track.trail[-1]
                cam.heat[min(GRID_ROWS - 1, max(0, int(y * GRID_ROWS))), min(GRID_COLUMNS - 1, max(0, int(x * GRID_COLUMNS)))] += step

    def _flush_heat(self, cam):
        grid, minute = cam.heat, cam.heat_minute
        cam.heat, cam.heat_minute = None, None
        if grid is None or not grid.any():
            return
        with self.db_lock:
            self.db.execute("INSERT OR REPLACE INTO heat VALUES (?, ?, ?)", (cam.name, minute, grid.tobytes()))
            self.db.commit()

    def heat(self, camera, hours=1.0):
        import numpy as np
        since = int((self.clock() - float(hours) * 3600) // 60)
        total = np.zeros((GRID_ROWS, GRID_COLUMNS), np.float32)
        with self.db_lock:
            for (grid,) in self.db.execute("SELECT grid FROM heat WHERE camera = ? AND minute >= ?", (camera, since)):
                total += np.frombuffer(grid, np.float32).reshape(GRID_ROWS, GRID_COLUMNS)
        with self.lock:
            cam = self.cameras.get(camera)
            if cam is not None and cam.heat is not None:
                total += cam.heat
        return {"camera": camera, "grid": [[round(float(v), 1) for v in row] for row in total],
                "max": round(float(total.max()), 1)}

    # ----- alerts -----
    def alert_people(self, cameras, t, around_s=5.0):
        """The named people and visitors that these cameras saw within a few seconds of an alert, with why
        Whenever thinks it is them, and 3 or 4 moments of the 2 minutes before it."""
        found, story = [], []
        with self.lock:
            names = cameras or self._names()
            tracks = [tr for name in names for tr in (self.cameras[name].tracks.values() if name in self.cameras else [])
                      if tr.who and tr.first - around_s <= t <= tr.last + around_s]
            best = {}
            for track in tracks:
                key = track.who["id"]
                if key not in best or (track.who.get("sure") or 0) > (best[key].who.get("sure") or 0):
                    best[key] = track
            for track in sorted(best.values(), key=lambda tr: -(tr.who.get("sure") or 0))[:4]:
                found.append(self._evidence(track, t))
            moments = [e for e in self.events if e["camera"] in names and t - 120 <= e["t"] <= t + 1 and e["kind"] != "alert"]
        chosen = sorted(moments, key=lambda e: (e["kind"] in ("back", "hand_up", "told", "seated"), e["t"]))[-4:]
        story = [{"t": e["t"], "text": e["text"], "picture": self.url(e["picture"])} for e in sorted(chosen, key=lambda e: e["t"])]
        if len(story) < 3:
            for person in found:
                told = any(item["text"].startswith(f"{person['name']} ") for item in story)   # it came in already
                if person.get("arrived") and not told and t - 120 <= person["arrived"]["t"] <= t:
                    story.append(person["arrived"])
            story = sorted(story, key=lambda s: s["t"])[-4:]
        for person in found:
            person.pop("arrived", None)
        return {"people": found, "story": story}

    def _evidence(self, track, t):
        who = track.who
        clock = lambda at: datetime.fromtimestamp(at, self.zone).strftime("%H:%M")  # noqa: E731
        evidence = []
        hits = track.hits.get(who["id"]) or []
        if track.faces and hits:
            face = max(track.faces, key=lambda f: f["q"])
            looks = len(hits)
            evidence.append({"kind": "face", "text": f"{looks} clear look{'s' if looks > 1 else ''}, {clock(face['t'])}, "
                                                     f"{round(face['size'])} px", "score": who.get("score")})
        if who.get("by") == "look":
            seen = self.day_looks.get(who["id"]) or []
            cameras = len({item["camera"] for item in seen} | {track.camera})
            since = min((item["t"] for item in seen), default=track.first)
            evidence.append({"kind": "clothes", "text": f"same clothes since {clock(since)}, {cameras} camera"
                                                        f"{'s' if cameras > 1 else ''}"})
        minutes = (min(t, track.last) - track.first) / 60
        evidence.append({"kind": "tracker", "text": f"followed for {max(1, round(minutes))} min" if minutes >= 0.5
                         else "followed for under a minute"})
        record = self.app.people.get(who["id"]) or {}
        tag_face = f"/people-file/{who['id']}/{record['picture']}" if record.get("picture") else None
        now_face = self.url(getattr(track, "face_file", None))
        name = who.get("visitor") or who.get("name")
        return {"person_id": who["id"], "name": name, "role": who.get("role") or "", "sure": who.get("sure"),
                "by": who.get("by"), "evidence": evidence, "tag_face": tag_face, "now_face": now_face,
                "sighting": track.sighting,
                "arrived": {"t": round(track.first, 2), "text": f"{name} arrived", "picture": now_face or self.url(track.crop)}}

    def alert_subject(self, cameras, t):
        """(camera, box) of the one person an alert is clearly about: the only raised hand, or else the only
        named person, on these cameras near time t. None when it is not clear."""
        with self.lock:
            names = cameras or self._names()
            near = [tr for name in names for tr in (self.cameras[name].tracks.values() if name in self.cameras else [])
                    if tr.label == "person" and abs(tr.last - t) <= 5]
        for pick in ([tr for tr in near if tr.hand_since is not None or (tr.hand_end and t - tr.hand_end <= TOLD_S)],
                     [tr for tr in near if tr.who and (tr.who.get("sure") or 0) >= 0.5]):
            if len(pick) == 1:
                return pick[0].camera, tuple(pick[0].box)
        return None

    # ----- keeping it small -----
    def prune(self):
        """Forgets frames and events after a day, and tracks with their pictures after two."""
        now = self.clock()
        with self.db_lock:
            self.db.execute("DELETE FROM frames WHERE t < ?", (now - FRAME_HOURS * 3600,))
            old = self.db.execute("SELECT crop, face FROM tracks WHERE last < ?", (now - TRACK_HOURS * 3600,)).fetchall()
            self.db.execute("DELETE FROM tracks WHERE last < ?", (now - TRACK_HOURS * 3600,))
            gone = self.db.execute("SELECT picture FROM events WHERE t < ?", (now - EVENT_HOURS * 3600,)).fetchall()
            self.db.execute("DELETE FROM events WHERE t < ?", (now - EVENT_HOURS * 3600,))
            self.db.execute("DELETE FROM heat WHERE minute < ?", (int(now // 60) - FRAME_HOURS * 60,))
            self.db.commit()
        for row in old + gone:
            for path in row:
                if path:
                    (self.folder / path).unlink(missing_ok=True)


def as_number(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default
