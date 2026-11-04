"""Ask: plan a question, answer it from the journal, the live picture or the saved footage."""

import shutil
import sys
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ask
import search
import server
from PIL import Image, ImageDraw
from test_server import FakeClient, FakeRule

CAMERAS = ["Driveway", "Garden", "Side", "Gate wide", "Gate tracker"]


def wall(color=(40, 40, 40), mark=None):
    """A 3x2 wall. mark draws a white square in Gate wide's tile, where a pointed thing would be."""
    picture = Image.new("RGB", (1920, 720), color)
    if mark:
        draw = ImageDraw.Draw(picture)
        draw.rectangle((100, 460, 260, 620), fill=(240, 240, 240))
        draw.line((100, 460, 260, 620), fill=(0, 0, 0), width=8)
    out = BytesIO()
    picture.save(out, "JPEG")
    return out.getvalue()


class PlanClient(FakeClient):
    """Answers structured calls in order: first the plan, then sheets or the words."""

    def __init__(self, plan, sheets=(), words="It happened.", pair=None):
        super().__init__()
        self.plan = plan
        self.sheets = list(sheets)
        self.words = words
        self.pairs = list(pair) if isinstance(pair, list) else [pair or {"first_has_it": False, "second_has_it": True,
                                                                         "note": "a box appeared"}]
        self.prompts = []

    def structured(self, prompt, schema, frames=(), name="answer", model="spark", max_tokens=1200, timeout=None):
        self.prompts.append((name, prompt, len(frames)))
        if name == "plan":
            base = {"kind": "past", "rule": "", "event": "", "cameras": [], "since_minutes_ago": 0,
                    "until_minutes_ago": 0, "thing": "", "claim": "", "pace": "medium", "answer_as": "what",
                    "which": "all", "watch": "", "unclear": ""}
            return {**base, **self.plan}, 0.001
        if name == "sheet":
            sheet = self.sheets.pop(0) if self.sheets else {}
            return {"hits": [], "closer": [], "sure": 1, "notes": [], **sheet}, 0.004
        if name == "pair":
            return (self.pairs.pop(0) if len(self.pairs) > 1 else self.pairs[0]), 0.003
        return {"answer": self.words, "sure": 0.9}, 0.001


class Off:
    def available(self):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.logs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.logs)

    def app(self, client):
        app = server.App(client, self.logs, inline=True)
        app.cameras = list(CAMERAS)
        app.wall = (3, 2)
        # The real PE-Core model is slow in a test and changes the order. A test that ranks sets its own.
        app.embedder = Off()
        return app

    def wait(self, app, job_id):
        for _ in range(200):
            job = app.asks.get(job_id)
            if job and job["status"] != "working":
                return job
            time.sleep(0.02)
        self.fail("The question did not finish.")


class Plans(Base):
    def test_a_plan_keeps_only_real_cameras_and_a_real_span(self):
        now = time.time()
        plan = ask.clean_plan({"kind": "past", "cameras": ["Gate wide", "Moon"], "since_minutes_ago": 60,
                               "until_minutes_ago": 0, "pace": "warp"}, CAMERAS, now, ask.clean_context({}), None)
        self.assertEqual(plan["cameras"], ["Gate wide"])
        self.assertAlmostEqual(plan["since"], now - 3600)
        self.assertEqual(plan["pace"], "medium")

    def test_a_question_about_an_alert_looks_at_that_alert(self):
        context = ask.clean_context({"occurrence": {"id": "c1", "rule": "Gate", "t": 1000.0, "end": 1100.0}})
        plan = ask.clean_plan({"kind": "past"}, CAMERAS, 5000.0, context, None)
        self.assertEqual((plan["since"], plan["until"]), (940.0, 1220.0))

    def test_a_span_reads_as_words(self):
        from datetime import datetime, timedelta
        now = datetime.now(ask.ZONE).replace(hour=12, minute=0, second=0, microsecond=0)
        midnight = now.replace(hour=0, minute=0)
        self.assertEqual(ask.span_words((midnight - timedelta(days=1)).timestamp(), midnight.timestamp(), now.timestamp()),
                         "yesterday")
        self.assertEqual(ask.span_words(midnight.timestamp(), now.timestamp(), now.timestamp()), "today until 12:00")

    def test_a_pointed_question_about_arrival_needs_a_pointed_thing(self):
        plan = ask.clean_plan({"kind": "since"}, CAMERAS, 5000.0, ask.clean_context({}), None)
        self.assertEqual(plan["kind"], "past")


