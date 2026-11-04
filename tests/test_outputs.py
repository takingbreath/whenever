"""Actions, the journal and schedules: the outputs of a rule, now and later."""

import base64
import json
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import connectors
import rule_runtime
import server
import writer
from connectors import ActionError, Connectors, check_draft
from journal import Journal


class Reply:
    def __init__(self, status=200, body=b"ok"):
        self.status = status
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    """Keeps each request and answers it without a network."""

    def __init__(self, status=200, body=b"ok"):
        self.requests = []
        self.status = status
        self.body = body

    def open(self, request, timeout=None):
        self.requests.append(request)
        return Reply(self.status, self.body)


LAMP = {
    "name": "Desk lamp", "description": "Turns the desk lamp on or off.", "method": "PUT",
    "url": "http://192.168.1.5/api/{secret:HUE_USER}/lights/{light}/state",
    "headers": {"X-Note": "{note}"}, "body_type": "json", "body": '{"on": "{on}", "label": "lamp {light}"}',
    "inputs": [{"name": "on", "description": "true or false", "example": "true"},
               {"name": "light", "description": "the light number", "example": "3"},
               {"name": "note", "description": "a note", "example": "hi"}],
    "secrets": [{"name": "HUE_USER", "description": "the bridge user name"}],
}


class Secrets(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        self.secrets = Path(folder) / "actions.env"
        self.secrets.write_text("HUE_USER=abc123secret\nNTFY_TOPIC=whenever-test\n")
        self.opener = FakeOpener()

    def connectors(self, paired=True, **kwargs):
        actions = Connectors(secrets_path=self.secrets, opener=self.opener, **kwargs)
        if paired:
            actions.channels.add_ntfy()
        return actions


class Drafts(unittest.TestCase):
    def test_a_good_draft_passes(self):
        action = check_draft(LAMP)
        self.assertEqual(action["name"], "Desk lamp")
        self.assertEqual(action["kind"], "web")

    def test_the_host_must_be_fixed(self):
        with self.assertRaises(ActionError):
            check_draft({**LAMP, "url": "http://{host}/x"})

    def test_every_blank_must_be_listed(self):
        with self.assertRaises(ActionError):
            check_draft({**LAMP, "body": '{"on": "{power}"}'})
        with self.assertRaises(ActionError):
            check_draft({**LAMP, "headers": {"Authorization": "Bearer {secret:TOKEN}"}})

    def test_bad_json_and_bad_scheme_fail(self):
        with self.assertRaises(ActionError):
            check_draft({**LAMP, "body": '{"on": '})
        with self.assertRaises(ActionError):
            check_draft({**LAMP, "url": "file:///etc/passwd"})

    def test_a_name_cannot_repeat(self):
        with self.assertRaises(ActionError):
            check_draft(LAMP, taken=["desk lamp"])
        with self.assertRaises(ActionError):
            check_draft({**LAMP, "name": "Phone"})

    def test_a_hosted_server_needs_https(self):
        with mock.patch.object(connectors, "PUBLIC", True):
            with self.assertRaises(ActionError):
                check_draft(LAMP)


class Web(Secrets):
    def test_the_request_fills_its_blanks(self):
        actions = self.connectors()
        actions.add(LAMP)
        result = actions.run("desk lamp", {"on": False, "light": "3 a", "note": "hi"})
        self.assertTrue(result["ok"])
        request = self.opener.requests[0]
        self.assertEqual(request.full_url, "http://192.168.1.5/api/abc123secret/lights/3%20a/state")
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(json.loads(request.data), {"on": False, "label": "lamp 3 a"})
        self.assertEqual(request.get_header("X-note"), "hi")

    def test_a_missing_input_or_key_stops_the_request(self):
        actions = self.connectors()
        actions.add(LAMP)
        with self.assertRaises(ActionError):
            actions.run("Desk lamp", {"on": True})
        self.secrets.write_text("")
        with self.assertRaises(ActionError):
            actions.run("Desk lamp", {"on": True, "light": "3", "note": ""})
        self.assertEqual(self.opener.requests, [])

    def test_a_header_cannot_add_a_line(self):
        actions = self.connectors()
        actions.add(LAMP)
        with self.assertRaises(ActionError):
            actions.run("Desk lamp", {"on": True, "light": "3", "note": "a\r\nHost: evil"})

    def test_a_key_in_the_reply_is_hidden(self):
        self.opener.body = b'{"user": "abc123secret"}'
        actions = self.connectors()
        actions.add(LAMP)
        result = actions.run("Desk lamp", {"on": True, "light": "3", "note": ""})
        self.assertNotIn("abc123secret", result["reply"])

    def test_an_unknown_action_names_the_ready_ones(self):
        with self.assertRaisesRegex(ActionError, "Phone"):
            self.connectors().run("Garage door")

    def test_a_hosted_server_refuses_a_private_host(self):
        actions = self.connectors()
        actions.items.append({**check_draft(LAMP), "url": "https://192.168.1.5/x"})
        with mock.patch.object(connectors, "PUBLIC", True):
            with self.assertRaisesRegex(ActionError, "public"):
                actions.run("Desk lamp", {"on": True, "light": "3", "note": ""})

    def test_actions_are_saved_and_read_back(self):
        path = self.secrets.parent / "actions.json"
        self.connectors(path=path).add(LAMP)
        again = self.connectors(path=path)
        self.assertEqual([a["name"] for a in again.items], ["Desk lamp"])
        self.assertTrue(again.remove("desk lamp"))
        self.assertEqual(self.connectors(path=path).items, [])

    def test_the_page_never_sees_a_key(self):
        actions = self.connectors()
        actions.add(LAMP)
        self.assertNotIn("abc123secret", json.dumps(actions.public()))
        self.assertNotIn("whenever-test", json.dumps(actions.public()))


class Phone(Secrets):
    def test_text_goes_as_a_post(self):
        result = self.connectors().run("Phone", {"text": "A car blocks the gate", "title": "Gate"})
        self.assertTrue(result["ok"])
        request = self.opener.requests[0]
        self.assertEqual(request.full_url, "https://ntfy.sh/whenever-test")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"A car blocks the gate")
        self.assertEqual(request.get_header("Title"), "Gate")

    def test_a_picture_goes_as_a_put_with_the_text_in_a_header(self):
        self.connectors().run("Phone", {"text": "Tisch 4 wartet – 5 min"}, picture=b"jpeg")
        request = self.opener.requests[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.data, b"jpeg")
        message = request.get_header("Message")
        self.assertTrue(message.startswith("=?UTF-8?B?"))
        self.assertEqual(base64.b64decode(message[10:-2]).decode(), "Tisch 4 wartet – 5 min")

    def test_an_alert_reaches_only_alert_actions(self):
        actions = self.connectors()
        actions.add(LAMP)
        sent = actions.alert("Hello")
        self.assertEqual([name for name, _ in sent], ["Phone"])
        self.assertEqual(len(self.opener.requests), 1)

    def test_with_nothing_paired_the_phone_is_not_ready(self):
        actions = self.connectors(paired=False)
        self.assertEqual(actions.ready(), [])
        self.assertEqual(actions.alert("Hello"), [])
        self.assertEqual(actions.public()[0]["missing"], ["a paired phone"])
        with self.assertRaisesRegex(ActionError, "Pair a phone"):
            actions.run("Phone", {"text": "Hello"})

    def test_one_alert_reaches_every_paired_place_and_each_one_reports(self):
        actions = self.connectors()
        actions.channels.places["email"].append({"id": "e1", "address": "a@example.com", "t": 0})
        result = actions.run("Phone", {"text": "Hello", "title": "Gate"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 207)
        self.assertEqual([(p["kind"], p["ok"]) for p in result["places"]], [("email", False), ("ntfy", True)])
        self.assertIn("SES", result["places"][0]["detail"])


class Journals(unittest.TestCase):
    def test_history_filters_and_keeps_time_order(self):
        journal = Journal()
        journal.add("event", "r1", "Tables", "seated", t=100)
        journal.add("record", "r1", "Tables", "waited", values={"table": "4", "seconds": 312}, t=200)
        journal.add("end", "r2", "Gate", "car", seconds=40, t=300)
        self.assertEqual([row["t"] for row in journal.history()], [100, 200, 300])
        rows = journal.history(kind="record")
        self.assertEqual(rows[0]["values"], {"table": "4", "seconds": 312})
        self.assertEqual([row["rule"] for row in journal.history(rule="Gate")], ["Gate"])
        self.assertEqual([row["t"] for row in journal.history(since=150, until=250)], [200])
        self.assertEqual([row["t"] for row in journal.history(limit=2)], [200, 300])

    def test_it_stays_after_a_restart(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        Journal(Path(folder) / "journal.db").add("event", "r1", "Tables", "seated")
        self.assertEqual(len(Journal(Path(folder) / "journal.db").history()), 1)

    def test_a_large_record_is_refused(self):
        with self.assertRaises(ValueError):
            Journal().add("record", values={"x": "a" * 5000})

    def test_the_summary_names_rules_and_record_keys(self):
        journal = Journal()
        journal.add("record", "r1", "Tables", "waited", values={"table": "4", "seconds": 312})
        journal.add("log", "r1", "Tables", text="hello")
        summary = journal.summary()
        self.assertIn("'Tables'", summary)
        self.assertIn("table, seconds", summary)
        self.assertNotIn("hello", summary)


class Schedules(unittest.TestCase):
    def test_every_minutes_repeats(self):
        schedule = server.Schedule({"id": "report", "every_minutes": 10, "at": "", "days": []}, now=1000)
        self.assertFalse(schedule.due(1599))
        self.assertTrue(schedule.due(1600))
        self.assertFalse(schedule.due(1601))
        self.assertTrue(schedule.due(2200))

    def test_at_a_time_on_a_day(self):
        monday = datetime(2026, 11, 2, 8, 0, tzinfo=server.ZONE).timestamp()
        schedule = server.Schedule({"id": "weekly", "every_minutes": 0, "at": "09:00", "days": ["mon"]}, now=monday)
        self.assertEqual(datetime.fromtimestamp(schedule.next_t, server.ZONE).strftime("%a %H:%M"), "Mon 09:00")
        schedule.due(schedule.next_t)
        due = datetime.fromtimestamp(schedule.next_t, server.ZONE)
        self.assertEqual((due.strftime("%a %H:%M"), due.day), ("Mon 09:00", 9))

    def test_a_bad_time_repeats_hourly(self):
        schedule = server.Schedule({"id": "x", "every_minutes": 0, "at": "25:00", "days": []}, now=0)
        self.assertEqual(schedule.next_t, 3600)


class App(unittest.TestCase):
    def setUp(self):
        self.logs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.logs)
        secrets = Path(self.logs) / "actions.env"
        secrets.write_text("NTFY_TOPIC=whenever-test\n")
        self.opener = FakeOpener()
        self.app = server.App(object(), self.logs, inline=True,
                              connectors=Connectors(secrets_path=secrets, opener=self.opener))
        self.app.channels.add_ntfy()
        self.rule = server.Rule(self.app, "r1", "Tell me", {"name": "Gate", "sensors": [], "schedules": [
            {"id": "report", "every_minutes": 1, "at": "", "days": []}]}, Path(self.logs))
        self.rule.sent = []
        self.rule.send = self.rule.sent.append
        self.rule.reply = lambda message: self.rule.sent.append(message)
        self.app.entries["r1"] = self.rule

    def call(self, block, **args):
        self.app._rule_call(self.rule, {"id": 1, "name": block, "args": args})
        return self.rule.sent[-1]

    def test_an_action_runs_and_goes_in_the_journal(self):
        (Path(self.logs) / "shot.jpg").write_bytes(b"jpeg")
        reply = self.call("act", name="Phone", inputs={"text": "Hi"}, picture="shot.jpg")
        self.assertTrue(reply["value"]["ok"])
        self.assertEqual(self.opener.requests[0].data, b"jpeg")
        row = self.app.journal.history(kind="action")[0]
        self.assertEqual((row["rule"], row["name"], row["values"]["ok"]), ("Gate", "Phone", True))

    def test_a_picture_outside_the_folder_is_refused(self):
        reply = self.call("act", name="Phone", inputs={"text": "Hi"}, picture="../../.env")
        self.assertIn("cannot send", reply["error"])
        self.assertEqual(self.opener.requests, [])

    def test_actions_have_a_rate_limit(self):
        for _ in range(server.MAX_ACTS_PER_MINUTE):
            self.call("act", name="Phone", inputs={"text": "Hi"})
        self.assertIn("more than", self.call("act", name="Phone", inputs={"text": "Hi"})["error"])

    def test_a_report_works_at_the_spend_limit(self):
        self.app.writer_usd = server.SPEND_LIMIT_USD
        self.app.journal.add("end", "r2", "Tables", "seated", seconds=600)
        reply = self.call("history", kind="end", rule_name="Tables")
        self.assertEqual(reply["value"][0]["seconds"], 600)
        self.assertIn("spend limit", self.call("ask", question="x")["error"])

    def test_notify_also_reaches_the_phone(self):
        self.rule._handle({"do": "notify", "text": "A car blocks the gate"})
        self.assertEqual(self.opener.requests[0].data, b"A car blocks the gate")
        self.assertEqual(self.app.journal.history(kind="notify")[0]["text"], "A car blocks the gate")

    def test_a_record_goes_in_the_journal(self):
        self.rule._handle({"do": "record", "name": "waited", "values": {"table": "4", "seconds": 312}})
        self.assertEqual(self.app.journal.history(kind="record")[0]["values"]["seconds"], 312)

    def test_a_schedule_fires_with_no_camera(self):
        self.rule.schedules[0].next_t = 0
        self.app.tick()
        kinds = [(m.get("type"), m.get("sensor")) for m in self.rule.sent]
        self.assertEqual(kinds[:3], [("on", "report"), ("off", "report"), ("tick", None)])
        self.app.tick()
        self.assertEqual(sum(1 for m in self.rule.sent if m.get("type") == "on"), 1)

    def test_rules_return_after_a_restart(self):
        rules_file = Path(self.logs) / "rules.json"
        folder = Path(self.logs) / "rules" / "abc123"
        folder.mkdir(parents=True)
        (folder / "rule.json").write_text(json.dumps({"sentence": "Every hour", "name": "Hourly", "sensors": [],
                                                      "schedules": [{"id": "h", "every_minutes": 60}]}))
        self.app.entries = {}
        self.app.rules_file = rules_file
        with mock.patch.object(server.sandbox, "RULES_DIR", Path(self.logs) / "rules"), \
                mock.patch.object(server.Rule, "start"):
            rule = server.Rule(self.app, "abc123", "Every hour", {"name": "Hourly", "sensors": []}, folder)
            rule.paused = True
            self.app.entries["abc123"] = rule
            self.app.save_rules()
            fresh = server.App(object(), self.logs, inline=True, rules_file=rules_file)
            self.assertEqual(fresh.load_rules(), 1)
        back = fresh.entries["abc123"]
        self.assertEqual((back.name, back.paused, back.schedules[0].id), ("Hourly", True, "h"))


class Runtime(unittest.TestCase):
    def test_tests_can_script_actions_records_and_history(self):
        ctx = rule_runtime.Context()
        ctx.past = [{"t": 1.0, "kind": "end", "seconds": 40.0}]
        self.assertTrue(ctx.act("Lamp", on=False)["ok"])
        ctx.record("waited", table=4, seen=object())
        ctx.notify("Hi", picture=rule_runtime.Frame(path="frames/a.jpg"))
        self.assertEqual(ctx.history()[0]["seconds"], 40.0)
        self.assertEqual(ctx.actions[0], {"do": "act", "name": "Lamp", "inputs": {"on": False}})
        self.assertEqual(ctx.actions[1]["values"]["table"], 4)
        self.assertIsInstance(ctx.actions[1]["values"]["seen"], str)
        self.assertEqual(ctx.actions[2], {"do": "notify", "text": "Hi", "picture": "frames/a.jpg"})


class Finish(unittest.TestCase):
    def finish(self, sensors=(), schedules=()):
        call = {"name": "finish", "arguments": json.dumps({
            "name": "Report", "sensors": list(sensors), "schedules": list(schedules), "plan": [], "unsupported": ""})}
        return writer.handle(call, Path("."), None, None, None, lambda text: None)

    def test_a_rule_needs_a_sensor_or_a_schedule(self):
        output, done = self.finish()
        self.assertIn("sensor or schedule", output)
        self.assertIsNone(done)

    def test_a_schedule_needs_a_real_time(self):
        output, _ = self.finish(schedules=[{"id": "r", "every_minutes": 0, "at": "9am", "days": []}])
        self.assertIn("HH:MM", output)
        output, _ = self.finish(schedules=[{"id": "r", "every_minutes": 0, "at": "", "days": []}])
        self.assertIn("at least 1", output)

    def test_ids_cannot_repeat(self):
        output, _ = self.finish(sensors=[{"id": "r"}], schedules=[{"id": "r", "every_minutes": 5, "at": "", "days": []}])
        self.assertIn("own id", output)


class ClickAndPictures(App):
    def test_a_phone_alert_links_back_to_the_hosted_page(self):
        with mock.patch.object(server, "HOSTS", ("wheneverwhatever.si",)):
            self.call("act", name="Phone", inputs={"text": "Hi"})
        self.assertEqual(self.opener.requests[0].get_header("Click"), "https://wheneverwhatever.si/#/activity")

    def test_a_local_page_sends_no_link(self):
        self.call("act", name="Phone", inputs={"text": "Hi"})
        self.assertIsNone(self.opener.requests[0].get_header("Click"))

    def test_a_notify_picture_reaches_the_journal_as_a_page_address(self):
        (Path(self.logs) / "frames").mkdir()
        (Path(self.logs) / "frames" / "a.jpg").write_bytes(b"jpeg")
        self.rule._handle({"do": "notify", "text": "Car", "picture": "frames/a.jpg"})
        row = self.app.journal.history(kind="notify")[0]
        self.assertEqual(row["values"]["picture"], "/rule-file/r1/frames/a.jpg")
        self.assertEqual(self.opener.requests[0].data, b"jpeg")

    def test_the_page_learns_the_phone_topic(self):
        self.assertEqual(self.app.status()["phone_topic"], "whenever-test")


class Understand(unittest.TestCase):
    def test_spark_says_back_when_and_then(self):
        reply = {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({
            "when": "When a car blocks the gate", "then": ["Alert your phone"], "watch": ["Gate wide"],
            "missing": "", "question": ""})}]}], "usage": {"input_tokens": 1000, "output_tokens": 100}}

        class Client:
            def __init__(self):
                self.bodies = []

            def _post(self, body, **kwargs):
                self.bodies.append(body)
                return json.dumps(reply)

        client = Client()
        summary, cost = writer.understand(client, "When a car blocks the gate, alert my phone", "- Phone", "- Gate wide")
        self.assertEqual(summary["then"], ["Alert your phone"])
        self.assertGreater(cost, 0)
        self.assertEqual(client.bodies[0]["model"], writer.SPARK)
        self.assertIn("- Phone", client.bodies[0]["input"])


