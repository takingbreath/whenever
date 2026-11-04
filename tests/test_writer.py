import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import writer


class FakeClient:
    def __init__(self, facts, note="a hand on a cheek"):
        self.facts = facts
        self.note = note

    def check_facts(self, question, facts, frames, moment=False, view=None):
        return {"seen": all(self.facts), "facts": self.facts, "note": self.note}, 0.0


SENSOR = {"id": "slap", "target": "", "question": "Slap?", "pattern": "moment",
          "requires": ["A hand touches a cheek", "The head recoils"], "frames_per_check": 6}


class FakeReviewer:
    def __init__(self, faults):
        self.faults = faults
        self.body = None

    def _post(self, body, timeout=0, retries=0):
        self.body = body
        answer = json.dumps({"faults": self.faults})
        return json.dumps({"output": [{"type": "message", "content": [{"text": answer}]}]}).encode()


class RuleReview(unittest.TestCase):
    def test_a_fault_reaches_wonder_with_its_fix(self):
        client = FakeReviewer([{"fault": "It stops after 4 s.", "fix": "Arm a sensor."}])
        self.assertEqual(writer.review_rule(client, "words", [SENSOR], "code"),
                         ["It stops after 4 s. Fix: Arm a sensor."])

    def test_the_reviewer_reads_the_words_the_sensors_and_the_code(self):
        client = FakeReviewer([])
        writer.review_rule(client, "the words", [SENSOR], "the code")
        self.assertIn("the words", client.body["input"])
        self.assertIn("the code", client.body["input"])
        self.assertIn("Slap?", client.body["input"])

    def test_a_good_rule_has_no_faults(self):
        self.assertEqual(writer.review_rule(FakeReviewer([]), "words", [SENSOR], "code"), [])

    def test_an_edit_tells_wonder_what_changed(self):
        text = writer.describe_previous({"sentence": "old words", "sensors": [SENSOR]})
        self.assertIn("old words", text)
        self.assertIn("rule.py", text)


class CheckOnRecording(unittest.TestCase):
    def test_a_later_stage_need_not_appear_in_the_recording(self):
        later = {**SENSOR, "starts_off": True}
        self.assertEqual(writer.check_sensors(FakeClient([False, False]), [later], [b"a"] * 3, None), [])

    def test_a_failure_names_the_fact_and_what_spark_saw(self):
        problems = writer.check_sensors(FakeClient([True, False]), [SENSOR], [b"a"] * 3, None)
        self.assertEqual(len(problems), 1)
        self.assertIn("The head recoils", problems[0])
        self.assertNotIn("A hand touches a cheek;", problems[0])
        self.assertIn("a hand on a cheek", problems[0])

    def test_a_good_sensor_passes(self):
        self.assertEqual(writer.check_sensors(FakeClient([True, True]), [SENSOR], [b"a"] * 3, None), [])

    def test_a_yes_on_still_frames_is_a_problem(self):
        problems = writer.check_sensors(FakeClient([True, True]), [SENSOR], None, [b"a"])
        self.assertIn("said yes", problems[0])


class Review(unittest.TestCase):
    class Wonder:
        def __init__(self, verdicts):
            self.verdicts = verdicts

        def _post(self, body, timeout=0, retries=0):
            text = json.dumps({"facts": self.verdicts})
            return json.dumps({"output": [{"type": "message", "content": [{"text": text}]}]})

    def test_a_side_effect_is_rejected_with_its_reason(self):
        sensor = dict(SENSOR, requires=["A hand hits the face", "The eyes close"])
        verdicts = [{"essential": True, "reason": "the act"}, {"essential": False, "reason": "a reaction"}]
        rejected = writer.review_facts(self.Wonder(verdicts), "count slaps", sensor)
        self.assertEqual(rejected, ["The eyes close (a reaction)"])

    def test_essential_facts_pass(self):
        verdicts = [{"essential": True, "reason": "x"}, {"essential": True, "reason": "y"}]
        self.assertEqual(writer.review_facts(self.Wonder(verdicts), "count slaps", SENSOR), [])

    def test_a_target_sensor_has_nothing_to_review(self):
        sensor = {"id": "t", "target": "thumbs up", "requires": ["x"]}
        self.assertEqual(writer.review_facts(self.Wonder([]), "count thumbs", sensor), [])