class Journal(Base):
    def test_a_wrong_event_name_still_counts_the_rules_events(self):
        client = PlanClient({"kind": "journal", "rule": "fake", "event": "gate_opened", "since_minutes_ago": 600,
                             "answer_as": "count"})
        app = self.app(client)
        FakeRule(app)
        app.journal.add("event", "r", "fake", "s", text="gate open", values={"occurrence": "c1"}, t=time.time() - 60)
        # An older row has no occurrence at all.
        app.journal.add("event", "r", "fake", "s", text="gate open", values={}, t=time.time() - 50)
        app.journal.add("end", "r", "fake", "s", seconds=5.0, values={}, t=time.time() - 45)
        self.assertEqual(self.wait(app, app.asks.start("How many times?"))["status"], "done")
        data = next(p for n, p, _ in client.prompts if n == "answer")
        self.assertIn('"count": 2', data)

    def test_a_counted_thing_is_answered_from_the_journal(self):
        client = PlanClient({"kind": "journal", "rule": "fake", "event": "s", "since_minutes_ago": 600,
                             "answer_as": "count"}, words="The gate opened 2 times today.")
        app = self.app(client)
        FakeRule(app)
        now = time.time()
        for t in (now - 300, now - 100):
            app.journal.add("event", "r", "fake", "s", text="gate open", values={"occurrence": f"c{int(t)}", "picture": "/p.jpg"}, t=t)
        job = self.wait(app, app.asks.start("How many times did the gate open?"))
        self.assertEqual(job["answer"], "The gate opened 2 times today.")
        self.assertEqual(len(job["moments"]), 2)
        data = next(p for n, p, _ in client.prompts if n == "answer")
        self.assertIn('"count": 2', data)
        # The answer stays, and shows in Activity.
        self.assertEqual(app.asks.history()[0]["id"], job["id"])
        self.assertEqual(app.journal.history("ask")[0]["values"]["ask"], job["id"])


