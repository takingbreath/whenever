"""Sends security cameras to the server, in place of the browser page.

Each camera has its own reader, so a slow camera cannot hold up the others. The cameras go on one
wall, so one model call can watch all of them. Sensors get a small wall and rule code gets a larger
one. Each camera's full frame is also saved while it has movement, so zoom has real pixels. Frames go
at the capture rate while any picture changes, and once every 10 s while all are still. Frames saved
with no rule cost nothing, so the footage can be searched later.

Usage: python3 camera.py [address ...]
       python3 camera.py --find          finds ONVIF cameras on this network
       python3 camera.py --test [name]   reads one frame from each camera in camera.env
With no address, it reads camera.env, with one camera on each line: an address, a name, then what it
can do, such as "ezviz-rtp:ABC123:2 Gate tracker | moving light". An address is a video file, an
rtsp:// or http:// stream, a short address for a make of camera (hikvision://, dahua://, cpplus://,
reolink://, tapo://, ezviz-local:// and more, see camera_sources.py), onvif:// for any ONVIF camera,
snapshot:http://... for a JPEG address, ezviz:SERIAL for an EZVIZ camera with no RTSP, or
ezviz-rtp:SERIAL:CHANNEL for one that sends RTP. camera.env stays out of the repo.

To send to a hosted Whenever, add two lines to camera.env:
WHENEVER_SERVER=https://wheneverwhatever.si
WHENEVER_CODE=the access code

A moving lens points where it is told and follows people by itself, so its picture changes with no
event. Only the fixed cameras can wake the checks. The views of one device are kept together, so the
closer view is there when a fixed lens sees something.
"""

import getpass
import json
import statistics
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat  # noqa: E402

import camera_sources as sources  # noqa: E402

CONFIG = ROOT / "camera.env"
EZVIZ = "ezviz:"
EZVIZ_RTP = "ezviz-rtp:"
SERVER = f"http://127.0.0.1:{os.environ.get('WHENEVER_PORT', 8787)}"
SETTINGS = ("WHENEVER_SERVER", "WHENEVER_CODE")
FRAME_WIDTH = 640
SHARP_WIDTH = 1280
# A small server reads many cameras, so it can ask for smaller and fewer frames than a Mac does.
FULL_WIDTH = int(os.environ.get("WHENEVER_FULL_WIDTH", 2880))
# On a wall, each camera keeps enough pixels for a model to see a person.
WALL_TILE_WIDTH = 480
WALL_FRAME_WIDTH = 1280
WALL_SHARP_WIDTH = 1920
SMALL_SIZE = (64, 36)
CELL = 8
CHANGE_LEVEL = 5
# A decoder that starts between two keyframes fills the picture with flat grey until the next keyframe.
# Such a frame is dropped: it shows nothing, and the jump to grey looks like a big change. Broken frames
# were 78 to 97% this grey. Normal frames of these cameras were at most 50%, by day and by night.
BROKEN_SHARE = 0.75
# A picture that stays flat grey this many frames in a row is real, such as thick fog. From then on it
# is kept, until a normal frame comes.
BROKEN_LIMIT = 40
SETTLE_S = 2.5
# A still picture is sent once a minute, as a safety net. Anything that happens moves, and movement
# sends frames at once at the rate the rules ask for. A rule about a pointed thing asks its model only
# when the thing's place changed, or every 10 minutes (server.py, QUIET_SAFETY_S).
IDLE_CHECK_S = 60.0
READ_FPS = int(os.environ.get("WHENEVER_READ_FPS", 8))
DECODE_THREADS = 2
# Skipping the decoder's smoothing step costs a little sharpness and saves much of the decoding work.
FAST_DECODE = os.environ.get("WHENEVER_FAST_DECODE") == "1"
# With a media server here (MediaMTX), each camera's video goes there as it is, for the live view, the
# buffer and clips. This reader then decodes in full only while something changes.
MEDIA = os.environ.get("WHENEVER_MEDIA", "").rstrip("/")
# A second of compressed video takes more bytes when something moves. One second evens out encoders that
# alternate small and large frames: a quiet H.265 camera stayed under 1.3 times its usual second.
SECOND = 15             # frames in one second at the cameras' usual 15 frames a second
ACTIVE_RATIO = 1.6      # a second this many times the usual second means that something moved
ACTIVE_BYTES = 9000     # and at least this many bytes more, so a quiet camera's small seconds do not count
USUAL_SECONDS = 30      # the usual second is the median of the last 30
LOOK_SETTLE_S = 8.0     # full decoding stops this long after the last sign of change
ALIVE_S = 2.0           # with no live picture to send, the server still hears that the cameras run
DEBUG = os.environ.get("WHENEVER_DEBUG_MOTION") == "1"
LIVE_FPS = 6
# A hosted server is over the internet, so the view goes less often and smaller.
REMOTE_LIVE_FPS = 2
REMOTE_LIVE_WIDTH = 1280
# The live view goes to browsers over the internet, so a server can send it smaller than the wall.
LIVE_WIDTH = int(os.environ.get("WHENEVER_LIVE_WIDTH", 0))
TEST_TIMEOUT_S = 20
MAX_IN_FLIGHT = 4
POLL_S = 2.0
RECONNECT_S = 5.0
# A decoder that falls this far behind the live video starts again at the live point. On a busy CPU, full
# decoding of a 2880 px lens ran at about 1.05 times real time, so in a long busy spell it fell up to 47 s
# behind: alerts came late, and clips cut from the recording by real time showed a later moment.
BEHIND_LIMIT_S = 5.0
# ffmpeg tells how far into the video it is, this often.
PROGRESS_S = "0.5"
# No camera says whether its lens can turn, so Whenever watches for it. A turn moves the whole picture
# by one shift, which a person walking or a light changing does not do.
TURN_CHECK_S = 0.25      # how often each camera's picture is compared for a shift
TURN_REACH = 5           # the largest shift tried, in pixels of the 64 x 36 picture
TURN_GAIN = 0.6          # the best shift must explain at least 40% of the change
TURN_HOLD_S = 1.5        # motion counts again this long after the picture stopped shifting
TURN_REPORT_S = 60       # the server hears about a turning camera at most this often
CONNECT_TIMEOUT_US = 10_000_000


