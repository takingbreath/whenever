"""What a check's model sees, one card and one message for each occurrence, and the health checks."""

import json
import shutil
import sys
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server
from evidence import Evidence
from PIL import Image
from test_server import FakeClient, FakeControls, FakeRule

CAMERAS = ["Driveway", "Garden", "Side", "Gate wide", "Gate tracker"]
POINTED = {"noun": "gate", "camera": "Gate tracker", "kind": "moving",
           "view_box": {"x1": 300, "y1": 200, "x2": 900, "y2": 680, "w": 1280, "h": 720},
           "also": [{"camera": "Gate wide", "kind": "fixed",
                     "view_box": {"x1": 174, "y1": 96, "x2": 279, "y2": 175, "w": 427, "h": 240}}]}


def wall_jpeg(seed=0):
    picture = Image.new("RGB", (1920, 720), (20 + seed, 30, 40))
    out = BytesIO()
    picture.save(out, "JPEG")
    return out.getvalue()


class FakeChannels:
    def __init__(self):
        self.sent = []
        self.clips = []

    def paired(self):
        return True

    def alert(self, text, title, picture, click, base_url, only, clip, reply, tag, skip):
        self.sent.append({"text": text, "reply": reply, "tag": tag, "skip": skip})
        return [{"id": "telegram", "kind": "telegram", "label": "Telegram: AK", "ok": True, "detail": "",
                 "ref": 100 + len(self.sent)}]

    def add_clip(self, sent, clip, title, text, click=None, picture=None):
        self.clips.append(sent)
        return []


class Base(unittest.TestCase):
    def setUp(self):
        self.logs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.logs)
        self.client = FakeClient(answer=False)
        self.app = server.App(self.client, self.logs, inline=True)
        self.app.controls = FakeControls()
        self.app.controls.set_cameras([{"name": "Gate tracker", "serial": "BD1234567", "moves": True,
                                        "light": False, "guide": "Gate wide"}])
        self.app.cameras = list(CAMERAS)
        self.app.wall = (3, 2)

    def rule(self, question="In Gate wide or Gate tracker, is the gate open?", **fields):
        rule = FakeRule(self.app, **fields)
        rule.pointed = dict(POINTED)
        rule.sentence = "When the gate opens in Gate wide and Gate tracker, alert my phone"
        rule.sensors[0].question = question
        return rule


class Focus(Base):
    def test_a_rule_watches_only_its_cameras(self):
        rule = self.rule()
        self.assertEqual(self.app.focus_cameras(rule), ["Gate wide", "Gate tracker"])

    def test_a_still_camera_gets_a_close_up_and_a_moving_one_does_not(self):
        rule = self.rule()
        panels, left_out = self.app.focus_plan(rule, 100.0)
        self.assertEqual([(name, box is not None) for name, _, box in panels],
                         [("Gate wide", True), ("Gate tracker", False)])
        self.assertEqual(left_out, [])

    def test_a_turning_lens_is_left_out(self):
        rule = self.rule()
        self.app.turned_tiles[4] = 98.0
        panels, left_out = self.app.focus_plan(rule, 100.0)
        self.assertEqual([name for name, _, _ in panels], ["Gate wide"])
        self.assertEqual(left_out, ["Gate tracker"])
        self.assertIn("Gate tracker is left out", self.app.focus_view(rule, (panels, left_out)))
        panels, left_out = self.app.focus_plan(rule, 100.0 + server.TURN_SETTLE_S)
        self.assertEqual(left_out, [])

    def test_the_check_sees_the_focus_picture_and_keeps_it_for_the_why_view(self):
        self.rule()
        self.app.frame(wall_jpeg(), 1.0)
        asked = self.client.asked[-1][0]
        self.assertNotEqual(asked, wall_jpeg())
        size = Image.open(BytesIO(asked)).size
        # Gate wide, its close-up of the gate, and Gate tracker: two columns, two rows.
        self.assertEqual(size, (2 * server.FOCUS_PANEL[0] + 6, 2 * server.FOCUS_PANEL[1] + 6))
        check = next(iter(self.app.evidence.checks.values()))
        self.assertTrue(check["focus"])
        self.assertIsNotNone(self.app.evidence.focus_path(self.app.evidence.session, check["frames"][0]))

    def test_a_rule_for_every_camera_sees_the_whole_wall(self):
        rule = FakeRule(self.app)
        rule.sentence = "When a car comes, alert my phone"
        self.assertIsNone(self.app.focus_plan(rule, 1.0))
        self.app.frame(wall_jpeg(), 1.0)
        self.assertEqual(self.client.asked[-1][0], wall_jpeg())

    def test_a_person_in_another_camera_does_not_count(self):
        rule = self.rule()
        # A box in the middle of the top row is the Garden camera.
        self.assertFalse(self.app.in_focus(rule, {"x1": 700, "y1": 100, "x2": 800, "y2": 200, "w": 1920, "h": 720}))
        self.assertTrue(self.app.in_focus(rule, {"x1": 100, "y1": 500, "x2": 200, "y2": 600, "w": 1920, "h": 720}))

    def test_a_person_seen_by_a_lens_that_looks_away_does_not_count(self):
        rule = self.rule()
        self.app.views[(rule.id, "Gate tracker")] = {"lost_since": 1.0}
        # The middle of the bottom row is Gate tracker.
        self.assertFalse(self.app.in_focus(rule, {"x1": 900, "y1": 500, "x2": 1000, "y2": 600, "w": 1920, "h": 720}, 5.0))
        self.assertTrue(self.app.in_focus(rule, {"x1": 100, "y1": 500, "x2": 200, "y2": 600, "w": 1920, "h": 720}, 5.0))

    def test_a_close_up_stays_inside_the_picture(self):
        picture = Image.new("RGB", (640, 360))
        cut = server.close_up(picture, (0.9, 0.9, 1.0, 1.0))
        self.assertLessEqual(cut.width, 640)
        self.assertLessEqual(cut.height, 360)


