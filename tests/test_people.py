"""Tagged people: tag once, know them again by face across days and by look within a day, learn from sure
sightings, and the blocks and alerts that rules use with them."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import people
from channels import Channels
from rule_runtime import Context, Frame, Found


def face_of(identity, wobble=0.0, seed=0):
    """A unit vector for one identity: its own axis, a little off for another picture of the same face."""
    rng = np.random.default_rng(seed)
    vector = np.zeros(512, np.float32)
    vector[identity] = 1.0
    vector += rng.normal(0, wobble, 512).astype(np.float32)
    return vector / np.linalg.norm(vector)


class FakeFaces:
    """A picture's top-left pixel says whose face it shows (0 for none), and its second pixel how far from
    that person's usual face it is, in hundredths."""
    kind = "arcface"

    def find(self, picture, box=None):
        return {"row": [0.0] * 15, "size": 40.0, "score": 0.9} if picture[0, 0, 0] else None

    def vector(self, picture, face):
        return face_of(int(picture[0, 0, 0]), picture[0, 1, 0] / 100, seed=int(picture[0, 2, 0]))

    def crop(self, picture, face, size=112):
        return np.zeros((size, size, 3), np.uint8)


class FakeLooks:
    def available(self):
        return True

    def images(self, pictures):
        # The look of a picture is its colour: one person's clothes.
        colour = np.asarray(pictures[0].convert("RGB"))[192, 192].astype(np.float32)
        vector = np.zeros(512, np.float32)
        vector[300 + int(colour[1]) % 200] = 1.0
        return vector[None]


def picture(identity=0, wobble=0, seed=0, clothes=1):
    pic = np.zeros((64, 64, 3), np.uint8)
    pic[:, :, 1] = clothes
    pic[0, 0, 0], pic[0, 1, 0], pic[0, 2, 0] = identity, wobble, seed
    return pic