def hide(text):
    """An address can hold a camera's code, so it never reaches a log."""
    return sources.hide_login(text)


def parse_camera(line):
    """Returns (address, name, abilities). The name is optional and can hold spaces."""
    address, _, rest = line.strip().removeprefix("CAMERA_URL=").partition(" ")
    name, _, abilities = rest.partition("|")
    return address, name.strip(), set(abilities.split())


def _lines(path):
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def read_cameras(path=CONFIG):
    return [parse_camera(line) for line in _lines(path) if line.partition("=")[0] not in SETTINGS]


def read_settings(path=CONFIG):
    """WHENEVER_SERVER and WHENEVER_CODE send the cameras to a hosted Whenever. The environment also works."""
    found = {name: os.environ.get(name, "") for name in SETTINGS}
    for line in _lines(path):
        name, sep, value = line.partition("=")
        if sep and name in SETTINGS:
            found[name] = value.strip()
    # On the same computer as a hosted server, the reader uses that server's own access code.
    access = path.parent / "access.env"
    if not found["WHENEVER_CODE"] and access.exists():
        found["WHENEVER_CODE"] = access.read_text().strip()
    return found


def grid(count):
    """Returns (columns, rows) for a wall of this many cameras."""
    columns = math.ceil(math.sqrt(count))
    return columns, math.ceil(count / columns)


def device(address):
    """The serial of an EZVIZ camera, or "" for a camera that takes no commands."""
    for prefix in (EZVIZ_RTP, EZVIZ):
        if address.startswith(prefix):
            return address[len(prefix):].split(":")[0]
    return ""


def is_live(address):
    return "://" in address or address.startswith((EZVIZ, EZVIZ_RTP, sources.SNAPSHOT))


def feeder_command(address):
    """The command that writes this camera's video to a pipe, or None if ffmpeg reads it directly."""
    if address.startswith(EZVIZ):
        return [str(ROOT / ".ezviz" / "bin" / "pyezvizapi"), "--token-file", str(ROOT / "ezviz_token.json"), "stream",
                "dump", "--serial", address[len(EZVIZ):], "--channel", "1", "--duration", "0", "--output", "-"]
    if address.startswith(EZVIZ_RTP):
        serial, channel = address[len(EZVIZ_RTP):].split(":")
        return [str(ROOT / ".ezviz" / "bin" / "python"), str(ROOT / "ezviz_rtp.py"), serial, channel]
    return None


def ffmpeg_command(address):
    if address.startswith(EZVIZ):
        source = ["-f", "mpegts", "-i", "pipe:0"]
    elif address.startswith(EZVIZ_RTP):
        # Plain video has no clock, so the arrival time gives each frame its time.
        source = ["-use_wallclock_as_timestamps", "1", "-f", "hevc", "-i", "pipe:0"]
    elif address.startswith("rtsp"):
        source = ["-rtsp_transport", "tcp", "-timeout", str(CONNECT_TIMEOUT_US), "-i", address]
    else:
        # A video file plays in a loop, as a camera would, the same as through the media server.
        source = [*([] if is_live(address) else ["-re", "-stream_loop", "-1"]), "-i", address]
    if FAST_DECODE and is_live(address):
        source = ["-skip_loop_filter", "all", *source]
    return ["ffmpeg", "-loglevel", "error", *source, "-an",
            "-vf", f"fps={READ_FPS},scale='min({FULL_WIDTH},iw)':-2",
            "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1"]


def media_path(name):
    """The camera's path on the media server: small letters, digits and dashes."""
    path = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return "-".join(filter(None, path.split("-")))[:40] or "camera"


def publish_command(address, target):
    """ffmpeg copies the camera's video to the media server as it is. It decodes nothing."""
    if address.startswith(EZVIZ):
        source = ["-f", "mpegts", "-i", "pipe:0"]
    elif address.startswith(EZVIZ_RTP):
        source = ["-use_wallclock_as_timestamps", "1", "-f", "hevc", "-i", "pipe:0"]
    elif address.startswith("rtsp"):
        source = ["-rtsp_transport", "tcp", "-timeout", str(CONNECT_TIMEOUT_US), "-i", address]
    elif is_live(address):
        source = ["-i", address]
    else:
        # A video file plays in a loop, as a camera would.
        source = ["-re", "-stream_loop", "-1", "-i", address]
    return ["ffmpeg", "-loglevel", "error", *source, "-map", "0:v:0", "-c", "copy",
            "-f", "rtsp", "-rtsp_transport", "tcp", target]


def sizes_command(url):
    """One line for each compressed frame, with its size, as it arrives. Nothing is decoded."""
    return ["ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url, "-map", "0:v:0", "-c", "copy",
            "-f", "framecrc", "-flush_packets", "1", "pipe:1"]


def keyframes_command(url):
    """Decodes only the keyframes, one every few seconds, so a still scene costs almost nothing."""
    return ["ffmpeg", "-loglevel", "error", "-threads", "1", "-skip_frame", "nokey", "-rtsp_transport", "tcp",
            "-i", url, "-an", "-fps_mode", "passthrough", "-filter_threads", "1",
            "-vf", f"scale='min({FULL_WIDTH},iw)':-2", "-threads", "1",
            "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1"]


def look_command(url):
    """Decodes every frame at full size. It runs only while something changes.

    ffmpeg's own thread count gives each thread a copy of its frames: about 350 MB for one 2880 px
    camera. Two threads use about 210 MB and still decode such a camera at nearly 3 times real time.
    """
    return ["ffmpeg", "-loglevel", "error", "-progress", "pipe:2", "-stats_period", PROGRESS_S,
            "-threads", str(DECODE_THREADS), "-rtsp_transport", "tcp",
            "-i", url, "-an", "-filter_threads", "1", "-vf", f"fps={READ_FPS},scale='min({FULL_WIDTH},iw)':-2",
            "-threads", str(DECODE_THREADS), "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1"]


def camera_make(address):
    """The make of a camera, for the page: ezviz, rtsp, video or other. The address itself never leaves."""
    if address.startswith((EZVIZ, EZVIZ_RTP)):
        return "ezviz"
    if address.startswith("rtsp"):
        return "rtsp"
    return "video" if not is_live(address) else "other"


