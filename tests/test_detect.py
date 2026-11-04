"""The local detector and the tools around it: places, geometry, numbers across looks, steady counts,
timers, a check of one place, and a watch plan that pays only when a count changes."""

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect
import server
from PIL import Image
from rule_runtime import (Context, Frame, Found, Line, Place, Tracker, beside, center, edge, foot, inside, overlap,
                          place_of, share_inside)
from test_occurrences import CAMERAS, Base, marked_wall, textured_wall
from test_server import FakeRule

TABLE = {"x1": 100, "y1": 100, "x2": 300, "y2": 250, "w": 640, "h": 360, "c": 0.9}   # in Gate wide


class Words(unittest.TestCase):
    def test_plain_words_become_the_detector_names(self):
        self.assertEqual(detect.label_for("people"), "person")
        self.assertEqual(detect.label_for("Tables"), "dining table")
        self.assertEqual(detect.label_for("cars"), "car")
        self.assertEqual(detect.label_for("buses"), "bus")
        self.assertEqual(detect.label_for("cell_phone"), "cell phone")
        self.assertIsNone(detect.label_for("waiter's tray"))


class Geometry(unittest.TestCase):
    def test_points_and_overlaps(self):
        box = (0.2, 0.2, 0.4, 0.6)
        self.assertEqual(center(box), (0.30000000000000004, 0.4))
        self.assertEqual(foot({"box": box}), (0.30000000000000004, 0.6))
        self.assertTrue(inside((0.3, 0.3), box))
        self.assertTrue(inside(Found(box=(0.25, 0.25, 0.35, 0.35)), box))
        self.assertAlmostEqual(overlap(box, box), 1.0)
        self.assertEqual(overlap(box, (0.5, 0.5, 0.6, 0.6)), 0.0)
        self.assertAlmostEqual(share_inside((0.3, 0.2, 0.5, 0.6), box), 0.5)

    def test_a_thing_belongs_to_the_smallest_place_of_its_own_camera(self):
        places = [Place("room", "Hall", (0, 0, 1, 1)), Place("table 1", "Hall", (0.2, 0.2, 0.5, 0.5)),
                  Place("table 2", "Patio", (0.2, 0.2, 0.5, 0.5))]
        self.assertEqual(place_of(Found(camera="Hall", box=(0.3, 0.3, 0.4, 0.4)), places), "table 1")
        self.assertEqual(place_of(Found(camera="Hall", box=(0.7, 0.7, 0.8, 0.8)), places), "room")
        self.assertEqual(place_of(Found(camera="Patio", box=(0.3, 0.3, 0.4, 0.4)), places), "table 2")
        self.assertIsNone(place_of(Found(camera="Patio", box=(0.7, 0.7, 0.8, 0.8)), places))
        # A person who stands keeps their feet on the floor: by foot, the place is where they stand.
        self.assertEqual(place_of((0.3, 0.1, 0.4, 0.45), {"door": (0.25, 0.4, 0.45, 0.5)}, point=foot), "door")

    def test_a_person_on_a_chair_beside_a_table_counts_at_the_nearest_table(self):
        tables = {"table 1": (0.1, 0.4, 0.4, 0.6), "table 2": (0.45, 0.4, 0.75, 0.6)}
        person = (0.2, 0.35, 0.3, 0.66)   # feet 0.06 below table 1, which is 0.3 wide
        self.assertIsNone(place_of(person, tables, point=foot))
        self.assertEqual(place_of(person, tables, point=foot, near=0.3), "table 1")
        self.assertIsNone(place_of((0.2, 0.35, 0.3, 0.8), tables, point=foot, near=0.3))


