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
import traceback
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
        self._call = call

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

    def frames(self, before=2.0, after=0.0, count=8):
        """Frames around the event, or around now, in time order. after waits for later frames."""
        if not self._call:
            return self._test_frames(max(int(count), 1))
        t = self.event.t if self.event else None
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

    def check(self, claim, frames=None, model="spark", reference=None):
        """A model judges each frame alone against a claim. Returns Checks, the best first."""
        frames = self._list(frames)
        if not self._call:
            self.asked.append((str(claim), model))
            found = []
            for frame in frames:
                passed, confidence = self.checks.pop(0) if self.checks else (True, 0.9)
                found.append(Check(frame, passed, confidence, "test view", model))
            return sorted(found, key=lambda c: (c.passed, c.confidence), reverse=True)
        reply = self._call("check", claim=str(claim)[:1000], frames=[f.name for f in frames], model=model,
                           reference=reference.name if reference else None, check=self._check())
        by_name = {f.name: f for f in frames}
        return [Check(by_name[item["name"]], item["passed"], item["confidence"], item["seen"], model)
                for item in reply]

    def find(self, thing, frame=None):
        """SAM finds a short noun phrase in one frame. Returns boxes, the most certain first."""
        frame = self._list(frame)[-1]
        if not self._call:
            return list(self.found)
        return self._call("find", thing=str(thing)[:60], frame=frame.name, check=self._check())

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
            self._call("arm", sensor=str(sensor), seconds=float(seconds), since=since)
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
                     values={str(k): v if isinstance(v, plain) else str(v) for k, v in values.items()})

    def history(self, kind=None, name=None, rule=None, since=None, until=None, limit=2000):
        """Reads the journal of all rules, oldest first. Each item is a dict with t, rule, kind, name,
        seconds, text and values."""
        if not self._call:
            return [dict(item) for item in self.past]
        return self._call("history", kind=kind, name=name, rule_name=rule, since=since, until=until, limit=int(limit))

    def notify(self, text, picture=None):
        """Shows an alert, and sends it to the person's phone if they have a phone action."""
        path = picture.path if isinstance(picture, Frame) else (str(picture) if picture else None)
        self._record("notify", text=str(text)[:300], picture=path)

    def say(self, text):
        self._record("say", text=str(text)[:300])

    def show(self, name, value):
        self._record("show", name=str(name)[:60], value=str(value)[:200])

    def log(self, text):
        self._record("log", text=str(text)[:300])


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
