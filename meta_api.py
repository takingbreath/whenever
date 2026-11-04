"""Client for the Meta Model API.

Connections stay open between calls. In our test a new connection added about
0.6 s to each Spark check.
"""

import base64
import http.client
import json
import queue
import re
import ssl
import struct
import sys
import time
from pathlib import Path

# Meta's SAM parser lives here, because this Mac's Python does not allow global installs.
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from meta_sam_parser import (  # noqa: E402
    InvalidSegmentationMaskError, SegmentationMask, decode_mask_to_svg_path,
)

HOST = "api.meta.ai"
SPARK = "muse-spark-1.3"
SAM = "sam-3.1"
WRITER = "muse-wonder-1.0"

# A live check that takes this long is no use. One Spark call took 26 s in our tests.
CHECK_TIMEOUT_S = 8
# Spark's slowest tenth took 6 s or more on a slow day, so 8 s missed many checks that were fine.
LIVE_SPARK_TIMEOUT_S = 12
RETRY_STATUS = (429, 500, 502, 503)
RETRY_DELAYS_S = (2, 5, 10)

MAX_DOERS = 4
SPARK_INPUT_USD = 1.25 / 1_000_000
SPARK_OUTPUT_USD = 4.25 / 1_000_000
WONDER_INPUT_USD = 4.00 / 1_000_000
WONDER_CACHED_USD = 0.20 / 1_000_000
WONDER_OUTPUT_USD = 20.00 / 1_000_000

LOOK_TIMEOUT_S = {"spark": 20, "wonder": 60}

BOX = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>"
)

# The mask alphabet has no "|", so this cannot end early.
MASK = re.compile(r"<\|mask;x=0;y=0(?:;c=[\d.]+)?;data=(\d+),(\d+),([~!])([^|]*)\|>")


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def load_key(path):
    """The file may hold the bare key or a NAME=key line."""
    raw = Path(path).read_text().strip()
    return raw.split("=", 1)[1].strip() if "=" in raw else raw


def parse_boxes(text):
    boxes = []
    for m in BOX.finditer(text):
        x1, y1, x2, y2, w, h = (int(v) for v in m.groups()[:6])
        conf = float(m.group(7)) if m.group(7) else None
        boxes.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h, "c": conf})
    return boxes


def parse_segments(text):
    """Returns each object's box. path is its outline as SVG, in the mask's own pixels."""
    boxes = parse_boxes(text)
    for box, (rows, columns, marker, payload) in zip(boxes, MASK.findall(text)):
        encoding = "one_bit" if marker == "!" else "lossless"
        box["mask_w"], box["mask_h"] = int(columns), int(rows)
        try:
            mask = SegmentationMask(encoding, marker + payload, int(columns), int(rows))
            box["path"] = decode_mask_to_svg_path(mask)
        except InvalidSegmentationMaskError:
            box["path"] = ""
    return boxes