class Measure(unittest.TestCase):
    def test_motion_bursts_are_measured(self):
        class Spark:
            def ask_text(self, prompt, frames):
                return "3 5", 0.0

        motions = [0, 0, 20, 30, 20, 0, 0, 0, 25, 0]
        text = writer.measure_example(Spark(), "a slap", [b"x"] * 10, motions, 0.1)
        self.assertIn("2 bursts", text)
        self.assertIn("[0.3, 0.1]", text)
        self.assertIn("0.30 s", text)



class PaidTurns(unittest.TestCase):
    def test_a_rule_that_wonder_never_finishes_still_counts_its_cost(self):
        class Chatty:
            def _post(self, body, timeout=0, retries=0):
                return json.dumps({"output": [{"type": "message", "content": [{"text": "thinking"}]}],
                                   "usage": {"input_tokens": 1000, "output_tokens": 100}}).encode()
        with self.assertRaises(writer.WriterError) as caught:
            writer.write_rule(Chatty(), Path("/nonexistent"), "Tell me when the gate opens.")
        self.assertGreater(caught.exception.cost, 0)


FINISH = {"name": "Slaps", "sensors": [SENSOR], "schedules": [], "plan": [], "unsupported": ""}
FIRST_CODE = "def on_event(ctx): ctx.notify('slap')\n"
FAULT = "It stops after 4 s, but the person can come back later. Fix: Arm a sensor with a deadline."


