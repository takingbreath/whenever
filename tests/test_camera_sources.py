"""Camera makes, ONVIF, finding cameras, snapshots, and sending to a hosted Whenever."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import camera  # it adds vendor/ to the path, where Pillow is
import camera_sources as sources
from camera_sources import SourceError

DEVICE_TIME = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>
<tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:SystemDateAndTime><tt:UTCDateTime>
<tt:Time><tt:Hour>10</tt:Hour><tt:Minute>0</tt:Minute><tt:Second>0</tt:Second></tt:Time>
<tt:Date><tt:Year>2026</tt:Year><tt:Month>11</tt:Month><tt:Day>3</tt:Day></tt:Date></tt:UTCDateTime>
</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse></s:Body></s:Envelope>"""
CAPABILITIES = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>
<tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Capabilities><tt:Media>
<tt:XAddr>http://192.168.1.70/onvif/Media</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse></s:Body></s:Envelope>"""
PROFILES = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>
<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><trt:Profiles token="main_1"/><trt:Profiles token="sub_1"/>
</trt:GetProfilesResponse></s:Body></s:Envelope>"""
STREAM = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>
<trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><trt:MediaUri>
<tt:Uri>rtsp://0.0.0.0:554/Streaming/Channels/101?transportmode=unicast</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse></s:Body></s:Envelope>"""
MATCH = b"""<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><e:Body>
<d:ProbeMatches><d:ProbeMatch><d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/C320WS
onvif://www.onvif.org/hardware/Tapo_C320WS</d:Scopes><d:XAddrs>http://192.168.1.40:2020/onvif/device_service</d:XAddrs>
</d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>"""


class Reply:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.requests = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return Reply(body)

    open = urlopen


class Makes(unittest.TestCase):
    def test_each_make_gives_its_known_paths(self):
        self.assertEqual(sources.candidates("hikvision://admin:pw@192.168.1.64"),
                         ["rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/101"])
        self.assertEqual(sources.candidates("hikvision://admin:pw@192.168.1.10/3?sub"),
                         ["rtsp://admin:pw@192.168.1.10:554/Streaming/Channels/302"])
        self.assertEqual(sources.candidates("cpplus://admin:pw@10.0.0.8/2"),
                         ["rtsp://admin:pw@10.0.0.8:554/cam/realmonitor?channel=2&subtype=0"])
        self.assertEqual(len(sources.candidates("reolink://admin:pw@10.0.0.9")), 3)
        self.assertTrue(sources.candidates("reolink://admin:pw@10.0.0.9")[0].endswith("/h264Preview_01_main"))
        self.assertEqual(sources.candidates("tapo://cam:pw@10.0.0.4?sub"), ["rtsp://cam:pw@10.0.0.4:554/stream2"])
        self.assertTrue(sources.candidates("ezviz-local://admin:ABCDEF@10.0.0.3")[0].endswith("/h264/ch1/main/av_stream"))
        self.assertTrue(sources.candidates("axis://root:pw@10.0.0.2:8554")[0].startswith("rtsp://root:pw@10.0.0.2:8554/axis-media"))

    def test_a_password_with_an_at_sign_stays_whole(self):
        self.assertEqual(sources.candidates("dahua://admin:p@ss@10.0.0.8")[0][:28], "rtsp://admin:p@ss@10.0.0.8:5")

    def test_other_addresses_stay_as_they_are(self):
        for address in ("rtsp://x/y", "clip.webm", "http://10.0.0.1/video.mjpg", "ezviz:ABC123"):
            self.assertEqual(sources.candidates(address), [address])

    def test_a_bad_channel_says_how_to_write_it(self):
        with self.assertRaisesRegex(SourceError, "channel number"):
            sources.candidates("hikvision://admin:pw@10.0.0.1/front")


