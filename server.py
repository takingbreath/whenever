"""Local server for the Whenever prototype.

The page sends frames here. Sensors turn frames into events, and each rule's own code reacts
to those events in its sandbox. The API key stays in this process and never reaches the
browser or a rule. Frames stay in memory and are not saved.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlencode
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import actions
import ask
import detect
import embed
import moments
import people
import sandbox
import search
import writer
from camera_control import ControlError, Controls
from channels import CHANNELS_FILE, PICTURES_DIR, ChannelError, Channels
from connectors import ACTIONS_FILE, ActionError, Connectors, check_draft, read_secrets
from journal import JOURNAL_FILE, Journal
from problems import Problems
from engine import HITS_TO_SET, SensorState, pick_frames, spread
from evidence import Evidence
from meta_api import ApiError, Client, load_key
from rule_runtime import place_of, seat, share_inside
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat  # noqa: E402  (meta_api adds vendor/ to the path)
from voice import VoiceError, VoiceSession

PORT = int(os.environ.get("WHENEVER_PORT", 8787))
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
LOGS_DIR = ROOT / "logs"
# A hosted server names its public host, such as wheneverwhatever.si. Caddy brings HTTPS traffic here.
HOSTS = tuple(h.strip() for h in os.environ.get("WHENEVER_HOST", "").split(",") if h.strip())
# The access code of a hosted server. It is a file, not an environment value, because a rule could
# read the environment of a process of the same user. With no file, as on a Mac, nobody logs in.
ACCESS_FILE = ROOT / "access.env"
SESSIONS_FILE = ROOT / "data" / "sessions.json"
SESSION_S = 30 * 86_400
MAX_LOGINS_PER_MINUTE = 10

DATA_DIR = ROOT / "data"
RULES_FILE = DATA_DIR / "rules.json"
PHONE_FILE = DATA_DIR / "phone.json"
PROBLEMS_FILE = DATA_DIR / "problems.json"
# Which messages each event's first alert became, so a later picture of that event answers it.
THREADS_FILE = DATA_DIR / "alert_threads.json"
# Today's model spend. A restart must not start the daily limit again.
SPEND_FILE = DATA_DIR / "spend.json"
MAX_THREADS = 200
# The same words about the same occurrence within this time are one alert. A rule's event job and its
# on_tick can find the same person at the same moment.
REPEAT_S = 90
CAMERAS_FILE = DATA_DIR / "cameras.json"
# Live checks that fail without a break for this long become a warning. A shorter gap is only a note.
STUCK_S = 30
MAX_RULES = 5
MAX_SCHEDULES = 4
MIN_SCHEDULE_S = 60
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Schedules use the person's own clock. A hosted server sets WHENEVER_TZ, such as Asia/Kolkata.
ZONE = ZoneInfo(os.environ["WHENEVER_TZ"]) if os.environ.get("WHENEVER_TZ") else datetime.now().astimezone().tzinfo
MAX_ACTS_PER_MINUTE = 12
MAX_ACTION_WORDS = 6_000
MAX_DRAFTS = 10
MAX_FRAME_BYTES = 1_000_000
MAX_SHARP_BYTES = 3_000_000
MAX_FULL_BYTES = 8_000_000
MAX_CAMERAS = 16
MAX_JSON_BYTES = 24_000_000
MAX_LABEL_BYTES = 1_000
MAX_CLAIM_BYTES = 8_000
MAX_SENTENCE = 2_000
MAX_AUDIO_BYTES = 64_000
MAX_POINTS = 20
# Pointing: SAM and Spark get one camera's own picture, at most this long, and a close crop for the name.
POINT_SIDE = 1280
POINT_CROP_SIDE = 640
# A camera's own full frame this new is sharper than its tile of the wall, so pointing uses it.
FULL_FRESH_S = 5
MAX_ALSO = 4
MAX_NOTE = 300

# Limits on rule code. Rules can ask models anything, so a mistake must not cost much.
MAX_CALLS_PER_MINUTE = 60
MAX_ASK_FRAMES = 12
MAX_CHECK_THREADS = 6
MAX_RULE_FRAMES = 24
MAX_DETECT_FRAMES = 24
MAX_POSES = 20            # people in one camera picture that get body points
MAX_WHO = 20              # person boxes that one ctx.who call reads
MAX_BEFORE_S = 900
MAX_AFTER_S = 30
MAX_ARM_S = 900
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
# An alert shows only the cameras where it happened, with a short clip of the frames around it.
CLIP_BEFORE_S = 4.0
CLIP_AFTER_S = 3.0
CLIP_WIDTH = 960
CLIP_HEIGHT = 540
MIN_CLIP_FRAMES = 3
MAX_CLIP_FRAMES = 48
MAX_FOCUS = 2
EVENT_FRESH_S = 180
MAX_GALLERY_FRAMES = 24
MAX_GALLERIES_PER_MINUTE = 6
MAX_GALLERY_SHOTS = 12
GALLERY_MIN_CONFIDENCE = 0.5
# In a camera that stays still, a gallery's thing must be this near the pointed thing, in parts of its size:
# to each side, above it, and below it, toward the camera. A neighbour beyond the wall was once pictured as
# the person at the gate.
NEAR_SIDE, NEAR_ABOVE, NEAR_BELOW = 0.5, 0.5, 1.5
# Pictures in a gallery must differ, so they stay this far apart in time.
GALLERY_GAP_S = 0.7
GALLERY_ZOOM_PAD = 0.8
GALLERY_ZOOM_SIZE = 480
GALLERY_SCORE_SIZE = 64
GALLERY_USEFUL_WIDTH = 60
# A moving lens has 2 times the pixels on the same thing, and the score below cannot see this.
MOVING_LENS_BONUS = 1.5
MAX_CONTROLS_PER_MINUTE = 12
PAGE_MOVE_S = 0.8
PAGE_LIGHT_S = 30

# 20 seconds at the highest capture rate.
FRAME_BUFFER = 240
MAX_CAPTURE_FPS = 12
DEFAULT_CAPTURE_FPS = 3
# Every saved frame stays on disk. This much of the list stays in memory: 1 hour at 12 frames a second.
TIMELINE_FRAMES = 43_200
MIN_CHECK_EVERY_S = 0.8
MOTION_MIN = 6
# Checks overlap, so a slow model answer cannot delay the next check.
MAX_SENSOR_CHECKS = 3
STRONG_HIT = 0.85
DETECTION_THREADS = 12
ANALYSIS_THREADS = 32

# A near miss goes to a review of the recent past. Each sure "no" doubles the wait, so an idle camera
# and a thing that only looks close cost little. The wait stops at the span, so no frame goes unread.
REVIEW_SPAN_S = 24
MOMENT_REVIEW_SPAN_S = 8
MIN_REVIEW_SPAN_S = 5
MOMENT_MIN_REVIEW_SPAN_S = 2
REVIEW_FRAMES = 10
REVIEW_PASS = 0.8
# One weak yes is not enough, but two in a row are, as with two yes answers on a check.
REVIEW_WEAK = 0.6
REVIEW_EVERY_S = 6
REVIEW_MAX_WAIT_S = REVIEW_SPAN_S
# While a state is on, a review runs this often, to find anyone new who is doing the same thing.
# It must run before the state clears, so a short clear time gets a shorter beat.
WATCH_EVERY_S = 8
MIN_WATCH_S = 4
SUBJECT_PASS = 0.8
MAX_SUBJECTS = 6

# What a sensor's model sees. A wall shows every camera small, but a Whenever watches one or two.
# Its model gets those cameras large, each with its name, and a close-up of the thing that the person
# pointed at in each still camera, cut from that camera's own full frame when one was kept.
FOCUS_PANEL = (640, 360)
FOCUS_PAD = 0.45         # a close-up shows this much of the thing's size around it, on each side
FOCUS_MIN_SHARE = 0.25   # and at least this share of the camera's width
FOCUS_DRAFT = (1440, 810)
FOCUS_CACHE = 48
# Before a state of a pointed thing starts, such as an open gate, Spark compares the thing's close-up with a
# moment before it. A change of light alone, such as a car's headlights behind a closed gate, is no event.
LIGHT_BEFORE_S = 2.0      # the moment before is at least this much older than the proof
LIGHT_LOOK_BACK_S = 120.0
# A state of a pointed thing is asked again only when the thing's place changed since the last "no", or
# after QUIET_SAFETY_S. In one night, 71% of the gate rule's checks came with the gate's place unchanged,
# often because another camera moved, and only 1 of those said yes.
QUIET_LIKENESS = 0.9
QUIET_SAFETY_S = 600.0
# A watch plan names the places that a sensor looks at. Things found by name are found once in each camera.
# "count" wakes a paid check only when the local detector counts a different number of things in a place.
WATCH_WAKES = ("change", "count", "always")
MAX_COUNT_THINGS = 4
# A thing counts in the place that holds its seat point (three quarters down its box), or in the nearest
# place less than this share of the place's size away: a person on a chair beside a table counts at the
# table, from either side of it. Each thing counts in one place only.
COUNT_NEAR = 0.5
MAX_PLACES = 12
PLACE_MIN_CONFIDENCE = 0.4
PLACE_MIN_AREA = 0.002    # a smaller box, as a share of the picture, is a piece of a thing at the edge
PLACE_RETRY_S = 600.0
PLACES_FILE = "places.json"
LIGHT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["moved", "only_light", "yes_now", "note"],
    "properties": {"moved": {"type": "boolean"}, "only_light": {"type": "boolean"}, "yes_now": {"type": "boolean"},
                   "note": {"type": "string"}},
}
# A moving camera's picture counts again this long after it stopped turning. While it turns, its
# picture is blurred or shows another place, such as the street above a gate.
TURN_SETTLE_S = 6.0

TICK_S = 1.0
CAMERA_LOST_S = 25
HEALTH_EVERY_S = 10
# A camera reader that starts this often is failing, though pictures still come between its starts.
RESTARTS_WARN = 3
RESTARTS_SPAN_S = 600
# A moving lens can follow a person and stay where it lost them. After the lens turned, once it is
# still, SAM looks for the pointed thing in its picture; a lens that did not turn cannot have left, so
# otherwise a look comes only every 10 minutes. Two misses, 30 s apart, mean that the lens looks away:
# its picture is left out, and Whenever turns it back to the thing, found in its fixed lens.
VIEW_CHECK_S = 30
VIEW_SAFETY_S = 600
VIEW_QUIET_S = 20
VIEW_MISSES = 2
VIEW_TURN_BACK_S = 300
VIEW_MIN_CONFIDENCE = 0.35
# A security camera sends the page a live picture, so the page shows it in place of the Mac's camera.
CAMERA_PAGE_PREFIX = "camera-"
LIVE_LOST_S = 5.0
LIVE_WAIT_S = 2.0
# One open stream sends each new live picture at once. It ends after a while, and the page opens a new one.
LIVE_STREAM_S = 300
# A media server (MediaMTX) on this computer relays each camera as it is. The page plays it as HLS through
# this server, which checks the login, and alert clips come from its buffer at full quality.
HLS_URL = os.environ.get("WHENEVER_HLS", "").rstrip("/")
# The media server's own API, on this computer only, says which camera it receives now.
MEDIA_API_URL = os.environ.get("WHENEVER_MEDIA_API", "http://127.0.0.1:9997").rstrip("/")
PLAYBACK_URL = os.environ.get("WHENEVER_PLAYBACK", "").rstrip("/")
MAX_PENDING = 30
FEED_LENGTH = 200

# Estimates from the published prices. Spark and Wonder use the real token counts.
PRICE_SEE = 0.0025
# The spend limit is for each day, in the person's own time.
SPEND_LIMIT_USD = float(os.environ.get("WHENEVER_SPEND_LIMIT") or 5.00)
# Extra money for one day only, as "YYYY-MM-DD:USD", several with commas. It ends at that day's midnight.
SPEND_EXTRA = os.environ.get("WHENEVER_SPEND_EXTRA") or ""

STATIC = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/pair.js": ("web/pair.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("web/sw.js", "text/javascript; charset=utf-8"),
    "/icons/icon-192.png": ("web/icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("web/icons/icon-512.png", "image/png"),
    "/icons/apple-touch-icon.png": ("web/icons/apple-touch-icon.png", "image/png"),
    "/icons/badge.png": ("web/icons/badge.png", "image/png"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/mic.js": ("web/mic.js", "text/javascript; charset=utf-8"),
    "/style.css": ("web/style.css", "text/css; charset=utf-8"),
    "/testdata/clip.webm": ("testdata/clip.webm", "video/webm"),
    "/fonts/schibsted-grotesk.woff2": ("web/fonts/schibsted-grotesk.woff2", "font/woff2"),
}
PAIR_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
# A phone that pairs has no login yet, so these need none. A pairing token guards the rest.
OPEN_PATHS = ("/style.css", "/fonts/schibsted-grotesk.woff2", "/pair.js", "/sw.js", "/manifest.webmanifest",
              "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png", "/icons/badge.png")


def manifest(start_url="/"):
    """The web app manifest. An iPhone shows alerts only for a web app on its Home Screen."""
    return json.dumps({
        "name": "Whenever", "short_name": "Whenever", "id": "/", "start_url": start_url, "scope": "/",
        "display": "standalone", "background_color": "#F3F3F1", "theme_color": "#F3F3F1",
        "description": "Whenever it happens, do whatever you want.",
        "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    })


Frame = namedtuple("Frame", "t jpeg name motion")


def fit(picture, side):
    """The picture, no longer than side on its long edge."""
    if max(picture.size) <= side:
        return picture
    scale = side / max(picture.size)
    return picture.resize((max(1, round(picture.width * scale)), max(1, round(picture.height * scale))), Image.LANCZOS)


def short(text, limit):
    """Text no longer than limit, cut at a word, with an ellipsis when it was cut."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def joined(names):
    """Names as words: "A", "A and B", "A, B and C"."""
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def close_up(picture, box):
    """Cuts the place around a box given from 0 to 1. It uses only real pixels."""
    x1, y1, x2, y2 = (box[0] * picture.width, box[1] * picture.height, box[2] * picture.width, box[3] * picture.height)
    width = max((x2 - x1) * (1 + 2 * FOCUS_PAD), picture.width * FOCUS_MIN_SHARE)
    height = max((y2 - y1) * (1 + 2 * FOCUS_PAD), width * FOCUS_PANEL[1] / FOCUS_PANEL[0])
    width = max(width, height * FOCUS_PANEL[0] / FOCUS_PANEL[1])
    width, height = min(width, picture.width), min(height, picture.height)
    left = min(max(0, (x1 + x2 - width) / 2), picture.width - width)
    top = min(max(0, (y1 + y2 - height) / 2), picture.height - height)
    return picture.crop((round(left), round(top), round(left + width), round(top + height)))


def into_panel(picture, size=FOCUS_PANEL):
    """The picture scaled to fill a panel's width or height, keeping its shape."""
    scale = min(size[0] / picture.width, size[1] / picture.height)
    return picture.resize((max(2, round(picture.width * scale)), max(2, round(picture.height * scale))), Image.LANCZOS)


