"""Runs inside each rule's folder, in the sandbox.

Tests use Context and replay(). The server starts this file as a worker, and it speaks
one JSON message per line. A rule can look at frames and ask models through ctx, but this
process holds no key and has no network. The server makes each call and sends the answer back.
"""

import base64
import importlib
import itertools
import json
import queue
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

STATE_FILE = Path("data/state.json")
TEST_FRAME = Path("data/test_frame.jpg")
TEST_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERX"
    "RTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2Nj"
    "Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAA"
    "AAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk"
    "M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKT"
    "lJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QA"
    "HwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdh"
    "cRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDj4rX2q3Fa+1XorX2q5Fa+1SpkUMSUYrX2q3Fa+1XorX2q5Fa+"
    "1aqZ7lDElaK19qtxWvtRRXCmfDUKki3Fa+1XIrX2oorVM9yhUkf/2Q=="
)


class ModelError(Exception):
    """A model call failed, or a limit stopped it."""


class Frame:
    """One camera frame. path is a JPEG in this folder, so any library can open it."""

    def __init__(self, name="test", t=0.0, motion=0.0, path=""):
        self.name = name
        self.t = t
        self.motion = motion
        self.path = path

    def __repr__(self):
        return f"Frame({self.name}, t={self.t})"


class Answer:
    """What a model said. confidence is the model's own estimate, from 0 to 1."""

    def __init__(self, text="", confidence=0.0, frame=None, model="spark"):
        self.text = text
        self.confidence = confidence
        self.frame = frame
        self.model = model

    def __repr__(self):
        return f"Answer({self.text!r}, confidence={self.confidence}, model={self.model})"


class Check:
    """One frame judged alone. seen is what the model really saw there."""

    def __init__(self, frame=None, passed=False, confidence=0.0, seen="", model="spark"):
        self.frame = frame
        self.passed = passed
        self.confidence = confidence
        self.seen = seen
        self.model = model

    def __repr__(self):
        return f"Check({self.frame}, passed={self.passed}, confidence={self.confidence})"


def _box(thing):
    """(x1, y1, x2, y2) from a box, a dict with a box, or a Found or a Place."""
    box = getattr(thing, "box", None)
    if box is None and isinstance(thing, dict):
        box = thing.get("box")
    box = thing if box is None else box
    x1, y1, x2, y2 = (float(v) for v in box)
    return x1, y1, x2, y2


def center(thing):
    """The middle of a box, as (x, y). Boxes run from 0 to 1 across a camera's picture."""
    x1, y1, x2, y2 = _box(thing)
    return (x1 + x2) / 2, (y1 + y2) / 2


def foot(thing):
    """The middle of a box's bottom edge, where a standing person or a parked car touches the ground."""
    x1, _, x2, y2 = _box(thing)
    return (x1 + x2) / 2, y2


def seat(thing):
    """A point three quarters down a box, as (x, y): where a sitting person sits, near the edge of the
    table or desk they sit at, whether the camera sees their legs or not. For a person who stands, it is
    the legs. Use it to put people at tables, desks, beds or counters."""
    x1, y1, x2, y2 = _box(thing)
    return (x1 + x2) / 2, y1 + 0.75 * (y2 - y1)


def inside(point, place):
    """True if a point (x, y) lies in a place. A box or a Found counts by its middle."""
    if not (isinstance(point, (tuple, list)) and len(point) == 2):
        point = center(point)
    x1, y1, x2, y2 = _box(place)
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def overlap(a, b):
    """How much two boxes cover each other, from 0 (apart) to 1 (the same box)."""
    ax1, ay1, ax2, ay2 = _box(a)
    bx1, by1, bx2, by2 = _box(b)
    common = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - common
    return common / union if union > 0 else 0.0


def share_inside(thing, place):
    """The share of a box's area that lies in a place, from 0 to 1."""
    x1, y1, x2, y2 = _box(thing)
    px1, py1, px2, py2 = _box(place)
    common = max(0.0, min(x2, px2) - max(x1, px1)) * max(0.0, min(y2, py2) - max(y1, py1))
    area = (x2 - x1) * (y2 - y1)
    return common / area if area > 0 else 0.0