class Footage(Base):
    def footage(self, app, frames):
        """Saved frames in the app's own session: [(t, busy tiles, mark)]."""
        folder = app.evidence.folder
        for t, busy, mark in frames:
            name = app.evidence.save_frame(t, wall(mark=mark))
            app.evidence.save_sharp(name, wall(mark=mark))
            for tile in busy:
                app.evidence.save_full(name, tile, wall(mark=mark))
        return folder

    def test_episodes_follow_the_busy_frames_of_one_camera(self):
        frames = [{"t": t, "busy": {3} if busy else set()} for t, busy in
                  [(0, 1), (1, 1), (2, 1), (30, 0), (40, 1), (41, 1)]]
        self.assertEqual([[f["t"] for f in e] for e in search.episodes(frames, 3)], [[0, 1, 2], [40, 41]])

    def test_a_past_question_looks_at_sheets_and_stops_when_sure(self):
        now = time.time()
        client = PlanClient({"kind": "past", "cameras": ["Gate wide"], "since_minutes_ago": 30, "claim": "A person is at the gate",
                             "which": "last", "answer_as": "when"},
                            sheets=[{"hits": [1], "sure": 3, "notes": [{"tile": 1, "note": "a man in white"}]}],
                            words="A man in white was at the gate at 10:00.")
        client.verify = lambda claim, jpeg, model="spark", reference=None: (
            {"seen": "a man in white at the gate", "passed": True, "confidence": 0.9}, 0.003)
        app = self.app(client)
        self.footage(app, [(now - 600 + i, {3}, False) for i in range(5)] + [(now - 60 + i, {3}, False) for i in range(5)])
        job = self.wait(app, app.asks.start("When was someone last at the gate?"))
        self.assertEqual(job["status"], "done")
        sheets = [p for p in client.prompts if p[0] == "sheet"]
        self.assertEqual(len(sheets), 1)
        self.assertIn("A person is at the gate", sheets[0][1])
        self.assertEqual(job["moments"][0]["note"], "a man in white at the gate")
        self.assertTrue(job["moments"][0]["picture"].startswith("/ask-file/"))
        self.assertTrue(app.asks.file(job["id"], "m0.jpg"))

    def test_a_hit_that_the_full_size_look_rejects_is_not_an_answer(self):
        now = time.time()
        client = PlanClient({"kind": "past", "cameras": ["Gate wide"], "since_minutes_ago": 30, "claim": "A person is at the gate",
                             "which": "last", "answer_as": "when"},
                            sheets=[{"hits": [1], "sure": 3, "notes": [{"tile": 1, "note": "a figure"}]}],
                            words="Nobody was at the gate.")
        client.verify = lambda claim, jpeg, model="spark", reference=None: (
            {"seen": "leaves, no person", "passed": False, "confidence": 0.8}, 0.003)
        app = self.app(client)
        self.footage(app, [(now - 60 + i, {3}, False) for i in range(5)])
        job = self.wait(app, app.asks.start("When was someone last at the gate?"))
        self.assertEqual(job["moments"], [])
        self.assertTrue(any("did not confirm" in s for s in job["steps"]))

    def test_nothing_moving_means_no_paid_look(self):
        now = time.time()
        client = PlanClient({"kind": "past", "cameras": ["Gate wide"], "since_minutes_ago": 30})
        app = self.app(client)
        self.footage(app, [(now - 300, set(), False), (now - 200, set(), False)])
        job = self.wait(app, app.asks.start("Did anyone come to the gate?"))
        self.assertEqual([p[0] for p in client.prompts], ["plan", "answer"])
        self.assertEqual(job["status"], "done")

    def test_a_big_count_asks_before_it_spends(self):
        now = time.time()
        client = PlanClient({"kind": "past", "cameras": ["Gate wide"], "since_minutes_ago": 120, "answer_as": "count",
                             "pace": "fast"})
        app = self.app(client)
        # Many short episodes, each 20 s apart.
        self.footage(app, [(now - 7000 + i * 20, {3}, False) for i in range(80)])
        job = self.wait(app, app.asks.start("How many people came today?"))
        self.assertEqual(job["status"], "needs")
        self.assertGreater(job["estimate_usd"], 0)

    def test_a_pointed_thing_arrived_when_its_place_changed(self):
        now = time.time()
        client = PlanClient({"kind": "since", "thing": "box", "answer_as": "since_when"},
                            words="The box arrived at 10:00.")
        app = self.app(client)
        # The box is there in the last 20 minutes, and not before.
        timeline = [(now - 3600 + i * 60, set(), (now - 3600 + i * 60) > now - 1200) for i in range(60)]
        self.footage(app, timeline)
        point = app.points.setdefault("p1", {"camera": "Gate wide", "index": 3, "view": wall(mark=True)[:],
                                             "jpeg": wall(mark=True), "noun": "box",
                                             "view_box": {"x1": 100, "y1": 100, "x2": 260, "y2": 260, "w": 640, "h": 360}})
        # The view of a point is the camera's own picture: Gate wide's tile.
        tile = Image.open(BytesIO(wall(mark=True))).crop((0, 360, 640, 720))
        out = BytesIO()
        tile.save(out, "JPEG")
        point["view"] = out.getvalue()
        job = self.wait(app, app.asks.start("When did this arrive?", {"thing": {"id": "p1", "noun": "box", "camera": "Gate wide"}}))
        self.assertEqual(job["status"], "done", job.get("error"))
        changed = [s for s in job["steps"] if "The place changed between" in s][0]
        # The change is found within a minute of when the box came.
        before, after = re_times(changed)
        # The clock can pass midnight during the test, so times compare within one day.
        self.assertLessEqual((after - before) % 86400, 61)
        self.assertLessEqual(min((after - (now - 1200)) % 86400, (now - 1200 - after) % 86400), 61)
        self.assertEqual([p[0] for p in client.prompts], ["plan", "pair", "answer"])
        self.assertGreaterEqual(job["sure"], 0.85)


