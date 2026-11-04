"""People that an operator tags, known again later: by face across days and weeks, and by look within a day.

An operator tags a person once, from any picture of them, with a name or any label such as "Banned 1" or
"Regular, likes the window table". Whenever keeps that person's face numbers and look numbers, adds new
ones each time it is sure, and says for any person in a frame whether it is one of the tagged people.

- Faces: YuNet (OpenCV Zoo, MIT) finds a face and its 5 points in the upper part of a person's box.
  ArcFace R50 (InsightFace, trained on WebFace600K) turns the face into 512 numbers. On 542 webcam faces of
  3 people over 3 days, it knew the same person on another day 97% of the time with 1 false match in 1000,
  and 96% when the faces were shrunk to 24 pixels. Its weights are for non-commercial use only.
  WHENEVER_FACE_MODEL=sface uses OpenCV's SFace (Apache-2.0) instead, which knew 82% on the same test.
- Looks: Meta's PE-Core (embed.py) on the person's whole box: clothes, build and colours. Clothes change
  from day to day, so a look counts only within LOOK_HOURS of a sure sighting, and only as a hint: on the
  gate camera, other people in similar clothes came close.

A face smaller than MIN_FACE pixels, turned away or seen from straight above gives no face numbers. A high
camera over a gate sees few faces. A camera at head height near a door sees most of them.

Each match says how sure it is, from 0 to 0.99, from its score and the face's size (sure_of). The curve
comes from our own webcam test and is made more careful, because a real site sees many strangers. Each
Yes or No that the operator gives on a sighting moves the curve for this site (confirm).

A clear face that is nobody tagged becomes an anonymous visitor, "Visitor 12", so a regular is known
before anyone names them. Naming a visitor makes it a tagged person with all its faces and sightings.

    python people.py --download     # gets the face models into models/ and checks their sha256
"""

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

MODELS = Path(__file__).resolve().parent / "models"
FACE_MODEL = os.environ.get("WHENEVER_FACE_MODEL") or "arcface"
FILES = {
    "yunet": ("face_yunet.onnx", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
              "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx"),
    "arcface": ("face_arcface_r50.onnx", "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
                "https://github.com/yakhyo/uniface/releases/download/weights/w600k_r50.onnx"),
    "sface": ("face_sface.onnx", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
              "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/"
              "face_recognition_sface_2021dec.onnx"),
}
# A face is the same person at or above this likeness. Each value lets through about 1 stranger in 1000
# on the webcam test, with some room for the poorer faces of security cameras.
FACE_MATCH = {"arcface": 0.36, "sface": 0.45}
FACE_SURE = 0.12          # this much above the match, a new face of the person is kept as one more sample
FACE_SCORE = 0.75         # YuNet's confidence that it is a face
MIN_FACE = 20             # pixels across, in the camera's own picture
LOOK_HOURS = 12.0
LOOK_MATCH = 0.90
LOOK_MARGIN = 0.04        # a look must be this much nearer one person than any other tagged person
MAX_FACES = 40
MAX_LOOKS = 30
MAX_SEEN = 500
SAME_SAMPLE = 0.95        # a new sample this like an old one adds nothing
LEARN_EVERY_S = 60.0      # a person learns at most one new face from sightings in this time
ROLES = ("staff", "regular", "vip", "banned", "")
# How sure a face match is, from its score, measured on our own webcam faces (ArcFace R50, tags from
# another day, the best of 3 tags, 8,840 readings of 5 people). Full-size faces: under 0.25 all were
# strangers, 0.25 to 0.30 half, 0.30 to 0.36 88%, and 0.36 or more 99%. Faces shrunk to 24 pixels: 1%, 50%,
# 94% and 99%. A real site sees many more strangers than 5 people, so the middle band stops near 0.75.
SURE_CURVES = {
    "arcface": {"small": ((0.20, 0.0), (0.25, 0.06), (0.275, 0.5), (0.30, 0.6), (0.36, 0.78), (0.37, 0.97), (0.42, 0.99)),
                "large": ((0.20, 0.0), (0.25, 0.03), (0.275, 0.5), (0.30, 0.6), (0.36, 0.75), (0.37, 0.97), (0.42, 0.99))},
    # SFace: under 0.36 about 10%, 0.36 to 0.42 about 65%, 0.42 to 0.50 98%, and 0.50 or more 99%. The
    # band from 0.42 stays lower here for the same reason.
    "sface": {"small": ((0.30, 0.0), (0.36, 0.1), (0.39, 0.6), (0.42, 0.7), (0.50, 0.85), (0.52, 0.99)),
              "large": ((0.30, 0.0), (0.36, 0.1), (0.39, 0.6), (0.42, 0.7), (0.50, 0.85), (0.52, 0.99))},
}
SURE_BANDS = {"arcface": (0.25, 0.30, 0.36), "sface": (0.36, 0.42, 0.50)}
SMALL_FACE, LARGE_FACE = 24, 48   # pixels across: the two sizes that were measured
PRIOR_WEIGHT = 20                 # the curve counts as this many answers, so a few answers move it a little
MAX_SURE = 0.99                   # nothing is ever certain
MIN_SURE = 0.6                    # a face match less sure than this names nobody
LOOK_SAME_CAMERA, LOOK_OTHER_CAMERA = 0.9, 0.8   # a name carried by clothes is less sure than the face
VISITOR_NEW_BELOW = {"arcface": 0.25, "sface": 0.30}   # a new visitor only for a face like nobody known
VISITOR_FACES = 10
VISITOR_DAYS = 30
VISIT_GAP_S = 1800                # sightings further apart than this are two visits
MAX_NEGATIVES = 50
SIGHTING_PICTURES = 60            # the newest sightings of each person keep their face picture and numbers
ARCFACE_POINTS = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))