class ScriptedWonder:
    """Plays Wonder's turns. Each turn is one tool call, and the last turn repeats."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.posts = 0

    def _post(self, body, timeout=0, retries=0):
        self.posts += 1
        name, args = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        call = {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": f"c{self.posts}"}
        return json.dumps({"output": [call], "usage": {"input_tokens": 1000, "output_tokens": 100}}).encode()


class ScriptedReviews:
    """Stands in for both reviewers. faults(code) decides what the whole-rule review finds."""

    def __init__(self, faults=lambda code: [], facts=lambda sensor: []):
        self.faults = faults
        self.facts = facts
        self.codes = []
        self.sensors = []

    def review_facts(self, client, sentence, sensor):
        self.sensors.append(sensor)
        return self.facts(sensor)

    def review_rule(self, client, sentence, sensors, code):
        self.codes.append(code)
        return self.faults(code)


class ReviewGate(unittest.TestCase):
    """A rule goes live only when both reviews pass on its final sensors and code."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        (self.folder / "rule.py").write_text(FIRST_CODE)
        self.steps = []
        tests_pass = mock.patch.object(writer.sandbox, "run", return_value=(0, "OK"))
        tests_pass.start()
        self.addCleanup(tests_pass.stop)

    def use(self, reviews):
        for name in ("review_facts", "review_rule"):
            patch = mock.patch.object(writer, name, getattr(reviews, name))
            patch.start()
            self.addCleanup(patch.stop)
        return reviews

    def write(self, wonder):
        return writer.write_rule(wonder, self.folder, "Tell me when someone slaps.", progress=self.steps.append)

    def finish(self, review, client=None, example=None, sensors=(SENSOR,)):
        call = {"name": "finish", "arguments": json.dumps({**FINISH, "sensors": list(sensors)})}
        return writer.handle(call, self.folder, client, example, None, self.steps.append, None, "slaps", review)

    def test_a_rule_that_review_keeps_rejecting_never_goes_live(self):
        reviews = self.use(ScriptedReviews(faults=lambda code: [FAULT]))
        wonder = ScriptedWonder(("finish", FINISH))
        with self.assertRaises(writer.ReviewFailed) as caught:
            self.write(wonder)
        message = str(caught.exception)
        self.assertIn("not live", message)
        self.assertIn(FAULT, message)
        self.assertIn("try again", message)
        # The server catches WriterError, so the job ends as a failure and counts what it spent.
        self.assertIsInstance(caught.exception, writer.WriterError)
        self.assertGreater(caught.exception.cost, 0)
        # Wonder got its rounds of changes, then the job stopped, long before its turn limit.
        self.assertEqual(len(reviews.codes), writer.MAX_REVIEW_ROUNDS + 1)
        self.assertEqual(wonder.posts, writer.MAX_REVIEW_ROUNDS + 1)
        self.assertLess(wonder.posts, writer.MAX_TURNS)

    def test_facts_that_review_keeps_rejecting_never_go_live(self):
        self.use(ScriptedReviews(facts=lambda sensor: ["The head recoils (a reaction)"]))
        with self.assertRaises(writer.ReviewFailed) as caught:
            self.write(ScriptedWonder(("finish", FINISH)))
        self.assertIn("The head recoils (a reaction)", str(caught.exception))

    def test_the_last_round_is_still_reviewed(self):
        # The old code skipped both reviews once the rounds were used up, so the last finish went live.
        review = {"rounds": writer.MAX_REVIEW_ROUNDS}
        reviews = self.use(ScriptedReviews(faults=lambda code: [FAULT]))
        with self.assertRaises(writer.ReviewFailed):
            self.finish(review)
        self.assertEqual(len(reviews.codes), 1)

    def test_a_correction_that_passes_goes_live(self):
        reviews = self.use(ScriptedReviews(faults=lambda code: [] if "ctx.arm" in code else [FAULT]))
        fixed = "def on_event(ctx): ctx.arm('back', 600)\n"
        wonder = ScriptedWonder(("finish", FINISH), ("write_file", {"path": "rule.py", "content": fixed}),
                                ("finish", FINISH))
        fields, cost = self.write(wonder)
        self.assertEqual(fields["sensors"], [SENSOR])
        self.assertEqual(reviews.codes, [FIRST_CODE, fixed])
        self.assertGreater(cost, 0)

    def test_code_changed_after_an_approval_is_reviewed_again(self):
        reviews = self.use(ScriptedReviews(faults=lambda code: [FAULT] if "sleep" in code else []))
        review = {"rounds": 0}
        # The reviewers pass the rule, but the sensor misses the recording, so it is not live yet.
        output, done = self.finish(review, FakeClient([False, False]), [b"a"] * 3)
        self.assertIsNone(done)
        self.assertEqual(len(reviews.codes), 1)
        # Wonder then changes rule.py. The old approval does not cover the new code.
        (self.folder / "rule.py").write_text("import time\ndef on_event(ctx): time.sleep(4)\n")
        output, done = self.finish(review, FakeClient([True, True]), [b"a"] * 3)
        self.assertIsNone(done)
        self.assertEqual(len(reviews.codes), 2)
        self.assertIn(FAULT, output)

    def test_sensors_changed_after_an_approval_are_reviewed_again(self):
        reviews = self.use(ScriptedReviews())
        review = {"rounds": 0}
        self.finish(review, FakeClient([False, False]), [b"a"] * 3)
        changed = dict(SENSOR, requires=["A hand touches a cheek"])
        _, done = self.finish(review, FakeClient([True, True]), [b"a"] * 3, sensors=[changed])
        self.assertEqual(done["sensors"], [changed])
        self.assertEqual(reviews.sensors, [SENSOR, changed])
        self.assertEqual(len(reviews.codes), 2)

    def test_an_unchanged_rule_keeps_its_approval(self):
        # Spark can miss a recording once. The same sensors and code need no second paid review.
        reviews = self.use(ScriptedReviews())
        review = {"rounds": 0}
        self.finish(review, FakeClient([False, False]), [b"a"] * 3)
        _, done = self.finish(review, FakeClient([True, True]), [b"a"] * 3)
        self.assertEqual(done["sensors"], [SENSOR])
        self.assertEqual(len(reviews.codes), 1)


if __name__ == "__main__":
    unittest.main()