def place_of(thing, places, point=center, near=0.0):
    """The name of the place that holds a thing's point, or None. places is a list of Place, or a dict of
    name to box. point is center, seat for people at tables, desks or counters, or foot for things that
    stand on a floor, such as people in a doorway or cars. When no place holds the point, the nearest
    place counts if the point is less than near times that place's size away from it: near=0.5 counts a
    person on a chair beside a table. Of several places that
    hold the point, the smallest wins. A Found only fits a Place of its own camera."""
    items = places.items() if isinstance(places, dict) else [(p.name, p) for p in places]
    x, y = point(thing)
    best = None
    for name, place in items:
        camera = getattr(place, "camera", None)
        if camera and getattr(thing, "camera", None) and camera != thing.camera:
            continue
        x1, y1, x2, y2 = _box(place)
        size = max(x2 - x1, y2 - y1, 1e-6)
        away = max(x1 - x, 0.0, x - x2, y1 - y, 0.0, y - y2) / size
        if away > near:
            continue
        rank = (away, (x2 - x1) * (y2 - y1))
        if best is None or rank < best[0]:
            best = (rank, name)
    return best[1] if best else None


def beside(place, side="below", size=1.0, name=None):
    """A Place next to another one, such as the floor in front of a counter where people queue. side is
    "below", "above", "left" or "right", and size is its depth as a share of the place's own height or
    width. It keeps the place's camera."""
    x1, y1, x2, y2 = _box(place)
    w, h = (x2 - x1) * size, (y2 - y1) * size
    box = {"below": (x1, y2, x2, y2 + h), "above": (x1, y1 - h, x2, y1),
           "left": (x1 - w, y1, x1, y2), "right": (x2, y1, x2 + w, y2)}[side]
    box = tuple(min(1.0, max(0.0, v)) for v in box)
    base = getattr(place, "name", "") or "place"
    return Place(name or f"{side} {base}", getattr(place, "camera", ""), box)


def edge(place, side="bottom"):
    """Two points along one edge of a place, "top", "bottom", "left" or "right", such as the threshold
    of a door, for a Line."""
    x1, y1, x2, y2 = _box(place)
    return {"top": ((x1, y1), (x2, y1)), "bottom": ((x1, y2), (x2, y2)),
            "left": ((x1, y1), (x1, y2)), "right": ((x2, y1), (x2, y2))}[side]


def _cross(p, q, a, b):
    """True if the step from p to q crosses the segment from a to b."""
    def turn(o, u, v):
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
    return turn(a, b, p) * turn(a, b, q) < 0 and turn(p, q, a) * turn(p, q, b) < 0


class Line:
    """A line in one camera's picture that things cross, such as a doorway or the edge of a pavement.
    a and b are its two ends and inside is any point on the side that counts as in, such as a point in
    the room, each (x, y) from 0 to 1."""

    def __init__(self, name="", camera="", a=(0.0, 0.5), b=(1.0, 0.5), inside=(0.5, 1.0)):
        self.name = name
        self.camera = camera
        self.a, self.b, self.inside = tuple(a), tuple(b), tuple(inside)

    def side(self, point):
        """1 on the inside, -1 on the outside, 0 on the line itself."""
        away = self.away(point)
        return 0 if away == 0 else (1 if away > 0 else -1)

    def away(self, point):
        """How far a point is from the line, in picture widths: more than 0 on the inside, less on the outside."""
        def turn(p):
            return (self.b[0] - self.a[0]) * (p[1] - self.a[1]) - (self.b[1] - self.a[1]) * (p[0] - self.a[0])
        length = ((self.b[0] - self.a[0]) ** 2 + (self.b[1] - self.a[1]) ** 2) ** 0.5 or 1e-9
        here = turn(point) / length
        return here if turn(self.inside) > 0 else -here

    def __repr__(self):
        return f"Line({self.name!r}, {self.camera}, {self.a} to {self.b})"


class Place:
    """A named place in one camera, such as "table 3". box is (x1, y1, x2, y2) from 0 to 1."""

    def __init__(self, name="", camera="", box=(0.0, 0.0, 1.0, 1.0)):
        self.name = name
        self.camera = camera
        self.box = tuple(float(v) for v in box)

    def __repr__(self):
        return f"Place({self.name!r}, {self.camera}, {self.box})"


