"""A recorded-scene harness for the example rules. It calls no model and needs no network or key.

A live rule runs rule_runtime.Context with a call function: each ctx.detect, ctx.who, ctx.check or
ctx.frames goes to the server, and the server answers. This harness uses that same path,
Context(call=FakeServer(...)), so the rule runs the real Context code: the tracker, place_of, the
who track memory and the Check objects. Only the server is fake. It answers from a recorded scene:
which frames exist, what the free detector finds in each one, who each person is, and which frames
pass a picture check.

Run drives a rule the way the worker does (rule_runtime.main): an "on" sets ctx.event and calls
on_event, an "off" calls on_end with the same event, and a tick calls on_tick with no event, once a
second as the server does (server.py, TICK_S). Each action that the rule makes is stamped with the
time of the step that made it, so a test can check when an alert went out.
"""

import importlib.util
import json
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rule_runtime import Context, Event, Frame, ModelError  # noqa: E402

# On a real server each of these is a paid Muse Spark or SAM call.
PAID = ("ask", "check", "find", "gallery")
# SAM finds the places once for each camera and word, and the rule's folder keeps them.
ONCE = ("places",)
# These run on the server's CPU or read saved data, at no model cost.
FREE = ("frames", "detect", "who", "people", "tag", "history", "act", "arm", "disarm")

ASPECT = 16 / 9   # the cameras' picture shape, for a box's height over its width in pixels


def load_rule(folder):
    """Imports rule.py from an example folder under its own module name."""
    folder = Path(folder)
    name = "rule_" + folder.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, folder / "rule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plan(folder):
    """rule.json: the sentence, the sensors and the plan that Wonder submitted."""
    return json.loads((Path(folder) / "rule.json").read_text())


class Thing:
    """One recorded thing in one camera, such as a guest at a table or a car.

    It is in view from start until end. box is (x1, y1, x2, y2) from 0 to 1. hand_up is a list of
    (start, end) spans when its hand is raised. who is the tagged person it is, as ctx.who gives it,
    or None. sitting is what the body points say.
    """

    def __init__(self, camera, box, start, end, label="person", hand_up=(), who=None, sitting=None,
                 confidence=0.9):
        self.camera = camera
        self.box = tuple(float(v) for v in box)
        self.start = float(start)
        self.end = float(end)
        self.label = label
        self.hand_up = list(hand_up)
        self.who = who
        self.sitting = sitting
        self.confidence = confidence

    def visible(self, t):
        return self.start <= t < self.end

    def hand_is_up(self, t):
        return any(a <= t < b for a, b in self.hand_up)

    def tall(self):
        x1, y1, x2, y2 = self.box
        return round((y2 - y1) / max(x2 - x1, 1e-6) / ASPECT, 2)


class FakeServer:
    """Answers a rule's calls from a recorded scene, and keeps a list of every call.

    judge(t, claim) gives (passed, confidence, seen) for a ctx.check of the frame at time t. fail is a
    set of call names that raise ModelError, as a real call does when Meta does not answer.
    """

    def __init__(self, things=(), places=(), judge=None, fail=(), every=0.5):
        self.things = list(things)
        self.place_list = [dict(p) for p in places]
        self.judge = judge or (lambda t, claim: (True, 0.9, "recorded view"))
        self.fail = set(fail)
        self.every = every
        self.now = 0.0
        self.calls = []        # (time, name, model calls on a real server)
        self.times = {}        # frame name: time

    def frame(self, t):
        t = round(float(t), 2)
        name = f"frame-{t:012.2f}"
        self.times[name] = t
        return Frame(name, t, 10.0, f"frames/{name}.jpg")

    def event(self, sensor, t, count=3, every=None, proof=-1, end=None):
        """The Event that a sensor's check sends: count frames that end at end (default t), and the
        proof frame among them."""
        every = self.every if every is None else every
        end = t if end is None else end
        frames = [self.frame(end - (count - 1 - i) * every) for i in range(count)]
        return Event(sensor, t, frames, frames[proof], note="recorded scene")

    def paid(self, until=None):
        """The paid calls, as (time, name, model calls). A real server judges each frame of a ctx.check
        alone, so one check of 6 frames is 6 Spark calls (server.py, App._rule_check)."""
        return [c for c in self.calls if c[1] in PAID and (until is None or c[0] <= until)]

    def paid_count(self, until=None):
        return sum(c[2] for c in self.paid(until))

    def __call__(self, name, **args):
        units = len(args.get("frames") or []) if name == "check" else 1
        self.calls.append((self.now, name, units))
        if name in self.fail:
            raise ModelError(f"{name}: the recorded server did not answer")
        handler = getattr(self, "_" + name, None)
        if handler is None:
            raise ModelError(f"The recorded server has no answer for {name}")
        return handler(**args)

    def _time(self, frame_name):
        if frame_name not in self.times:
            raise ModelError(f"Frame {frame_name} is not available")
        return self.times[frame_name]

    def _frames(self, t=None, before=2.0, after=0.0, count=8):
        t = self.now if t is None else float(t)
        count = max(1, int(count))
        start, end = t - float(before), min(t + float(after), max(self.now, t))
        step = (end - start) / (count - 1) if count > 1 else 0.0
        times = [end] if count == 1 else [start + i * step for i in range(count)]
        return [vars(self.frame(x)) for x in times]

    def _detect(self, things=("person",), frames=(), camera=None, min_confidence=0.35, region=None,
                pose=False, check=None):
        found = []
        for name in frames:
            t = self._time(name)
            for thing in self.things:
                if not thing.visible(t) or thing.label not in things or (camera and thing.camera != camera):
                    continue
                if thing.confidence < min_confidence:
                    continue
                item = {"frame": name, "camera": thing.camera, "label": thing.label,
                        "confidence": thing.confidence, "box": list(thing.box), "tall": thing.tall()}
                if pose and thing.label == "person":
                    item["pose"] = {"points": [], "hand_up": thing.hand_is_up(t),
                                    "sitting": thing.sitting, "lying": False}
                found.append(item)
        return found

    def _places(self, thing, camera=None, check=None):
        return [p for p in self.place_list if not camera or p["camera"] == camera]

    def _who(self, items=(), look=True, check=None):
        answers = []
        for item in items:
            t = self._time(item.get("frame")) if item.get("frame") else self.now
            match = None
            for thing in self.things:
                if (thing.visible(t) and thing.camera == item.get("camera")
                        and all(abs(a - b) < 1e-6 for a, b in zip(thing.box, item.get("box") or []))):
                    match = thing
                    break
            if match is not None and match.who:
                answers.append({**match.who, "face": True, "face_size": 40})
            else:
                answers.append({"face": False, "face_size": 0})
        return answers

    def _check(self, claim, frames=(), model="spark", reference=None, place=None, check=None):
        found = []
        for name in frames:
            passed, confidence, seen = self.judge(self._time(name), claim)
            found.append({"name": name, "passed": bool(passed), "confidence": float(confidence), "seen": seen})
        found.sort(key=lambda item: (item["passed"], item["confidence"]), reverse=True)
        return found