class Onvif(unittest.TestCase):
    def test_the_camera_gives_its_stream_and_the_login_goes_in(self):
        opener = Opener(DEVICE_TIME, CAPABILITIES, PROFILES, STREAM)
        stream = sources.onvif_stream("onvif://admin:pw@192.168.1.70", opener)
        self.assertEqual(stream, "rtsp://admin:pw@192.168.1.70:554/Streaming/Channels/101?transportmode=unicast")
        bodies = [r.data.decode() for r in opener.requests]
        self.assertNotIn("UsernameToken", bodies[0])
        self.assertIn("PasswordDigest", bodies[1])
        self.assertNotIn(">pw<", "".join(bodies))
        self.assertIn("main_1", bodies[3])
        self.assertEqual(opener.requests[2].full_url, "http://192.168.1.70/onvif/Media")

    def test_a_refused_login_says_what_to_check(self):
        refused = urllib.error.HTTPError("http://x", 401, "no", {}, None)
        with self.assertRaisesRegex(SourceError, "refused the ONVIF login"):
            sources.onvif_stream("onvif://admin:wrong@192.168.1.70", Opener(DEVICE_TIME, refused))

    def test_a_found_camera_names_itself(self):
        found = sources.read_probe_match(MATCH)
        self.assertEqual(found, {"host": "192.168.1.40", "port": 2020, "name": "C320WS", "hardware": "Tapo C320WS"})
        self.assertIsNone(sources.read_probe_match(b"not xml"))


class Snapshots(unittest.TestCase):
    def test_a_jpeg_address_gives_pictures_and_hides_its_login(self):
        clean, _ = sources.snapshot_opener("snapshot:http://user:pw@10.0.0.6/snap.jpg")
        self.assertEqual(clean, "http://10.0.0.6/snap.jpg")
        self.assertEqual(sources.fetch_snapshot(clean, Opener(b"\xff\xd8jpeg")), b"\xff\xd8jpeg")
        with self.assertRaisesRegex(SourceError, "JPEG"):
            sources.fetch_snapshot(clean, Opener(b"<html>"))
        self.assertEqual(sources.hide_login("snapshot:http://user:pw@10.0.0.6/a"), "snapshot:http://***@10.0.0.6/a")

    def test_a_wrong_snapshot_address_says_its_shape(self):
        with self.assertRaisesRegex(SourceError, "snapshot:http"):
            sources.snapshot_opener("snapshot:ftp://x")


class Settings(unittest.TestCase):
    def test_server_lines_are_settings_and_not_cameras(self):
        folder = Path(tempfile.mkdtemp())
        config = folder / "camera.env"
        config.write_text("# home\nWHENEVER_SERVER=https://example.si\nWHENEVER_CODE=amber-1\n"
                          "tapo://u:p@10.0.0.4 Hall\n")
        self.assertEqual([c[1] for c in camera.read_cameras(config)], ["Hall"])
        settings = camera.read_settings(config)
        self.assertEqual((settings["WHENEVER_SERVER"], settings["WHENEVER_CODE"]), ("https://example.si", "amber-1"))

    def test_a_reader_tries_the_next_path_of_its_make(self):
        reader = camera.Camera("reolink://admin:pw@10.0.0.9", "Porch", threading.Event())
        first = reader.stream()
        reader.stream_at += 1
        self.assertNotEqual(reader.stream(), first)
        self.assertTrue(reader.stream().endswith("/Preview_01_main"))

    def test_ffmpeg_errors_become_advice(self):
        self.assertIn("password", camera.explain("method DESCRIBE failed: 401 Unauthorized"))
        self.assertIn("RTSP", camera.explain("Connection refused"))


class Removing(unittest.TestCase):
    def test_a_removed_camera_stops_and_the_wall_goes_on_without_it(self):
        finished = threading.Event()
        readers = [camera.Camera(f"clip{i}.webm", name, finished) for i, name in enumerate(["Driveway", "Side", "Gate"])]
        sender = camera.Sender(readers, finished)
        claims = []
        original = readers[1]
        sender._post = lambda path, body, headers: claims.append(json.loads(body))
        with mock.patch.object(camera.Camera, "start"):
            self.assertTrue(sender.apply_removed(["Side"]))
            self.assertTrue(original.halt.is_set())
            self.assertEqual(claims[-1]["cameras"], ["Driveway", "Gate"])
            self.assertFalse(sender.apply_removed(["Side"]))
            sender.apply_removed([])
        self.assertEqual(claims[-1]["cameras"], ["Driveway", "Side", "Gate"])
        self.assertIsNot(sender.cameras[1], original)
        with mock.patch.object(camera.Camera, "start"):
            sender.apply_removed(["Driveway", "Side", "Gate"])
        self.assertIsNone(sender.wall)
        self.assertEqual(claims[-1]["cameras"], [])
        sender.tick()