class Light(Base):
    """Headlights behind a closed gate made it look open. Before a state of a pointed thing starts, Spark
    compares the thing's close-up now with a moment before."""

    def answers(self, only_light):
        asked = []

        def structured(prompt, schema, frames=(), name="answer", model="spark", max_tokens=1200, timeout=None,
                       lane="analysis"):
            asked.append({"name": name, "prompt": prompt, "frames": list(frames), "lane": lane})
            return {"moved": not only_light, "only_light": only_light, "yes_now": not only_light,
                    "note": "light behind the closed gate"}, 0.002

        self.client.structured = structured
        return asked

    def opening(self, rule):
        self.app.frame(wall_jpeg(1), 1.0)
        self.client.answer = True
        for i in range(3):
            self.app.frame(wall_jpeg(2 + i), 10.0 + 2 * i)
        return [m for m in rule.sent if m["type"] == "on"]

    def test_only_light_on_the_gate_is_no_opening(self):
        rule = self.rule()
        asked = self.answers(only_light=True)
        self.assertEqual(self.opening(rule), [])
        self.assertTrue(asked)
        self.assertEqual({a["name"] for a in asked}, {"light"})
        self.assertEqual(asked[0]["lane"], "live")
        self.assertIn("A rule watches the gate: In Gate wide or Gate tracker, is the gate open?", asked[0]["prompt"])
        # Gate wide only: a moving lens is not compared. Before on the left, now on the right.
        size = Image.open(BytesIO(asked[0]["frames"][0])).size
        self.assertEqual(size, (2 * server.FOCUS_PANEL[0] + 6, server.FOCUS_PANEL[1]))
        vetoed = [c for c in self.app.evidence.checks.values() if c.get("light")]
        self.assertTrue(vetoed)
        self.assertFalse(vetoed[0]["hit"])

    def test_a_gate_that_really_opened_is_an_opening(self):
        rule = self.rule()
        asked = self.answers(only_light=False)
        self.assertEqual(len(self.opening(rule)), 1)
        # Each yes while the gate counts as closed is compared, the first one too: two checks can run at
        # once, and the second does not know of the first yet.
        self.assertEqual(len(asked), 2)

    def test_a_gate_that_did_not_move_and_is_closed_is_no_opening(self):
        rule = self.rule()
        self.client.structured = lambda *a, **k: (
            {"moved": False, "only_light": False, "yes_now": False, "note": "closed in both"}, 0.002)
        self.assertEqual(self.opening(rule), [])

    def test_a_gate_that_stayed_open_is_not_vetoed(self):
        rule = self.rule()
        self.client.structured = lambda *a, **k: (
            {"moved": False, "only_light": False, "yes_now": True, "note": "open in both"}, 0.002)
        self.assertEqual(len(self.opening(rule)), 1)

    def test_a_failed_comparison_never_stops_detection(self):
        rule = self.rule()

        def broken(*args, **kwargs):
            raise server.ApiError("Meta API did not answer in time.")

        self.client.structured = broken
        self.assertEqual(len(self.opening(rule)), 1)

    def test_a_review_that_saw_only_light_starts_nothing(self):
        rule = self.rule()
        asked = self.answers(only_light=True)
        for i in range(13):
            self.app.frame(wall_jpeg(i), 1.0 + 2 * i)
        self.client.reviews = [(True, 0.95)]
        self.client.doers = [[{"who": "", "best_frame": 6, "last_frame": 8}]]
        self.app._read_recent(rule, rule.sensors[0], 25.0)
        self.assertEqual([m for m in rule.sent if m["type"] == "on"], [])
        self.assertTrue(asked)
        review = [c for c in self.app.evidence.checks.values() if c.get("review")][-1]["review"]
        self.assertFalse(review["passed"])
        self.assertTrue(review["light"]["only_light"])

    def test_a_review_of_a_real_opening_starts_it(self):
        rule = self.rule()
        self.answers(only_light=False)
        for i in range(13):
            self.app.frame(wall_jpeg(i), 1.0 + 2 * i)
        self.client.reviews = [(True, 0.95)]
        self.client.doers = [[{"who": "", "best_frame": 6, "last_frame": 8}]]
        self.app._read_recent(rule, rule.sensors[0], 25.0)
        self.assertEqual(len([m for m in rule.sent if m["type"] == "on"]), 1)


