import sys
import tempfile
import threading
import types
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import camera  # it adds vendor/ to the path, where Pillow is
from PIL import Image


class Address(unittest.TestCase):
    def test_the_camera_code_never_reaches_a_message(self):
        text = "Failed to open rtsp://admin:ABCDEF@192.168.1.9:554/H.264 now"
        self.assertEqual(camera.hide(text), "Failed to open rtsp://***@192.168.1.9:554/H.264 now")

    def test_an_address_with_no_code_stays_the_same(self):
        self.assertEqual(camera.hide("clip.webm"), "clip.webm")

    def read(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.env"
            path.write_text(text)
            return camera.read_cameras(path)

    def test_each_line_is_one_camera_with_an_optional_name(self):
        self.assertEqual(self.read("rtsp://x\nezviz:K1 Front gate\n\n# off\n"),
                         [("rtsp://x", "", set()), ("ezviz:K1", "Front gate", set())])

    def test_a_line_can_say_what_the_camera_can_do(self):
        self.assertEqual(self.read("ezviz-rtp:K1:2 Gate tracker | moving light\n"),
                         [("ezviz-rtp:K1:2", "Gate tracker", {"moving", "light"})])

    def test_the_old_file_format_still_works(self):
        self.assertEqual(self.read("CAMERA_URL=rtsp://a:b=c@x\n"), [("rtsp://a:b=c@x", "", set())])

    def test_a_missing_file_gives_no_cameras(self):
        self.assertEqual(camera.read_cameras(Path("/no/such/file")), [])


class Command(unittest.TestCase):
    def test_a_camera_uses_tcp_and_a_file_plays_at_real_speed(self):
        live = camera.ffmpeg_command("rtsp://x")
        self.assertIn("tcp", live)
        self.assertNotIn("-re", live)
        file = camera.ffmpeg_command("clip.webm")
        self.assertIn("-re", file)
        self.assertEqual(file[file.index("-stream_loop") + 1], "-1", "a file plays in a loop, as a camera would")
        self.assertNotIn("tcp", file)

    def test_an_ezviz_camera_reads_the_account_stream_from_a_pipe(self):
        self.assertTrue(camera.is_live("ezviz:K1"))
        self.assertEqual(camera.ffmpeg_command("ezviz:K1")[3:7], ["-f", "mpegts", "-i", "pipe:0"])
        command = camera.feeder_command("ezviz:K1")
        self.assertIn("K1", command)
        self.assertEqual(command[command.index("--output") + 1], "-")
        self.assertEqual(command[command.index("--duration") + 1], "0")


    def test_an_rtp_camera_uses_its_own_helper_and_its_channel(self):
        self.assertTrue(camera.is_live("ezviz-rtp:B1:2"))
        self.assertEqual(camera.feeder_command("ezviz-rtp:B1:2")[-2:], ["B1", "2"])
        self.assertIn("hevc", camera.ffmpeg_command("ezviz-rtp:B1:2"))

    def test_a_plain_address_needs_no_helper(self):
        self.assertIsNone(camera.feeder_command("rtsp://x"))


class Layout(unittest.TestCase):
    def test_the_wall_stays_nearly_square(self):
        self.assertEqual([camera.grid(n) for n in (1, 2, 3, 4, 5, 6, 7)],
                         [(1, 1), (2, 1), (2, 2), (2, 2), (3, 2), (3, 2), (3, 3)])

    def reader(self, seq=1):
        reader = camera.Camera("clip", "One", threading.Event())
        buffer = BytesIO()
        Image.new("RGB", (1920, 1080), (200, 10, 10)).save(buffer, "JPEG")
        reader.jpeg, reader.seq = buffer.getvalue(), seq
        return reader

    def test_one_camera_fills_the_wall_with_no_label(self):
        wall = camera.Wall([self.reader()])
        self.assertEqual(wall.compose().size, (1280, 720))
        self.assertEqual(wall.small(wall.compose()).size, (640, 360))

    def test_five_cameras_share_a_wall_and_a_camera_with_no_picture_stays_dark(self):
        readers = [self.reader() for _ in range(4)] + [camera.Camera("x", "Off", threading.Event())]
        wall = camera.Wall(readers)
        picture = wall.compose()
        self.assertEqual(picture.size, (1920, 720))
        self.assertEqual(wall.small(picture).width, 1280)
        self.assertEqual(picture.getpixel((1000, 600)), (16, 16, 16))

    def test_no_picture_means_no_wall(self):
        self.assertIsNone(camera.Wall([camera.Camera("x", "Off", threading.Event())]).compose())

    def test_only_moving_cameras_keep_full_frames(self):
        still, moving = self.reader(), self.reader()
        moving.last_change = 100.0
        sender = camera.Sender([still, moving], threading.Event())
        self.assertEqual([index for index, _ in sender.moving(101.0)], [1])
        sender.gate.hold = True
        self.assertEqual([index for index, _ in sender.moving(101.0)], [0, 1])


class TwoLenses(unittest.TestCase):
    def lenses(self):
        finished = threading.Event()
        wide = camera.Camera("ezviz-rtp:K1:1", "Wide", finished)
        lens = camera.Camera("ezviz-rtp:K1:2", "Lens", finished, {"moving", "light"})
        other = camera.Camera("ezviz:K2", "Other", finished)
        for reader in (wide, lens, other):
            reader.jpeg = b"jpeg"
        return wide, lens, other

    def test_the_serial_comes_from_the_address(self):
        self.assertEqual([camera.device(a) for a in ("ezviz:K1", "ezviz-rtp:K2:1", "rtsp://x")], ["K1", "K2", ""])

    def test_a_moving_lens_gets_points_from_the_fixed_lens_beside_it(self):
        self.assertEqual(camera.controls(self.lenses()), [
            {"name": "Lens", "serial": "K1", "moves": True, "light": True, "guide": "Wide"}])

    def test_the_views_of_one_device_are_kept_together(self):
        wide, lens, other = self.lenses()
        wide.last_change = 100.0
        sender = camera.Sender([wide, lens, other], threading.Event())
        self.assertEqual([index for index, _ in sender.moving(101.0)], [0, 1])

    def test_a_moving_lens_cannot_wake_the_checks(self):
        wide, lens, other = self.lenses()
        lens.motion = 90.0
        sender = camera.Sender([wide, lens, other], threading.Event())
        sender._post = lambda *args: {}
        sender.wall.compose = lambda: Image.new("RGB", (32, 18))
        sender.gate.last_sent = 1e12
        sender.tick()
        self.assertEqual(sender.gate.peak, 0.0)
        wide.motion = 90.0
        sender.tick()
        self.assertEqual(sender.gate.peak, 90.0)


class Frames(unittest.TestCase):
    def test_a_frame_cut_by_a_read_waits_for_its_end(self):
        found, rest = camera.split_jpegs(b"\xff\xd8a\xff\xd9\xff\xd8b\xff\xd9\xff\xd8c")
        self.assertEqual(found, [b"\xff\xd8a\xff\xd9", b"\xff\xd8b\xff\xd9"])
        self.assertEqual(rest, b"\xff\xd8c")

    def test_the_first_frame_has_no_change(self):
        self.assertEqual(camera.change_level(bytes(64 * 36), None), 0.0)

    def test_a_change_in_one_block_counts_in_full(self):
        before = bytearray(64 * 36)
        after = bytearray(before)
        for y in range(8):
            for x in range(8):
                after[y * 64 + x] = 100
        self.assertEqual(camera.change_level(bytes(after), bytes(before)), 100.0)
        self.assertEqual(camera.change_level(bytes(before), bytes(before)), 0.0)


class Turning(unittest.TestCase):
    """No camera says whether its lens turns, so the picture itself shows it."""

    def scene(self, left):
        from PIL import ImageFilter
        texture = Image.effect_noise((200, 120), 60).filter(ImageFilter.GaussianBlur(1.5))
        return texture, texture.crop((left, 20, left + 128, 92)).resize(camera.SMALL_SIZE).tobytes()

    def test_a_pan_moves_the_whole_picture_and_a_person_does_not(self):
        texture = Image.effect_noise((200, 120), 60)
        def view(left):
            return texture.crop((left, 20, left + 128, 92)).resize(camera.SMALL_SIZE).tobytes()
        still = view(20)
        self.assertIsNotNone(camera.whole_shift(still, view(26)))
        person = Image.frombytes("L", camera.SMALL_SIZE, still)
        person.paste(255, (10, 8, 22, 30))
        self.assertIsNone(camera.whole_shift(still, person.tobytes()))
        self.assertIsNone(camera.whole_shift(still, bytes(min(255, v + 40) for v in still)))
        self.assertIsNone(camera.whole_shift(still, still))
        self.assertIsNone(camera.whole_shift(None, still))

    def test_a_turning_lens_makes_no_motion(self):
        reader = camera.Camera("rtsp://x", "Gate tracker", threading.Event())
        texture = Image.effect_noise((400, 240), 60)
        def jpeg(left):
            out = BytesIO()
            texture.crop((left, 40, left + 256, 184)).save(out, "JPEG", quality=95)
            return out.getvalue()
        reader.frame(jpeg(40))
        reader.shift_at = float("-inf")
        reader.frame(jpeg(52))
        self.assertTrue(reader.turning(camera.time.monotonic()))
        self.assertEqual(reader.take_motion(), 0.0)


    def test_a_grey_frame_from_a_decoder_start_is_dropped(self):
        reader = camera.Camera("rtsp://x", "Gate wide", threading.Event())
        scene, grey = BytesIO(), BytesIO()
        Image.effect_noise((256, 144), 60).convert("RGB").save(scene, "JPEG", quality=90)
        Image.new("RGB", (256, 144), (128, 128, 128)).save(grey, "JPEG")
        reader.frame(scene.getvalue())
        reader.frame(grey.getvalue())
        self.assertEqual((reader.seq, reader.jpeg), (1, scene.getvalue()))
        self.assertEqual(reader.take_motion(), 0.0)
        # A picture that stays grey is real, so it is kept in the end, and every frame after it too.
        for _ in range(camera.BROKEN_LIMIT + 1):
            reader.frame(grey.getvalue())
        self.assertEqual(reader.jpeg, grey.getvalue())
        seq = reader.seq
        for _ in range(5):
            reader.frame(grey.getvalue())
        self.assertEqual(reader.seq, seq + 5)

    def test_a_mostly_grey_broken_frame_is_dropped_too(self):
        reader = camera.Camera("rtsp://x", "Gate wide", threading.Event())
        scene, broken = BytesIO(), BytesIO()
        Image.effect_noise((256, 144), 60).convert("RGB").save(scene, "JPEG", quality=90)
        # About 85% flat grey with some garbage, like the frame at 07:06:18.
        picture = Image.new("RGB", (256, 144), (128, 128, 128))
        picture.paste(Image.effect_noise((100, 40), 80).convert("RGB"), (60, 50))
        picture.save(broken, "JPEG")
        reader.frame(scene.getvalue())
        reader.frame(broken.getvalue())
        self.assertEqual(reader.seq, 1)


class Clock(unittest.TestCase):
    """A decoder that is slower than the camera falls behind the live video, and its frames say so."""

    def test_progress_lines_are_read_and_others_are_errors(self):
        clock = camera.VideoClock()
        self.assertTrue(clock.progress("out_time_us=2500000\n"))
        self.assertTrue(clock.progress("progress=continue\n"))
        self.assertTrue(clock.progress("stream_0_0_q=3.0\n"))
        self.assertFalse(clock.progress("[hevc @ 0x1] Could not find ref with POC 9\n"))
        self.assertEqual(clock.video_s, 2.5)

    def test_a_decoder_that_keeps_up_is_not_behind(self):
        clock = camera.VideoClock()
        clock.progress("out_time_us=0\n")
        self.assertEqual(clock.taken(100.0), 100.0)  # still waiting for its first keyframe
        clock.progress("out_time_us=125000\n")
        self.assertEqual(clock.taken(104.0), 104.0)
        clock.progress("out_time_us=10125000\n")
        self.assertAlmostEqual(clock.behind(114.0), 0.0)

    def test_a_slow_decoder_falls_behind(self):
        clock = camera.VideoClock()
        clock.progress("out_time_us=125000\n")
        clock.taken(100.0)
        clock.progress("out_time_us=9125000\n")  # 9 s of video in 15 s
        self.assertAlmostEqual(clock.taken(115.0), 109.0)
        self.assertAlmostEqual(clock.behind(115.0), 6.0)
        self.assertGreater(clock.behind(115.0), camera.BEHIND_LIMIT_S)

    def test_a_frame_says_when_it_was_taken(self):
        reader = camera.Camera("rtsp://x", "Gate wide", threading.Event())
        scene = BytesIO()
        Image.effect_noise((256, 144), 60).convert("RGB").save(scene, "JPEG", quality=90)
        reader.frame(scene.getvalue(), 1234.5)
        self.assertEqual(reader.taken, 1234.5)

    def test_a_moment_is_stamped_when_its_busy_cameras_took_it(self):
        sender = types.SimpleNamespace(last_t=0.0)
        sender.frame_time = lambda active, full: camera.Sender.frame_time(sender, active, full)
        late, fresh = types.SimpleNamespace(taken=0.0), types.SimpleNamespace(taken=0.0)
        now = camera.time.time()
        late.taken, fresh.taken = now - 3.0, now - 0.5
        self.assertAlmostEqual(sender.frame_time([late, fresh], [(0, b""), (1, b"")]), now - 3.0, delta=0.05)
        # Never more than the decoder's limit behind, and never before the moment sent last.
        late.taken = now - 60
        self.assertGreaterEqual(sender.frame_time([late], [(0, b"")]), now - camera.BEHIND_LIMIT_S - 1.1)
        self.assertGreater(sender.frame_time([late], [(0, b"")]), sender.last_t - 0.01)
        # With no busy camera, the moment is now.
        self.assertAlmostEqual(sender.frame_time([late], []), camera.time.time(), delta=0.05)


class Sending(unittest.TestCase):
    def test_a_still_picture_sends_one_frame_a_minute(self):
        gate = camera.Gate()
        sent = [t for t in range(0, 150) if gate.decide(float(t), 0.0) is not None]
        self.assertEqual(sent, [0, 60, 120])

    def test_a_change_sends_at_the_capture_rate_and_then_settles(self):
        gate = camera.Gate()
        gate.decide(0.0, 0.0)
        self.assertEqual(gate.decide(1.0, 50.0), 50.0)
        self.assertIsNone(gate.decide(1.1, 0.0))
        self.assertIsNotNone(gate.decide(1.4, 0.0))
        self.assertIsNone(gate.decide(5.0, 0.0))

    def test_a_skipped_peak_goes_with_the_next_frame(self):
        gate = camera.Gate()
        gate.decide(0.0, 0.0)
        gate.decide(1.0, 9.0)
        gate.decide(1.1, 80.0)
        self.assertEqual(gate.decide(1.4, 0.0), 80.0)

    def test_a_waiting_rule_keeps_frames_coming_in_a_still_picture(self):
        gate = camera.Gate()
        gate.decide(0.0, 0.0)
        gate.hold = True
        self.assertIsNotNone(gate.decide(1.0, 0.0))


if __name__ == "__main__":
    unittest.main()