class Setup(unittest.TestCase):
    def test_a_password_with_odd_signs_goes_into_the_address_safely(self):
        self.assertEqual(camera.camera_line("hikvision", "10.0.0.5", "admin", "p@ss:w/rd", "3"),
                         "hikvision://admin:p%40ss%3Aw%2Frd@10.0.0.5/3")
        self.assertEqual(camera.camera_line("onvif", "10.0.0.6:2020", "cam", "pw"), "onvif://cam:pw@10.0.0.6:2020")

    def test_setup_asks_tests_and_writes_camera_env(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, folder)
        config = folder / "camera.env"
        answers = iter(["y", "1", "10.0.0.5", "admin", "1", "Gate", "n", "n"])
        with mock.patch.object(camera, "CONFIG", config), mock.patch("builtins.input", lambda prompt="": next(answers)), \
                mock.patch.object(camera.getpass, "getpass", side_effect=["amber-1", "secret"]), \
                mock.patch.object(camera.sources, "discover", return_value=[]), \
                mock.patch.object(camera, "grab_one", return_value=(1920, 1080, "rtsp://x")):
            self.assertFalse(camera.setup("https://example.si"))
        text = config.read_text()
        self.assertIn("WHENEVER_SERVER=https://example.si", text)
        self.assertIn("WHENEVER_CODE=amber-1", text)
        self.assertIn("hikvision://admin:secret@10.0.0.5 Gate", text)
        self.assertEqual(len(camera.read_cameras(config)), 1)
        self.assertEqual(oct(config.stat().st_mode & 0o777), "0o600")