def labelled_grid(panels):
    """Panels in two columns, each with its name in its top corner, so a model knows which is which."""
    columns = 1 if len(panels) == 1 else 2
    rows = (len(panels) + columns - 1) // columns
    gap = 6
    grid = Image.new("RGB", (columns * FOCUS_PANEL[0] + (columns - 1) * gap, rows * FOCUS_PANEL[1] + (rows - 1) * gap),
                     (16, 16, 16))
    draw = ImageDraw.Draw(grid)
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:
        font = ImageFont.load_default()
    for index, (label, picture) in enumerate(panels):
        left = (index % columns) * (FOCUS_PANEL[0] + gap)
        top = (index // columns) * (FOCUS_PANEL[1] + gap)
        grid.paste(picture, (left + (FOCUS_PANEL[0] - picture.width) // 2, top + (FOCUS_PANEL[1] - picture.height) // 2))
        box = draw.textbbox((left + 10, top + 8), label, font=font)
        draw.rectangle((box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4), fill=(0, 0, 0))
        draw.text((left + 10, top + 8), label, fill=(255, 214, 0), font=font)
    return grid


def box_has_size(boxes):
    return all(box.get("w") and box.get("h") for box in boxes)


def near_place(box, place):
    """True if a found box, in pixels with its frame size, is near a place given from 0 to 1."""
    x1, y1, x2, y2 = place
    width, height = x2 - x1, y2 - y1
    bx1, by1 = box["x1"] / box["w"], box["y1"] / box["h"]
    bx2, by2 = box["x2"] / box["w"], box["y2"] / box["h"]
    return (bx2 >= x1 - NEAR_SIDE * width and bx1 <= x2 + NEAR_SIDE * width
            and by2 >= y1 - NEAR_ABOVE * height and by1 <= y2 + NEAR_BELOW * height)


def jpeg_bytes(picture, quality=88):
    out = BytesIO()
    picture.convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue()


def zoom_crop(jpeg, box):
    """A close crop of a box, with some of its surroundings, large enough for a model to read."""
    picture = Image.open(BytesIO(jpeg)).convert("RGB")
    sx, sy = picture.width / box["w"], picture.height / box["h"]
    x1, y1, x2, y2 = box["x1"] * sx, box["y1"] * sy, box["x2"] * sx, box["y2"] * sy
    margin = max(24, 0.35 * max(x2 - x1, y2 - y1))
    area = (max(0, x1 - margin), max(0, y1 - margin), min(picture.width, x2 + margin), min(picture.height, y2 + margin))
    crop = picture.crop(tuple(round(v) for v in area))
    if max(crop.size) < POINT_CROP_SIDE:
        scale = POINT_CROP_SIDE / max(crop.size)
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
    return jpeg_bytes(fit(crop, POINT_CROP_SIDE))


def numbered(jpeg, boxes):
    """The picture with each box drawn and numbered from 1, so Spark can answer with a number."""
    picture = Image.open(BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    size = max(16, picture.width // 40)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    for number, box in enumerate(boxes, 1):
        sx, sy = picture.width / box["w"], picture.height / box["h"]
        rect = (box["x1"] * sx, box["y1"] * sy, box["x2"] * sx, box["y2"] * sy)
        draw.rectangle(rect, outline=(255, 214, 0), width=max(3, picture.width // 320))
        label = (rect[0], max(0, rect[1] - size - 6), rect[0] + size + 8, max(size + 6, rect[1]))
        draw.rectangle(label, fill=(255, 214, 0))
        draw.text((label[0] + 4, label[1] + 2), str(number), fill=(0, 0, 0), font=font)
    return jpeg_bytes(picture)


def grid(box):
    """A box on a 0-1000 grid, as [x_min, y_min, x_max, y_max]."""
    return [box["x1"] * 1000 // box["w"], box["y1"] * 1000 // box["h"],
            box["x2"] * 1000 // box["w"], box["y2"] * 1000 // box["h"]]
OUTLINE_FIELDS = ("x1", "y1", "x2", "y2", "w", "h", "path", "mask_w", "mask_h")


def number(value, low, high, default):
    try:
        return min(max(float(value), low), high)
    except (TypeError, ValueError):
        return default


def spend_limit(day):
    """A day's model budget: the standing limit, plus any extra that the owner gave for that day alone."""
    extra = 0.0
    for item in SPEND_EXTRA.split(","):
        date, _, usd = item.strip().partition(":")
        if date == str(day):
            extra += number(usd, 0, 1000, 0)
    return SPEND_LIMIT_USD + extra


def clean_watch(watch, pattern, target, starts_off):
    """A sensor's watch plan, as Wonder wrote it: the places that a paid check is about, and when to pay.

    places: the pointed thing, things found once by name (such as "table"), or a camera's whole view, only
    in cameras that stay still. wake "change" pays only when a place looks different from the last paid
    check, or after safety_s. "count" pays only when the number of the things in count, such as people,
    changes in a place. "always" pays for every check. With no plan, a state sensor of a rule watches
    the rule's pointed thing, and other sensors pay for every check, as before plans existed.
    """
    watch = watch if isinstance(watch, dict) else {}
    places = []
    for place in (watch.get("places") or [])[:MAX_PLACES]:
        if isinstance(place, dict):
            places.append({"camera": str(place.get("camera") or "").strip()[:40],
                           "find": str(place.get("find") or "").strip()[:40], "pointed": bool(place.get("pointed"))})
    plain = pattern == "state" and not target and not starts_off
    wake = watch.get("wake") if watch.get("wake") in WATCH_WAKES else ("change" if plain else "always")
    if wake == "change" and not places:
        places = [{"camera": "", "find": "", "pointed": True}]
    count = []
    for word in (watch.get("count") or [])[:MAX_COUNT_THINGS]:
        label = detect.label_for(word)
        if label and label not in count:
            count.append(label)
    if wake == "count":
        count = count or ["person"]
        places = places or [{"camera": "", "find": "", "pointed": False}]
    return {"places": places, "wake": wake, "change": number(watch.get("change"), 0.5, 0.99, QUIET_LIKENESS),
            "safety_s": number(watch.get("safety_s"), 60, 3600, QUIET_SAFETY_S), "count": count}


def distinct_boxes(boxes, cover=0.8):
    """Boxes with no repeats: a box that lies mostly inside a larger one is the same thing found twice,
    such as half a table and the whole table. A very small box, such as the corner of a table at the
    picture's edge, is left out."""
    kept = []
    for box in sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True):
        if (box[2] - box[0]) * (box[3] - box[1]) < PLACE_MIN_AREA:
            break
        if all(share_inside(box, other) < cover for other in kept):
            kept.append(box)
    return kept


def same_place(before, now):
    """How much a watched place stayed the same, from 1 down: its edge map's likeness, or for a count,
    1 if the number is the same and else 0."""
    if isinstance(now, int):
        return 1.0 if before == now else 0.0
    return search.likeness(before, now)


def proof_frame(result):
    """The frame that best shows the event. Spark numbers its frames from 1."""
    if result.get("proof"):
        return result["proof"]
    names = result.get("frames") or []
    answers = result.get("answers") or []
    best = answers[0].get("best_frame", 1) if answers else 1
    if 1 <= best <= len(names):
        return names[best - 1]
    return names[0] if names else None


def centre(box):
    return (box["x1"] + box["x2"]) / 2, (box["y1"] + box["y2"]) / 2


def zoom(picture, box):
    """Cuts out a box with space around it. It uses only real pixels, and invents no detail."""
    scale = picture.width / box["w"]
    x1, y1, x2, y2 = (box[key] * scale for key in ("x1", "y1", "x2", "y2"))
    pad = max(x2 - x1, y2 - y1) * GALLERY_ZOOM_PAD
    cut = picture.crop((max(0, int(x1 - pad)), max(0, int(y1 - pad)),
                        min(picture.width, int(x2 + pad)), min(picture.height, int(y2 + pad))))
    grow = GALLERY_ZOOM_SIZE / max(cut.size)
    return cut.resize((round(cut.width * grow), round(cut.height * grow)), Image.LANCZOS)


def sharpness(picture, box):
    """Scores a view on this computer, at no cost. Blur counts most, then size up to a useful width.

    A fixed size makes the blur score fair: a large blurred face would otherwise beat a small sharp one.
    """
    scale = picture.width / box["w"]
    tight = picture.crop(tuple(int(box[key] * scale) for key in ("x1", "y1", "x2", "y2"))).convert("L")
    edges = tight.resize((GALLERY_SCORE_SIZE, GALLERY_SCORE_SIZE)).filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0] ** 0.5 * min(1.0, tight.width / GALLERY_USEFUL_WIDTH)


def pick_shots(shots, count):
    """The best shots, spaced apart in time, so each picture adds something new."""
    chosen = []
    for shot in sorted(shots, key=lambda item: item["score"], reverse=True):
        if all(abs(shot["t"] - other["t"]) >= GALLERY_GAP_S for other in chosen):
            chosen.append(shot)
        if len(chosen) == count:
            break
    return chosen


def rule_clip(folder, path):
    """A clip that the server made for a rule's alert, or None."""
    root = Path(folder).resolve()
    file = (root / str(path)).resolve()
    if root / "clips" in file.parents and file.suffix == ".mp4" and file.is_file():
        return file
    return None


def rule_picture(folder, path):
    """Returns a picture inside a rule's folder, or None. Rule code chooses the path."""
    root = Path(folder).resolve()
    file = (root / str(path)).resolve()
    if root in file.parents and file.suffix.lower() in IMAGE_TYPES and file.is_file():
        return file
    return None


# One shift of the whole picture can be headlights or a light switching on. A camera that Whenever cannot
# turn counts as moving only after this many pans, each reported at least a minute apart.
TURNS_TO_MOVE = 3


class CameraKinds:
    """Whether each camera stays still or moves. No camera says so itself, so Whenever judges it:
    a lens that Whenever can turn moves, and so does one whose whole picture was seen to pan. The
    person can set it, and the person's choice wins."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.items = {}
        if self.path and self.path.exists():
            try:
                self.items = {str(k): dict(v) for k, v in json.loads(self.path.read_text()).items()}
            except (OSError, ValueError, AttributeError, TypeError):
                self.items = {}

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, indent=1, ensure_ascii=False))

    def kind(self, name, can_turn=False):
        item = self.items.get(name, {})
        if item.get("set") in ("fixed", "moving"):
            return item["set"]
        return "moving" if can_turn or self.turns(name) >= TURNS_TO_MOVE else "fixed"

    def turns(self, name):
        item = self.items.get(name, {})
        return int(item.get("turns") or (1 if item.get("turned") else 0))

    def why(self, name, can_turn=False):
        item = self.items.get(name, {})
        if item.get("set") in ("fixed", "moving"):
            return "You set this."
        if can_turn:
            return "Whenever can turn this lens."
        if item.get("turned"):
            when = datetime.fromtimestamp(item["turned"], ZONE)
            if self.turns(name) < TURNS_TO_MOVE:
                return (f"Its picture shifted once, on {when:%d %b} at {when:%H:%M}. That can be a light, "
                        "so it still counts as still.")
            return f"Its picture panned {self.turns(name)} times, last on {when:%d %b} at {when:%H:%M}."
        return "Its picture has stayed still so far."

    def set_makes(self, makes):
        """Each camera's make, such as ezviz, kept across restarts: a reader claims only when it starts."""
        with self.lock:
            changed = False
            for name, make in makes.items():
                if make and self.items.get(name, {}).get("make") != make:
                    self.items.setdefault(name, {})["make"] = make
                    changed = True
            if changed:
                self._save()

    def make(self, name):
        return self.items.get(name, {}).get("make", "")

    def turned(self, name, t):
        with self.lock:
            item = self.items.setdefault(name, {})
            item["turns"] = self.turns(name) + 1
            item["turned"] = t
            self._save()

    def remove(self, name, removed=True):
        """A removed camera stops: camera.py leaves it out of the wall, and a phone leaves camera mode."""
        with self.lock:
            item = self.items.setdefault(name, {})
            if bool(item.get("removed")) == removed:
                return
            if removed:
                item["removed"] = True
            else:
                item.pop("removed", None)
            self._save()

    def removed(self):
        return sorted(name for name, item in self.items.items() if item.get("removed"))

    def set(self, name, kind):
        """kind is fixed or moving, or auto to let Whenever judge again."""
        if kind not in ("fixed", "moving", "auto"):
            raise ValueError("A camera is fixed or moving.")
        with self.lock:
            item = self.items.setdefault(name, {})
            if kind == "auto":
                item.pop("set", None)
            else:
                item["set"] = kind
            self._save()


class Sensor:
    def __init__(self, fields):
        self.id = str(fields["id"])[:40]
        self.target = str(fields.get("target") or "").strip()[:40]
        self.outline = str(fields.get("outline") or "").strip()[:40]
        self.question = str(fields.get("question") or "").strip()[:300]
        self.requires = [str(f)[:200] for f in fields.get("requires") or []][:4] or [self.question]
        self.min_count = max(1, int(fields.get("min_count") or 1))
        # Wonder chooses the timing for each sensor. These limits only protect the system.
        self.pattern = "moment" if fields.get("pattern") == "moment" else "state"
        self.frames_per_second = number(fields.get("frames_per_second"), 1, MAX_CAPTURE_FPS, DEFAULT_CAPTURE_FPS)
        self.check_every_s = number(fields.get("check_every_s"), MIN_CHECK_EVERY_S, 60, 1.5)
        self.frames_per_check = int(number(fields.get("frames_per_check"), 1, 8, 6))
        self.clear_after_s = number(fields.get("clear_after_s"), 0.3, 600, 3)
        self.watch_s = min(WATCH_EVERY_S, max(MIN_WATCH_S, self.clear_after_s / 2))
        self.min_motion = number(fields.get("min_motion"), MOTION_MIN, 200, MOTION_MIN)
        self.reason = str(fields.get("reason") or "")[:200]
        # None means that the sensor always runs. Otherwise it runs only until this time.
        self.armed_until = 0.0 if fields.get("starts_off") else None
        self.state = SensorState(self.pattern, self.clear_after_s)
        self.lock = threading.Lock()
        self.running = 0
        self.next_turn = 0
        self.settled_turn = 0
        self.finished = {}
        self.last_check = 0.0
        self.covered_until = 0.0
        # When live checks began to fail without a break. The page warns only if this lasts.
        self.failing_since = None
        # A review never reads frames before this time, so an old occurrence cannot fire again.
        self.review_from = 0.0
        self.review_after = 0.0
        self.review_wait = REVIEW_EVERY_S
        self.reviewing = False
        self.lone_yes = False
        # Where this sensor looks and when it pays (see clean_watch).
        self.watch = clean_watch(fields.get("watch"), self.pattern, self.target, fields.get("starts_off"))
        # The watched places at the last paid check, with its time and answer, so an unchanged place costs no call.
        self.quiet = None
        self.last_look = None   # a counting plan's newest counts, paid or held
        # The event that is on now. Every step that its job takes belongs to it.
        self.occurrence = None
        # The event whose job armed this sensor. What this sensor sees is a step of that event.
        self.armed_by = None
        self.weak_yes = False
        # The proof frames of each person or thing in the state that is on.
        self.subjects = []

    def awake(self, t):
        return self.armed_until is None or t <= self.armed_until

    def arm(self, now, seconds, since=None):
        """A new wait starts clean. since keeps the frames that came while the rule was busy."""
        with self.lock:
            self.armed_until = now + seconds
            self.state = SensorState(self.pattern, self.clear_after_s)
            self.covered_until = now if since is None else since
            self.last_check = 0.0

    def reset(self, now):
        """Forgets the current event and every frame before now. A waiting job ends."""
        with self.lock:
            if self.armed_until is not None:
                self.armed_until = 0.0
            self.state = SensorState(self.pattern, self.clear_after_s)
            self.covered_until = self.review_from = now
            self.last_check = self.review_after = 0.0
            self.review_wait = REVIEW_EVERY_S
            self.weak_yes = False
            self.subjects = []

    def public(self):
        return {"id": self.id, "target": self.target, "outline": self.outline, "question": self.question,
                "starts_off": self.armed_until is not None,
                "requires": self.requires, "min_count": self.min_count, "on": self.state.on,
                "pattern": self.pattern, "frames_per_second": self.frames_per_second,
                "check_every_s": self.check_every_s, "frames_per_check": self.frames_per_check,
                "clear_after_s": self.clear_after_s, "min_motion": self.min_motion, "reason": self.reason}


def parse_at(text):
    """"09:30" becomes (9, 30). Anything else is None."""
    hour, sep, minute = str(text or "").strip().partition(":")
    if sep and hour.isdigit() and minute.isdigit() and int(hour) < 24 and int(minute) < 60:
        return int(hour), int(minute)
    return None


class Schedule:
    """A trigger at a time, with no camera. A report is a rule with a schedule."""

    def __init__(self, fields, now=None):
        self.id = str(fields["id"])[:40]
        self.at = parse_at(fields.get("at"))
        self.every_s = max(MIN_SCHEDULE_S, float(fields.get("every_minutes") or 60) * 60)
        self.days = [day for day in fields.get("days") or [] if day in DAYS]
        self.next_t = self._next(time.time() if now is None else now)

    def _next(self, now):
        if not self.at:
            return now + self.every_s
        today = datetime.fromtimestamp(now, ZONE)
        for add in range(8):
            day = today + timedelta(days=add)
            due = day.replace(hour=self.at[0], minute=self.at[1], second=0, microsecond=0)
            if due.timestamp() > now and (not self.days or DAYS[due.weekday()] in self.days):
                return due.timestamp()
        return now + 86_400

    def due(self, now):
        """True once for each time it comes. A time missed while the server was off is skipped."""
        if now < self.next_t:
            return False
        self.next_t = self._next(now)
        return True

    def public(self):
        when = f"{self.at[0]:02d}:{self.at[1]:02d}" if self.at else f"every {self.every_s / 60:g} min"
        return {"id": self.id, "when": when, "days": self.days, "next": self.next_t}


class Rule:
    def __init__(self, app, rule_id, sentence, fields, folder, pointed=None):
        self.app = app
        self.id = rule_id
        self.sentence = sentence
        self.name = str(fields.get("name") or "Rule")[:60]
        self.plan = [short(step, 140) for step in fields.get("plan") or []][:6]
        self.sensors = [Sensor(s) for s in fields.get("sensors") or []][:6]
        self.schedules = [Schedule(s) for s in fields.get("schedules") or []][:MAX_SCHEDULES]
        self.folder = folder
        self.pointed = pointed
        self.calls = deque()
        self.galleries = deque()
        self.controls = deque()
        self.acts = deque()
        self.lit = set()
        # What the rule's code shows, such as a count. It stays across restarts.
        self.values_file = Path(folder) / "data" / "values.json" if folder else None
        try:
            self.values = dict(json.loads(self.values_file.read_text())) if self.values_file else {}
        except (OSError, ValueError, TypeError):
            self.values = {}
        # A run is one event of this Whenever. The journal keeps them, so the count survives a restart.
        self.fired, self.last_fired = app.journal.runs(rule_id) if getattr(app, "journal", None) else (0, None)
        self.pending = 0
        self.worker = None
        self.paused = False
        self.send_lock = threading.Lock()

    def start(self):
        self.worker = sandbox.start_worker(self.folder)
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, message):
        if not self.worker or self.worker.poll() is not None:
            return
        if self.pending > MAX_PENDING:
            self.app.trouble(self, f"{self.name} stopped: its code did not answer", source="Rule code",
                             hint="Start it again. If it stops again, change the words so Wonder writes it again.")
            self.stop()
            return
        with self.send_lock:
            try:
                self.worker.stdin.write(json.dumps(message) + "\n")
                self.worker.stdin.flush()
                self.pending += 1
            except OSError:
                pass

    def reply(self, message):
        """Answers a call from the rule's code. This does not count as pending work."""
        if not self.worker or self.worker.poll() is not None:
            return
        with self.send_lock:
            try:
                self.worker.stdin.write(json.dumps(message) + "\n")
                self.worker.stdin.flush()
            except OSError:
                pass

    def allow_call(self):
        now = time.time()
        while self.calls and now - self.calls[0] > 60:
            self.calls.popleft()
        if len(self.calls) >= MAX_CALLS_PER_MINUTE:
            raise ValueError(f"This rule made more than {MAX_CALLS_PER_MINUTE} model calls in a minute.")
        self.calls.append(now)

    def allow_gallery(self):
        now = time.time()
        while self.galleries and now - self.galleries[0] > 60:
            self.galleries.popleft()
        if len(self.galleries) >= MAX_GALLERIES_PER_MINUTE:
            raise ValueError(f"This rule asked for more than {MAX_GALLERIES_PER_MINUTE} galleries in a minute.")
        self.galleries.append(now)

    def allow_control(self):
        now = time.time()
        while self.controls and now - self.controls[0] > 60:
            self.controls.popleft()
        if len(self.controls) >= MAX_CONTROLS_PER_MINUTE:
            raise ValueError(f"This rule used a camera more than {MAX_CONTROLS_PER_MINUTE} times in a minute.")
        self.controls.append(now)

    def allow_act(self):
        now = time.time()
        while self.acts and now - self.acts[0] > 60:
            self.acts.popleft()
        if len(self.acts) >= MAX_ACTS_PER_MINUTE:
            raise ValueError(f"This rule ran more than {MAX_ACTS_PER_MINUTE} actions in a minute.")
        self.acts.append(now)

    def _read(self):
        for line in self.worker.stdout:
            try:
                self._handle(json.loads(line))
            except ValueError:
                continue
        if self.app.entries.get(self.id) is self:
            trace = self.worker.stderr.read()[-400:] if self.worker.stderr else ""
            self.app.trouble(self, f"{self.name} stopped because of an error in its code", trace,
                             source="Rule code", hint="Change the words so Wonder writes the code again.")

    def _handle(self, message):
        kind = message.get("do")
        if kind == "done":
            self.pending = max(0, self.pending - 1)
        elif kind == "call":
            self.app.start_call(self, message)
        elif self.paused:
            # A job that was running when the person pressed Stop must not act afterwards.
            return
        elif kind == "show":
            self.values[message["name"]] = message["value"]
            if self.values_file:
                try:
                    self.values_file.parent.mkdir(exist_ok=True)
                    self.values_file.write_text(json.dumps(self.values))
                except OSError:
                    pass
        elif kind == "log":
            self.app.add_feed(self, "log", message["text"], check=message.get("check"))
        elif kind == "error":
            self.app.trouble(self, f"{self.name} hit an error in its code", message["trace"][-2000:],
                             source="Rule code", level="warn", hint="The rule keeps running. Check that it still does what you want.")
        elif kind == "draw":
            self.app.draw(self, message["text"], message.get("frame"), message.get("check"), message.get("target"))
        elif kind == "image":
            self.app.image(self, message["path"], message.get("caption", ""), message.get("check"))
        elif kind == "record":
            self.app.record(self, message["name"], message.get("values") or {}, message.get("check"))
        elif kind == "notify":
            actions.run({"action": kind, "text": message["text"]})
            self.app.notify(self, message["text"], message.get("picture"), message.get("check"), message.get("at"),
                            message.get("to"))
        elif kind == "say":
            actions.run({"action": kind, "text": message["text"]})
            self.app.add_feed(self, kind, message["text"], url=self.app.picture_url(self, message.get("picture")))

    def stop(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.kill()

    def public(self):
        return {
            "id": self.id, "sentence": self.sentence, "name": self.name, "plan": self.plan,
            "sensors": [s.public() for s in self.sensors], "values": self.values, "fired": self.fired,
            "schedules": [s.public() for s in self.schedules], "last_fired": self.last_fired,
            "cameras": self.app.watched(self),
            "pointed": (self.pointed or {}).get("noun", ""), "paused": self.paused,
            "today": self.app.runs_today(self), "status": self.app.rule_status(self),
            "focus_cameras": self.app.focus_cameras(self),
        }


class App:
    def __init__(self, client, logs_dir=LOGS_DIR, inline=False, journal=None, connectors=None, rules_file=None,
                 phone_file=None, problems=None, camera_kinds=None):
        """inline runs checks inside frame(), for tests. Live checks run in the background.

        With no rules_file, rules do not return after a restart, and the journal and the actions
        live in memory. main() passes the real files.
        """
        self.client = client
        self.inline = inline
        self.journal = journal or Journal()
        self.connectors = connectors or Connectors()
        self.channels = self.connectors.channels
        self.connectors.base_url = self.base_url()
        self.problems = problems or Problems()
        self.camera_kinds = camera_kinds or CameraKinds()
        self.rules_file = rules_file
        self.phone_file = phone_file
        try:
            self.phone_paired = bool(json.loads(Path(phone_file).read_text()).get("paired")) if phone_file else False
        except (OSError, ValueError):
            self.phone_paired = False
        # Before channels.py, a paired phone meant the ntfy app. It stays paired.
        if self.phone_paired and not self.channels.places["ntfy"]:
            self.channels.add_ntfy()
        self.drafts = OrderedDict()
        self.evidence = Evidence(logs_dir)
        self.entries = {}
        self.jobs = {}
        self.lock = threading.Lock()
        # Detection and analysis never share threads, so a busy job cannot delay a check.
        self.detection = ThreadPoolExecutor(max_workers=DETECTION_THREADS)
        self.analysis = ThreadPoolExecutor(max_workers=ANALYSIS_THREADS)
        self.frames = deque(maxlen=FRAME_BUFFER)
        # The newest frame time at which each wall tile's camera was turning, from camera.py.
        self.turned_tiles = {}
        self.focus_cache = OrderedDict()
        # Whether a moving camera still shows the thing that a Whenever pointed at. See _watch_views.
        self.views = {}
        # An event is one occurrence, named by its check. A step of a job, or the sensor that its job
        # armed, belongs to it. The page draws one card for each occurrence.
        self.parents = {}
        self.runs_cache = {}
        self.said = {}
        self.occ_events = OrderedDict()
        self.threads_file = THREADS_FILE if phone_file else None
        try:
            self.threads = OrderedDict(json.loads(self.threads_file.read_text())) if self.threads_file else OrderedDict()
        except (OSError, ValueError, TypeError):
            self.threads = OrderedDict()
        self.timeline = deque(maxlen=TIMELINE_FRAMES)
        self.feed = deque(maxlen=FEED_LENGTH)
        self.feed_id = 0
        self.results = deque(maxlen=FEED_LENGTH)
        self.result_id = 0
        self.points = OrderedDict()
        self.price_see = PRICE_SEE
        self.playback_url = PLAYBACK_URL
        # A test keeps its answers beside its own logs. The real server keeps them in data/asks.
        self.asks = ask.Asks(self, DATA_DIR / ask.ASKS_DIR_NAME if phone_file else Path(logs_dir) / ask.ASKS_DIR_NAME)
        self.oldest_cache = (0.0, None)
        # The moment index: free PE-Core embeddings of each camera, for Ask's ranking. It starts in main().
        self.embedder = embed.Embedder()
        # The people that the operator tagged. A test without a phone file keeps them in its own folder.
        self.people = people.People(DATA_DIR / "people" if phone_file else Path(logs_dir) / "people", self.embedder)
        self.moments = moments.Moments(DATA_DIR / "moments.db" if phone_file else Path(logs_dir) / "moments.db",
                                       self.embedder, self._moment_picture, lambda: self.wall, self._moment_busy)
        self.voices = {}
        self.open_voice = VoiceSession
        self.hold_until = 0.0
        self.last_frame = 0.0
        self.active_page = None
        self.live = threading.Condition()
        self.live_jpeg = None
        self.live_seq = 0
        self.live_at = 0.0
        self.cameras = []
        self.streams = {}
        self.live_viewers = 0
        self.wall = (1, 1)
        self.controls = Controls()
        self.see_calls = 0
        self.ask_calls = 0
        self.detect_runs = 0
        self.spark_usd = 0.0
        self.writer_usd = 0.0
        self.spend_day = datetime.now(ZONE).date()
        self.spend_flagged = None
        self.spend_file = SPEND_FILE if phone_file else None
        self.spend_saved = None
        self._load_spend()
        self.camera_starts = deque(maxlen=20)
        self.media_paths = (0.0, {})
        self.health_at = 0.0

    def _new_day(self):
        """The spend starts again at midnight, so a hosted server works the next day."""
        today = datetime.now(ZONE).date()
        if today != self.spend_day:
            # No lock: status() calls this while it holds self.lock.
            self.problems.resolve(f"spend:{self.spend_day}")
            self.spend_day = today
            self.see_calls = self.ask_calls = 0
            self.spark_usd = self.writer_usd = 0.0

    def spend(self):
        return self.see_calls * PRICE_SEE + self.spark_usd + self.writer_usd

    def _load_spend(self):
        try:
            saved = json.loads(self.spend_file.read_text()) if self.spend_file else {}
        except (OSError, ValueError):
            saved = {}
        if saved.get("day") == str(self.spend_day):
            self.see_calls = int(saved.get("see_calls") or 0)
            self.ask_calls = int(saved.get("ask_calls") or 0)
            self.spark_usd = float(saved.get("spark_usd") or 0)
            self.writer_usd = float(saved.get("writer_usd") or 0)
        if self.spend() < spend_limit(self.spend_day):
            # The limit was raised, or a warning stayed open from an older run.
            self.problems.resolve(f"spend:{self.spend_day}")

    def _save_spend(self):
        if not self.spend_file:
            return
        saved = {"day": str(self.spend_day), "see_calls": self.see_calls, "ask_calls": self.ask_calls,
                 "spark_usd": round(self.spark_usd, 6), "writer_usd": round(self.writer_usd, 6)}
        if saved == self.spend_saved:
            return
        try:
            self.spend_file.parent.mkdir(parents=True, exist_ok=True)
            self.spend_file.write_text(json.dumps(saved))
            self.spend_saved = saved
        except OSError:
            pass

    def runs_today(self, rule):
        """How many times a Whenever's thing happened since midnight, read from the journal now and then."""
        midnight = datetime.now(ZONE).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        key = (rule.id, int(time.time() // 15), rule.fired)
        cached = self.runs_cache.get(rule.id)
        if cached and cached[0] == key:
            return cached[1]
        count = self.journal.runs(rule.id, since=midnight)[0]
        self.runs_cache[rule.id] = (key, count)
        return count

    def rule_status(self, rule):
        """One honest line on whether a Whenever can see what it needs right now: {"level", "text"}."""
        if rule.paused:
            return {"level": "off", "text": "Stopped"}
        if self.spend() >= spend_limit(self.spend_day):
            return {"level": "warn", "text": "Paused until midnight: today's model budget is used up"}
        if self.cameras and time.time() - max(self.live_at, self.last_frame) > CAMERA_LOST_S:
            since = max(self.live_at, self.last_frame)
            when = f" since {datetime.fromtimestamp(since, ZONE):%H:%M}" if since else ""
            return {"level": "warn", "text": f"No picture from the cameras{when}"}
        noun = (rule.pointed or {}).get("noun") or "it"
        for name in self.focus_cameras(rule):
            view = self.views.get((rule.id, name)) or {}
            if view.get("lost_since"):
                others = [n for n in self.focus_cameras(rule) if n != name]
                rest = f" {joined(others)} watches it alone." if others else ""
                return {"level": "warn", "text": f"{name} does not show the {noun} now.{rest}"}
        watched = self.focus_cameras(rule) or self.watched(rule)
        if rule.sensors:
            return {"level": "ok", "text": f"Watching {joined(watched)}" if watched else "Watching every camera"}
        return {"level": "ok", "text": "Waiting for its time"}

    def stopped(self):
        self._new_day()
        if self.spend() < spend_limit(self.spend_day):
            if self.spend_flagged == self.spend_day:
                # The owner gave more for today.
                self.spend_flagged = None
                self.problems.resolve(f"spend:{self.spend_day}")
            return False
        if self.spend_flagged == self.spend_day:
            return True
        self.spend_flagged = self.spend_day
        self.problems.add(f"spend:{self.spend_day}", "Whenever paused for today: the model budget is used up", "warn",
                          "Spending", f"Spent ${self.spend():.2f} of ${spend_limit(self.spend_day):.2f} on {self.spend_day}.",
                          "Whenever starts again at midnight. The person who runs this server can raise the daily budget.")
        return True

    def trouble(self, rule, title, detail="", source="", hint="", level="error"):
        """Something that the person may need to fix. The feed and the journal keep it, and Problems
        groups its repeats, so the page shows it once."""
        self.add_feed(rule, "error", title, **({"detail": str(detail)[-2000:]} if detail else {}))
        return self.problems.add(f"{source}:{getattr(rule, 'id', '')}:{title}", title, level, source, detail, hint, rule)

    def add_feed(self, rule, kind, text, **extra):
        with self.lock:
            self.feed_id += 1
            item = {"id": self.feed_id, "rule": rule.name if rule else "Ask", "kind": kind, "text": text, **extra}
            self.feed.append(item)
        self.evidence.save_feed(item)
        if rule and kind in ("notify", "say", "draw", "image", "gallery", "log", "error"):
            picture = extra.get("url") or extra.get("frame") or ((extra.get("pictures") or [{}])[0].get("zoom"))
            if picture and picture.startswith("/evidence/") and getattr(rule, "folder", None):
                # The log of one run is served only during that run. The rule's own copy lasts.
                name = picture.split("?")[0].rsplit("/", 1)[-1]
                if (Path(rule.folder) / "frames" / name).is_file():
                    picture = f"/rule-file/{rule.id}/frames/{name}"
            occurrence = extra.get("occurrence") or self.occurrence_of(rule, extra.get("check"))
            pictures = [p.get("zoom") for p in extra.get("pictures") or [] if p.get("zoom")][:6]
            values = {k: v for k, v in (("picture", picture), ("check", extra.get("check")), ("clip", extra.get("clip")),
                                        ("cameras", extra.get("cameras")), ("occurrence", occurrence),
                                        ("pictures", pictures), ("places", extra.get("places")),
                                        ("mode", extra.get("mode")), ("sent_at", extra.get("sent_at"))) if v}
            self.journal.add(kind, rule.id, rule.name, text=text, values=values)
        return item

    def occurrence_of(self, rule, check=None, at=None):
        """The occurrence that a step belongs to: the event's check, or the event that started at time at.
        A rule's on_tick has no event, so it names one by its time."""
        if check:
            return self.parents.get(check, check)
        if at is None:
            return None
        try:
            at = float(at)
        except (TypeError, ValueError):
            return None
        with self.lock:
            events = list(self.occ_events.items())
        for occurrence, event in reversed(events):
            if event["rule"] == rule.id and abs(event["t"] - at) < 1.5:
                return occurrence
        for row in self.journal.history("event", rule=rule.name, since=at - 1.5, until=at + 1.5, limit=20):
            if row["values"].get("occurrence") and not row["values"].get("step"):
                return row["values"]["occurrence"]
        return None

    def occurrence_near(self, rule, picture):
        """The newest occurrence that started before a picture, within a wait's length. A rule's on_tick
        that sends a picture has no event, so its picture's time says which occurrence it is about."""
        name = Path(str(picture or "")).name
        with self.lock:
            when = next((f.t for f in reversed(self.timeline) if f.name == name), None)
            events = list(self.occ_events.items())
        if when is None:
            return None
        found = [(event["t"], occurrence) for occurrence, event in events
                 if event["rule"] == rule.id and 0 <= when - event["t"] <= MAX_ARM_S]
        return max(found)[1] if found else None

    def occurrence_event(self, rule, occurrence):
        """When an occurrence started, on which camera tile, and its proof frame. It lasts across restarts."""
        with self.lock:
            event = self.occ_events.get(occurrence)
        if event:
            return event
        for row in reversed(self.journal.history("event", rule=rule.name, limit=1000)):
            values = row["values"]
            if values.get("occurrence") == occurrence and not values.get("step"):
                frame = Path(str(values.get("picture") or "")).name or None
                return {"rule": rule.id, "t": row["t"], "tile": values.get("tile"), "frame": frame}
        return None

    def picture_url(self, rule, path):
        """The page address of a picture in a rule's folder, or None."""
        file = rule_picture(rule.folder, path) if path and rule.folder else None
        if not file:
            return None
        return f"/rule-file/{rule.id}/{quote(str(file.relative_to(Path(rule.folder).resolve())))}"

    def phone_topic(self):
        """The private ntfy topic for phone alerts. A first pairing makes one, so nobody edits a file."""
        topic = read_secrets(self.connectors.secrets_path).get("NTFY_TOPIC", "")
        if topic:
            return topic
        topic = f"whenever-{secrets.token_hex(8)}"
        path = Path(self.connectors.secrets_path)
        old = path.read_text() if path.exists() else "# Keys for actions. Git ignores this file.\n"
        path.write_text(old.rstrip("\n") + f"\nNTFY_TOPIC={topic}\n")
        os.chmod(path, 0o600)
        return topic

    def set_paired(self, paired):
        """The person saw the test alert in the ntfy app, so ntfy is a place for alerts."""
        self.phone_paired = bool(paired)
        if self.phone_file:
            Path(self.phone_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.phone_file).write_text(json.dumps({"paired": self.phone_paired}))
        if self.phone_paired:
            self.channels.add_ntfy()
        else:
            self.channels.remove("ntfy")
        return self.phone_paired

    def start_pairing(self, kind, login=False, host=None):
        """A link for a phone or a Telegram chat. A phone's link also logs that phone in.

        The link uses the address that the person has open, so the phone logs in to the same site.
        """
        token = self.channels.start(kind, login=login and kind in ("push", "camera"))
        base = f"https://{host}" if host in HOSTS else self.base_url()
        if kind == "camera":
            return {"token": token, "url": f"{base}/camera/{token}", "phone_ok": bool(HOSTS)}
        if kind == "telegram":
            bot = self.channels.telegram_bot()
            return {"token": token, "url": f"https://t.me/{bot['username']}?start={token}", "bot": bot}
        return {"token": token, "url": f"{base}/pair/{token}", "phone_ok": bool(HOSTS),
                "push_key": self.channels.push_public_key()}

    def base_url(self):
        """This server's own address. A phone can reach only a hosted one."""
        return f"https://{HOSTS[0]}" if HOSTS else f"http://localhost:{PORT}"

    def telegram_loop(self):
        """Reads the bot's messages, so /start can pair a chat. With no bot, it only waits."""
        wait = 2
        while True:
            if not self.channels.telegram_token():
                time.sleep(3)
                continue
            try:
                self.channels.poll_telegram()
                self.problems.resolve("telegram:poll")
                wait = 2
            except ChannelError as e:
                text = str(e)
                hint = ("Another program reads this bot's messages, maybe a second Whenever. Give each its own bot."
                        if "409" in text else "Whenever tries again. If the bot was deleted, pair Telegram again.")
                self.problems.add("telegram:poll", "Whenever cannot read Telegram messages", "warn", "Alerts", text, hint)
                time.sleep(wait)
                wait = min(wait * 2, 60)

    def send_alert(self, rule, text, title=None, picture=None, only=None, clip=None, reply=None, tag=None, skip=()):
        """One alert to every paired place. A place that fails is a problem for that place only."""
        places = self.channels.alert(text, title or (rule.name if rule else "Whenever"), picture,
                                     self.click_url(), self.base_url(), only, clip, reply, tag, skip)
        return self._place_results(rule, places)

    def _place_results(self, rule, places):
        for place in places:
            key = f"place:{place['id']}"
            if place["ok"]:
                self.problems.resolve(key)
            else:
                self.problems.add(key, f"An alert did not reach {place['label']}", "warn", "Alerts", place["detail"],
                                  "Send a test under Actions, Where alerts go. Pair it again if the test fails.", rule)
        return places

    def can_turn(self, name):
        return any(c["name"] == name and c.get("moves") for c in self.controls.public())

    def camera_kind(self, name):
        """fixed or moving. A place in a moving camera's picture is true only for one moment."""
        return self.camera_kinds.kind(name, self.can_turn(name)) if name else ""

    def media_ready(self):
        """Which camera paths the media server receives now, asked at most every 10 s. {} without one."""
        if not HLS_URL:
            return {}
        at, paths = self.media_paths
        if time.time() - at < 10:
            return paths
        try:
            with urllib.request.urlopen(MEDIA_API_URL + "/v3/paths/list", timeout=2) as reply:
                items = json.loads(reply.read(200_000)).get("items") or []
            paths = {str(item.get("name")): bool(item.get("ready")) for item in items}
        except (OSError, ValueError):
            paths = {}
        self.media_paths = (time.time(), paths)
        return paths

    def camera_live_now(self, name):
        """True or False for one camera, from the media server, or the whole feed's state without one."""
        path = self.streams.get(name) if isinstance(self.streams, dict) else None
        ready = self.media_ready()
        if path and ready:
            return ready.get(path, False)
        return time.time() - max(self.live_at, self.last_frame) < CAMERA_LOST_S

    # ----- What Ask needs from the cameras and the footage -----

    def _moment_picture(self, frame, tile):
        return search.Footage(self.evidence.root, self.wall).picture(frame, tile, draft=(768, 432))

    def _moment_busy(self, frame):
        folder = Path(self.evidence.root) / frame["session"] / "frames" / "full"
        return {int(p.stem.rsplit("_", 1)[1]) for p in folder.glob(f"{frame['name'][:-4]}_*.jpg")}

    def oldest_footage(self):
        """The time of the oldest saved frame, read now and then from the oldest session's list."""
        at, oldest = self.oldest_cache
        if time.time() - at < 300:
            return oldest
        oldest = None
        for folder in sorted(p for p in Path(self.evidence.root).iterdir() if p.is_dir()):
            try:
                with (folder / "frames.jsonl").open() as f:
                    for line in f:
                        record = json.loads(line)
                        if (folder / "frames" / record["name"]).exists():
                            oldest = record["t"]
                            break
            except (OSError, ValueError, KeyError):
                continue
            if oldest:
                break
        self.oldest_cache = (time.time(), oldest)
        return oldest

    def recording_minutes(self):
        """How far back the media server's full video goes."""
        return float(os.environ.get("WHENEVER_BUFFER_MIN") or 720) if PLAYBACK_URL else 0.0

    def newest_wall(self):
        with self.lock:
            newest = self.timeline[-1].name if self.timeline else None
        sharp = self.evidence.read_sharp(newest) if newest else None
        return Image.open(BytesIO(sharp)).convert("RGB") if sharp else None

    def camera_pictures(self, wall, names):
        """[(name, picture)] of the named cameras, each its own full frame when fresh. No names means all."""
        columns, rows = self.wall
        if columns * rows <= 1 or not self.cameras:
            return [("Camera", wall)]
        chosen = [n for n in self.cameras if n in names] or list(self.cameras)
        return [(name, self._camera_view(wall, self.cameras.index(name))) for name in chosen]

    def labelled(self, views):
        return jpeg_bytes(labelled_grid([(name, into_panel(picture)) for name, picture in views]), 85)

    def cameras_public(self):
        removed = self.camera_kinds.removed()
        return [{"name": name, "kind": self.camera_kind(name),
                 "by": "person" if self.camera_kinds.items.get(name, {}).get("set") else "whenever",
                 "why": self.camera_kinds.why(name, self.can_turn(name)), "removed": False,
                 "make": self.camera_kinds.make(name), "live": self.camera_live_now(name)}
                for name in self.cameras if name not in removed] + \
               [{"name": name, "kind": self.camera_kind(name), "by": "whenever", "why": "", "removed": True} for name in removed]

    def watched(self, rule):
        """The cameras that a Whenever names in its words or its sensors. [] means every camera."""
        text = " ".join([rule.sentence, *(f"{s.question} {s.target}" for s in rule.sensors)]).lower()
        return [name for name in self.cameras if name.lower() in text]

    def focus_cameras(self, rule):
        """The cameras that a Whenever's checks look at, in the wall's order. [] means the whole wall."""
        if not self.cameras or self.wall[0] * self.wall[1] <= 1:
            return []
        pointed = getattr(rule, "pointed", None) or {}
        named = {pointed.get("camera"), *(a.get("camera") for a in pointed.get("also") or []), *self.watched(rule)}
        names = [name for name in self.cameras if name in named]
        return names if len(names) < len(self.cameras) else []

    def pointed_box(self, rule, name):
        """Where the person pointed in one camera, from 0 to 1, or None."""
        pointed = getattr(rule, "pointed", None) or {}
        for item in [pointed, *(pointed.get("also") or [])]:
            box = item.get("view_box") or {}
            if item.get("camera") == name and box.get("w") and box.get("h"):
                return (box["x1"] / box["w"], box["y1"] / box["h"], box["x2"] / box["w"], box["y2"] / box["h"])
        return None

    def view_lost(self, rule, name):
        """True when a moving camera was last seen to show none of the thing that a Whenever pointed at."""
        return bool(self.views.get((rule.id, name), {}).get("lost_since"))

    def focus_plan(self, rule, t):
        """Which cameras a check at time t shows, as [(name, tile, box)], and the cameras it leaves out.

        A moving camera that turned in the last few seconds is left out, and so is one that lost the
        thing: its picture misleads. Its box is never used, because the place moved with the lens.
        Returns None when the whole wall is the right picture."""
        names = self.focus_cameras(rule)
        if not names:
            return None
        panels, left_out = [], []
        for name in names:
            tile = self.cameras.index(name)
            moving = self.camera_kind(name) == "moving"
            if moving and (t - self.turned_tiles.get(tile, -1e9) < TURN_SETTLE_S or self.view_lost(rule, name)):
                left_out.append(name)
                continue
            panels.append((name, tile, None if moving else self.pointed_box(rule, name)))
        if not panels:
            return [(name, self.cameras.index(name), None) for name in names], []
        return panels, left_out

    def focus_view(self, rule, plan):
        """One line for the model about what each picture shows."""
        if plan is None:
            return None
        panels, left_out = plan
        noun = ((getattr(rule, "pointed", None) or {}).get("noun") or "thing").strip()
        shown = []
        for name, _, box in panels:
            shown.append(name)
            if box:
                shown.append(f"{name}, close-up of the {noun}")
        line = ("Each picture shows only the cameras that matter here, each named in its top corner: "
                + "; ".join(shown) + ". A close-up is the same moment, cut from that camera at full size.")
        if left_out:
            line += (f" {joined(left_out)} {'is' if len(left_out) == 1 else 'are'} left out, because "
                     "that lens is turning or does not show it now.")
        return line

    def focus_picture(self, rule, name, plan):
        """The picture of one saved frame that a check shows its model, as JPEG bytes."""
        key = (name, repr(plan))
        with self.lock:
            if key in self.focus_cache:
                return self.focus_cache[key]
        wall = self._sharp_wall(name)
        noun = ((getattr(rule, "pointed", None) or {}).get("noun") or "thing").strip()
        panels = []
        for camera, tile, box in plan[0]:
            source = self._camera_source(name, tile, wall)
            if source is None:
                continue
            panels.append((camera, into_panel(source)))
            if box:
                panels.append((f"{camera}, close-up of the {noun}", into_panel(close_up(source, box))))
        jpeg = jpeg_bytes(labelled_grid(panels), 85) if panels else None
        with self.lock:
            self.focus_cache[key] = jpeg
            while len(self.focus_cache) > FOCUS_CACHE:
                self.focus_cache.popitem(last=False)
        return jpeg

    def _sharp_wall(self, name):
        sharp = self.evidence.read_sharp(name)
        return Image.open(BytesIO(sharp)).convert("RGB") if sharp else None

    def _camera_source(self, name, tile, wall=None):
        """One camera's picture in a saved frame: its own full frame if kept, or else its tile of the wall."""
        full = self.evidence.full_path(name, tile)
        if full:
            source = Image.open(full)
            source.draft("RGB", FOCUS_DRAFT)
            return source.convert("RGB")
        wall = wall if wall is not None else self._sharp_wall(name)
        return wall.crop(self._tile_box(wall.size, tile)) if wall is not None else None

    def light_pair(self, rule, before, after, plan):
        """The pointed thing's close-up in each camera that stays still: the moment before on the left, now
        on the right, as JPEG bytes. None when no such camera shows it. A moving camera's place moves with
        its lens, so it is not compared."""
        panels = []
        for camera, tile, box in plan[0]:
            if not box:
                continue
            pair = [self._camera_source(frame.name, tile) for frame in (before, after)]
            if any(source is None for source in pair):
                continue
            for label, frame, source in (("Before", before, pair[0]), ("Now", after, pair[1])):
                clock = datetime.fromtimestamp(frame.t, ZONE).strftime("%H:%M:%S")
                panels.append((f"{label} {clock}: {camera}", into_panel(close_up(source, box))))
        return jpeg_bytes(labelled_grid(panels), 85) if panels else None

    def only_light(self, rule, sensor, before, after):
        """(True, answer) when Spark sees that the pointed thing did not move between a moment before and
        now, and that only light changed or its parts say no now. Car headlights behind a closed gate made
        it look open, twice in one night. It runs only when a state of a pointed thing is about to start,
        for one small call. Any trouble gives (False, None), so detection goes on as before."""
        noun = ((getattr(rule, "pointed", None) or {}).get("noun") or "").strip()
        if (not noun or sensor.target or sensor.pattern != "state" or before is None or after is None
                or after.t - before.t < LIGHT_BEFORE_S):
            return False, None
        try:
            plan = self.focus_plan(rule, after.t)
            jpeg = self.light_pair(rule, before, after, plan) if plan else None
            if not jpeg:
                return False, None
            prompt = (
                "Two moments of the same place, cut at full size from a camera that does not move: Before on "
                "the left, Now on the right, each with its time in its corner.\n"
                f"A rule watches the {noun}: {sensor.question}\n"
                f"Compare the {noun} itself, part by part, against fixed things around it, such as posts, walls "
                "and the ground: the outline of each part, where its edges and bars are, and any gap. Brightness "
                "does not count. Car headlights, a lamp, sunlight, a shadow, or the camera's switch between "
                f"colour and infrared can make a closed {noun} look open or different, and light can shine "
                "through bars.\n"
                f"moved: true if the {noun} or any part of it, such as one leaf or a small door, is in another "
                "place, opened or closed, or appeared or went away, between Before and Now.\n"
                "only_light: true if what differs is only light, glare or shadow.\n"
                f"yes_now: judged only by where the parts of the {noun} are in Now, not by light, is the answer "
                "to the rule's question yes?\n"
                "note: one short line on what differs.")
            answer, cost = self.client.structured(prompt, LIGHT_SCHEMA, frames=[jpeg], name="light",
                                                  max_tokens=1500, lane="live")
        except Exception:  # a failed comparison must never stop detection
            return False, None
        with self.lock:
            self.ask_calls += 1
            self.spark_usd += cost
        # Only a thing that did not move can be vetoed: a real opening moves the gate, whatever the light.
        light = not answer.get("moved") and (bool(answer.get("only_light")) or answer.get("yes_now") is False)
        if light:
            print(f"{rule.name}: the {noun} did not move, so it is no event: "
                  f"{str(answer.get('note', ''))[:120]}", flush=True)
        return light, answer

    def _moment_before(self, sensor, t):
        """The newest saved frame from before the yes that is about to start a state at t: one check of
        the sensor earlier, and a little more."""
        frames = self._between(t - LIGHT_LOOK_BACK_S, t - sensor.check_every_s - LIGHT_BEFORE_S)
        return frames[-1] if frames else None

    def _light_veto(self, rule, sensor, picked, outcome):
        """A yes that could start a state of a pointed thing is compared with the moment before it first.
        If only the light changed, the yes becomes a no, and the check keeps Spark's words for the Why view."""
        hit, _, result = outcome
        # Every yes while the state is off, not only the one that seems to start it: two checks can run at
        # once, and then the first yes is not counted yet when the second one runs.
        if not hit or sensor.state.on:
            return outcome
        proof = proof_frame(result)
        after = next((frame for frame in picked if frame.name == proof), picked[-1])
        light, answer = self.only_light(rule, sensor, self._moment_before(sensor, picked[0].t), after)
        if answer is not None:
            result["light"] = answer
        if not light:
            return outcome
        return False, False, {**result, "answer": False}

    def watch_places(self, rule, sensor, t):
        """[(key, tile, box)]: the places of a sensor's watch plan, each from 0 to 1, in cameras that stay
        still. A moving camera's places move with its lens, so it never makes a check free."""
        plan = self.focus_plan(rule, t)
        cameras = [name for name, _, _ in plan[0]] if plan else list(self.cameras)
        fixed = [name for name in cameras if self.camera_kind(name) == "fixed"]
        found = []
        for spec in sensor.watch["places"]:
            for name in ([spec["camera"]] if spec["camera"] in fixed else [] if spec["camera"] else fixed):
                tile = self.cameras.index(name)
                if spec["pointed"]:
                    box = self.pointed_box(rule, name)
                    boxes, kind = ([box] if box else []), "pointed"
                elif spec["find"]:
                    boxes, kind = self.found_places(rule, name, spec["find"]), spec["find"]
                else:
                    boxes, kind = [(0.0, 0.0, 1.0, 1.0)], "view"
                found += [(f"{name}|{kind}|{index}", tile, tuple(box)) for index, box in enumerate(boxes)]
        return found

    def found_places(self, rule, camera, word):
        """The boxes of a thing named in words, such as "table", in one camera that stays still. SAM finds
        them once, and the rule's folder keeps them. Without a folder, or while spending is paused, none."""
        key = f"{camera}|{word}"
        with self.lock:
            cache = getattr(rule, "places", None)
            if cache is None:
                cache = rule.places = self._read_places(rule)
            known = cache.get(key)
        if known and (known.get("boxes") or time.time() - known.get("t", 0) < PLACE_RETRY_S):
            return [tuple(box) for box in known.get("boxes") or []]
        if self.stopped() or camera not in self.cameras:
            return []
        boxes = []
        try:
            tile = self.cameras.index(camera)
            with self.lock:
                newest = self.timeline[-1].name if self.timeline else None
            source = self._camera_source(newest, tile) if newest else None
            if source is None:
                return []  # no picture yet, such as just after a start: ask again with the next frame
            with self.lock:
                self.see_calls += 1
            for box in self.client.find(word, jpeg_bytes(source, 90)):
                if (box.get("c") or 0) >= PLACE_MIN_CONFIDENCE and box.get("w") and box.get("h"):
                    boxes.append((round(box["x1"] / box["w"], 4), round(box["y1"] / box["h"], 4),
                                  round(box["x2"] / box["w"], 4), round(box["y2"] / box["h"], 4)))
        except (ApiError, OSError, ValueError):
            boxes = []
        boxes = distinct_boxes(boxes)[:MAX_PLACES]
        with self.lock:
            cache[key] = {"boxes": boxes, "t": time.time()}
        self._save_places(rule, cache)
        return boxes

    def _read_places(self, rule):
        folder = getattr(rule, "folder", None)
        try:
            return json.loads((Path(folder) / "data" / PLACES_FILE).read_text()) if folder else {}
        except (OSError, ValueError):
            return {}

    def _save_places(self, rule, cache):
        folder = getattr(rule, "folder", None)
        if not folder:
            return
        try:
            target = Path(folder) / "data"
            target.mkdir(parents=True, exist_ok=True)
            (target / PLACES_FILE).write_text(json.dumps(cache, indent=1))
        except OSError:
            pass

    def place_now(self, rule, sensor, frame):
        """Each watched place in this frame: [(key, value)]. The value is a small edge map, or for a plan
        that counts, how many of its things have their middle in the place. None when this sensor pays
        for every check: its plan says so, or no camera that stays still shows one of its places, or the
        local detector is missing."""
        wake = sensor.watch["wake"]
        if wake not in ("change", "count") or (wake == "count" and not detect.available()):
            return None
        try:
            places = self.watch_places(rule, sensor, frame.t)
            by_tile, out = {}, []
            for key, tile, box in places:
                by_tile.setdefault(tile, {})[key] = box
            for tile, boxes in by_tile.items():
                source = self._camera_source(frame.name, tile)
                if source is None:
                    return None
                if wake == "count":
                    counts = dict.fromkeys(boxes, 0)
                    for item in detect.detect(source, sensor.watch["count"]):
                        key = place_of(item["box"], boxes, point=seat, near=COUNT_NEAR)
                        if key:
                            counts[key] += 1
                    out += list(counts.items())
                else:
                    out += [(key, search.region(source, box)) for key, box in boxes.items()]
        except (OSError, ValueError):
            return None
        return out or None

    def held_answer(self, sensor, places, t):
        """(answer, scores): the last paid answer if no watched place changed since it, else None, and how
        much each place still looks the same, from 1 down.

        A held answer never changes a state. Only a paid answer does. So while a state is on, only a yes
        is kept, and while it is off, only a no: after a paid answer that does not fit the state, the next
        check is paid too. A state still needs two paid yes answers on new frames to start, and one false
        no does not end it. A moment gets no, because nothing moved where it happens. The safety time
        makes one paid check anyway.
        """
        quiet = sensor.quiet
        if places is None or quiet is None:
            return None, []
        before = dict(quiet["places"])
        if any(key not in before for key, _ in places):
            return None, []
        scores = [same_place(before[key], place) for key, place in places]
        changed = min(scores) < sensor.watch["change"]
        if sensor.watch["wake"] == "count":
            # A new count in a place pays only when the next look sees it there too: one picture can miss
            # a person who passes behind another, or count a passer-by for a moment.
            last, sensor.last_look = sensor.last_look, dict(places)
            changed = last is not None and any(before[key] != value and last.get(key) == value for key, value in places)
        if t - quiet["t"] >= sensor.watch["safety_s"] or changed:
            return None, scores
        if sensor.pattern == "moment":
            return (False, False), scores
        if bool(quiet["hit"]) != sensor.state.on:
            return None, scores
        return (quiet["hit"], quiet["strong"]), scores

    def in_focus(self, rule, box, t=None):
        """True if a found box lies in one of the cameras that a Whenever watches. A moving lens that turns,
        or that looks away from the pointed thing, does not count: a person in the garden is not at the gate."""
        plan = self.focus_plan(rule, time.time() if t is None else t)
        names = [name for name, _, _ in plan[0]] if plan else []
        if not names or not box.get("w") or not box.get("h"):
            return True
        columns, rows = self.wall
        x = (box["x1"] + box["x2"]) / 2 / box["w"]
        y = (box["y1"] + box["y2"]) / 2 / box["h"]
        tile = min(int(y * rows), rows - 1) * columns + min(int(x * columns), columns - 1)
        return tile < len(self.cameras) and self.cameras[tile] in names

    def _event_tile(self, rule, box):
        """Which camera tile of the wall an event came from, so the page can show that camera alone."""
        columns, rows = self.wall
        if columns * rows <= 1:
            return None
        if box and box.get("w") and box.get("h"):
            x = (box["x1"] + box["x2"]) / 2 / box["w"]
            y = (box["y1"] + box["y2"]) / 2 / box["h"]
            return min(int(y * rows), rows - 1) * columns + min(int(x * columns), columns - 1)
        named = self.watched(rule)
        return self.cameras.index(named[0]) if len(named) == 1 else None

    def click_url(self):
        """Where a phone alert takes the person when they tap it. Only a hosted page has an address."""
        return f"https://{HOSTS[0]}/#/activity" if HOSTS else None

    def capture_fps(self):
        """The fastest rate that any awake sensor asked for."""
        now = time.time()
        rates = [s.frames_per_second for rule in self.entries.values() if not rule.paused
                 for s in rule.sensors if s.awake(now)]
        return max(rates, default=DEFAULT_CAPTURE_FPS)

    def status(self, after=0, after_result=0):
        with self.lock:
            return {
                "rules": [rule.public() for rule in self.entries.values()],
                "feed": [item for item in self.feed if item["id"] > after],
                "results": [item for item in self.results if item["id"] > after_result],
                "capture_fps": self.capture_fps(),
                "hold_capture": time.time() < self.hold_until,
                "live_camera": self.camera_live(),
                "cameras": self.cameras,
                "controls": self.controls.public(),
                "actions": self.connectors.public(),
                "phone_topic": read_secrets(self.connectors.secrets_path).get("NTFY_TOPIC", ""),
                "phone_paired": self.channels.paired(),
                "alerts": self.channels.public(),
                "camera_kinds": self.cameras_public(),
                "removed_cameras": self.camera_kinds.removed(),
                "streams": self.streams if HLS_URL else {},
                # A page that cannot play the media server watches a live wall, which costs decoding.
                "live_wanted": self.live_viewers > 0,
                # Who feeds the camera: security cameras through camera.py, or one page. A page compares
                # page with its own id, so it can say "this page" or "another tab or device".
                "owner": {"cameras": self.camera_live(), "page": str(self.active_page or "")},
                "platform": sys.platform, "hosted": bool(HOSTS),
                "wall": self.wall,
                "see_calls": self.see_calls,
                "ask_calls": self.ask_calls,
                # Pictures that the local detector read since the server started. They cost nothing.
                "detect_runs": self.detect_runs,
                "spend_usd": round(self.spend(), 4),
                "spend_limit_usd": spend_limit(self.spend_day),
                "stopped": self.stopped(),
                "problems": [{k: g[k] for k in ("id", "level", "source", "title", "hint", "rule_id", "count", "last")}
                             for g in self.problems.open()],
            }

    def start_job(self, sentence, example, idle, motions=None, gap_s=None, point=None, edit=None):
        """edit is the id of a live rule to replace. That rule keeps running until the new one passes."""
        with self.lock:
            old = self.entries.get(edit)
            if edit and not old:
                raise writer.WriterError("That rule is no longer running.")
            if not edit and len(self.entries) >= MAX_RULES:
                raise writer.WriterError(f"This prototype runs up to {MAX_RULES} rules.")
            job = {"id": uuid.uuid4().hex[:8], "status": "working", "steps": [], "error": ""}
            self.jobs[job["id"]] = job
        args = (job, sentence, example, idle, motions, gap_s, point, old)
        threading.Thread(target=self._write, args=args, daemon=True).start()
        return job["id"]

    def _write(self, job, sentence, example, idle, motions, gap_s, point=None, old=None):
        rule_id = uuid.uuid4().hex[:8]
        steps = []

        def progress(text):
            job["steps"].append(text[:200])
            steps.append(text)
        try:
            if not sentence:
                progress("Spark is reading your recording")
                sentence = writer.describe_example(self.client, example).strip()[:300]
                progress(f"Understood: {sentence}")
            pointed = old.pointed if old else None
            if point in self.points:
                pointed = self.pointed_for_rule(point)
                seen = ", ".join(filter(None, [pointed["camera"], *[a["camera"] for a in pointed["also"]]]))
                progress(f"You pointed at: {pointed['noun']}" + (f", seen in {seen}" if seen else ""))
            progress("Wonder is changing the rule" if old else "Wonder is writing the rule")
            folder = sandbox.copy_rule(old.folder, rule_id) if old else sandbox.create(rule_id)
            if point in self.points:
                pointed = self.save_pointed(point, folder, pointed)
            previous = json.loads((Path(old.folder) / "rule.json").read_text()) if old else None
            if example:
                save_recording(folder, example, idle, motions, gap_s)
            elif old:
                # The person's old recording still shows the event, so it still tests the sensors.
                example, idle, motions, gap_s = load_recording(folder)
            fields, cost = writer.write_rule(
                self.client, folder, sentence, example, idle, progress, motions, gap_s, pointed, previous,
                self.controls.describe(self.cameras), self.connectors.describe(), self.journal.summary())
            with self.lock:
                self.writer_usd += cost
            if fields.get("unsupported"):
                raise writer.WriterError(fields["unsupported"])
            (folder / "rule.json").write_text(json.dumps(
                {"sentence": sentence, **fields, "writer_steps": steps}, indent=1, ensure_ascii=False))
            rule = Rule(self, rule_id, sentence, fields, folder, pointed)
            rule.paused = bool(old and old.paused)
            rule.start()
            with self.lock:
                self.entries[rule_id] = rule
                if old:
                    self.entries.pop(old.id, None)
            if old:
                old.stop()
                self.add_feed(rule, "log", "Rule updated. It now follows your new words.")
            self.save_rules()
            job.update(status="done", rule=rule_id, cost=round(cost, 3))
        except (writer.WriterError, ApiError, OSError, ValueError, KeyError) as e:
            job.update(status="failed", error=str(e)[:300])

    def remove_rule(self, rule_id):
        with self.lock:
            rule = self.entries.pop(rule_id, None)
        if rule:
            rule.stop()
            self._lights_off(rule)
            self.save_rules()
        return rule is not None

    def save_rules(self):
        """Lists the live rules, so they return after a restart."""
        if not self.rules_file:
            return
        with self.lock:
            items = [{"id": rule.id, "pointed": rule.pointed, "paused": rule.paused} for rule in self.entries.values()]
        Path(self.rules_file).parent.mkdir(parents=True, exist_ok=True)
        Path(self.rules_file).write_text(json.dumps({"rules": items}, ensure_ascii=False))

    def load_rules(self):
        """Starts the rules that were live when the server stopped. Each keeps its ctx.state."""
        try:
            items = json.loads(Path(self.rules_file).read_text())["rules"]
        except (OSError, TypeError, ValueError, KeyError):
            return 0
        for item in items[:MAX_RULES]:
            rule_id = str(item.get("id") or "")
            folder = sandbox.RULES_DIR / rule_id
            if not rule_id.isalnum() or not (folder / "rule.json").exists():
                continue
            fields = json.loads((folder / "rule.json").read_text())
            rule = Rule(self, rule_id, fields.get("sentence", ""), fields, folder, item.get("pointed"))
            rule.paused = bool(item.get("paused"))
            rule.start()
            with self.lock:
                self.entries[rule_id] = rule
            self.add_feed(rule, "log", f"{rule.name} is back after a restart.")
        return len(self.entries)

    def pause_rule(self, rule_id):
        """Stop: the rule stays, but it watches nothing and calls no model until Resume."""
        rule = self.entries.get(rule_id)
        if not rule:
            return False
        if not rule.paused:
            now = time.time()
            rule.paused = True
            for sensor in rule.sensors:
                if sensor.state.on:
                    rule.send({"type": "off", "sensor": sensor.id, "t": now, "duration": now - sensor.state.on_since})
                sensor.reset(now)
            self._lights_off(rule)
            self.add_feed(rule, "log", f"{rule.name} is stopped.")
            self.save_rules()
        return True

    def resume_rule(self, rule_id):
        """Resume starts from now. Frames from the stopped time are not read."""
        rule = self.entries.get(rule_id)
        if not rule:
            return False
        if rule.paused:
            for sensor in rule.sensors:
                sensor.reset(time.time())
            rule.paused = False
            self.add_feed(rule, "log", f"{rule.name} is running again.")
            self.save_rules()
        return True

    def code(self, rule_id):
        rule = self.entries.get(rule_id)
        return (rule.folder / "rule.py").read_text() if rule else None

    def tick(self):
        """A schedule runs with no camera. on_tick needs the camera, or a schedule in the rule."""
        now = time.time()
        # A still picture sends a frame only once a minute, so the camera's heartbeat also counts.
        camera = now - max(self.last_frame, self.live_at) <= CAMERA_LOST_S
        with self.lock:
            rules = list(self.entries.values())
        for rule in rules:
            if rule.paused:
                continue
            for schedule in rule.schedules:
                if schedule.due(now):
                    self.journal.add("event", rule.id, rule.name, schedule.id, text="scheduled", t=now)
                    rule.fired = getattr(rule, "fired", 0) + 1
                    rule.last_fired = now
                    rule.send({"type": "on", "sensor": schedule.id, "t": now, "event": {"note": "It is time."}})
                    rule.send({"type": "off", "sensor": schedule.id, "t": now, "duration": 0.0})
            if camera or rule.schedules:
                rule.send({"type": "tick", "t": now})

    def _tick_loop(self):
        while True:
            time.sleep(TICK_S)
            self.tick()
            if time.time() - self.health_at >= HEALTH_EVERY_S:
                self.health_at = time.time()
                try:
                    self._save_spend()
                    self._watch_cameras()
                    self._watch_views()
                except Exception as e:  # a health check must never stop the rules' ticks
                    print("Health check failed:", e, flush=True)

    def _watch_cameras(self):
        """The cameras' own trouble, under Problems: no picture at all, or a reader that keeps restarting."""
        if not self.cameras:
            return
        now = time.time()
        newest = max(self.live_at, self.last_frame)
        if newest and now - newest > CAMERA_LOST_S:
            since = datetime.fromtimestamp(newest, ZONE).strftime("%H:%M")
            self.problems.add("cameras:lost", "The cameras stopped sending pictures", "warn", "Cameras",
                              f"No picture since {since}. No Whenever can see anything until they come back.",
                              "Check that the cameras and the camera service on the server are running.")
        elif newest:
            self.problems.resolve("cameras:lost")
        starts = [t for t in self.camera_starts if now - t <= RESTARTS_SPAN_S]
        if len(starts) >= RESTARTS_WARN:
            self.problems.add("cameras:restarts", "The camera reader keeps restarting", "warn", "Cameras",
                              f"It started {len(starts)} times in the last {RESTARTS_SPAN_S // 60} minutes. "
                              "Pictures can come late or stop for a while.",
                              "Look at the camera service's log on the server. It can run out of memory.")
        elif self.camera_starts and now - self.camera_starts[-1] > RESTARTS_SPAN_S:
            self.problems.resolve("cameras:restarts")

    def _watch_views(self):
        """Starts a look at each moving lens that a pointed Whenever needs, when one is due."""
        now = time.time()
        with self.lock:
            rules = [r for r in self.entries.values() if not r.paused and (r.pointed or {}).get("noun")]
        for rule in rules:
            if any(s.state.on for s in rule.sensors):
                continue
            for name in self.focus_cameras(rule):
                if self.camera_kind(name) != "moving" or not self.can_turn(name):
                    continue
                view = self.views.setdefault((rule.id, name), {})
                tile = self.cameras.index(name)
                turned = max(self.turned_tiles.get(tile, 0), self.camera_kinds.items.get(name, {}).get("turned") or 0)
                checked = view.get("checked", 0)
                if now - checked < VIEW_CHECK_S or now - turned < VIEW_QUIET_S:
                    continue
                if not (view.get("misses") or turned > checked or now - checked >= VIEW_SAFETY_S):
                    continue
                view["checked"] = now
                self._later(self._check_view, rule, name)

    def _check_view(self, rule, name):
        """Does a moving lens still show the thing? If not, it turns back to it, found in its fixed lens."""
        if self.stopped():
            return
        noun = rule.pointed["noun"]
        tile = self.cameras.index(name) if name in self.cameras else None
        with self.lock:
            newest = self.timeline[-1].name if self.timeline else None
        if tile is None or not newest:
            return
        full = self.evidence.full_path(newest, tile)
        sharp = self.evidence.read_sharp(newest)
        try:
            if full:
                jpeg = full.read_bytes()
            elif sharp:
                wall = Image.open(BytesIO(sharp)).convert("RGB")
                jpeg = jpeg_bytes(wall.crop(self._tile_box(wall.size, tile)), 88)
            else:
                return
            with self.lock:
                self.see_calls += 1
            boxes = self.client.see(noun, jpeg, lane="analysis")
        except (ApiError, OSError, ValueError):
            return
        view = self.views.setdefault((rule.id, name), {})
        key = f"view:{rule.id}:{name}"
        if any((b["c"] or 0) >= VIEW_MIN_CONFIDENCE for b in boxes):
            if view.get("lost_since"):
                self.problems.resolve(key)
            view.update(misses=0, lost_since=None)
            return
        view["misses"] = view.get("misses", 0) + 1
        if view["misses"] < VIEW_MISSES:
            return
        now = time.time()
        view["lost_since"] = view.get("lost_since") or now
        others = [n for n in self.focus_cameras(rule) if n != name]
        detail = f"{name} looks away from the {noun}." + (f" {joined(others)} watches it alone." if others else "")
        try:
            guide = self.controls.guide(name)
        except ControlError:
            guide = None
        box = self.pointed_box(rule, guide) if guide else None
        if box and now - view.get("turned_back", 0) >= VIEW_TURN_BACK_S:
            try:
                self.controls.aim(name, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                view["turned_back"] = now
                # Look again soon, to see that it worked.
                view["checked"] = now - VIEW_CHECK_S + 20
                view["misses"] = VIEW_MISSES - 1
                detail += f" Whenever turned it back to the {noun} at {datetime.now(ZONE):%H:%M}."
            except ControlError as e:
                detail += f" Whenever could not turn it back: {e}"
        self.problems.add(key, f"{name} looked away from the {noun}", "note", "Cameras", detail,
                          "A lens that follows people can stay where it lost them. Whenever turns it back.", rule)

    def start_ticks(self):
        threading.Thread(target=self._tick_loop, daemon=True).start()

    def _tile_box(self, size, index):
        """Where one camera sits on the wall, in pixels of a wall picture of this size."""
        columns, rows = self.wall
        width, height = size[0] / columns, size[1] / rows
        column, row = index % columns, index // columns
        return round(column * width), round(row * height), round((column + 1) * width), round((row + 1) * height)

    def _camera_view(self, wall, index):
        """One camera's own picture: its newest full frame if it is fresh, or else its tile of the wall."""
        now = time.time()
        for frame in reversed(list(self.frames)[-30:]):
            if now - frame.t > FULL_FRESH_S:
                break
            path = self.evidence.full_path(frame.name, index)
            if path:
                return Image.open(path).convert("RGB")
        return wall.crop(self._tile_box(wall.size, index))

    def point(self, jpeg, x, y, width, height):
        """A click on the view. SAM finds the one object under it, in that camera's own picture.

        A wall shrinks each camera, so a small thing such as a gate far away has few pixels. SAM and
        Spark get the clicked camera alone, from its newest full frame when there is one.
        """
        if self.stopped():
            return {"found": False, "stopped": True}
        wall = Image.open(BytesIO(jpeg)).convert("RGB")
        nx, ny = x / max(1, width - 1), y / max(1, height - 1)
        columns, rows = self.wall
        index, camera = None, ""
        if columns * rows > 1 and self.cameras:
            index = min(int(ny * rows), rows - 1) * columns + min(int(nx * columns), columns - 1)
            if index >= len(self.cameras):
                return {"found": False}
            camera = self.cameras[index]
            left, top, right, bottom = self._tile_box(wall.size, index)
            tx = (nx * wall.width - left) / max(1, right - left)
            ty = (ny * wall.height - top) / max(1, bottom - top)
            view = self._camera_view(wall, index)
        else:
            left, top, right, bottom = 0, 0, wall.width, wall.height
            tx, ty, view = nx, ny, wall
        view = fit(view, POINT_SIDE)
        view_jpeg = jpeg_bytes(view)
        with self.lock:
            self.see_calls += 1
        found = self.client.mark(view_jpeg, view.width, view.height,
                                 point=(round(tx * (view.width - 1)), round(ty * (view.height - 1))))
        if not found:
            return {"found": False, "camera": camera}
        view_box = found[0]
        # The page draws the outline on the wall, so the box goes back to wall pixels. The shape stays.
        sx, sy = (right - left) / view_box["w"], (bottom - top) / view_box["h"]
        box = {**view_box, "x1": round(left + view_box["x1"] * sx), "y1": round(top + view_box["y1"] * sy),
               "x2": round(left + view_box["x2"] * sx), "y2": round(top + view_box["y2"] * sy),
               "w": wall.width, "h": wall.height}
        point_id = uuid.uuid4().hex[:8]
        self.evidence.save_frame(time.time(), jpeg)
        with self.lock:
            self.points[point_id] = {"jpeg": jpeg, "box": box, "noun": "", "camera": camera, "index": index,
                                     "kind": self.camera_kind(camera), "view": view_jpeg, "view_box": view_box,
                                     "crop": None, "also": [], "named_by": "", "note": ""}
            while len(self.points) > MAX_POINTS:
                self.points.popitem(last=False)
        return {"found": True, "id": point_id, "box": box, "camera": camera, "kind": self.camera_kind(camera)}

    def name_point(self, point_id):
        point = self.points.get(point_id)
        if not point:
            return ""
        if not point["noun"]:
            view, view_box = point.get("view") or point["jpeg"], point.get("view_box") or point["box"]
            point["crop"] = point.get("crop") or zoom_crop(view, view_box)
            point["noun"], cost = self.client.name_object(view, view_box, point["crop"])
            point["named_by"] = "spark" if point["noun"] else ""
            with self.lock:
                self.spark_usd += cost
        return point["noun"]

    def point_crop(self, point_id):
        point = self.points.get(point_id)
        if not point:
            return None
        if not point.get("crop"):
            point["crop"] = zoom_crop(point.get("view") or point["jpeg"], point.get("view_box") or point["box"])
        return point["crop"]

    def find_elsewhere(self, point_id):
        """Looks for the same object in the other cameras, so a rule can watch it from every side.

        SAM finds each thing with the same name. Spark then compares them with the clicked one, so a
        second car in the driveway does not count as the car that the person meant.
        """
        point = self.points.get(point_id)
        if not point or point.get("index") is None or not point["noun"] or self.stopped():
            return []
        wall = Image.open(BytesIO(point["jpeg"])).convert("RGB")
        views = []
        for index, name in enumerate(self.cameras):
            if index == point["index"]:
                continue
            view_jpeg = jpeg_bytes(fit(self._camera_view(wall, index), POINT_SIDE))
            with self.lock:
                self.see_calls += 1
            try:
                boxes = [b for b in self.client.find(point["noun"], view_jpeg) if (b.get("c") or 1) >= 0.3][:MAX_ALSO]
            except ApiError:
                continue
            if boxes:
                views.append((index, name, view_jpeg, boxes))
        also = []
        if views:
            picks, cost = self.client.same_object(
                point["noun"], self.point_crop(point_id), point["camera"],
                [(name, numbered(view_jpeg, boxes), len(boxes)) for _, name, view_jpeg, boxes in views])
            with self.lock:
                self.spark_usd += cost
            for index, name, view_jpeg, boxes in views:
                number = picks.get(name, 0)
                if 1 <= number <= len(boxes):
                    box = {k: boxes[number - 1][k] for k in ("x1", "y1", "x2", "y2", "w", "h")}
                    also.append({"camera": name, "index": index, "kind": self.camera_kind(name), "view_box": box,
                                 "crop": zoom_crop(view_jpeg, box)})
        point["also"] = also
        return also

    def confirm_point(self, point_id, noun, note="", cameras=None):
        """The person checked the name, and chose the other cameras where the object is the same one."""
        point = self.points.get(point_id)
        if not point:
            raise KeyError(point_id)
        noun = " ".join(str(noun or "").split())[:40]
        if not noun:
            raise ValueError("Give the thing a name.")
        if noun.lower() != (point["noun"] or "").lower():
            point["named_by"] = "person"
        point["noun"] = noun
        point["note"] = " ".join(str(note or "").split())[:MAX_NOTE]
        if cameras is not None:
            keep = {str(c) for c in cameras}
            point["also"] = [a for a in point["also"] if a["camera"] in keep]
        return {"noun": point["noun"], "camera": point["camera"], "also": [a["camera"] for a in point["also"]],
                "kinds": {name: self.camera_kind(name) for name in [point["camera"], *[a["camera"] for a in point["also"]]] if name}}

    def pointed_for_rule(self, point_id):
        """What the rule keeps about the pointed object. Its pictures go in the rule's folder."""
        point = self.points[point_id]
        noun = self.name_point(point_id)
        camera = point.get("camera", "")
        views = [camera, *[a["camera"] for a in point["also"]]]
        return {"noun": noun, "box": point["box"], "camera": camera, "kind": self.camera_kind(camera),
                "view_box": {k: point["view_box"][k] for k in ("x1", "y1", "x2", "y2", "w", "h")} if point.get("view_box") else None,
                "also": [{"camera": a["camera"], "kind": self.camera_kind(a["camera"]), "view_box": a["view_box"]}
                         for a in point.get("also", [])],
                "note": point.get("note", ""), "named_by": point.get("named_by", ""),
                "moving": [name for name in views if name and self.camera_kind(name) == "moving"]}

    def save_pointed(self, point_id, folder, pointed):
        """Keeps the close-ups with the rule. A moving camera shows the thing in a new place each time,
        so its look, not its place, finds it again. Rule code reads pointed.json and these pictures."""
        point = self.points.get(point_id)
        if not point:
            return pointed
        pictures = Path(folder) / "pointed"
        pictures.mkdir(exist_ok=True)
        (pictures / "clicked.jpg").write_bytes(self.point_crop(point_id))
        pointed = {**pointed, "reference": "pointed/clicked.jpg", "also": [dict(a) for a in pointed["also"]]}
        for number, (saved, found) in enumerate(zip(pointed["also"], point.get("also", [])), 1):
            (pictures / f"also-{number}.jpg").write_bytes(found["crop"])
            saved["reference"] = f"pointed/also-{number}.jpg"
        (Path(folder) / "pointed.json").write_text(json.dumps(
            {k: v for k, v in pointed.items() if k != "box"}, indent=1, ensure_ascii=False))
        return pointed

    def start_voice(self):
        session = self.open_voice(self.client.key)
        with self.lock:
            self.voices[session.id] = session
        return session.id

    def voice_audio(self, voice_id, chunk):
        session = self.voices.get(voice_id)
        return session.audio(chunk) if session else ""

    def end_voice(self, voice_id):
        with self.lock:
            session = self.voices.pop(voice_id, None)
        return session.finish() if session else ""

    def _later(self, work, *args):
        """Runs analysis beside detection. Nothing that called this waits for it."""
        if self.inline:
            work(*args)
        else:
            self.analysis.submit(work, *args)

    def start_call(self, rule, message):
        self._later(self._rule_call, rule, message)

    def _lights_off(self, rule):
        """A light must not outlast the rule that turned it on."""
        for camera in rule.lit:
            try:
                self.controls.light(camera, False)
            except ControlError as e:
                self.trouble(rule, f"{camera} did not turn its light off", str(e), source="Camera",
                             hint="Check that the camera is on and online.")
        rule.lit.clear()

    def _rule_call(self, rule, message):
        """Runs one building block for a rule's code, and always sends an answer back."""
        reply = {"type": "reply", "id": message.get("id")}
        blocks = {"ask": self._rule_ask, "check": self._rule_check, "find": self._rule_find,
                  "frames": self._rule_frames, "gallery": self._rule_gallery,
                  "arm": self._rule_arm, "disarm": self._rule_disarm,
                  "aim": self._rule_aim, "move": self._rule_move, "light": self._rule_light,
                  "act": self._rule_act, "history": self._rule_history,
                  "detect": self._rule_detect, "places": self._rule_places,
                  "who": self._rule_who, "tag": self._rule_tag, "people": self._rule_people}
        try:
            # An action, the journal and the local detector call no model, so a report still works at the
            # spend limit. Places found before still come back, and new ones wait.
            if self.stopped() and message.get("name") not in ("act", "history", "disarm", "detect", "places", "who",
                                                              "tag", "people"):
                raise ValueError("The spend limit stopped this call.")
            if rule.paused:
                raise ValueError("This rule is stopped.")
            reply["value"] = blocks[message["name"]](rule, **(message.get("args") or {}))
        except ApiError as e:
            reply["error"] = str(e)[:300]
            self.problems.add(f"call:{rule.id}:{message.get('name')}:{e}", f"A model call from {rule.name} failed", "note",
                              "Meta", f"{message.get('name')}: {e}", "The rule's code got the error and can try again.", rule)
        except (KeyError, TypeError, ValueError) as e:
            reply["error"] = str(e)[:300]
        rule.reply(reply)

    def _step(self, rule, check, step, text):
        if check:
            self.evidence.add_step(check, step)
        self.add_feed(rule, "step", text)

    def _picture(self, rule, path):
        """A picture that rule code chose. It must be inside the rule's folder."""
        if not path:
            return None
        file = rule_picture(rule.folder, path) if rule.folder else None
        if not file:
            raise ValueError(f"The rule cannot send {str(path)[:80]}.")
        return file.read_bytes()

    def _rule_act(self, rule, name, inputs=None, picture=None, check=None):
        """Runs one of the person's actions for a rule. The server sends the request, not the rule."""
        rule.allow_act()
        inputs = dict(inputs or {})
        occurrence = self.occurrence_of(rule, check) or (self.occurrence_near(rule, picture) if picture else None)
        media = None
        try:
            if self.connectors.get(name)["kind"] == "phone":
                text = inputs.get("text") or rule.name
                if self.repeated(occurrence, text):
                    return {"ok": True, "status": 200, "how": "repeat", "reply": "Already sent a moment ago."}
                media = self.alert_media(rule, picture, occurrence, clip=False)
                title = inputs.get("title") or self._alert_title(rule, media)
                # The page shows the alert at once, as the phone does.
                self.add_feed(rule, "alert", text, url=media["url"], cameras=media["cameras"], occurrence=occurrence)
                places, how = self._deliver(rule, occurrence, text, title, media)
                sent = [p["label"] for p in places if p["ok"]]
                ok = bool(sent) or not self.channels.paired()
                result = {"ok": ok, "status": 200 if ok else 502, "places": sent, "how": how,
                          "reply": "Sent to " + ", ".join(sent) if sent else
                          ("Shown here. Pair a phone under Actions to get it there too." if ok else "No place took it.")}
            else:
                result = self.connectors.run(name, inputs, self._picture(rule, picture), self.click_url())
        except ActionError as e:
            self.journal.add("action", rule.id, rule.name, name, text=str(e),
                             values={"ok": False, **({"occurrence": occurrence} if occurrence else {})})
            raise
        url = (media or {}).get("url") or self.picture_url(rule, picture)
        values = {"ok": result["ok"], "inputs": inputs, "picture": url, "occurrence": occurrence,
                  "clip": (media or {}).get("clip_url"), "places": result.get("places"),
                  "cameras": (media or {}).get("cameras"), "how": result.get("how"),
                  "sent_at": round((media or {}).get("sent_at") or 0, 1) or None}
        self.journal.add("action", rule.id, rule.name, name, text=f"status {result['status']}",
                         values={k: v for k, v in values.items() if v not in (None, [], "")} | {"ok": result["ok"]})
        word = "done" if result["ok"] else f"failed with status {result['status']}"
        self._step(rule, check, {"act": name, "inputs": inputs, **result}, f"{name}: {word}")
        return result

    def _rule_history(self, rule, kind=None, name=None, rule_name=None, since=None, until=None, limit=2000):
        """Reads the journal of all the person's rules, not only this one. A report reads this."""
        return self.journal.history(kind, name, rule_name, since, until, limit)

    def record(self, rule, name, values, check=None):
        occurrence = self.occurrence_of(rule, check)
        try:
            self.journal.add("record", rule.id, rule.name, name,
                             values={**values, **({"occurrence": occurrence} if occurrence else {})})
        except ValueError as e:
            self.trouble(rule, f"{rule.name} could not keep a record", str(e), source="Journal", level="warn")

    def notify(self, rule, text, picture=None, check=None, at=None, to=None):
        """ctx.notify: the page and every paired place get the cameras where it happened, and a clip.
        Cutting the cameras and making the clip take a moment, so this runs beside the rule. to names the
        places to send it to, such as "Waiter"."""
        self._later(self._notify, rule, text, picture, check, at, to)

    def _notify(self, rule, text, picture, check=None, at=None, to=None):
        occurrence = self.occurrence_of(rule, check, at) or (self.occurrence_near(rule, picture) if picture else None)
        if self.repeated(occurrence, f"{to or ''}:{text}"):
            return
        try:
            media = self.alert_media(rule, picture, occurrence, clip=False)
        except (ValueError, OSError) as e:
            media = {"picture": None, "url": self.picture_url(rule, picture), "cameras": []}
            self.trouble(rule, f"{rule.name} could not cut its picture", str(e)[:300], source="Alerts", level="note")
        # A later note about an occurrence that already alerted, with nothing new to see, stays on the page.
        news = bool(picture) or not (occurrence and occurrence in self.threads)
        if news:
            self.add_feed(rule, "alert", text, url=media["url"], cameras=media["cameras"], occurrence=occurrence)
        places, how = [], "here"
        try:
            places, how = self._deliver(rule, occurrence, text, self._alert_title(rule, media), media, news, to)
        except (ValueError, OSError) as e:
            self.trouble(rule, f"{rule.name} could not send an alert", str(e)[:300], source="Alerts", level="warn")
        if how == "here" and "clip_url" not in media and not (occurrence and occurrence in self.threads):
            self.alert_clip(rule, media)
        self.add_feed(rule, "notify", text, url=media["url"], clip=media.get("clip_url"), cameras=media["cameras"],
                      occurrence=occurrence, places=[p["label"] for p in places if p["ok"]], mode=how,
                      sent_at=round(media.get("sent_at") or 0, 1) or None)

    def alert(self, rule, text, picture=None):
        """An alert from the server itself, such as a test. Rules use notify."""
        if self.channels.paired():
            self._later(self._notify, rule, text, picture)

    def focus_tiles(self, rule, event=None):
        """The cameras that an alert is about: the one that fired first, then one more that shows the
        same thing, such as the other view of a pointed gate. [] means the picture has one camera."""
        columns, rows = self.wall
        if columns * rows <= 1 or not self.cameras:
            return []
        tiles = []
        if event and event.get("tile") is not None and event["tile"] < len(self.cameras):
            tiles.append(event["tile"])
        pointed = rule.pointed or {}
        for name in [pointed.get("camera"), *[a.get("camera") for a in pointed.get("also") or []], *self.watched(rule)]:
            if name in self.cameras and self.cameras.index(name) not in tiles:
                tiles.append(self.cameras.index(name))
        return tiles[:MAX_FOCUS] if len(tiles) < len(self.cameras) else []

    def camera_picture(self, name, tiles):
        """One moment of the cameras that matter, side by side. Each is its own full frame when one was
        kept, or its tile of the sharp wall. Returns a PIL picture, or None."""
        sharp = self.evidence.read_sharp(name)
        wall = Image.open(BytesIO(sharp)).convert("RGB") if sharp else None
        if not tiles:
            return wall
        pictures = []
        for tile in tiles:
            full = self.evidence.full_path(name, tile)
            if full:
                pictures.append(Image.open(full).convert("RGB"))
            elif wall is not None:
                pictures.append(wall.crop(self._tile_box(wall.size, tile)))
        if not pictures:
            return wall
        height = min(CLIP_HEIGHT, max(p.height for p in pictures))
        pictures = [p.resize((max(2, round(p.width * height / p.height)), height), Image.LANCZOS) for p in pictures]
        joined = Image.new("RGB", (sum(p.width for p in pictures) + 6 * (len(pictures) - 1), height), (16, 16, 16))
        left = 0
        for picture in pictures:
            joined.paste(picture, (left, 0))
            left += picture.width + 6
        return joined

    def make_clip(self, rule, t, tiles, folder):
        """A short MP4 of the cameras that matter, from the frames around the moment. None without ffmpeg."""
        frames = [f for f in list(self.timeline) if t - CLIP_BEFORE_S <= f.t <= t + CLIP_AFTER_S][-MAX_CLIP_FRAMES:]
        if len(frames) < MIN_CLIP_FRAMES or not shutil.which("ffmpeg"):
            return None
        with tempfile.TemporaryDirectory() as work:
            size = None
            count = 0
            for frame in frames:
                picture = self.camera_picture(frame.name, tiles)
                if picture is None:
                    continue
                picture = fit(picture, CLIP_WIDTH)
                size = size or (picture.width // 2 * 2, picture.height // 2 * 2)
                picture.resize(size).save(Path(work) / f"{count:04d}.jpg", quality=88)
                count += 1
            if count < MIN_CLIP_FRAMES:
                return None
            rate = max(2, min(8, round(count / max(0.5, frames[-1].t - frames[0].t))))
            out = Path(work) / "clip.mp4"
            done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(rate), "-i", str(Path(work) / "%04d.jpg"),
                                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                                  capture_output=True, timeout=60)
            if done.returncode or not out.exists():
                return None
            clip = out.read_bytes()
        target = Path(folder) / "clips"
        target.mkdir(exist_ok=True)
        name = f"{int(t)}-{uuid.uuid4().hex[:6]}.mp4"
        (target / name).write_bytes(clip)
        return clip, f"/rule-file/{rule.id}/clips/{name}"

    def _buffer_video(self, camera, start, duration):
        """One camera's own video for a span, from the media server's buffer, as MP4 bytes, or None."""
        path = self.streams.get(camera)
        if not PLAYBACK_URL or not path:
            return None
        query = urlencode({"path": path, "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                           "duration": f"{duration:.1f}", "format": "mp4"})
        try:
            with urllib.request.urlopen(f"{PLAYBACK_URL}/get?{query}", timeout=20) as reply:
                raw = reply.read()
        except (urllib.error.URLError, OSError):
            return None
        return raw if len(raw) >= 1000 else None

    def buffer_clip(self, rule, cameras, t, folder):
        """A clip from the media server's buffer: the cameras' own video around the moment.

        Several cameras play side by side, in the order of the alert's picture, so the picture is a moment
        of the clip. Chats and mail apps play H.264 best, so the short clip is converted once. The buffer
        keeps the original. If one camera's video is missing, there is no buffer clip.
        """
        start = t - CLIP_BEFORE_S
        # The newest second may not be in the buffer yet.
        duration = min(CLIP_BEFORE_S + CLIP_AFTER_S, max(1.0, time.time() - 1.5 - start))
        videos = [self._buffer_video(camera, start, duration) for camera in cameras]
        if not videos or any(video is None for video in videos):
            return None
        with tempfile.TemporaryDirectory() as work:
            sources = []
            for index, video in enumerate(videos):
                source = Path(work) / f"in{index}.mp4"
                source.write_bytes(video)
                sources += ["-i", str(source)]
            out = Path(work) / "clip.mp4"
            if len(videos) == 1:
                shape = ["-c:v", "libx264"]
            else:
                scaled = "".join(f"[{i}:v]scale=-2:{CLIP_HEIGHT},setsar=1[v{i}];" for i in range(len(videos)))
                stacked = "".join(f"[v{i}]" for i in range(len(videos))) + f"hstack=inputs={len(videos)}:shortest=1[out]"
                shape = ["-filter_complex", scaled + stacked, "-map", "[out]", "-c:v", "libx264"]
            done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *sources, "-an", *shape,
                                   "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                   str(out)], capture_output=True, timeout=120)
            if done.returncode or not out.exists():
                return None
            clip = out.read_bytes()
        target = Path(folder) / "clips"
        target.mkdir(exist_ok=True)
        name = f"{int(t)}-{uuid.uuid4().hex[:6]}.mp4"
        (target / name).write_bytes(clip)
        return clip, f"/rule-file/{rule.id}/clips/{name}"

    def alert_media(self, rule, picture=None, occurrence=None, clip=True):
        """What an alert shows: only the cameras where it happened, as a picture and a clip.

        The alert's own occurrence says which camera fired and when, even minutes later or after a
        restart. Without one, the rule's newest event says it. With one camera, the whole picture is
        that camera. clip=False leaves the clip for later, so the picture can go out first.
        """
        event = self.occurrence_event(rule, occurrence) if occurrence else None
        if event is None:
            event = getattr(rule, "last_event", None)
            if event and time.time() - event["t"] > EVENT_FRESH_S:
                event = None
        tiles = self.focus_tiles(rule, event)
        cameras = [self.cameras[i] for i in tiles]
        name = None
        if picture:
            name = Path(str(picture)).name if self.evidence.frame_path(self.evidence.session, Path(str(picture)).name) else None
        name = name or (event or {}).get("frame")
        media = {"picture": None, "url": self.picture_url(rule, picture), "cameras": cameras}
        chosen = self.camera_picture(name, tiles) if name else None
        if chosen is None and picture:
            media["picture"] = self._picture(rule, picture)
            return media
        if chosen is None:
            return media
        media["picture"] = jpeg_bytes(fit(chosen, CLIP_WIDTH * len(tiles or [1])))
        if rule.folder:
            target = Path(rule.folder) / "pictures"
            target.mkdir(exist_ok=True)
            file = target / f"alert-{int(time.time())}-{uuid.uuid4().hex[:6]}.jpg"
            file.write_bytes(media["picture"])
            media["url"] = f"/rule-file/{rule.id}/pictures/{file.name}"
            media["when"] = (event or {}).get("t") or time.time()
            media["tiles"] = tiles
            if clip:
                self.alert_clip(rule, media)
        return media

    def alert_clip(self, rule, media):
        """Adds the clip of the moment to media, from the media server's buffer or from saved frames."""
        if not rule.folder or "when" not in media:
            return media
        # The same cameras as the picture, in its order: the picture is a moment of the clip.
        cameras = media["cameras"] or (list(self.cameras[:1]) if len(self.cameras) == 1 else [])
        clip = (self.buffer_clip(rule, cameras, media["when"], rule.folder) if cameras else None) or \
            self.make_clip(rule, media["when"], media["tiles"], rule.folder)
        if clip:
            media["clip"], media["clip_url"] = clip
        return media

    def _deliver(self, rule, occurrence, text, title, media, news=True, to=None):
        """Sends one alert to the person's places, and returns (places, how): "sent", "reply" or "here".

        One occurrence is one message. Its first alert goes out at once with its picture, and its clip
        follows in the same message. A later alert of the same occurrence answers that message, if it
        has something new to see (news), such as the person's picture. Without news it stays on the page.
        An alert to named places (to), such as the waiter and then the manager, is its own message."""
        if to and self.channels.paired():
            only = self.channels.named(to)
            if not only:
                self.problems.add(f"to:{rule.id}:{to}", f"No alert place is named {to}", "warn", "Alerts",
                                  f"{rule.name} sent an alert to {to}, so it went to every place.",
                                  "Under Actions, Where alerts go, give a place that name.", rule)
            media["sent_at"] = time.time()
            places = self.send_alert(rule, text, title, media["picture"], only=only or None, skip=("email",))
            self.alert_clip(rule, media)
            self.channels.add_clip({p["id"]: p.get("ref") for p in places if p["ok"]}, media.get("clip"),
                                   title or rule.name, text, self.click_url(), media["picture"])
            return places, "sent"
        thread = self.threads.get(occurrence) if occurrence else None
        if not self.channels.paired() or (thread is not None and not news):
            return [], "here"
        media["sent_at"] = time.time()
        if thread is not None:
            return self.send_alert(rule, text, title, media["picture"], reply=thread, tag=occurrence), "reply"
        places = self.send_alert(rule, text, title, media["picture"], tag=occurrence, skip=("email",))
        sent = {p["id"]: p.get("ref") for p in places if p["ok"]}
        if occurrence:
            self._keep_thread(occurrence, sent)
        self.alert_clip(rule, media)
        later = self.channels.add_clip(sent, media.get("clip"), title or rule.name, text, self.click_url(),
                                       media["picture"])
        for place in later:
            if place["kind"] == "email":
                places += self._place_results(rule, [place])
            elif not place["ok"]:
                # The picture already reached this place. Only the clip is missing, so this is a note.
                self.problems.add(f"clip:{place['id']}", f"The clip did not reach {place['label']}", "note", "Alerts",
                                  place["detail"], "The alert and its picture arrived. The clip is in Activity.", rule)
        return places, "sent"

    def repeated(self, occurrence, text):
        """True if these words about this occurrence already went out a moment ago."""
        if not occurrence:
            return False
        now = time.time()
        key = (occurrence, " ".join(str(text or "").split()).lower())
        with self.lock:
            self.said = {k: t for k, t in self.said.items() if now - t < REPEAT_S}
            if key in self.said:
                return True
            self.said[key] = now
        return False

    def _keep_thread(self, occurrence, sent):
        with self.lock:
            self.threads[occurrence] = sent
            while len(self.threads) > MAX_THREADS:
                self.threads.popitem(last=False)
            saved = json.dumps(self.threads)
        if self.threads_file:
            try:
                self.threads_file.parent.mkdir(parents=True, exist_ok=True)
                self.threads_file.write_text(saved)
            except OSError:
                pass

    def _alert_title(self, rule, media):
        return f"{rule.name} · {joined(media['cameras'])}" if media["cameras"] else rule.name

    def draft_action(self, words):
        """Wonder turns the person's words into one web request. Nothing runs until they test it."""
        if self.stopped():
            raise ValueError("The spend limit stopped this call.")
        draft, cost = writer.write_action(self.client, words, [a["name"] for a in self.connectors.all()])
        with self.lock:
            self.writer_usd += cost
        if draft.get("unsupported"):
            raise writer.WriterError(draft["unsupported"])
        action = check_draft(draft, [a["name"] for a in self.connectors.items])
        draft_id = uuid.uuid4().hex[:8]
        with self.lock:
            self.drafts[draft_id] = action
            while len(self.drafts) > MAX_DRAFTS:
                self.drafts.popitem(last=False)
        return {"id": draft_id, "action": self._show_draft(action), "cost": round(cost, 3)}

    def _show_draft(self, action):
        """The page sees the request with its blanks. The keys stay in actions.env."""
        return {**action, "missing": self.connectors.missing(action)}

    def test_draft(self, draft_id, inputs):
        action = self.drafts.get(draft_id)
        if not action:
            raise ActionError("That draft is gone. Describe the action again.")
        return self.connectors.send(action, inputs)

    def understand(self, sentence):
        """Spark says back how it reads a rule, for about $0.002, before Wonder spends more."""
        if self.stopped():
            raise ValueError("The spend limit stopped this call.")
        cameras = self.controls.describe(self.cameras) if self.cameras else ""
        summary, cost = writer.understand(self.client, sentence, self.connectors.describe(), cameras)
        with self.lock:
            self.spark_usd += cost
        return {**summary, "cost": round(cost, 4)}

    def test_action(self, name, inputs):
        """Runs a saved action from the page, so the person can see it work."""
        return self.connectors.run(name, inputs, None, self.click_url())

    def save_draft(self, draft_id):
        with self.lock:
            action = self.drafts.pop(draft_id, None)
        if not action:
            raise ActionError("That draft is gone. Describe the action again.")
        return self.connectors.add(action)

    def _rule_ask(self, rule, question, frames=(), model="spark", check=None):
        rule.allow_call()
        jpegs = [self.evidence.read_sharp(name) for name in list(frames)[:MAX_ASK_FRAMES]]
        jpegs = [jpeg for jpeg in jpegs if jpeg]
        if not jpegs:
            raise ValueError("Those frames are not available.")
        answer, cost = self.client.look(question, jpegs, model)
        with self.lock:
            self.ask_calls += 1
            if model == "wonder":
                self.writer_usd += cost
            else:
                self.spark_usd += cost
        self._step(rule, check, {"model": model, "question": question, "frames": len(jpegs), **answer},
                   f"{model.capitalize()} says: {answer['answer']} ({answer['confidence']:.2f} sure)")
        return answer

    def _rule_check(self, rule, claim, frames=(), model="spark", reference=None, place=None, check=None):
        """A model judges each frame alone. A search or a claim about a frame uses this. With a place, the
        model sees only that place's close-up, cut at full size from its camera."""
        names = list(frames)[:MAX_ASK_FRAMES]
        with self.lock:
            times = {f.name: f.t for f in self.timeline if f.name in names}
        if place:
            tile, box = self._place_tile(place)
            pictures = [(name, self._place_picture(name, tile, box)) for name in names]
        else:
            pictures = [(name, self._check_picture(rule, name, times.get(name))) for name in names]
        pictures = [(name, jpeg) for name, jpeg in pictures if jpeg]
        if not pictures:
            raise ValueError("Those frames are not available.")
        for _ in pictures:
            rule.allow_call()
        example = self.evidence.read_sharp(reference) if reference else None
        if reference and example is None and rule.folder:
            # Such as pointed/clicked.jpg, the close-up that the person confirmed.
            file = rule_picture(rule.folder, reference)
            example = file.read_bytes() if file else None

        def judge(picture):
            return picture[0], *self.client.verify(claim, picture[1], model, example)

        with ThreadPoolExecutor(max_workers=MAX_CHECK_THREADS) as pool:
            judged = list(pool.map(judge, pictures))
        cost = sum(item[2] for item in judged)
        with self.lock:
            self.ask_calls += len(judged)
            if model == "wonder":
                self.writer_usd += cost
            else:
                self.spark_usd += cost
        found = [{"name": name, **result} for name, result, _ in judged]
        found.sort(key=lambda item: (item["passed"], item["confidence"]), reverse=True)
        best = found[0]
        passed = sum(item["passed"] for item in found)
        answer = f"{passed} of {len(found)} frames passed. Best: {best['seen']}"
        self._step(rule, check, {"model": model, "question": claim, "answer": answer,
                                 "confidence": best["confidence"], "frames": len(found)},
                   f"{model.capitalize()} checked {len(found)} frames alone, {passed} passed: {best['seen']}")
        return found

    def _check_picture(self, rule, name, t):
        """What ctx.check shows its model for one frame: the watched cameras large, or else the frame."""
        plan = self.focus_plan(rule, t) if t is not None else None
        if plan is not None:
            try:
                jpeg = self.focus_picture(rule, name, plan)
            except (OSError, ValueError):
                jpeg = None
            if jpeg:
                return jpeg
        return self.evidence.read_sharp(name)

    def _place_tile(self, place):
        """(tile, box) of a place that rule code sent: a camera's name and a box from 0 to 1."""
        camera = str(place.get("camera") or "")
        if not camera and len(self.cameras) <= 1:
            tile = 0
        elif camera in self.cameras:
            tile = self.cameras.index(camera)
        else:
            raise ValueError(f"There is no camera named {camera[:40]}.")
        x1, y1, x2, y2 = (number(v, 0, 1, 0) for v in (list(place.get("box") or []) + [0, 0, 0, 0])[:4])
        if x2 - x1 < 0.005 or y2 - y1 < 0.005:
            raise ValueError("A place needs a box from 0 to 1, such as a Place from ctx.places.")
        return tile, (x1, y1, x2, y2)

    def _place_picture(self, name, tile, box):
        source = self._camera_source(name, tile)
        return jpeg_bytes(into_panel(close_up(source, box)), 88) if source is not None else None

    def _rule_detect(self, rule, things=("person",), frames=(), camera=None, min_confidence=detect.MIN_CONFIDENCE,
                     region=None, pose=False, check=None):
        """The local detector on each camera of some frames, for a rule's code. It calls no model, so it is
        free, and the spend limit does not stop it. Boxes are from 0 to 1 in each camera's own picture. With
        pose, each person also gets body points, and whether a hand is up, they lie down, or they sit."""
        if not detect.available():
            raise ValueError("The local detector is not installed on this server.")
        if pose and not detect.pose_available():
            raise ValueError("The body point model is not installed on this server.")
        labels = []
        for word in ([things] if isinstance(things, str) else list(things or []))[:8]:
            label = detect.label_for(word)
            if not label:
                raise ValueError(f"The local detector does not know {str(word)[:40]!r}. It knows: {', '.join(detect.LABELS)}.")
            labels.append(label)
        if camera and camera not in self.cameras:
            raise ValueError(f"There is no camera named {str(camera)[:40]}.")
        cameras = [camera] if camera else (self.focus_cameras(rule) or list(self.cameras) or [""])
        confidence = number(min_confidence, 0.05, 0.95, detect.MIN_CONFIDENCE)
        region = tuple(number(v, 0, 1, 0) for v in region[:4]) if region and len(region) >= 4 else None
        jobs = [(name, cam) for name in list(frames)[:MAX_DETECT_FRAMES] for cam in cameras]

        def look(job):
            name, cam = job
            if cam:
                source = self._camera_source(name, self.cameras.index(cam))
            else:
                source = self._sharp_wall(name)
            if source is None:
                return []
            found = [{"frame": name, "camera": cam, "label": item["label"], "confidence": item["confidence"],
                      "box": list(item["box"]), "tall": item["tall"]} for item in detect.detect(source, labels, confidence, region)]
            people = [item for item in found if item["label"] == "person"][:MAX_POSES] if pose else []
            for item, body in zip(people, detect.poses(source, [item["box"] for item in people])):
                item["pose"] = body
            return found

        with ThreadPoolExecutor(max_workers=detect.AT_ONCE) as pool:
            found = [item for items in pool.map(look, jobs) for item in items]
        with self.lock:
            self.detect_runs += len(jobs)
        if check:
            counts = ", ".join(f"{sum(1 for f in found if f['label'] == label)} {label}" for label in labels)
            self._step(rule, check, {"model": "local", "question": ", ".join(labels), "answer": counts},
                       f"The local detector found {counts} in {len(frames)} frames, at no cost")
        return found

    def _rule_places(self, rule, thing, camera=None, check=None):
        """Every such thing in the cameras that stay still, found once by SAM and kept in the rule's folder,
        named by rows from the top left, such as "table 3"."""
        word = " ".join(str(thing or "").split())[:40]
        if not word:
            raise ValueError("Name a thing, such as table.")
        if camera and camera not in self.cameras:
            raise ValueError(f"There is no camera named {str(camera)[:40]}.")
        cameras = [camera] if camera else (self.focus_cameras(rule) or list(self.cameras))
        fixed = [name for name in cameras if self.camera_kind(name) == "fixed"]
        if cameras and not fixed:
            raise ValueError("Places need a camera that stays still. A moving camera's picture moves with its lens.")
        places = []
        for name in fixed or [""]:
            with self.lock:
                known = (getattr(rule, "places", None) or {}).get(f"{name}|{word}")
            if not known and not self.stopped():
                rule.allow_call()
            boxes = self.found_places(rule, name, word) if name else self._whole_places(rule, word)
            # By rows from the top, then from the left, as a person reads a floor plan.
            for box in sorted(boxes, key=lambda b: (round((b[1] + b[3]) * 2), (b[0] + b[2]) / 2)):
                places.append({"name": f"{word} {len(places) + 1}", "camera": name, "box": list(box)})
        if check:
            self._step(rule, check, {"model": "sam", "question": word, "answer": f"{len(places)} found"},
                       f"{len(places)} {word} places")
        return places

    def _whole_places(self, rule, word):
        """Places of a thing in a server with no named cameras: the whole picture is one camera."""
        with self.lock:
            cache = getattr(rule, "places", None)
            if cache is None:
                cache = rule.places = self._read_places(rule)
            known = cache.get(f"|{word}")
            newest = self.timeline[-1].name if self.timeline else None
        if known:
            return [tuple(box) for box in known.get("boxes") or []]
        wall = self._sharp_wall(newest) if newest and not self.stopped() else None
        if wall is None:
            return []
        with self.lock:
            self.see_calls += 1
        boxes = []
        for box in self.client.find(word, jpeg_bytes(wall, 90)):
            if (box.get("c") or 0) >= PLACE_MIN_CONFIDENCE and box.get("w") and box.get("h"):
                boxes.append((round(box["x1"] / box["w"], 4), round(box["y1"] / box["h"], 4),
                              round(box["x2"] / box["w"], 4), round(box["y2"] / box["h"], 4)))
        boxes = distinct_boxes(boxes)[:MAX_PLACES]
        with self.lock:
            cache[f"|{word}"] = {"boxes": boxes, "t": time.time()}
        self._save_places(rule, cache)
        return boxes

    def _person_picture(self, frame, camera):
        """One camera's own picture of a saved frame, as OpenCV wants it, or None."""
        if camera and camera not in self.cameras:
            raise ValueError(f"There is no camera named {str(camera)[:40]}.")
        source = self._camera_source(frame, self.cameras.index(camera)) if camera else self._sharp_wall(frame)
        return people.picture_array(source) if source is not None else None

    def _rule_who(self, rule, items=(), look=True, check=None):
        """Which tagged person each person box is: by face across days and weeks, and by look within a day.
        It runs on this server and costs nothing. Each sure sighting teaches Whenever the person better."""
        if not people.available():
            raise ValueError("The face models are not installed on this server.")
        answers, pictures = [], {}
        for item in list(items)[:MAX_WHO]:
            key = (item.get("frame"), item.get("camera") or "")
            if key not in pictures:
                pictures[key] = self._person_picture(*key)
            picture = pictures[key]
            if picture is None:
                answers.append(None)
                continue
            box = tuple(number(v, 0, 1, 0) for v in (list(item.get("box") or []) + [0, 0, 1, 1])[:4])
            found = self.people.read(picture, box, look=bool(look))
            match = self.people.match(found)
            if match:
                self.people.saw(match, found, key[1], where=key[0])
            answers.append({**(match or {}), "face": found["face"] is not None, "face_size": round(found["face_size"])})
        named = [a["name"] for a in answers if a and a.get("name")]
        if check:
            self._step(rule, check, {"model": "local", "question": "who", "answer": ", ".join(named) or "nobody tagged"},
                       f"Faces and looks: {', '.join(named) or 'nobody tagged'}, at no cost")
        return answers

    def _rule_tag(self, rule, frame=None, camera=None, box=None, name="", note="", check=None):
        """Tags a person from a frame, for a rule, such as everyone who works behind the counter."""
        if not people.available():
            raise ValueError("The face models are not installed on this server.")
        picture = self._person_picture(frame, camera or "")
        if picture is None:
            raise ValueError("That frame is not available.")
        box = tuple(number(v, 0, 1, 0) for v in (list(box or []) + [0, 0, 1, 1])[:4]) if box else None
        person = self.people.tag(name, picture, box, note, by=f"rule {rule.name}")
        self.add_feed(rule, "log", f"Tagged {person['name']}")
        return person

    def _rule_people(self, rule, check=None):
        return self.people.public()

    def tag_from_page(self, url, name, note="", box=None):
        """Tags the person in a picture that the page shows, such as a gallery picture of a visitor. With no
        box, it is the largest person in the picture."""
        if not people.available():
            raise ValueError("The face models are not installed on this server.")
        data = self.page_picture(url)
        if not data:
            raise ValueError("That picture is no longer here.")
        picture = people.picture_array(data)
        if box is None and detect.available():
            found = detect.detect(data, ["person"], 0.3)
            if found:
                box = max(found, key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))["box"]
        return self.people.tag(name, picture, box, note, by="person")

    def page_picture(self, url):
        """The bytes of a picture by the address that the page shows it at, or None."""
        path, _, query = str(url or "").partition("?")
        path = unquote(path)
        if path.startswith("/rule-file/"):
            parts = path.split("/", 3)
            folder = sandbox.RULES_DIR / parts[2] if len(parts) == 4 and parts[2].isalnum() else None
            file = rule_picture(folder, parts[3]) if folder else None
            return file.read_bytes() if file else None
        if path.startswith("/evidence/"):
            parts = path.split("/")
            if len(parts) != 4:
                return None
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            camera = params.get("camera", "")
            file = None
            if parts[2] == self.evidence.session and camera.isdigit():
                file = self.evidence.full_path(parts[3], int(camera))
            file = file or self.evidence.sharp_path(parts[3], parts[2])
            return file.read_bytes() if file else None
        return None

    def _rule_find(self, rule, thing, frame=None, check=None):
        rule.allow_call()
        jpeg = self.evidence.read_sharp(frame)
        if not jpeg:
            raise ValueError("That frame is not available.")
        with self.lock:
            self.see_calls += 1
        boxes = self.client.see(thing, jpeg, lane="analysis")
        boxes = [{**{k: b[k] for k in ("x1", "y1", "x2", "y2", "w", "h")}, "confidence": b["c"]} for b in boxes]
        boxes.sort(key=lambda b: b["confidence"] or 0, reverse=True)
        self._step(rule, check, {"model": "sam", "question": thing, "answer": f"{len(boxes)} found"},
                   f"SAM found {len(boxes)} {thing}")
        return boxes

    def _shots_in(self, thing, name, t, rule=None):
        """Finds a thing in the best copies of one moment. Each camera's own frame has the most pixels.

        For a Whenever that watches some cameras, only those count, as its checks see them. In a camera
        that stays still, the thing must be near the pointed thing: a person at the gate, not beyond the wall.
        """
        plan = self.focus_plan(rule, t) if rule is not None else None
        places = {tile: box for _, tile, box in plan[0]} if plan else None
        full = self.evidence.full_copies(name)
        sources = [(camera, path.read_bytes()) for camera, path in full if places is None or camera in places]
        if not sources and not full:
            jpeg = self.evidence.read_sharp(name)
            sources = [(None, jpeg)] if jpeg else []
        shots = []
        for camera, jpeg in sources:
            with self.lock:
                self.see_calls += 1
            boxes = self.client.see(thing, jpeg, lane="analysis")
            good = [box for box in boxes if (box["c"] or 0) >= GALLERY_MIN_CONFIDENCE]
            place = (places or {}).get(camera)
            if place and box_has_size(good):
                good = [box for box in good if near_place(box, place)]
            if camera is None and rule is not None:
                # The whole wall: only a box in one of the Whenever's cameras counts.
                good = [box for box in good if self.in_focus(rule, box, t)]
            if not good:
                continue
            picture = Image.open(BytesIO(jpeg)).convert("RGB")
            for box in good:
                shots.append({"name": name, "t": t, "camera": camera, "box": box,
                              "score": sharpness(picture, box) * self._lens_bonus(camera),
                              "zoom": zoom(picture, box)})
        return shots

    def _rule_gallery(self, rule, thing, frames=(), count=6, caption="", check=None):
        """Finds a thing across frames and shows a row of zoomed pictures, the best first.

        A click on a picture opens its full frame, so a person can judge it. One sharpest frame can
        be the wrong one, and a model's "clear" is only an opinion.
        """
        names = list(dict.fromkeys(frames))[:MAX_GALLERY_FRAMES]
        count = int(number(count, 1, MAX_GALLERY_SHOTS, 6))
        # This has its own limit, because it ends a job. A rule's rechecks must not cost it the final pictures.
        rule.allow_gallery()
        with self.lock:
            times = {f.name: f.t for f in self.timeline if f.name in names}
        with ThreadPoolExecutor(max_workers=MAX_CHECK_THREADS) as pool:
            found = list(pool.map(lambda name: self._shots_in(thing, name, times.get(name, 0.0), rule), names))
        chosen = pick_shots([shot for shots in found for shot in shots], count)
        pictures = []
        for index, shot in enumerate(chosen):
            camera = shot["camera"]
            path = f"pictures/{shot['name'][:-4]}_{camera}_{index}.jpg"
            pictures.append({
                "name": shot["name"], "t": shot["t"], "path": path, "score": round(shot["score"], 1),
                "camera": self.cameras[camera] if camera is not None and camera < len(self.cameras) else "",
                "zoom": f"/rule-file/{rule.id}/{path}" if rule.folder else "",
                "frame": f"/evidence/{self.evidence.session}/{shot['name']}"
                         + (f"?camera={camera}" if camera is not None else ""),
                "box": {key: shot["box"][key] for key in ("x1", "y1", "x2", "y2", "w", "h")},
            })
            if rule.folder:
                target = Path(rule.folder) / path
                target.parent.mkdir(exist_ok=True)
                shot["zoom"].save(target, "JPEG", quality=92)
        text = caption or f"Best pictures of {thing}"
        if check and pictures:
            self.evidence.add_step(check, {"pictures": pictures, "caption": text})
        # A gallery from on_tick has no event. Its middle frame's time says which occurrence it is about,
        # because its frames can start a little before the event.
        occurrence = self.occurrence_of(rule, check) or (self.occurrence_near(rule, names[len(names) // 2]) if names else None)
        self.add_feed(rule, "gallery", f"{text} ({len(pictures)})", check=check, pictures=pictures, occurrence=occurrence)
        return pictures

    def _lens_bonus(self, camera):
        names = {item["name"] for item in self.controls.public() if item["moves"]}
        moving = camera is not None and camera < len(self.cameras) and self.cameras[camera] in names
        return MOVING_LENS_BONUS if moving else 1.0

    def _tile(self, name):
        """Returns (left, top, right, bottom) of a camera's place on the wall, from 0 to 1."""
        columns, rows = self.wall
        index = self.cameras.index(name)
        left, top = index % columns, index // columns
        return left / columns, top / rows, (left + 1) / columns, (top + 1) / rows

    def _find_in_guide(self, camera, thing, frame=None):
        """Finds a thing in the fixed lens's picture. Returns its centre from 0 to 1, or None."""
        with self.lock:
            name = frame or (self.timeline[-1].name if self.timeline else None)
        wall = self.evidence.read_sharp(name) if name else None
        if not wall:
            raise ValueError("No frame is available.")
        picture = Image.open(BytesIO(wall)).convert("RGB")
        left, top, right, bottom = self._tile(self.controls.guide(camera))
        tile = picture.crop((round(left * picture.width), round(top * picture.height),
                             round(right * picture.width), round(bottom * picture.height)))
        out = BytesIO()
        tile.save(out, "JPEG", quality=90)
        with self.lock:
            self.see_calls += 1
        boxes = [box for box in self.client.see(thing, out.getvalue(), lane="analysis") if box["w"] and box["h"]]
        if not boxes:
            return None
        best = max(boxes, key=lambda box: box["c"] or 0)
        x, y = centre(best)
        return x / best["w"], y / best["h"]

    def _rule_aim(self, rule, camera, thing=None, x=None, y=None, frame=None, check=None):
        """The fixed lens finds, and the moving lens goes to look closer."""
        rule.allow_control()
        if thing:
            rule.allow_call()
            point = self._find_in_guide(camera, thing, frame)
            if not point:
                self._step(rule, check, {"model": "sam", "question": thing, "answer": "0 found"},
                           f"SAM did not find {thing} in {self.controls.guide(camera)}")
                return None
            x, y = point
        self.controls.aim(camera, x, y)
        text = f"{camera} turned to {thing or 'a point'}"
        self._step(rule, check, {"model": "camera", "question": text, "answer": f"x {x:.2f}, y {y:.2f}"}, text)
        return {"x": x, "y": y}

    def _rule_move(self, rule, camera, direction, seconds=1.0, check=None):
        rule.allow_control()
        self.controls.move(camera, direction, seconds)
        text = f"{camera} moved {direction}"
        self._step(rule, check, {"model": "camera", "question": text, "answer": "Done"}, text)
        return True

    def _rule_light(self, rule, camera, on=True, seconds=20.0, check=None):
        rule.allow_control()
        self.controls.light(camera, on, seconds)
        (rule.lit.add if on else rule.lit.discard)(camera)
        text = f"{camera} light {'on' if on else 'off'}"
        self._step(rule, check, {"model": "camera", "question": text, "answer": "Done"}, text)
        return True

    def camera_command(self, body):
        """A person's press on the page. It waits in its own request, so no check waits for it."""
        camera, action = str(body["camera"]), body["do"]
        if action == "move":
            self.controls.move(camera, str(body["direction"]), PAGE_MOVE_S)
        elif action == "light":
            self.controls.light(camera, bool(body["on"]), PAGE_LIGHT_S)
        elif action == "aim":
            self.controls.aim(camera, *self._point_in_guide(camera, float(body["x"]), float(body["y"])))
        else:
            raise KeyError(action)
        return {"controls": self.controls.public()}

    def _point_in_guide(self, camera, x, y):
        """Turns a place on the wall into a point in the fixed lens's picture."""
        guide = self.controls.guide(camera)
        left, top, right, bottom = self._tile(guide)
        if not (left <= x < right and top <= y < bottom):
            raise ControlError(f"Click inside {guide}.")
        return (x - left) / (right - left), (y - top) / (bottom - top)

    def _rule_frames(self, rule, t=None, before=2.0, after=0.0, count=8):
        """Frames around a moment, for a rule's code. It waits if the rule asks for later frames."""
        t = time.time() if t is None else float(t)
        before = number(before, 0, MAX_BEFORE_S, 2.0)
        end = t + number(after, 0, MAX_AFTER_S, 0.0)
        count = int(number(count, 1, MAX_RULE_FRAMES, 8))
        # The page sends few frames in a still picture, so it must keep capturing for this wait.
        self.hold_until = max(self.hold_until, end)
        wait = end - time.time()
        if wait > 0 and not self.inline:
            time.sleep(wait + 0.3)
        frames = self._between(t - before, end)
        # A still picture repeats itself, so moments with movement come first.
        moving = [f for f in frames if f.motion >= MOTION_MIN]
        frames = moving if len(moving) >= count else frames
        with self.lock:
            nearest = next((f for f in reversed(self.timeline) if f.t <= end), None)
        return self._share(rule, spread(frames, count) if frames else [nearest] if nearest else [])

    def _sensor(self, rule, sensor_id):
        for sensor in rule.sensors:
            if sensor.id == sensor_id:
                return sensor
        raise ValueError(f"This rule has no sensor named {sensor_id}.")

    def _rule_arm(self, rule, sensor, seconds=120, since=None, check=None):
        """Lets a rule wait for a later moment. A job can outlast the event that started it."""
        target = self._sensor(rule, sensor)
        seconds = number(seconds, 1, MAX_ARM_S, 120)
        now = time.time()
        target.arm(now, seconds, since)
        # What the armed sensor sees is a step of the event whose job armed it.
        target.armed_by = self.occurrence_of(rule, check) or target.armed_by
        # The page sends few frames in a still picture, and this wait needs them.
        self.hold_until = max(self.hold_until, now + seconds)
        self.add_feed(rule, "step", f"Now waiting for {sensor}, for up to {seconds:.0f} s")
        return True

    def _rule_disarm(self, rule, sensor):
        target = self._sensor(rule, sensor)
        target.armed_until = 0.0
        self.hold_until = time.time() if not self._waiting() else self.hold_until
        self.add_feed(rule, "step", f"Stopped waiting for {sensor}")
        return True

    def _waiting(self):
        now = time.time()
        return any(s.armed_until and s.armed_until > now for r in self.entries.values() for s in r.sensors)

    def _between(self, start, end):
        """Saved frames in a time span, oldest first. A job that runs late still gets its evidence."""
        found = []
        with self.lock:
            for frame in reversed(self.timeline):
                if frame.t < start:
                    break
                if frame.t <= end:
                    found.append(frame)
        return found[::-1]

    def _share(self, rule, frames):
        """Copies frames into the rule's folder, because its sandbox cannot read the logs."""
        shared = []
        for frame in frames:
            path = ""
            if rule.folder:
                target = Path(rule.folder) / "frames" / frame.name
                target.parent.mkdir(exist_ok=True)
                # A sharp copy can arrive just after its frame, so replace an older small copy.
                target.write_bytes(self.evidence.read_sharp(frame.name) or frame.jpeg or b"")
                path = f"frames/{frame.name}"
            shared.append({"name": frame.name, "t": frame.t, "motion": frame.motion, "path": path})
        return shared

    def _event_detail(self, rule, check, result, proof):
        """What a rule's code gets with its event, so it can look at the same frames."""
        names = set(result.get("frames") or [])
        with self.lock:
            frames = [f for f in self.timeline if f.name in names]
        answers = result.get("answers") or []
        review = result.get("review")
        note = answers[0].get("note", "") if answers else ""
        if review:
            note = "; ".join([result.get("who", ""), *review["clues_for"][:3]]).strip("; ")
        return {
            "check": check, "proof": proof, "frames": self._share(rule, frames), "note": note,
            "facts": answers[0].get("facts", []) if answers else [],
            "boxes": [{k: b[k] for k in ("x1", "y1", "x2", "y2", "w", "h")} for b in result.get("boxes") or []],
        }

    def image(self, rule, path, caption="", check=None):
        """Shows a frame, a crop or a chart that a rule made."""
        file = rule_picture(rule.folder, path)
        if not file:
            self.trouble(rule, f"{rule.name} could not show a picture", f"The rule cannot show {str(path)[:80]}.",
                         source="Rule code", level="note")
            return
        relative = file.relative_to(Path(rule.folder).resolve())
        url = f"/rule-file/{rule.id}/{quote(str(relative))}"
        self.add_feed(rule, "image", caption, url=url, check=check)
        if check:
            self.evidence.add_step(check, {"image": url, "caption": caption})

    def draw(self, rule, text, frame=None, check=None, target=None):
        """Freezes the page on a frame, and puts text on it.

        The rule can choose the frame and what to outline. Otherwise it is the frame that
        proved the event. The freeze shows at once and the outline follows, so a slow model
        does not delay it. A rule can also draw from on_tick, when no event just fired.
        """
        stored = self.evidence.check(check) if check else None
        with self.lock:
            newest = self.timeline[-1].name if self.timeline else None
        name = frame if self.evidence.read_frame(frame) else (stored or {}).get("proof_frame") or newest
        sensor = next((s for s in rule.sensors if stored and s.id == stored["sensor"]), rule.sensors[0])
        url = f"/evidence/{self.evidence.session}/{name}" if name else None
        item = self.add_feed(rule, "draw", text, frame=url, check=check)
        if not name:
            return
        self._later(self._outline, rule, sensor, name, check, item["id"], target)

    def _find_outline(self, rule, sensor, jpeg, chosen=None):
        """Returns the one object to outline on a frame, or None.

        It tries the fastest way first. A name fails if SAM does not know that word, so the
        last way lets Spark find the object from the event, and SAM cuts out that box.
        """
        pointed = {} if chosen else rule.pointed or {}
        box = pointed.get("box")
        # The click's place is still right only in a camera that stays still.
        if box and pointed.get("camera") and self.camera_kind(pointed["camera"]) != "fixed":
            box = None
        target = chosen or pointed.get("noun") or sensor.target or sensor.outline
        found = []
        if target:
            with self.lock:
                self.see_calls += 1
            found = self.client.find(target, jpeg)
        if not found and box:
            # A fixed object stays in its place, even if its name does not match this frame.
            with self.lock:
                self.see_calls += 1
            found = self.client.mark(jpeg, box["w"], box["h"], box=box)
        if not found:
            located, cost = self.client.locate(f"Where is the {chosen}?" if chosen else sensor.question, jpeg)
            with self.lock:
                self.spark_usd += cost
                self.see_calls += 1 if located else 0
            if located:
                found = self.client.mark(jpeg, located["w"], located["h"], box=located)
        if not found:
            return None
        if box:
            x, y = centre(box)
            return min(found, key=lambda s: abs(centre(s)[0] - x) + abs(centre(s)[1] - y))
        if len(found) > 1 and pointed.get("reference"):
            picked = self._pick_by_look(rule, pointed, jpeg, found)
            if picked:
                return picked
        return max(found, key=lambda s: s["c"] or 0)

    def _pick_by_look(self, rule, pointed, jpeg, found):
        """Of several things with the same name, the one that looks like the close-up from the click."""
        file = rule_picture(rule.folder, pointed["reference"]) if rule.folder else None
        if not file:
            return None
        found = sorted(found, key=lambda s: s["c"] or 0, reverse=True)[:MAX_ALSO]
        try:
            marked = numbered(jpeg, found)
        except OSError:
            return None
        picks, cost = self.client.same_object(pointed["noun"], file.read_bytes(), pointed.get("camera") or "a camera",
                                              [("All cameras", marked, len(found))])
        with self.lock:
            self.spark_usd += cost
        number = picks.get("All cameras", 0)
        return found[number - 1] if 1 <= number <= len(found) else None

    def _outline(self, rule, sensor, name, check=None, draw=None, target=None):
        """The page keeps its freeze until this answers, even when no outline is found."""
        jpeg = self.evidence.read_frame(name)
        outline = None
        try:
            found = self._find_outline(rule, sensor, jpeg, target) if jpeg and not self.stopped() else None
        except ApiError as e:
            found = None
            # A missing outline changes nothing that the person must act on, so it is a note under Problems.
            self.problems.add(f"outline:{rule.id}", f"{rule.name} drew its picture without an outline", "note",
                              "SAM 3.1", str(e)[:160], "The picture is still right. The outline returns when the "
                              "model answers in time.", rule)
        if found:
            outline = {key: found[key] for key in OUTLINE_FIELDS if key in found}
            # A rule can draw on a later frame. The Why view paints this on the proof frame.
            if check and name == (self.evidence.check(check) or {}).get("proof_frame"):
                self.evidence.update_check(check, {"outline": outline})
        if draw:
            self.add_feed(rule, "outline", "", draw=draw, outline=outline)
        return outline

    def check_outline(self, check_id):
        """For the Why view. An event that drew nothing has no outline yet."""
        check = self.evidence.check(check_id)
        if not check:
            return None
        if check.get("outline"):
            return check["outline"]
        rule = self.entries.get(check["rule"])
        sensor = next((s for s in rule.sensors if s.id == check["sensor"]), None) if rule else None
        name = check.get("proof_frame")
        return self._outline(rule, sensor, name, check_id) if sensor and name else None

    def _facts(self, sensor, jpegs, view=None):
        started = time.perf_counter()
        try:
            answer, cost = self.client.check_facts(
                sensor.question, sensor.requires, jpegs, sensor.pattern == "moment", view=view)
        except ApiError as e:
            self.problems.call("Muse Spark", error=str(e)[:160])
            raise
        with self.lock:
            self.ask_calls += 1
            self.spark_usd += cost
        answer["seconds"] = round(time.perf_counter() - started, 3)
        self.problems.call("Muse Spark", answer["seconds"])
        return answer

    def _focused(self, rule, frames, plan):
        """The frames as the check's model sees them, and kept for the Why view. On any trouble the
        model sees the whole wall, as before."""
        if plan is None:
            return frames
        try:
            pictures = [self.focus_picture(rule, frame.name, plan) for frame in frames]
        except (OSError, ValueError):
            return frames
        if not all(pictures):
            return frames
        for frame, jpeg in zip(frames, pictures):
            self.evidence.save_focus(frame.name, jpeg)
        return [frame._replace(jpeg=jpeg) for frame, jpeg in zip(frames, pictures)]

    def _detect(self, sensor, frame, rule=None):
        """SAM checks a solid thing in one frame. A thing in a camera that the Whenever does not watch
        does not count, such as a person in the driveway for a rule about the gate."""
        started = time.perf_counter()
        with self.lock:
            self.see_calls += 1
        try:
            boxes = self.client.see(sensor.target, frame.jpeg)
        except ApiError as e:
            self.problems.call("SAM 3.1", error=str(e)[:160])
            raise
        self.problems.call("SAM 3.1", round(time.perf_counter() - started, 3))
        if rule is not None:
            boxes = [b for b in boxes if self.in_focus(rule, b, frame.t)]
        confidences = [b["c"] for b in boxes if b["c"] is not None]
        hit = len(boxes) >= sensor.min_count
        strong = hit and bool(confidences) and max(confidences) >= STRONG_HIT
        return hit, strong, {
            "boxes": boxes,
            "conf": max(confidences) if confidences else None,
            "sam_s": round(time.perf_counter() - started, 3),
            "frames": [frame.name],
        }

    def _judge(self, sensor, frames, view=None):
        """Spark judges chosen frames. It handles actions and things with no clear shape.

        A new moment needs a second yes on the same frames, because a moment can last for one
        frame. A state instead needs the next check to say yes too, on new frames (see engine.py):
        the same frames asked twice repeat the same mistake.
        """
        jpegs = [frame.jpeg for frame in frames]
        first = self._facts(sensor, jpegs, view)
        answers = [first]
        if first["seen"] and sensor.pattern == "moment":
            answers.append(self._facts(sensor, jpegs, view))
        hit = all(a["seen"] for a in answers)
        return hit, hit, {
            "answer": hit,
            "answers": answers,
            "spark_frames": len(frames),
            "spark_s": round(sum(a["seconds"] for a in answers), 3),
            "frames": [frame.name for frame in frames],
        }

    def _watch(self, rule, sensor, t, result):
        """Queues a review if one is due, and returns at once. Detection never waits for analysis.

        A sleeping sensor waits for something that one frame must prove, such as a clear face.
        Several frames can say yes to that while each frame alone says no, so it gets no review.
        A moment that is under way is already one occurrence. A check that failed counts as a
        near miss, because a review can read the frames that it lost.
        """
        if sensor.target:
            return
        answers = (result or {}).get("answers", [])
        with sensor.lock:
            # A first yes waits for the next check, which reads new frames. If that one says no,
            # the lone yes was a near miss, and a review reads the frames around it.
            waiting = sensor.pattern == "state" and not sensor.state.on and sensor.state.streak == 1
            lone = sensor.lone_yes and not any(a["seen"] for a in answers)
            sensor.lone_yes = waiting
        if waiting:
            return
        near = lone or result is None or any(a["seen"] or a.get("part_seen") for a in answers)
        with sensor.lock:
            if (sensor.reviewing or rule.paused or sensor.armed_until is not None or sensor.state.in_moment
                    or t < sensor.review_after or not (near or sensor.state.on)):
                return
            if t - sensor.review_after > REVIEW_MAX_WAIT_S:
                sensor.review_wait = REVIEW_EVERY_S
            sensor.reviewing = True
        self._later(self._review, rule, sensor, t)

    def _review(self, rule, sensor, t):
        try:
            self._read_recent(rule, sensor, t)
        finally:
            sensor.reviewing = False

    def _read_recent(self, rule, sensor, t):
        """Spark watches the recent past, for an action that no single frame proves.

        It reads saved frames, so it can start late and still see everything. While a state is
        on, this also finds a second person who starts straight after the first one.
        """
        moment = sensor.pattern == "moment"
        span = MOMENT_REVIEW_SPAN_S if moment else REVIEW_SPAN_S
        # The rolling store can remove the oldest pictures of a long run.
        frames = [f for f in self._between(max(t - span, sensor.review_from), t) if self.evidence.frame_path(
            self.evidence.session, f.name)]
        frames = spread(frames, REVIEW_FRAMES)
        # A review of a few seconds proves little, so it waits for more frames and costs nothing.
        if not frames or frames[-1].t - frames[0].t < (MOMENT_MIN_REVIEW_SPAN_S if moment else MIN_REVIEW_SPAN_S):
            return
        if self.stopped():
            return
        started = time.perf_counter()
        try:
            review, cost = self.client.review(
                sensor.question, sensor.requires, [self.evidence.read_frame(f.name) for f in frames],
                [t - f.t for f in frames])
        except (ApiError, KeyError, ValueError):
            return
        with self.lock:
            self.ask_calls += 1
            self.spark_usd += cost
        sure = review["confidence"] >= REVIEW_PASS
        weak = review["passed"] and not sure and review["confidence"] >= REVIEW_WEAK
        # A review that would start a state of a pointed thing is compared with the moment before it too.
        # Headlights behind a closed gate once read as an opening in a review of whole-wall frames.
        if review["passed"] and (sure or (weak and sensor.weak_yes)) and not sensor.state.on:
            doers = [d for d in review["doers"] if 1 <= d["best_frame"] <= len(frames)]
            best = frames[doers[0]["best_frame"] - 1] if doers else frames[-1]
            light, answer = self.only_light(rule, sensor, frames[0], best)
            if light:
                review = {**review, "passed": False, "light": answer}
        with sensor.lock:
            passed = review["passed"] and (sure or (weak and sensor.weak_yes)) and not rule.paused
            sensor.weak_yes = weak and not passed
            sensor.review_after = t + (sensor.watch_s if sensor.state.on or passed else sensor.review_wait)
            # Only a sure "no" slows down. An unsure answer means that the evidence is still growing.
            sensor.review_wait = (min(sensor.review_wait * 2, REVIEW_MAX_WAIT_S)
                                  if sure and not review["passed"] else REVIEW_EVERY_S)
        record = {
            "t": t, "rule": rule.id, "rule_name": rule.name, "sensor": sensor.id, "target": "",
            "question": sensor.question, "requires": sensor.requires, "pattern": sensor.pattern,
            "frames_covered": len(frames), "min_motion": sensor.min_motion, "hit": passed, "answer": passed,
            "frames": [f.name for f in frames],
            "review": {**review, "passed": passed, "span_s": round(t - frames[0].t),
                       "seconds": round(time.perf_counter() - started, 3)},
        }
        if not passed:
            self.evidence.save_check({**record, "event": None, "proof_frame": frames[-1].name})
            return
        doers = [d for d in review["doers"] if 1 <= d["best_frame"] <= len(frames)]
        for doer in doers or [{"who": "", "best_frame": len(frames), "last_frame": len(frames)}]:
            newest = doer.get("last_frame", 0)
            until = frames[newest - 1].t if 1 <= newest <= len(frames) else None
            self._meet(rule, sensor, frames[doer["best_frame"] - 1], until, doer["who"], record)

    def _is_new(self, sensor, known, frame):
        """Only a sure "different" counts: a wrong split repeats an alert, and a wrong merge loses one."""
        jpeg = self.evidence.read_frame(frame.name)
        for name in known:
            try:
                answer, cost = self.client.same_subject(sensor.question, self.evidence.read_frame(name), jpeg)
            except (ApiError, KeyError, ValueError):
                return False
            with self.lock:
                self.ask_calls += 1
                self.spark_usd += cost
            if answer["same"] or answer["confidence"] < SUBJECT_PASS:
                return False
        return True

    def _meet(self, rule, sensor, frame, until, who, record):
        """One proof from a review. A new person gets a new event, and a known one keeps theirs on."""
        with sensor.lock:
            known = list(sensor.subjects)
        # A state of a pointed thing, such as an open gate, is about the thing. A second person who walks
        # through the open gate does not make it another opening, so only an action by a person splits.
        about_thing = bool((getattr(rule, "pointed", None) or {}).get("noun")) and not sensor.target
        stranger = (bool(known) and sensor.pattern == "state" and not about_thing
                    and self._is_new(sensor, known, frame))
        with sensor.lock:
            # The occurrence can end while a review runs. Its old frames must not fire it again.
            if frame.t < sensor.review_from or rule.paused:
                return
            started_at = sensor.state.on_since
            if stranger and sensor.state.on:
                event = sensor.state.next_subject(frame.t, until)
            else:
                event = sensor.state.late_hit(frame.t, until)
        result = {**record, "who": who}
        check = self.evidence.save_check({**result, "t": frame.t, "event": event, "proof_frame": frame.name})
        if event:
            self._act(rule, sensor, check, frame.t, result, frame.name, event, started_at)
            self._publish({"rule": rule.id, "sensor": sensor.id, "check": check, "hit": True, "event": event,
                           "frames_covered": len(record["frames"]), "frames": record["frames"]})

    def _due(self, sensor, t):
        """Returns all frames since this sensor's last check, or None if no check is due."""
        if not sensor.awake(t):
            return None
        with sensor.lock:
            if sensor.running >= MAX_SENSOR_CHECKS or t - sensor.last_check < sensor.check_every_s:
                return None
            with self.lock:
                frames = [f for f in self.frames if f.t > sensor.covered_until and f.t <= t]
            if not frames:
                return None
            sensor.last_check = t
            sensor.covered_until = frames[-1].t
            # A moment needs movement, so a still picture costs no model call.
            if sensor.pattern == "moment" and max(f.motion for f in frames) < sensor.min_motion:
                return None
            sensor.running += 1
            turn = sensor.next_turn
            sensor.next_turn += 1
            return frames, turn

    def _check(self, rule, sensor, frames, turn):
        # One burst can span two checks. Its peak gives both checks nearly the same time,
        # so clear_after_s treats them as one occurrence.
        t = max(frames, key=lambda f: f.motion).t if sensor.pattern == "moment" else frames[-1].t
        base = {"rule": rule.id, "sensor": sensor.id}
        try:
            if sensor.target:
                outcome = self._detect(sensor, pick_frames(frames, 1, sensor.pattern)[0], rule)
            else:
                picked = pick_frames(frames, sensor.frames_per_check, sensor.pattern)
                places = self.place_now(rule, sensor, picked[-1])
                held, scores = self.held_answer(sensor, places, picked[-1].t)
                if held is not None:
                    # The watched places look as they did at the last paid check: the same answer, at no cost.
                    outcome = (held[0], held[1], {"answer": held[0], "held": True, "unchanged": True,
                                                  "places": [round(v, 3) for v in scores],
                                                  "frames": [f.name for f in picked]})
                else:
                    plan = self.focus_plan(rule, picked[-1].t)
                    focused = self._focused(rule, picked, plan)
                    outcome = self._judge(sensor, focused, self.focus_view(rule, plan) if focused is not picked else None)
                    if focused is not picked:
                        outcome[2].update(focus=True, left_out=plan[1])
                    outcome = self._light_veto(rule, sensor, picked, outcome)
                    if scores:
                        outcome[2]["places"] = [round(v, 3) for v in scores]
                    if places is not None:
                        sensor.quiet = {"places": places, "t": picked[-1].t, "hit": outcome[0], "strong": outcome[1]}
            error = None
            outcome[2]["peak_motion"] = max(frame.motion for frame in frames)
        except ApiError as e:
            outcome, error = None, str(e)[:160]
        self._check_health(rule, sensor, error)
        with sensor.lock:
            sensor.running -= 1
            sensor.finished[turn] = (t, len(frames), outcome, error)
        return self._settle(rule, sensor, base)

    def _check_health(self, rule, sensor, error):
        """A missed check is a note: the next check looks again. Misses without a break become a warning."""
        model = "SAM 3.1" if sensor.target else "Muse Spark"
        key = f"stuck:{rule.id}:{sensor.id}"
        now = time.time()
        if not error:
            if sensor.failing_since is not None:
                sensor.failing_since = None
                self.problems.resolve(key)
            return
        self.problems.add(f"miss:{rule.id}:{error}", f"{model} missed a check for {rule.name}", "note", model,
                          error, "The next check looks again, so one miss changes nothing.", rule)
        if sensor.failing_since is None:
            sensor.failing_since = now
        elif now - sensor.failing_since >= STUCK_S:
            since = datetime.fromtimestamp(sensor.failing_since, ZONE).strftime("%H:%M:%S")
            self.problems.add(key, f"{rule.name} cannot check right now", "warn", model,
                              f"Every live check failed since {since}. The last one said: {error}",
                              "Whenever keeps trying. This is usually slowness at Meta, and it passes.", rule)

    def _settle(self, rule, sensor, base):
        """Applies finished checks in time order. A later check can finish first."""
        results = []
        while True:
            with sensor.lock:
                ready = sensor.finished.pop(sensor.settled_turn, None)
                if ready is None:
                    return results
                sensor.settled_turn += 1
                t, covered, outcome, error = ready
                if outcome is None or rule.paused:
                    event = None
                else:
                    started_at = sensor.state.on_since
                    event = sensor.state.observe(t, outcome[0], outcome[1], sensor.reviewing)
            if outcome is None:
                results.append(self._publish({**base, "skipped": True, "error": error}))
            else:
                results.append(self._record(rule, sensor, base, t, covered, outcome, event, started_at))
            self._watch(rule, sensor, t, outcome[2] if outcome else None)

    def _record(self, rule, sensor, base, t, covered, outcome, event, started_at):
        hit, strong, result = outcome
        proof = proof_frame(result)
        check = self.evidence.save_check({
            "t": t, "rule": rule.id, "rule_name": rule.name, "sensor": sensor.id,
            "target": sensor.target, "question": sensor.question, "requires": sensor.requires,
            "pattern": sensor.pattern, "frames_covered": covered, "min_motion": sensor.min_motion,
            "hit": hit, "strong": strong, "event": event, "proof_frame": proof, **result,
        })
        self._act(rule, sensor, check, t, result, proof, event, started_at)
        return self._publish({
            **base, "check": check, "hit": hit, "event": event, "frames_covered": covered, **result,
        })

    def _act(self, rule, sensor, check, t, result, proof, event, started_at):
        """Tells the rule's code. It runs each event in its own job, so this never waits for one."""
        # A sensor that a job armed adds steps to that job's occurrence. Any other event starts its own.
        parent = sensor.armed_by if sensor.armed_until is not None else None
        ending = {"occurrence": parent or sensor.occurrence, **({"step": True} if parent else {})}
        if event == "next":
            rule.send({"type": "off", "sensor": sensor.id, "t": t, "duration": t - started_at})
        if event == "next":
            self.journal.add("end", rule.id, rule.name, sensor.id, seconds=t - started_at, t=t, values=ending)
        if event in ("on", "moment", "next"):
            with sensor.lock:
                sensor.subjects = [*sensor.subjects, proof][-MAX_SUBJECTS:]
                sensor.review_after = max(sensor.review_after, t + sensor.watch_s)
            detail = self._event_detail(rule, check, result, proof)
            if not parent:
                rule.fired = getattr(rule, "fired", 0) + 1
                rule.last_fired = t
            shared = next((f["path"] for f in detail["frames"] if f["name"] == proof and f["path"]), None)
            values = {"check": check, **({"picture": f"/rule-file/{rule.id}/{shared}"} if shared else {})}
            tile = self._event_tile(rule, (detail.get("boxes") or [None])[0])
            rule.last_event = {"t": t, "tile": tile, "frame": proof}
            if parent:
                self.parents[check] = parent
                values.update(occurrence=parent, step=True)
            else:
                sensor.occurrence = check
                values["occurrence"] = check
                with self.lock:
                    self.occ_events[check] = {"rule": rule.id, "t": t, "tile": tile, "frame": proof}
                    while len(self.occ_events) > MAX_THREADS:
                        self.occ_events.popitem(last=False)
            if tile is not None:
                values.update(tile=tile, wall=list(self.wall), camera=self.cameras[tile] if tile < len(self.cameras) else "")
            if sensor.target:
                values["what"] = sensor.target
            self.journal.add("event", rule.id, rule.name, sensor.id, text=detail["note"], values=values, t=t)
            rule.send({"type": "on", "sensor": sensor.id, "t": t, "event": detail})
        if event == "moment":
            self.journal.add("end", rule.id, rule.name, sensor.id, seconds=0.0, t=t, values=ending)
            rule.send({"type": "off", "sensor": sensor.id, "t": t, "duration": 0.0})
        elif event == "off":
            self.journal.add("end", rule.id, rule.name, sensor.id, seconds=t - started_at, t=t, values=ending)
            rule.send({"type": "off", "sensor": sensor.id, "t": t, "duration": t - started_at})
        if event in ("off", "moment"):
            with sensor.lock:
                sensor.subjects = []
                sensor.review_from = t
                sensor.review_after = 0.0 if event == "off" else sensor.review_after

    def _publish(self, result):
        with self.lock:
            self.result_id += 1
            result = {**result, "id": self.result_id}
            self.results.append(result)
        return result

    def camera_live(self):
        return time.time() - self.live_at < LIVE_LOST_S

    def claim(self, page, cameras=None, controls=(), wall=(1, 1), streams=()):
        """The newest page owns the camera, so two tabs cannot mix frames in one check.

        A security camera keeps it while it runs, so a page that opens or reloads only watches.
        """
        if cameras:
            # The camera reader claims once each time it starts. Many starts mean that it keeps failing.
            self.camera_starts.append(time.time())
        if self.camera_live() and not str(page).startswith(CAMERA_PAGE_PREFIX):
            return
        # A camera that claims again was added back, such as a phone that scanned a new code.
        for name in cameras or []:
            self.camera_kinds.remove(name, False)
        self.active_page = page
        self.cameras = list(cameras or [])
        # Each camera's path on the media server, where the page plays it and clips come from.
        self.streams = {name: path for name, path in zip(self.cameras, streams or ()) if path}
        self.wall = tuple(wall)
        self.controls.set_cameras(controls)

    def show_live(self, jpeg, page):
        """Keeps the newest picture for the page. This is the view, and no model reads it."""
        if page != self.active_page:
            return False
        with self.live:
            self.live_jpeg = jpeg
            self.live_seq += 1
            self.live_at = time.time()
            self.live.notify_all()
        return True

    def alive(self, page):
        """The security cameras run, though no live picture came: the page plays them from the media server."""
        if page == self.active_page:
            self.live_at = time.time()
            return True
        return False

    def read_live(self, after):
        """Waits for a picture newer than after. Returns (seq, jpeg), or None."""
        with self.live:
            self.live.wait_for(lambda: self.live_seq > after, LIVE_WAIT_S)
            return (self.live_seq, self.live_jpeg) if self.live_seq > after else None

    def frame_full(self, name, camera, jpeg, page=None):
        """Keeps one camera's own frame. Zoom cuts from this, because a wall has fewer pixels."""
        if page != self.active_page:
            return False
        return self.evidence.save_full(name, camera, jpeg)

    def frame_sharp(self, name, jpeg, page=None):
        """Keeps the larger copy of a frame that the server already has."""
        if page != self.active_page:
            return False
        return self.evidence.save_sharp(name, jpeg)

    def frame(self, jpeg, t, page=None, motion=0.0, turning=()):
        """Stores one frame and starts any checks that are due.

        Frames never wait for a model, so a slow check cannot slow the camera.
        """
        if page != self.active_page:
            return {"results": [], "inactive": True}
        if self.stopped():
            return {"results": [], "stopped": True}
        self.last_frame = time.time()
        for tile in turning:
            self.turned_tiles[tile] = max(t, self.turned_tiles.get(tile, t))
        name = self.evidence.save_frame(t, jpeg, motion)
        with self.lock:
            self.frames.append(Frame(t, jpeg, name, motion))
        if name:
            self.moments.offer({"t": t, "name": name, "session": self.evidence.session})
        with self.lock:
            if name:
                self.timeline.append(Frame(t, None, name, motion))
            pairs = [(rule, sensor) for rule in self.entries.values() if not rule.paused for sensor in rule.sensors]
        results = []
        for rule, sensor in pairs:
            due = self._due(sensor, t)
            if not due:
                continue
            if self.inline:
                results.extend(self._check(rule, sensor, *due))
            else:
                self.detection.submit(self._check, rule, sensor, *due)
        return {"results": results, "stopped": self.stopped(), "name": name}


def load_recording(folder):
    """Returns what save_recording kept: (example, idle, motions, gap_s)."""
    def frames(label):
        return [file.read_bytes() for file in sorted((folder / "recording" / label).glob("*.jpg"))]
    timing = folder / "recording" / "timing.json"
    saved = json.loads(timing.read_text()) if timing.exists() else {}
    return frames("example"), frames("idle"), saved.get("motions"), saved.get("gap_s")


def save_recording(folder, example, idle, motions=None, gap_s=None):
    """Keeps a person's recording with the rule, for later tests."""
    for label, frames in (("example", example), ("idle", idle)):
        if frames:
            target = folder / "recording" / label
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            for index, jpeg in enumerate(frames):
                (target / f"{index:03d}.jpg").write_bytes(jpeg)
    if example:
        (folder / "recording" / "timing.json").write_text(json.dumps({"motions": motions, "gap_s": gap_s}))


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Whenever</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <main class="screens">
    <div class="start">
      <p class="brand-line"><span class="brand-mark" aria-hidden="true"></span>Whenever</p>
      <h1 class="start-title">Whenever it happens, do whatever you want.</h1>
      <p class="lede">This is the live demo of Whenever. It spends real model credit, so it needs the access code from the submission.</p>
      <form id="login" class="builder">
        <label class="connector" for="code">Access code</label>
        <div class="slot-text"><input id="code" type="password" autocomplete="current-password" required></div>
        <div class="form-row"><button type="submit" class="pill-button big">Open Whenever</button></div>
        <p id="note" class="note" role="status"></p>
      </form>
    </div>
  </main>
  <script>
    document.getElementById("login").addEventListener("submit", async (event) => {
      event.preventDefault();
      const response = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: document.getElementById("code").value }),
      });
      if (response.ok) location.reload();
      else {
        const note = document.getElementById("note");
        note.className = "note error";
        note.textContent = (await response.json()).error || "That code did not work.";
      }
    });
  </script>
</body>
</html>
"""


class Access:
    """The access code of a hosted server, and the sessions of the people who gave it."""

    def __init__(self, path=ACCESS_FILE, sessions_path=SESSIONS_FILE):
        try:
            self.code = Path(path).read_text().strip()
        except OSError:
            self.code = ""
        self.sessions_path = Path(sessions_path)
        self.lock = threading.Lock()
        self.failures = deque()
        try:
            self.sessions = {k: float(v) for k, v in json.loads(self.sessions_path.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            self.sessions = {}

    def needed(self):
        return bool(self.code)

    @staticmethod
    def _hash(token):
        # Only a hash is saved, so the sessions file cannot log anyone in.
        return hashlib.sha256(token.encode()).hexdigest()

    def valid(self, token):
        return self.sessions.get(self._hash(str(token)), 0) > time.time()

    def login(self, code):
        """Returns a new session token, or None. Wrong codes are limited, so guessing is slow."""
        now = time.time()
        with self.lock:
            while self.failures and now - self.failures[0] > 60:
                self.failures.popleft()
            if len(self.failures) >= MAX_LOGINS_PER_MINUTE:
                raise ValueError("Too many tries. Wait one minute.")
            if not self.code or not hmac.compare_digest(str(code).encode(), self.code.encode()):
                self.failures.append(now)
                return None
            return self._new_session(now)

    def grant(self):
        """A session with no code: a phone that a logged-in page paired gets one."""
        with self.lock:
            return self._new_session(time.time())

    def _new_session(self, now):
        token = secrets.token_urlsafe(32)
        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
        self.sessions[self._hash(token)] = now + SESSION_S
        self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_path.write_text(json.dumps(self.sessions))
        return token


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# The live view's redirects go back to the page, so this server does not follow them itself.
STAY = urllib.request.build_opener(_Stay)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app = None
    access = Access(path="", sessions_path=os.devnull)

    def log_message(self, fmt, *args):
        if "/api/" not in str(args[0]):
            super().log_message(fmt, *args)

    def _send(self, status, body, content_type, headers=()):
        # A body that a route did not read would become the start of the next request on this connection.
        if int(self.headers.get("Content-Length") or 0) and not getattr(self, "body_read", False):
            self.close_connection = True
        self.send_response(status)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _live_stream(self):
        """The live view as one stream of pictures, so no picture waits for a request of its own.

        A slow viewer gets the newest picture each time and skips the rest, so it never falls behind.
        """
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        seq = 0
        ends = time.time() + LIVE_STREAM_S
        with self.app.lock:
            self.app.live_viewers += 1
        try:
            while time.time() < ends:
                picture = self.app.read_live(seq)
                if not picture:
                    continue
                seq, jpeg = picture
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Seq: %d\r\n\r\n"
                                 % (len(jpeg), seq) + jpeg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            with self.app.lock:
                self.app.live_viewers -= 1

    def _hls(self, rest, query):
        """The media server's live view, for a logged-in page. The media server listens only on this computer."""
        if not HLS_URL or ".." in rest:
            return self._json(404, {"error": "Not found."})
        headers = {k: v for k, v in (("Cookie", self.headers.get("Cookie")), ("Range", self.headers.get("Range"))) if v}
        request = urllib.request.Request(f"{HLS_URL}/{rest}" + (f"?{query}" if query else ""), headers=headers)
        try:
            with STAY.open(request, timeout=20) as reply:
                status, body, found = reply.status, reply.read(), reply.headers
        except urllib.error.HTTPError as e:
            status, body, found = e.code, e.read() if e.fp else b"", e.headers
        except (urllib.error.URLError, OSError):
            return self._json(502, {"error": "The live video is not ready."})
        extra = []
        location = found.get("Location")
        if location:
            if "://" in location:
                location = "/" + location.split("://", 1)[1].split("/", 1)[-1]
            extra.append(("Location", "/hls" + location if location.startswith("/") else location))
        for cookie in found.get_all("Set-Cookie") or []:
            extra.append(("Set-Cookie", re.sub(r"(?i)path=/", "Path=/hls/", cookie)))
        try:
            self._send(status, body, found.get("Content-Type", "application/octet-stream"), extra)
        except (BrokenPipeError, ConnectionResetError):
            # The player moved on to the next part, or the page closed. Nothing is lost.
            self.close_connection = True

    def _send_video(self, data):
        """A clip, in parts when the browser asks for a range. Safari plays a video only that way."""
        found = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if not found or not (found.group(1) or found.group(2)):
            return self._send(200, data, "video/mp4", [("Accept-Ranges", "bytes")])
        if found.group(1):
            start = int(found.group(1))
            end = min(int(found.group(2)) if found.group(2) else len(data) - 1, len(data) - 1)
        else:
            start, end = max(0, len(data) - int(found.group(2))), len(data) - 1
        if start > end:
            return self._send(416, b"", "video/mp4", [("Content-Range", f"bytes */{len(data)}")])
        self._send(206, data[start:end + 1], "video/mp4",
                   [("Accept-Ranges", "bytes"), ("Content-Range", f"bytes {start}-{end}/{len(data)}")])

    def _json(self, status, value):
        self._send(status, json.dumps(value).encode(), "application/json")

    def _allowed(self):
        # Only this page may use the server, so another website cannot trigger actions or spend.
        local = (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        host = self.headers.get("Host")
        if host in local:
            origins = tuple(f"http://{h}" for h in local)
        elif host in HOSTS:
            origins = (f"https://{host}",)
        else:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in origins

    def _logged_in(self):
        if not self.access.needed():
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "").get("whenever")
        return bool(cookie) and self.access.valid(cookie.value)

    def _login(self):
        raw = self._read(MAX_LABEL_BYTES)
        try:
            token = self.access.login(json.loads(raw or b"{}").get("code") or "")
        except ValueError as e:
            return self._json(429, {"error": str(e)})
        if not token:
            return self._json(403, {"error": "That code did not work."})
        secure = "; Secure" if self.headers.get("Host") in HOSTS else ""
        cookie = f"whenever={token}; Path=/; Max-Age={SESSION_S}; HttpOnly; SameSite=Strict{secure}"
        self._send(200, b'{"ok": true}', "application/json", [("Set-Cookie", cookie)])

    def _pair_info(self, token):
        """What the pairing page on a phone needs. It names no place and no key except the public one."""
        found = self.app.channels.pairing(token)
        if not found:
            return {"valid": False, "logged_in": self._logged_in()}
        push_key = ""
        if found["kind"] == "push":
            push_key = self.app.channels.push_public_key()
        return {"valid": not found["expired"] and not found["done"], "done": found["done"], "kind": found["kind"],
                "push_key": push_key, "logged_in": self._logged_in(), "address": found.get("address") or ""}

    def _camera_link(self, token):
        """A phone scanned the camera QR code. It logs in and opens camera mode, where it sends its picture."""
        try:
            item = self.app.channels.claim_camera(token)
        except ChannelError:
            # The pairing page says that the link is old, and how to make a new one.
            return self._send(302, b"", "text/plain", [("Location", f"/pair/{token}")])
        headers = [("Location", "/#/phone-camera")]
        if item["login"] and self.access.needed() and not self._logged_in():
            secure = "; Secure" if self.headers.get("Host") in HOSTS else ""
            headers.append(("Set-Cookie", f"whenever={self.access.grant()}; Path=/; Max-Age={SESSION_S}; HttpOnly; SameSite=Lax{secure}"))
        self._send(302, b"", "text/plain", headers)

    def _pair_finish(self, token, kind):
        """The phone subscribed, or the person tapped the button in the email."""
        try:
            body = json.loads(self._read(MAX_CLAIM_BYTES) or b"{}")
        except ValueError:
            return self._json(400, {"error": "Bad request."})
        found = self.app.channels.pairing(token)
        try:
            if kind == "push":
                place = self.app.channels.add_push(body.get("subscription"), body.get("label"), token)
                self.app.send_alert(None, "Your phone is paired. Whenever alerts will look like this.", "Whenever",
                                    only=place["id"])
            else:
                place = self.app.channels.confirm_email(token)
        except ChannelError as e:
            return self._json(400, {"error": str(e)})
        headers = []
        if kind == "push" and found and found["login"] and self.access.needed() and not self._logged_in():
            secure = "; Secure" if self.headers.get("Host") in HOSTS else ""
            session = self.access.grant()
            headers.append(("Set-Cookie", f"whenever={session}; Path=/; Max-Age={SESSION_S}; HttpOnly; SameSite=Lax{secure}"))
        self._send(200, json.dumps({"ok": True, "label": place.get("label") or place.get("address")}).encode(),
                   "application/json", headers)

    def _read(self, limit):
        self.body_read = True
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def do_GET(self):
        if not self._allowed():
            return self._json(403, {"error": "Forbidden."})
        path, _, query = self.path.partition("?")
        parts = path.split("/")
        if path == "/manifest.webmanifest":
            return self._send(200, manifest().encode(), "application/manifest+json")
        if len(parts) in (3, 4) and parts[1] == "pair" and PAIR_TOKEN.fullmatch(parts[2]):
            if len(parts) == 4 and parts[3] == "manifest.webmanifest":
                return self._send(200, manifest(f"/pair/{parts[2]}").encode(), "application/manifest+json")
            page = (ROOT / "web" / "pair.html").read_text().replace("__TOKEN__", parts[2])
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        if path == "/connect.sh":
            # One command connects a home's cameras. It holds no secret: the setup asks for the access code.
            host = self.headers.get("Host")
            server = f"https://{host}" if host in HOSTS else self.app.base_url()
            script = (ROOT / "web" / "connect.sh").read_text().replace("__SERVER__", server)
            return self._send(200, script.encode(), "text/x-shellscript; charset=utf-8")
        if len(parts) == 3 and parts[1] == "connect" and parts[2] in ("camera.py", "camera_sources.py"):
            return self._send(200, (ROOT / parts[2]).read_bytes(), "text/x-python; charset=utf-8")
        if len(parts) == 3 and parts[1] == "camera" and PAIR_TOKEN.fullmatch(parts[2]):
            return self._camera_link(parts[2])
        if len(parts) == 5 and parts[1:3] == ["api", "pair"] and parts[4] == "info":
            return self._json(200, self._pair_info(parts[3]))
        if len(parts) == 3 and parts[1] == "alert-picture":
            picture = self.app.channels.picture(parts[2])
            if not picture:
                return self._json(404, {"error": "Not found."})
            return self._send(200, picture, "image/jpeg")
        if not self._logged_in():
            if path in ("/", "/index.html"):
                return self._send(200, LOGIN_PAGE.encode(), "text/html; charset=utf-8")
            if path not in OPEN_PATHS:
                return self._json(401, {"error": "Log in first."})
        if len(parts) == 4 and parts[1:3] == ["api", "pair"]:
            found = self.app.channels.pairing(parts[3])
            return self._json(200 if found else 404, found or {"error": "Not found."})
        if path == "/api/state":
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            return self._json(200, self.app.status(int(params.get("after", 0)), int(params.get("results", 0))))
        if path == "/api/live.mjpg":
            return self._live_stream()
        if path.startswith("/hls/"):
            return self._hls(path[len("/hls/"):], query)
        if path == "/api/live.jpg":
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            picture = self.app.read_live(int(params.get("after", 0)))
            if not picture:
                return self._send(204, b"", "image/jpeg")
            return self._send(200, picture[1], "image/jpeg", [("X-Seq", str(picture[0]))])
        if path == "/api/journal":
            params = {k: unquote(v) for k, v in (part.split("=", 1) for part in query.split("&") if "=" in part)}
            since = float(params["since"]) if params.get("since") else None
            until = float(params["until"]) if params.get("until") else None
            rows = self.app.journal.history(params.get("kind") or None, None, params.get("rule") or None,
                                            since, until, int(params.get("limit") or 300))
            return self._json(200, {"rows": rows})
        if path == "/api/problems":
            return self._json(200, self.app.problems.public())
        if path == "/api/people":
            return self._json(200, {"people": self.app.people.public(), "ready": people.available(),
                                    "model": people.FACE_MODEL})
        if path.startswith("/people-file/"):
            parts = unquote(path).split("/")
            folder = self.app.people.folder / parts[2] if len(parts) == 4 and parts[2].isalnum() else None
            file = rule_picture(folder, parts[3]) if folder and folder.is_dir() else None
            if file:
                return self._send(200, file.read_bytes(), IMAGE_TYPES[file.suffix.lower()])
            return self._json(404, {"error": "Not found."})
        if path.startswith("/api/job/"):
            job = self.app.jobs.get(path.rsplit("/", 1)[1])
            return self._json(200 if job else 404, job or {"error": "Not found."})
        if path.startswith("/api/check/"):
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            t = params.get("t", "")
            check = self.app.evidence.check(unquote(path.rsplit("/", 1)[1]),
                                            float(t) if t.replace(".", "", 1).isdigit() else None)
            return self._json(200 if check else 404, check or {"error": "Not found."})
        if path == "/api/asks":
            return self._json(200, {"asks": self.app.asks.history()})
        if path.startswith("/api/ask/"):
            found = self.app.asks.get(path.rsplit("/", 1)[1])
            return self._json(200 if found else 404, found or {"error": "Not found."})
        if path.startswith("/ask-file/"):
            parts = path.split("/")
            file = self.app.asks.file(parts[2], parts[3]) if len(parts) == 4 else None
            if not file:
                return self._json(404, {"error": "Not found."})
            if file.suffix == ".mp4":
                return self._send_video(file.read_bytes())
            return self._send(200, file.read_bytes(), "image/jpeg")
        if path.startswith("/rule-file/"):
            parts = unquote(path).split("/", 3)
            folder = sandbox.RULES_DIR / parts[2] if len(parts) == 4 and parts[2].isalnum() else None
            picture = rule_picture(folder, parts[3]) if folder else None
            if picture:
                return self._send(200, picture.read_bytes(), IMAGE_TYPES[picture.suffix.lower()])
            clip = rule_clip(folder, parts[3]) if folder else None
            if clip:
                return self._send_video(clip.read_bytes())
            return self._json(404, {"error": "Not found."})
        if path.startswith("/evidence/"):
            parts = path.split("/")
            frame = None
            if len(parts) == 4:
                params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                camera = params.get("camera", "")
                if params.get("view") == "focus":
                    frame = self.app.evidence.focus_path(parts[2], parts[3])
                elif parts[2] == self.app.evidence.session and camera.isdigit():
                    frame = self.app.evidence.full_path(parts[3], int(camera))
                frame = frame or self.app.evidence.sharp_path(parts[3], parts[2])
            if frame:
                return self._send(200, frame.read_bytes(), "image/jpeg")
            return self._json(404, {"error": "Not found."})
        if path.startswith("/api/rule/") and path.endswith("/code"):
            code = self.app.code(path.split("/")[3])
            if code is None:
                return self._json(404, {"error": "Not found."})
            return self._send(200, code.encode(), "text/plain; charset=utf-8")
        if path in STATIC:
            name, content_type = STATIC[path]
            file = ROOT / name
            if file.exists():
                return self._send(200, file.read_bytes(), content_type)
        self._json(404, {"error": "Not found."})

    def do_POST(self):
        self.body_read = False
        if not self._allowed():
            return self._json(403, {"error": "Forbidden."})
        if self.path == "/api/login":
            return self._login()
        parts = self.path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "pair"] and parts[4] in ("push", "email"):
            return self._pair_finish(parts[3], parts[4])
        if not self._logged_in():
            return self._json(401, {"error": "Log in first."})
        try:
            if self.path == "/api/frame":
                jpeg = self._read(MAX_FRAME_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                t = float(self.headers.get("X-Frame-Time", "0"))
                motion = float(self.headers.get("X-Motion", "0"))
                turning = [int(i) for i in self.headers.get("X-Turning", "").split(",") if i.strip().isdigit()]
                return self._json(200, self.app.frame(jpeg, t, self.headers.get("X-Page"), motion, turning))
            if self.path == "/api/alive":
                self._read(MAX_LABEL_BYTES)
                return self._json(200, {"alive": self.app.alive(self.headers.get("X-Page"))})
            if self.path == "/api/live":
                jpeg = self._read(MAX_SHARP_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                return self._json(200, {"shown": self.app.show_live(jpeg, self.headers.get("X-Page"))})
            if self.path.startswith("/api/frame/") and "/full/" in self.path:
                jpeg = self._read(MAX_FULL_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                parts = self.path.split("/")
                saved = self.app.frame_full(parts[3], int(parts[5]), jpeg, self.headers.get("X-Page"))
                return self._json(200, {"saved": saved})
            if self.path.startswith("/api/frame/") and self.path.endswith("/sharp"):
                jpeg = self._read(MAX_SHARP_BYTES)
                if jpeg is None:
                    return self._json(413, {"error": "Frame is too large."})
                saved = self.app.frame_sharp(self.path.split("/")[3], jpeg, self.headers.get("X-Page"))
                return self._json(200, {"saved": saved})
            if self.path == "/api/claim":
                claim = json.loads(self._read(MAX_CLAIM_BYTES))
                names = [str(name)[:40] for name in claim.get("cameras") or []][:MAX_CAMERAS]
                columns, rows = claim.get("wall") or (1, 1)
                wall = (int(number(columns, 1, MAX_CAMERAS, 1)), int(number(rows, 1, MAX_CAMERAS, 1)))
                streams = [str(path)[:40] for path in claim.get("streams") or []][:MAX_CAMERAS]
                makes = [str(make)[:20] for make in claim.get("makes") or []][:MAX_CAMERAS]
                self.app.claim(str(claim.get("page"))[:40], names, list(claim.get("controls") or []), wall, streams)
                self.app.camera_kinds.set_makes(dict(zip(names, makes)))
                return self._json(200, {"active": True})
            if self.path == "/api/people":
                body = json.loads(self._read(MAX_JSON_BYTES) or b"{}")
                try:
                    person = self.app.tag_from_page(body.get("picture"), body.get("name"), body.get("note") or "",
                                                    body.get("box"))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"person": person})
            if self.path.startswith("/api/people/"):
                body = json.loads(self._read(MAX_JSON_BYTES) or b"{}")
                try:
                    person = self.app.people.rename(self.path.split("/")[3], body.get("name"), body.get("note"))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"person": {k: person[k] for k in ("id", "name", "note")}})
            if self.path == "/api/camera":
                return self._json(200, self.app.camera_command(json.loads(self._read(MAX_LABEL_BYTES))))
            if self.path == "/api/rule" or (self.path.startswith("/api/rule/") and self.path.endswith("/edit")):
                edit = self.path.split("/")[3] if self.path != "/api/rule" else None
                raw = self._read(MAX_JSON_BYTES)
                if raw is None:
                    return self._json(413, {"error": "The recording is too large."})
                body = json.loads(raw)
                sentence = str(body.get("sentence") or "").strip()[:MAX_SENTENCE]
                example = [base64.b64decode(f) for f in body.get("example") or []]
                idle = [base64.b64decode(f) for f in body.get("idle") or []]
                if not sentence and not (example and not edit):
                    return self._json(400, {"error": "Write a sentence or record an example."})
                motions = [float(m) for m in body.get("motions") or []]
                gap_s = number(body.get("gap_s"), 0.02, 2, None)
                point = str(body.get("point") or "")[:20]
                return self._json(200, {"job": self.app.start_job(sentence, example, idle, motions, gap_s, point, edit)})
            if self.path == "/api/ask":
                raw = self._read(MAX_FRAME_BYTES * 2)
                if raw is None:
                    return self._json(413, {"error": "The picture is too large."})
                body = json.loads(raw)
                context = dict(body.get("context") or {})
                if body.get("snapshot"):
                    context["snapshot"] = base64.b64decode(body["snapshot"])
                try:
                    return self._json(200, {"id": self.app.asks.start(body.get("question"), context)})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
            if self.path.startswith("/api/ask/") and self.path.endswith("/go"):
                self._read(MAX_LABEL_BYTES)
                try:
                    return self._json(200, {"id": self.app.asks.go(self.path.split("/")[3])})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
            if self.path == "/api/point":
                raw = self._read(MAX_FRAME_BYTES * 2)
                if raw is None:
                    return self._json(413, {"error": "Frame is too large."})
                body = json.loads(raw)
                return self._json(200, self.app.point(
                    base64.b64decode(body["frame"]), int(body["x"]), int(body["y"]), int(body["w"]), int(body["h"])))
            if self.path.startswith("/api/point/") and self.path.endswith("/name"):
                self._read(MAX_LABEL_BYTES)
                point_id = self.path.split("/")[3]
                noun = self.app.name_point(point_id)
                crop = self.app.point_crop(point_id)
                return self._json(200, {"noun": noun, "crop": base64.b64encode(crop).decode() if crop else ""})
            if self.path.startswith("/api/point/") and self.path.endswith("/elsewhere"):
                self._read(MAX_LABEL_BYTES)
                also = self.app.find_elsewhere(self.path.split("/")[3])
                return self._json(200, {"also": [{"camera": a["camera"], "kind": a["kind"],
                                                  "crop": base64.b64encode(a["crop"]).decode()} for a in also]})
            if self.path.startswith("/api/point/") and self.path.endswith("/confirm"):
                body = json.loads(self._read(MAX_CLAIM_BYTES) or b"{}")
                try:
                    return self._json(200, self.app.confirm_point(self.path.split("/")[3], body.get("noun"),
                                                                  body.get("note"), body.get("cameras")))
                except KeyError:
                    return self._json(404, {"error": "That pointing is gone. Point again."})
                except ValueError as e:
                    return self._json(422, {"error": str(e)})
            if self.path.startswith("/api/rule/") and self.path.rsplit("/", 1)[1] in ("stop", "resume"):
                rule_id, action = self.path.split("/")[3:5]
                done = (self.app.pause_rule if action == "stop" else self.app.resume_rule)(rule_id)
                return self._json(200 if done else 404, {"done": done})
            if self.path.startswith("/api/check/") and self.path.endswith("/outline"):
                return self._json(200, {"outline": self.app.check_outline(self.path.split("/")[3])})
            if self.path == "/api/voice":
                return self._json(200, {"id": self.app.start_voice()})
            if self.path.startswith("/api/voice/") and self.path.endswith("/audio"):
                chunk = self._read(MAX_AUDIO_BYTES)
                if chunk is None:
                    return self._json(413, {"error": "Audio is too large."})
                return self._json(200, {"text": self.app.voice_audio(self.path.split("/")[3], chunk)})
            if self.path.startswith("/api/voice/") and self.path.endswith("/end"):
                return self._json(200, {"text": self.app.end_voice(self.path.split("/")[3])})
            if self.path == "/api/label":
                raw = self._read(MAX_LABEL_BYTES)
                body = json.loads(raw)
                self.app.evidence.label(str(body.get("check"))[:48], str(body.get("label"))[:40])
                return self._json(200, {"saved": True})
            if self.path == "/api/cameras/turned":
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                name = str(body.get("camera") or "")
                if name in self.app.cameras:
                    self.app.camera_kinds.turned(name, time.time())
                return self._json(200, {"kind": self.app.camera_kind(name) if name in self.app.cameras else ""})
            if self.path in ("/api/cameras/remove", "/api/cameras/restore"):
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                name = str(body.get("camera") or "")[:40]
                if not name or (self.path.endswith("remove") and name not in self.app.cameras):
                    return self._json(404, {"error": "There is no camera with that name."})
                self.app.camera_kinds.remove(name, self.path.endswith("remove"))
                return self._json(200, {"removed": self.app.camera_kinds.removed()})
            if self.path == "/api/cameras/kind":
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                name = str(body.get("camera") or "")
                if name not in self.app.cameras:
                    return self._json(404, {"error": "There is no camera with that name."})
                try:
                    self.app.camera_kinds.set(name, str(body.get("kind") or ""))
                except ValueError as e:
                    return self._json(422, {"error": str(e)})
                return self._json(200, {"kind": self.app.camera_kind(name)})
            if self.path == "/api/pair/start":
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                return self._json(200, self.app.start_pairing(body.get("kind"), self.access.needed(), self.headers.get("Host")))
            if self.path == "/api/pair/email":
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                base = self.app.base_url()
                token = self.app.channels.start_email(body.get("address"), lambda t: f"{base}/pair/{t}")
                return self._json(200, {"token": token})
            if self.path == "/api/telegram/key":
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                return self._json(200, {"bot": self.app.channels.set_telegram_token(body.get("key"))})
            if self.path == "/api/push/subscribe":
                body = json.loads(self._read(MAX_CLAIM_BYTES) or b"{}")
                place = self.app.channels.add_push(body.get("subscription"), body.get("label"))
                self.app.send_alert(None, "This browser is paired. Whenever alerts will look like this.", "Whenever",
                                    only=place["id"])
                return self._json(200, {"place": {k: place[k] for k in ("id", "label", "t")}})
            if self.path.startswith("/api/places/") and self.path.endswith("/name"):
                body = json.loads(self._read(MAX_LABEL_BYTES) or b"{}")
                try:
                    place = self.app.channels.rename(unquote(self.path.split("/")[3]), body.get("name"))
                except ChannelError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"place": place})
            if self.path.startswith("/api/places/") and self.path.endswith("/test"):
                self._read(MAX_LABEL_BYTES)
                place = unquote(self.path.split("/")[3])
                results = self.app.send_alert(None, "Test from Whenever. Alerts will look like this.", "Whenever",
                                              only=place)
                return self._json(200, {"ok": all(r["ok"] for r in results), "results": results})
            if self.path.startswith("/api/problems/") and self.path.endswith("/dismiss"):
                self._read(MAX_LABEL_BYTES)
                group = self.path.split("/")[3]
                return self._json(200, {"done": group.isdigit() and self.app.problems.dismiss(int(group))})
            if self.path == "/api/phone/topic":
                return self._json(200, {"topic": self.app.phone_topic()})
            if self.path == "/api/phone/paired":
                paired = json.loads(self._read(MAX_LABEL_BYTES) or b"{}").get("paired")
                return self._json(200, {"paired": self.app.set_paired(paired)})
            if self.path == "/api/understand":
                sentence = str(json.loads(self._read(MAX_SENTENCE * 4) or b"{}").get("sentence") or "").strip()
                if not sentence:
                    return self._json(400, {"error": "Write what should happen first."})
                return self._json(200, self.app.understand(sentence[:MAX_SENTENCE]))
            if self.path.startswith("/api/saved-action/") and self.path.endswith("/test"):
                name = unquote(self.path.split("/")[3])
                inputs = json.loads(self._read(MAX_LABEL_BYTES * 4) or b"{}").get("inputs") or {}
                return self._json(200, self.app.test_action(name, inputs))
            if self.path == "/api/action/draft":
                raw = self._read(MAX_ACTION_WORDS * 4)
                if raw is None:
                    return self._json(413, {"error": "The description is too long."})
                words = str(json.loads(raw).get("words") or "").strip()[:MAX_ACTION_WORDS]
                if not words:
                    return self._json(400, {"error": "Describe the action."})
                return self._json(200, self.app.draft_action(words))
            if self.path.startswith("/api/action/") and self.path.rsplit("/", 1)[1] in ("test", "save"):
                draft_id, step = self.path.split("/")[3:5]
                if step == "save":
                    return self._json(200, {"saved": self.app.save_draft(draft_id)["name"]})
                inputs = json.loads(self._read(MAX_LABEL_BYTES * 4) or b"{}").get("inputs") or {}
                return self._json(200, self.app.test_draft(draft_id, inputs))
            self._json(404, {"error": "Not found."})
        except (writer.WriterError, ControlError, ActionError, ChannelError) as e:
            self._json(422, {"error": str(e)})
        except (ApiError, VoiceError) as e:
            self._json(502, {"error": str(e)[:300]})
        except (KeyError, ValueError):
            self._json(400, {"error": "Bad request."})

    def do_DELETE(self):
        self.body_read = False
        if not self._allowed() or not self._logged_in():
            return self._json(403, {"error": "Forbidden."})
        if self.path.startswith("/api/rule/"):
            removed = self.app.remove_rule(self.path.rsplit("/", 1)[1])
            return self._json(200 if removed else 404, {"removed": removed})
        if self.path.startswith("/api/people/"):
            removed = self.app.people.remove(self.path.rsplit("/", 1)[1])
            return self._json(200 if removed else 404, {"removed": removed})
        if self.path.startswith("/api/action/"):
            removed = self.app.connectors.remove(unquote(self.path.rsplit("/", 1)[1]))
            return self._json(200 if removed else 404, {"removed": removed})
        if self.path.startswith("/api/places/"):
            place = unquote(self.path.rsplit("/", 1)[1])
            removed = self.app.channels.remove(place)
            if place == "ntfy" and removed:
                self.app.set_paired(False)
            return self._json(200 if removed else 404, {"removed": removed})
        self._json(404, {"error": "Not found."})


def main():
    if not ENV_FILE.exists() or not ENV_FILE.read_text().strip():
        sys.exit(f"Add your Meta API key to {ENV_FILE}")
    channels = Channels(CHANNELS_FILE, pictures_dir=PICTURES_DIR)
    Handler.app = App(Client(load_key(ENV_FILE)), journal=Journal(JOURNAL_FILE),
                      connectors=Connectors(ACTIONS_FILE, channels=channels), rules_file=RULES_FILE,
                      phone_file=PHONE_FILE, problems=Problems(PROBLEMS_FILE), camera_kinds=CameraKinds(CAMERAS_FILE))
    Handler.access = Access()
    if Handler.access.needed():
        print("The page asks for the access code in access.env.", flush=True)
    back = Handler.app.load_rules()
    if back:
        print(f"{back} rules are back after the restart.", flush=True)
    Handler.app.start_ticks()
    threading.Thread(target=Handler.app.telegram_loop, daemon=True).start()
    if Handler.app.moments.start():
        app = Handler.app
        print(f"The moment index runs: {app.moments.count()} moments.", flush=True)
        def backfill():
            # The footage of the last day joins the index slowly, so Ask can rank it too. It waits until the
            # camera reader says how its wall is laid out.
            for _ in range(120):
                if app.wall[0] * app.wall[1] > 1:
                    break
                time.sleep(1)
            app.moments.trim()
            app.moments.backfill(search.Footage(app.evidence.root, app.wall).frames(time.time() - 86400, time.time() - 60))
        threading.Thread(target=backfill, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Whenever is running at http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