class Run:
    """Feeds one rule a timeline of sensor events and ticks, like the rule's worker process."""

    def __init__(self, rule, server, state=None):
        self.rule = rule
        self.server = server
        self.ctx = Context(state=state, call=server)
        self.log = []          # (time, action)
        self.events = {}
        self.started = {}

    def _step(self, t, work):
        self.server.now = float(t)
        before = len(self.ctx.actions)
        work()
        self.log.extend((float(t), action) for action in self.ctx.actions[before:])

    def _call(self, name, *args):
        function = getattr(self.rule, name, None)
        if function:
            function(*args)

    def on(self, sensor, t, event=None):
        event = event or self.server.event(sensor, t)
        self.events[sensor] = event
        self.started[sensor] = t

        def work():
            self.ctx.event = event
            self._call("on_event", sensor, t, self.ctx)
        self._step(t, work)
        return event

    def off(self, sensor, t):
        duration = t - self.started.pop(sensor, t)

        def work():
            self.ctx.event = self.events.get(sensor)
            self._call("on_end", sensor, t, duration, self.ctx)
        self._step(t, work)

    def moment(self, sensor, t, event=None):
        """A moment sensor: on_event, then at once on_end with duration 0 (server.py, App._act)."""
        event = self.on(sensor, t, event)
        self.off(sensor, t)
        return event

    def tick(self, t):
        def work():
            self.ctx.event = None
            self._call("on_tick", t, self.ctx)
        self._step(t, work)

    def ticks(self, start, end, every=1.0):
        """Ticks from start to end, both included."""
        t = float(start)
        while t <= end + 1e-9:
            self.tick(t)
            t += every

    def actions(self, kind):
        return [(t, a) for t, a in self.log if a.get("do") == kind]

    def alerts(self, to=None):
        return [(t, a) for t, a in self.actions("notify") if to is None or a.get("to") == to]


def expect(condition, message):
    """An assert that python -O does not remove."""
    if not condition:
        raise AssertionError(message)


NOTES = []


def note(text):
    """A finding that is not a pass or a fail, such as how many paid calls came before an alert."""
    NOTES.append(text)


def run_cases(namespace):
    """Runs every function named case_* in definition order. Prints one line for each, then exits 1
    if any case failed."""
    cases = [(name, fn) for name, fn in namespace.items() if name.startswith("case_") and callable(fn)]
    failed = 0
    for name, fn in cases:
        del NOTES[:]
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as error:
            failed += 1
            print(f"FAIL {name}: {error}")
        except Exception:
            failed += 1
            print(f"FAIL {name}: the rule raised an error")
            print("     " + traceback.format_exc().strip().replace("\n", "\n     "))
        for text in NOTES:
            print(f"     NOTE {text}")
    print(f"{len(cases) - failed} of {len(cases)} cases passed")
    sys.exit(1 if failed else 0)
