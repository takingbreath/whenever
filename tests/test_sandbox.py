"""Starts a real sandbox and a real worker. No API calls."""

import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rule_runtime
import sandbox

FRAME = Path(__file__).resolve().parent.parent / "testdata" / "frames" / "mug_384.jpg"

RULE = """
import statistics

def on_event(sensor, t, ctx):
    ctx.state["count"] = ctx.state.get("count", 0) + 1
    ctx.show("count", ctx.state["count"])
    if ctx.state["count"] == 2:
        ctx.notify("two")

def on_tick(t, ctx):
    raise ValueError("boom")
"""


CALLING_RULE = """
from PIL import Image
from rule_runtime import ModelError

def on_event(sensor, t, ctx):
    ctx.log(ctx.event.note)
    frames = ctx.frames(before=1, after=2, count=2)
    answer = ctx.ask("What is it?", frames)
    if answer.confidence < 0.7:
        answer = ctx.ask("What is it?", frames, model="wonder")
    best = ctx.check("It is clear", frames, reference=ctx.event.proof)[0]
    ctx.log(f"{best.passed} {best.frame.name} {best.seen}")
    with Image.open(answer.frame.path) as picture:
        ctx.show("width", picture.size[0])
    ctx.draw(answer.text, frame=answer.frame, target="bag")
    try:
        ctx.ask("Again?")
    except ModelError as e:
        ctx.log(f"failed: {e}")
    ctx.image(answer.frame, "Seen")

def on_end(sensor, t, duration, ctx):
    ctx.log(f"ended {ctx.event.note}")
"""


class Worker:
    """Drives a real worker as the server does."""

    def __init__(self, test, folder):
        self.process = sandbox.start_worker(folder)
        test.addCleanup(self.process.kill)
        self.ready = json.loads(self.process.stdout.readline())

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def read(self):
        return json.loads(self.process.stdout.readline())


class Blocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = sandbox.create("test-" + uuid.uuid4().hex[:6])
        cls.addClassCleanup(shutil.rmtree, cls.folder)
        code, output = sandbox.install(cls.folder, ["pillow"])
        assert code == 0, output

    def test_a_rule_runs_a_whole_program_through_the_server(self):
        (self.folder / "rule.py").write_text(CALLING_RULE)
        (self.folder / "frames").mkdir(exist_ok=True)
        shutil.copy(FRAME, self.folder / "frames" / "0000001.jpg")
        frame = {"name": "0000001.jpg", "t": 1.0, "motion": 0.0, "path": "frames/0000001.jpg"}
        worker = Worker(self, self.folder)
        self.assertEqual(worker.ready["do"], "ready")
        worker.send({"type": "on", "sensor": "s", "t": 1.0, "event": {
            "check": "c9", "proof": "0000001.jpg", "frames": [frame], "note": "seen", "facts": [True]}})
        # While the rule waits, a new event arrives. It must wait and must not be lost.
        worker.send({"type": "off", "sensor": "s", "t": 2.0, "duration": 1.0})
        seen = []
        calls = []
        while True:
            message = worker.read()
            seen.append(message)
            if message["do"] == "call":
                calls.append((message["name"], message["args"].get("model")))
                if message["name"] == "frames":
                    worker.send({"type": "reply", "id": message["id"], "value": [frame]})
                elif len(calls) == 2:
                    worker.send({"type": "reply", "id": message["id"], "value":
                                 {"answer": "a phone", "confidence": 0.4, "best_frame": 1}})
                elif len(calls) == 3:
                    worker.send({"type": "reply", "id": message["id"], "value":
                                 {"answer": "a phone", "confidence": 0.95, "best_frame": 1}})
                elif message["name"] == "check":
                    self.assertEqual(message["args"]["reference"], "0000001.jpg")
                    worker.send({"type": "reply", "id": message["id"], "value": [
                        {"name": "0000001.jpg", "seen": "a sharp view", "passed": True, "confidence": 0.9}]})
                else:
                    worker.send({"type": "reply", "id": message["id"], "error": "too many calls"})
            if message["do"] == "done" and sum(m["do"] == "done" for m in seen) == 2:
                break
        self.assertEqual(calls[:3], [("frames", None), ("ask", "spark"), ("ask", "wonder")])
        draw = next(m for m in seen if m["do"] == "draw")
        self.assertEqual((draw["text"], draw["frame"], draw["target"], draw["check"]),
                         ("a phone", "0000001.jpg", "bag", "c9"))
        self.assertIn({"do": "show", "name": "width", "value": "384"}, seen)
        logs = [{k: v for k, v in item.items() if k != "check"} for item in seen if item.get("do") == "log"]
        self.assertIn({"do": "log", "text": "failed: too many calls"}, logs)
        self.assertIn({"do": "log", "text": "ended seen"}, logs)
        self.assertIn({"do": "log", "text": "True 0000001.jpg a sharp view"}, logs)
        self.assertIn("frames/0000001.jpg", [m.get("path") for m in seen])
        self.assertFalse([m for m in seen if m["do"] == "error"])