class Numbers(unittest.TestCase):
    def look(self, tracker, t, *boxes, camera="Hall"):
        found = [Found(camera=camera, label="person", box=box) for box in boxes]
        tracker.number(found, t)
        return [f.id for f in found]

    def test_a_thing_that_stays_keeps_its_number(self):
        tracker = Tracker()
        first = self.look(tracker, 1.0, (0.1, 0.1, 0.2, 0.3), (0.6, 0.1, 0.7, 0.3))
        second = self.look(tracker, 3.0, (0.61, 0.11, 0.71, 0.31), (0.11, 0.1, 0.21, 0.3), (0.4, 0.5, 0.5, 0.7))
        self.assertEqual(second[:2], [first[1], first[0]])
        self.assertNotIn(second[2], first)
        # The same box in another camera is another thing.
        self.assertNotIn(self.look(tracker, 3.0, (0.1, 0.1, 0.2, 0.3), camera="Patio")[0], first)

    def test_a_thing_that_stays_put_gets_its_time_there(self):
        tracker = Tracker()
        seated = [Found(camera="Hall", label="person", box=(0.1, 0.1, 0.2, 0.3)) for _ in range(3)]
        walker = [Found(camera="Hall", label="person", box=(0.5 + 0.04 * i, 0.1, 0.6 + 0.04 * i, 0.3)) for i in range(3)]
        for i in range(3):
            tracker.number([seated[i], walker[i]], 1.0 + 2 * i)
        self.assertEqual((seated[2].stayed, len({f.id for f in seated})), (4.0, 1))
        self.assertEqual((walker[2].stayed, len({f.id for f in walker})), (0.0, 1))

    def test_a_long_gap_gives_a_new_number_and_a_look_back_changes_nothing(self):
        tracker = Tracker(max_gap_s=10)
        first = self.look(tracker, 1.0, (0.1, 0.1, 0.2, 0.3))[0]
        self.assertEqual(self.look(tracker, 0.5, (0.1, 0.1, 0.2, 0.3), (0.5, 0.5, 0.6, 0.6)), [first, None])
        self.assertNotEqual(self.look(tracker, 30.0, (0.1, 0.1, 0.2, 0.3))[0], first)


class RuleTools(unittest.TestCase):
    def test_detect_sets_places_and_numbers_in_a_test(self):
        ctx = Context()
        ctx.place_list = [{"name": "table 1", "camera": "Hall", "box": (0.2, 0.2, 0.5, 0.5)}]
        ctx.detected = [{"camera": "Hall", "label": "person", "confidence": 0.8, "box": (0.3, 0.3, 0.4, 0.4)},
                        {"camera": "Hall", "label": "person", "confidence": 0.7, "box": (0.7, 0.7, 0.8, 0.8)}]
        places = ctx.places("table")
        found = ctx.detect("people", places=places)
        self.assertEqual([f.place for f in found], ["table 1", None])
        self.assertEqual(len({f.id for f in found}), 2)

    def test_a_count_must_repeat_before_it_counts(self):
        ctx = Context()
        self.assertEqual([ctx.steady("t1", n, looks=3) for n in (2, 2, 3, 2, 2, 2, 3, 3, 3)],
                         [None, None, None, None, None, 2, 2, 2, 3])

    def test_since_counts_unbroken_seconds_and_keeps_them_in_state(self):
        ctx = Context()
        self.assertEqual(ctx.since("t1 waits", True, t=100), 0.0)
        self.assertEqual(ctx.since("t1 waits", True, t=160), 60.0)
        self.assertEqual(Context(state=json.loads(json.dumps(ctx.state))).since("t1 waits", True, t=190), 90.0)
        self.assertEqual(ctx.since("t1 waits", False, t=200), 0.0)
        self.assertEqual(ctx.since("t1 waits", True, t=210), 0.0)

    def test_the_worker_sends_detect_places_and_a_place_check(self):
        sent = []

        def call(block, **args):
            sent.append((block, args))
            if block == "frames":
                return [{"name": "a.jpg", "t": 5.0, "motion": 0.0, "path": "a.jpg"}]
            if block == "detect":
                return [{"frame": "a.jpg", "camera": "Hall", "label": "person", "confidence": 0.9,
                         "box": [0.3, 0.3, 0.4, 0.4]}]
            if block == "places":
                return [{"name": "table 1", "camera": "Hall", "box": [0.2, 0.2, 0.5, 0.5]}]
            return [{"name": "a.jpg", "passed": True, "confidence": 0.9, "seen": "a plate"}]

        ctx = Context(call=call)
        frames = ctx.frames(count=1)
        tables = ctx.places("table")
        found = ctx.detect(["people"], frames, places=tables)
        self.assertEqual((found[0].place, found[0].frame.name, found[0].id), ("table 1", "a.jpg", 1))
        ctx.check("A plate is on this table.", frames, place=tables[0])
        self.assertEqual(sent[-1][1]["place"], {"camera": "Hall", "box": [0.2, 0.2, 0.5, 0.5]})
        self.assertEqual(sent[2][1]["things"], ["people"])