def alarm_matches(address, alarm):
    """True if a camera alarm is about this camera. The two lenses of one device have their own channels."""
    if device(address) != alarm.get("serial"):
        return False
    if address.startswith(EZVIZ_RTP):
        return address.rsplit(":", 1)[-1] == str(alarm.get("channel"))
    return True


def frame_size(line):
    """(size, keyframe) from one framecrc line, or None. A keyframe's line has no F= flag."""
    if line.startswith("#"):
        return None
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 6 or not parts[4].isdigit():
        return None
    return int(parts[4]), not any(part.startswith("F=") for part in parts[5:])


class SizeWatch:
    """Notices change from how many bytes each second of compressed video takes. Nothing is decoded."""

    def __init__(self):
        self.recent = deque(maxlen=SECOND)
        self.total = 0
        self.seconds = deque(maxlen=USUAL_SECONDS)
        self.count = 0

    @property
    def sizes(self):
        return self.seconds

    def add(self, size, keyframe):
        if keyframe:
            return False
        if len(self.recent) == SECOND:
            self.total -= self.recent[0]
        self.recent.append(size)
        self.total += size
        self.count += 1
        if len(self.recent) < SECOND:
            return False
        usual = statistics.median(self.seconds) if len(self.seconds) >= 10 else None
        if self.count % SECOND == 0:
            self.seconds.append(self.total)
        return usual is not None and self.total > usual * ACTIVE_RATIO and self.total - usual > ACTIVE_BYTES


class VideoClock:
    """When each decoded frame was taken, from ffmpeg's progress: the wall time of the first frame, plus
    how far the video went since. A decoder that is slower than the camera falls behind this clock."""

    KEYS = {"frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms", "out_time", "dup_frames",
            "drop_frames", "speed", "progress"}

    def __init__(self):
        self.video_s = None
        self.first = None

    def progress(self, line):
        """Reads one line of ffmpeg's -progress output. Returns False for a line that is not progress."""
        key, _, value = line.strip().partition("=")
        if key not in self.KEYS and not key.startswith("stream_"):
            return False
        if key == "out_time_us" and value.strip().isdigit():
            self.video_s = int(value) / 1e6
        return True

    def taken(self, now=None):
        """The wall time when the newest frame was taken. The clock starts when the video time first moves,
        not while the decoder still waits for its first keyframe."""
        now = time.time() if now is None else now
        if not self.video_s:
            return now
        if self.first is None:
            self.first = (now, self.video_s)
        return min(now, self.first[0] + self.video_s - self.first[1])

    def behind(self, now=None):
        now = time.time() if now is None else now
        return max(0.0, now - self.taken(now))


def split_jpegs(buffer):
    """Returns each whole JPEG in buffer, and the bytes after the last one."""
    found = []
    while (end := buffer.find(b"\xff\xd9")) >= 0:
        found.append(buffer[:end + 2])
        buffer = buffer[end + 2:]
    return found, buffer


def whole_shift(previous, grey, size=SMALL_SIZE, reach=TURN_REACH):
    """Returns (dx, dy) if one shift of the whole picture explains the change, or None."""
    if previous is None:
        return None
    width, height = size
    before, after = Image.frombytes("L", size, previous), Image.frombytes("L", size, grey)

    def error(dx, dy):
        first = before.crop((max(0, dx), max(0, dy), width + min(0, dx), height + min(0, dy)))
        second = after.crop((max(0, -dx), max(0, -dy), width + min(0, -dx), height + min(0, -dy)))
        return ImageStat.Stat(ImageChops.difference(first, second)).mean[0]

    plain = error(0, 0)
    if plain < CHANGE_LEVEL:
        return None
    best, dx, dy = min((error(dx, dy), dx, dy) for dx in range(-reach, reach + 1)
                       for dy in range(-reach, reach + 1) if dx or dy)
    return (dx, dy) if best <= plain * TURN_GAIN else None


def change_level(grey, previous):
    """The largest mean difference in one block, so a small change in one place still counts."""
    if previous is None:
        return 0.0
    width, height = SMALL_SIZE
    highest = 0.0
    for top in range(0, height - CELL + 1, CELL):
        for left in range(0, width - CELL + 1, CELL):
            total = 0
            for y in range(top, top + CELL):
                row = y * width
                for i in range(row + left, row + left + CELL):
                    total += abs(grey[i] - previous[i])
            highest = max(highest, total / (CELL * CELL))
    return highest


