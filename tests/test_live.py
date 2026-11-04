"""The free live analyzer: boxes and timers for the page, tables and their states, names on tracks, the
history of frames, heat, word search, and what it adds to alerts. The detector and the face model are
scripted, so these tests run without models."""

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections import namedtuple
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import live
import people
from test_people import FakeFaces, FakeLooks

Frame = namedtuple("Frame", "t jpeg name motion")
TABLES = [(0.10, 0.50, 0.30, 0.70), (0.60, 0.50, 0.80, 0.70), (0.30, 0.10, 0.50, 0.25)]
GUEST = (0.12, 0.40, 0.20, 0.62)      # sits at the first table: its seat point is on the table
WAITER = (0.21, 0.30, 0.27, 0.68)


def person(box, tall=1.5):
    return {"label": "person", "confidence": 0.9, "box": box, "tall": tall}


def table(box):
    return {"label": "dining table", "confidence": 0.8, "box": box, "tall": 0.5}


class FakeEmbedder:
    """A look is the colour at the middle of the picture; the words "red" and "green" are those colours."""

    def available(self):
        return True

    def images(self, pictures):
        r, g, _ = pictures[0].convert("RGB").getpixel((192, 192))
        vector = np.array([r, g] + [0.0] * 510, np.float32)
        return (vector / np.linalg.norm(vector))[None]

    def text(self, words):
        return np.array(([1.0, 0.0] if "red" in words else [0.0, 1.0]) + [0.0] * 510, np.float32)


class Stub:
    """The parts of the server's App that the analyzer uses."""

    def __init__(self, folder):
        self.cameras = ["Cafe", "Gate"]
        self.lock = threading.Lock()
        self.timeline = []
        self.evidence = SimpleNamespace(frames_dir=Path(folder) / "frames")
        self.entries = {}
        self.embedder = FakeEmbedder()
        self.people = people.People(Path(folder) / "people", FakeLooks(), FakeFaces())
        self.spent = 0.0

    def camera_kind(self, name):
        return "fixed"

    def spend(self):
        return self.spent


def frame_picture(identity=0, colour=(30, 40, 50)):
    """A picture whose top-left pixel tells FakeFaces whose face it shows (in its blue, as OpenCV reads it)."""
    picture = Image.new("RGB", (1280, 720), colour)
    for x, value in ((0, identity), (1, 0), (2, 0)):   # whose face, how far from their usual face, and a seed
        picture.putpixel((x, 0), (0, 0, value))
    return picture