class Found:
    """One thing that the local detector found in one camera's picture of a frame. box is (x1, y1, x2,
    y2) from 0 to 1 in that picture. id stays the same while the thing stays about where it was. stayed
    is how many seconds it has been in about this spot: a seated person or a parked car stays, and a
    passer-by does not. tall is the box's height over its width in pixels: a person who stands is about
    2.5 or more, and one who sits is about 2 or less, seen from a camera on a wall. place is the name of
    its place, when ctx.detect got places. pose, when ctx.detect got pose=True, is a dict with points
    (17 body points) and hand_up, lying and sitting, each True, False or None when the body hides it.
    direction is "in" or "out" for a Found that ctx.crossings returns."""

    def __init__(self, frame=None, camera="", label="", confidence=0.0, box=(0.0, 0.0, 0.0, 0.0), id=None, place=None,
                 stayed=0.0, tall=0.0, pose=None):
        self.frame = frame
        self.camera = camera
        self.label = label
        self.confidence = confidence
        self.box = tuple(float(v) for v in box)
        self.id = id
        self.place = place
        self.stayed = stayed
        self.tall = tall
        self.pose = pose
        self.direction = None

    def __repr__(self):
        return f"Found({self.label} {self.id} in {self.place or self.camera}, confidence={self.confidence})"


class Tracker:
    """Gives each found thing a number across looks. A thing keeps the number of the thing of its kind,
    in the same camera and seen in the last max_gap_s, whose box it covers most, if they cover each other
    at least min_overlap. Failing that, it keeps the number of the nearest such thing whose middle is less
    than max_step of the taller box's height away: a person who walks away from the camera shrinks, and
    at one or two looks a second the boxes hardly overlap. It suits people and cars at a few looks a
    second, not a runner between far looks."""

    STAY_OVERLAP = 0.5   # a thing that still covers half of the box where it stopped has stayed there

    def __init__(self, min_overlap=0.3, max_gap_s=30.0, max_step=0.5):
        self.min_overlap = min_overlap
        self.max_gap_s = max_gap_s
        self.max_step = max_step
        self.tracks = {}   # number: [camera, label, box, t, the box where it stopped, since when]
        self.newest = {}   # camera: the time of its newest look
        self.numbers = itertools.count(1)

    def _fit(self, item, box):
        """How well a new box fits an old one: from 1 to min_overlap by overlap, below that by nearness,
        and 0 when it does not fit."""
        cover = overlap(item.box, box)
        if cover >= self.min_overlap:
            return cover
        (x1, y1), (x2, y2) = center(item.box), center(box)
        tallest = max(item.box[3] - item.box[1], box[3] - box[1], 1e-6)
        step = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 / tallest
        return self.min_overlap * (1 - step / self.max_step) if step < self.max_step else 0.0

    def number(self, found, t):
        """Sets .id on each Found of one look at time t, in one camera."""
        if not found:
            return
        camera = found[0].camera
        older = t < self.newest.get(camera, t)
        open_tracks = {n: track for n, track in self.tracks.items()
                       if track[0] == camera and abs(t - track[3]) <= self.max_gap_s}
        pairs = sorted(((self._fit(item, track[2]), i, n) for i, item in enumerate(found)
                        for n, track in open_tracks.items() if track[1] == item.label), reverse=True)
        taken = set()
        for score, i, n in pairs:
            if score <= 0 or found[i].id is not None or n in taken:
                continue
            item, track = found[i], open_tracks[n]
            item.id = n
            taken.add(n)
            stopped, since = track[4], track[5]
            if overlap(item.box, stopped) < self.STAY_OVERLAP:
                stopped, since = item.box, t
            item.stayed = max(0.0, t - since)
            if not older:
                self.tracks[n] = [camera, item.label, item.box, t, stopped, since]
        if older:
            return  # a look back in time keeps the numbers it fits, and changes no track
        for item in found:
            if item.id is None:
                item.id = next(self.numbers)
                self.tracks[item.id] = [camera, item.label, item.box, t, item.box, t]
        self.newest[camera] = t
        for n in [n for n, track in self.tracks.items() if t - track[3] > self.max_gap_s]:
            del self.tracks[n]


class Event:
    """What fired: the frames that proved it, and what the sensor saw."""

    def __init__(self, sensor="", t=0.0, frames=None, proof=None, note="", facts=None, boxes=None, check=None):
        self.sensor = sensor
        self.t = t
        self.frames = frames or []
        self.proof = proof or (self.frames[-1] if self.frames else None)
        self.note = note
        self.facts = facts or []
        self.boxes = boxes or []
        self.check = check