class Lights(Base):
    def test_a_light_change_is_not_the_arrival(self):
        now = time.time()
        client = PlanClient({"kind": "since", "thing": "box"}, pair=[
            {"first_has_it": True, "second_has_it": True, "note": "the light changed"},
            {"first_has_it": False, "second_has_it": True, "note": "a box appeared"}])
        app = self.app(client)
        # The box came 40 minutes ago. 15 minutes ago a light came on, and the place looks different since.
        frames = []
        for i in range(60):
            t = now - 3600 + i * 60
            frames.append((t, set(), t > now - 2400))
        for t, busy, mark in frames:
            bright = t > now - 900
            name = app.evidence.save_frame(t, wall((200, 200, 200) if bright else (40, 40, 40), mark=mark))
            picture = Image.open(BytesIO(wall((200, 200, 200) if bright else (40, 40, 40), mark=mark)))
            if bright:
                # Light from one side changes the place's edges as well.
                ImageDraw.Draw(picture).polygon([(0, 360), (640, 360), (0, 720)], fill=(255, 255, 255))
            out = BytesIO()
            picture.save(out, "JPEG")
            app.evidence.save_sharp(name, out.getvalue())
        tile = Image.open(BytesIO(app.evidence.read_sharp(app.timeline[-1].name if app.timeline else name))).crop((0, 360, 640, 720))
        out = BytesIO()
        tile.save(out, "JPEG")
        app.points["p1"] = {"camera": "Gate wide", "index": 3, "view": out.getvalue(), "jpeg": out.getvalue(),
                            "noun": "box", "view_box": {"x1": 100, "y1": 100, "x2": 260, "y2": 260, "w": 640, "h": 360}}
        job = self.wait(app, app.asks.start("When did this arrive?", {"thing": {"id": "p1", "noun": "box", "camera": "Gate wide"}}))
        self.assertEqual(job["status"], "done", job.get("error"))
        steps = [s for s in job["steps"] if "The place changed between" in s]
        self.assertTrue(any("further back" in s for s in job["steps"]), job["steps"])
        before, after = re_times(steps[-1])
        self.assertLessEqual(min((after - (now - 2400)) % 86400, (now - 2400 - after) % 86400), 61)


def re_times(step):
    import re
    from datetime import datetime
    times = re.findall(r"\d{2}:\d{2}:\d{2}", step)
    today = datetime.now().strftime("%Y-%m-%d")
    return [datetime.strptime(f"{today} {t}", "%Y-%m-%d %H:%M:%S").timestamp() for t in times]