OUTPUT_RULE = """
def on_event(sensor, t, ctx):
    past = ctx.history(kind="end", name="seated", rule="Tables")
    result = ctx.act("Phone", text=f"{len(past)} tables", title="Report")
    ctx.record("report", tables=len(past), ok=result["ok"])
    ctx.notify("Sent")
"""


class OutputBlocks(unittest.TestCase):
    def test_actions_and_the_journal_pass_their_name_through_the_worker(self):
        folder = sandbox.create("test-" + uuid.uuid4().hex[:6])
        self.addCleanup(shutil.rmtree, folder)
        (folder / "rule.py").write_text(OUTPUT_RULE)
        worker = Worker(self, folder)
        worker.send({"type": "on", "sensor": "weekly", "t": 1.0, "event": {"note": "It is time."}})
        seen = []
        while not seen or seen[-1]["do"] != "done":
            message = worker.read()
            seen.append(message)
            if message["do"] == "call" and message["name"] == "history":
                worker.send({"type": "reply", "id": message["id"], "value": [{"t": 1.0}, {"t": 2.0}]})
            elif message["do"] == "call":
                worker.send({"type": "reply", "id": message["id"], "value": {"ok": True, "status": 200, "reply": ""}})
        calls = [(m["name"], m["args"]) for m in seen if m["do"] == "call"]
        self.assertEqual(calls[0], ("history", {"kind": "end", "name": "seated", "rule_name": "Tables",
                                                "since": None, "until": None, "limit": 2000}))
        self.assertEqual(calls[1][0], "act")
        self.assertEqual(calls[1][1]["name"], "Phone")
        self.assertEqual(calls[1][1]["inputs"], {"text": "2 tables", "title": "Report"})
        self.assertIn({"do": "record", "name": "report", "values": {"tables": 2, "ok": True}}, seen)
        self.assertIn({"do": "notify", "text": "Sent"}, seen)
        self.assertFalse([m for m in seen if m["do"] == "error"])


class CameraBlocks(unittest.TestCase):
    def test_a_test_records_each_camera_block_and_touches_no_camera(self):
        ctx = rule_runtime.Context()
        self.assertEqual(ctx.aim("Lens", "car"), {"x": 0.5, "y": 0.5})
        ctx.move("Lens", "left", 2)
        ctx.light("Lens", False)
        self.assertEqual(ctx.actions, [
            {"do": "aim", "camera": "Lens", "thing": "car"},
            {"do": "move", "camera": "Lens", "direction": "left", "seconds": 2.0},
            {"do": "light", "camera": "Lens", "on": False, "seconds": 20.0}])

    def test_a_test_can_say_that_the_thing_is_not_there(self):
        ctx = rule_runtime.Context()
        ctx.aim_finds = False
        self.assertIsNone(ctx.aim("Lens", "car"))


class CopiedRule(unittest.TestCase):
    def test_an_edited_rule_keeps_its_libraries_and_its_state(self):
        source = sandbox.create("test-" + uuid.uuid4().hex[:6])
        self.addCleanup(shutil.rmtree, source)
        code, output = sandbox.install(source, ["six"])
        self.assertEqual(code, 0, output)
        (source / "data" / "state.json").write_text('{"count": 3}')
        (source / "frames").mkdir()
        (source / "frames" / "0000001.jpg").write_bytes(b"x")
        folder = sandbox.copy_rule(source, "test-" + uuid.uuid4().hex[:6])
        self.addCleanup(shutil.rmtree, folder)
        code, output = sandbox.run(folder, 'python -c "import six, json; print(json.load(open(\'data/state.json\')))"')
        self.assertEqual(code, 0, output)
        self.assertIn("3", output)
        self.assertFalse((folder / "frames").exists())


class Sandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = sandbox.create("test-" + uuid.uuid4().hex[:6])
        cls.addClassCleanup(shutil.rmtree, cls.folder)

    def test_cannot_read_the_api_key(self):
        if not (Path(__file__).resolve().parent.parent / ".env").exists():
            self.skipTest("There is no .env to protect.")
        code, output = sandbox.run(self.folder, "cat ../../.env")
        self.assertNotEqual(code, 0)
        # macOS says "Operation not permitted". Landlock on Linux says "Permission denied".
        self.assertRegex(output, "Operation not permitted|Permission denied")

    def test_cannot_write_outside_its_folder(self):
        code, _ = sandbox.run(self.folder, "touch ../outside.txt")
        self.assertNotEqual(code, 0)

    def test_can_write_inside_its_folder(self):
        code, _ = sandbox.run(self.folder, "touch inside.txt")
        self.assertEqual(code, 0)

    def test_has_no_network(self):
        script = "import socket; socket.create_connection(('api.meta.ai', 443), 3)"
        code, _ = sandbox.run(self.folder, f'python -c "{script}"')
        self.assertNotEqual(code, 0)

    def test_worker_runs_code_keeps_state_and_survives_an_error(self):
        (self.folder / "rule.py").write_text(RULE)
        worker = sandbox.start_worker(self.folder)
        self.addCleanup(worker.kill)
        self.assertEqual(json.loads(worker.stdout.readline())["do"], "ready")
        seen = []
        for message in ({"type": "on", "sensor": "s", "t": 1.0}, {"type": "tick", "t": 2.0},
                        {"type": "on", "sensor": "s", "t": 3.0}):
            worker.stdin.write(json.dumps(message) + "\n")
            worker.stdin.flush()
            while True:
                reply = json.loads(worker.stdout.readline())
                seen.append(reply)
                if reply["do"] == "done":
                    break
        kinds = [m["do"] for m in seen]
        self.assertIn("error", kinds)
        self.assertIn({"do": "show", "name": "count", "value": "2"}, seen)
        self.assertIn({"do": "notify", "text": "two"}, seen)


class TestJobs(unittest.TestCase):
    """One event's job must never make another event's job wait."""

    def setUp(self):
        self.folder = sandbox.create("t" + uuid.uuid4().hex[:8])
        self.addCleanup(shutil.rmtree, self.folder)
        (self.folder / "rule.py").write_text(
            "def on_event(sensor, t, ctx):\n"
            "    answer = ctx.ask('Who?')\n"
            "    ctx.state[ctx.event.check] = answer.text\n"
            "    ctx.log(ctx.event.check + ' ' + answer.text)\n"
            "def on_tick(t, ctx):\n"
            "    ctx.ask('Anything?', ctx.frames(count=1))\n"
        )
        self.worker = sandbox.start_worker(self.folder)
        self.addCleanup(self.worker.kill)
        self.assertEqual(self.read()["do"], "ready")

    def read(self):
        return json.loads(self.worker.stdout.readline())

    def write(self, message):
        self.worker.stdin.write(json.dumps(message) + "\n")
        self.worker.stdin.flush()

    def event(self, check):
        frame = {"name": "1.jpg", "t": 1.0, "motion": 0.0, "path": ""}
        self.write({"type": "on", "sensor": "s", "t": 1.0, "event": {"check": check, "frames": [frame]}})
        return self.read()

    def test_a_second_event_starts_while_the_first_waits_for_a_model(self):
        first, second = self.event("a"), self.event("b")
        self.assertEqual((first["do"], second["do"]), ("call", "call"))
        self.assertEqual((first["args"]["check"], second["args"]["check"]), ("a", "b"))

    def test_each_job_keeps_its_own_event_and_can_finish_first(self):
        first, second = self.event("a"), self.event("b")
        self.write({"type": "reply", "id": second["id"], "value": {"answer": "two", "confidence": 1}})
        # Each line carries its own job's event, so the page puts it on that event's card.
        self.assertEqual(self.read(), {"do": "log", "text": "b two", "check": "b"})
        self.assertEqual(self.read()["do"], "done")
        self.write({"type": "reply", "id": first["id"], "value": {"answer": "one", "confidence": 1}})
        self.assertEqual(self.read(), {"do": "log", "text": "a one", "check": "a"})
        self.assertEqual(self.read()["do"], "done")
        self.assertEqual(json.loads((self.folder / "data/state.json").read_text()), {"a": "one", "b": "two"})

    def test_a_slow_tick_does_not_pile_up_or_stop_an_event(self):
        self.write({"type": "tick", "t": 1.0})
        self.assertEqual(self.read()["name"], "frames")
        self.write({"type": "tick", "t": 2.0})
        self.assertEqual(self.read(), {"do": "done"})
        self.assertEqual(self.event("a")["do"], "call")


