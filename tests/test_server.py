import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import camera_control
import server
from meta_api import ApiError, Client, jpeg_size, parse_segments
from PIL import Image


class FakeClient:
    def __init__(self, boxes=0, answer=True, conf=0.9, fail=False):
        self.boxes = boxes
        self.answer = answer
        self.conf = conf
        self.fail = fail
        self.answers = []
        self.asked = []
        self.key = "test"
        self.matches = []
        self.marks = []
        self.found = []
        self.marked = []
        self.named = 0
        self.located = []
        self.place = None
        self.looked = []
        self.reply = "a phone"
        self.verified = []
        self.verdicts = {}
        self.part_seen = False
        self.reviews = []
        self.reviewed = []
        self.doers = []
        self.review_error = None
        self.same = (True, 0.95)
        self.compared = []

    def see(self, target, jpeg, lane="live"):
        return [{"x1": 0, "y1": 0, "x2": 1, "y2": 1, "w": 2, "h": 2, "c": self.conf}] * self.boxes

    def find(self, target, jpeg):
        self.found.append((target, jpeg))
        return self.matches

    def mark(self, jpeg, width, height, point=None, box=None):
        self.marked.append(point or box)
        return self.marks

    def name_object(self, jpeg, box, crop=None):
        self.named += 1
        self.name_pictures = (jpeg, crop)
        return getattr(self, "noun", "water bottle"), 0.001

    def same_object(self, noun, reference, camera, views):
        self.compared_views = [(name, count) for name, _, count in views]
        return getattr(self, "picks", {}), 0.002

    def locate(self, question, jpeg):
        self.located.append(question)
        return self.place, 0.001

    def verify(self, claim, jpeg, model="spark", reference=None):
        self.verified.append((claim, jpeg, model, reference))
        passed, confidence = self.verdicts.get(jpeg, (False, 0.9))
        return {"seen": f"saw {jpeg!r}", "passed": passed, "confidence": confidence}, 0.002

    def look(self, question, frames, model="spark"):
        self.looked.append((question, len(frames), model))
        return {"answer": self.reply, "confidence": 0.5, "best_frame": 1}, 0.01

    def check_facts(self, question, facts, frames, moment=False, view=None):
        if self.fail:
            raise ApiError("Meta API did not answer in time.")
        self.asked.append(frames)
        answer = self.answers.pop(0) if self.answers else self.answer
        return {"seen": answer, "best_frame": 1, "facts": [answer], "note": "test",
                "part_seen": self.part_seen and not answer}, 0.001

    def review(self, question, facts, frames, ages):
        self.reviewed.append((question, facts, frames, ages))
        if self.review_error:
            raise self.review_error
        passed, confidence = self.reviews.pop(0) if self.reviews else (False, 0.9)
        doers = self.doers.pop(0) if self.doers else [{"who": "red shirt", "best_frame": 1, "last_frame": 1}]
        return {"frames": ["a line"] * len(frames), "clues_for": ["a lighter", "smoke"], "clues_against": [],
                "passed": passed, "confidence": confidence, "doers": doers if passed else []}, 0.006

    def same_subject(self, question, first, second):
        self.compared.append((first, second))
        same, confidence = self.same(first, second) if callable(self.same) else self.same
        return {"first": "a", "second": "b", "same": same, "confidence": confidence}, 0.002


def sensor(target="thumbs up", min_count=1):
    return server.Sensor({"id": "s", "target": target, "question": "Is it there?", "min_count": min_count})


class TempLogs(unittest.TestCase):
    def setUp(self):
        self.logs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.logs)

    def app(self, client):
        return server.App(client, self.logs, inline=True)


class Detect(TempLogs):
    def frame(self):
        return server.Frame(1.0, b"", "a.jpg", 0.0)

    def test_one_box_is_enough_by_default(self):
        hit, _, _ = self.app(FakeClient(boxes=1))._detect(sensor(), self.frame())
        self.assertTrue(hit)

    def test_two_needs_two_boxes(self):
        hit, _, _ = self.app(FakeClient(boxes=1))._detect(sensor(min_count=2), self.frame())
        self.assertFalse(hit)
        hit, _, _ = self.app(FakeClient(boxes=2))._detect(sensor(min_count=2), self.frame())
        self.assertTrue(hit)

    def test_strength_follows_confidence(self):
        _, strong, _ = self.app(FakeClient(boxes=1, conf=0.95))._detect(sensor(), self.frame())
        self.assertTrue(strong)
        _, strong, _ = self.app(FakeClient(boxes=1, conf=0.5))._detect(sensor(), self.frame())
        self.assertFalse(strong)


class FakeRule:
    """A rule with no worker. It records what the server sends."""

    def __init__(self, app, target="", **fields):
        self.id = "r"
        self.name = "fake"
        self.sentence = ""
        self.pointed = None
        self.folder = None
        self.paused = False
        self.lit = set()
        self.sensors = [server.Sensor({
            "id": "s", "target": target, "question": "Is it there?", "check_every_s": 1.0, **fields,
        })]
        self.sent = []
        self.send = self.sent.append
        app.entries[self.id] = self


class Review(TempLogs):
    def setUp(self):
        super().setUp()
        for name in ("MIN_REVIEW_SPAN_S", "MOMENT_MIN_REVIEW_SPAN_S"):
            patcher = mock.patch.object(server, name, 0.5)
            patcher.start()
            self.addCleanup(patcher.stop)

    def app_with(self, reviews=(), **fields):
        client = FakeClient(answer=False)
        client.part_seen = True
        client.reviews = list(reviews)
        app = self.app(client)
        return app, FakeRule(app, **fields)

    def feed(self, app, times):
        for t in times:
            app.frame(b"f%d" % int(t), float(t))

    def saved_reviews(self, app):
        return [c for c in app.evidence.checks.values() if "review" in c]

    def test_a_near_miss_that_the_review_passes_starts_an_event(self):
        app, rule = self.app_with([(True, 0.9)])
        self.feed(app, [1, 2])
        self.assertEqual(rule.sent[0]["type"], "on")
        event = rule.sent[0]["event"]
        self.assertEqual(event["note"], "red shirt; a lighter; smoke")
        check = app.evidence.check(event["check"])
        self.assertEqual((check["who"], check["proof_frame"]), ("red shirt", event["proof"]))
        self.assertTrue(check["review"]["passed"])

    def test_an_event_keeps_the_time_of_its_proof_and_not_of_the_review(self):
        app, rule = self.app_with([(True, 0.9)])
        app.client.doers = [[{"who": "x", "best_frame": 1}]]
        self.feed(app, [1, 2])
        self.assertEqual(rule.sent[0]["t"], 1.0)

    def test_the_page_hears_about_a_review_event(self):
        app, _ = self.app_with([(True, 0.9)])
        self.feed(app, [1, 2])
        self.assertEqual(app.results[-1]["event"], "on")

    def test_a_review_sees_time_labels_and_the_sensor_question(self):
        app, _ = self.app_with()
        self.feed(app, [1, 2, 3])
        question, facts, frames, ages = app.client.reviewed[0]
        self.assertEqual((question, facts), ("Is it there?", ["Is it there?"]))
        self.assertEqual(frames, [b"f1", b"f2"])
        self.assertEqual(ages, [1.0, 0.0])

    def test_a_review_with_low_confidence_is_no_event(self):
        app, rule = self.app_with([(True, server.REVIEW_PASS - 0.1)])
        self.feed(app, [1, 2])
        self.assertEqual(rule.sent, [])
        self.assertFalse(self.saved_reviews(app)[0]["review"]["passed"])

    def test_a_few_seconds_of_frames_cost_no_review(self):
        app, _ = self.app_with()
        server.MIN_REVIEW_SPAN_S = 5
        self.feed(app, [1, 2, 3, 4, 5])
        self.assertEqual(app.client.reviewed, [])
        self.feed(app, [6])
        self.assertEqual(len(app.client.reviewed), 1)

    def test_two_weak_yes_answers_in_a_row_start_an_event(self):
        app, rule = self.app_with([(True, 0.7), (True, 0.7)])
        self.feed(app, range(1, 8))
        self.assertEqual(len(app.client.reviewed), 1)
        self.assertEqual(rule.sent, [])
        self.feed(app, range(8, 10))
        self.assertEqual(rule.sent[0]["type"], "on")

    def test_a_sure_no_between_weak_yes_answers_starts_nothing(self):
        app, rule = self.app_with([(True, 0.7), (False, 0.9), (True, 0.7)])
        self.feed(app, range(1, 30))
        self.assertEqual(rule.sent, [])

    def test_a_very_weak_yes_does_not_count(self):
        app, rule = self.app_with([(True, server.REVIEW_WEAK - 0.1)] * 2)
        self.feed(app, range(1, 16))
        self.assertEqual(rule.sent, [])

    def test_an_unsure_answer_does_not_slow_the_reviews(self):
        app, _ = self.app_with([(False, 0.5), (False, 0.5), (False, 0.5)])
        self.feed(app, range(1, 16))
        self.assertEqual(len(app.client.reviewed), 3)

    def test_a_check_with_no_part_seen_costs_no_review(self):
        app, rule = self.app_with()
        app.client.part_seen = False
        self.feed(app, [1, 2, 3])
        self.assertEqual(app.client.reviewed, [])

    def test_a_single_yes_is_a_near_miss(self):
        app, rule = self.app_with([(True, 0.9)])
        app.client.part_seen = False
        app.client.answers = [False, True, False]
        self.feed(app, [1, 2, 3])
        self.assertEqual(rule.sent[0]["type"], "on")

    def test_a_failed_check_is_read_by_a_review(self):
        app, rule = self.app_with([(True, 0.9)])
        self.feed(app, [1])
        app.client.fail = True
        self.feed(app, [2])
        self.assertEqual(rule.sent[0]["type"], "on")

    def test_each_no_doubles_the_wait(self):
        app, _ = self.app_with()
        self.feed(app, range(1, 8))
        self.assertEqual(len(app.client.reviewed), 1)
        self.feed(app, [8])
        self.assertEqual(len(app.client.reviewed), 2)
        self.feed(app, range(9, 20))
        self.assertEqual(len(app.client.reviewed), 2)
        self.feed(app, [20])
        self.assertEqual(len(app.client.reviewed), 3)

    def test_the_wait_never_leaves_a_frame_unread(self):
        app, rule = self.app_with()
        sensor_ = rule.sensors[0]
        sensor_.review_wait = 1000
        self.feed(app, [1, 2])
        self.assertLessEqual(sensor_.review_wait, server.REVIEW_SPAN_S)

    def test_the_wait_resets_after_a_quiet_time(self):
        app, rule = self.app_with()
        sensor_ = rule.sensors[0]
        sensor_.review_wait = server.REVIEW_MAX_WAIT_S
        sensor_.review_after = 5.0
        self.feed(app, [2 + server.REVIEW_MAX_WAIT_S + 5])
        self.assertEqual(sensor_.review_wait, server.REVIEW_EVERY_S)

    def test_a_sleeping_sensor_gets_no_review(self):
        app, rule = self.app_with(starts_off=True)
        rule.sensors[0].arm(0.0, 100)
        self.feed(app, [1, 2, 3])
        self.assertEqual(app.client.reviewed, [])

    def test_a_failed_review_changes_nothing(self):
        for error in (ApiError("slow"), ValueError("empty")):
            app, rule = self.app_with()
            app.client.review_error = error
            self.feed(app, [1, 2, 3])
            self.assertEqual(rule.sent, [])
            self.assertFalse(rule.sensors[0].reviewing)

    def test_a_review_keeps_a_state_on_while_it_lasts(self):
        app, rule = self.app_with(clear_after_s=20.0)
        app.client.part_seen = False
        app.client.answers = [True, True]
        app.client.reviews = [(True, 0.9)] * 4
        app.client.doers = [[{"who": "x", "best_frame": 10, "last_frame": 10}]] * 4
        self.feed(app, range(1, 30))
        self.assertEqual([m["type"] for m in rule.sent], ["on"])
        self.assertGreaterEqual(len(app.client.reviewed), 3)

    def test_a_state_stays_on_to_the_newest_frame_that_shows_the_action(self):
        app, rule = self.app_with(clear_after_s=8.0)
        app.client.part_seen = False
        app.client.answers = [True, True]
        app.client.reviews = [(True, 0.9)]
        app.client.doers = [[{"who": "x", "best_frame": 1, "last_frame": 5}]]
        self.feed(app, range(1, 13))
        self.assertEqual([m["type"] for m in rule.sent], ["on"])

    def test_a_short_clear_time_gets_a_review_before_it_clears(self):
        self.assertLess(server.Sensor({"id": "s", "clear_after_s": 8.0}).watch_s, 8.0)
        self.assertEqual(server.Sensor({"id": "s", "clear_after_s": 60.0}).watch_s, server.WATCH_EVERY_S)

    def test_an_event_that_ends_during_a_review_does_not_fire_again(self):
        app, rule = self.app_with(clear_after_s=30.0)
        app.client.part_seen = False
        app.client.answers = [True, True]
        app.client.reviews = [(True, 0.9)]
        app.client.doers = [[{"who": "x", "best_frame": 1, "last_frame": 1}]]
        sensor_ = rule.sensors[0]

        def ended(first, second):
            with sensor_.lock:
                sensor_.state.on = False
                sensor_.subjects = []
                sensor_.review_from = 5.0
            return True, 0.95
        app.client.same = ended
        self.feed(app, range(1, 10))
        self.assertEqual([m["type"] for m in rule.sent], ["on"])

    def test_a_review_never_reads_an_occurrence_that_already_ended(self):
        app, rule = self.app_with(clear_after_s=2.0)
        app.client.part_seen = False
        app.client.answers = [True]
        rule.sensors[0].state.observe(0.5, True)  # the check before this one said yes
        self.feed(app, [1, 2, 3, 4])
        self.assertEqual([m["type"] for m in rule.sent], ["on", "off"])
        app.client.part_seen = True
        self.feed(app, [5, 6])
        self.assertEqual(app.client.reviewed[0][2], [b"f3", b"f4", b"f5"])

    def test_a_moment_reviews_a_short_span_once(self):
        app, rule = self.app_with([(True, 0.9)], pattern="moment", clear_after_s=30.0)
        for t in range(1, 12):
            app.frame(b"f%d" % t, float(t), motion=50.0)
        self.assertEqual([m["type"] for m in rule.sent], ["on", "off"])
        self.assertEqual(len(app.client.reviewed), 1)
        self.assertLessEqual(max(app.client.reviewed[0][3]), server.MOMENT_REVIEW_SPAN_S)