class Context:
    """What a rule can use. In the worker, these calls reach the server.

    A test has no models, so it scripts them. Set ctx.answers to a list of (text, confidence),
    ctx.found to the boxes that find returns, ctx.shots to the pictures that gallery returns, and
    ctx.checks to a list of (passed, confidence) with one item for each frame that check judges.
    Set ctx.aim_finds to False to test a thing that ctx.aim cannot find. Set ctx.past to the
    journal rows that history returns.
    ctx.asked lists each (question, model), and ctx.armed holds each armed sensor with its seconds.
    Set ctx.detected to the Found items that detect returns, and ctx.place_list to the Places that places
    returns.
    ctx.pointed is what the person pointed at, from pointed.json, or None. A test can set it.
    """

    def __init__(self, state=None, folder=".", call=None):
        self.state = state if state is not None else {}
        self.folder = str(Path(folder).resolve())
        self.actions = []
        self._local = threading.local()
        self.answers = []
        self.found = []
        self.checks = []
        self.shots = []
        self.aim_finds = True
        self.past = []
        self.asked = []
        self.armed = {}
        self.detected = []
        self.place_list = []
        self.tracker = Tracker()
        self._steady = {}
        self._lines = {}
        self._call = call
        try:
            self.pointed = json.loads((Path(self.folder) / "pointed.json").read_text())
        except (OSError, ValueError):
            self.pointed = None

    @property
    def event(self):
        """Jobs run side by side, so each one keeps its own event."""
        return getattr(self._local, "event", None)

    @event.setter
    def event(self, value):
        self._local.event = value

    def _record(self, kind, **fields):
        self.actions.append({"do": kind, **{k: v for k, v in fields.items() if v is not None}})

    def _check(self):
        return self.event.check if self.event else None

    def _test_frames(self, count):
        path = Path(self.folder) / TEST_FRAME
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(TEST_JPEG)
        t = self.event.t if self.event else 0.0
        return [Frame(f"test{i}", t + i * 0.25, 0.0, str(TEST_FRAME)) for i in range(count)]

    def _list(self, frames):
        if frames is None:
            if self.event and self.event.frames:
                return self.event.frames
            return self.frames(count=1)
        return [frames] if isinstance(frames, Frame) else list(frames)

    def frames(self, before=2.0, after=0.0, count=8, at=None):
        """Frames around the event, or around now, in time order. after waits for later frames.
        at is a time to look around instead, such as the time of an event that on_tick still handles."""
        if not self._call:
            return self._test_frames(max(int(count), 1))
        t = float(at) if at is not None else (self.event.t if self.event else None)
        reply = self._call("frames", t=t, before=float(before), after=float(after), count=int(count))
        return [Frame(**item) for item in reply]

    def ask(self, question, frames=None, model="spark"):
        """A model looks at frames and answers in words. Returns an Answer."""
        frames = self._list(frames)
        if not self._call:
            self.asked.append((str(question), model))
            text, confidence = self.answers.pop(0) if self.answers else ("test answer", 0.9)
            return Answer(text, confidence, frames[0] if frames else None, model)
        reply = self._call("ask", question=str(question)[:1500], frames=[f.name for f in frames],
                           model=model, check=self._check())
        best = reply.get("best_frame", 0)
        frame = frames[best - 1] if 1 <= best <= len(frames) else (frames[0] if frames else None)
        return Answer(reply["answer"], reply["confidence"], frame, model)

    def check(self, claim, frames=None, model="spark", reference=None, place=None):
        """A model judges each frame alone against a claim. Returns Checks, the best first. place, a Place
        or a Found, makes the model see only a close-up of it in its camera, at full size."""
        frames = self._list(frames)
        if not self._call:
            self.asked.append((str(claim), model))
            found = []
            for frame in frames:
                passed, confidence = self.checks.pop(0) if self.checks else (True, 0.9)
                found.append(Check(frame, passed, confidence, "test view", model))
            return sorted(found, key=lambda c: (c.passed, c.confidence), reverse=True)
        # A reference is a Frame, or a picture in this folder such as ctx.pointed["reference"].
        reference = str(getattr(reference, "name", reference))[:200] if reference else None
        reply = self._call("check", claim=str(claim)[:1000], frames=[f.name for f in frames], model=model,
                           reference=reference, place=_place_arg(place), check=self._check())
        by_name = {f.name: f for f in frames}
        return [Check(by_name[item["name"]], item["passed"], item["confidence"], item["seen"], model)
                for item in reply]

    def find(self, thing, frame=None):
        """SAM finds a short noun phrase in one frame. Returns boxes, the most certain first."""
        frame = self._list(frame)[-1]
        if not self._call:
            return list(self.found)
        return self._call("find", thing=str(thing)[:60], frame=frame.name, check=self._check())

    def detect(self, things="person", frames=None, camera=None, places=None, min_confidence=0.35, region=None,
               near=0.5, pose=False):
        """The local detector finds common things, such as "person", "car" or "chair", in each camera of
        each frame. It runs on the server's CPU and costs nothing. Returns a list of Found, in time order.

        things is one word or a list. camera limits it to one camera. places, a list of Place, sets
        each Found's place: the place that holds its seat point, or the nearest one less than near times
        that place's size away. region, a box from 0 to 1, makes it look only there, at more pixels, which
        finds small far things better. Each Found gets an id that stays while the thing stays put. pose
        adds body points to each person, about 15 ms a person, also free.
        """
        frames = self._list(frames)
        words = [things] if isinstance(things, str) else list(things)
        if not self._call:
            found = [item if isinstance(item, Found) else Found(**item) for item in self.detected]
        else:
            by_name = {f.name: f for f in frames}
            reply = self._call("detect", things=[str(w)[:40] for w in words][:8], frames=[f.name for f in frames],
                               camera=str(camera) if camera else None, min_confidence=float(min_confidence),
                               region=list(_box(region)) if region else None, pose=bool(pose), check=self._check())
            found = [Found(by_name.get(item["frame"]), item["camera"], item["label"], item["confidence"], item["box"],
                           tall=item.get("tall", 0.0), pose=item.get("pose")) for item in reply]
        looks = {}
        for item in found:
            looks.setdefault((item.camera, item.frame.t if item.frame else 0.0), []).append(item)
        for (_, t), items in sorted(looks.items(), key=lambda pair: pair[0][1]):
            self.tracker.number(items, t)
        if places:
            for item in found:
                item.place = place_of(item, places, point=seat, near=near)
        return found

    def crossings(self, line, found, max_gap_s=3.0, margin=0.02):
        """The things in found that crossed a Line, each a Found with .direction "in" or "out", in time
        order. A thing crosses when the middle of its box's bottom edge goes from more than margin (a share
        of the picture's width) on one side to more than margin on the other, within max_gap_s, through
        the line between its ends. A person who sits or stands near the line does not count again and
        again. Pass ctx.detect results of frames about 0.25 s apart, such as
        ctx.detect("person", ctx.frames(before=5, count=20), camera=line.camera), because a person walks
        through a door in about a second. Frames that an earlier call already counted for this line are
        skipped, so calls can overlap and nothing counts twice."""
        seen = self._lines.setdefault(line.name or repr(line), {"until": float("-inf"), "last": {}})
        crossed, newest = [], seen["until"]
        for item in sorted(found, key=lambda f: f.frame.t if f.frame else 0.0):
            t = item.frame.t if item.frame else 0.0
            if t <= seen["until"] or item.id is None or (line.camera and item.camera != line.camera):
                continue
            newest = max(newest, t)
            point = foot(item)
            away = line.away(point)
            if abs(away) < margin:
                continue  # too near the line to say which side it is on
            here = 1 if away > 0 else -1
            before = seen["last"].get(item.id)   # (time, point, side) of its last clear look
            if before and before[2] != here and t - before[0] <= max_gap_s and _cross(before[1], point, line.a, line.b):
                item.direction = "in" if here > 0 else "out"
                crossed.append(item)
            seen["last"][item.id] = (t, point, here)
        seen["until"] = newest
        seen["last"] = {n: last for n, last in seen["last"].items() if newest - last[0] <= 30}
        return crossed

    def visit(self, key, present, t=None, gone_s=30.0):
        """Follows the visits at one place, such as guests at a table, a car in a parking space or a person
        at the host stand. present says whether someone or something is there now. A visit starts when
        it is present, and ends when it has been gone for gone_s, so a short gap, such as a guest hidden
        by a waiter, does not split it. Returns None, or a dict when a visit starts or ends:
        {"kind": "start", "key", "t", "empty_s"} with the seconds since the last visit ended, or None, or
        {"kind": "end", "key", "start", "end", "seconds"}. ctx.state keeps open visits across restarts."""
        if t is None:
            t = self.event.t if self.event else time.time()
        t = float(t)
        visits = self.state.setdefault("visits", {})
        now = visits.get(str(key)) or {"start": None, "seen": None, "ended": None}
        if present:
            if now["start"] is None:
                visits[str(key)] = {"start": t, "seen": t, "ended": now["ended"]}
                return {"kind": "start", "key": key, "t": t,
                        "empty_s": t - now["ended"] if now["ended"] is not None else None}
            now["seen"] = max(now["seen"], t)
            return None
        if now["start"] is not None and t - now["seen"] >= gone_s:
            visits[str(key)] = {"start": None, "seen": None, "ended": now["seen"]}
            return {"kind": "end", "key": key, "start": now["start"], "end": now["seen"],
                    "seconds": now["seen"] - now["start"]}
        return None

    def places(self, thing, camera=None):
        """Every such thing in the cameras that stay still, such as "table", each as a Place with a name
        such as "table 3", numbered by rows from the top left. SAM finds them once in each camera, which
        costs one small call, and your folder keeps them."""
        if not self._call:
            return [item if isinstance(item, Place) else Place(**item) for item in self.place_list]
        reply = self._call("places", thing=str(thing)[:40], camera=str(camera) if camera else None,
                           check=self._check())
        return [Place(item["name"], item["camera"], item["box"]) for item in reply]

    def steady(self, key, value, looks=3):
        """value once it has been the same in this many calls in a row with this key, or else the last
        such value, None at first. A count from one picture can flicker, as people pass each other."""
        seen = self._steady.get(key)
        if seen is None or seen["recent"].maxlen != max(1, int(looks)):
            seen = self._steady[key] = {"recent": deque(maxlen=max(1, int(looks))), "value": None}
        seen["recent"].append(value)
        if len(seen["recent"]) == seen["recent"].maxlen and all(v == value for v in seen["recent"]):
            seen["value"] = value
        return seen["value"]

    def since(self, key, true, t=None):
        """How many seconds a condition has been true without a break, such as a table that is taken and
        not served. 0 while it is false. ctx.state keeps each start, so a restart does not reset it."""
        if t is None:
            t = self.event.t if self.event else time.time()
        starts = self.state.setdefault("since", {})
        if not true:
            starts.pop(str(key), None)
            return 0.0
        return max(0.0, float(t) - starts.setdefault(str(key), float(t)))

    def gallery(self, thing, frames=None, count=6, caption=""):
        """Finds a thing across frames, and shows a row of zoomed pictures, the best first.

        A person can click each one to see its full frame. Returns the pictures, as dicts with
        name, t, camera, path and score, and [] if the thing is in no frame.
        """
        frames = self._list(frames)
        if not self._call:
            self._record("gallery", thing=str(thing)[:60], count=int(count), caption=str(caption)[:200])
            return list(self.shots)
        return self._call("gallery", thing=str(thing)[:60], frames=[f.name for f in frames], count=int(count),
                          caption=str(caption)[:200], check=self._check())

    def aim(self, camera, thing=None, x=None, y=None, frame=None):
        """Turns a moving lens to a thing, found in the fixed lens beside it, or to a point there.

        x and y run from 0 to 1 across the fixed lens's picture. Returns the point as a dict with x
        and y, or None if the thing is not in that picture.
        """
        thing = str(thing)[:60] if thing else None
        if self._call:
            return self._call("aim", camera=str(camera), thing=thing, x=x, y=y,
                              frame=frame.name if frame else None, check=self._check())
        self._record("aim", camera=str(camera), thing=thing, x=x, y=y)
        return {"x": 0.5, "y": 0.5} if self.aim_finds else None

    def move(self, camera, direction, seconds=1):
        """Turns a moving lens left, right, up or down, for this many seconds."""
        if self._call:
            self._call("move", camera=str(camera), direction=str(direction), seconds=float(seconds), check=self._check())
        else:
            self._record("move", camera=str(camera), direction=str(direction), seconds=float(seconds))

    def light(self, camera, on=True, seconds=20):
        """Turns a camera's light on or off. It turns off by itself after seconds, 60 at most."""
        if self._call:
            self._call("light", camera=str(camera), on=bool(on), seconds=float(seconds), check=self._check())
        else:
            self._record("light", camera=str(camera), on=bool(on), seconds=float(seconds))

    def arm(self, sensor, seconds=120):
        """Starts a sensor that waits for a later moment. It runs for this many seconds."""
        if self._call:
            # The first check also covers the frames since the event, because a rule can
            # spend many seconds on models before it decides to wait.
            since = self.event.t if self.event else None
            self._call("arm", sensor=str(sensor), seconds=float(seconds), since=since, check=self._check())
        else:
            self.armed[str(sensor)] = float(seconds)

    def disarm(self, sensor):
        """Stops an armed sensor, because its job is done."""
        if self._call:
            self._call("disarm", sensor=str(sensor))
        else:
            self.armed.pop(str(sensor), None)

    def draw(self, text, frame=None, target=None):
        """Freezes the view on a frame, outlines the object and puts text on it."""
        self._record("draw", text=str(text)[:80], frame=frame.name if frame else None,
                     target=str(target)[:60] if target else None, check=self._check())

    def image(self, picture, caption=""):
        """Shows a Frame, or any picture file in this folder, in the event list."""
        path = picture.path if isinstance(picture, Frame) else str(picture)
        self._record("image", path=path[:200], caption=str(caption)[:200], check=self._check())

    def act(self, name, picture=None, **inputs):
        """Runs one of the person's actions, such as "Phone". picture is a Frame or a picture in this
        folder. Returns a dict with ok, status and reply. A failed action raises ModelError."""
        path = picture.path if isinstance(picture, Frame) else (str(picture) if picture else None)
        if not self._call:
            self._record("act", name=str(name), inputs=dict(inputs), picture=path)
            return {"ok": True, "status": 200, "reply": ""}
        return self._call("act", name=str(name)[:40], inputs=inputs, picture=path, check=self._check())

    def record(self, name, **values):
        """Adds a measured fact to the journal, so a later report can count or compare it."""
        plain = (str, int, float, bool, type(None))
        self._record("record", name=str(name)[:60],
                     values={str(k): v if isinstance(v, plain) else str(v) for k, v in values.items()},
                     check=self._check())

    def history(self, kind=None, name=None, rule=None, since=None, until=None, limit=2000):
        """Reads the journal of all rules, oldest first. Each item is a dict with t, rule, kind, name,
        seconds, text and values."""
        if not self._call:
            return [dict(item) for item in self.past]
        return self._call("history", kind=kind, name=name, rule_name=rule, since=since, until=until, limit=int(limit))

    def notify(self, text, picture=None, at=None):
        """Shows an alert, and sends it to the person's phone if they have a phone action.

        An alert about an event shows that event's cameras and clip. From on_tick, pass at, the time
        of the event it is about. A later alert of the same event with no new picture stays on the page."""
        path = picture.path if isinstance(picture, Frame) else (str(picture) if picture else None)
        self._record("notify", text=str(text)[:300], picture=path, check=self._check(),
                     at=float(at) if at is not None else None)

    def say(self, text):
        self._record("say", text=str(text)[:300])

    def show(self, name, value):
        self._record("show", name=str(name)[:60], value=str(value)[:200])

    def log(self, text):
        self._record("log", text=str(text)[:300], check=self._check())