class TestContext(unittest.TestCase):
    """Wonder tests its rules with these, so they must behave like the real blocks."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.ctx = rule_runtime.Context(folder=self.folder)

    def test_scripted_answers_come_in_order(self):
        self.ctx.answers = [("a phone", 0.4), ("a pen", 0.9)]
        self.assertEqual(self.ctx.ask("What?").confidence, 0.4)
        answer = self.ctx.ask("What?", model="wonder")
        self.assertEqual((answer.text, answer.model), ("a pen", "wonder"))
        self.assertEqual(self.ctx.asked, [("What?", "spark"), ("What?", "wonder")])

    def test_test_frames_are_real_pictures(self):
        frame = self.ctx.frames(count=2)[1]
        self.assertTrue((self.folder / frame.path).read_bytes().startswith(b"\xff\xd8"))

    def test_scripted_checks_come_back_best_first(self):
        self.ctx.checks = [(False, 0.9), (True, 0.8)]
        found = self.ctx.check("A clear face", self.ctx.frames(count=2))
        self.assertEqual([c.passed for c in found], [True, False])
        self.assertEqual(self.ctx.asked, [("A clear face", "spark")])

    def test_the_pointed_thing_and_its_close_up_reach_the_code(self):
        (self.folder / "pointed.json").write_text('{"noun": "gate", "reference": "pointed/clicked.jpg"}')
        ctx = rule_runtime.Context(folder=self.folder)
        self.assertEqual(ctx.pointed["noun"], "gate")
        self.assertIsNone(self.ctx.pointed)
        sent = []
        live = rule_runtime.Context(folder=self.folder, call=lambda block, **args: sent.append(args) or [])
        live.check("The same gate is open.", [rule_runtime.Frame("1.jpg")], reference=ctx.pointed["reference"])
        live.check("The same gate is open.", [rule_runtime.Frame("1.jpg")], reference=rule_runtime.Frame("2.jpg"))
        self.assertEqual([a["reference"] for a in sent], ["pointed/clicked.jpg", "2.jpg"])

    def test_a_test_can_see_what_was_armed(self):
        self.ctx.arm("face", 60)
        self.ctx.arm("door")
        self.ctx.disarm("door")
        self.assertEqual(self.ctx.armed, {"face": 60.0})

    def test_a_draw_records_only_what_was_given(self):
        self.ctx.draw("hello")
        self.assertEqual(self.ctx.actions, [{"do": "draw", "text": "hello"}])

    def test_replay_gives_each_event_its_frames(self):
        class Rule:
            @staticmethod
            def on_event(sensor, t, ctx):
                ctx.log(f"{ctx.event.sensor} {len(ctx.event.frames)}")

            @staticmethod
            def on_tick(t, ctx):
                ctx.log(f"tick {ctx.event}")
        ctx = rule_runtime.replay(Rule, [("on", "s", 1.0), ("tick", 2.0)], self.ctx)
        self.assertEqual([a["text"] for a in ctx.actions], ["s 3", "tick None"])


if __name__ == "__main__":
    unittest.main()
