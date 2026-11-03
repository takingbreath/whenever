"""Sends security cameras to the server, in place of the browser page.

Each camera has its own reader, so a slow camera cannot hold up the others. The cameras go on one
wall, so one model call can watch all of them. Sensors get a small wall and rule code gets a larger
one. Each camera's full frame is also saved while it has movement, so zoom has real pixels. Frames go
at the capture rate while any picture changes, and once every 10 s while all are still. Frames saved
with no rule cost nothing, so the footage can be searched later.

Usage: python3 camera.py [address ...]
With no address, it reads camera.env, with one camera on each line: an address, a name, then what it
can do, such as "ezviz-rtp:ABC123:2 Gate tracker | moving light". An address is a video file, a stream
address, ezviz:SERIAL for a camera with no RTSP, or ezviz-rtp:SERIAL:CHANNEL for a camera that sends
RTP. camera.env stays out of the repo.

A moving lens points where it is told and follows people by itself, so its picture changes with no
event. Only the fixed cameras can wake the checks. The views of one device are kept together, so the
closer view is there when a fixed lens sees something.
"""

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

CONFIG = ROOT / "camera.env"
EZVIZ = "ezviz:"
EZVIZ_RTP = "ezviz-rtp:"
SERVER = f"http://127.0.0.1:{os.environ.get('WHENEVER_PORT', 8787)}"
FRAME_WIDTH = 640
SHARP_WIDTH = 1280
FULL_WIDTH = 2880
# On a wall, each camera keeps enough pixels for a model to see a person.
WALL_TILE_WIDTH = 480
WALL_FRAME_WIDTH = 1280
WALL_SHARP_WIDTH = 1920
SMALL_SIZE = (64, 36)
CELL = 8
CHANGE_LEVEL = 5
SETTLE_S = 2.5
IDLE_CHECK_S = 10.0
READ_FPS = 8
LIVE_FPS = 6
MAX_IN_FLIGHT = 4
POLL_S = 2.0
RECONNECT_S = 5.0
CONNECT_TIMEOUT_US = 10_000_000


def hide(text):
    """An address can hold a camera's code, so it never reaches a log."""
    return re.sub(r"://[^@/\s]*@", "://***@", str(text))


def parse_camera(line):
    """Returns (address, name, abilities). The name is optional and can hold spaces."""
    address, _, rest = line.strip().removeprefix("CAMERA_URL=").partition(" ")
    name, _, abilities = rest.partition("|")
    return address, name.strip(), set(abilities.split())


def read_cameras(path=CONFIG):
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    return [parse_camera(line) for line in lines]


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
    return "://" in address or address.startswith((EZVIZ, EZVIZ_RTP))


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
        source = [*([] if is_live(address) else ["-re"]), "-i", address]
    return ["ffmpeg", "-loglevel", "error", *source, "-an",
            "-vf", f"fps={READ_FPS},scale='min({FULL_WIDTH},iw)':-2",
            "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1"]