class Ranking(Base):
    """The moment index puts the moments that look like the question first, for free."""

    def search_for(self, app, which):
        import numpy as np
        now = time.time()
        times = [now - 3000 + i * 30 for i in range(40)]
        person, other = np.zeros(512, dtype="float32"), np.zeros(512, dtype="float32")
        person[0], other[1] = 1.0, 1.0
        good = set(times[10:13]) | {times[30]}

        class FakeEmbedder:
            def available(self):
                return True

            def text(self, phrase):
                return person

        class FakeIndex:
            def vectors(self, since, until, tiles):
                return {3: (np.array(times), np.stack([person if t in good else other for t in times]))}

        app.embedder, app.moments = FakeEmbedder(), FakeIndex()
        plan = {"kind": "past", "cameras": ["Gate wide"], "since": now - 3600, "until": now, "thing": "person",
                "claim": "A person is at the gate", "which": which, "pace": "slow", "answer_as": "when"}
        job = {"id": "a" * 10, "plan": plan, "steps": [], "cost": 0.0, "question": "q", "context": {}}
        found = search.Search(app.asks, job)
        queue = [{"camera": "Gate wide", "tile": 3, "frame": {"name": f"{i}.jpg"}, "t": t, "episode": []}
                 for i, t in enumerate(times)]
        return found.rank(queue), times, job

    def test_the_moments_that_look_like_the_question_come_first(self):
        ranked, times, job = self.search_for(self.app(PlanClient({})), "all")
        self.assertEqual(sorted(item["t"] for item in ranked[:4]), sorted([times[10], times[11], times[12], times[30]]))
        self.assertTrue(any("Perception Encoder" in s for s in job["steps"]))

    def test_the_last_time_takes_the_newest_of_the_best_first(self):
        ranked, times, _ = self.search_for(self.app(PlanClient({})), "last")
        best = [item["t"] for item in ranked[:search.TOP_RANKED]]
        self.assertEqual(best, sorted(best, reverse=True))
        self.assertIn(times[30], best)

    def test_without_the_index_the_order_stays(self):
        app = self.app(PlanClient({}))
        job = {"id": "b" * 10, "plan": {"kind": "past", "cameras": ["Gate wide"], "since": 0, "until": 1, "thing": "",
               "claim": "", "which": "all", "pace": "slow", "answer_as": "when"}, "steps": [], "cost": 0.0,
               "question": "q", "context": {}}
        queue = [{"camera": "Gate wide", "tile": 3, "frame": {"name": "1.jpg"}, "t": 1.0, "episode": []}]

        class Off:
            def available(self):
                return False

        app.embedder = Off()
        self.assertEqual(search.Search(app.asks, job).rank(queue), queue)

    def test_the_words_read_as_pe_core_was_trained(self):
        self.assertEqual(search.photo_of("person"), "a photo of a person")
        self.assertEqual(search.photo_of("animal"), "a photo of an animal")
        self.assertEqual(search.photo_of("two cars"), "a photo of two cars")
        self.assertEqual(search.photo_of("people"), "a photo of people")
        self.assertEqual(search.photo_of("dogs"), "a photo of dogs")
        self.assertEqual(search.photo_of("glass"), "a photo of a glass")

    def test_a_camera_that_always_looks_like_the_words_does_not_win(self):
        import numpy as np
        now = time.time()
        times = [now - 1200 + i * 30 for i in range(40)]
        text = np.zeros(512, dtype="float32")
        text[0] = 1.0

        def row(likeness):
            v = np.zeros(512, dtype="float32")
            v[0], v[1] = likeness, (1 - likeness ** 2) ** 0.5
            return v

        # Gate wide is usually 0.5 like the words, and 0.7 once. The tracker always shows the gate: 0.8.
        index = {3: (np.array(times), np.stack([row(0.7 if i == 20 else 0.5) for i in range(40)])),
                 4: (np.array(times), np.stack([row(0.8) for _ in times]))}
        queue = [{"camera": "Gate wide", "tile": 3, "frame": {"name": f"{i}.jpg"}, "t": t, "episode": []}
                 for i, t in enumerate(times)]
        queue += [{"camera": "Gate tracker", "tile": 4, "frame": {"name": f"{i}.jpg"}, "t": t, "episode": []}
                  for i, t in enumerate(times)]
        scores = search.moment_scores(queue, index, text)
        best = queue[int(np.argmax(scores))]
        self.assertEqual((best["tile"], best["t"]), (3, times[20]))

    def test_moments_the_index_lacks_are_embedded_during_the_question(self):
        import numpy as np
        app = self.app(PlanClient({}))
        now = time.time()
        times = [now - 600 + i * 30 for i in range(10)]
        person = np.zeros(512, dtype="float32")
        person[0] = 1.0
        asked = []

        class Embedder:
            def available(self):
                return True

            def text(self, phrase):
                return person

        class Index:
            rows = {}

            def vectors(self, since, until, tiles):
                return dict(self.rows)

            def index_now(self, wanted, seconds):
                asked.extend(wanted)
                vectors = np.stack([person if i == 7 else np.roll(person, 1) for i in range(len(times))])
                self.rows = {3: (np.array(times), vectors)}
                return len(wanted)

        app.embedder, app.moments = Embedder(), Index()
        plan = {"kind": "past", "cameras": ["Gate wide"], "since": now - 900, "until": now, "thing": "person",
                "claim": "", "which": "all", "pace": "slow", "answer_as": "when"}
        job = {"id": "c" * 10, "plan": plan, "steps": [], "cost": 0.0, "question": "q", "context": {}}
        queue = [{"camera": "Gate wide", "tile": 3, "frame": {"name": f"{i}.jpg", "session": "s", "busy": {3}},
                  "t": t, "episode": []} for i, t in enumerate(times)]
        ranked = search.Search(app.asks, job).rank(queue)
        self.assertEqual(len(asked), 10)
        self.assertEqual(ranked[0]["t"], times[7])
        self.assertTrue(any(s.startswith("Indexing 10 moments") for s in job["steps"]))
        self.assertTrue(any("look like a person," in s for s in job["steps"]))