class Relay(unittest.TestCase):
    """With a media server, video goes there as it is, and full decoding runs only while something changes."""

    def test_frame_sizes_come_from_framecrc_lines(self):
        self.assertEqual(camera.frame_size("0,          0,          0,      100,    56153, 0xf73c8206"), (56153, True))
        self.assertEqual(camera.frame_size("0,        100,        100,      100,       63, 0x80bd16d2, F=0x0"), (63, False))
        self.assertIsNone(camera.frame_size("#tb 0: 1/1000"))

    def test_a_second_of_larger_frames_means_change_and_keyframes_do_not(self):
        watch = camera.SizeWatch()
        for _ in range(camera.SECOND * 20):
            self.assertFalse(watch.add(900, False))
        self.assertFalse(watch.add(400000, True))
        found = [watch.add(4000, False) for _ in range(camera.SECOND)]
        self.assertTrue(found[-1])

    def test_an_encoder_that_alternates_small_and_large_frames_stays_quiet(self):
        watch = camera.SizeWatch()
        pattern = [1800, 16000, 2300, 38000, 1200, 12000, 3000, 34000, 1500, 20000]
        found = [watch.add(pattern[i % len(pattern)], False) for i in range(camera.SECOND * 40)]
        self.assertFalse(any(found))

    def test_a_quiet_camera_needs_more_than_a_few_bytes(self):
        watch = camera.SizeWatch()
        for _ in range(camera.SECOND * 20):
            watch.add(60, False)
        self.assertFalse(any(watch.add(300, False) for _ in range(camera.SECOND)))

    def test_a_camera_alarm_wakes_only_its_own_lens(self):
        alarm = {"serial": "BD1234567", "channel": "2", "t": 1}
        self.assertTrue(camera.alarm_matches("ezviz-rtp:BD1234567:2", alarm))
        self.assertFalse(camera.alarm_matches("ezviz-rtp:BD1234567:1", alarm))
        self.assertTrue(camera.alarm_matches("ezviz:BD1234567", alarm))
        self.assertFalse(camera.alarm_matches("ezviz:K12345678", alarm))
        self.assertFalse(camera.alarm_matches("rtsp://x/y", alarm))

    def test_only_new_and_recent_alarms_count(self):
        import ezviz_events
        seen = set()
        data = {"message": [{"msgId": "b", "deviceSerial": "S1", "channel": 2, "time": 100_000},
                            {"msgId": "a", "deviceSerial": "S1", "channel": 1, "time": 10_000}]}
        found = ezviz_events.new_alarms(data, seen, 120)
        self.assertEqual(found, [{"serial": "S1", "channel": "2", "t": 100.0}])
        self.assertEqual(ezviz_events.new_alarms(data, seen, 120), [])

    def test_video_is_copied_to_the_media_server_without_decoding(self):
        command = camera.publish_command("ezviz-rtp:BD1:1", "rtsp://127.0.0.1:8554/gate-wide")
        self.assertEqual(command[command.index("-c") + 1], "copy")
        self.assertIn("hevc", command)
        self.assertEqual(command[-1], "rtsp://127.0.0.1:8554/gate-wide")
        self.assertIn("-stream_loop", camera.publish_command("clip.mp4", "rtsp://x/y"))
        self.assertIn("nokey", camera.keyframes_command("rtsp://x/y"))
        self.assertEqual(camera.media_path("Gate wide"), "gate-wide")
        self.assertEqual(camera.media_path("Front door (2)"), "front-door-2")

    def test_full_decoding_settles_after_the_change_ends(self):
        reader = camera.Camera("rtsp://x/y", "Gate", threading.Event())
        self.assertTrue(reader.settled())
        reader.wake()
        self.assertTrue(reader.woken.is_set())
        self.assertFalse(reader.settled())
        reader.woke_at -= camera.LOOK_SETTLE_S + 1
        self.assertTrue(reader.settled())
        reader.demand = True
        self.assertFalse(reader.settled())

    def test_without_a_viewer_the_reader_only_says_it_runs(self):
        finished = threading.Event()
        sender = camera.Sender([camera.Camera("clip.webm", "A", finished)], finished)
        sender.live_wanted = False
        posts = []
        sender._post = lambda path, body, headers: posts.append(path)
        sender.wall.compose = lambda: self.fail("no wall is built for nobody")
        sender.gate.decide = lambda now, motion: None
        sender.tick()
        self.assertEqual(posts, ["/api/alive"])


class Hosted(unittest.TestCase):
    def test_a_reader_logs_in_once_and_again_when_its_cookie_ends(self):
        sender = camera.Sender([camera.Camera("clip.webm", "A", threading.Event())], threading.Event(),
                               "https://example.si", "amber-1")
        self.assertTrue(sender.remote)
        self.assertEqual(sender.live_fps, camera.REMOTE_LIVE_FPS)
        calls = []

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.headers.get("Cookie")))
            if request.full_url.endswith("/api/login"):
                return Reply(b'{"ok": true}', {"Set-Cookie": "whenever=abc; Path=/; HttpOnly"})
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, "no", {}, BytesIO(b"{}"))
            return Reply(b'{"name": "1.jpg"}')

        with mock.patch.object(camera.urllib.request, "urlopen", urlopen):
            self.assertEqual(sender._post("/api/frame", b"jpeg", {}), {"name": "1.jpg"})
        self.assertEqual([url.rsplit("/", 1)[1] for url, _ in calls], ["frame", "login", "frame"])
        self.assertEqual(calls[-1][1], "whenever=abc")

    def test_a_local_server_needs_no_login(self):
        sender = camera.Sender([camera.Camera("clip.webm", "A", threading.Event())], threading.Event())
        self.assertFalse(sender.remote)
        self.assertEqual(sender.live_fps, camera.LIVE_FPS)


if __name__ == "__main__":
    unittest.main()