def _place_arg(place):
    if place is None:
        return None
    return {"camera": str(getattr(place, "camera", "") or (place.get("camera", "") if isinstance(place, dict) else "")),
            "box": list(_box(place))}


def call(module, name, *args):
    function = getattr(module, name, None)
    if function:
        function(*args)


def replay(module, steps, ctx=None):
    """Feeds test steps to a rule and returns its Context.

    Each step is ("on", sensor, t), ("off", sensor, t) or ("tick", t). An "on" step can add
    an Event as a fourth item. Otherwise it gets an Event with three test frames.
    """
    ctx = ctx or Context()
    started = {}
    events = {}
    for step in steps:
        if step[0] == "on":
            started[step[1]] = step[2]
            ctx.event = None
            ctx.event = step[3] if len(step) > 3 else Event(step[1], step[2], ctx._test_frames(3), note="test note")
            events[step[1]] = ctx.event
            call(module, "on_event", step[1], step[2], ctx)
        elif step[0] == "off":
            ctx.event = events.get(step[1])
            call(module, "on_end", step[1], step[2], step[2] - started.pop(step[1], step[2]), ctx)
        else:
            ctx.event = None
            call(module, "on_tick", step[1], ctx)
    return ctx


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except (OSError, TypeError, ValueError):
        pass


