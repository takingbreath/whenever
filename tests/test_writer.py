import json
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