class People(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.now = 1000.0
        self.book = people.People(self.folder, FakeLooks(), FakeFaces(), clock=lambda: self.now)

    def who(self, pic):
        return self.book.match(self.book.read(pic, None))

    def test_a_tagged_face_is_known_on_another_day_and_a_stranger_is_not(self):
        self.book.tag("Ravi", picture(7))
        self.now += 14 * 86400
        self.assertEqual(self.who(picture(7, wobble=3, seed=1))["name"], "Ravi")
        self.assertEqual(self.who(picture(7, wobble=3, seed=1))["by"], "face")
        self.assertIsNone(self.who(picture(9)))

    def test_a_clear_strange_face_is_never_named_by_clothes(self):
        self.book.tag("Ravi", picture(7, clothes=40))
        self.assertIsNone(self.who(picture(9, clothes=40)))

    def test_a_look_names_a_person_only_within_the_day(self):
        self.book.tag("Ravi", picture(7, clothes=40))
        back = picture(0, clothes=40)   # the same clothes, the face turned away
        self.assertEqual(self.who(back)["by"], "look")
        self.now += (people.LOOK_HOURS + 1) * 3600
        self.assertIsNone(self.who(back))

    def test_two_people_in_the_same_clothes_leave_a_look_undecided(self):
        self.book.tag("Ravi", picture(7, clothes=40))
        self.book.tag("Asha", picture(8, clothes=40))
        self.assertIsNone(self.who(picture(0, clothes=40)))

    def test_a_sure_sighting_teaches_a_new_face_and_sightings_are_kept(self):
        self.book.tag("Ravi", picture(7))
        new = picture(7, wobble=2, seed=5)
        found = self.book.read(new, None)
        match = self.book.match(found)
        self.book.saw(match, found, "Door")
        person = self.book.public()[0]
        self.assertEqual((person["faces"], person["times_seen"], person["seen"]["camera"]), (2, 1, "Door"))
        kept = json.loads((Path(self.folder) / "people.json").read_text())
        self.assertEqual(kept["people"][0]["name"], "Ravi")

    def test_sightings_teach_at_most_one_face_a_minute(self):
        self.book.tag("Ravi", picture(7))
        for seed in range(5):
            found = self.book.read(picture(7, wobble=2, seed=10 + seed), None)
            self.book.saw(self.book.match(found), found, "Door")
            self.now += 10
        self.assertEqual(self.book.public()[0]["faces"], 2)

    def test_a_second_tag_adds_to_the_same_person_and_renaming_and_forgetting_work(self):
        first = self.book.tag("Ravi", picture(7))
        again = self.book.tag("ravi", picture(7, wobble=4, seed=2))
        self.assertEqual((first["id"], again["faces"]), (again["id"], 2))
        self.book.rename(first["id"], name="Ravi K", note="Likes the window table")
        self.assertEqual(self.book.public()[0]["name"], "Ravi K")
        self.assertTrue(self.book.remove(first["id"]))
        self.assertEqual(self.book.public(), [])
        faceless = people.People(self.folder, None, FakeFaces())   # no look model either
        with self.assertRaises(ValueError):
            faceless.tag("Nobody", picture(0))


class RuleBlocks(unittest.TestCase):
    def test_who_names_people_and_keeps_the_name_on_the_track(self):
        ctx = Context()
        a = Found(Frame("f1", 1.0), "Door", "person", 0.9, (0.1, 0.1, 0.3, 0.9), id=4)
        ctx.matches = [{"id": "p1", "name": "Ravi", "score": 0.6, "by": "face"}]
        self.assertEqual([f.who["name"] for f in ctx.who([a])], ["Ravi"])
        later = Found(Frame("f2", 3.0), "Door", "person", 0.9, (0.12, 0.1, 0.32, 0.9), id=4)
        ctx.matches = [None]   # the face turned away
        ctx.who([later])
        self.assertEqual((later.who["name"], later.who["by"]), ("Ravi", "track"))

    def test_the_worker_sends_who_and_tag(self):
        sent = []

        def call(block, **args):
            sent.append((block, args))
            if block == "who":
                return [{"id": "p1", "name": "Ravi", "score": 0.5, "by": "face", "face": True, "face_size": 30}]
            return {"id": "p1", "name": args.get("name")}

        ctx = Context(call=call)
        person = Found(Frame("f1", 1.0), "Door", "person", 0.9, (0.1, 0.1, 0.3, 0.9), id=1)
        ctx.who([person], look=False)
        ctx.tag(person, "Asha")
        self.assertEqual(sent[0][1]["items"], [{"frame": "f1", "camera": "Door", "box": [0.1, 0.1, 0.3, 0.9]}])
        self.assertEqual((sent[0][1]["look"], sent[1][1]["name"]), (False, "Asha"))


class NamedPlaces(unittest.TestCase):
    def test_a_place_can_be_named_and_found_by_its_name(self):
        channels = Channels()
        channels.places["email"].append({"id": "e1", "address": "owner@example.com", "t": 1})
        channels.places["email"].append({"id": "e2", "address": "floor@example.com", "t": 1})
        channels.rename("e2", "Waiter Asha")
        self.assertEqual(channels.named("waiter"), ["e2"])
        self.assertEqual([p["label"] for p in channels.all()], ["owner@example.com", "Waiter Asha"])
        channels.rename("e2", "")
        self.assertEqual(channels.named("waiter"), [])


class Alerts(unittest.TestCase):
    """ctx.notify(to=...) goes only to the places with that name, as its own message."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import server
        from test_server import FakeClient, FakeRule
        self.logs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.logs)
        self.app = server.App(FakeClient(), self.logs, inline=True)
        self.rule = FakeRule(self.app)
        self.sent = []
        self.app.channels.paired = lambda: True
        self.app.channels.named = lambda words: ["p2"] if "waiter" in words.lower() else []
        self.app.channels.add_clip = lambda *a, **k: []
        self.app.alert_clip = lambda *a, **k: None

        def send_alert(rule, text, title=None, picture=None, only=None, **kw):
            self.sent.append((text, only))
            return [{"id": "p2", "label": "Waiter", "ok": True, "kind": "push"}]
        self.app.send_alert = send_alert

    def test_to_a_named_place_and_to_a_missing_name(self):
        media = {"picture": None}
        self.app._deliver(self.rule, None, "Table 3 calls for staff", "Whenever", media, True, to="Waiter")
        self.assertEqual(self.sent[-1], ("Table 3 calls for staff", ["p2"]))
        self.app._deliver(self.rule, None, "Nobody came to table 3", "Whenever", media, True, to="Manager")
        self.assertEqual(self.sent[-1], ("Nobody came to table 3", None))   # to every place, and a problem
        self.assertTrue(any("Manager" in g["title"] for g in self.app.problems.open()))


@unittest.skipUnless(people.available(), "the face models are not here")
class RealFaces(unittest.TestCase):
    def test_no_face_in_a_plain_picture(self):
        faces = people.Faces()
        self.assertIsNone(faces.find(np.full((240, 320, 3), 128, np.uint8)))


if __name__ == "__main__":
    unittest.main()
