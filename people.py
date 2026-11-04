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
        row[0:14:2] += left
        row[1:14:2] += top
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


class People:
    """The tagged people, kept in folder/people.json, with a face picture of each in folder/<id>/."""

    def __init__(self, folder, embedder=None, faces=None, clock=time.time):
        self.folder = Path(folder)
        self.embedder = embedder
        self.faces = faces or Faces()
        self.clock = clock
        self.lock = threading.Lock()
        self.items = self._read()

    # ----- storage -----
    def _read(self):
        try:
            return json.loads((self.folder / "people.json").read_text()).get("people") or []
        except (OSError, ValueError):
            return []

    def _save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.folder / "people.json.part"
        part.write_text(json.dumps({"people": self.items}))
        part.replace(self.folder / "people.json")

    def public(self):
        """Each person for the page and for rules, without their numbers."""
        with self.lock:
            return [{"id": p["id"], "name": p["name"], "note": p.get("note", ""), "t": p["t"],
                     "faces": len(p["faces"]), "looks": len(p["looks"]), "picture": p.get("picture"),
                     "seen": p["seen"][-1] if p["seen"] else None, "times_seen": len(p["seen"])}
                    for p in self.items]

    def get(self, person_id):
        with self.lock:
            return next((p for p in self.items if p["id"] == person_id), None)

    def by_name(self, name):
        name = " ".join(str(name or "").split()).lower()
        with self.lock:
            return next((p for p in self.items if p["name"].lower() == name), None)

    def rename(self, person_id, name=None, note=None):
        with self.lock:
            person = next((p for p in self.items if p["id"] == person_id), None)
            if not person:
                raise ValueError("There is no such person.")
            if name is not None:
                name = " ".join(str(name).split())[:60]
                if not name:
                    raise ValueError("A person needs a name or a label.")
                person["name"] = name
            if note is not None:
                person["note"] = " ".join(str(note).split())[:300]
            self._save()
            return dict(person, faces=len(person["faces"]), looks=len(person["looks"]))

    def remove(self, person_id):
        """Forgets a person: their numbers, their pictures and where they were seen."""
        with self.lock:
            before = len(self.items)
            self.items = [p for p in self.items if p["id"] != person_id]
            if len(self.items) == before:
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
            found["look"] = self._look(picture, box)
        return found

    def _look(self, picture, box):
        from PIL import Image, ImageOps
        height, width = picture.shape[:2]
        if box is not None:
            picture = picture[max(0, int(box[1] * height)):int(box[3] * height), max(0, int(box[0] * width)):int(box[2] * width)]
        if picture.size == 0:
            return None
        body = Image.fromarray(picture[:, :, ::-1])
        return self.embedder.images([ImageOps.pad(body, (384, 384), color=(0, 0, 0))])[0]

    # ----- tagging -----
    def tag(self, name, picture, box=None, note="", by="person"):
        """Adds a person, or one more picture of a person with this name. The picture must show a face, or
        else a look that is kept for today only. Returns the person's public fields."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("A person needs a name or a label.")
        found = self.read(picture, box)
        if found["face"] is None and found["look"] is None:
            raise ValueError("No face or person is clear enough in that picture.")
        now = self.clock()
        with self.lock:
            person = next((p for p in self.items if p["name"].lower() == name.lower()), None)
            if person is None:
                person = {"id": uuid.uuid4().hex[:10], "name": name, "note": " ".join(str(note).split())[:300],
                          "t": now, "by": by, "faces": [], "looks": [], "seen": [], "picture": None}
                self.items.append(person)
            self._add_samples(person, found, now, keep=True)
            self._save()
        return next(p for p in self.public() if p["id"] == person["id"])

    def _add_samples(self, person, found, now, keep=False):
        """Adds new face and look numbers to a person, if they are not repeats. Samples from a tag are kept
        when the list is full; others give way, oldest first."""
        import numpy as np
        learned = [s["t"] for s in person["faces"] if not s.get("tag")]
        fresh = keep or not learned or now - max(learned) >= LEARN_EVERY_S
        if found["face"] is not None and fresh:
            vectors = np.array([s["v"] for s in person["faces"]], np.float32) if person["faces"] else None
            if vectors is None or float((vectors @ found["face"]).max()) < SAME_SAMPLE:
                person["faces"].append({"v": [round(float(v), 5) for v in found["face"]], "t": now, "tag": keep})
                if found.get("crop") is not None and (keep or not person.get("picture")):
                    person["picture"] = self._save_crop(person["id"], found["crop"])
            while len(person["faces"]) > MAX_FACES:
                index = next((i for i, s in enumerate(person["faces"]) if not s.get("tag")), 0)
                person["faces"].pop(index)
        if found["look"] is not None:
            person["looks"].append({"v": [round(float(v), 5) for v in found["look"]], "t": now})
            person["looks"] = [s for s in person["looks"] if now - s["t"] < LOOK_HOURS * 3600][-MAX_LOOKS:]

    def _save_crop(self, person_id, crop):
        from PIL import Image
        folder = self.folder / person_id
        folder.mkdir(parents=True, exist_ok=True)
        name = f"face-{int(self.clock() * 1000)}.jpg"
        Image.fromarray(crop[:, :, ::-1]).save(folder / name, quality=90)
        for old in sorted(folder.glob("face-*.jpg"))[:-12]:
            old.unlink(missing_ok=True)
        return name

    # ----- matching -----
    def match(self, found, now=None):
        """The tagged person that read() found, or None, as a dict with id, name, score and by ("face" or
        "look"). A face decides across days and weeks. Without a clear face, a look decides only within
        LOOK_HOURS of a sighting, and only when it is much nearer one person than any other."""
        import numpy as np
        now = self.clock() if now is None else now
        with self.lock:
            people = [p for p in self.items]
            if found["face"] is not None:
                scores = []
                for p in people:
                    if p["faces"]:
                        scores.append((float((np.array([s["v"] for s in p["faces"]], np.float32) @ found["face"]).max()), p))
                scores.sort(key=lambda pair: pair[0], reverse=True)
                if scores and scores[0][0] >= FACE_MATCH[self.faces.kind]:
                    return {"id": scores[0][1]["id"], "name": scores[0][1]["name"], "score": round(scores[0][0], 3),
                            "by": "face"}
                return None   # a clear face that is nobody's settles it: a look must not name a stranger
            if found["look"] is None:
                return None
            scores = []
            for p in people:
                recent = [s["v"] for s in p["looks"] if now - s["t"] < LOOK_HOURS * 3600]
                if recent:
                    scores.append((float((np.array(recent, np.float32) @ found["look"]).max()), p))
            scores.sort(key=lambda pair: pair[0], reverse=True)
            if not scores or scores[0][0] < LOOK_MATCH:
                return None
            if len(scores) > 1 and scores[0][0] - scores[1][0] < LOOK_MARGIN:
                return None
            return {"id": scores[0][1]["id"], "name": scores[0][1]["name"], "score": round(scores[0][0], 3), "by": "look"}

    def saw(self, match, found, camera="", where=None, now=None):
        """Keeps a sighting of a matched person, and learns from a sure one: a face well above the match
        adds its face and today's look, so the person is known again in new light and new clothes."""
        now = self.clock() if now is None else now
        with self.lock:
            person = next((p for p in self.items if p["id"] == match["id"]), None)
            if person is None:
                return
            last = person["seen"][-1] if person["seen"] else None
            if not last or now - last["t"] > 60 or last.get("camera") != camera:
                person["seen"].append({"t": now, "camera": camera, "by": match["by"], "score": match["score"],
                                       "where": where})
                person["seen"] = person["seen"][-MAX_SEEN:]
            else:
                last["t"] = now
            if match["by"] == "face" and match["score"] >= FACE_MATCH[self.faces.kind] + FACE_SURE:
                self._add_samples(person, found, now)
            self._save()


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