def textured_wall(mark=False):
    """A wall with texture, as cameras give. mark draws a box in Gate wide's gate, as an opening would."""
    import random
    rng = random.Random(7)
    picture = Image.new("L", (1920, 720))
    picture.putdata([rng.randrange(40, 220) for _ in range(1920 * 720)])
    picture = picture.convert("RGB")
    if mark:
        from PIL import ImageDraw
        ImageDraw.Draw(picture).rectangle((270, 500, 410, 620), fill=(250, 250, 250))
    out = BytesIO()
    picture.save(out, "JPEG", quality=92)
    return out.getvalue()


class Quiet(Base):
    """The gate's place unchanged since the last no gives the same answer, so it costs no call."""

    def setUp(self):
        super().setUp()
        self.still, self.changed = textured_wall(), textured_wall(mark=True)

    def test_an_unchanged_gate_asks_no_model(self):
        self.rule()
        self.app.frame(self.still, 1.0)
        asked = len(self.client.asked)
        for i in range(5):
            self.app.frame(self.still, 3.0 + 2 * i)
        self.assertEqual(len(self.client.asked), asked)
        skipped = [c for c in self.app.evidence.checks.values() if c.get("unchanged")]
        self.assertEqual(len(skipped), 5)

    def test_a_changed_gate_is_asked(self):
        self.rule()
        self.app.frame(self.still, 1.0)
        asked = len(self.client.asked)
        self.app.frame(self.changed, 3.0)
        self.assertEqual(len(self.client.asked), asked + 1)

    def test_the_gate_is_asked_again_after_ten_minutes(self):
        self.rule()
        self.app.frame(self.still, 1.0)
        asked = len(self.client.asked)
        self.app.frame(self.still, 1.0 + server.QUIET_SAFETY_S + 1)
        self.assertEqual(len(self.client.asked), asked + 1)

    def test_after_a_yes_the_next_check_is_asked(self):
        self.rule()
        self.client.structured = lambda *a, **k: ({"moved": True, "only_light": False, "yes_now": True, "note": ""}, 0.002)
        self.app.frame(self.still, 1.0)
        self.client.answer = True
        self.app.frame(self.changed, 3.0)
        asked = len(self.client.asked)
        self.app.frame(self.changed, 5.0)
        self.assertEqual(len(self.client.asked), asked + 1)

    def test_a_sensor_that_a_job_arms_is_always_asked(self):
        rule = self.rule(starts_off=True)
        rule.sensors[0].armed_until = 1e12
        self.app.frame(self.still, 1.0)
        asked = len(self.client.asked)
        self.app.frame(self.still, 3.0)
        self.assertEqual(len(self.client.asked), asked + 1)