class TheRest(Base):
    """Nothing found in part of the footage is not a no. The person can pay to see the rest."""

    footage = Footage.footage

    def test_nothing_found_yet_offers_the_rest_and_never_pays_twice(self):
        now = time.time()
        client = PlanClient({"kind": "past", "cameras": ["Gate wide"], "since_minutes_ago": 120, "claim": "A dog is at the gate",
                             "which": "last", "answer_as": "when", "pace": "slow"}, words="No dog yet.")
        app = self.app(client)
        # 80 visits, 20 s apart: one moment each at a slow pace, more than the free sheets can see.
        self.footage(app, [(now - 7000 + i * 20, {3}, False) for i in range(80)])
        job = self.wait(app, app.asks.start("When was a dog last at the gate?"))
        first = [p for p in client.prompts if p[0] == "sheet"]
        self.assertEqual(len(first), search.FREE_SHEETS)
        self.assertEqual(job["looked"], search.FREE_SHEETS * search.SHEET_SIZE)
        self.assertEqual(job["unseen"], 80 - job["looked"])
        self.assertGreater(job["more_usd"], 0)
        self.assertEqual(job["more_moments"], 26)
        answer_prompt = [p for p in client.prompts if p[0] == "answer"][-1][1]
        self.assertIn('"not_seen": 26', answer_prompt)
        # The server restarts before the person says yes.
        app.asks.jobs.clear()
        app.asks.go(job["id"])
        job = self.wait(app, job["id"])
        second = [p for p in client.prompts if p[0] == "sheet"][len(first):]
        self.assertEqual(len(second), 3)  # 26 more moments, 9 a sheet
        self.assertEqual(job["looked"], 80)
        self.assertEqual(job["unseen"], 0)
        self.assertIsNone(job.get("more_usd"))
        self.assertIn("Looking at the rest", job["steps"])
        self.assertFalse((app.asks.folder / job["id"] / "resume.json").exists())


    def test_a_rejected_hit_does_not_end_the_search(self):
        now = time.time()
        client = PlanClient({"kind": "past", "cameras": ["Gate wide"], "since_minutes_ago": 120, "claim": "A dog is at the gate",
                             "which": "last", "answer_as": "when", "pace": "slow"},
                            sheets=[{"hits": [2], "sure": 3}, {"hits": [4], "sure": 3}], words="A dog was at the gate.")
        verdicts = [{"seen": "leaves, no dog", "passed": False, "confidence": 0.8},
                    {"seen": "a brown dog at the gate", "passed": True, "confidence": 0.9}]
        client.verify = lambda claim, jpeg, model="spark", reference=None: (verdicts.pop(0), 0.003)
        app = self.app(client)
        self.footage(app, [(now - 7000 + i * 20, {3}, False) for i in range(40)])
        job = self.wait(app, app.asks.start("When was a dog last at the gate?"))
        self.assertEqual(len([p for p in client.prompts if p[0] == "sheet"]), 2)
        self.assertEqual(len(job["moments"]), 1)
        self.assertEqual(job["moments"][0]["note"], "a brown dog at the gate")
        self.assertTrue(any("did not confirm" in s for s in job["steps"]))
        answer_prompt = [p for p in client.prompts if p[0] == "answer"][-1][1]
        self.assertIn('"rejected_looks": [{"at"', answer_prompt)
        self.assertIn("leaves, no dog", answer_prompt)


class Backfill(unittest.TestCase):
    def test_the_newest_hours_are_indexed_first(self):
        import numpy as np
        import moments
        order = []

        class Embedder:
            def available(self):
                return True

            def images(self, pictures):
                return np.zeros((len(pictures), 512), dtype="float32")

        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        index = moments.Moments(Path(folder) / "m.db", Embedder(),
                                lambda frame, tile: order.append(frame["t"]) or Image.new("RGB", (8, 8)),
                                lambda: (1, 1), lambda frame: set())
        moments.BACKFILL_PAUSE_S, pause = 0, moments.BACKFILL_PAUSE_S
        self.addCleanup(setattr, moments, "BACKFILL_PAUSE_S", pause)
        index.backfill([{"t": t, "session": "s", "name": f"{t}.jpg", "busy": set()} for t in (0, 30, 60, 120, 180)])
        self.assertEqual(order, [180, 120, 60, 0])
        self.assertEqual(index.count(), 4)


if __name__ == "__main__":
    unittest.main()