class Zones(unittest.TestCase):
    def test_a_zone_beside_a_place_and_an_edge_for_a_line(self):
        counter = Place("counter", "Hall", (0.4, 0.2, 0.6, 0.3))
        queue = beside(counter, "below", 2.0)
        self.assertEqual((queue.name, queue.camera), ("below counter", "Hall"))
        self.assertEqual(tuple(round(v, 3) for v in queue.box), (0.4, 0.3, 0.6, 0.5))
        self.assertEqual(beside(counter, "left", 3.0).box[0], 0.0)   # it stays in the picture
        self.assertEqual(edge(counter, "bottom"), ((0.4, 0.3), (0.6, 0.3)))


class Crossings(unittest.TestCase):
    """People who walk through a doorway, a few looks a second."""

    DOOR = Line("door", "Hall", (0.4, 0.5), (0.6, 0.5), inside=(0.5, 0.9))

    def walk(self, ctx, steps, start=0.0, x=0.5, every=0.5):
        """One person's feet at these heights, one look each, and their box above them."""
        found = []
        for i, y in enumerate(steps):
            item = Found(Frame(f"f{start + i}", start + i * every), "Hall", "person", 0.9, (x - 0.03, y - 0.25, x + 0.03, y))
            ctx.tracker.number([item], item.frame.t)
            found.append(item)
        return found

    def test_one_walk_in_and_one_walk_out_count_once_each(self):
        ctx = Context()
        crossed = ctx.crossings(self.DOOR, self.walk(ctx, [0.3, 0.4, 0.47, 0.55, 0.65]))
        self.assertEqual([c.direction for c in crossed], ["in"])
        crossed = ctx.crossings(self.DOOR, self.walk(ctx, [0.7, 0.6, 0.52, 0.45, 0.35], start=10.0))
        self.assertEqual([c.direction for c in crossed], ["out"])

    def test_standing_on_the_line_does_not_count_again_and_again(self):
        ctx = Context()
        crossed = ctx.crossings(self.DOOR, self.walk(ctx, [0.45, 0.51, 0.49, 0.515, 0.485, 0.51]))
        self.assertEqual(crossed, [])

    def test_beside_the_door_or_a_repeated_call_does_not_count(self):
        ctx = Context()
        self.assertEqual(ctx.crossings(self.DOOR, self.walk(ctx, [0.3, 0.45, 0.6, 0.7], x=0.8)), [])
        ctx = Context()
        walk = self.walk(ctx, [0.3, 0.42, 0.55, 0.62])
        self.assertEqual(len(ctx.crossings(self.DOOR, walk[:3])), 1)
        self.assertEqual(ctx.crossings(self.DOOR, walk), [])   # the same frames again

    def test_a_person_who_shrinks_as_they_walk_away_keeps_one_number(self):
        tracker = Tracker()
        boxes = [(0.5, 0.56, 0.57, 0.87), (0.53, 0.49, 0.59, 0.77), (0.51, 0.49, 0.55, 0.72),
                 (0.46, 0.45, 0.51, 0.67), (0.43, 0.43, 0.47, 0.64)]   # real boxes from a gate at night
        found = [Found(camera="Gate", label="person", box=box) for box in boxes]
        for i, item in enumerate(found):
            tracker.number([item], i * 0.6)
        self.assertEqual(len({item.id for item in found}), 1)