def make_video(color, size="640x360", seconds=2):
    import subprocess
    out = Path(tempfile.mkdtemp()) / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={seconds}:r=10",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    data = out.read_bytes()
    shutil.rmtree(out.parent)
    return data


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class Clips(Base):
    """The alert's clip shows the same cameras as its picture, side by side and in the same order."""

    def test_the_clip_stacks_the_pictures_cameras_in_order(self):
        import subprocess
        rule = self.rule()
        rule.folder = Path(self.logs) / "rule"
        rule.folder.mkdir()
        videos = {"Gate tracker": make_video("red", "640x360"), "Gate wide": make_video("blue", "1280x720")}
        asked = []
        self.app._buffer_video = lambda camera, start, duration: asked.append(camera) or videos[camera]
        clip, url = self.app.buffer_clip(rule, ["Gate tracker", "Gate wide"], 1000.0, rule.folder)
        self.assertEqual(asked, ["Gate tracker", "Gate wide"])
        self.assertTrue(url.startswith(f"/rule-file/{rule.id}/clips/"))
        file = rule.folder / "clips" / url.rsplit("/", 1)[1]
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", str(file)], capture_output=True, text=True).stdout.strip()
        # Each camera at the clip height, side by side: 960 + 960 wide.
        self.assertEqual(size, f"{2 * round(server.CLIP_HEIGHT * 16 / 9)},{server.CLIP_HEIGHT}")
        # The left half is the first camera of the picture: red.
        frame = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(file), "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        width = 2 * round(server.CLIP_HEIGHT * 16 / 9)
        row = (server.CLIP_HEIGHT // 2) * width * 3
        left, right = frame[row + 100 * 3: row + 100 * 3 + 3], frame[row + (width - 100) * 3: row + (width - 100) * 3 + 3]
        self.assertGreater(left[0], left[2])
        self.assertGreater(right[2], right[0])

    def test_a_missing_camera_video_falls_back_to_the_saved_frames(self):
        rule = self.rule()
        rule.folder = Path(self.logs) / "rule"
        rule.folder.mkdir()
        self.app._buffer_video = lambda camera, start, duration: None if camera == "Gate wide" else make_video("red")
        self.assertIsNone(self.app.buffer_clip(rule, ["Gate tracker", "Gate wide"], 1000.0, rule.folder))

    def test_the_alert_asks_for_its_pictures_cameras(self):
        rule = self.rule()
        rule.folder = Path(self.logs) / "rule"
        rule.folder.mkdir()
        asked = []
        self.app.buffer_clip = lambda rule, cameras, t, folder: asked.append(list(cameras)) or (b"x", "/clip")
        media = {"cameras": ["Gate tracker", "Gate wide"], "when": 1000.0, "tiles": [4, 3]}
        self.app.alert_clip(rule, media)
        self.assertEqual(asked, [["Gate tracker", "Gate wide"]])
        self.assertEqual(media["clip_url"], "/clip")


class GalleryPlace(Base):
    """A person in the gallery of a gate rule is at the gate: not in another camera, not beyond the wall."""

    def test_near_the_pointed_thing_counts_and_far_does_not(self):
        gate = (174 / 427, 96 / 240, 279 / 427, 175 / 240)
        neighbour = {"x1": 530, "y1": 684, "x2": 617, "y2": 835, "w": 2880, "h": 1620}
        at_the_gate = {"x1": 1300, "y1": 700, "x2": 1500, "y2": 1250, "w": 2880, "h": 1620}
        walking_up = {"x1": 1250, "y1": 1150, "x2": 1500, "y2": 1620, "w": 2880, "h": 1620}
        self.assertFalse(server.near_place(neighbour, gate))
        self.assertTrue(server.near_place(at_the_gate, gate))
        self.assertTrue(server.near_place(walking_up, gate))

    def test_the_gallery_looks_only_at_the_rules_cameras_and_place(self):
        rule = self.rule()
        name = self.app.evidence.save_frame(1.0, wall_jpeg(1))
        for tile in (0, 3, 4):
            self.app.evidence.save_full(name, tile, wall_jpeg(tile))
        seen = []
        neighbour = {"x1": 530, "y1": 684, "x2": 617, "y2": 835, "w": 2880, "h": 1620, "c": 0.9}
        at_the_gate = {"x1": 1300, "y1": 700, "x2": 1500, "y2": 1250, "w": 2880, "h": 1620, "c": 0.9}
        self.client.see = lambda thing, jpeg, lane="live": seen.append(jpeg) or [neighbour, at_the_gate]
        shots = self.app._shots_in("person", name, 1.0, rule)
        # Driveway (tile 0) is not one of the rule's cameras, so it is not even asked.
        self.assertEqual(len(seen), 2)
        cameras = sorted({shot["camera"] for shot in shots})
        self.assertEqual(cameras, [3, 4])
        # In Gate wide, which stays still, only the person at the gate. Gate tracker moves, so both stay.
        self.assertEqual([shot["box"]["x1"] for shot in shots if shot["camera"] == 3], [1300])
        self.assertEqual(len([shot for shot in shots if shot["camera"] == 4]), 2)


def marked_wall(*boxes):
    """The textured wall with white boxes drawn on it, in wall pixels."""
    from PIL import ImageDraw
    picture = Image.open(BytesIO(textured_wall())).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for box in boxes:
        draw.rectangle(box, fill=(250, 250, 250))
    out = BytesIO()
    picture.save(out, "JPEG", quality=92)
    return out.getvalue()


class SpendExtra(unittest.TestCase):
    def test_an_extra_counts_only_on_its_own_day(self):
        from unittest import mock
        with mock.patch.object(server, "SPEND_LIMIT_USD", 10.0), mock.patch.object(server, "SPEND_EXTRA", "2026-11-04:20"):
            self.assertEqual(server.spend_limit("2026-11-04"), 30.0)
            self.assertEqual(server.spend_limit("2026-11-05"), 10.0)
        with mock.patch.object(server, "SPEND_LIMIT_USD", 10.0), mock.patch.object(server, "SPEND_EXTRA", "bad,2026-11-04:x"):
            self.assertEqual(server.spend_limit("2026-11-04"), 10.0)


class WatchPlans(Base):
    """A watch plan is data that Wonder writes for any rule: places, and when a check is paid."""

    def test_the_defaults_keep_each_kind_of_sensor_as_it_was(self):
        state = server.clean_watch(None, "state", "", False)
        self.assertEqual((state["wake"], state["places"]), ("change", [{"camera": "", "find": "", "pointed": True}]))
        self.assertEqual(server.clean_watch(None, "moment", "", False)["wake"], "always")
        self.assertEqual(server.clean_watch(None, "state", "person", False)["wake"], "always")
        self.assertEqual(server.clean_watch(None, "state", "", True)["wake"], "always")
        plan = server.clean_watch({"places": [{"camera": "Gate wide", "find": "table", "pointed": False}] * 20,
                                   "wake": "change", "change": 2, "safety_s": 5}, "moment", "", False)
        self.assertEqual((len(plan["places"]), plan["change"], plan["safety_s"]), (server.MAX_PLACES, 0.99, 60))

    def table_rule(self):
        rule = FakeRule(self.app, question="Is anyone sitting at a table in Gate wide?",
                        watch={"places": [{"camera": "Gate wide", "find": "table", "pointed": False}],
                               "wake": "change", "change": 0.9, "safety_s": 600})
        rule.folder = Path(self.logs) / "rule"
        rule.folder.mkdir()
        # SAM finds one table in Gate wide's own picture, which is 640 x 360 here.
        self.client.matches = [{"x1": 100, "y1": 100, "x2": 300, "y2": 250, "w": 640, "h": 360, "c": 0.9}]
        return rule

    def test_a_place_found_by_name_is_found_once_and_kept(self):
        rule = self.table_rule()
        self.app.frame(textured_wall(), 1.0)
        self.app.frame(textured_wall(), 3.0)
        self.assertEqual(len(self.client.found), 1)
        kept = json.loads((rule.folder / "data" / server.PLACES_FILE).read_text())
        self.assertEqual(kept["Gate wide|table"]["boxes"], [[0.1562, 0.2778, 0.4688, 0.6944]])

    def test_a_change_outside_the_places_is_free_and_inside_is_paid(self):
        self.table_rule()
        self.app.frame(textured_wall(), 1.0)
        asked = len(self.client.asked)
        # Gate wide is the bottom-left camera: its picture starts 360 px down the wall.
        self.app.frame(marked_wall((450, 650, 600, 710)), 3.0)  # far from the table
        self.assertEqual(len(self.client.asked), asked)
        self.app.frame(marked_wall((450, 650, 600, 710), (150, 480, 250, 560)), 5.0)  # on the table
        self.assertEqual(len(self.client.asked), asked + 1)
        held = [c for c in self.app.evidence.checks.values() if c.get("held")]
        self.assertEqual(len(held), 1)
        self.assertGreaterEqual(min(held[0]["places"]), 0.9)

    def test_an_open_state_keeps_its_yes_while_nothing_changes(self):
        rule = self.rule()
        self.client.structured = lambda *a, **k: ({"moved": True, "only_light": False, "yes_now": True, "note": ""}, 0.002)
        still, open_gate = textured_wall(), textured_wall(mark=True)
        self.app.frame(still, 1.0)
        self.client.answer = True
        self.app.frame(open_gate, 3.0)
        self.app.frame(open_gate, 5.0)
        self.assertEqual([m["type"] for m in rule.sent], ["on"])
        asked = len(self.client.asked)
        for i in range(4):
            self.app.frame(open_gate, 7.0 + 2 * i)
        self.assertEqual(len(self.client.asked), asked)
        self.assertTrue(rule.sensors[0].state.on)
        self.client.answer = False
        self.app.frame(still, 20.0)
        self.assertEqual(len(self.client.asked), asked + 1)

    def test_one_false_no_does_not_end_an_open_state(self):
        rule = self.rule()
        self.client.structured = lambda *a, **k: ({"moved": True, "only_light": False, "yes_now": True, "note": ""}, 0.002)
        still, open_gate = textured_wall(), textured_wall(mark=True)
        self.app.frame(still, 1.0)
        self.client.answer = True
        self.app.frame(open_gate, 3.0)
        self.app.frame(open_gate, 5.0)
        self.assertTrue(rule.sensors[0].state.on)
        # Someone moves at the open gate, so this check is paid, and Spark gives one false no.
        moved = marked_wall((261, 504, 340, 622))
        self.client.answers = [False]
        self.app.frame(moved, 6.0)
        asked = len(self.client.asked)
        self.app.frame(moved, 7.0)
        # The no does not fit the open state, so the next check is paid, and its yes keeps the state on.
        self.assertEqual(len(self.client.asked), asked + 1)
        self.assertTrue(rule.sensors[0].state.on)

    def test_a_moment_gets_no_while_nothing_moves_where_it_happens(self):
        rule = FakeRule(self.app, question="Does someone knock on the door in Gate wide?", pattern="moment",
                        min_motion=0, watch={"places": [{"camera": "Gate wide", "find": "", "pointed": False}],
                                             "wake": "change", "change": 0.9, "safety_s": 600})
        self.client.answer = False
        self.app.frame(textured_wall(), 1.0, motion=5.0)
        self.client.answer = True
        asked = len(self.client.asked)
        self.app.frame(textured_wall(), 3.0, motion=5.0)
        self.assertEqual(len(self.client.asked), asked)
        self.assertEqual([m for m in rule.sent if m["type"] == "on"], [])


class Occurrences(Base):
    def fire(self, t0=1.0):
        rule = self.rule()
        self.client.answer = True
        self.app.frame(wall_jpeg(1), t0)
        self.app.frame(wall_jpeg(2), t0 + 1)
        return rule

    def rows(self, kind):
        return [r for r in self.app.journal.history(kind)]

    def test_the_event_names_its_own_occurrence_and_the_end_belongs_to_it(self):
        rule = self.fire()
        event = self.rows("event")[0]
        self.assertEqual(event["values"]["occurrence"], event["values"]["check"])
        self.client.answer = False
        for t in (3.0, 4.0, 5.0, 6.0):
            self.app.frame(wall_jpeg(3), t)
        end = self.rows("end")[0]
        self.assertEqual(end["values"]["occurrence"], event["values"]["occurrence"])
        self.assertEqual(rule.fired, 1)

    def test_what_an_armed_sensor_sees_is_a_step_of_the_event_that_armed_it(self):
        rule = self.fire()
        parent = self.rows("event")[0]["values"]["occurrence"]
        helper = server.Sensor({"id": "person", "target": "person", "starts_off": True, "check_every_s": 1.0})
        rule.sensors.append(helper)
        self.app._rule_arm(rule, "person", 60, check=parent)
        # A person in Gate wide, the first tile of the bottom row.
        self.client.see = lambda target, jpeg, lane="live": [
            {"x1": 100, "y1": 500, "x2": 200, "y2": 700, "w": 1920, "h": 720, "c": 0.95}]
        now = time.time()
        self.app.frame(wall_jpeg(4), now)
        self.app.frame(wall_jpeg(5), now + 1.6)
        steps = [r for r in self.rows("event") if r["values"].get("step")]
        self.assertTrue(steps)
        self.assertEqual(steps[0]["values"]["occurrence"], parent)
        self.assertEqual(rule.fired, 1)

    def test_a_later_note_finds_its_occurrence_by_time(self):
        rule = self.fire(10.0)
        occurrence = self.rows("event")[0]["values"]["occurrence"]
        self.assertEqual(self.app.occurrence_of(rule, at=11.0), occurrence)
        self.app.occ_events.clear()
        self.assertEqual(self.app.occurrence_of(rule, at=11.0), occurrence)
        self.assertIsNone(self.app.occurrence_of(rule, at=500.0))


class OneMessage(Base):
    def setUp(self):
        super().setUp()
        self.app.channels = FakeChannels()
        self.rule_ = self.rule()
        self.media = {"picture": b"jpeg", "url": None, "cameras": ["Gate wide"]}

    def test_the_first_alert_goes_at_once_and_email_waits_for_the_clip(self):
        places, how = self.app._deliver(self.rule_, "occ1", "The gate opened", "Gate", dict(self.media))
        self.assertEqual(how, "sent")
        self.assertEqual(self.app.channels.sent[0]["skip"], ("email",))
        self.assertEqual(self.app.channels.sent[0]["tag"], "occ1")
        self.assertEqual(self.app.channels.clips, [{"telegram": 101}])

    def test_a_new_picture_of_the_same_occurrence_answers_the_first_message(self):
        self.app._deliver(self.rule_, "occ1", "The gate opened", "Gate", dict(self.media))
        _, how = self.app._deliver(self.rule_, "occ1", "The person", "Gate", dict(self.media), news=True)
        self.assertEqual(how, "reply")
        self.assertEqual(self.app.channels.sent[1]["reply"], {"telegram": 101})

    def test_a_note_with_nothing_new_stays_on_the_page(self):
        self.app._deliver(self.rule_, "occ1", "The gate opened", "Gate", dict(self.media))
        places, how = self.app._deliver(self.rule_, "occ1", "No clear picture", "Gate", dict(self.media), news=False)
        self.assertEqual((places, how), ([], "here"))
        self.assertEqual(len(self.app.channels.sent), 1)

    def test_the_same_words_about_one_occurrence_go_out_once(self):
        self.assertFalse(self.app.repeated("occ1", "A person was pictured at the gate."))
        self.assertTrue(self.app.repeated("occ1", "A person was pictured at the gate. "))
        self.assertFalse(self.app.repeated("occ2", "A person was pictured at the gate."))
        self.assertFalse(self.app.repeated(None, "Anything"))

    def test_another_occurrence_is_another_message(self):
        self.app._deliver(self.rule_, "occ1", "The gate opened", "Gate", dict(self.media))
        _, how = self.app._deliver(self.rule_, "occ2", "The gate opened", "Gate", dict(self.media), news=False)
        self.assertEqual(how, "sent")


class OldProof(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def test_a_check_of_an_older_session_opens_by_its_name_or_by_its_time(self):
        started = time.time()
        old = Evidence(self.root)
        name = old.save_frame(1.0, b"jpeg")
        old_id = old.save_check({"t": time.time(), "proof_frame": name})
        old.add_step(old_id, {"act": "Phone"})
        time.sleep(2.1)
        new = Evidence(self.root)
        found = new.check(old_id)
        self.assertEqual((found["id"], found["steps"]), (old_id, [{"act": "Phone"}]))
        self.assertEqual(new.frame_path(old.session, name).read_bytes(), b"jpeg")
        # A name from before sessions were in names: the time finds the session.
        bare = old_id.split("-")[0]
        with (old.folder / "checks.jsonl").open("a") as f:
            f.write(json.dumps({"id": bare, "session": old.session, "t": 1.0}) + "\n")
        new.old.clear()
        self.assertIsNone(new.check(bare))
        self.assertEqual(new.check(bare, t=started + 0.2)["id"], bare)


class Health(Base):
    def test_the_daily_spend_survives_a_restart(self):
        folder = Path(self.logs)
        app = server.App(self.client, self.logs, inline=True, phone_file=folder / "phone.json")
        app.spend_file = folder / "spend.json"
        app.spark_usd = 1.25
        app._save_spend()
        again = server.App(self.client, self.logs, inline=True, phone_file=folder / "phone.json")
        again.spend_file = folder / "spend.json"
        again._load_spend()
        self.assertAlmostEqual(again.spark_usd, 1.25)

    def test_a_lens_that_lost_the_thing_is_turned_back(self):
        rule = self.rule()
        self.app.frame(wall_jpeg(), time.time())
        self.client.boxes = 0
        for _ in range(server.VIEW_MISSES):
            self.app._check_view(rule, "Gate tracker")
        self.assertTrue(self.app.view_lost(rule, "Gate tracker"))
        self.assertEqual(self.app.controls.commands[-1][:2], ("aim", "BD1234567"))
        status = self.app.rule_status(rule)
        self.assertEqual(status["level"], "warn")
        self.assertIn("Gate tracker does not show the gate", status["text"])
        # Its picture is left out of the checks until it shows the gate again.
        self.assertEqual(self.app.focus_plan(rule, time.time())[1], ["Gate tracker"])
        self.client.boxes = 1
        self.app._check_view(rule, "Gate tracker")
        self.assertFalse(self.app.view_lost(rule, "Gate tracker"))

    def test_a_lens_that_did_not_turn_is_looked_at_only_now_and_then(self):
        rule = self.rule()
        rule.paused = False
        looked = []
        self.app._check_view = lambda rule_, name: looked.append(name)
        self.app._later = lambda work, *args: work(*args)
        now = time.time()
        self.app.views[(rule.id, "Gate tracker")] = {"checked": now - 120}
        self.app._watch_views()
        self.assertEqual(looked, [])
        self.app.turned_tiles[4] = now - 60
        self.app._watch_views()
        self.assertEqual(looked, ["Gate tracker"])
        self.app.views[(rule.id, "Gate tracker")] = {"checked": now - server.VIEW_SAFETY_S - 1}
        self.app.turned_tiles.clear()
        self.app._watch_views()
        self.assertEqual(looked, ["Gate tracker", "Gate tracker"])

    def test_cameras_that_stop_are_a_problem(self):
        self.app.last_frame = time.time() - server.CAMERA_LOST_S - 5
        self.app._watch_cameras()
        titles = [g["title"] for g in self.app.problems.public()["groups"]]
        self.assertIn("The cameras stopped sending pictures", titles)

    def test_a_reader_that_keeps_starting_is_a_problem(self):
        for _ in range(server.RESTARTS_WARN):
            self.app.claim("security-1", cameras=["Gate wide"])
        self.app.last_frame = time.time()
        self.app._watch_cameras()
        titles = [g["title"] for g in self.app.problems.public()["groups"]]
        self.assertIn("The camera reader keeps restarting", titles)


if __name__ == "__main__":
    unittest.main()