class Analyzer(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.app = Stub(self.folder)
        self.live = live.Analyzer(self.app, Path(self.folder) / "live")
        self.count = 0
        self.bodies = {}
        for name, value in (("TABLE_LOOKS", 3),):
            patch = mock.patch.object(live, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.faces = mock.patch.object(live.people, "available", lambda: False)
        self.faces.start()
        self.addCleanup(self.faces.stop)

    def look(self, t, items, camera="Cafe", picture=None):
        """One look at one camera at time t, with the detector finding items."""
        self.count += 1
        frame = Frame(float(t), None, f"{self.count:07d}.jpg", 0.0)
        self.live.clock = lambda: float(t)
        poses = lambda pic, boxes: [self.bodies.get(tuple(b), {"hand_up": False, "lying": False, "sitting": None})  # noqa: E731
                                    for b in boxes]
        with mock.patch.object(live.detect, "detect", lambda *a, **k: [dict(i) for i in items]), \
                mock.patch.object(live.detect, "poses", poses), \
                mock.patch.object(live.detect, "pose_available", lambda: True):
            self.live.look(camera, frame, picture or frame_picture())
        return self.live.overlay([camera])

    def learn(self):
        for t in range(3):
            self.look(t, [table(b) for b in TABLES] + [table((0.0, 0.0, 0.02, 0.02))])

    def test_tables_are_learned_named_by_rows_and_renamed(self):
        self.learn()
        places = self.live.overlay(["Cafe"])["cameras"]["Cafe"]["places"]
        self.assertEqual([(p["id"], p["name"], p["state"]) for p in places],
                         [("cafe-t1", "T1", "free"), ("cafe-t2", "T2", "free"), ("cafe-t3", "T3", "free")])
        self.assertEqual(places[0]["box"], [0.3, 0.1, 0.5, 0.25])   # the top row comes first
        self.assertEqual(self.live.rename_place("cafe-t2", "Window")["name"], "Window")
        self.assertIsNone(self.live.rename_place("nowhere", "X"))
        again = live.Analyzer(self.app, Path(self.folder) / "live")
        self.live = again
        out = self.look(10, [])
        self.assertEqual([p["name"] for p in out["cameras"]["Cafe"]["places"]], ["T1", "Window", "T3"])
        self.assertEqual(self.live.floor()["places"][1]["camera"], "Cafe")

    def test_a_guest_sits_waits_is_served_calls_and_leaves(self):
        self.learn()
        guest = (0.12, 0.42, 0.20, 0.62)   # sits at T2, the left table of the lower row
        for t in range(10, 680, 10):
            out = self.look(t, [person(guest)])
        cafe = out["cameras"]["Cafe"]
        seated = cafe["people"][0]
        self.assertEqual((seated["place"], seated["stayed_s"]), ("T2", 660))
        self.assertGreaterEqual(seated["seated_s"], 660)
        t2 = cafe["places"][1]
        self.assertEqual((t2["name"], t2["state"], t2["count"]), ("T2", "waiting", 1))
        self.assertTrue(t2["note"].startswith("no staff 1"))
        self.assertEqual(out["totals"]["busy"], 1)
        self.assertEqual(out["events"][0]["text"], "T2 seated")
        # A waiter stands at the table for 2 s: the table was served.
        waiter = (0.21, 0.30, 0.27, 0.70)
        self.bodies[waiter] = {"hand_up": False, "sitting": False}
        for t in (680, 681, 682, 683):
            out = self.look(t, [person(guest), person(waiter, 3.0)])
        self.assertEqual(out["cameras"]["Cafe"]["places"][1]["state"], "busy")
        self.assertEqual((out["totals"]["staff"], out["totals"]["guests"]), (1, 1))
        # The guest raises a hand on two looks.
        self.bodies[guest] = {"hand_up": True, "sitting": True}
        self.look(684, [person(guest)])
        out = self.look(686, [person(guest)])
        self.assertEqual(out["cameras"]["Cafe"]["places"][1]["state"], "help")
        self.assertEqual(out["cameras"]["Cafe"]["people"][0]["hand_up_s"], 2)
        self.assertEqual(self.live.told("Waiter", ["Cafe"], 687), [out["cameras"]["Cafe"]["people"][0]["id"]])
        out = self.look(688, [person(guest)])
        self.assertEqual(out["cameras"]["Cafe"]["people"][0]["told"], "Waiter")
        self.assertEqual([e["kind"] for e in out["events"]][-2:], ["hand_up", "told"])
        # The guest leaves: after 30 s with nobody, the visit ends.
        for t in range(690, 740, 10):
            out = self.look(t, [])
        self.assertEqual(out["cameras"]["Cafe"]["places"][1]["state"], "free")
        self.assertEqual(out["events"][-1]["text"], "T2 left after 11 min")

    def test_the_overlay_has_exactly_the_contracts_fields(self):
        self.learn()
        self.look(5, [person(GUEST), {"label": "car", "confidence": 0.8, "box": (0.5, 0.8, 0.7, 0.95), "tall": 0.6}])
        out = self.look(7, [person(GUEST), {"label": "car", "confidence": 0.8, "box": (0.5, 0.8, 0.7, 0.95), "tall": 0.6}])
        self.assertEqual(set(out), {"t", "cameras", "totals", "events"})
        cafe = out["cameras"]["Cafe"]
        self.assertEqual(set(cafe), {"t", "size", "people", "things", "places"})
        self.assertEqual(cafe["size"], [1280, 720])
        self.assertEqual(set(cafe["people"][0]), {"id", "box", "name", "person_id", "role", "visitor", "visits", "sure",
                                                  "by", "stayed_s", "place", "seated_s", "hand_up_s", "told", "trail"})
        self.assertEqual(cafe["people"][0]["trail"], [[0.16, 0.62], [0.16, 0.62]])
        self.assertEqual(set(cafe["things"][0]), {"id", "label", "box", "stayed_s"})
        self.assertEqual((cafe["things"][0]["label"], cafe["things"][0]["stayed_s"]), ("car", 2))
        self.assertEqual(set(cafe["places"][0]), {"id", "name", "box", "kind", "count", "since_s", "state", "note"})
        self.assertEqual(set(out["totals"]), {"people", "guests", "staff", "busy", "tables", "longest_s", "spend_hour"})
        self.assertEqual(self.live.watched["Cafe"], 7 + live.WATCHED_S)
        self.assertNotIn("Gate", out["cameras"])

    def test_a_face_names_the_whole_track_also_back_in_history(self):
        self.faces.stop()
        self.faces = mock.patch.object(live.people, "available", lambda: True)
        self.faces.start()
        array = np.zeros((64, 64, 3), np.uint8)
        array[0, 0, 0] = 7
        self.app.people.tag("Asha", array, role="regular")
        box = (0.40, 0.30, 0.50, 0.70)
        for t in (1, 2, 3):
            self.look(t, [person(box, 3.0)])                     # turned away: no face
        out = self.look(4, [person(box, 3.0)], picture=frame_picture(7))
        named = out["cameras"]["Cafe"]["people"][0]
        self.assertEqual((named["name"], named["role"], named["by"], named["sure"]), ("Asha", "regular", "face", 0.99))
        out = self.look(12, [person(box, 3.0)])
        self.assertEqual(out["cameras"]["Cafe"]["people"][0]["by"], "track")
        history = self.live.history("Cafe", 0, 20)
        self.assertEqual([[p["name"] for p in f["people"]] for f in history["frames"]], [["Asha"]] * 5)
        self.assertEqual(out["events"][0]["text"], "Asha came in")
        seen = self.live.alert_people(["Cafe"], 12)
        asha = seen["people"][0]
        self.assertEqual((asha["name"], asha["by"], asha["sighting"] is not None), ("Asha", "face", True))
        self.assertEqual((asha["confirmed"], asha["tagged"] is not None), (None, True))
        self.assertTrue(out["events"][0]["picture"].startswith("/live-file/events/"))
        self.assertEqual([e["kind"] for e in asha["evidence"]], ["face", "tracker"])
        self.assertTrue(asha["evidence"][0]["text"].startswith("1 clear look, "))
        self.assertTrue(asha["now_face"].startswith("/live-file/faces/"))
        self.assertEqual(seen["story"][0]["text"], "Asha came in")
        self.assertEqual(self.live.alert_subject(["Cafe"], 12), ("Cafe", tuple(box)))
        today = self.app.people.today(0, 100)
        self.assertEqual((today[0]["name"], today[0]["cameras"]), ("Asha", ["Cafe"]))

    def test_clothes_carry_a_name_to_another_camera_less_surely(self):
        self.faces.stop()
        self.faces = mock.patch.object(live.people, "available", lambda: True)
        self.faces.start()
        array = np.zeros((64, 64, 3), np.uint8)
        array[0, 0, 0] = 7
        self.app.people.tag("Asha", array)
        box = (0.40, 0.30, 0.50, 0.70)
        green = (20, 200, 20)
        for t in range(1, 8):
            self.look(t, [person(box, 3.0)], picture=frame_picture(7, green))
        while self.live.looks:
            self.live.add_look(*self.live.looks.popleft())
        for t in range(40, 48):
            out = self.look(t, [person(box, 3.0)], camera="Gate", picture=frame_picture(0, green))
        while self.live.looks:
            self.live.add_look(*self.live.looks.popleft())
        out = self.look(48, [person(box, 3.0)], camera="Gate", picture=frame_picture(0, green))
        carried = out["cameras"]["Gate"]["people"][0]
        self.assertEqual((carried["name"], carried["by"], carried["sure"]), ("Asha", "look", round(0.99 * 0.8, 3)))
        evidence = self.live.alert_people(["Gate"], 48)["people"][0]["evidence"]
        self.assertEqual([e["kind"] for e in evidence], ["clothes", "tracker"])
        self.assertTrue(evidence[0]["text"].endswith("2 cameras"))

    def test_a_tag_of_clothes_today_names_a_track_by_look(self):
        self.app.embedder = FakeLooks()   # the same look numbers as the tag's
        clothes = np.zeros((64, 64, 3), np.uint8)
        clothes[:, :, 1] = 90             # no face, green clothes
        ravi = self.app.people.tag("Ravi", clothes)
        start = time.time()
        for i in range(7):
            self.look(start + i, [person(GUEST)], picture=frame_picture(0, (0, 90, 0)))
        while self.live.looks:
            self.live.add_look(*self.live.looks.popleft())
        seen = self.look(start + 8, [person(GUEST)], picture=frame_picture(0, (0, 90, 0)))["cameras"]["Cafe"]["people"][0]
        self.assertEqual((seen["name"], seen["by"], seen["sure"]), ("Ravi", "look", round(0.99 * 0.8, 3)))
        self.assertEqual(self.app.people.page(ravi["id"])["sightings"][0]["by"], "look")

    def test_heat_and_a_search_in_words(self):
        red = (220, 10, 10)
        for t in range(1, 8):
            self.look(t, [person(GUEST)], picture=frame_picture(0, red))
        while self.live.looks:
            self.live.add_look(*self.live.looks.popleft())
        heat = self.live.heat("Cafe", 1)
        self.assertEqual((len(heat["grid"]), len(heat["grid"][0])), (18, 32))
        self.assertEqual(heat["grid"][11][5], 6.0)   # the foot point (0.16, 0.62), seen for 6 s
        found = self.live.search("a person in red")
        self.assertEqual(found[0]["camera"], "Cafe")
        self.assertTrue(found[0]["picture"].startswith("/live-file/crops/"))
        self.assertGreater(found[0]["score"], 0.5)
        self.assertLess(self.live.search("green")[0]["score"], 0.2)
        self.assertIsNotNone(self.live.file(found[0]["picture"][len("/live-file/"):]))
        self.assertIsNone(self.live.file("../live.db"))

    def test_old_frames_and_events_are_forgotten(self):
        self.look(1, [person(GUEST)])
        self.live.clock = lambda: 1 + 25 * 3600
        self.live.prune()
        self.assertEqual(self.live.history("Cafe", 0, 1e10)["frames"], [])

    def test_a_demo_with_nobody_watching_reads_nothing(self):
        self.app.idle = lambda: True
        self.app.timeline.append(Frame(1.0, None, "0000001.jpg", 0.0))
        self.live.pool = mock.Mock()
        self.live._schedule()
        self.live.pool.submit.assert_not_called()
        self.app.idle = lambda: False
        self.live._schedule()
        self.assertEqual(self.live.pool.submit.call_count, 2)


class Routes(unittest.TestCase):
    """The routes behind the login, on a real App."""

    def setUp(self):
        import server
        from test_server import FakeClient
        self.server = server
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.client = FakeClient()
        self.app = server.App(self.client, self.folder, inline=True)
        self.app.cameras = ["Cafe"]
        self.app.embedder.available = lambda: False
        access = server.Access(self.folder / "access.env", self.folder / "sessions.json")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (server.Handler,), {"app": self.app, "access": access}))
        self.port = self.httpd.server_address[1]
        patch = mock.patch.object(server, "PORT", self.port)
        patch.start()
        self.addCleanup(patch.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_live_routes_answer(self):
        status, overlay = self.call("GET", "/api/overlay?cameras=Cafe,Nowhere&after=1")
        self.assertEqual((status, overlay["cameras"], overlay["totals"]["tables"]), (200, {}, 0))
        self.assertEqual(self.call("GET", "/api/overlay/history?camera=Cafe&from=0&to=10")[1]["frames"], [])
        self.assertEqual(self.call("GET", "/api/people/today"), (200, {"groups": []}))
        self.assertEqual(self.call("GET", "/api/people/nobody/page")[0], 404)
        self.assertEqual(len(self.call("GET", "/api/heat?camera=Cafe&hours=1")[1]["grid"]), 18)
        self.assertEqual(self.call("GET", "/api/floor"), (200, {"places": []}))
        self.assertEqual(self.call("GET", "/api/people/search?q=red")[0], 422)
        self.assertEqual(self.call("GET", "/live-file/crops/..%2Flive.db")[0], 404)
        state = self.app.status()
        self.assertEqual(set(state["analyzer"]) >= {"cameras", "per_s"}, True)

    def test_people_pages_names_answers_and_merges(self):
        book = people.People(self.folder / "book", FakeLooks(), FakeFaces())
        self.app.people = book
        array = np.zeros((64, 64, 3), np.uint8)
        array[0, 0, 0] = 7
        asha = book.tag("Asha", array)
        array[0, 1, 0], array[0, 2, 0] = 2, 3
        found = book.read(array, None)
        sighting = book.saw(book.match(found), found, "Cafe")
        groups = self.call("GET", "/api/people/today")[1]["groups"]
        self.assertEqual((groups[0]["name"], groups[0]["kind"], groups[0]["count"]), ("Asha", "person", 1))
        self.assertTrue(groups[0]["face"].startswith(f"/people-file/{asha['id']}/face-"))
        page = self.call("GET", f"/api/people/{asha['id']}/page")[1]
        self.assertEqual((page["person"]["name"], page["sightings"][0]["id"]), ("Asha", sighting))
        self.assertEqual(page["visits"][0]["count"], 1)
        status, answer = self.call("POST", f"/api/people/{asha['id']}/confirm", {"sighting": sighting, "yes": True})
        self.assertEqual((status, answer["person"]["faces"]), (200, 2))
        self.assertEqual(self.call("POST", f"/api/people/{asha['id']}", {"role": "staff"})[1]["person"]["role"], "staff")
        array[0, 0, 0], array[0, 1, 0] = 9, 0
        visitor = book.identify(book.read(array, None), "Cafe", clear=True)
        status, named = self.call("POST", "/api/people", {"visitor": visitor["id"], "name": "Ravi", "role": "vip"})
        self.assertEqual((status, named["person"]["name"], named["person"]["role"]), (200, "Ravi", "vip"))
        status, merged = self.call("POST", "/api/people/merge", {"from": named["person"]["id"], "into": asha["id"]})
        self.assertEqual((status, merged["person"]["name"], len(book.items)), (200, "Asha", 1))
        self.assertEqual(self.call("POST", "/api/people/merge", {"from": "x", "into": "y"})[0], 400)

    def test_a_table_is_renamed_through_the_places_route(self):
        self.app.analyzer.places = {"Cafe": {"boxes": [[0.1, 0.5, 0.3, 0.7]], "names": {}}}
        cam = live.Camera("Cafe")
        self.app.analyzer._set_tables(cam, [[0.1, 0.5, 0.3, 0.7]], {})
        self.app.analyzer.cameras["Cafe"] = cam
        status, answer = self.call("POST", "/api/places/cafe-t1/name", {"name": "Window"})
        self.assertEqual((status, answer["place"]["name"]), (200, "Window"))


class Alerts(unittest.TestCase):
    """An alert gets the people the analyzer saw, their story, and a glowing outline of its subject."""

    def setUp(self):
        import server
        from test_server import FakeClient, FakeRule
        self.server = server
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.client = FakeClient()
        self.app = server.App(self.client, self.folder, inline=True)
        self.app.claim("page")
        self.rule = FakeRule(self.app)
        self.app.channels.paired = lambda: False

    def frame(self, t):
        out = BytesIO()
        Image.new("RGB", (640, 360), (60, 60, 60)).save(out, "JPEG")
        name = self.app.frame(out.getvalue(), t, "page")["name"]
        self.app.evidence.save_sharp(name, out.getvalue())
        return name

    def test_a_found_subject_gets_one_sam_outline_and_the_journal_row_gets_people_and_story(self):
        name = self.frame(100.0)
        self.client.marks = [{"x1": 10, "y1": 10, "x2": 60, "y2": 90, "w": 640, "h": 360, "c": 0.9,
                              "path": "M10 10L60 10L60 90L10 90L5 50L10 30L12 20L11 15Z", "mask_w": 640, "mask_h": 360}]
        self.app.analyzer.alert_people = lambda cameras, t: {
            "people": [{"person_id": "p1", "name": "Asha", "role": "", "sure": 0.9, "by": "face", "evidence": [],
                        "tag_face": None, "now_face": None, "sighting": "s1"}],
            "story": [{"t": 99.0, "text": "T3 hand up", "picture": None}]}
        told = []
        self.app.analyzer.told = lambda to, cameras, t: told.append(to)
        before = self.app.see_calls
        self.app._notify(self.rule, "T3 calls for staff", name, to="Waiter",
                         subject={"camera": "", "box": [0.02, 0.03, 0.09, 0.25]})
        self.assertEqual(self.app.see_calls - before, 1)
        self.assertEqual(self.client.marked, [{"x1": 13, "y1": 11, "x2": 58, "y2": 90}])
        self.assertEqual(told, ["Waiter"])
        row = [r for r in self.app.journal.history("notify")][-1]
        self.assertEqual(row["values"]["people"][0]["name"], "Asha")
        self.assertEqual(row["values"]["story"][0]["text"], "T3 hand up")
        alert = next(item for item in self.app.feed if item["kind"] == "alert")
        self.assertEqual((alert["people"][0]["name"], alert["story"][0]["text"]), ("Asha", "T3 hand up"))
        self.assertIn("cameras", alert)

    def test_a_clip_of_saved_frames_starts_at_its_first_frame(self):
        for t in (96.0, 97.5, 99.0, 101.0):
            self.frame(t)
        self.assertEqual(self.app.clip_start(100.0), 96.0)
        self.assertIsNone(self.app.clip_start(500.0))

    def test_the_glow_draws_from_a_mask_or_a_box(self):
        picture = Image.new("RGB", (200, 100), (0, 0, 0))
        self.server.glow(picture, ("box", (0.4, 0.3, 0.6, 0.7)))
        self.assertGreater(picture.getpixel((80, 50))[0], 100)     # the frame's left edge, with room
        picture = Image.new("RGB", (200, 100), (0, 0, 0))
        polygons = self.server.svg_polygons("M10 10L60 10L60 90L10 90L5 50L10 30L12 20L11 15Z", 200, 100)
        self.assertEqual(len(polygons[0]), 8)
        self.server.glow(picture, ("mask", polygons))
        self.assertGreater(picture.getpixel((35, 10))[0], 100)
        # SAM's mask covers only its object's box, so its points are placed inside that box.
        inside = self.server.svg_polygons("M10 10L60 10L60 90L10 90L5 50L10 30L12 20L11 15Z", 100, 100, (0.5, 0.5, 1, 1))
        self.assertEqual(inside[0][0], (0.55, 0.55))

    def test_paused_spending_draws_the_box_without_sam(self):
        self.app.stopped = lambda: True
        self.assertEqual(self.app.alert_outline("x.jpg", 0, (0.1, 0.1, 0.2, 0.2)), ("box", (0.1, 0.1, 0.2, 0.2)))
        self.assertEqual(self.client.marked, [])

    def test_journal_values_fit_by_dropping_the_story_first(self):
        values = {"story": [{"text": "x" * 500}] * 20, "people": [{"name": "Asha", "evidence": [{"text": "y" * 100}] * 3}]}
        fitted = self.server.fit_values(values, 2000)
        self.assertLessEqual(len(json.dumps(fitted)), 2000)
        self.assertEqual(fitted["people"][0]["name"], "Asha")


if __name__ == "__main__":
    unittest.main()