class Runs(App):
    def test_runs_come_from_the_journal_and_survive_a_restart(self):
        self.app.journal.add("event", "r1", "Gate", "car", t=100)
        self.app.journal.add("event", "r1", "Gate", "car", t=200)
        self.app.journal.add("draw", "r1", "Gate", t=201)
        again = server.Rule(self.app, "r1", "Tell me", {"name": "Gate", "sensors": []}, Path(self.logs))
        self.assertEqual((again.fired, again.last_fired), (2, 200))

    def test_a_schedule_counts_as_a_run(self):
        self.rule.schedules[0].next_t = 0
        self.app.tick()
        self.assertEqual(self.rule.fired, 1)
        self.assertIsNotNone(self.rule.last_fired)


class Pairing(unittest.TestCase):
    def test_a_first_pairing_makes_a_private_topic_and_remembers_the_phone(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        secrets = folder / "actions.env"
        app = server.App(object(), folder, inline=True, connectors=Connectors(secrets_path=secrets),
                         phone_file=folder / "phone.json")
        self.assertFalse(app.status()["phone_paired"])
        topic = app.phone_topic()
        self.assertRegex(topic, r"^whenever-[0-9a-f]{16}$")
        self.assertEqual(app.phone_topic(), topic)
        self.assertEqual(oct(secrets.stat().st_mode & 0o777), "0o600")
        self.assertEqual(app.status()["phone_topic"], topic)
        app.set_paired(True)
        again = server.App(object(), folder, inline=True, connectors=Connectors(secrets_path=secrets),
                           phone_file=folder / "phone.json")
        self.assertTrue(again.status()["phone_paired"])

    def test_an_existing_topic_is_kept(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        secrets = folder / "actions.env"
        secrets.write_text("HUE_USER=abc\nNTFY_TOPIC=whenever-old\n")
        app = server.App(object(), folder, inline=True, connectors=Connectors(secrets_path=secrets))
        self.assertEqual(app.phone_topic(), "whenever-old")
        self.assertIn("HUE_USER=abc", secrets.read_text())


if __name__ == "__main__":
    unittest.main()