def model_path(kind):
    return MODELS / FILES[kind][0]


def available():
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return model_path("yunet").exists() and model_path(FACE_MODEL).exists()


def _curve(points, x):
    """Straight lines between the points of a curve, flat beyond its ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if x <= x2:
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return points[-1][1]


def band_of(score, kind=FACE_MODEL):
    """Which of the measured score bands a score is in, from 0 (nobody) to 3 (sure)."""
    return sum(1 for edge in SURE_BANDS[kind] if score >= edge)


def sure_of(score, face_px=LARGE_FACE, kind=FACE_MODEL, counts=None):
    """How sure a face match is, from 0 to 0.99, from its score and the face's size in pixels. counts are
    this site's answers in the score's band, (yes, no): the curve is a prior worth PRIOR_WEIGHT answers,
    and the result is the mean of what follows."""
    curves = SURE_CURVES[kind]
    share = min(1.0, max(0.0, (float(face_px or 0) - SMALL_FACE) / (LARGE_FACE - SMALL_FACE)))
    prior = _curve(curves["small"], score) * (1 - share) + _curve(curves["large"], score) * share
    if face_px and face_px < SMALL_FACE:
        prior *= 0.85   # smaller than any face that was measured
    yes, no = counts or (0, 0)
    sure = (prior * PRIOR_WEIGHT + yes) / (PRIOR_WEIGHT + yes + no)
    return round(min(MAX_SURE, max(0.0, sure)), 3)


def combine(sures):
    """How sure several looks at one person make it, each a chance on its own: 1 - the product of the
    misses, at most MAX_SURE."""
    miss = 1.0
    for sure in sures:
        miss *= 1.0 - max(0.0, min(1.0, float(sure)))
    return round(min(MAX_SURE, 1.0 - miss), 3)


def same_day(a, b):
    return datetime.fromtimestamp(a).date() == datetime.fromtimestamp(b).date()


def visits(sightings, gap_s=VISIT_GAP_S):
    """Sightings grouped into visits, oldest first, each (start, end, [sightings])."""
    found = []
    for item in sorted(sightings, key=lambda s: s.get("since", s["t"])):
        start = item.get("since", item["t"])
        if found and start - found[-1][1] <= gap_s:
            found[-1] = (found[-1][0], max(found[-1][1], item["t"]), found[-1][2] + [item])
        else:
            found.append((start, item["t"], [item]))
    return found


class Faces:
    """Finds the face of a person and turns it into 512 numbers. Safe to share between threads."""

    def __init__(self, kind=FACE_MODEL):
        self.kind = kind
        self.lock = threading.Lock()
        self.detector = None
        self.recognizer = None

    def _load(self):
        import cv2
        if self.recognizer is None:
            if self.kind == "sface":
                self.recognizer = cv2.FaceRecognizerSF.create(str(model_path("sface")), "")
            else:
                import onnxruntime as ort
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                self.recognizer = ort.InferenceSession(str(model_path("arcface")), options,
                                                       providers=["CPUExecutionProvider"])
        return cv2

    def find(self, picture, box=None):
        """The clearest face in a picture, or in the upper part of a person's box from 0 to 1, as a dict with
        row (YuNet's 15 numbers, in the picture's pixels), size in pixels and score. None if no clear face."""
        cv2 = self._load()
        import numpy as np
        height, width = picture.shape[:2]
        left, top, right, bottom = 0, 0, width, height
        if box is not None:
            x1, y1, x2, y2 = box[0] * width, box[1] * height, box[2] * width, box[3] * height
            pad = (x2 - x1) * 0.15
            # The head and shoulders: the top half of a standing person, and most of a sitting one.
            left, right = max(0, int(x1 - pad)), min(width, int(x2 + pad))
            top, bottom = max(0, int(y1 - pad)), min(height, int(y1 + (y2 - y1) * 0.6))
        crop = picture[top:bottom, left:right]
        if crop.size == 0 or min(crop.shape[:2]) < 8:
            return None
        # YuNet finds faces from about 10 pixels in an enlarged picture.
        scale = min(4.0, max(1.0, 320.0 / max(crop.shape[:2])))
        if scale > 1:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        with self.lock:
            if self.detector is None:
                self.detector = cv2.FaceDetectorYN.create(str(model_path("yunet")), "", (320, 320), FACE_SCORE)
            self.detector.setInputSize((crop.shape[1], crop.shape[0]))
            _, faces = self.detector.detect(crop)
        if faces is None or not len(faces):
            return None
        face = max(faces, key=lambda row: row[-1] * row[2])
        row = face.astype(np.float64).copy()
        row[:14] /= scale
        # x and y of the box and of the 5 points move with the crop. Its width and height (2 and 3) do not.
        row[0:1] += left
        row[1:2] += top
        row[4:14:2] += left
        row[5:14:2] += top
        if row[2] < MIN_FACE:
            return None
        return {"row": [float(v) for v in row], "size": float(row[2]), "score": float(row[-1])}

    def vector(self, picture, face):
        """512 numbers for a face from find, as a unit vector."""
        cv2 = self._load()
        import numpy as np
        row = np.array(face["row"], np.float32)
        if self.kind == "sface":
            with self.lock:
                out = self.recognizer.feature(self.recognizer.alignCrop(picture, row[None])).flatten()
        else:
            matrix, _ = cv2.estimateAffinePartial2D(row[4:14].reshape(5, 2), np.array(ARCFACE_POINTS, np.float32),
                                                    method=cv2.LMEDS)
            aligned = cv2.warpAffine(picture, matrix, (112, 112), borderValue=0)
            blob = ((aligned[:, :, ::-1].astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
            out = self.recognizer.run(None, {self.recognizer.get_inputs()[0].name: blob})[0][0]
        return out / max(float(np.linalg.norm(out)), 1e-9)

    def crop(self, picture, face, size=112):
        """A square picture of the face, for the page."""
        cv2 = self._load()
        x, y, w, h = face["row"][:4]
        pad = max(w, h) * 0.35
        left, top = max(0, int(x - pad)), max(0, int(y - pad))
        right, bottom = int(x + w + pad), int(y + h + pad)
        part = picture[top:bottom, left:right]
        return cv2.resize(part, (size, size), interpolation=cv2.INTER_CUBIC) if part.size else None


def _unit(vector):
    import numpy as np
    vector = np.asarray(vector, np.float32)
    return vector / max(float(np.linalg.norm(vector)), 1e-9)


def _numbers(vector):
    return [round(float(v), 5) for v in vector]


class People:
    """The tagged people, kept in folder/people.json, with a face picture of each in folder/<id>/. The
    anonymous visitors, this site's answers to "is this them?" and the visitor count live there too."""

    def __init__(self, folder, embedder=None, faces=None, clock=time.time):
        self.folder = Path(folder)
        self.embedder = embedder
        self.faces = faces or Faces()
        self.clock = clock
        self.lock = threading.Lock()
        self.items, self.visitors, self.calibration, self.visitor_count = self._read()
        self.pruned = 0.0

    # ----- storage -----
    def _read(self):
        try:
            saved = json.loads((self.folder / "people.json").read_text())
        except (OSError, ValueError):
            saved = {}
        return (saved.get("people") or [], saved.get("visitors") or [], saved.get("calibration") or {},
                int(saved.get("visitor_count") or 0))

    def _save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.folder / "people.json.part"
        part.write_text(json.dumps({"people": self.items, "visitors": self.visitors, "calibration": self.calibration,
                                    "visitor_count": self.visitor_count}))
        part.replace(self.folder / "people.json")

    def _public(self, p):
        visitor = p.get("kind") == "visitor"
        return {"id": p["id"], "name": p["name"], "note": p.get("note", ""), "t": p["t"], "role": p.get("role", ""),
                "kind": "visitor" if visitor else "person", "faces": len(p["faces"]), "looks": len(p["looks"]),
                "picture": p.get("picture"), "seen": p["seen"][-1] if p["seen"] else None,
                "times_seen": len(p["seen"]), "visits": len(visits(p["seen"]))}

    def public(self):
        """Each tagged person for the page and for rules, without their numbers."""
        with self.lock:
            return [self._public(p) for p in self.items]

    def public_visitors(self):
        with self.lock:
            return [self._public(p) for p in self.visitors]

    def _find(self, person_id):
        return next((p for p in self.items + self.visitors if p["id"] == person_id), None)

    def get(self, person_id):
        """A tagged person or a visitor, by id."""
        with self.lock:
            return self._find(person_id)

    def by_name(self, name):
        name = " ".join(str(name or "").split()).lower()
        with self.lock:
            return next((p for p in self.items if p["name"].lower() == name), None)

    @staticmethod
    def _role(role):
        role = str(role or "").strip().lower()
        if role not in ROLES:
            raise ValueError(f"A role is one of {', '.join(r for r in ROLES if r)}, or none.")
        return role

    def rename(self, person_id, name=None, note=None, role=None):
        """A new name, note or role. A name for a visitor makes it a tagged person."""
        with self.lock:
            visitor = next((p for p in self.visitors if p["id"] == person_id), None)
        if visitor and name is not None:
            return self.name_visitor(person_id, name, role or "", note or "")
        with self.lock:
            person = self._find(person_id)
            if not person:
                raise ValueError("There is no such person.")
            if name is not None:
                name = " ".join(str(name).split())[:60]
                if not name:
                    raise ValueError("A person needs a name or a label.")
                person["name"] = name
            if note is not None:
                person["note"] = " ".join(str(note).split())[:300]
            if role is not None:
                person["role"] = self._role(role)
            self._save()
            return dict(self._public(person))

    def remove(self, person_id):
        """Forgets a person or a visitor: their numbers, their pictures and where they were seen."""
        with self.lock:
            before = len(self.items) + len(self.visitors)
            self.items = [p for p in self.items if p["id"] != person_id]
            self.visitors = [p for p in self.visitors if p["id"] != person_id]
            if len(self.items) + len(self.visitors) == before:
                return False
            self._save()
        shutil.rmtree(self.folder / person_id, ignore_errors=True)
        return True

    # ----- reading a person in a picture -----
    def read(self, picture, box=None, look=True):
        """The face and look numbers of a person in a picture (BGR numpy), and a face crop, as a dict."""
        face = self.faces.find(picture, box)
        found = {"face": None, "look": None, "face_size": face["size"] if face else 0.0, "crop": None}
        if face:
            found["face"] = self.faces.vector(picture, face)
            found["crop"] = self.faces.crop(picture, face)
        if look and self.embedder is not None and self.embedder.available():
            found["look"] = self.look(picture, box)
        return found

    def look(self, picture, box):
        """The look numbers of a person's whole box (clothes, build, colours), or None."""
        from PIL import Image, ImageOps
        height, width = picture.shape[:2]
        if box is not None:
            picture = picture[max(0, int(box[1] * height)):int(box[3] * height), max(0, int(box[0] * width)):int(box[2] * width)]
        if picture.size == 0:
            return None
        body = Image.fromarray(picture[:, :, ::-1])
        return self.embedder.images([ImageOps.pad(body, (384, 384), color=(0, 0, 0))])[0]

    _look = look

    # ----- tagging -----
    def tag(self, name, picture, box=None, note="", by="person", role=None):
        """Adds a person, or one more picture of a person with this name. The picture must show a face, or
        else a look that is kept for today only. Returns the person's public fields."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("A person needs a name or a label.")
        role = self._role(role) if role is not None else None
        found = self.read(picture, box)
        if found["face"] is None and found["look"] is None:
            raise ValueError("No face or person is clear enough in that picture.")
        now = self.clock()
        with self.lock:
            person = next((p for p in self.items if p["name"].lower() == name.lower()), None)
            if person is None:
                person = {"id": uuid.uuid4().hex[:10], "name": name, "note": " ".join(str(note).split())[:300],
                          "t": now, "by": by, "role": role or "", "faces": [], "looks": [], "seen": [], "picture": None}
                self.items.append(person)
            elif role is not None:
                person["role"] = role
            self._add_samples(person, found, now, keep=True)
            self._save()
            return self._public(person)

    def _add_samples(self, person, found, now, keep=False):
        """Adds new face and look numbers to a person, if they are not repeats. Samples from a tag are kept
        when the list is full; others give way, oldest first."""
        import numpy as np
        learned = [s["t"] for s in person["faces"] if not s.get("tag")]
        fresh = keep or not learned or now - max(learned) >= LEARN_EVERY_S
        limit = VISITOR_FACES if person.get("kind") == "visitor" else MAX_FACES
        if found["face"] is not None and fresh:
            vectors = np.array([s["v"] for s in person["faces"]], np.float32) if person["faces"] else None
            if vectors is None or float((vectors @ found["face"]).max()) < SAME_SAMPLE:
                person["faces"].append({"v": _numbers(found["face"]), "t": now, "tag": keep})
                if found.get("crop") is not None and (keep or not person.get("picture")):
                    person["picture"] = self._save_crop(person["id"], found["crop"])
            while len(person["faces"]) > limit:
                index = next((i for i, s in enumerate(person["faces"]) if not s.get("tag")), 0)
                person["faces"].pop(index)
        if found["look"] is not None:
            person["looks"].append({"v": _numbers(found["look"]), "t": now})
            person["looks"] = [s for s in person["looks"] if now - s["t"] < LOOK_HOURS * 3600][-MAX_LOOKS:]

    def _save_crop(self, person_id, crop, prefix="face", keep=12):
        from PIL import Image
        folder = self.folder / person_id
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}-{int(self.clock() * 1000)}.jpg"
        Image.fromarray(crop[:, :, ::-1]).save(folder / name, quality=90)
        for old in sorted(folder.glob(f"{prefix}-*.jpg"))[:-keep]:
            old.unlink(missing_ok=True)
            old.with_suffix(".npy").unlink(missing_ok=True)
        return name

    # ----- matching -----
    def _sure(self, score, face_px):
        counts = (self.calibration.get(self.faces.kind) or {}).get(str(band_of(score, self.faces.kind)))
        return sure_of(score, face_px, self.faces.kind, counts)

    def _best_face(self, group, face):
        """(score, person) of the person in a group whose faces are most like this face, or None. A person is
        left out when this face is at least as like a face that the operator said is not them."""
        import numpy as np
        best = None
        for p in group:
            if not p["faces"]:
                continue
            score = float((np.array([s["v"] for s in p["faces"]], np.float32) @ face).max())
            if p.get("not") and float((np.array([s["v"] for s in p["not"]], np.float32) @ face).max()) >= score:
                continue
            if best is None or score > best[0]:
                best = (score, p)
        return best

    def _answer(self, person, by, score, sure, found):
        visitor = person.get("kind") == "visitor"
        return {"id": person["id"], "person_id": person["id"], "name": person["name"], "role": person.get("role", ""),
                "by": by, "score": round(float(score), 3), "sure": sure, "face_px": round(float(found.get("face_size") or 0)),
                "visitor": person["name"] if visitor else None, "visits": len(visits(person["seen"])) if visitor else None}

    def _face_today(self, person, now):
        """(sure, camera) of the person's surest face today: a tag, or a face sighting. None without one."""
        best = None
        for sample in person["faces"]:
            if sample.get("tag") and same_day(sample["t"], now):
                best = max(best or (0.0, ""), (MAX_SURE, ""))
        for seen in person["seen"]:
            if seen.get("by") == "face" and same_day(seen["t"], now) and not seen.get("wrong"):
                sure = seen.get("sure") or sure_of(seen.get("score") or 0.0, seen.get("face_px") or LARGE_FACE, self.faces.kind)
                if best is None or sure >= best[0]:
                    best = (sure, seen.get("camera") or "")
        return best

    def match(self, found, now=None, camera=None):
        """The tagged person that read() found, or None, as a dict with id (also person_id), name, role, score,
        sure (0 to 0.99), face_px and by ("face" or "look"). A face decides across days and weeks, from
        MIN_SURE. Without a clear face, a look decides only within LOOK_HOURS, only for a person whose face
        was seen today, and only when it is much nearer one person than any other. A look is less sure than
        that face: LOOK_SAME_CAMERA times on the same camera, LOOK_OTHER_CAMERA times on another one."""
        import numpy as np
        now = self.clock() if now is None else now
        with self.lock:
            if found["face"] is not None:
                best = self._best_face(self.items, found["face"])
                if best:
                    sure = self._sure(best[0], found.get("face_size") or 0)
                    if sure >= MIN_SURE:
                        return self._answer(best[1], "face", best[0], sure, found)
                return None   # a clear face that is nobody's settles it: a look must not name a stranger
            if found["look"] is None:
                return None
            scores = []
            for p in self.items:
                recent = [s["v"] for s in p["looks"] if now - s["t"] < LOOK_HOURS * 3600]
                if recent:
                    scores.append((float((np.array(recent, np.float32) @ found["look"]).max()), p))
            scores.sort(key=lambda pair: pair[0], reverse=True)
            if not scores or scores[0][0] < LOOK_MATCH:
                return None
            if len(scores) > 1 and scores[0][0] - scores[1][0] < LOOK_MARGIN:
                return None
            today = self._face_today(scores[0][1], now)
            if today is None:
                return None
            factor = LOOK_SAME_CAMERA if camera and camera == today[1] else LOOK_OTHER_CAMERA
            return self._answer(scores[0][1], "look", scores[0][0], round(today[0] * factor, 3), found)

    def identify(self, found, camera="", now=None, clear=False):
        """match(), and for a face that is no tagged person, the anonymous visitor it is. A clear face (clear
        is True) that is like no visitor at all becomes a new visitor. A face in between names nobody."""
        answer = self.match(found, now, camera)
        if answer is not None or found["face"] is None:
            return answer
        now = self.clock() if now is None else now
        kind = self.faces.kind
        with self.lock:
            self._forget_old_visitors(now)
            best = self._best_face(self.visitors, found["face"])
            if best and best[0] >= FACE_MATCH[kind]:
                return self._answer(best[1], "face", best[0], self._sure(best[0], found.get("face_size") or 0), found)
            if not clear or (best and best[0] >= VISITOR_NEW_BELOW[kind]):
                return None
            self.visitor_count += 1
            visitor = {"id": "v" + uuid.uuid4().hex[:9], "kind": "visitor", "name": f"Visitor {self.visitor_count}",
                       "note": "", "t": now, "by": "whenever", "role": "", "faces": [], "looks": [], "seen": [],
                       "picture": None, "picture_q": 0.0}
            self.visitors.append(visitor)
            self._add_samples(visitor, dict(found, look=None), now, keep=False)
            visitor["picture_q"] = float(found.get("face_size") or 0)
            self._save()
            return self._answer(visitor, "face", 1.0, MAX_SURE, found)

    def _forget_old_visitors(self, now):
        if now - self.pruned < 3600:
            return
        self.pruned = now
        old = [v for v in self.visitors if now - max([v["t"]] + [s["t"] for s in v["seen"]]) > VISITOR_DAYS * 86400]
        if old:
            self.visitors = [v for v in self.visitors if v not in old]
            for v in old:
                shutil.rmtree(self.folder / v["id"], ignore_errors=True)
            self._save()

    def saw(self, match, found, camera="", where=None, now=None):
        """Keeps a sighting of a matched person or visitor, and learns from a sure one: a face well above the
        match adds its face and today's look, so the person is known again in new light and new clothes.
        A sighting with a face keeps its face picture and numbers, for the operator's Yes or No. Returns the
        sighting's id."""
        import numpy as np
        now = self.clock() if now is None else now
        with self.lock:
            person = self._find(match["id"])
            if person is None:
                return None
            visitor = person.get("kind") == "visitor"
            last = person["seen"][-1] if person["seen"] else None
            score = float(match.get("score") or 0.0)
            better = found.get("crop") is not None and found.get("face") is not None and \
                (not last or score > float(last.get("score") or 0.0) or not last.get("picture"))
            if not last or now - last["t"] > 60 or last.get("camera") != camera:
                last = {"id": uuid.uuid4().hex[:8], "t": now, "since": now, "camera": camera, "by": match["by"],
                        "score": match["score"], "sure": match.get("sure"), "face_px": match.get("face_px"),
                        "where": where, "picture": None}
                person["seen"].append(last)
                person["seen"] = person["seen"][-MAX_SEEN:]
                better = found.get("crop") is not None and found.get("face") is not None
            else:
                last["t"] = now
                if (match.get("sure") or 0) > (last.get("sure") or 0):
                    last.update(by=match["by"], score=match["score"], sure=match.get("sure"), face_px=match.get("face_px"))
            if better:
                last["picture"] = self._save_crop(person["id"], found["crop"], "seen", SIGHTING_PICTURES)
                np.save(self.folder / person["id"] / (last["picture"][:-4] + ".npy"), np.asarray(found["face"], np.float32))
            sure_face = match["by"] == "face" and score >= FACE_MATCH[self.faces.kind] + FACE_SURE
            if visitor and match["by"] == "face" and score >= FACE_MATCH[self.faces.kind]:
                self._add_samples(person, found, now)
                if (found.get("face_size") or 0) > person.get("picture_q", 0) and found.get("crop") is not None:
                    person["picture"] = self._save_crop(person["id"], found["crop"])
                    person["picture_q"] = float(found.get("face_size") or 0)
            elif sure_face:
                self._add_samples(person, found, now)
            self._save()
            return last["id"]

    # ----- the operator's answers -----
    def _sighting_face(self, person, sighting):
        import numpy as np
        if not sighting.get("picture"):
            return None
        try:
            return np.load(self.folder / person["id"] / (sighting["picture"][:-4] + ".npy"))
        except (OSError, ValueError):
            return None

    def confirm(self, person_id, sighting_id, yes):
        """The operator says whether a sighting really is this person. Yes keeps its face as one more sample
        of them. No keeps it as a face that is not them, so it never names them again. Either way the answer
        counts for this site's curve, in the sighting's score band. Returns the person's public fields."""
        now = self.clock()
        with self.lock:
            person = self._find(person_id)
            if person is None:
                raise ValueError("There is no such person.")
            sighting = next((s for s in person["seen"] if s.get("id") == sighting_id), None)
            if sighting is None:
                raise ValueError("There is no such sighting.")
            face = self._sighting_face(person, sighting)
            if sighting.get("by") == "face" and sighting.get("score") is not None and not sighting.get("checked"):
                counts = self.calibration.setdefault(self.faces.kind, {}).setdefault(
                    str(band_of(float(sighting["score"]), self.faces.kind)), [0, 0])
                counts[0 if yes else 1] += 1
            sighting["checked"] = "yes" if yes else "no"
            if yes:
                sighting.pop("wrong", None)
                if face is not None:
                    self._add_samples(person, {"face": face, "look": None, "crop": None}, now, keep=True)
            else:
                sighting["wrong"] = True
                if face is not None:
                    person.setdefault("not", []).append({"v": _numbers(face), "t": now})
                    person["not"] = person["not"][-MAX_NEGATIVES:]
            self._save()
            return self._public(person)

    def name_visitor(self, visitor_id, name, role="", note=""):
        """Gives a visitor a name, so it becomes a tagged person with all its faces and sightings. A tagged
        person who already has that name takes them instead."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("A person needs a name or a label.")
        role = self._role(role)
        with self.lock:
            visitor = next((p for p in self.visitors if p["id"] == visitor_id), None)
            if visitor is None:
                raise ValueError("There is no such visitor.")
            same = next((p for p in self.items if p["name"].lower() == name.lower()), None)
            if same is not None:
                self._merge(visitor, same)
                same["role"] = role or same.get("role", "")
                if note:
                    same["note"] = " ".join(str(note).split())[:300]
                self._save()
                return self._public(same)
            self.visitors.remove(visitor)
            visitor.update(kind="person", name=name, role=role, by="person",
                           note=" ".join(str(note).split())[:300] or visitor.get("note", ""))
            visitor.pop("picture_q", None)
            for sample in visitor["faces"]:
                sample["tag"] = True   # the operator named this face, so it stays
            self.items.append(visitor)
            self._save()
            return self._public(visitor)

    def merge(self, from_id, into_id):
        """Makes two people or visitors one: into keeps its id, and takes the other's faces, looks and
        sightings. A visitor merged with a tagged person becomes that tagged person."""
        with self.lock:
            source, target = self._find(from_id), self._find(into_id)
            if source is None or target is None or source is target:
                raise ValueError("Choose two different people.")
            if target.get("kind") == "visitor" and source.get("kind") != "visitor":
                self.visitors.remove(target)
                target.update(kind="person", name=source["name"], role=source.get("role", ""),
                              note=source.get("note", ""), by=source.get("by", "person"))
                target.pop("picture_q", None)
                self.items.append(target)
            self._merge(source, target)
            self._save()
            return self._public(target)

    def _merge(self, source, target):
        limit = VISITOR_FACES if target.get("kind") == "visitor" else MAX_FACES
        target["faces"] = (target["faces"] + source["faces"])[-limit:]
        target["looks"] = sorted(target["looks"] + source["looks"], key=lambda s: s["t"])[-MAX_LOOKS:]
        target["seen"] = sorted(target["seen"] + source["seen"], key=lambda s: s["t"])[-MAX_SEEN:]
        target["not"] = (target.get("not") or []) + (source.get("not") or [])
        target["role"] = target.get("role") or source.get("role", "")
        if not target.get("picture") and source.get("picture"):
            target["picture"] = source["picture"]
        # The pictures move too, so the sightings keep their faces.
        old, new = self.folder / source["id"], self.folder / target["id"]
        if old.is_dir():
            new.mkdir(parents=True, exist_ok=True)
            for file in old.iterdir():
                if not (new / file.name).exists():
                    shutil.move(str(file), str(new / file.name))
            shutil.rmtree(old, ignore_errors=True)
        self.items = [p for p in self.items if p is not source]
        self.visitors = [p for p in self.visitors if p is not source]

    # ----- what the page shows -----
    def today(self, start, end):
        """The people and visitors seen between start and end, such as today, the latest first."""
        groups = []
        with self.lock:
            for p in self.items + self.visitors:
                seen = [s for s in p["seen"] if start <= s["t"] < end and not s.get("wrong")]
                if not seen:
                    continue
                sures = [s.get("sure") for s in seen if s.get("sure") is not None]
                groups.append({"kind": "visitor" if p.get("kind") == "visitor" else "person", "id": p["id"],
                               "name": p["name"], "role": p.get("role", ""), "picture": p.get("picture"),
                               "count": len(seen), "first": min(s.get("since", s["t"]) for s in seen),
                               "last": max(s["t"] for s in seen),
                               "cameras": list(dict.fromkeys(s.get("camera") or "" for s in seen if s.get("camera"))),
                               "sure": max(sures) if sures else None})
        return sorted(groups, key=lambda g: g["last"], reverse=True)

    def page(self, person_id, now=None, day=lambda t: datetime.fromtimestamp(t).date().isoformat(), days=30):
        """One person's or visitor's page: their visits in the last days, by day, how long they stay, where
        they were last, and their newest sightings. day turns a time into the day it belongs to."""
        now = self.clock() if now is None else now
        with self.lock:
            person = self._find(person_id)
            if person is None:
                return None
            seen = [s for s in person["seen"] if now - s["t"] <= days * 86400]
            groups = visits([s for s in seen if not s.get("wrong")])
            by_day = {}
            for start, end, _ in groups:
                entry = by_day.setdefault(day(start), {"day": day(start), "count": 0, "minutes": 0})
                entry["count"] += 1
                entry["minutes"] += max(1, round((end - start) / 60))
            stays = [max(1, round((end - start) / 60)) for start, end, _ in groups]
            last = person["seen"][-1] if person["seen"] else None
            return {"person": self._public(person), "visits": sorted(by_day.values(), key=lambda d: d["day"]),
                    "avg_stay_min": round(sum(stays) / len(stays), 1) if stays else None,
                    "last": {"t": last["t"], "camera": last.get("camera") or "", "picture": last.get("picture")} if last else None,
                    "sightings": [dict(s) for s in reversed(person["seen"][-50:])]}


def picture_array(data):
    """BGR numpy picture from JPEG bytes or a Pillow picture, as OpenCV wants it."""
    import numpy as np
    from PIL import Image
    if isinstance(data, (bytes, bytearray)):
        data = Image.open(BytesIO(data))
    return np.ascontiguousarray(np.asarray(data.convert("RGB"))[:, :, ::-1])


def download():
    """Gets YuNet and the chosen face model into models/, refusing a file whose sha256 differs."""
    for kind in ("yunet", FACE_MODEL):
        name, digest, url = FILES[kind]
        target = MODELS / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            print(f"{target} is already here.")
            continue
        data = urllib.request.urlopen(url, timeout=300).read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f"{kind}: the download has another sha256. Nothing was saved.")
        MODELS.mkdir(exist_ok=True)
        part = target.with_suffix(".part")
        part.write_bytes(data)
        part.replace(target)
        print(f"Saved {target} ({len(data) // 1_000_000} MB).")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        download()
    else:
        print(__doc__)