def make_event(message):
    detail = message.get("event") or {}
    frames = [Frame(**item) for item in detail.get("frames") or []]
    proof = next((f for f in frames if f.name == detail.get("proof")), None)
    return Event(message["sensor"], message["t"], frames, proof, detail.get("note", ""),
                 detail.get("facts"), detail.get("boxes"), detail.get("check"))


def main():
    out = sys.stdout
    # Anything a rule prints goes to the event list and cannot break the message stream.
    sys.stdout = sys.stderr
    writing = threading.Lock()

    def send(message):
        with writing:
            out.write(json.dumps(message) + "\n")
            out.flush()

    # Each event runs as its own job, so one person's job never makes another person's job wait.
    # Only the job that holds this turn runs rule code. It gives the turn away while the server
    # works, so code between two ctx calls runs alone and needs no locks.
    turn = threading.Lock()
    inbox = queue.Queue()
    replies = {}
    numbers = itertools.count(1)

    def flush():
        for action in ctx.actions:
            send(action)
        ctx.actions.clear()

    def call_server(block, /, **args):
        """Waits for the server. Other jobs and new events run in the meantime.

        block is positional only, because ctx.act and ctx.history send an argument named name.
        """
        flush()
        number = next(numbers)
        box = replies[number] = queue.Queue(maxsize=1)
        send({"do": "call", "id": number, "name": block, "args": args})
        turn.release()
        try:
            message = box.get()
        finally:
            turn.acquire()
        if message is None:
            raise ModelError("The server closed.")
        if "error" in message:
            raise ModelError(message["error"])
        return message["value"]

    def read():
        for line in sys.stdin:
            message = json.loads(line)
            if message.get("type") == "reply":
                box = replies.pop(message.get("id"), None)
                if box:
                    box.put(message)
            else:
                inbox.put(message)
        for box in list(replies.values()):
            box.put(None)
        inbox.put(None)

    sys.path.insert(0, ".")
    # This file runs as __main__. A rule that imports it must get these same classes,
    # or its "except ModelError" would not catch the error that this code raises.
    sys.modules["rule_runtime"] = sys.modules[__name__]
    STATE_FILE.parent.mkdir(exist_ok=True)
    ctx = Context(load_state(), call=call_server)
    try:
        module = importlib.import_module("rule")
    except Exception:
        send({"do": "error", "trace": traceback.format_exc()[-1500:]})
        return
    send({"do": "ready"})

    ticking = threading.Event()

    def job(message, event):
        """Runs with the turn, and gives it back at the end."""
        kind = message["type"]
        ctx.event = event
        try:
            if kind == "on":
                call(module, "on_event", message["sensor"], message["t"], ctx)
            elif kind == "off":
                call(module, "on_end", message["sensor"], message["t"], message["duration"], ctx)
            elif kind == "tick":
                call(module, "on_tick", message["t"], ctx)
        except Exception:
            send({"do": "error", "trace": traceback.format_exc()[-1500:]})
        finally:
            flush()
            save_state(ctx.state)
            send({"do": "done"})
            if kind == "tick":
                ticking.clear()
            turn.release()

    threading.Thread(target=read, daemon=True).start()
    events = {}
    while (message := inbox.get()) is not None:
        kind = message["type"]
        # A slow on_tick must not pile up.
        if kind == "tick" and ticking.is_set():
            send({"do": "done"})
            continue
        event = None
        if kind == "on":
            event = events[message["sensor"]] = make_event(message)
        elif kind == "off":
            event = events.get(message["sensor"])
        elif kind == "tick":
            ticking.set()
        # Taking the turn here starts the jobs in the order that their events came.
        turn.acquire()
        threading.Thread(target=job, args=(message, event), daemon=True).start()


if __name__ == "__main__":
    main()