def broken(grey):
    """True for a frame that is almost all the decoder's flat grey, or whose lower half is."""
    def flat(part):
        return sum(1 for value in part if 118 <= value <= 138) >= BROKEN_SHARE * len(part)
    return flat(grey) or flat(grey[len(grey) // 2:])


def load(jpeg, size):
    """Decodes a JPEG quickly at about this size."""
    picture = Image.open(BytesIO(jpeg))
    picture.draft("RGB", size)
    return picture.convert("RGB")


def encode(picture, quality=80):
    out = BytesIO()
    picture.save(out, "JPEG", quality=quality)
    return out.getvalue()


class Gate:
    """Decides which frames to send. The page uses the same rule."""

    def __init__(self):
        self.capture_fps = 3
        self.hold = False
        self.last_change = float("-inf")
        self.last_sent = float("-inf")
        self.peak = 0.0

    def changing(self, now):
        return self.hold or now - self.last_change < SETTLE_S

    def decide(self, now, motion):
        """Returns the peak movement to send with this frame, or None to skip the frame."""
        self.peak = max(self.peak, motion)
        if motion > CHANGE_LEVEL:
            self.last_change = now
        due = 1 / self.capture_fps if self.changing(now) else IDLE_CHECK_S
        if now - self.last_sent < due:
            return None
        self.last_sent = now
        peak, self.peak = self.peak, 0.0
        return peak


class Camera(threading.Thread):
    """Reads one camera. Others only read its newest frame, so nothing here waits for them."""

    def __init__(self, address, name, finished, abilities=()):
        super().__init__(daemon=True)
        self.address = address
        self.name = name
        self.finished = finished
        self.serial = device(address)
        self.moves = "moving" in abilities
        self.light = "light" in abilities
        self.jpeg = None
        self.taken = 0.0
        self.seq = 0
        self.previous = None
        self.broken_run = 0
        self.motion = 0.0
        self.last_change = float("-inf")
        self.shift_base = None
        self.shift_at = float("-inf")
        self.turned_at = float("-inf")
        self.reported_at = float("-inf")
        self.streams = None
        self.stream_at = 0
        # Set when the person removes this camera in Whenever. The reader stops, and the wall goes on without it.
        self.halt = threading.Event()
        self.path = media_path(name)
        self.media = f"{MEDIA}/{self.path}" if MEDIA else ""
        self.size_watch = SizeWatch()
        self.woken = threading.Event()
        self.woke_at = float("-inf")
        self.looking = False
        self.behind_restarts = 0
        self.behind_said = float("-inf")
        # Set by the reader when a rule waits for frames or someone watches without the media server.
        self.demand = False
        self.debug_levels = []
        self.debug_wakes = []

    @property
    def relayed(self):
        return bool(MEDIA) and not self.address.startswith(sources.SNAPSHOT)
    def stream(self):
        """The address that ffmpeg reads now. A make of camera can have several known paths."""
        if self.streams is None:
            if self.address.lower().startswith("onvif://"):
                self.streams = [sources.onvif_stream(self.address)]
            else:
                self.streams = sources.candidates(self.address)
        return self.streams[self.stream_at % len(self.streams)]

    def read_snapshots(self):
        """A camera or bridge with no stream gives one JPEG at a time."""
        try:
            clean, opener = sources.snapshot_opener(self.address)
            while not self.finished.is_set() and not self.halt.is_set():
                started = time.monotonic()
                self.frame(sources.fetch_snapshot(clean, opener))
                self.finished.wait(max(0.0, 1 / READ_FPS - (time.monotonic() - started)))
        except sources.SourceError as e:
            return str(e)
        return ""

    def take_motion(self):
        motion, self.motion = self.motion, 0.0
        return motion

    def turning(self, now):
        return now - self.turned_at < TURN_HOLD_S

    def frame(self, jpeg, taken=None):
        grey = load(jpeg, (128, 72)).convert("L").resize(SMALL_SIZE).tobytes()
        if broken(grey):
            self.broken_run += 1
            if self.broken_run <= BROKEN_LIMIT:
                return
        else:
            self.broken_run = 0
        now = time.monotonic()
        if now - self.shift_at >= TURN_CHECK_S:
            if whole_shift(self.shift_base, grey):
                self.turned_at = now
            self.shift_base, self.shift_at = grey, now
        # While the lens turns, every pixel changes, but nothing happened in the scene.
        motion = 0.0 if self.turning(now) else change_level(grey, self.previous)
        self.previous = grey
        self.motion = max(self.motion, motion)
        if DEBUG:
            self.debug_levels.append(motion)
        if motion > CHANGE_LEVEL:
            self.last_change = time.monotonic()
        self.jpeg = jpeg
        # When the camera took this frame, by the decoder's video clock. A reader with no clock took it now.
        self.taken = taken if taken is not None else time.time()
        self.seq += 1

    def stopping(self):
        return self.finished.is_set() or self.halt.is_set()

    def pipe_frames(self, command, on_frame, feeder_args=None, until=lambda: False, clock=None):
        """Runs ffmpeg and hands each JPEG to on_frame, until it ends or until() says stop. With a clock,
        ffmpeg's progress lines on stderr tell when each frame was taken, and on_frame gets that time."""
        feeder = None
        if feeder_args:
            feeder = subprocess.Popen(feeder_args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        process = subprocess.Popen(command, stdin=feeder.stdout if feeder else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        errors = deque(maxlen=40)

        def read_errors():
            for raw in process.stderr:
                line = raw.decode("utf-8", "ignore")
                if clock is None or not clock.progress(line):
                    errors.append(line)

        threading.Thread(target=read_errors, daemon=True).start()
        buffer = b""
        try:
            while not self.stopping() and not until():
                chunk = process.stdout.read1(1 << 18)
                if not chunk:
                    break
                found, buffer = split_jpegs(buffer + chunk)
                for jpeg in found:
                    if clock is None:
                        on_frame(jpeg)
                    else:
                        on_frame(jpeg, clock.taken())
        finally:
            for running in filter(None, (process, feeder)):
                running.kill()
                running.wait()
        return hide("".join(errors))[-300:].strip()

    def read(self):
        """Reads the stream until it ends. Returns what ffmpeg said, with the address hidden."""
        if self.address.startswith(sources.SNAPSHOT):
            return self.read_snapshots()
        try:
            stream = self.stream()
        except sources.SourceError as e:
            self.streams = None
            return hide(e)
        first = self.seq
        message = self.pipe_frames(ffmpeg_command(stream), self.frame, feeder_command(self.address))
        if self.seq == first and self.streams and len(self.streams) > 1:
            # This path gave no picture, so the next known path of this make gets a turn.
            self.stream_at += 1
            message += f" Trying stream path {self.stream_at % len(self.streams) + 1} of {len(self.streams)}."
        elif self.seq == first and self.address.lower().startswith("onvif://"):
            self.streams = None
        return message

    # ---------- With a media server: relay as it is, watch cheaply, decode in full only when needed ----------

    def wake(self, why=""):
        self.woke_at = time.monotonic()
        self.woken.set()
        if DEBUG:
            self.debug_wakes.append(why)

    def keep(self, part):
        """Runs one part again whenever it ends, until the camera is removed or the reader stops."""
        while not self.stopping():
            message = part()
            # A 404 means the camera is still connecting to the media server. The next try finds it.
            if message and not self.stopping() and "404 Not Found" not in message:
                print(f"{self.name}: {message}", flush=True)
            self.finished.wait(RECONNECT_S)

    def publish(self):
        """Copies the camera's video to the media server as it is: the live view, the buffer and clips use it."""
        try:
            stream = self.stream()
        except sources.SourceError as e:
            self.streams = None
            return hide(e)
        feeder_args = feeder_command(self.address)
        feeder = subprocess.Popen(feeder_args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) if feeder_args else None
        process = subprocess.Popen(publish_command(stream, self.media), stdin=feeder.stdout if feeder else None,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        errors = []
        threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True).start()
        try:
            while not self.stopping() and process.poll() is None:
                self.finished.wait(1.0)
        finally:
            for running in filter(None, (process, feeder)):
                running.kill()
                running.wait()
        return hide(b"".join(errors).decode("utf-8", "ignore"))[-300:].strip()

    def watch_sizes(self):
        """Reads only the sizes of the compressed frames. A burst of larger frames wakes full decoding."""
        process = subprocess.Popen(sizes_command(self.media), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in process.stdout:
                if self.stopping():
                    break
                found = frame_size(line)
                if found and self.size_watch.add(*found):
                    self.wake("size")
        finally:
            process.kill()
            process.wait()
        return ""

    def keyframe(self, jpeg, taken=None):
        """A keyframe keeps the wall fresh while nothing moves. A change between two of them also wakes decoding."""
        if self.looking:
            return
        before = self.last_change
        self.frame(jpeg, taken)
        if self.last_change != before:
            self.wake("keyframe")

    def watch_keyframes(self):
        # Decoding one keyframe every few seconds is cheap and does not fall behind. Its video time stands
        # still between keyframes, so a clock would wrongly call it late.
        return self.pipe_frames(keyframes_command(self.media), self.keyframe)

    def settled(self):
        return not self.demand and time.monotonic() - max(self.woke_at, self.last_change) > LOOK_SETTLE_S

    def look(self):
        """Decodes every frame at full size while something changes, then stops."""
        while not self.stopping():
            if self.woken.wait(1.0) or self.demand:
                break
        else:
            return ""
        self.woken.clear()

        def looked(jpeg, taken=None):
            # Keyframes keep the picture fresh until full decoding gives its first frame: at the start, and
            # after a restart, ffmpeg waits for a keyframe.
            self.looking = True
            self.frame(jpeg, taken)

        try:
            while not self.stopping():
                clock = VideoClock()
                self.looking = False
                message = self.pipe_frames(look_command(self.media), looked,
                                           until=lambda: self.settled() or clock.behind() > BEHIND_LIMIT_S, clock=clock)
                if clock.behind() <= BEHIND_LIMIT_S:
                    # Decoding that starts between keyframes reports missing references. That is normal when it stops.
                    return "" if self.settled() else message
                # Too far behind the live video: start again at once, at the live point.
                self.behind_restarts += 1
                now = time.monotonic()
                if now - self.behind_said >= 60:
                    self.behind_said = now
                    print(f"{self.name}: full decoding fell {clock.behind():.0f} s behind, so it starts again at the "
                          f"live point ({self.behind_restarts} times since the reader started).", flush=True)
            return ""
        finally:
            self.looking = False

    def relay(self):
        for part in (self.publish, self.watch_sizes, self.look):
            threading.Thread(target=self.keep, args=(part,), daemon=True).start()
        self.keep(self.watch_keyframes)

    def run(self):
        if self.relayed:
            return self.relay()
        while not self.finished.is_set() and not self.halt.is_set():
            message = self.read()
            if message:
                print(f"{self.name}: the stream stopped: {message}", flush=True)
            if not is_live(self.address) or self.halt.is_set():
                return
            self.finished.wait(RECONNECT_S)


class Wall:
    """Puts cameras side by side. One camera fills the wall by itself."""

    def __init__(self, cameras):
        self.cameras = cameras
        self.columns, self.rows = grid(len(cameras))
        many = len(cameras) > 1
        self.frame_width = min(WALL_FRAME_WIDTH, WALL_TILE_WIDTH * self.columns) if many else FRAME_WIDTH
        self.sharp_width = WALL_SHARP_WIDTH if many else SHARP_WIDTH
        self.tile = (self.sharp_width // self.columns, round(self.sharp_width / self.columns * 9 / 16))
        self.tiles = {}
        self.font = self._font(max(14, self.tile[1] // 18))

    @staticmethod
    def _font(size):
        try:
            return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except OSError:
            return ImageFont.load_default()

    def _tile(self, camera):
        cached = self.tiles.get(camera)
        if cached and cached[0] == camera.seq:
            return cached[1]
        picture = load(camera.jpeg, self.tile).resize(self.tile, Image.BILINEAR, reducing_gap=2.0)
        if len(self.cameras) > 1:
            draw = ImageDraw.Draw(picture)
            box = draw.textbbox((6, 4), camera.name, font=self.font)
            draw.rectangle((0, 0, box[2] + 8, box[3] + 6), fill=(0, 0, 0))
            draw.text((6, 4), camera.name, fill=(255, 220, 60), font=self.font)
        self.tiles[camera] = (camera.seq, picture)
        return picture

    def compose(self):
        """Returns the wall, or None until a camera has a picture."""
        ready = [camera for camera in self.cameras if camera.jpeg]
        if not ready:
            return None
        width, height = self.tile
        wall = Image.new("RGB", (width * self.columns, height * self.rows), (16, 16, 16))
        for index, camera in enumerate(self.cameras):
            if camera.jpeg:
                wall.paste(self._tile(camera), ((index % self.columns) * width, (index // self.columns) * height))
        return wall

    def small(self, wall):
        return wall.resize((self.frame_width, round(self.frame_width * wall.height / wall.width)))


def controls(cameras):
    """What the server can do with each camera. A moving lens gets its points from the fixed lens beside it."""
    found = []
    for camera in cameras:
        if camera.serial and (camera.moves or camera.light):
            guide = next((other.name for other in cameras if other.serial == camera.serial and not other.moves), "")
            found.append({"name": camera.name, "serial": camera.serial, "moves": camera.moves,
                          "light": camera.light, "guide": guide})
    return found


class Sender:
    def __init__(self, cameras, finished, server=SERVER, code=""):
        self.cameras = cameras
        self.finished = finished
        self.server = server.rstrip("/")
        self.code = code
        self.cookie = ""
        self.remote = not self.server.startswith(("http://127.0.0.1", "http://localhost"))
        self.removed = set()
        # With a media server, the page plays each camera itself. A live wall goes out only for a page that cannot.
        self.live_wanted = not MEDIA
        self.alive_at = float("-inf")
        self.live_fps = REMOTE_LIVE_FPS if self.remote else LIVE_FPS
        self.wall = Wall(cameras)
        self.gate = Gate()
        self.page = "camera-" + uuid.uuid4().hex[:8]
        self.pool = ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT)
        self.in_flight = 0
        self.sent = 0
        self.last_t = 0.0

    def login(self):
        """A hosted Whenever asks for its access code once, then knows this reader by a cookie."""
        request = urllib.request.Request(self.server + "/api/login", data=json.dumps({"code": self.code}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            self.cookie = (response.headers.get("Set-Cookie") or "").split(";")[0]

    def _open(self, path, body=None, headers=None, timeout=15):
        for attempt in (1, 2):
            request = urllib.request.Request(self.server + path, data=body,
                                             headers={**(headers or {}), **({"Cookie": self.cookie} if self.cookie else {})})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                if e.code != 401 or attempt == 2 or not self.code:
                    raise
                self.login()

    def _post(self, path, body, headers):
        return self._open(path, body, headers)

    def active(self):
        return [camera for camera in self.cameras if camera.name not in self.removed]

    def claim(self):
        active = self.active()
        names = [camera.name for camera in active]
        wall = [self.wall.columns, self.wall.rows] if self.wall else [1, 1]
        claim = {"page": self.page, "cameras": names, "controls": controls(active), "wall": wall,
                 "streams": [camera.path if camera.relayed else "" for camera in active],
                 "makes": [camera_make(camera.address) for camera in active]}
        self._post("/api/claim", json.dumps(claim).encode(), {"Content-Type": "application/json"})

    def apply_removed(self, removed):
        """The person removed or added back cameras in Whenever. Removed readers stop; added ones start."""
        removed = set(removed) & {camera.name for camera in self.cameras}
        if removed == self.removed:
            return False
        for index, camera in enumerate(self.cameras):
            if camera.name in removed and camera.name not in self.removed:
                camera.halt.set()
            elif camera.name in self.removed and camera.name not in removed:
                fresh = Camera(camera.address, camera.name, self.finished, ({"moving"} if camera.moves else set()) |
                               ({"light"} if camera.light else set()))
                self.cameras[index] = fresh
                fresh.start()
        self.removed = removed
        active = self.active()
        self.wall = Wall(active) if active else None
        self.claim()
        print("Cameras now:", ", ".join(c.name for c in active) or "none", flush=True)
        return True

    def follow_server(self):
        """A rule that waits for a later frame asks for a faster rate, or for frames in a still picture."""
        while not self.finished.wait(POLL_S):
            try:
                state = self._open("/api/state?after=999999&results=999999", timeout=5)
                self.gate.capture_fps = state["capture_fps"]
                self.gate.hold = state["hold_capture"]
                self.apply_removed(state.get("removed_cameras") or [])
                if MEDIA:
                    self.live_wanted = bool(state.get("live_wanted"))
                    # A rule that waits for frames, or a page without the media server, needs full decoding.
                    for camera in self.active():
                        camera.demand = self.gate.hold or self.live_wanted
            except (OSError, ValueError, KeyError):
                continue

    def _send(self, wall, full, t, motion, turning=()):
        try:
            headers = {"Content-Type": "image/jpeg", "X-Page": self.page}
            # The server leaves a turning lens out of its checks, because its picture misleads then.
            data = self._post("/api/frame", encode(self.wall.small(wall), 70), {
                **headers, "X-Frame-Time": str(t), "X-Motion": f"{motion:.1f}",
                "X-Turning": ",".join(str(index) for index in turning)})
            if data.get("inactive"):
                # A browser page claims the camera when the server restarts. A security camera
                # comes first, so the reader takes it back and does not stop.
                print("A page took the camera, so this reader took it back.", flush=True)
                self.claim()
            elif data.get("name"):
                if wall.width > self.wall.frame_width:
                    self._post(f"/api/frame/{data['name']}/sharp", encode(wall, 85), headers)
                for index, jpeg in full:
                    self._post(f"/api/frame/{data['name']}/full/{index}", jpeg, headers)
            self.sent += 1
        except (OSError, ValueError) as e:
            print("The server did not take a frame:", hide(e)[:160], flush=True)
        finally:
            self.in_flight -= 1

    def report_turn(self, name):
        """Tells the server that this camera's picture panned, so Whenever can tag it as moving."""
        try:
            self._post("/api/cameras/turned", json.dumps({"camera": name}).encode(), {"Content-Type": "application/json"})
        except (OSError, ValueError):
            pass

    def moving(self, now):
        """The cameras whose full frames are worth keeping for this moment. The index is the wall's."""
        active = self.active()
        busy = {camera.serial or camera for camera in active
                if self.gate.hold or now - camera.last_change < SETTLE_S}
        return [(index, camera.jpeg) for index, camera in enumerate(active)
                if camera.jpeg and (camera.serial or camera) in busy]

    def tick(self):
        if self.wall is None:
            return
        now = time.monotonic()
        active = self.active()
        fixed = [camera for camera in active if not camera.moves] or active
        peak = self.gate.decide(now, max(camera.take_motion() for camera in fixed))
        sending = peak is not None and self.in_flight < MAX_IN_FLIGHT
        if not sending and not self.live_wanted:
            # Nothing to build: the page plays each camera from the media server.
            if now - self.alive_at > ALIVE_S:
                self.alive_at = now
                try:
                    self._post("/api/alive", b"", {"X-Page": self.page})
                except (OSError, ValueError):
                    pass
            self.report_turns(now, active)
            return
        wall = self.wall.compose()
        if wall is None:
            return
        if self.live_wanted:
            width = REMOTE_LIVE_WIDTH if self.remote else LIVE_WIDTH
            view = wall.resize((width, round(width * wall.height / wall.width)), Image.BILINEAR) \
                if width and wall.width > width else wall
            try:
                self._post("/api/live", encode(view, 75), {"Content-Type": "image/jpeg", "X-Page": self.page})
            except (OSError, ValueError):
                self.finished.wait(1.0)
                return
        elif now - self.alive_at > ALIVE_S:
            self.alive_at = now
            try:
                self._post("/api/alive", b"", {"X-Page": self.page})
            except (OSError, ValueError):
                pass
        self.report_turns(now, active)
        if sending:
            self.in_flight += 1
            turning = [index for index, camera in enumerate(active) if camera.turning(now)]
            full = self.moving(now)
            self.pool.submit(self._send, wall, full, self.frame_time(active, full), peak, turning)

    def frame_time(self, active, full):
        """When this moment was taken: the oldest newest frame of the busy cameras, which the checks look
        at. Never later than now, never more than a decoder's limit behind, and always after the last one."""
        now = time.time()
        taken = [active[index].taken for index, _ in full if active[index].taken]
        t = min(taken) if taken else now
        t = min(now, max(t, now - BEHIND_LIMIT_S - 1.0, self.last_t + 0.001))
        self.last_t = t
        return t

    def camera_alarms(self):
        """EZVIZ's own motion alarms, as an extra reason to wake full decoding. They decide nothing."""
        serials = sorted({camera.serial for camera in self.cameras if camera.serial})
        if not serials or not MEDIA:
            return
        while not self.finished.is_set():
            process = subprocess.Popen([str(ROOT / ".ezviz" / "bin" / "python"), str(ROOT / "ezviz_events.py"), ",".join(serials)],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for line in process.stdout:
                try:
                    alarm = json.loads(line)
                except ValueError:
                    continue
                for camera in self.active():
                    if alarm_matches(camera.address, alarm):
                        camera.wake("camera alarm")
            process.wait()
            self.finished.wait(30)

    def report_turns(self, now, active):
        for camera in active:
            if camera.turning(now) and now - camera.reported_at >= TURN_REPORT_S:
                camera.reported_at = now
                threading.Thread(target=self.report_turn, args=(camera.name,), daemon=True).start()

    def debug(self):
        while not self.finished.wait(15):
            for camera in self.active():
                levels = sorted(camera.debug_levels) or [0]
                sizes = list(camera.size_watch.sizes) or [0]
                print(f"DEBUG {camera.name}: looking={camera.looking} demand={camera.demand} motion median "
                      f"{levels[len(levels) // 2]:.1f} p90 {levels[int(len(levels) * 0.9)]:.1f} max {levels[-1]:.1f} "
                      f"n={len(camera.debug_levels)} wakes={dict((w, camera.debug_wakes.count(w)) for w in set(camera.debug_wakes))} "
                      f"size median {statistics.median(sizes):.0f}", flush=True)
                camera.debug_levels, camera.debug_wakes = [], []

    def run(self):
        if DEBUG:
            threading.Thread(target=self.debug, daemon=True).start()
        threading.Thread(target=self.camera_alarms, daemon=True).start()
        # A steady beat: the time to build a picture comes out of the wait, not on top of it.
        due = time.monotonic()
        while not self.finished.is_set():
            due += 1 / self.live_fps
            self.tick()
            pause = due - time.monotonic()
            if pause < 0:
                due = time.monotonic()
            if self.finished.wait(max(0.0, pause)):
                break


def find_cameras():
    """Lists the ONVIF cameras that answer on this network, with a line to copy into camera.env."""
    print("Looking for ONVIF cameras on this network for 3 seconds...", flush=True)
    found = sources.discover()
    if not found:
        print("No camera answered. Switch on ONVIF in the camera's app or web page, and make sure this computer\n"
              "is on the same network. A camera without ONVIF can still use its rtsp:// or brand address.")
        return
    for camera in found:
        what = " ".join(filter(None, [camera["name"], camera["hardware"]])) or "camera"
        port = "" if camera["port"] == 80 else f":{camera['port']}"
        print(f"\n{camera['host']}  {what}\n  onvif://USER:PASSWORD@{camera['host']}{port} {camera['name'] or 'Camera'}")
    print("\nCopy a line into camera.env, put in the camera's user and password, then run: python3 camera.py --test")


def grab_one(address, timeout=TEST_TIMEOUT_S):
    """Reads one frame. Returns (width, height, the address that worked), or raises SourceError."""
    if address.startswith(sources.SNAPSHOT):
        clean, opener = sources.snapshot_opener(address)
        picture = Image.open(BytesIO(sources.fetch_snapshot(clean, opener)))
        return picture.width, picture.height, address
    if address.startswith((EZVIZ, EZVIZ_RTP)):
        raise sources.SourceError("EZVIZ cloud cameras are tested by running python3 camera.py.")
    streams = [sources.onvif_stream(address)] if address.lower().startswith("onvif://") else sources.candidates(address)
    problem = "no picture"
    for stream in streams:
        command = ffmpeg_command(stream)
        command = [*command[:command.index("-vf")], "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
        try:
            done = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            problem = "the camera sent nothing for 20 seconds"
            continue
        if done.stdout.startswith(b"\xff\xd8"):
            picture = Image.open(BytesIO(done.stdout))
            return picture.width, picture.height, stream
        problem = explain(done.stderr.decode("utf-8", "ignore"))
    raise sources.SourceError(problem)


def explain(errors):
    """ffmpeg's words, turned into what to check."""
    text = errors.lower()
    if "401" in text or "unauthorized" in text:
        return "the camera refused the user or the password."
    if "404" in text or "not found" in text:
        return "the camera has no stream at that path. Check the make, the channel, or switch on RTSP."
    if "connection refused" in text:
        return "nothing answers on that port. Switch on RTSP in the camera's app or web page."
    if "timed out" in text or "no route" in text or "host is down" in text:
        return "the camera did not answer. Check its IP address, and that this computer is on the same network."
    lines = [line for line in errors.strip().splitlines() if line.strip()]
    return hide(lines[-1] if lines else "no picture")[:200]


def test_cameras(names):
    cameras = read_cameras()
    if names:
        cameras = [c for c in cameras if c[1] in names] or [(name, "", set()) for name in names]
    if not cameras:
        sys.exit(f"Put the cameras in {CONFIG} first.")
    for address, name, _ in cameras:
        label = name or hide(address)
        if address.startswith((EZVIZ, EZVIZ_RTP)):
            print(f"SKIP  {label}: an EZVIZ cloud camera shows its picture when python3 camera.py runs.", flush=True)
            continue
        try:
            width, height, used = grab_one(address)
            print(f"OK    {label}: {width}x{height} from {hide(used)}", flush=True)
        except sources.SourceError as e:
            print(f"FAIL  {label}: {e}", flush=True)


MAKES = [("hikvision", "Hikvision, HiLook, Prama", "admin"), ("cpplus", "CP Plus, Dahua, Amcrest, Imou, Lorex", "admin"),
         ("tapo", "TP-Link Tapo (its Camera Account)", ""), ("reolink", "Reolink", "admin"),
         ("ezviz-local", "EZVIZ with local RTSP (password: the verification code)", "admin"), ("axis", "Axis", "root"),
         ("onvif", "Any ONVIF camera", "admin"), ("rtsp", "I have an rtsp:// address", "")]


def ask(question, default=""):
    answer = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
    return answer or default


def yes(question, default=True):
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def camera_line(make, host, user, password, channel="1"):
    login = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@" if user else ""
    if make == "onvif":
        return f"onvif://{login}{host}"
    return f"{make}://{login}{host}" + (f"/{channel}" if channel not in ("", "1") else "")


def add_camera(lines, make, host, default_name):
    """Asks for one camera's login, tests it, and keeps it only if a picture came back."""
    user = ask("  User", dict((m, u) for m, _, u in MAKES).get(make, "admin"))
    password = getpass.getpass("  Password (hidden): ")
    channel = ask("  Channel (for a recorder with many cameras)", "1") if make not in ("onvif", "tapo", "ezviz-local") else "1"
    name = ask("  Name in Whenever", default_name)
    address = camera_line(make, host, user, password, channel)
    print("  Testing...", flush=True)
    try:
        width, height, _ = grab_one(address)
        print(f"  It works: {width}x{height}.")
        lines.append(f"{address} {name}")
    except sources.SourceError as e:
        print(f"  It did not work: {e}")
        if yes("  Keep it anyway?", False):
            lines.append(f"{address} {name}")


def setup(server):
    """Asks a few questions, writes camera.env, and tests each camera. Nobody edits a file by hand."""
    print("Whenever camera setup. Your cameras' passwords stay on this computer.\n")
    settings = read_settings(CONFIG)
    lines = []
    if server and server != settings["WHENEVER_SERVER"]:
        lines.append(f"WHENEVER_SERVER={server}")
    elif settings["WHENEVER_SERVER"]:
        lines.append(f"WHENEVER_SERVER={settings['WHENEVER_SERVER']}")
    if lines and not settings["WHENEVER_CODE"]:
        lines.append(f"WHENEVER_CODE={getpass.getpass('The access code that you use to log in to Whenever (hidden): ').strip()}")
    elif settings["WHENEVER_CODE"]:
        lines.append(f"WHENEVER_CODE={settings['WHENEVER_CODE']}")
    cameras = [f"{address} {name}".strip() for address, name, _ in read_cameras(CONFIG)]
    if cameras:
        print("Cameras already set up: " + ", ".join(line.split(" ", 1)[-1] for line in cameras))
    print("Looking for cameras on this network...", flush=True)
    for found in sources.discover():
        what = " ".join(filter(None, [found["name"], found["hardware"]])) or "a camera"
        if yes(f"\nFound {what} at {found['host']}. Add it?"):
            port = "" if found["port"] == 80 else f":{found['port']}"
            add_camera(cameras, "onvif", f"{found['host']}{port}", found["name"] or "Camera")
    while yes("\nAdd a camera by its make?", not cameras):
        for number, (_, label, _) in enumerate(MAKES, 1):
            print(f"  {number}. {label}")
        choice = ask("  Number", "1")
        make = MAKES[int(choice) - 1][0] if choice.isdigit() and 1 <= int(choice) <= len(MAKES) else "onvif"
        if make == "rtsp":
            address = ask("  The rtsp:// address")
            cameras.append(f"{address} {ask('  Name in Whenever', f'Camera {len(cameras) + 1}')}")
            continue
        add_camera(cameras, make, ask("  The camera's IP address, such as 192.168.1.64"), f"Camera {len(cameras) + 1}")
    if not cameras:
        sys.exit("No camera was added. Run python3 camera.py --setup again when the camera is on the network.")
    CONFIG.write_text("# Written by python3 camera.py --setup. Git ignores this file.\n" + "\n".join(lines + cameras) + "\n")
    os.chmod(CONFIG, 0o600)
    print(f"\nSaved {len(cameras)} camera(s) in camera.env.")
    return yes("Start sending them to Whenever now?")


def main():
    if sys.argv[1:2] == ["--find"]:
        return find_cameras()
    if sys.argv[1:2] == ["--setup"]:
        if not setup(sys.argv[2] if len(sys.argv) > 2 else ""):
            return
        sys.argv = sys.argv[:1]
    if sys.argv[1:2] == ["--test"]:
        return test_cameras(sys.argv[2:])
    cameras = [(address, "", set()) for address in sys.argv[1:]] or read_cameras()
    if not cameras:
        sys.exit(f"Put the cameras in {CONFIG}, or pass a video file.")
    settings = read_settings()
    finished = threading.Event()
    readers = [Camera(address, name or f"Camera {index + 1}", finished, abilities)
               for index, (address, name, abilities) in enumerate(cameras)]
    sender = Sender(readers, finished, settings["WHENEVER_SERVER"] or SERVER, settings["WHENEVER_CODE"])
    if sender.remote and not sender.code:
        sys.exit("A hosted Whenever needs WHENEVER_CODE, its access code, in camera.env.")
    if sender.code:
        sender.login()
        print("Sending to", sender.server, flush=True)
    try:
        # Cameras that the person removed in Whenever stay off, also after a restart.
        sender.removed = set(sender._open("/api/state?after=999999&results=999999", timeout=5).get("removed_cameras") or []) \
            & {reader.name for reader in readers}
    except (OSError, ValueError):
        pass
    active = sender.active()
    sender.wall = Wall(active) if active else None
    sender.claim()
    threading.Thread(target=sender.follow_server, daemon=True).start()
    for reader in sender.active():
        print("Reading", reader.name, hide(reader.address), flush=True)
        reader.start()
    try:
        if all(not is_live(reader.address) for reader in readers):
            threading.Thread(target=lambda: ([r.join() for r in readers], finished.set()), daemon=True).start()
        sender.run()
    except KeyboardInterrupt:
        finished.set()
    sender.pool.shutdown(wait=True)
    print(f"Sent {sender.sent} frames.", flush=True)


if __name__ == "__main__":
    main()