class Visits(unittest.TestCase):
    def test_a_visit_starts_survives_a_short_gap_and_ends(self):
        ctx = Context()
        self.assertIsNone(ctx.visit("table 1", False, t=0))
        self.assertEqual(ctx.visit("table 1", True, t=10), {"kind": "start", "key": "table 1", "t": 10.0, "empty_s": None})
        self.assertIsNone(ctx.visit("table 1", False, t=20))   # a waiter hides the guests
        self.assertIsNone(ctx.visit("table 1", True, t=25))
        self.assertIsNone(ctx.visit("table 1", False, t=40))
        ended = ctx.visit("table 1", False, t=60, gone_s=30)
        self.assertEqual(ended, {"kind": "end", "key": "table 1", "start": 10.0, "end": 25.0, "seconds": 15.0})
        later = Context(state=json.loads(json.dumps(ctx.state)))   # after a restart
        self.assertEqual(later.visit("table 1", True, t=85)["empty_s"], 60.0)


class Bodies(unittest.TestCase):
    """Flags from body points, in pixels, as RTMPose gives them."""

    def body(self, **moved):
        points = {"nose": (100, 40), "left shoulder": (90, 80), "right shoulder": (110, 80),
                  "left wrist": (85, 150), "right wrist": (115, 150), "left hip": (92, 160), "right hip": (108, 160),
                  "left knee": (92, 240), "right knee": (108, 240), "left ankle": (92, 320), "right ankle": (108, 320)}
        points.update(moved)
        return [(*points[name], 0.9) if name in points else (0, 0, 0.0) for name in detect.KEYPOINTS]

    def test_standing_sitting_lying_and_a_raised_hand(self):
        self.assertEqual(detect.body_flags(self.body()), {"hand_up": False, "lying": False, "sitting": False})
        self.assertTrue(detect.body_flags(self.body(**{"left knee": (60, 170), "right knee": (80, 172)}))["sitting"])
        self.assertTrue(detect.body_flags(self.body(**{"right wrist": (118, 20)}))["hand_up"])
        lying = self.body(**{"left shoulder": (40, 200), "right shoulder": (40, 215), "left hip": (120, 205),
                             "right hip": (120, 218)})
        self.assertTrue(detect.body_flags(lying)["lying"])

    def test_hidden_knees_leave_sitting_unknown(self):
        hidden = [point if name not in ("left knee", "right knee") else (0, 0, 0.1)
                  for name, point in zip(detect.KEYPOINTS, self.body())]
        self.assertIsNone(detect.body_flags(hidden)["sitting"])


def fake_detector(seen):
    """Stands in for YOLOX: returns the boxes in seen, as from 0 to 1 in the picture it gets."""
    pictures = []

    def run(picture, things=None, min_confidence=0.35, region=None):
        pictures.append(picture.size)
        return [{"label": "person", "confidence": 0.9, "box": tuple(box), "tall": 2.5} for box in seen]
    return run, pictures