def split_jpegs(buffer):
    """Returns each whole JPEG in buffer, and the bytes after the last one."""
    found = []
    while (end := buffer.find(b"\xff\xd9")) >= 0:
        found.append(buffer[:end + 2])
        buffer = buffer[end + 2:]
    return found, buffer


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
        self.seq = 0
        self.previous = None
        self.motion = 0.0
        self.last_change = float("-inf")

    def take_motion(self):
        motion, self.motion = self.motion, 0.0
        return motion

    def frame(self, jpeg):
        grey = load(jpeg, (128, 72)).convert("L").resize(SMALL_SIZE).tobytes()
        motion = change_level(grey, self.previous)
        self.previous = grey
        self.motion = max(self.motion, motion)
        if motion > CHANGE_LEVEL:
            self.last_change = time.monotonic()
        self.jpeg = jpeg
        self.seq += 1

    def read(self):
        """Reads the stream until it ends. Returns what ffmpeg said, with the address hidden."""
        feeder_args = feeder_command(self.address)
        feeder = None
        if feeder_args:
            feeder = subprocess.Popen(feeder_args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        process = subprocess.Popen(ffmpeg_command(self.address), stdin=feeder.stdout if feeder else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        errors = []
        threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True).start()
        buffer = b""
        try:
            while not self.finished.is_set():
                chunk = process.stdout.read1(1 << 18)
                if not chunk:
                    break
                found, buffer = split_jpegs(buffer + chunk)
                for jpeg in found:
                    self.frame(jpeg)
        finally:
            for running in filter(None, (process, feeder)):
                running.kill()
                running.wait()
        return hide(b"".join(errors).decode("utf-8", "ignore"))[-300:].strip()

    def run(self):
        while not self.finished.is_set():
            message = self.read()
            if message:
                print(f"{self.name}: the stream stopped: {message}", flush=True)
            if not is_live(self.address):
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
        picture = load(camera.jpeg, self.tile).resize(self.tile)
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
    def __init__(self, cameras, finished):
        self.cameras = cameras
        self.finished = finished
        self.wall = Wall(cameras)
        self.gate = Gate()
        self.page = "camera-" + uuid.uuid4().hex[:8]
        self.pool = ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT)
        self.in_flight = 0
        self.sent = 0

    def _post(self, path, body, headers):
        request = urllib.request.Request(SERVER + path, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    def claim(self):
        names = [camera.name for camera in self.cameras]
        claim = {"page": self.page, "cameras": names, "controls": controls(self.cameras),
                 "wall": [self.wall.columns, self.wall.rows]}
        self._post("/api/claim", json.dumps(claim).encode(), {"Content-Type": "application/json"})

    def follow_server(self):
        """A rule that waits for a later frame asks for a faster rate, or for frames in a still picture."""
        while not self.finished.wait(POLL_S):
            try:
                with urllib.request.urlopen(f"{SERVER}/api/state?after=999999&results=999999", timeout=5) as response:
                    state = json.load(response)
                self.gate.capture_fps = state["capture_fps"]
                self.gate.hold = state["hold_capture"]
            except (OSError, ValueError, KeyError):
                continue

    def _send(self, wall, full, t, motion):
        try:
            headers = {"Content-Type": "image/jpeg", "X-Page": self.page}
            data = self._post("/api/frame", encode(self.wall.small(wall), 70), {
                **headers, "X-Frame-Time": str(t), "X-Motion": f"{motion:.1f}"})
            if data.get("inactive"):
                print("Another page took the camera, so this reader stopped.", flush=True)
                self.finished.set()
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

    def moving(self, now):
        """The cameras whose full frames are worth keeping for this moment."""
        busy = {camera.serial or camera for camera in self.cameras
                if self.gate.hold or now - camera.last_change < SETTLE_S}
        return [(index, camera.jpeg) for index, camera in enumerate(self.cameras)
                if camera.jpeg and (camera.serial or camera) in busy]

    def tick(self):
        wall = self.wall.compose()
        if wall is None:
            return
        try:
            self._post("/api/live", encode(wall, 75), {"Content-Type": "image/jpeg", "X-Page": self.page})
        except (OSError, ValueError):
            self.finished.wait(1.0)
            return
        now = time.monotonic()
        fixed = [camera for camera in self.cameras if not camera.moves] or self.cameras
        peak = self.gate.decide(now, max(camera.take_motion() for camera in fixed))
        if peak is not None and self.in_flight < MAX_IN_FLIGHT:
            self.in_flight += 1
            self.pool.submit(self._send, wall, self.moving(now), time.time(), peak)

    def run(self):
        while not self.finished.wait(1 / LIVE_FPS):
            self.tick()


def main():
    cameras = [(address, "", set()) for address in sys.argv[1:]] or read_cameras()
    if not cameras:
        sys.exit(f"Put the cameras in {CONFIG}, or pass a video file.")
    finished = threading.Event()
    readers = [Camera(address, name or f"Camera {index + 1}", finished, abilities)
               for index, (address, name, abilities) in enumerate(cameras)]
    sender = Sender(readers, finished)
    sender.claim()
    threading.Thread(target=sender.follow_server, daemon=True).start()
    for reader in readers:
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