def jpeg_size(jpeg):
    """Reads (width, height) from a JPEG header. Returns None for anything else."""
    index = 2
    while index + 9 < len(jpeg):
        if jpeg[index] != 0xFF:
            index += 1
            continue
        marker = jpeg[index + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", jpeg[index + 5:index + 9])
            return width, height
        index += 2 + int.from_bytes(jpeg[index + 2:index + 4], "big")
    return None


def _sse_text(raw):
    text = ""
    for line in raw.decode("utf-8", "ignore").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if event.get("type") == "response.output_text.delta":
            text += event.get("delta", "")
    return text


def _output_text(data):
    return "".join(
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
    ).strip()


def wonder_cost(usage):
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    fresh = usage.get("input_tokens", 0) - cached
    return (
        fresh * WONDER_INPUT_USD
        + cached * WONDER_CACHED_USD
        + usage.get("output_tokens", 0) * WONDER_OUTPUT_USD
    )


def spark_cost(usage):
    return (
        usage.get("input_tokens", 0) * SPARK_INPUT_USD
        + usage.get("output_tokens", 0) * SPARK_OUTPUT_USD
    )


def _image_part(jpeg):
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return {"type": "input_image", "image_url": url}


class Client:
    def __init__(self, key, pool_size=6):
        self.key = key
        self._headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        self._ssl = ssl.create_default_context()
        # Each lane has its own connections, so a slow job can never hold up a live check.
        self._pools = {"live": queue.LifoQueue(), "analysis": queue.LifoQueue()}
        for pool in self._pools.values():
            for _ in range(pool_size):
                pool.put(None)

    def _new_connection(self):
        return http.client.HTTPSConnection(HOST, timeout=30, context=self._ssl)

    def _post(self, body, timeout=CHECK_TIMEOUT_S, retries=0, lane="analysis"):
        """retries is for slow background work. A live check must not wait."""
        for delay in RETRY_DELAYS_S[:retries]:
            try:
                return self._post_once(body, timeout, lane)
            except ApiError as e:
                if e.status not in RETRY_STATUS:
                    raise
                time.sleep(delay)
        return self._post_once(body, timeout, lane)

    def _post_once(self, body, timeout, lane):
        pool = self._pools[lane]
        conn = pool.get() or self._new_connection()
        try:
            for attempt in (1, 2):
                try:
                    if conn.sock is None:
                        conn.connect()
                    conn.sock.settimeout(timeout)
                    conn.request("POST", "/v1/responses", json.dumps(body), self._headers)
                    resp = conn.getresponse()
                    data = resp.read()
                    if resp.status != 200:
                        message = data.decode("utf-8", "ignore")[:300]
                        raise ApiError(f"Meta API status {resp.status}: {message}", resp.status)
                    return data
                except TimeoutError:
                    conn.close()
                    raise ApiError("Meta API did not answer in time.") from None
                except (http.client.HTTPException, ConnectionError, OSError):
                    # The server may close an idle connection. Try once on a new one.
                    conn.close()
                    conn = self._new_connection()
                    if attempt == 2:
                        raise ApiError("Meta API connection failed.") from None
        finally:
            pool.put(conn)

    def see(self, target, jpeg, lane="live"):
        """SAM 3.1: return the boxes for a short noun phrase. Rule code uses the analysis lane."""
        raw = self._post({
            "model": SAM,
            "stream": True,
            "metadata": {"include_confidence": "true"},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": target}, _image_part(jpeg)],
            }],
        }, lane=lane)
        return parse_boxes(_sse_text(raw))

    def _segment(self, prompt, jpeg):
        raw = self._post({
            "model": SAM,
            "stream": True,
            "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}, _image_part(jpeg)],
            }],
        })
        return parse_segments(_sse_text(raw))

    def find(self, target, jpeg):
        """SAM 3.1: every match for a noun phrase, with its outline."""
        return self._segment(target, jpeg)

    def mark(self, jpeg, width, height, point=None, box=None):
        """SAM 3.1: the one object at a clicked point, or inside a box it had before."""
        size = f"w={width};h={height}"
        if point:
            token = f"<|point;x={point[0]};y={point[1]};{size}|>"
        else:
            token = f"<|box;x1={box['x1']};y1={box['y1']};x2={box['x2']};y2={box['y2']};{size}|>"
        return self._segment(f"<0f>1{token}", jpeg)

    def name_object(self, jpeg, box, crop=None):
        """Muse Spark: a short general name for the object in a box. crop is a close view of it."""
        grid = [box["x1"] * 1000 // box["w"], box["y1"] * 1000 // box["h"],
                box["x2"] * 1000 // box["w"], box["y2"] * 1000 // box["h"]]
        close = " Picture 2 is a close crop of the same object, so use it to see what it is." if crop else ""
        answer, cost = self.ask_text(
            f"A person clicked one object in this camera's picture. It fills this box on a 0-1000 grid, "
            f"as [x_min, y_min, x_max, y_max]: {grid}.{close} Name that object in 1 to 3 words, as a "
            "plain noun phrase, such as gate, parked car or water bottle. Do not name a colour or a brand. "
            "Reply with only the name.",
            [jpeg, crop] if crop else [jpeg], max_tokens=1500,
        )
        return answer.strip().strip('."').lower()[:40], cost

    def same_object(self, noun, reference, camera, views):
        """Muse Spark: which numbered box in each other camera shows the same physical object.

        views is [(camera name, picture with numbered boxes, box count)]. Returns ({name: number}, cost).
        """
        lines = "\n".join(f"Picture {i + 2}: {name}, with boxes 1 to {count}"
                          for i, (name, _, count) in enumerate(views))
        answer, cost = self.ask_text(
            f"Picture 1 is a close view of a {noun} that a person clicked in the camera {camera}. The other "
            f"pictures come from other cameras at the same place:\n{lines}\nIn each one, numbered boxes mark "
            f"things that may be a {noun}. For each camera, give the number of the box that shows the same "
            f"physical {noun} as picture 1, seen from another angle. Give 0 if no box shows it. Another "
            f"{noun} that only looks alike is 0. Reply with only JSON, such as {{\"{views[0][0]}\": 2}}.",
            [reference, *[picture for _, picture, _ in views]], max_tokens=2000,
        )
        start, end = answer.find("{"), answer.rfind("}")
        try:
            picks = json.loads(answer[start:end + 1]) if start >= 0 else {}
        except ValueError:
            picks = {}
        return {str(k): int(v) for k, v in picks.items() if str(v).isdigit()}, cost

    def look(self, question, frames, model="spark"):
        """A model looks at frames and answers in words, for a rule's code.

        Returns ({answer, confidence, best_frame}, cost). The confidence is the model's own
        estimate, so a rule compares it with a threshold and can ask again.
        """
        if model not in LOOK_TIMEOUT_S:
            raise ValueError("model must be spark or wonder.")
        count = len(frames)
        scene = (f"These are {count} frames from a live camera, in time order, numbered from 1."
                 if count > 1 else "This is one frame from a live camera.")
        prompt = (
            f"{scene}\n{question}\nReply in answer, and do not guess. Set confidence from 0 to 1 for "
            "how sure you are from what the frames really show. Use a low value if the thing is small, "
            "blurred, blocked, out of view or only implied. Set best_frame to the number of the frame "
            "that best supports your answer."
        )
        raw = self._post({
            "model": WRITER if model == "wonder" else SPARK,
            "max_output_tokens": 3000 if model == "wonder" else 800,
            "reasoning": {"effort": "low" if model == "wonder" else "minimal"},
            "text": {"format": {"type": "json_schema", "name": "look", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["answer", "confidence", "best_frame"],
                "properties": {
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "best_frame": {"type": "integer"},
                },
            }}},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *[_image_part(jpeg) for jpeg in frames],
                ],
            }],
        }, timeout=LOOK_TIMEOUT_S[model], retries=1)
        data = json.loads(raw)
        usage = data.get("usage") or {}
        cost = wonder_cost(usage) if model == "wonder" else spark_cost(usage)
        answer = json.loads(_output_text(data))
        return {
            "answer": str(answer["answer"])[:600],
            "confidence": min(max(float(answer["confidence"]), 0.0), 1.0),
            "best_frame": int(answer["best_frame"]),
        }, cost

    def verify(self, claim, jpeg, model="spark", reference=None):
        """A model judges one frame alone, and says what it sees before it decides.

        In a real run, Spark saw 7 frames, said yes and named one frame, 3 times out of 3. Shown
        that frame alone, it said no. So a claim about a frame needs a check on that frame.
        Returns ({seen, passed, confidence}, cost).
        """
        if model not in LOOK_TIMEOUT_S:
            raise ValueError("model must be spark or wonder.")
        if reference:
            parts = [
                {"type": "input_text", "text": "The first image shows the event. Use it only to know who or "
                 "what the claim means. Compare clothes, build and position. Do not try to name anyone."},
                _image_part(reference),
                {"type": "input_text", "text": "The next image is the frame to judge."},
            ]
        else:
            parts = []
        parts += [
            _image_part(jpeg),
            {"type": "input_text", "text": (
                f"Look only at the frame to judge. Claim: {claim}\nIn seen, first write what is really "
                "visible for this claim, including anything cut off, blurred, small or hidden. Then set "
                "passed to true only if the whole claim is clearly true in this frame. Set confidence "
                "from 0 to 1 for how sure you are about passed."
            )},
        ]
        raw = self._post({
            "model": WRITER if model == "wonder" else SPARK,
            "max_output_tokens": 3000 if model == "wonder" else 800,
            "reasoning": {"effort": "low" if model == "wonder" else "minimal"},
            "text": {"format": {"type": "json_schema", "name": "verify", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["seen", "passed", "confidence"],
                "properties": {
                    "seen": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
            }}},
            "input": [{"type": "message", "role": "user", "content": parts}],
        }, timeout=LOOK_TIMEOUT_S[model], retries=1)
        data = json.loads(raw)
        usage = data.get("usage") or {}
        cost = wonder_cost(usage) if model == "wonder" else spark_cost(usage)
        answer = json.loads(_output_text(data))
        return {
            "seen": str(answer["seen"])[:400],
            "passed": bool(answer["passed"]),
            "confidence": min(max(float(answer["confidence"]), 0.0), 1.0),
        }, cost

    def locate(self, question, jpeg):
        """Muse Spark: the box of the one object that an event is about.

        A rule's noun can fail on SAM, and Spark understands the event itself.
        Returns the box in pixels, or None, and the cost in USD.
        """
        size = jpeg_size(jpeg)
        if not size:
            return None, 0.0
        raw = self._post({
            "model": SPARK,
            "max_output_tokens": 500,
            "reasoning": {"effort": "minimal"},
            "text": {"format": {"type": "json_schema", "name": "object", "strict": True, "schema": {
                "type": "object", "additionalProperties": False, "required": ["found", "box"],
                "properties": {
                    "found": {"type": "boolean"},
                    "box": {"type": "array", "items": {"type": "integer"}},
                },
            }}},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": (
                        f"A camera rule just fired on this frame. The rule asks: {question}\n"
                        "Locate the one solid object that this event is about, such as the thing that "
                        "a person holds or uses. Do not choose the person. Set box to [x_min, y_min, "
                        "x_max, y_max] on a normalized 0-1000 grid, with (0,0) at the top-left. "
                        "Set found to false if there is no such object."
                    )},
                    _image_part(jpeg),
                ],
            }],
        })
        data = json.loads(raw)
        cost = spark_cost(data.get("usage") or {})
        try:
            answer = json.loads(_output_text(data))
            x1, y1, x2, y2 = answer["box"]
        except (KeyError, TypeError, ValueError):
            return None, cost
        if not answer["found"]:
            return None, cost
        width, height = size
        box = {
            "x1": max(x1 * width // 1000, 0), "y1": max(y1 * height // 1000, 0),
            "x2": min(x2 * width // 1000, width - 1), "y2": min(y2 * height // 1000, height - 1),
            "w": width, "h": height,
        }
        return (box if box["x2"] > box["x1"] and box["y2"] > box["y1"] else None), cost

    def ask(self, question, frames):
        """Muse Spark: a yes/no answer about frames in time order.

        We send our own frames, not an mp4. In our tests Spark missed a 0.3 s event in
        every short clip, but it found one frame among six every time.
        Returns the answer and the cost in USD.
        """
        prompt = question
        if len(frames) > 1:
            prompt = (
                f"These are {len(frames)} frames from a short video, in time order. "
                f"{question} Answer yes if this is true in any frame."
            )
        answer, cost = self.ask_text(prompt + " Answer with one word: yes or no.", frames)
        return answer.lower().startswith("yes"), cost

    def check_facts(self, question, facts, frames, moment=False, view=None):
        """Muse Spark: are all the required facts visible together in one frame?

        We ask for each fact, not for one yes or no. A question such as "is this person
        drinking?" invites a guess. "Is a real container visible?" fails on an empty hand,
        and nobody needs to plan for that case.
        """
        count = len(frames)
        scene = (
            f"These are {count} frames from a live camera, in time order. Frame 1 is the oldest."
            if count > 1 else "This is one frame from a live camera."
        )
        if view:
            scene += " " + view
        listed = "\n".join(f"{i + 1}. {fact}" for i, fact in enumerate(facts))
        if moment:
            how = (
                f"{scene} They surround a fast movement, so blur is normal.\n"
                f"Find what happens across these frames: {question}\n"
                "For each fact below, say true if the frames show it, in one frame or across "
                "neighbouring frames. If nothing shows it, say false.\n"
            )
        else:
            how = (
                f"{scene}\nFind the one frame that best shows this: {question}\n"
                "Look only at that frame. For each fact below, say true only if you can clearly see it "
                "in that frame. If you are guessing, or it is blocked, blurred or out of view, say false. "
                "A hand shape is not an object.\n"
            )
        prompt = (f"{how}Facts:\n{listed}\nSet best_frame to the number of the frame that shows it "
                  "most clearly. Also write one short line that says what you see.\n"
                  "If a fact fails, set part_seen to true when part of this may still be happening: the "
                  "thing is held away from the key place, a hand may hide it, or you see a step just "
                  "before or after it. If nothing here suggests it, set part_seen to false.")
        raw = self._post({
            "model": SPARK,
            "max_output_tokens": 800,
            "reasoning": {"effort": "minimal"},
            "text": {"format": {"type": "json_schema", "name": "facts", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["best_frame", "facts", "note", "part_seen"],
                "properties": {
                    "best_frame": {"type": "integer"},
                    "facts": {"type": "array", "items": {"type": "boolean"}},
                    "note": {"type": "string"},
                    "part_seen": {"type": "boolean"},
                },
            }}},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *[_image_part(jpeg) for jpeg in frames],
                ],
            }],
        }, timeout=LIVE_SPARK_TIMEOUT_S, lane="live")
        data = json.loads(raw)
        cost = spark_cost(data.get("usage") or {})
        try:
            answer = json.loads(_output_text(data))
        except ValueError:
            return {"seen": False, "best_frame": 0, "facts": [], "note": "Unreadable answer."}, cost
        found = [bool(v) for v in answer.get("facts") or []]
        answer["seen"] = len(found) == len(facts) and all(found)
        answer["part_seen"] = bool(answer.get("part_seen")) and not answer["seen"]
        return answer, cost

    def review(self, question, facts, frames, ages):
        """Muse Spark: would a person who watched these seconds say yes?

        check_facts needs one frame that proves everything, and a real action can have no such
        frame. In a real run a hand hid a cigarette at the lips, so 20 checks said no while a person
        smoked. Spark then saw 10 frames over 24 seconds, joined the lighter, the hand and the
        cigarette in the fingers, and said yes on 16 of 16 tries. It said no on 16 of 16 tries on
        bottles, cups and empty hands at the mouth. ages gives the seconds before now for each frame.
        doers lists each person or thing that did it, so two people in one window both count.
        Returns ({frames, clues_for, clues_against, passed, confidence, doers}, cost).
        """
        times = ", ".join(f"{i + 1}: {age:.0f} s ago" for i, age in enumerate(ages))
        listed = "\n".join(f"- {fact}" for fact in facts)
        prompt = (
            f"These are {len(frames)} frames from a live camera, in time order, oldest first. "
            f"Their times are: {times}.\n"
            f"Question: {question}\n"
            f"One clear frame would show all of this:\n{listed}\n"
            "But real actions spread over time. One frame rarely shows everything: a hand can hide a "
            "small object at the key moment, and the object can be thin or blurred. So join the clues "
            "across all frames, as a person who watched the video would.\n"
            "Step 1: in frames, write one short line for each frame. Report only what you see.\n"
            "Step 2: in clues_for and clues_against, list the clues for yes and against yes.\n"
            "Step 3: set passed to true only if the frames together show that the answer is yes, and "
            "no ordinary other explanation fits better. Do not guess from one weak clue.\n"
            "Set confidence from 0 to 1 for how sure you are about passed.\n"
            "Step 4: if passed is true, list in doers each different person or thing that did this. "
            "Describe each one in who by clothes or colour, and do not try to name anyone. Set "
            "best_frame to the number of the frame that best shows that one, and last_frame to the "
            "number of the newest frame where that one is still doing it. Two people in the same "
            "frames are two doers. If passed is false, leave doers empty."
        )
        raw = self._post({
            "model": SPARK,
            "max_output_tokens": 1500,
            "reasoning": {"effort": "minimal"},
            "text": {"format": {"type": "json_schema", "name": "review", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["frames", "clues_for", "clues_against", "passed", "confidence", "doers"],
                "properties": {
                    "frames": {"type": "array", "items": {"type": "string"}},
                    "clues_for": {"type": "array", "items": {"type": "string"}},
                    "clues_against": {"type": "array", "items": {"type": "string"}},
                    "passed": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "doers": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["who", "best_frame", "last_frame"],
                        "properties": {"who": {"type": "string"}, "best_frame": {"type": "integer"},
                                       "last_frame": {"type": "integer"}},
                    }},
                },
            }}},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *[_image_part(jpeg) for jpeg in frames],
                ],
            }],
        }, timeout=LOOK_TIMEOUT_S["spark"], retries=1)
        data = json.loads(raw)
        cost = spark_cost(data.get("usage") or {})
        answer = json.loads(_output_text(data))
        return {
            "frames": [str(line)[:200] for line in answer["frames"]][:len(frames)],
            "clues_for": [str(line)[:200] for line in answer["clues_for"]][:6],
            "clues_against": [str(line)[:200] for line in answer["clues_against"]][:6],
            "passed": bool(answer["passed"]),
            "confidence": min(max(float(answer["confidence"]), 0.0), 1.0),
            "doers": [{"who": str(doer["who"])[:100], "best_frame": int(doer["best_frame"]),
                       "last_frame": int(doer["last_frame"])} for doer in answer["doers"]][:MAX_DOERS],
        }, cost

    def same_subject(self, question, first, second):
        """Muse Spark: do two frames show the same person or thing doing this?

        It describes both before it decides, and it compares clothes. Asked to compare in general,
        Spark called two bearded men in different shirts the same on 5 of 6 tries. This form was
        right on 18 of 18 tries. It names nobody, so two people in the same clothes count as one.
        Returns ({first, second, same, confidence}, cost).
        """
        raw = self._post({
            "model": SPARK,
            "max_output_tokens": 800,
            "reasoning": {"effort": "minimal"},
            "text": {"format": {"type": "json_schema", "name": "subject", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["first", "second", "same", "confidence"],
                "properties": {
                    "first": {"type": "string"},
                    "second": {"type": "string"},
                    "same": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
            }}},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"Two frames from one camera follow. Question: {question}"},
                    _image_part(first),
                    _image_part(second),
                    {"type": "input_text", "text": (
                        "In first, describe the person or thing that this question is about in the first "
                        "frame. For a person, write the colour and pattern of the clothes. For a thing, "
                        "write its colour and shape. In second, do the same for the second frame. Then "
                        "compare the two descriptions. These frames are minutes apart, and people do not "
                        "change clothes in minutes, so different clothes mean a different person. Similar "
                        "hair or a similar beard is not enough to say same. Set same to true only if the "
                        "descriptions match. Do not try to name anyone. Set confidence from 0 to 1 for how "
                        "sure you are about same."
                    )},
                ],
            }],
        })
        data = json.loads(raw)
        cost = spark_cost(data.get("usage") or {})
        answer = json.loads(_output_text(data))
        return {
            "first": str(answer["first"])[:200],
            "second": str(answer["second"])[:200],
            "same": bool(answer["same"]),
            "confidence": min(max(float(answer["confidence"]), 0.0), 1.0),
        }, cost

    def structured(self, prompt, schema, frames=(), name="answer", model="spark", max_tokens=1200, timeout=None,
                   lane="analysis"):
        """A model answers in JSON that fits schema, about text and any frames. Returns (dict, cost).

        Ask uses this to plan a question, to judge a batch of moments, and to word the answer. A live check
        uses the live lane, so a long question never holds it up."""
        raw = self._post({
            "model": WRITER if model == "wonder" else SPARK,
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": "low" if model == "wonder" else "minimal"},
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}, *[_image_part(jpeg) for jpeg in frames]],
            }],
        }, timeout=timeout or LOOK_TIMEOUT_S[model], retries=1, lane=lane)
        data = json.loads(raw)
        usage = data.get("usage") or {}
        cost = wonder_cost(usage) if model == "wonder" else spark_cost(usage)
        try:
            return json.loads(_output_text(data)), cost
        except ValueError:
            # A reply cut off by its token budget is not JSON. Once more, with twice the room.
            if max_tokens >= 6000:
                raise ApiError("The model's answer was cut off.") from None
            again, more = self.structured(prompt, schema, frames, name, model, max_tokens * 2, timeout, lane)
            return again, cost + more

    def ask_text(self, prompt, frames, max_tokens=400):
        """Muse Spark: free text about frames. Returns the text and the cost in USD.

        Spark can spend its whole budget on reasoning, so a short answer about a picture needs room.
        """
        raw = self._post({
            "model": SPARK,
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": "minimal"},
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *[_image_part(jpeg) for jpeg in frames],
                ],
            }],
        })
        data = json.loads(raw)
        return _output_text(data), spark_cost(data.get("usage") or {})