class ServerBlocks(Base):
    def setUp(self):
        super().setUp()
        self.seen = []
        run, self.pictures = fake_detector(self.seen)
        for patcher in (mock.patch.object(detect, "available", lambda: True), mock.patch.object(detect, "detect", run)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.replies = []
        self.target = FakeRule(self.app)
        self.target.reply = self.replies.append
        self.target.allow_call = lambda: None
        self.target.folder = Path(self.logs) / "rule"
        self.target.folder.mkdir()

    def call(self, name, **args):
        self.app._rule_call(self.target, {"id": 3, "name": name, "args": args})
        return self.replies[-1]

    def frame(self, t=1.0):
        self.app.frame(textured_wall(), t)
        return self.app.timeline[-1].name

    def test_detect_reads_each_camera_and_costs_nothing(self):
        name = self.frame()
        self.seen.append((0.4, 0.4, 0.5, 0.7))
        spent = self.app.spend()
        reply = self.call("detect", things=["people"], frames=[name], camera="Gate wide")
        self.assertEqual(reply["value"], [{"frame": name, "camera": "Gate wide", "label": "person",
                                           "confidence": 0.9, "box": [0.4, 0.4, 0.5, 0.7], "tall": 2.5}])
        self.assertEqual(self.pictures, [(640, 360)])
        self.assertEqual(self.app.spend(), spent)
        everything = self.call("detect", things="person", frames=[name])
        self.assertEqual(len(everything["value"]), len(CAMERAS))

    def test_detect_with_pose_adds_a_body_to_each_person(self):
        name = self.frame()
        self.seen.append((0.4, 0.4, 0.5, 0.7))
        body = {"points": [(0.45, 0.45, 0.9)] * 17, "hand_up": True, "lying": False, "sitting": None}
        with mock.patch.object(detect, "pose_available", lambda: True), \
                mock.patch.object(detect, "poses", lambda picture, boxes: [body] * len(boxes)):
            reply = self.call("detect", things=["person"], frames=[name], camera="Gate wide", pose=True)
        self.assertEqual(reply["value"][0]["pose"]["hand_up"], True)
        with mock.patch.object(detect, "pose_available", lambda: False):
            self.assertIn("not installed", self.call("detect", things=["person"], frames=[name], pose=True)["error"])

    def test_detect_names_what_it_does_not_know_and_works_at_the_spend_limit(self):
        name = self.frame()
        self.assertIn("does not know", self.call("detect", things=["waiter's tray"], frames=[name])["error"])
        with mock.patch.object(self.app, "stopped", lambda: True):
            self.assertNotIn("error", self.call("detect", things=["person"], frames=[name]))
            self.assertIn("error", self.call("ask", question="?", frames=[name]))

    def test_places_are_found_once_and_named_by_rows(self):
        self.frame()
        self.client.matches = [{"x1": 400, "y1": 200, "x2": 500, "y2": 300, "w": 640, "h": 360, "c": 0.9},
                               {"x1": 300, "y1": 40, "x2": 380, "y2": 100, "w": 640, "h": 360, "c": 0.9},
                               {"x1": 100, "y1": 210, "x2": 200, "y2": 300, "w": 640, "h": 360, "c": 0.9}]
        places = self.call("places", thing="table", camera="Gate wide")["value"]
        self.assertEqual([p["name"] for p in places], ["table 1", "table 2", "table 3"])
        self.assertEqual([round(p["box"][0], 3) for p in places], [0.469, 0.156, 0.625])
        self.call("places", thing="table", camera="Gate wide")
        self.assertEqual(len(self.client.found), 1)
        self.assertIn("error", self.call("places", thing="table", camera="Gate tracker"))  # it moves

    def test_repeated_and_tiny_places_are_left_out(self):
        boxes = [(0.1, 0.1, 0.4, 0.4), (0.1, 0.1, 0.4, 0.25), (0.5, 0.5, 0.8, 0.8), (0.97, 0.96, 1.0, 1.0)]
        self.assertEqual(server.distinct_boxes(boxes), [(0.1, 0.1, 0.4, 0.4), (0.5, 0.5, 0.8, 0.8)])

    def test_places_asked_before_the_first_frame_are_asked_again_later(self):
        self.client.matches = [dict(TABLE)]
        self.assertEqual(self.call("places", thing="table", camera="Gate wide")["value"], [])
        self.frame()
        self.assertEqual(len(self.call("places", thing="table", camera="Gate wide")["value"]), 1)

    def test_a_place_check_sees_only_the_close_up(self):
        name = self.frame()
        reply = self.call("check", claim="A plate is on this table.", frames=[name],
                          place={"camera": "Gate wide", "box": [0.156, 0.278, 0.469, 0.694]})
        self.assertNotIn("error", reply)
        picture = Image.open(BytesIO(self.client.verified[-1][1]))
        fill = max(picture.width / server.FOCUS_PANEL[0], picture.height / server.FOCUS_PANEL[1])
        self.assertAlmostEqual(fill, 1.0, places=2)
        self.assertIn("error", self.call("check", claim="?", frames=[name], place={"camera": "Nowhere", "box": [0, 0, 1, 1]}))


class CountWake(Base):
    """A watch plan that counts pays only when the number of people in a place changes."""

    def setUp(self):
        super().setUp()
        self.seen = []
        run, _ = fake_detector(self.seen)
        for patcher in (mock.patch.object(detect, "available", lambda: True), mock.patch.object(detect, "detect", run)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def table_rule(self):
        rule = FakeRule(self.app, question="Is anyone sitting at a table in Gate wide?",
                        watch={"places": [{"camera": "Gate wide", "find": "table", "pointed": False}],
                               "wake": "count", "count": ["people"], "change": 0.9, "safety_s": 600})
        rule.folder = Path(self.logs) / "rule"
        rule.folder.mkdir()
        self.client.matches = [dict(TABLE)]
        return rule

    def test_the_plan_keeps_known_things_and_defaults_to_people(self):
        plan = server.clean_watch({"wake": "count", "count": ["tables", "unicorn", "car", "cars"]}, "state", "", False)
        self.assertEqual(plan["count"], ["dining table", "car"])
        self.assertEqual(plan["places"], [{"camera": "", "find": "", "pointed": False}])
        self.assertEqual(server.clean_watch({"wake": "count"}, "moment", "", False)["count"], ["person"])
        self.assertEqual(server.clean_watch(None, "state", "", False)["count"], [])

    def test_only_a_new_count_in_the_place_is_paid(self):
        self.table_rule()
        self.app.frame(textured_wall(), 1.0)
        asked = len(self.client.asked)
        # The table's picture changes, and someone walks past far from it: the count at the table stays 0.
        self.seen.append((0.8, 0.1, 0.9, 0.5))
        self.app.frame(marked_wall((150, 480, 250, 560)), 3.0)
        self.assertEqual(len(self.client.asked), asked)
        # Someone sits at the table. The next look must see the new count too before a check is paid.
        self.seen.append((0.25, 0.4, 0.35, 0.6))
        self.app.frame(textured_wall(), 5.0)
        self.assertEqual(len(self.client.asked), asked)
        self.app.frame(textured_wall(), 7.0)
        self.assertEqual(len(self.client.asked), asked + 1)
        self.app.frame(marked_wall((150, 480, 250, 560)), 9.0)
        self.assertEqual(len(self.client.asked), asked + 1)
        held = [c for c in self.app.evidence.checks.values() if c.get("held")]
        self.assertEqual(len(held), 3)

    def test_a_count_in_one_look_only_is_free(self):
        self.table_rule()
        self.app.frame(textured_wall(), 1.0)
        asked = len(self.client.asked)
        self.seen.append((0.3, 0.3, 0.4, 0.75))   # a passer-by, beside the table for one look
        self.app.frame(textured_wall(), 3.0)
        self.seen.clear()
        self.app.frame(textured_wall(), 5.0)
        self.assertEqual(len(self.client.asked), asked)

    def test_a_person_beside_a_table_counts_there_once(self):
        rule = self.table_rule()
        # The table is (0.156, 0.278) to (0.469, 0.694) in Gate wide. The person's feet are just below it.
        self.seen.append((0.2, 0.5, 0.28, 0.75))
        self.app.frame(textured_wall(), 1.0)
        places = self.app.place_now(rule, rule.sensors[0], self.app.timeline[-1])
        self.assertEqual(places, [("Gate wide|table|0", 1)])

    def test_without_the_detector_every_check_is_paid(self):
        self.table_rule()
        with mock.patch.object(detect, "available", lambda: False):
            self.app.frame(textured_wall(), 1.0)
            asked = len(self.client.asked)
            self.app.frame(textured_wall(), 3.0)
            self.assertEqual(len(self.client.asked), asked + 1)


@unittest.skipUnless(detect.available(), "the YOLOX model or onnxruntime is not here")
class RealDetector(unittest.TestCase):
    def test_it_finds_a_drawn_scene_quickly_and_gives_boxes_from_0_to_1(self):
        picture = Image.new("RGB", (1280, 720), (120, 120, 120))
        found = detect.detect(picture)
        for item in found:
            self.assertTrue(all(0 <= v <= 1 for v in item["box"]))
        self.assertEqual(detect.detect(picture, ["person"], region=(0.5, 0.5, 1, 1)), [])


if __name__ == "__main__":
    unittest.main()
