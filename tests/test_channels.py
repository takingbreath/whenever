"""Places for alerts: a browser, Telegram, email and ntfy, and the problem log."""

import base64
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import channels
import server
from channels import ChannelError, Channels, b64u, encrypt_push, unb64u
from connectors import Connectors
from problems import Problems

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
BOT = "123456789:AAHfakefakefakefakefakefakefakefake"


class Reply:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    """Answers by address, and keeps each request."""

    def __init__(self):
        self.requests = []
        self.answers = {}

    def answer(self, part, status=200, body=b"{}"):
        self.answers[part] = (status, body if isinstance(body, bytes) else json.dumps(body).encode())

    def open(self, request, timeout=None):
        self.requests.append(request)
        for part, (status, body) in self.answers.items():
            if part in request.full_url:
                if status >= 400:
                    raise urllib.error.HTTPError(request.full_url, status, "no", {}, None)
                return Reply(status, body)
        return Reply(201, b"")

    def last(self, part):
        return [r for r in self.requests if part in r.full_url][-1]


def browser():
    """A browser's push keys, as PushManager would make them."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(channels._ec()[1].Encoding.X962,
                                           channels._ec()[1].PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    return key, {"endpoint": FCM, "keys": {"p256dh": b64u(public), "auth": b64u(auth)}}


def decrypt(body, key, subscription):
    """What the browser does with a push message (RFC 8291)."""
    salt, rs, size = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    sender = body[21:21 + size]
    sealed = body[21 + size:]
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
    ua_public = unb64u(subscription["keys"]["p256dh"])
    auth = unb64u(subscription["keys"]["auth"])
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_public + sender).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, sealed, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


class Places(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.secrets = self.folder / "actions.env"
        self.secrets.write_text("NTFY_TOPIC=whenever-test\n")
        self.opener = Opener()
        self.channels = Channels(self.folder / "channels.json", self.secrets, self.opener, self.folder / "pictures")


class Push(Places):
    def test_only_the_browser_can_read_an_alert(self):
        key, subscription = browser()
        body = encrypt_push(b'{"title": "Gate"}', subscription["keys"]["p256dh"], subscription["keys"]["auth"])
        self.assertEqual(decrypt(body, key, subscription), b'{"title": "Gate"}')

    def test_the_vapid_token_is_signed_for_the_push_service(self):
        server_key = self.channels._vapid_key()
        token = channels.vapid_token(FCM, server_key, now=1000)
        head, claims, signature = token.split(".")
        self.assertEqual(json.loads(unb64u(claims))["aud"], "https://fcm.googleapis.com")
        raw = unb64u(signature)
        der = utils.encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        server_key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))

    def test_the_keys_are_made_once_and_kept_private(self):
        first = self.channels.push_public_key()
        self.assertEqual(self.channels.push_public_key(), first)
        self.assertIn("WEBPUSH_PRIVATE_KEY=", self.secrets.read_text())
        self.assertEqual(oct(self.secrets.stat().st_mode & 0o777), "0o600")
        self.assertNotIn(self.secrets.read_text().split("WEBPUSH_PRIVATE_KEY=")[1].strip(),
                         json.dumps(self.channels.public()))

    def test_an_unknown_push_service_is_refused(self):
        _, subscription = browser()
        with self.assertRaisesRegex(ChannelError, "push service"):
            self.channels.add_push({**subscription, "endpoint": "https://192.168.1.5/x"}, "Phone")

    def test_a_pairing_link_works_once(self):
        _, subscription = browser()
        token = self.channels.start("push", login=True)
        place = self.channels.add_push(subscription, "Chrome on Android", token)
        found = self.channels.pairing(token)
        self.assertTrue(found["done"])
        self.assertEqual(found["place"]["label"], "Chrome on Android")
        with self.assertRaisesRegex(ChannelError, "old or used"):
            self.channels.add_push(subscription, "Again", token)
        self.assertEqual([p["id"] for p in Channels(self.folder / "channels.json", self.secrets).all()], [place["id"]])

    def test_an_old_link_is_refused(self):
        _, subscription = browser()
        token = self.channels.start("push")
        self.channels.clock = lambda: 10 ** 10
        self.assertTrue(self.channels.pairing(token)["expired"])
        with self.assertRaises(ChannelError):
            self.channels.add_push(subscription, "Late", token)

    def test_an_alert_goes_encrypted_with_its_picture_address(self):
        key, subscription = browser()
        self.channels.add_push(subscription, "Chrome on Android")
        results = self.channels.alert("A car blocks the gate", "Gate", b"jpeg", "https://x.si/#/activity", "https://x.si")
        self.assertTrue(results[0]["ok"])
        request = self.opener.last("fcm.googleapis.com")
        self.assertEqual(request.get_header("Content-encoding"), "aes128gcm")
        self.assertTrue(request.get_header("Authorization").startswith("vapid t="))
        payload = json.loads(decrypt(request.data, key, subscription))
        self.assertEqual((payload["title"], payload["body"], payload["url"]), ("Gate", "A car blocks the gate", "https://x.si/#/activity"))
        name = payload["image"].rsplit("/", 1)[1]
        self.assertEqual(self.channels.picture(name), b"jpeg")
        self.assertIsNone(self.channels.picture("../channels.json"))

    def test_a_gone_browser_is_removed(self):
        _, subscription = browser()
        self.channels.add_push(subscription, "Old phone")
        self.opener.answer("fcm.googleapis.com", 410)
        results = self.channels.alert("Hello")
        self.assertFalse(results[0]["ok"])
        self.assertIn("removed it", results[0]["detail"])
        self.assertEqual(self.channels.all(), [])


class Telegram(Places):
    def setUp(self):
        super().setUp()
        self.opener.answer("/getMe", body={"ok": True, "result": {"username": "whenever_ab12_bot", "first_name": "Whenever"}})
        self.opener.answer("/sendMessage", body={"ok": True, "result": {}})
        self.opener.answer("/sendPhoto", body={"ok": True, "result": {}})

    def updates(self, *texts, chat=42):
        self.opener.answer("/getUpdates", body={"ok": True, "result": [
            {"update_id": i + 1, "message": {"chat": {"id": chat, "type": "private"}, "from": {"first_name": "Asha"},
                                             "text": text}} for i, text in enumerate(texts)]})

    def test_a_token_must_look_right_and_telegram_must_accept_it(self):
        with self.assertRaisesRegex(ChannelError, "no bot token"):
            self.channels.set_telegram_token("hello")
        self.assertEqual(self.channels.set_telegram_token(BOT)["username"], "whenever_ab12_bot")
        self.assertIn(f"TELEGRAM_BOT_TOKEN={BOT}", self.secrets.read_text())
        self.assertNotIn(BOT, json.dumps(self.channels.public()))

    def test_the_token_is_found_in_botfathers_whole_answer(self):
        answer = (f"Done! Congratulations on your new bot. You will find it at t.me/Whenever_ab12_bot. "
                  f"See /help for a list of commands.\n\nUse this token to access the HTTP API:\n{BOT}\n"
                  f"Keep your token secure and store it safely.\n\nFor a description of the Bot API, "
                  f"see this page: https://core.telegram.org/bots/api")
        self.assertEqual(channels.find_bot_token(answer), BOT)
        self.assertEqual(channels.find_bot_token(f"  {BOT}\n"), BOT)
        # A text box can drop the line breaks, so the token touches the words around it.
        self.assertEqual(channels.find_bot_token(answer.replace("\n", "")), BOT)
        self.channels.set_telegram_token(answer)
        self.assertIn(f"TELEGRAM_BOT_TOKEN={BOT}\n", self.secrets.read_text())

    def test_no_token_or_two_tokens_say_what_to_do(self):
        with self.assertRaisesRegex(ChannelError, "no bot token"):
            channels.find_bot_token("Done! Congratulations on your new bot.")
        other = "987654321:AAHotherotherotherotherotherother12"
        with self.assertRaisesRegex(ChannelError, "more than one"):
            channels.find_bot_token(f"{BOT}\n{other}")
        self.assertEqual(channels.find_bot_token(f"{BOT} and again {BOT}"), BOT)

    def test_a_refused_token_is_not_kept(self):
        self.opener.answer("/getMe", 401)
        with self.assertRaisesRegex(ChannelError, "did not accept"):
            self.channels.set_telegram_token(BOT)
        self.assertNotIn("TELEGRAM", self.secrets.read_text())

    def test_start_with_the_token_pairs_the_chat_and_stop_ends_it(self):
        self.channels.set_telegram_token(BOT)
        token = self.channels.start("telegram")
        self.updates(f"/start {token}")
        self.channels.poll_telegram(timeout=0)
        self.assertTrue(self.channels.pairing(token)["done"])
        self.assertEqual(self.channels.all()[0]["label"], "Telegram: Asha")
        self.assertIn(b"Paired", self.opener.last("/sendMessage").data)
        self.updates("/stop")
        self.channels.update_offset = 0
        self.channels.poll_telegram(timeout=0)
        self.assertEqual(self.channels.all(), [])

    def test_a_stranger_cannot_pair_without_the_token(self):
        self.channels.set_telegram_token(BOT)
        self.channels.start("telegram")
        self.updates("/start", "/start wrong-token")
        self.channels.poll_telegram(timeout=0)
        self.assertEqual(self.channels.all(), [])

    def test_an_alert_sends_the_picture_and_an_open_button(self):
        self.channels.set_telegram_token(BOT)
        self.channels.places["telegram"] = {"chat_id": 42, "name": "Asha", "t": 0}
        results = self.channels.alert("A car <blocks> the gate", "Gate", b"jpeg", "https://x.si/#/activity")
        self.assertTrue(results[0]["ok"])
        body = self.opener.last("/sendPhoto").data
        self.assertIn(b"<b>Gate</b>\nA car &lt;blocks&gt; the gate", body)
        self.assertIn(b"Open in Whenever", body)
        self.assertIn(b"jpeg", body)

    def test_a_clip_goes_as_a_video_that_plays_in_the_chat(self):
        self.opener.answer("/sendVideo", body={"ok": True, "result": {}})
        self.channels.set_telegram_token(BOT)
        self.channels.places["telegram"] = {"chat_id": 42, "name": "Asha", "t": 0}
        results = self.channels.alert("The car moved", "Car · Driveway", b"jpeg", None, clip=b"\x00\x00\x00\x18ftypmp42")
        self.assertTrue(results[0]["ok"])
        body = self.opener.last("/sendVideo").data
        self.assertIn(b"ftypmp42", body)
        self.assertIn(b"supports_streaming", body)

    def test_an_error_never_shows_the_bot_token(self):
        self.channels.set_telegram_token(BOT)
        self.channels.places["telegram"] = {"chat_id": 42, "name": "Asha", "t": 0}

        def broken(request, timeout=None):
            raise urllib.error.URLError(f"cannot reach {request.full_url}")
        self.channels.opener = mock.Mock(open=broken)
        detail = self.channels.alert("Hello")[0]["detail"]
        self.assertNotIn(BOT, detail)


class Email(Places):
    def setUp(self):
        super().setUp()
        self.secrets.write_text("AWS_SES_ACCESS_KEY_ID=AKIDEXAMPLE\nAWS_SES_SECRET_ACCESS_KEY=secretsecret\n"
                                "WHENEVER_MAIL_FROM=Whenever <alerts@example.com>\n")
        self.opener.answer("amazonaws.com", body={"MessageId": "m1"})

    def sent(self):
        request = self.opener.last("amazonaws.com")
        body = json.loads(request.data)
        return request, body, base64.b64decode(body["Content"]["Raw"]["Data"]).decode()

    def test_an_address_pairs_only_after_the_button_in_the_email(self):
        token = self.channels.start_email("me@example.com", lambda t: f"https://x.si/pair/{t}")
        request, body, raw = self.sent()
        self.assertEqual(request.full_url, "https://email.ap-south-1.amazonaws.com/v2/email/outbound-emails")
        self.assertTrue(request.get_header("Authorization").startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/"))
        self.assertEqual(body["Destination"]["ToAddresses"], ["me@example.com"])
        self.assertIn(f"https://x.si/pair/{token}", raw)
        self.assertEqual(self.channels.all(), [])
        place = self.channels.confirm_email(token)
        self.assertEqual(self.channels.all()[0]["label"], "me@example.com")
        self.assertEqual(place["address"], "me@example.com")

    def test_pairing_emails_stop_after_a_few_in_an_hour(self):
        for _ in range(channels.MAX_PAIR_MAILS_HOUR):
            self.channels.start_email("me@example.com", str)
        with self.assertRaisesRegex(ChannelError, "many pairing emails"):
            self.channels.start_email("you@example.com", str)

    def test_a_bad_address_sends_nothing(self):
        with self.assertRaisesRegex(ChannelError, "full email address"):
            self.channels.start_email("me at home", str)
        self.assertEqual(self.opener.requests, [])

    def test_an_alert_carries_the_picture_inside(self):
        self.channels.places["email"] = [{"id": "e1", "address": "me@example.com", "t": 0}]
        self.channels.alert("A car blocks the gate", "Gate", b"\xff\xd8jpeg", "https://x.si/#/activity")
        _, _, raw = self.sent()
        self.assertIn("Subject: Gate", raw)
        self.assertIn("Content-Type: image/jpeg", raw)
        self.assertIn("cid:", raw)

    def test_a_clip_is_attached_to_the_email(self):
        self.channels.places["email"] = [{"id": "e1", "address": "me@example.com", "t": 0}]
        self.channels.alert("The car moved", "Car · Driveway", b"\xff\xd8jpeg", None, clip=b"ftypclip")
        _, _, raw = self.sent()
        self.assertIn("Content-Type: video/mp4", raw)
        self.assertIn('filename="clip.mp4"', raw)

    def test_keys_can_come_from_an_aws_profile(self):
        home = self.folder / "home"
        (home / ".aws").mkdir(parents=True)
        (home / ".aws" / "credentials").write_text("[mail]\naws_access_key_id = AKIDPROFILE\naws_secret_access_key = s\n")
        with mock.patch.object(Path, "home", return_value=home):
            keys = channels.aws_keys({"AWS_SES_PROFILE": "mail"})
        self.assertEqual(keys[:2], ("AKIDPROFILE", "s"))
        self.assertIsNone(channels.aws_keys({"AWS_SES_PROFILE": "none"}))

    def test_without_keys_email_says_what_it_needs(self):
        self.secrets.write_text("")
        self.channels.places["email"] = [{"id": "e1", "address": "me@example.com", "t": 0}]
        self.assertIn("SES", self.channels.alert("Hello")[0]["detail"])
        self.assertFalse(self.channels.public()["email_ready"])


class ProblemLog(unittest.TestCase):
    def test_repeats_join_one_group_and_a_note_needs_nobody(self):
        problems = Problems(clock=lambda: 100)
        for _ in range(5):
            group, fresh = problems.add("miss:r1", "Spark missed a check", "note", "Muse Spark", "did not answer")
        self.assertEqual((group["count"], group["open"]), (5, False))
        self.assertEqual(len(problems.public()["groups"]), 1)
        self.assertEqual(problems.open(), [])

    def test_a_warning_stays_open_until_it_passes(self):
        problems = Problems(clock=lambda: 100)
        problems.add("stuck:r1", "Gate cannot check", "warn")
        self.assertEqual(len(problems.open()), 1)
        problems.resolve("stuck:r1")
        self.assertEqual(problems.open(), [])
        self.assertEqual(problems.public()["groups"][0]["resolved"], 100)

    def test_health_counts_calls_and_misses(self):
        problems = Problems(clock=lambda: 100)
        for seconds in (1, 2, 3, 4):
            problems.call("Muse Spark", seconds, t=90)
        problems.call("Muse Spark", error="Meta API did not answer in time.", t=95)
        row = problems.health()[0]
        self.assertEqual((row["calls"], row["failed"], row["median_s"], row["slowest_s"]), (5, 1, 2.5, 4))

    def test_groups_come_back_after_a_restart(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        Problems(folder / "p.json").add("a", "A rule stopped", "error")
        again = Problems(folder / "p.json")
        self.assertEqual(again.open()[0]["title"], "A rule stopped")
        self.assertEqual(again.add("b", "Next")[0]["id"], 2)


class StuckSensor(unittest.TestCase):
    def test_misses_become_a_warning_only_when_they_last(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        app = server.App(object(), folder, inline=True)
        rule = server.Rule(app, "r1", "Gate", {"name": "Gate", "sensors": [
            {"id": "s1", "question": "Is the gate open?"}]}, folder)
        sensor = rule.sensors[0]
        clock = [1000.0]
        with mock.patch.object(server.time, "time", lambda: clock[0]):
            app._check_health(rule, sensor, "Meta API did not answer in time.")
            clock[0] += 10
            app._check_health(rule, sensor, "Meta API did not answer in time.")
            self.assertEqual(app.problems.open(), [])
            clock[0] += server.STUCK_S
            app._check_health(rule, sensor, "Meta API did not answer in time.")
            self.assertEqual(app.problems.open()[0]["title"], "Gate cannot check right now")
            app._check_health(rule, sensor, None)
            self.assertEqual(app.problems.open(), [])
        self.assertEqual(app.status()["problems"], [])


class PairingPages(unittest.TestCase):
    """A phone opens the pairing page with no login, and the page on the computer sees it finish."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        secrets = self.folder / "actions.env"
        secrets.write_text("")
        self.opener = Opener()
        app = server.App(object(), self.folder, inline=True,
                         connectors=Connectors(secrets_path=secrets, opener=self.opener))
        (self.folder / "access.env").write_text("tall-river-42\n")
        access = server.Access(self.folder / "access.env", self.folder / "sessions.json")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (server.Handler,), {"app": app, "access": access}))
        self.port = self.httpd.server_address[1]
        patch = mock.patch.object(server, "PORT", self.port)
        patch.start()
        self.addCleanup(patch.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.cookie = "whenever=" + access.login("tall-river-42")

    def call(self, method, path, body=None, cookie=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, reply.read(), reply.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_a_phone_pairs_from_the_link_and_gets_a_login(self):
        status, body, _ = self.call("POST", "/api/pair/start", {"kind": "push"}, self.cookie)
        start = json.loads(body)
        token = start["token"]
        self.assertTrue(start["url"].endswith(f"/pair/{token}"))
        self.assertEqual(self.call("POST", "/api/pair/start", {"kind": "push"})[0], 401)
        status, page, _ = self.call("GET", f"/pair/{token}")
        self.assertEqual(status, 200)
        self.assertIn(f"/pair/{token}/manifest.webmanifest".encode(), page)
        info = json.loads(self.call("GET", f"/api/pair/{token}/info")[1])
        self.assertTrue(info["valid"])
        self.assertGreater(len(info["push_key"]), 80)
        _, subscription = browser()
        status, _, headers = self.call("POST", f"/api/pair/{token}/push", {"subscription": subscription, "label": "Chrome on Android"})
        self.assertEqual(status, 200)
        self.assertIn("whenever=", headers.get("Set-Cookie"))
        self.assertIn("fcm.googleapis.com", self.opener.requests[-1].full_url)
        done = json.loads(self.call("GET", f"/api/pair/{token}", cookie=self.cookie)[1])
        self.assertEqual((done["done"], done["place"]["label"]), (True, "Chrome on Android"))
        self.assertEqual(self.call("POST", f"/api/pair/{token}/push", {"subscription": subscription})[0], 400)

    def test_a_phone_becomes_the_camera_from_its_link_once(self):
        start = json.loads(self.call("POST", "/api/pair/start", {"kind": "camera"}, self.cookie)[1])
        self.assertTrue(start["url"].endswith(f"/camera/{start['token']}"))

        class Stay(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        opener = urllib.request.build_opener(Stay)
        url = f"http://127.0.0.1:{self.port}/camera/{start['token']}"
        with self.assertRaises(urllib.error.HTTPError) as first:
            opener.open(url, timeout=5)
        self.assertEqual(first.exception.code, 302)
        self.assertEqual(first.exception.headers["Location"], "/#/phone-camera")
        self.assertIn("whenever=", first.exception.headers["Set-Cookie"])
        done = json.loads(self.call("GET", f"/api/pair/{start['token']}", cookie=self.cookie)[1])
        self.assertTrue(done["done"])
        with self.assertRaises(urllib.error.HTTPError) as again:
            opener.open(url, timeout=5)
        self.assertEqual(again.exception.headers["Location"], f"/pair/{start['token']}")
        self.assertIsNone(again.exception.headers.get("Set-Cookie"))

    def test_a_clip_plays_in_parts_for_safari(self):
        rules = self.folder / "rules"
        (rules / "abc123" / "clips").mkdir(parents=True)
        (rules / "abc123" / "clips" / "1.mp4").write_bytes(b"0123456789")
        with mock.patch.object(server.sandbox, "RULES_DIR", rules):
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}/rule-file/abc123/clips/1.mp4",
                                             headers={"Cookie": self.cookie, "Range": "bytes=2-5"})
            with urllib.request.urlopen(request, timeout=5) as reply:
                self.assertEqual((reply.status, reply.read(), reply.headers["Content-Range"]), (206, b"2345", "bytes 2-5/10"))
            status, body, headers = self.call("GET", "/rule-file/abc123/clips/1.mp4", cookie=self.cookie)
            self.assertEqual((status, body, headers["Content-Type"]), (200, b"0123456789", "video/mp4"))

    def test_one_command_gets_the_script_and_the_reader_without_a_login(self):
        status, script, _ = self.call("GET", "/connect.sh")
        self.assertEqual(status, 200)
        self.assertIn(b"--setup", script)
        self.assertNotIn(b"__SERVER__", script)
        self.assertNotIn(b"tall-river-42", script)
        status, code, _ = self.call("GET", "/connect/camera.py")
        self.assertEqual(status, 200)
        self.assertIn(b"def setup(", code)
        self.assertEqual(self.call("GET", "/connect/server.py")[0], 401)

    def test_the_live_view_comes_as_one_stream_of_pictures(self):
        app = self.httpd.RequestHandlerClass.app
        app.live_jpeg, app.live_seq = b"\xff\xd8picture\xff\xd9", 7
        with mock.patch.object(server, "LIVE_STREAM_S", 0.5):
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/live.mjpg", headers={"Cookie": self.cookie})
            with urllib.request.urlopen(request, timeout=10) as reply:
                body = reply.read()
                self.assertIn("multipart/x-mixed-replace", reply.headers["Content-Type"])
        self.assertIn(b"Content-Length: 11\r\nX-Seq: 7\r\n\r\n\xff\xd8picture\xff\xd9", body)
        self.assertEqual(self.call("GET", "/api/live.mjpg")[0], 401)

    def test_the_live_view_from_the_media_server_needs_a_login(self):
        from http.server import BaseHTTPRequestHandler as Base

        class Media(Base):
            def do_GET(self):
                if "cookieCheck" not in self.path:
                    self.send_response(302)
                    self.send_header("Location", "/driveway/index.m3u8?cookieCheck=1")
                    self.send_header("Set-Cookie", "s=1; Path=/")
                    self.end_headers()
                    return
                body = b"#EXTM3U"
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        media = ThreadingHTTPServer(("127.0.0.1", 0), Media)
        threading.Thread(target=media.serve_forever, daemon=True).start()
        self.addCleanup(media.shutdown)
        with mock.patch.object(server, "HLS_URL", f"http://127.0.0.1:{media.server_address[1]}"):
            self.assertEqual(self.call("GET", "/hls/driveway/index.m3u8")[0], 401)

            class Stay(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args):
                    return None
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}/hls/driveway/index.m3u8", headers={"Cookie": self.cookie})
            with self.assertRaises(urllib.error.HTTPError) as moved:
                urllib.request.build_opener(Stay).open(request, timeout=5)
            self.assertEqual(moved.exception.headers["Location"], "/hls/driveway/index.m3u8?cookieCheck=1")
            self.assertIn("Path=/hls/", moved.exception.headers["Set-Cookie"])
            status, body, headers = self.call("GET", "/hls/driveway/index.m3u8?cookieCheck=1", cookie=self.cookie)
            self.assertEqual((status, body), (200, b"#EXTM3U"))
            self.assertEqual(self.call("GET", "/hls/../secret", cookie=self.cookie)[0], 404)

    def test_a_bad_token_gets_no_page(self):
        self.assertEqual(self.call("GET", "/pair/<script>")[0], 401)
        self.assertFalse(json.loads(self.call("GET", "/api/pair/nothing-like-this/info")[1])["valid"])

    def test_the_worker_and_the_manifest_need_no_login(self):
        self.assertEqual(self.call("GET", "/sw.js")[0], 200)
        manifest = json.loads(self.call("GET", "/manifest.webmanifest")[1])
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(self.call("GET", "/icons/icon-192.png")[0], 200)
        self.assertEqual(self.call("GET", "/api/problems")[0], 401)
        self.assertEqual(self.call("GET", "/api/problems", cookie=self.cookie)[0], 200)


if __name__ == "__main__":
    unittest.main()