class NewSubject(TempLogs):
    """A strict yes starts the event on f1. Then a review reads frames f1 to f9 at t=9."""

    def run_state(self, same, doers=None, count=10):
        client = FakeClient(answer=False)
        client.answers = [True]
        client.reviews = [(True, 0.9)]
        client.doers = [doers or [{"who": "red polo", "best_frame": 5}]]
        client.same = same
        app = self.app(client)
        rule = FakeRule(app, clear_after_s=30.0)
        # A state starts on the second check in a row that says yes. This is the first one.
        rule.sensors[0].state.observe(0.5, True)
        for t in range(1, count):
            app.frame(b"f%d" % t, float(t))
        return app, rule

    def kinds(self, rule):
        return [m["type"] for m in rule.sent]

    def test_a_new_person_ends_the_event_and_starts_another(self):
        app, rule = self.run_state((False, 0.95))
        self.assertEqual(self.kinds(rule), ["on", "off", "on"])
        self.assertEqual(rule.sent[1]["duration"], 4.0)
        self.assertEqual(app.client.compared, [(b"f1", b"f5")])

    def test_the_new_event_carries_its_own_proof_and_note(self):
        app, rule = self.run_state((False, 0.95))
        event = rule.sent[2]["event"]
        self.assertEqual((rule.sent[2]["t"], event["note"]), (5.0, "red polo; a lighter; smoke"))
        self.assertEqual(app.evidence.read_frame(event["proof"]), b"f5")

    def test_two_people_in_one_review_get_one_event_each(self):
        doers = [{"who": "stripes", "best_frame": 1}, {"who": "red polo", "best_frame": 5}]
        app, rule = self.run_state(lambda first, second: (second == b"f1", 0.95), doers)
        self.assertEqual(self.kinds(rule), ["on", "off", "on"])

    def test_someone_who_comes_back_gets_no_new_event(self):
        doers = [{"who": "red polo", "best_frame": 5}]
        app, rule = self.run_state(lambda first, second: (first == second, 0.95), doers, 10)
        self.assertEqual(self.kinds(rule), ["on", "off", "on"])
        app.client.reviews = [(True, 0.9)]
        app.client.doers = [[{"who": "stripes", "best_frame": 1}]]
        sensor_ = rule.sensors[0]
        sensor_.review_from = 0.0
        sensor_.review_after = 0.0
        self.assertEqual(len(sensor_.subjects), 2)
        app.frame(b"f10", 10.0)
        self.assertEqual(self.kinds(rule), ["on", "off", "on"])

    def test_the_same_person_keeps_the_event(self):
        app, rule = self.run_state((True, 0.95))
        self.assertEqual(self.kinds(rule), ["on"])

    def test_an_unsure_answer_keeps_the_event(self):
        app, rule = self.run_state((False, server.SUBJECT_PASS - 0.1))
        self.assertEqual(self.kinds(rule), ["on"])

    def test_a_failed_comparison_keeps_the_event(self):
        def fail(first, second):
            raise ApiError("slow")
        app, rule = self.run_state(fail)
        self.assertEqual(self.kinds(rule), ["on"])

    def test_a_moment_is_not_compared(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        FakeRule(app, pattern="moment", clear_after_s=30.0)
        for t in (1, 20):
            app.frame(b"f%d" % t, float(t), motion=50.0)
        self.assertEqual(client.compared, [])


class Decoupled(TempLogs):
    """Nothing slow may hold up a check. These run with real threads."""

    def wait_for(self, test):
        for _ in range(300):
            if test():
                return True
            time.sleep(0.01)
        return False

    def live(self, client, gate):
        app = server.App(client, self.logs)

        def finish():
            gate.set()
            app.analysis.shutdown(wait=True)
            app.detection.shutdown(wait=True)
        self.addCleanup(finish)
        return app, FakeRule(app)

    def test_checks_go_on_while_a_review_waits_for_its_model(self):
        client = FakeClient(answer=False)
        client.part_seen = True
        gate = threading.Event()
        slow = client.review

        def review(*args):
            gate.wait(5)
            return slow(*args)
        client.review = review
        app, rule = self.live(client, gate)
        for t in range(1, 8):
            app.frame(b"f%d" % t, float(t))
            self.wait_for(lambda: rule.sensors[0].running == 0)
        self.assertTrue(rule.sensors[0].reviewing)
        self.assertEqual(len(client.asked), 7)

    def test_one_sensor_runs_one_review_at_a_time(self):
        client = FakeClient(answer=False)
        client.part_seen = True
        gate = threading.Event()
        calls = []

        def review(*args):
            calls.append(args)
            gate.wait(5)
            return {"frames": [], "clues_for": [], "clues_against": [], "passed": False, "confidence": 0.9,
                    "doers": []}, 0.0
        client.review = review
        app, rule = self.live(client, gate)
        rule.sensors[0].review_after = 0.0
        for t in range(1, 30):
            app.frame(b"f%d" % t, float(t))
            self.wait_for(lambda: rule.sensors[0].running == 0)
        self.assertEqual(len(calls), 1)

    def test_a_late_review_still_starts_its_event(self):
        client = FakeClient(answer=False)
        client.part_seen = True
        client.reviews = [(True, 0.9)]
        gate = threading.Event()
        slow = client.review

        def review(*args):
            gate.wait(5)
            return slow(*args)
        client.review = review
        app, rule = self.live(client, gate)
        for t in range(1, 8):
            app.frame(b"f%d" % t, float(t))
            self.wait_for(lambda: rule.sensors[0].running == 0)
        self.assertEqual(rule.sent, [])
        gate.set()
        self.assertTrue(self.wait_for(lambda: rule.sent))
        self.assertEqual(rule.sent[0]["type"], "on")

    def test_a_full_analysis_pool_leaves_checks_free(self):
        client = FakeClient(answer=False)
        gate = threading.Event()
        client.look = lambda *args: (gate.wait(5), ({"answer": "x", "confidence": 1, "best_frame": 1}, 0.0))[1]
        app, rule = self.live(client, gate)
        name = app.evidence.save_frame(0.5, b"x")
        replies = Replies()
        for i in range(server.ANALYSIS_THREADS + 5):
            app.start_call(replies, {"id": i, "name": "ask", "args": {"question": "q", "frames": [name]}})
        app.frame(b"f1", 1.0)
        self.assertTrue(self.wait_for(lambda: client.asked))


def picture(colour=(120, 120, 120), noisy=False):
    image = Image.new("RGB", (400, 300), colour)
    if noisy:
        pixels = image.load()
        for x in range(0, 400, 2):
            for y in range(0, 300, 2):
                pixels[x, y] = (255, 255, 255)
    out = BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


class Gallery(TempLogs):
    BOX = {"x1": 100, "y1": 100, "x2": 200, "y2": 200, "w": 400, "h": 300, "c": 0.9}

    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.app_ = self.app(self.client)
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.rule = Replies(self.folder)
        self.client.see = lambda thing, jpeg, lane="live": [dict(self.BOX)] if jpeg != self.empty else []
        self.empty = picture((1, 1, 1))

    def add(self, t, jpeg):
        name = self.app_.evidence.save_frame(t, jpeg)
        self.app_.timeline.append(server.Frame(t, None, name, 0.0))
        return name

    def show(self, names, **args):
        self.app_._rule_call(self.rule, {"id": 1, "name": "gallery", "args": {"thing": "face", "frames": names, **args}})
        return self.rule.replies[-1]

    def test_the_sharpest_views_come_first_and_files_exist(self):
        soft, crisp = self.add(1.0, picture()), self.add(2.0, picture(noisy=True))
        shots = self.show([soft, crisp])["value"]
        self.assertEqual([shot["name"] for shot in shots], [crisp, soft])
        self.assertTrue((self.folder / shots[0]["path"]).exists())

    def test_the_page_gets_each_zoom_with_its_full_frame_and_box(self):
        name = self.add(1.0, picture())
        self.show([name], caption="Who came in")
        item = [i for i in self.app_.feed if i["kind"] == "gallery"][0]
        self.assertIn("Who came in", item["text"])
        shot = item["pictures"][0]
        self.assertTrue(shot["frame"].endswith(name))
        self.assertEqual(shot["box"]["x2"], 200)

    def test_pictures_too_close_in_time_count_once(self):
        names = [self.add(1.0 + i * 0.1, picture()) for i in range(5)]
        self.assertEqual(len(self.show(names)["value"]), 1)

    def test_the_count_is_kept(self):
        names = [self.add(float(i), picture()) for i in range(8)]
        self.assertEqual(len(self.show(names, count=3)["value"]), 3)

    def test_a_thing_that_is_nowhere_gives_no_pictures(self):
        self.assertEqual(self.show([self.add(1.0, self.empty)])["value"], [])

    def test_a_full_camera_frame_is_used_before_the_wall(self):
        self.app_.cameras = ["Gate", "Patio"]
        name = self.add(1.0, self.empty)
        self.app_.evidence.save_full(name, 1, picture())
        shot = self.show([name])["value"][0]
        self.assertEqual(shot["camera"], "Patio")
        item = [i for i in self.app_.feed if i["kind"] == "gallery"][0]
        self.assertTrue(item["pictures"][0]["frame"].endswith("?camera=1"))

    def test_rechecks_at_their_limit_cannot_block_a_gallery(self):
        def full():
            raise ValueError("This rule made too many model calls.")
        self.rule.allow_call = full
        self.assertIn("value", self.show([self.add(1.0, picture())]))

    def test_a_rule_cannot_call_galleries_without_limit(self):
        name = self.add(1.0, picture())
        for _ in range(server.MAX_GALLERIES_PER_MINUTE):
            self.assertIn("value", self.show([name]))
        self.assertIn("more than", self.show([name])["error"])

    def test_a_rule_cannot_read_a_file_through_a_gallery(self):
        self.assertEqual(self.show(["../../.env"])["value"], [])

    def test_a_stopped_rule_gets_no_gallery(self):
        self.rule.paused = True
        self.assertIn("stopped", self.show([self.add(1.0, picture())])["error"])


class Storage(TempLogs):
    def test_the_oldest_pictures_go_first(self):
        app = self.app(FakeClient())
        with mock.patch("evidence.MAX_BYTES", 25):
            names = [app.evidence.save_frame(float(i), b"x" * 10) for i in range(5)]
        self.assertIsNone(app.evidence.read_frame(names[0]))
        self.assertEqual(app.evidence.read_frame(names[-1]), b"x" * 10)

    def test_a_full_frame_needs_a_real_frame(self):
        app = self.app(FakeClient())
        self.assertFalse(app.evidence.save_full("../../x.jpg", 0, b"j"))
        name = app.evidence.save_frame(1.0, b"f")
        self.assertTrue(app.evidence.save_full(name, 2, b"j"))
        self.assertEqual(app.evidence.full_path(name, 2).read_bytes(), b"j")
        self.assertIsNone(app.evidence.full_path(name, 3))

    def test_only_the_active_camera_can_save_a_full_frame(self):
        app = self.app(FakeClient())
        name = app.evidence.save_frame(1.0, b"f")
        self.assertFalse(app.frame_full(name, 0, b"j", "someone"))


class LiveCamera(TempLogs):
    PAGE = server.CAMERA_PAGE_PREFIX + "1"

    def app_with_camera(self):
        app = self.app(FakeClient())
        app.claim(self.PAGE)
        self.assertTrue(app.show_live(b"one", self.PAGE))
        return app

    def test_a_page_that_opens_only_watches_a_running_camera(self):
        app = self.app_with_camera()
        app.claim("browser")
        self.assertEqual(app.active_page, self.PAGE)
        self.assertTrue(app.frame(b"a", 1.0, "browser")["inactive"])

    def test_a_page_takes_the_camera_back_after_the_stream_ends(self):
        app = self.app_with_camera()
        app.live_at = time.time() - server.LIVE_LOST_S - 1
        self.assertFalse(app.camera_live())
        app.claim("browser")
        self.assertEqual(app.active_page, "browser")

    def test_a_security_camera_takes_over_from_a_page(self):
        app = self.app(FakeClient())
        app.claim("browser")
        app.claim(self.PAGE)
        self.assertEqual(app.active_page, self.PAGE)

    def test_only_the_active_camera_can_show_a_picture(self):
        app = self.app_with_camera()
        self.assertFalse(app.show_live(b"other", "someone"))
        self.assertEqual(app.read_live(0), (1, b"one"))

    def test_a_page_gets_only_a_newer_picture(self):
        app = self.app_with_camera()
        with mock.patch.object(server, "LIVE_WAIT_S", 0.01):
            self.assertIsNone(app.read_live(1))
        app.show_live(b"two", self.PAGE)
        self.assertEqual(app.read_live(1), (2, b"two"))

    def test_a_waiting_page_wakes_on_a_new_picture(self):
        app = self.app_with_camera()
        timer = threading.Timer(0.02, app.show_live, (b"two", self.PAGE))
        timer.start()
        self.addCleanup(timer.join)
        self.assertEqual(app.read_live(1), (2, b"two"))


class FakeControls(server.Controls):
    """Records each command in place of starting a process."""

    def __init__(self):
        super().__init__()
        self.commands = []

    def _run(self, *args):
        self.commands.append(args)

    def _start_light(self, serial, seconds):
        self.commands.append(("light", serial, "on", seconds))
        return mock.Mock(poll=lambda: None)


class CameraControls(TempLogs):
    LENS = {"name": "Lens", "serial": "ABC123", "moves": True, "light": True, "guide": "Wide"}

    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.app_ = self.app(self.client)
        self.app_.controls = self.controls = FakeControls()
        self.app_.claim("camera-1", ["Other", "Wide", "Lens"], [dict(self.LENS)], (2, 2))
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.rule = Replies(self.folder)

    def call(self, name, **args):
        self.app_._rule_call(self.rule, {"id": 1, "name": name, "args": args})
        return self.rule.replies[-1]

    def add_wall(self):
        out = BytesIO()
        Image.new("RGB", (400, 400)).save(out, "JPEG")
        name = self.app_.evidence.save_frame(1.0, out.getvalue())
        self.app_.timeline.append(server.Frame(1.0, None, name, 0.0))
        return name

    def test_a_rule_aims_at_a_thing_found_in_the_fixed_lens(self):
        self.add_wall()
        pictures = []

        def see(thing, jpeg, lane="live"):
            pictures.append(Image.open(BytesIO(jpeg)).size)
            return [{"x1": 10, "y1": 10, "x2": 30, "y2": 30, "w": 200, "h": 200, "c": 0.2},
                    {"x1": 40, "y1": 100, "x2": 80, "y2": 140, "w": 200, "h": 200, "c": 0.9}]
        self.client.see = see
        reply = self.call("aim", camera="Lens", thing="car")
        self.assertEqual(reply["value"], {"x": 0.3, "y": 0.6})
        self.assertEqual(self.controls.commands, [("aim", "ABC123", "0.300", "0.600")])
        self.assertEqual(pictures, [(200, 200)])

    def test_a_thing_that_is_not_there_turns_nothing(self):
        self.add_wall()
        self.client.see = lambda thing, jpeg, lane="live": []
        self.assertIsNone(self.call("aim", camera="Lens", thing="car")["value"])
        self.assertEqual(self.controls.commands, [])

    def test_a_rule_can_aim_at_a_point(self):
        self.assertEqual(self.call("aim", camera="Lens", x=0.25, y=1)["value"], {"x": 0.25, "y": 1})
        self.assertIn("error", self.call("aim", camera="Lens", x=2, y=0))
        self.assertEqual(len(self.controls.commands), 1)

    def test_only_a_camera_that_can_move_moves(self):
        self.assertIn("These can: Lens", self.call("move", camera="Wide", direction="left")["error"])
        self.assertIn("error", self.call("aim", camera="Nothing", x=0, y=0))
        self.assertEqual(self.controls.commands, [])

    def test_a_move_has_a_fixed_direction_and_a_short_time(self):
        self.assertIn("error", self.call("move", camera="Lens", direction="; rm -rf /"))
        self.call("move", camera="Lens", direction="left", seconds=500)
        self.assertEqual(self.controls.commands, [("move", "ABC123", "left", "4.0")])

    def test_a_light_always_has_an_end(self):
        self.call("light", camera="Lens", seconds=9000)
        self.assertEqual(self.controls.commands, [("light", "ABC123", "on", camera_control.MAX_LIGHT_S)])
        self.assertTrue(self.controls.public()[0]["light_on"])

    def test_a_new_light_command_replaces_the_old_time(self):
        self.call("light", camera="Lens", seconds=30)
        old = self.controls.lights["ABC123"]
        self.call("light", camera="Lens", on=False)
        old.kill.assert_called_once()
        self.assertEqual(self.controls.commands[-1], ("light", "ABC123", "off"))
        self.assertEqual(self.rule.lit, set())

    def test_stopping_a_rule_turns_off_its_light(self):
        rule = FakeRule(self.app_)
        rule.send = lambda message: None
        rule.lit.add("Lens")
        self.controls.lights["ABC123"] = mock.Mock()
        self.app_.pause_rule(rule.id)
        self.assertEqual(self.controls.commands, [("light", "ABC123", "off")])

    def test_a_stopped_rule_cannot_use_a_camera(self):
        self.rule.paused = True
        self.assertIn("stopped", self.call("light", camera="Lens")["error"])
        self.assertEqual(self.controls.commands, [])

    def test_a_rule_cannot_use_a_camera_without_end(self):
        for _ in range(server.MAX_CONTROLS_PER_MINUTE):
            self.call("move", camera="Lens", direction="up")
        self.assertIn("error", self.call("move", camera="Lens", direction="up"))

    def test_a_click_on_the_wall_becomes_a_point_in_the_fixed_lens(self):
        self.app_.camera_command({"do": "aim", "camera": "Lens", "x": 0.75, "y": 0.25})
        self.assertEqual(self.controls.commands, [("aim", "ABC123", "0.500", "0.500")])

    def test_a_click_outside_the_fixed_lens_turns_nothing(self):
        with self.assertRaisesRegex(server.ControlError, "Click inside Wide"):
            self.app_.camera_command({"do": "aim", "camera": "Lens", "x": 0.25, "y": 0.25})

    def test_the_page_never_gets_the_serial(self):
        self.assertNotIn("ABC123", json.dumps(self.app_.status()))

    def test_a_bad_serial_gets_no_commands(self):
        self.app_.claim("camera-1", ["Lens"], [{**self.LENS, "serial": "ABC123 --x"}], (1, 1))
        self.assertIn("error", self.call("aim", camera="Lens", x=0, y=0))

    def test_wonder_learns_what_each_camera_can_do(self):
        text = self.controls.describe(self.app_.cameras)
        self.assertIn("- Wide\n", text)
        self.assertIn("- Lens: a moving lens, which sees half the width of Wide, it has a light", text)

    def test_the_moving_lens_comes_first_in_a_gallery(self):
        self.assertEqual(self.app_._lens_bonus(2), server.MOVING_LENS_BONUS)
        self.assertEqual(self.app_._lens_bonus(1), 1.0)
        self.assertEqual(self.app_._lens_bonus(None), 1.0)


class StopAndResume(TempLogs):
    def stopped_rule(self, client=None, **fields):
        client = client or FakeClient(answer=True)
        app = self.app(client)
        rule = FakeRule(app, **fields)
        app.frame(b"a", 1.0)
        app.frame(b"a", 2.0)
        client.asked.clear()
        self.assertTrue(app.pause_rule("r"))
        return app, rule, client

    def test_a_stopped_rule_makes_no_model_call(self):
        app, rule, client = self.stopped_rule()
        for t in range(2, 8):
            app.frame(b"f%d" % t, float(t))
        self.assertEqual(client.asked, [])

    def test_stop_ends_the_event_that_was_on(self):
        app, rule, _ = self.stopped_rule()
        self.assertEqual([m["type"] for m in rule.sent], ["on", "off"])

    def test_a_stopped_rule_gets_no_ticks(self):
        client = FakeClient()
        app = self.app(client)
        rule = server.Rule(app, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        sent = []
        rule.send = sent.append
        app.entries["r"] = rule
        app.last_frame = time.time()
        app.pause_rule("r")
        app.tick()
        self.assertEqual(sent, [])

    def test_a_stopped_rule_does_not_set_the_capture_rate(self):
        app = self.app(FakeClient())
        FakeRule(app, frames_per_second=10)
        app.pause_rule("r")
        self.assertEqual(app.capture_fps(), server.DEFAULT_CAPTURE_FPS)

    def test_a_call_from_a_stopped_rule_gets_an_error_and_no_model(self):
        client = FakeClient()
        app = self.app(client)
        replies = Replies()
        replies.paused = True
        name = app.evidence.save_frame(0.5, b"x")
        app._rule_call(replies, {"id": 1, "name": "ask", "args": {"question": "q", "frames": [name]}})
        self.assertIn("stopped", replies.replies[0]["error"])
        self.assertEqual(client.looked, [])

    def test_a_job_that_was_running_cannot_act_after_stop(self):
        app = self.app(FakeClient())
        rule = server.Rule(app, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        rule.paused = True
        with mock.patch.object(server.actions, "run") as run:
            rule._handle({"do": "notify", "text": "hello"})
            rule._handle({"do": "show", "name": "a", "value": "b"})
        run.assert_not_called()
        self.assertEqual((rule.values, list(app.feed)), ({}, []))

    def test_a_review_that_ends_after_stop_fires_nothing(self):
        client = FakeClient(answer=False)
        client.part_seen = True
        client.reviews = [(True, 0.9)]
        app = self.app(client)
        rule = FakeRule(app)
        app.frame(b"a", 1.0)
        app.frame(b"b", 1.0 + 0.01)
        original = client.review

        def review(*args):
            app.pause_rule("r")
            return original(*args)
        client.review = review
        app.frame(b"c", 2.5)
        self.assertEqual([m["type"] for m in rule.sent], [])

    def test_resume_reads_only_new_frames(self):
        app, rule, client = self.stopped_rule(frames_per_check=8)
        app.frame(b"during", time.time() - 1)
        self.assertTrue(app.resume_rule("r"))
        self.assertFalse(rule.paused)
        app.frame(b"after", time.time() + 1)
        seen = [frame for frames in client.asked for frame in frames]
        self.assertIn(b"after", seen)
        self.assertNotIn(b"during", seen)

    def test_resume_can_fire_the_event_again(self):
        app, rule, _ = self.stopped_rule()
        app.resume_rule("r")
        app.frame(b"after", time.time() + 1)
        app.frame(b"after", time.time() + 2.5)
        self.assertEqual([m["type"] for m in rule.sent], ["on", "off", "on"])

    def test_a_waiting_job_ends_on_stop_and_the_sensor_stays_asleep(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app, starts_off=True)
        app._rule_arm(rule, "s", 60)
        app.pause_rule("r")
        app.resume_rule("r")
        self.assertFalse(rule.sensors[0].awake(time.time()))

    def test_the_page_can_see_a_stopped_rule(self):
        app = self.app(FakeClient())
        rule = server.Rule(app, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        app.entries["r"] = rule
        app.pause_rule("r")
        self.assertTrue(app.status()["rules"][0]["paused"])

    def test_an_unknown_rule_is_refused(self):
        app = self.app(FakeClient())
        self.assertFalse(app.pause_rule("nope"))
        self.assertFalse(app.resume_rule("nope"))


class ModelAnswers(unittest.TestCase):
    def client(self, answer):
        client = Client("test")
        reply = {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}],
                 "usage": {"input_tokens": 1000, "output_tokens": 100}}
        client._post = lambda body, **_: json.dumps(reply)
        return client

    def test_part_seen_needs_a_failed_fact(self):
        answer = {"best_frame": 1, "facts": [True], "note": "x", "part_seen": True}
        self.assertFalse(self.client(answer).check_facts("q", ["f"], [b"j"])[0]["part_seen"])
        answer = {"best_frame": 1, "facts": [False], "note": "x", "part_seen": True}
        self.assertTrue(self.client(answer).check_facts("q", ["f"], [b"j"])[0]["part_seen"])

    def test_a_review_keeps_its_answer_inside_the_limits(self):
        answer = {"frames": ["a"] * 50, "clues_for": ["b"] * 50, "clues_against": [], "passed": True,
                  "confidence": 7, "doers": [{"who": "x", "best_frame": 2, "last_frame": 2}] * 50}
        review, cost = self.client(answer).review("q", ["f"], [b"j", b"k"], [3.0, 0.0])
        self.assertEqual((len(review["frames"]), len(review["clues_for"]), review["confidence"]), (2, 6, 1.0))
        self.assertEqual(len(review["doers"]), 4)
        self.assertGreater(cost, 0)

    def test_a_busy_analysis_lane_leaves_live_checks_free(self):
        class Connection:
            sock = mock.Mock()
            status = 200

            def request(self, *args):
                pass

            def getresponse(self):
                return self

            def read(self):
                return b"{}"

            def close(self):
                pass

        client = Client("test", pool_size=1)
        client._pools["analysis"].get()
        client._new_connection = Connection
        self.assertEqual(client._post({}, lane="live"), b"{}")


class Plan(TempLogs):
    def test_wonder_timing_is_used(self):
        s = server.Sensor({"id": "s", "pattern": "moment", "frames_per_second": 10,
                           "check_every_s": 1.0, "frames_per_check": 8, "clear_after_s": 0.5})
        self.assertEqual((s.pattern, s.frames_per_second, s.check_every_s, s.frames_per_check, s.clear_after_s),
                         ("moment", 10, 1.0, 8, 0.5))

    def test_bad_timing_is_kept_inside_the_limits(self):
        s = server.Sensor({"id": "s", "frames_per_second": 500, "check_every_s": 0, "frames_per_check": 99})
        self.assertEqual((s.frames_per_second, s.check_every_s, s.frames_per_check),
                         (server.MAX_CAPTURE_FPS, server.MIN_CHECK_EVERY_S, 8))

    def test_capture_follows_the_fastest_sensor(self):
        app = self.app(FakeClient())
        self.assertEqual(app.capture_fps(), server.DEFAULT_CAPTURE_FPS)
        FakeRule(app, frames_per_second=10)
        self.assertEqual(app.capture_fps(), 10)


class Events(TempLogs):
    def test_two_checks_in_a_row_on_new_frames_send_on_to_the_code(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app)
        self.assertIsNone(app.frame(b"one", 1.0)["results"][0]["event"])
        out = app.frame(b"two", 2.0)
        self.assertEqual(out["results"][0]["event"], "on")
        self.assertEqual(app.client.asked, [[b"one"], [b"two"]])
        self.assertEqual({k: rule.sent[0][k] for k in ("type", "sensor", "t")},
                         {"type": "on", "sensor": "s", "t": 2.0})
        self.assertEqual(rule.sent[0]["event"]["note"], "test")

    def test_one_random_yes_does_not_start_an_event(self):
        client = FakeClient(answer=False)
        client.answers = [True, False, True]
        app = self.app(client)
        rule = FakeRule(app)
        for t in (1.0, 2.0, 3.0):
            app.frame(b"frame", t)
        self.assertEqual(rule.sent, [])

    def test_a_state_check_asks_once(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app)
        app.frame(b"a", 1.0)
        app.frame(b"b", 2.0)
        app.frame(b"c", 3.0)
        self.assertEqual(len(app.client.asked), 3)
        self.assertEqual(len(rule.sent), 1)

    def test_a_state_ends_after_its_clear_time(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule = FakeRule(app, clear_after_s=2.0)
        app.frame(b"a", 1.0)
        app.frame(b"a", 2.0)
        client.answer = False
        app.frame(b"b", 3.0)
        self.assertEqual(len(rule.sent), 1)
        app.frame(b"c", 4.0)
        self.assertEqual(rule.sent[-1], {"type": "off", "sensor": "s", "t": 4.0, "duration": 2.0})

    def test_no_check_runs_before_it_is_due(self):
        app = self.app(FakeClient(answer=False))
        FakeRule(app)
        app.frame(b"a", 1.0)
        self.assertEqual(app.frame(b"b", 1.3)["results"], [])
        self.assertEqual(len(app.client.asked), 1)

    def test_every_frame_is_covered_by_a_check(self):
        client = FakeClient(answer=False)
        app = self.app(client)
        rule = FakeRule(app, frames_per_check=8)
        for i in range(13):
            app.frame(bytes([i]), 1.0 + i * 0.25)
        covered = [r for r in (app.evidence.check(f"c{n}-{app.evidence.session}") for n in range(1, 40)) if r]
        total = sum(c["frames_covered"] for c in covered)
        self.assertEqual(total, 13)
        self.assertEqual(rule.sent, [])

    def test_a_slow_check_does_not_lose_frames(self):
        app = self.app(FakeClient(answer=False))
        rule = FakeRule(app)
        sensor_ = rule.sensors[0]
        app.frame(b"a", 1.0)
        sensor_.running = server.MAX_SENSOR_CHECKS
        for i in range(8):
            app.frame(bytes([i]), 2.0 + i * 0.5)
        sensor_.running = 0
        out = app.frame(b"z", 6.0)
        self.assertEqual(out["results"][0]["frames_covered"], 9)

    def test_a_moment_with_no_movement_costs_no_call(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        FakeRule(app, pattern="moment")
        app.frame(b"a", 1.0, motion=1.0)
        app.frame(b"b", 2.5, motion=2.0)
        self.assertEqual(client.asked, [])

    def test_a_moment_below_its_own_movement_level_costs_no_call(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        FakeRule(app, pattern="moment", min_motion=25)
        app.frame(b"a", 1.0, motion=15.0)
        self.assertEqual(client.asked, [])
        app.frame(b"b", 2.5, motion=30.0)
        self.assertEqual(len(client.asked), 2)

    def test_each_moment_is_one_occurrence_for_the_code(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app, pattern="moment", clear_after_s=0.5)
        app.frame(b"a", 1.0, motion=40.0)
        app.frame(b"b", 2.5, motion=40.0)
        kinds = [m["type"] for m in rule.sent]
        self.assertEqual(kinds, ["on", "off", "on", "off"])

    def test_checks_overlap_up_to_their_limit(self):
        app = self.app(FakeClient())
        for pattern in ("moment", "state"):
            sensor_ = server.Sensor({"id": "s", "pattern": pattern, "check_every_s": 1.0})
            count = 0
            for i in range(5):
                app.frames.append(server.Frame(i + 1.0, b"x", "x.jpg", 50.0))
                if app._due(sensor_, i + 1.0):
                    count += 1
            self.assertEqual(count, server.MAX_SENSOR_CHECKS)
            app.frames.clear()

    def test_one_burst_across_two_checks_is_one_occurrence(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app, pattern="moment", clear_after_s=1.0)
        app.frame(b"a", 1.0, motion=2.0)
        for i, level in enumerate([5, 30, 45]):
            app.frame(b"b", 1.2 + i * 0.3, motion=float(level))
        app.frame(b"c", 2.1, motion=44.0)
        app.frame(b"d", 2.2, motion=20.0)
        app.frame(b"e", 3.0, motion=9.0)
        self.assertEqual(len([m for m in rule.sent if m["type"] == "on"]), 1)

    def test_late_answers_are_applied_in_time_order(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app, pattern="moment", clear_after_s=0.5)
        sensor_ = rule.sensors[0]
        first = server.Frame(1.0, b"a", "a.jpg", 50.0)
        second = server.Frame(3.0, b"b", "b.jpg", 50.0)
        sensor_.next_turn = 2
        sensor_.running = 2
        # The second check finishes first, and nothing is lost.
        self.assertEqual(app._check(rule, sensor_, [second], 1), [])
        settled = app._check(rule, sensor_, [first], 0)
        self.assertEqual([r["event"] for r in settled], ["moment", "moment"])
        self.assertEqual([m["t"] for m in rule.sent if m["type"] == "on"], [1.0, 3.0])
        self.assertEqual(sensor_.running, 0)

    def test_a_failed_check_is_reported_and_does_not_block_the_sensor(self):
        app = self.app(FakeClient(fail=True))
        rule = FakeRule(app)
        out = app.frame(b"a", 1.0)
        self.assertIn("did not answer", out["results"][0]["error"])
        self.assertEqual(rule.sensors[0].running, 0)

    def test_frames_and_checks_are_saved(self):
        app = self.app(FakeClient(answer=True))
        FakeRule(app)
        out = app.frame(b"frame", 1.0, motion=7.0)
        check = app.evidence.check(out["results"][0]["check"])
        self.assertEqual(check["frames"], ["0000001.jpg"])
        self.assertEqual((app.evidence.frames_dir / "0000001.jpg").read_bytes(), b"frame")
        self.assertTrue((app.evidence.folder / "checks.jsonl").exists())

    def test_only_the_newest_page_can_send_frames(self):
        app = self.app(FakeClient(answer=False))
        FakeRule(app)
        app.claim("old")
        app.claim("new")
        self.assertTrue(app.frame(b"a", 1.0, "old")["inactive"])
        self.assertTrue(app.frame(b"b", 2.0)["inactive"])
        self.assertEqual(app.client.asked, [])
        self.assertEqual(len(app.frames), 0)
        self.assertNotIn("inactive", app.frame(b"c", 3.0, "new"))

    def test_a_frame_path_cannot_leave_the_log_folder(self):
        app = self.app(FakeClient())
        self.assertIsNone(app.evidence.frame_path(app.evidence.session, "../../.env"))
        self.assertIsNone(app.evidence.frame_path("other", "0000001.jpg"))


class JpegSize(unittest.TestCase):
    def test_a_real_frame_gives_its_size(self):
        frame = Path(__file__).resolve().parent.parent / "testdata" / "frames" / "mug_384.jpg"
        self.assertEqual(jpeg_size(frame.read_bytes()), (384, 288))

    def test_anything_else_gives_none(self):
        self.assertIsNone(jpeg_size(b"frame"))


class Masks(unittest.TestCase):
    LINE = ("<0f>1<|box;x1=198;y1=142;x2=252;y2=198;w=384;h=288;c=0.953125|>"
            "<|mask;x=0;y=0;c=0.953125;data=57,55,!!!!!SEg).EH0Lj/lJ*%M1(ZB4'X&/U$@@$k2E(s[%(%HvaMgx5.3Lg1%Aj('!!|>")

    def test_a_real_sam_line_gives_a_box_and_an_outline(self):
        box = parse_segments(self.LINE)[0]
        self.assertEqual((box["x1"], box["x2"], box["mask_w"], box["mask_h"]), (198, 252, 55, 57))
        self.assertTrue(box["path"].startswith("M"))

    def test_a_broken_mask_keeps_the_box(self):
        box = parse_segments(self.LINE.replace("SEg", "SE"))[0]
        self.assertEqual(box["x1"], 198)
        self.assertEqual(box["path"], "")


def segment(x1, x2, conf=0.9):
    return {"x1": x1, "y1": 0, "x2": x2, "y2": 10, "w": 100, "h": 100, "c": conf,
            "path": "M0 0Z", "mask_w": x2 - x1, "mask_h": 10}


class Draw(TempLogs):
    def fired(self, client, **fields):
        app = self.app(client)
        rule = FakeRule(app, outline="bottle", **fields)
        app.frame(b"frame", 1.0)
        app.frame(b"frame", 2.0)
        return app, rule

    def feed(self, app, kind):
        return [item for item in app.feed if item["kind"] == kind]

    def test_a_draw_freezes_on_the_frame_that_proved_the_event(self):
        client = FakeClient(answer=True)
        app, rule = self.fired(client)
        check = rule.sent[0]["event"]["check"]
        app.draw(rule, "3 drinks", check=check)
        item = self.feed(app, "draw")[0]
        self.assertEqual(item["frame"], f"/evidence/{app.evidence.session}/0000003.jpg")
        self.assertEqual(item["check"], check)
        self.assertEqual(item["text"], "3 drinks")

    def test_the_proof_is_the_frame_that_spark_chose(self):
        self.assertEqual(server.proof_frame(
            {"frames": ["a", "b", "c"], "answers": [{"best_frame": 2}]}), "b")
        self.assertEqual(server.proof_frame({"frames": ["a", "b"], "answers": [{"best_frame": 0}]}), "a")
        self.assertEqual(server.proof_frame({"frames": ["a"]}), "a")
        self.assertIsNone(server.proof_frame({"frames": []}))

    def test_the_outline_follows_the_freeze_and_stays_with_the_check(self):
        client = FakeClient(answer=True)
        client.matches = [segment(0, 10, 0.5), segment(50, 60, 0.9)]
        app, rule = self.fired(client)
        check = rule.sent[0]["event"]["check"]
        app.draw(rule, "3 drinks", check=check)
        outline = self.feed(app, "outline")[0]
        self.assertEqual(outline["draw"], self.feed(app, "draw")[0]["id"])
        self.assertEqual(outline["outline"]["x1"], 50)
        self.assertEqual(client.found, [("bottle", b"frame")])
        self.assertEqual(app.evidence.check(check)["outline"]["x1"], 50)

    def test_an_outline_on_another_frame_stays_off_the_check(self):
        client = FakeClient(answer=True)
        client.matches = [segment(50, 60, 0.9)]
        app, rule = self.fired(client)
        check = rule.sent[0]["event"]["check"]
        client.answer = False
        app.frame(b"later", 9.0)
        app.draw(rule, "Later picture", frame=app.frames[-1].name, check=check)
        self.assertEqual(self.feed(app, "outline")[0]["outline"]["x1"], 50)
        self.assertNotIn("outline", app.evidence.check(check))

    def test_nothing_to_outline_still_freezes(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule = FakeRule(app)
        app.frame(b"frame", 1.0)
        app.draw(rule, "done")
        self.assertEqual(client.found, [])
        self.assertEqual(len(self.feed(app, "draw")), 1)
        self.assertIsNone(self.feed(app, "outline")[0]["outline"])

    def test_a_noun_that_sam_does_not_know_falls_back_to_spark(self):
        client = FakeClient(answer=True)
        client.place = segment(30, 40)
        client.marks = [segment(31, 41)]
        app, rule = self.fired(client)
        app.draw(rule, "1 drink")
        self.assertEqual(client.located, ["Is it there?"])
        self.assertEqual(client.marked, [client.place])
        self.assertEqual(self.feed(app, "outline")[0]["outline"]["x1"], 31)

    def test_the_page_hears_when_no_outline_exists(self):
        client = FakeClient(answer=True)
        app, rule = self.fired(client)
        app.draw(rule, "1 drink")
        item = self.feed(app, "outline")[0]
        self.assertEqual(item["draw"], self.feed(app, "draw")[0]["id"])
        self.assertIsNone(item["outline"])

    def test_a_pointed_object_wins_and_the_nearest_match_is_outlined(self):
        client = FakeClient(answer=True)
        client.matches = [segment(0, 10, 0.99), segment(70, 80, 0.5)]
        app, rule = self.fired(client)
        rule.pointed = {"noun": "water bottle", "box": segment(60, 90)}
        app.draw(rule, "seen")
        self.assertEqual(client.found[0][0], "water bottle")
        self.assertEqual(self.feed(app, "outline")[0]["outline"]["x1"], 70)

    def test_a_pointed_object_with_no_name_match_uses_its_old_box(self):
        client = FakeClient(answer=True)
        client.marks = [segment(60, 90)]
        app, rule = self.fired(client)
        rule.pointed = {"noun": "door", "box": segment(60, 90)}
        app.draw(rule, "open")
        self.assertEqual(client.marked, [rule.pointed["box"]])
        self.assertEqual(len(self.feed(app, "outline")), 1)

    def test_in_a_moving_camera_the_old_box_is_not_used_and_the_look_decides(self):
        client = FakeClient(answer=True)
        client.matches = [segment(0, 10, 0.99), segment(70, 80, 0.5)]
        client.picks = {"All cameras": 1}
        app, rule = self.fired(client)
        app.cameras = ["Gate tracker"]
        app.camera_kinds.set("Gate tracker", "moving")
        rule.folder = Path(self.logs)
        (rule.folder / "pointed").mkdir()
        (rule.folder / "pointed" / "clicked.jpg").write_bytes(solid_jpeg(20, 20))
        rule.pointed = {"noun": "car", "box": segment(60, 90), "camera": "Gate tracker", "reference": "pointed/clicked.jpg"}
        with mock.patch.object(server, "numbered", lambda jpeg, boxes: b"numbered"):
            app.draw(rule, "moved")
        self.assertEqual(client.compared_views, [("All cameras", 2)])
        self.assertEqual(self.feed(app, "outline")[0]["outline"]["x1"], 0)

    def test_in_a_still_camera_the_old_box_still_helps(self):
        client = FakeClient(answer=True)
        client.matches = [segment(0, 10, 0.99), segment(70, 80, 0.5)]
        app, rule = self.fired(client)
        app.cameras = ["Gate wide"]
        rule.pointed = {"noun": "gate", "box": segment(60, 90), "camera": "Gate wide"}
        app.draw(rule, "open")
        self.assertEqual(self.feed(app, "outline")[0]["outline"]["x1"], 70)

    def test_a_tick_can_draw_on_the_newest_frame(self):
        client = FakeClient(answer=False)
        app, rule = self.fired(client)
        app.frame(b"later", 3.0)
        app.draw(rule, "5 minutes")
        self.assertTrue(self.feed(app, "draw")[0]["frame"].endswith(app.frames[-1].name))
        self.assertIsNone(self.feed(app, "draw")[0]["check"])

    def test_the_why_view_can_outline_an_event_that_drew_nothing(self):
        client = FakeClient(answer=True)
        client.matches = [segment(5, 15)]
        app, rule = self.fired(client)
        check = rule.sent[0]["event"]["check"]
        self.assertEqual(app.check_outline(check)["x1"], 5)
        self.assertEqual(app.check_outline(check)["x1"], 5)
        self.assertEqual(len(client.found), 1)

    def test_the_proof_keeps_the_movement_numbers(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        FakeRule(app, pattern="moment", min_motion=25)
        out = app.frame(b"a", 1.0, motion=40.0)
        check = app.evidence.check(out["results"][0]["check"])
        self.assertEqual((check["peak_motion"], check["min_motion"]), (40.0, 25))

    def test_a_failed_outline_does_not_stop_the_rule(self):
        client = FakeClient(answer=True)
        def fail(target, jpeg):
            raise ApiError("Meta API did not answer in time.")
        client.find = fail
        app, rule = self.fired(client)
        app.draw(rule, "seen")
        self.assertEqual(len(self.feed(app, "draw")), 1)
        # The person's activity stays clean. The miss is a note under Problems.
        self.assertEqual(self.feed(app, "log"), [])
        self.assertIn("without an outline", app.problems.public()["groups"][0]["title"])

    def test_a_rule_message_reaches_the_draw(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule = server.Rule(app, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        rule._handle({"do": "draw", "text": "hello"})
        self.assertEqual(self.feed(app, "draw")[0]["text"], "hello")
        # A run is an event of the Whenever. A draw is one of its outputs, so it does not count as one.
        self.assertEqual(rule.fired, 0)


def solid_jpeg(width, height, colour=(90, 90, 90)):
    from PIL import Image
    out = BytesIO()
    Image.new("RGB", (width, height), colour).save(out, "JPEG")
    return out.getvalue()


class Pointing(TempLogs):
    def test_a_click_finds_one_object_and_keeps_it(self):
        client = FakeClient()
        client.marks = [segment(10, 20)]
        app = self.app(client)
        out = app.point(solid_jpeg(100, 100), 15, 5, 100, 100)
        self.assertTrue(out["found"])
        self.assertEqual(client.marked, [(15, 5)])
        self.assertEqual(app.see_calls, 1)
        self.assertEqual(out["box"]["x1"], 10)

    def test_a_click_on_nothing_keeps_nothing(self):
        app = self.app(FakeClient())
        self.assertEqual(app.point(solid_jpeg(100, 100), 1, 1, 100, 100)["found"], False)
        self.assertEqual(len(app.points), 0)

    def test_an_object_is_named_once_from_a_close_crop(self):
        client = FakeClient()
        client.marks = [segment(10, 20)]
        app = self.app(client)
        point_id = app.point(solid_jpeg(100, 100), 15, 5, 100, 100)["id"]
        self.assertEqual(app.name_point(point_id), "water bottle")
        self.assertEqual(app.name_point(point_id), "water bottle")
        self.assertEqual(client.named, 1)
        self.assertIsNotNone(client.name_pictures[1])
        self.assertGreaterEqual(max(Image.open(BytesIO(app.point_crop(point_id))).size), server.POINT_CROP_SIDE)

    def test_old_points_are_dropped(self):
        client = FakeClient()
        client.marks = [segment(10, 20)]
        app = self.app(client)
        for _ in range(server.MAX_POINTS + 5):
            app.point(solid_jpeg(100, 100), 15, 5, 100, 100)
        self.assertEqual(len(app.points), server.MAX_POINTS)

    def test_no_click_runs_after_the_spend_limit(self):
        client = FakeClient()
        app = self.app(client)
        app.writer_usd = server.SPEND_LIMIT_USD
        self.assertTrue(app.point(solid_jpeg(100, 100), 1, 1, 100, 100)["stopped"])
        self.assertEqual(client.marked, [])

    def wall(self, client):
        """Two cameras side by side, as camera.py sends them."""
        app = self.app(client)
        app.wall, app.cameras = (2, 1), ["Gate wide", "Gate tracker"]
        return app

    def test_a_click_on_a_wall_goes_to_that_camera_alone(self):
        client = FakeClient()
        client.marks = [segment(40, 60)]
        app = self.wall(client)
        out = app.point(solid_jpeg(400, 100), 300, 50, 400, 100)
        self.assertEqual(out["camera"], "Gate tracker")
        # The tile is 200 wide, so the click sits at its middle, in the tile's own pixels.
        self.assertEqual(client.marked, [(100, 50)])
        # Back on the wall, the box moves into the right half: 200 + 40% and 60% of 200.
        self.assertEqual((out["box"]["x1"], out["box"]["x2"], out["box"]["w"]), (280, 320, 400))

    def test_a_fresh_full_frame_gives_the_camera_more_pixels(self):
        client = FakeClient()
        client.marks = [segment(40, 60)]
        app = self.wall(client)
        name = app.evidence.save_frame(time.time(), solid_jpeg(400, 100))
        app.evidence.save_full(name, 1, solid_jpeg(1920, 1080))
        app.frames.append(server.Frame(time.time(), b"", name, 5.0))
        out = app.point(solid_jpeg(400, 100), 300, 50, 400, 100)
        view = Image.open(BytesIO(app.points[out["id"]]["view"]))
        self.assertEqual(view.size, (1280, 720))

    def test_the_same_object_is_found_in_the_other_camera_and_the_person_decides(self):
        client = FakeClient()
        client.marks = [segment(40, 60)]
        client.noun = "gate"
        client.matches = [segment(10, 20), segment(50, 70)]
        client.picks = {"Gate wide": 2}
        app = self.wall(client)
        point_id = app.point(solid_jpeg(400, 100), 300, 50, 400, 100)["id"]
        app.name_point(point_id)
        also = app.find_elsewhere(point_id)
        self.assertEqual(client.compared_views, [("Gate wide", 2)])
        self.assertEqual([(a["camera"], a["view_box"]["x1"]) for a in also], [("Gate wide", 50)])
        done = app.confirm_point(point_id, "sliding gate", "It opens to the left.", ["Gate wide"])
        self.assertEqual(done, {"noun": "sliding gate", "camera": "Gate tracker", "also": ["Gate wide"],
                                "kinds": {"Gate tracker": "fixed", "Gate wide": "fixed"}})
        kept = app.pointed_for_rule(point_id)
        self.assertEqual((kept["named_by"], kept["note"], kept["also"][0]["camera"]), ("person", "It opens to the left.", "Gate wide"))
        self.assertNotIn("crop", json.dumps(kept))
        self.assertEqual(app.confirm_point(point_id, "sliding gate", "", [])["also"], [])
        with self.assertRaises(ValueError):
            app.confirm_point(point_id, "  ")

    def test_another_thing_with_the_same_name_does_not_count(self):
        client = FakeClient()
        client.marks = [segment(40, 60)]
        client.matches = [segment(10, 20)]
        client.picks = {"Gate wide": 0}
        app = self.wall(client)
        point_id = app.point(solid_jpeg(400, 100), 300, 50, 400, 100)["id"]
        app.name_point(point_id)
        self.assertEqual(app.find_elsewhere(point_id), [])

    def test_each_view_keeps_its_kind_and_the_close_ups_go_with_the_rule(self):
        client = FakeClient()
        client.marks = [segment(40, 60)]
        client.matches = [segment(50, 70)]
        client.picks = {"Gate wide": 1}
        app = self.wall(client)
        app.camera_kinds.set("Gate tracker", "moving")
        point_id = app.point(solid_jpeg(400, 100), 300, 50, 400, 100)["id"]
        app.name_point(point_id)
        app.find_elsewhere(point_id)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        pointed = app.save_pointed(point_id, folder, app.pointed_for_rule(point_id))
        self.assertEqual((pointed["kind"], pointed["also"][0]["kind"], pointed["moving"]), ("moving", "fixed", ["Gate tracker"]))
        self.assertEqual(pointed["reference"], "pointed/clicked.jpg")
        self.assertTrue((folder / "pointed" / "clicked.jpg").is_file())
        self.assertTrue((folder / pointed["also"][0]["reference"]).is_file())
        saved = json.loads((folder / "pointed.json").read_text())
        self.assertEqual(saved["noun"], "water bottle")
        self.assertNotIn("box", saved)

    def test_one_camera_has_nowhere_else_to_look(self):
        client = FakeClient()
        client.marks = [segment(10, 20)]
        app = self.app(client)
        point_id = app.point(solid_jpeg(100, 100), 15, 5, 100, 100)["id"]
        app.name_point(point_id)
        self.assertEqual(app.find_elsewhere(point_id), [])
        self.assertEqual(client.found, [])


class Kinds(TempLogs):
    def test_a_camera_is_still_until_it_is_seen_to_turn_and_the_person_wins(self):
        path = Path(self.logs) / "cameras.json"
        kinds = server.CameraKinds(path)
        self.assertEqual((kinds.kind("Side"), kinds.why("Side")), ("fixed", "Its picture has stayed still so far."))
        kinds.turned("Side", 1_800_000_000)
        # One shift can be headlights, so it takes more than one pan.
        self.assertEqual(kinds.kind("Side"), "fixed")
        self.assertIn("shifted once", kinds.why("Side"))
        for minute in range(1, server.TURNS_TO_MOVE):
            kinds.turned("Side", 1_800_000_000 + 60 * minute)
        self.assertEqual(kinds.kind("Side"), "moving")
        self.assertIn("panned", kinds.why("Side"))
        kinds.set("Side", "fixed")
        self.assertEqual((kinds.kind("Side"), kinds.why("Side")), ("fixed", "You set this."))
        again = server.CameraKinds(path)
        self.assertEqual(again.kind("Side"), "fixed")
        again.set("Side", "auto")
        self.assertEqual(again.kind("Side"), "moving")
        self.assertEqual(again.kind("Gate tracker", can_turn=True), "moving")
        with self.assertRaises(ValueError):
            again.set("Side", "sideways")

    def test_the_page_sees_each_camera_with_its_reason(self):
        app = self.app(FakeClient())
        app.cameras = ["Driveway", "Gate tracker"]
        app.camera_kinds.set("Gate tracker", "moving")
        self.assertEqual([(c["name"], c["kind"], c["by"]) for c in app.status()["camera_kinds"]],
                         [("Driveway", "fixed", "whenever"), ("Gate tracker", "moving", "person")])

    def test_a_removed_camera_leaves_the_list_and_can_come_back(self):
        app = self.app(FakeClient())
        app.cameras = ["Driveway", "Side"]
        app.camera_kinds.remove("Side")
        state = app.status()
        self.assertEqual(state["removed_cameras"], ["Side"])
        self.assertEqual([(c["name"], c["removed"]) for c in state["camera_kinds"]], [("Driveway", False), ("Side", True)])
        app.claim("camera-phone", ["Side"])
        self.assertEqual(app.status()["removed_cameras"], [])

    def test_relayed_cameras_name_their_streams_and_say_they_run(self):
        app = self.app(FakeClient())
        app.claim("camera-1", ["Driveway", "Side"], [], (2, 1), ["driveway", "side"])
        self.assertEqual(app.streams, {"Driveway": "driveway", "Side": "side"})
        with mock.patch.object(server, "HLS_URL", "http://127.0.0.1:8888"):
            self.assertEqual(app.status()["streams"], {"Driveway": "driveway", "Side": "side"})
        self.assertEqual(app.status()["streams"], {})
        self.assertFalse(app.status()["live_wanted"])
        self.assertFalse(app.camera_live())
        self.assertFalse(app.alive("someone-else"))
        self.assertTrue(app.alive("camera-1"))
        self.assertTrue(app.camera_live())

    def test_the_page_learns_who_feeds_the_camera(self):
        app = self.app(FakeClient())
        self.assertEqual(app.status()["owner"], {"cameras": False, "page": ""})
        app.claim("page-1")
        self.assertEqual(app.status()["owner"]["page"], "page-1")
        app.live_at = time.time()
        app.claim("page-2")
        self.assertEqual(app.status()["owner"], {"cameras": True, "page": "page-1"})

    def test_a_rule_can_check_against_the_close_up_in_its_folder(self):
        client = FakeClient()
        app = self.app(client)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / "pointed").mkdir()
        (folder / "pointed" / "clicked.jpg").write_bytes(b"close-up")
        rule = Replies(folder)
        name = app.evidence.save_frame(1.0, b"jpeg")
        app._rule_check(rule, "The same gate is open.", [name], reference="pointed/clicked.jpg")
        self.assertEqual(client.verified[0][3], b"close-up")
        app._rule_check(rule, "x", [name], reference="../../.env")
        self.assertIsNone(client.verified[1][3])


class AlertMedia(TempLogs):
    """An alert shows only the cameras where it happened, with a clip of the moments around it."""

    def setUp(self):
        super().setUp()
        self.app_ = self.app(FakeClient())
        self.app_.wall, self.app_.cameras = (2, 2), ["Gate wide", "Driveway", "Garden"]
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.rule = server.Rule(self.app_, "r1", "When a car moves", {"name": "Car", "sensors": []}, self.folder)
        now = time.time()
        self.names = []
        for i in range(6):
            name = self.app_.evidence.save_frame(now - 3 + i * 0.5, solid_jpeg(400, 113))
            self.app_.evidence.save_sharp(name, solid_jpeg(800, 450, (40 * i, 90, 90)))
            self.app_.timeline.append(server.Frame(now - 3 + i * 0.5, None, name, 5.0))
            self.names.append(name)
        self.rule.last_event = {"t": now - 2, "tile": 1, "frame": self.names[2]}

    def test_only_the_camera_that_fired_is_sent_with_a_clip(self):
        media = self.app_.alert_media(self.rule)
        self.assertEqual(media["cameras"], ["Driveway"])
        self.assertEqual(Image.open(BytesIO(media["picture"])).size, (400, 225))
        self.assertTrue(media["url"].startswith("/rule-file/r1/pictures/alert-"))
        if shutil.which("ffmpeg"):
            self.assertEqual(media["clip"][4:8], b"ftyp")
            self.assertTrue(media["clip_url"].startswith("/rule-file/r1/clips/"))
            self.assertIsNotNone(server.rule_clip(self.folder, media["clip_url"].split("/r1/", 1)[1]))

    def test_a_kept_full_frame_gives_the_camera_its_real_pixels(self):
        self.app_.evidence.save_full(self.names[2], 1, solid_jpeg(1920, 1080))
        media = self.app_.alert_media(self.rule)
        self.assertEqual(Image.open(BytesIO(media["picture"])).size, (960, 540))

    def test_two_views_of_a_pointed_thing_sit_side_by_side(self):
        self.rule.pointed = {"noun": "car", "camera": "Driveway", "also": [{"camera": "Gate wide"}]}
        media = self.app_.alert_media(self.rule)
        self.assertEqual(media["cameras"], ["Driveway", "Gate wide"])
        self.assertEqual(Image.open(BytesIO(media["picture"])).size, (806, 225))

    def test_one_camera_keeps_its_whole_picture(self):
        self.app_.wall, self.app_.cameras = (1, 1), []
        media = self.app_.alert_media(self.rule)
        self.assertEqual(media["cameras"], [])
        self.assertEqual(Image.open(BytesIO(media["picture"])).size, (800, 450))

    def test_an_old_event_does_not_choose_the_camera(self):
        self.rule.last_event["t"] -= server.EVENT_FRESH_S + 1
        self.assertEqual(self.app_.alert_media(self.rule)["cameras"], [])

    def test_a_clip_comes_from_the_buffer_at_full_size(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("needs ffmpeg")
        source = self.folder / "buffer.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=6",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
        asked = []

        class Reply(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def urlopen(url, timeout=None):
            asked.append(url)
            return Reply(source.read_bytes())

        self.app_.streams = {"Driveway": "driveway"}
        with mock.patch.object(server, "PLAYBACK_URL", "http://127.0.0.1:9996"), \
                mock.patch.object(server.urllib.request, "urlopen", urlopen):
            media = self.app_.alert_media(self.rule)
        self.assertIn("path=driveway", asked[0])
        self.assertIn("format=mp4", asked[0])
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width", "-of", "csv=p=0", "-"],
                               input=media["clip"], capture_output=True)
        self.assertEqual(probe.stdout.decode().strip(), "h264,1280")

    def test_a_clip_path_stays_inside_the_clips_folder(self):
        (self.folder / "clips").mkdir()
        (self.folder / "clips" / "a.mp4").write_bytes(b"x")
        (self.folder / "rule.py").write_text("x")
        self.assertIsNotNone(server.rule_clip(self.folder, "clips/a.mp4"))
        self.assertIsNone(server.rule_clip(self.folder, "rule.py"))
        self.assertIsNone(server.rule_clip(self.folder, "clips/../rule.py"))


class Replies:
    """Stands in for a real rule, and keeps what the server answers."""

    def __init__(self, folder=None, sensors=()):
        self.id = "r"
        self.name = "fake"
        self.folder = folder
        self.pointed = None
        self.paused = False
        self.sensors = list(sensors)
        self.replies = []
        self.calls = deque()
        self.galleries = deque()
        self.controls = deque()
        self.lit = set()
        self.allow_call = lambda: None
        self.allow_gallery = lambda: server.Rule.allow_gallery(self)
        self.allow_control = lambda: server.Rule.allow_control(self)
        self.reply = self.replies.append


class Blocks(TempLogs):
    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.app_ = self.app(self.client)
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.rule = Replies(self.folder, [server.Sensor({"id": "s", "question": "q", "outline": "bag"})])
        self.name = self.app_.evidence.save_frame(1.0, b"jpeg")
        self.app_.timeline.append(server.Frame(1.0, None, self.name, 3.0))

    def call(self, name, **args):
        self.app_._rule_call(self.rule, {"id": 7, "name": name, "args": args})
        return self.rule.replies[-1]

    def test_a_rule_can_ask_a_model_in_words(self):
        reply = self.call("ask", question="What is it?", frames=[self.name])
        self.assertEqual(reply["id"], 7)
        self.assertEqual(reply["value"]["answer"], "a phone")
        self.assertEqual(self.client.looked, [("What is it?", 1, "spark")])

    def test_a_fallback_call_counts_as_wonder_spend(self):
        self.call("ask", question="Sure?", frames=[self.name], model="wonder")
        self.assertEqual(self.client.looked[0][2], "wonder")
        self.assertEqual((self.app_.writer_usd, self.app_.spark_usd), (0.01, 0.0))

    def test_each_step_stays_with_its_event_and_reaches_the_events_list(self):
        check = self.app_.evidence.save_check({"rule": "r", "sensor": "s"})
        self.call("ask", question="What is it?", frames=[self.name], check=check)
        step = self.app_.evidence.check(check)["steps"][0]
        self.assertEqual((step["model"], step["answer"], step["confidence"]), ("spark", "a phone", 0.5))
        self.assertIn("a phone", [i for i in self.app_.feed if i["kind"] == "step"][0]["text"])

    def test_rule_code_cannot_read_a_file_through_a_frame_name(self):
        for name in ("../../.env", "/etc/passwd", "../checks.jsonl"):
            self.assertIn("error", self.call("ask", question="q", frames=[name]))
            self.assertIn("error", self.call("find", thing="key", frame=name))
        self.assertEqual(self.client.looked, [])

    def test_rule_code_cannot_show_a_file_outside_its_folder(self):
        secret = self.folder.parent / "secret.jpg"
        secret.write_bytes(b"x")
        self.addCleanup(secret.unlink)
        (self.folder / "link.jpg").symlink_to(secret)
        (self.folder / "note.txt").write_text("x")
        for path in ("../secret.jpg", "link.jpg", "note.txt", "/etc/passwd"):
            self.assertIsNone(server.rule_picture(self.folder, path))
        self.app_.image(self.rule, "../secret.jpg")
        self.assertEqual(self.app_.feed[-1]["kind"], "error")

    def test_a_picture_in_the_folder_reaches_the_events_list(self):
        (self.folder / "crop.png").write_bytes(b"x")
        self.app_.image(self.rule, "crop.png", "The face")
        item = self.app_.feed[-1]
        self.assertEqual((item["kind"], item["text"], item["url"]), ("image", "The face", "/rule-file/r/crop.png"))

    def test_frames_reach_the_rule_as_files_it_can_open(self):
        reply = self.call("frames", t=1.0, before=1, after=0, count=4)
        frame = reply["value"][0]
        self.assertEqual((frame["name"], frame["t"]), (self.name, 1.0))
        self.assertEqual((self.folder / frame["path"]).read_bytes(), b"jpeg")

    def test_frames_cover_the_chosen_time_and_count(self):
        for i in range(20):
            self.app_.timeline.append(server.Frame(10.0 + i, None, f"{i:07d}.jpg", 0.0))
        reply = self.call("frames", t=15.0, before=2, after=2, count=3)
        self.assertEqual([f["t"] for f in reply["value"]], [13.0, 15.0, 17.0])

    def test_a_late_job_still_gets_the_frames_of_its_event(self):
        self.app_.frames = deque(maxlen=3)
        self.app_.active_page = None
        for i in range(10):
            self.app_.frame(bytes([65 + i]), 50.0 + i)
        self.assertEqual(len(self.app_.frames), 3)
        reply = self.call("frames", t=52.0, before=0, count=1)
        self.assertEqual(reply["value"][0]["t"], 52.0)
        self.assertEqual((self.folder / reply["value"][0]["path"]).read_bytes(), b"C")

    def test_moments_with_movement_come_first(self):
        for i in range(30):
            self.app_.timeline.append(server.Frame(100.0 + i, None, f"{i:07d}.jpg", 50.0 if i in (3, 4, 5) else 0.0))
        reply = self.call("frames", t=130.0, before=30, count=3)
        self.assertEqual([f["t"] for f in reply["value"]], [103.0, 104.0, 105.0])

    def test_a_still_picture_still_gives_the_newest_frame(self):
        reply = self.call("frames", t=500.0, before=1)
        self.assertEqual(len(reply["value"]), 1)

    def test_waiting_for_later_frames_keeps_the_page_capturing(self):
        self.call("frames", t=time.time(), after=5)
        self.assertTrue(self.app_.status()["hold_capture"])

    def test_find_returns_the_most_certain_box_first(self):
        self.client.see = lambda thing, jpeg, lane="live": [
            {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "w": 2, "h": 2, "c": 0.4},
            {"x1": 1, "y1": 1, "x2": 2, "y2": 2, "w": 2, "h": 2, "c": 0.9},
        ]
        reply = self.call("find", thing="bag", frame=self.name)
        self.assertEqual([b["confidence"] for b in reply["value"]], [0.9, 0.4])

    def test_a_failed_call_is_an_answer_not_a_hang(self):
        def fail(question, frames, model):
            raise ApiError("Meta API did not answer in time.")
        self.client.look = fail
        self.assertIn("did not answer", self.call("ask", question="q", frames=[self.name])["error"])

    def test_an_unknown_block_is_an_answer_not_a_hang(self):
        self.assertIn("error", self.call("delete_everything"))

    def test_the_spend_limit_stops_rule_calls(self):
        self.app_.writer_usd = server.SPEND_LIMIT_USD
        self.assertIn("spend limit", self.call("ask", question="q", frames=[self.name])["error"])
        self.assertEqual(self.client.looked, [])

    def test_a_rule_cannot_call_models_without_limit(self):
        rule = server.Rule(self.app_, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        for _ in range(server.MAX_CALLS_PER_MINUTE):
            rule.allow_call()
        with self.assertRaises(ValueError):
            rule.allow_call()

    def test_a_rule_can_choose_the_frame_and_the_thing_to_outline(self):
        self.client.matches = [segment(5, 15)]
        other = self.app_.evidence.save_frame(2.0, b"other")
        self.app_.draw(self.rule, "Phone in bag", frame=other, target="phone")
        item = [i for i in self.app_.feed if i["kind"] == "draw"][0]
        self.assertTrue(item["frame"].endswith(other))
        self.assertEqual(self.client.found, [("phone", b"other")])

    def test_a_bad_frame_name_falls_back_to_a_real_frame(self):
        self.app_.draw(self.rule, "text", frame="../../.env")
        item = [i for i in self.app_.feed if i["kind"] == "draw"][0]
        self.assertTrue(item["frame"].endswith(self.name))

    def test_a_rule_waiting_for_a_model_still_gets_ticks(self):
        rule = server.Rule(self.app_, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        sent = []
        rule.send = sent.append
        self.app_.entries["r"] = rule
        self.app_.last_frame = time.time()
        self.app_.tick()
        self.assertEqual([m["type"] for m in sent], ["tick"])


class Checks(TempLogs):
    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.app_ = self.app(self.client)
        self.rule = Replies(None, [server.Sensor({"id": "s", "question": "q"})])
        self.names = [self.app_.evidence.save_frame(float(i), bytes([65 + i])) for i in range(3)]

    def check(self, **args):
        self.app_._rule_call(self.rule, {"id": 1, "name": "check", "args": {"claim": "A clear face", **args}})
        return self.rule.replies[-1]

    def test_each_frame_is_judged_alone_and_the_best_comes_first(self):
        self.client.verdicts = {b"A": (False, 0.9), b"B": (True, 0.6), b"C": (True, 0.95)}
        reply = self.check(frames=self.names)
        self.assertEqual(len(self.client.verified), 3)
        self.assertEqual([item["name"] for item in reply["value"]][0], self.names[2])
        self.assertEqual([item["passed"] for item in reply["value"]], [True, True, False])

    def test_the_fallback_model_and_the_reference_reach_the_check(self):
        self.check(frames=self.names[:1], model="wonder", reference=self.names[1])
        self.assertEqual(self.client.verified[0], ("A clear face", b"A", "wonder", b"B"))
        self.assertEqual(self.app_.writer_usd, 0.002)

    def test_a_rule_cannot_read_a_file_through_a_check(self):
        self.assertIn("error", self.check(frames=["../../.env"]))
        self.assertEqual(self.client.verified, [])

    def test_each_frame_counts_against_the_call_limit(self):
        rule = server.Rule(self.app_, "r", "s", {"sensors": [{"id": "s", "question": "q"}]}, None)
        replies = []
        rule.reply = replies.append
        rule.calls.extend([time.time()] * (server.MAX_CALLS_PER_MINUTE - 1))
        self.app_._rule_call(rule, {"id": 1, "name": "check", "args": {"claim": "c", "frames": self.names}})
        self.assertIn("error", replies[0])

    def test_a_check_uses_the_sharp_copy_when_it_exists(self):
        self.app_.evidence.save_sharp(self.names[0], b"sharp picture")
        self.check(frames=self.names[:1])
        self.assertEqual(self.client.verified[0][1], b"sharp picture")

    def test_the_result_stays_with_the_event(self):
        check = self.app_.evidence.save_check({"rule": "r", "sensor": "s"})
        self.client.verdicts = {b"A": (True, 0.9)}
        self.check(frames=self.names, check=check)
        step = self.app_.evidence.check(check)["steps"][0]
        self.assertIn("1 of 3 frames passed", step["answer"])


class SharpFrames(TempLogs):
    def test_a_sharp_copy_reaches_a_rule_but_sensors_keep_the_small_frame(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        rule = Replies(folder, [])
        app.claim("p")
        name = app.frame(b"small", 1.0, "p")["name"]
        self.assertTrue(app.frame_sharp(name, b"large", "p"))
        self.assertEqual(app.evidence.read_frame(name), b"small")
        shared = app._share(rule, list(app.frames))
        self.assertEqual((folder / shared[0]["path"]).read_bytes(), b"large")

    def test_a_late_sharp_copy_replaces_the_small_copy_in_the_rule_folder(self):
        app = self.app(FakeClient())
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        rule = Replies(folder, [])
        app.claim("p")
        name = app.frame(b"small", 1.0, "p")["name"]
        app._share(rule, list(app.frames))
        app.frame_sharp(name, b"large", "p")
        shared = app._share(rule, list(app.frames))
        self.assertEqual((folder / shared[0]["path"]).read_bytes(), b"large")

    def test_only_the_active_page_can_add_a_copy(self):
        app = self.app(FakeClient())
        app.claim("p")
        name = app.frame(b"small", 1.0, "p")["name"]
        self.assertFalse(app.frame_sharp(name, b"large", "other"))

    def test_a_copy_cannot_write_outside_the_log_folder(self):
        app = self.app(FakeClient())
        self.assertFalse(app.evidence.save_sharp("../../rule.py", b"x"))

    def test_a_frame_with_no_copy_still_works(self):
        app = self.app(FakeClient())
        app.claim("p")
        name = app.frame(b"small", 1.0, "p")["name"]
        self.assertEqual(app.evidence.read_sharp(name), b"small")


class Stages(TempLogs):
    def rule(self, app, **fields):
        rule = FakeRule(app, starts_off=True, **fields)
        rule.send = rule.sent.append
        return rule, rule.sensors[0]

    def test_a_sleeping_sensor_costs_nothing(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule, _ = self.rule(app)
        app.frame(b"a", time.time())
        self.assertEqual(client.asked, [])
        self.assertEqual(rule.sent, [])

    def test_a_sleeping_sensor_does_not_raise_the_capture_rate(self):
        app = self.app(FakeClient())
        self.rule(app, frames_per_second=10)
        self.assertEqual(app.capture_fps(), server.DEFAULT_CAPTURE_FPS)

    def test_an_armed_sensor_fires_with_its_own_event(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule, sensor = self.rule(app)
        app._rule_arm(rule, "s", 60)
        self.assertGreater(app.hold_until, time.time())
        app.frame(b"a", time.time() + 0.1)
        app.frame(b"a", time.time() + 1.6)
        self.assertEqual(rule.sent[0]["type"], "on")
        self.assertTrue(rule.sent[0]["event"]["frames"])

    def test_a_sensor_sleeps_again_after_its_time(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule, sensor = self.rule(app)
        app._rule_arm(rule, "s", 1)
        app.frame(b"a", time.time() + 5)
        self.assertEqual(client.asked, [])

    def test_a_new_wait_ignores_older_frames_and_older_state(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule, sensor = self.rule(app)
        app.frames.append(server.Frame(time.time() - 20, b"old", "old.jpg", 0.0))
        sensor.state.on = True
        app._rule_arm(rule, "s", 60)
        self.assertFalse(sensor.state.on)
        app.frame(b"new", time.time() + 0.1)
        self.assertNotIn(b"old", [frame for frames in client.asked for frame in frames])

    def test_a_wait_can_cover_the_frames_since_the_event(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule, sensor = self.rule(app, frames_per_check=8)
        now = time.time()
        app.frames.append(server.Frame(now - 30, b"before", "a.jpg", 0.0))
        app.frames.append(server.Frame(now - 5, b"during", "b.jpg", 0.0))
        app._rule_arm(rule, "s", 60, since=now - 10)
        app.frame(b"new", now + 0.1)
        seen = [frame for frames in client.asked for frame in frames]
        self.assertIn(b"during", seen)
        self.assertNotIn(b"before", seen)

    def test_disarm_stops_the_wait(self):
        client = FakeClient(answer=True)
        app = self.app(client)
        rule, _ = self.rule(app)
        app._rule_arm(rule, "s", 60)
        app._rule_disarm(rule, "s")
        app.frame(b"a", time.time() + 0.1)
        self.assertEqual(client.asked, [])

    def test_an_always_on_sensor_is_never_asleep(self):
        self.assertTrue(server.Sensor({"id": "s"}).awake(1e12))

    def test_an_unknown_sensor_is_an_answer_not_a_hang(self):
        app = self.app(FakeClient())
        rule = Replies(None, [server.Sensor({"id": "s"})])
        app._rule_call(rule, {"id": 1, "name": "arm", "args": {"sensor": "nope"}})
        self.assertIn("no sensor", rule.replies[0]["error"])

    def test_the_wait_time_has_a_limit(self):
        app = self.app(FakeClient())
        rule, sensor = self.rule(app)
        app._rule_arm(rule, "s", 99999)
        self.assertLessEqual(sensor.armed_until, time.time() + server.MAX_ARM_S)


class FakeWorkerRule(server.Rule):
    def start(self):
        self.started = True


class Editing(TempLogs):
    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base)
        self.app_ = self.app(FakeClient())
        self.old_folder = self.base / "old"
        self.old_folder.mkdir()
        (self.old_folder / "rule.json").write_text(json.dumps({"sentence": "old words", "sensors": []}))
        self.old = FakeWorkerRule(self.app_, "old", "old words",
                                  {"name": "Old", "sensors": [{"id": "s", "question": "q"}]}, self.old_folder,
                                  {"noun": "bottle", "box": segment(1, 2)})
        self.stopped = []
        self.old.stop = lambda: self.stopped.append(True)
        self.app_.entries["old"] = self.old
        self.calls = []

    def run_edit(self, result=None, error=None, **kwargs):
        def write(client, folder, sentence, example, idle, progress, motions, gap_s, pointed, previous, cameras,
                  actions="", journal=""):
            self.calls.append({"sentence": sentence, "pointed": pointed, "previous": previous, "folder": folder,
                               "example": example})
            if error:
                raise error
            return result or {"name": "New", "sensors": [{"id": "s", "question": "q"}], "plan": []}, 0.1

        def copy(source, rule_id):
            target = self.base / rule_id
            shutil.copytree(source, target)
            return target
        job = {"id": "j", "status": "working", "steps": [], "error": ""}
        with mock.patch.object(server.writer, "write_rule", write), \
                mock.patch.object(server.sandbox, "copy_rule", copy), \
                mock.patch.object(server, "Rule", FakeWorkerRule):
            self.app_._write(job, "new words", kwargs.get("example", []), [], None, None, kwargs.get("point"), self.old)
        return job

    def test_the_new_rule_replaces_the_old_one(self):
        job = self.run_edit()
        self.assertEqual(job["status"], "done")
        self.assertNotIn("old", self.app_.entries)
        self.assertEqual(self.app_.entries[job["rule"]].sentence, "new words")
        self.assertEqual(self.stopped, [True])

    def test_wonder_sees_the_old_rule_and_the_new_words(self):
        self.run_edit()
        self.assertEqual(self.calls[0]["sentence"], "new words")
        self.assertEqual(self.calls[0]["previous"]["sentence"], "old words")

    def test_a_failed_edit_leaves_the_old_rule_running(self):
        job = self.run_edit(error=server.writer.WriterError("Wonder did not finish the rule."))
        self.assertEqual(job["status"], "failed")
        self.assertIs(self.app_.entries["old"], self.old)
        self.assertEqual(self.stopped, [])

    def test_the_pointed_object_stays_after_an_edit(self):
        job = self.run_edit()
        self.assertEqual(self.app_.entries[job["rule"]].pointed["noun"], "bottle")

    def test_an_old_recording_still_tests_the_new_rule(self):
        server.save_recording(self.old_folder, [b"a", b"b"], [b"c"], [1.0, 2.0], 0.1)
        self.run_edit()
        self.assertEqual(self.calls[0]["example"], [b"a", b"b"])

    def test_a_new_recording_replaces_the_old_one(self):
        server.save_recording(self.old_folder, [b"a", b"b"], [b"c"], [1.0, 2.0], 0.1)
        self.run_edit(example=[b"new"])
        self.assertEqual(self.calls[0]["example"], [b"new"])
        self.assertEqual(server.load_recording(self.calls[0]["folder"])[0], [b"new"])

    def test_an_edit_works_at_the_rule_limit(self):
        for index in range(server.MAX_RULES):
            self.app_.entries[f"r{index}"] = self.old
        with mock.patch.object(threading.Thread, "start"):
            self.assertTrue(self.app_.start_job("new", [], [], edit="old"))

    def test_a_missing_rule_cannot_be_edited(self):
        with self.assertRaises(server.writer.WriterError):
            self.app_.start_job("new", [], [], edit="gone")


class EventDetail(TempLogs):
    def test_a_rule_gets_the_frames_and_what_the_sensor_saw(self):
        app = self.app(FakeClient(answer=True))
        rule = FakeRule(app, frames_per_check=2)
        app.frame(b"a", 1.0)
        app.frame(b"b", 2.0)
        detail = rule.sent[0]["event"]
        self.assertEqual(detail["note"], "test")
        self.assertEqual(detail["proof"], detail["frames"][0]["name"])
        self.assertEqual(detail["frames"][0]["t"], 2.0)


class FakeVoice:
    def __init__(self, key):
        self.id = "v"
        self.key = key
        self.chunks = []

    def audio(self, chunk):
        self.chunks.append(chunk)
        return "count"

    def finish(self):
        return "Count my drinks."


class Voice(TempLogs):
    def test_audio_reaches_the_session_and_the_end_returns_the_words(self):
        app = self.app(FakeClient())
        app.open_voice = FakeVoice
        voice_id = app.start_voice()
        self.assertEqual(app.voice_audio(voice_id, b"pcm"), "count")
        self.assertEqual(app.end_voice(voice_id), "Count my drinks.")
        self.assertEqual(app.voices, {})

    def test_an_unknown_session_is_harmless(self):
        app = self.app(FakeClient())
        self.assertEqual(app.voice_audio("x", b"pcm"), "")
        self.assertEqual(app.end_voice("x"), "")


if __name__ == "__main__":
    unittest.main()
