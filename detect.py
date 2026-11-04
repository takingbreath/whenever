"""Finds common things in a picture on this computer's CPU, for free: YOLOX from Megvii, run as ONNX.

YOLOX is Apache-2.0, its code and its weights (github.com/Megvii-BaseDetection/YOLOX). It knows the 80
kinds of thing in the COCO set, such as person, car, chair, dining table, cup and dog, and gives each one
a box and a confidence. YOLOX-S takes about 60 ms for one picture on a few CPU cores. A rule counts and
places things with it, and pays a model only for what needs understanding, such as whether a table was
served. A watch plan can also wake a paid check only when a count changes.

poses() finds 17 body points of each person with RTMPose-m from OpenMMLab (Apache-2.0, code and
weights), about 15 ms a person, and says from them whether a hand is up, the person lies down, or sits.
Its training sets (COCO and six other public pose sets) have their own terms.

    python detect.py --download          # gets both models into models/ and checks their sha256
    python detect.py picture.jpg [thing]  # prints what it finds

The models live in models/, not in Git. Without them, or without onnxruntime and numpy, available() is
False, a watch plan that counts pays for every check, and a rule's ctx.detect gets an error that says so.
"""

import hashlib
import math
import os
import sys
import threading
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

MODELS = Path(__file__).resolve().parent / "models"
MODEL = os.environ.get("WHENEVER_DETECT_MODEL") or "yolox_s"
# The official release files, and their sha256, so a changed download is never loaded.
RELEASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/{}.onnx"
SHA256 = {
    "yolox_nano": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
    "yolox_tiny": "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
    "yolox_s": "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
    "yolox_m": "21ff6cfdeb53b013bac2249599e55f00bff3cfdfdab37ed7a4620818c1d15b3f",
}
POSE_MODEL = "rtmpose_m"
POSE_RELEASE = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
                "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip")
POSE_ZIP_SHA256 = "f7fbb6c5c11a1bb70f3d445e4ddec5d144ea89ad5649c081ef976f9b24a0b741"
POSE_SHA256 = "5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c"
POSE_SIZE = (192, 256)    # the model's picture of one person, width and height
POSE_PAD = 1.25           # how much room around a person's box the model sees, as it was trained
POSE_MEAN = (123.675, 116.28, 103.53)
POSE_STD = (58.395, 57.12, 57.375)
POINT_MIN = 0.3           # a body point less sure than this counts as not seen
KEYPOINTS = ("nose", "left eye", "right eye", "left ear", "right ear", "left shoulder", "right shoulder",
             "left elbow", "right elbow", "left wrist", "right wrist", "left hip", "right hip", "left knee",
             "right knee", "left ankle", "right ankle")
THREADS = 2
AT_ONCE = 4               # pictures that the detector reads at the same time
MIN_CONFIDENCE = 0.35
SAME_THING = 0.45         # boxes of one kind that overlap more than this are one thing
PAD = 114                 # the grey that YOLOX was trained with around a picture that is not square

LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)
OTHER_WORDS = {
    "people": "person", "persons": "person", "human": "person", "table": "dining table", "phone": "cell phone",
    "mobile": "cell phone", "sofa": "couch", "television": "tv", "bike": "bicycle", "motorbike": "motorcycle",
    "scooter": "motorcycle", "plant": "potted plant", "bag": "handbag", "glass": "wine glass", "fridge": "refrigerator",
    "plate": "bowl", "ball": "sports ball", "van": "truck", "lorry": "truck",
}

_session = None
_pose_session = None
_loading = threading.Lock()
_gate = threading.BoundedSemaphore(AT_ONCE)
_grids = {}


def label_for(word):
    """The detector's own name for a word, such as "dining table" for "tables", or None."""
    word = " ".join(str(word or "").lower().replace("_", " ").split())
    for candidate in (word, word[:-1] if word.endswith("s") else None, word[:-2] if word.endswith("es") else None):
        if candidate in LABELS:
            return candidate
        if candidate in OTHER_WORDS:
            return OTHER_WORDS[candidate]
    return None


def model_path():
    return MODELS / f"{MODEL}.onnx"


def pose_path():
    return MODELS / f"{POSE_MODEL}.onnx"


def pose_available():
    return available() and pose_path().exists()


def available():
    if not model_path().exists():
        return False
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _new_session(path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _load():
    global _session
    with _loading:
        if _session is None:
            _session = _new_session(model_path())
    return _session


def _load_pose():
    global _pose_session
    with _loading:
        if _pose_session is None:
            _pose_session = _new_session(pose_path())
    return _pose_session


def _grid(size):
    """Each output row's cell and stride: YOLOX looks at the picture in cells of 8, 16 and 32 pixels."""
    import numpy as np
    if size not in _grids:
        cells, strides = [], []
        for stride in (8, 16, 32):
            n = size // stride
            ys, xs = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            cells.append(np.stack((xs, ys), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), stride))
        _grids[size] = (np.concatenate(cells).astype(np.float32), np.concatenate(strides).astype(np.float32))
    return _grids[size]


def _one_each(boxes, scores, kinds):
    """Non-maximum suppression: of boxes of one kind that overlap a lot, only the most certain stays."""
    import numpy as np
    shifted = boxes + kinds[:, None] * 4096.0  # boxes of different kinds never overlap
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        first, rest = order[0], order[1:]
        keep.append(first)
        x1 = np.maximum(shifted[first, 0], shifted[rest, 0])
        y1 = np.maximum(shifted[first, 1], shifted[rest, 1])
        x2 = np.minimum(shifted[first, 2], shifted[rest, 2])
        y2 = np.minimum(shifted[first, 3], shifted[rest, 3])
        common = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        overlap = common / (area(shifted[first]) + area(shifted[rest]) - common + 1e-9)
        order = rest[overlap <= SAME_THING]
    return keep


def detect(picture, things=None, min_confidence=MIN_CONFIDENCE, region=None):
    """The things in a picture, the most certain first, as dicts with label, confidence, box and tall.

    picture is a Pillow image or JPEG bytes. things is a list of labels, or None for all 80. A box is
    (x1, y1, x2, y2) from 0 to 1 across the whole picture. tall is its height over its width in pixels: a
    person who stands is about 2.5 or more, and one who sits is about 2 or less. region, from 0 to 1, makes the detector
    look only there, at more pixels: small things far from the camera are found better.
    """
    import numpy as np
    from PIL import Image
    if isinstance(picture, (bytes, bytearray)):
        picture = Image.open(BytesIO(picture))
    picture = picture.convert("RGB")
    full_w, full_h = picture.size
    left, top = 0, 0
    if region:
        x1, y1, x2, y2 = (max(0.0, min(1.0, float(v))) for v in region)
        left, top = int(x1 * full_w), int(y1 * full_h)
        right, bottom = max(left + 2, int(x2 * full_w)), max(top + 2, int(y2 * full_h))
        picture = picture.crop((left, top, right, bottom))
    session = _load()
    entry = session.get_inputs()[0]
    size = entry.shape[2]
    width, height = picture.size
    scale = min(size / width, size / height)
    scaled = picture.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.BILINEAR)
    # YOLOX takes BGR pixels from 0 to 255, the picture in the top left corner, and grey around it.
    square = np.full((size, size, 3), PAD, np.float32)
    pixels = np.asarray(scaled, np.float32)[:, :, ::-1]
    square[: pixels.shape[0], : pixels.shape[1]] = pixels
    with _gate:
        out = session.run(None, {entry.name: square.transpose(2, 0, 1)[None]})[0][0]
    cells, strides = _grid(size)
    centers = (out[:, :2] + cells) * strides
    sizes = np.exp(out[:, 2:4]) * strides
    kinds = out[:, 5:].argmax(1)
    scores = out[:, 4] * out[np.arange(len(out)), 5 + kinds]
    wanted = scores >= min_confidence
    if things is not None:
        wanted &= np.isin(kinds, [LABELS.index(label) for label in things if label in LABELS])
    if not wanted.any():
        return []
    centers, sizes, kinds, scores = centers[wanted], sizes[wanted], kinds[wanted], scores[wanted]
    boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), 1) / scale
    found = []
    for i in _one_each(boxes, scores, kinds):
        x1, y1, x2, y2 = (float(v) for v in boxes[i])
        x1, x2 = max(0.0, x1) + left, min(width, x2) + left
        y1, y2 = max(0.0, y1) + top, min(height, y2) + top
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        found.append({"label": LABELS[int(kinds[i])], "confidence": round(float(scores[i]), 3),
                      "box": (round(x1 / full_w, 4), round(y1 / full_h, 4), round(x2 / full_w, 4), round(y2 / full_h, 4)),
                      "tall": round((y2 - y1) / (x2 - x1), 2)})
    return found


def poses(picture, boxes):
    """The body of each person box in a picture, as dicts with points, hand_up, lying and sitting.

    boxes are (x1, y1, x2, y2) from 0 to 1, as detect gives them. points are 17 (x, y, score), from 0 to 1
    in the whole picture, in the order of KEYPOINTS. Each flag is True, False, or None when the points
    that it needs are hidden, such as knees under a table.
    """
    import numpy as np
    from PIL import Image
    if isinstance(picture, (bytes, bytearray)):
        picture = Image.open(BytesIO(picture))
    picture = picture.convert("RGB")
    if not boxes:
        return []
    width, height = picture.size
    crops, frames = [], []
    for box in boxes:
        # The model sees the box's middle, with room around it, in its own shape, as in its training.
        x1, y1, x2, y2 = box[0] * width, box[1] * height, box[2] * width, box[3] * height
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        w, h = (x2 - x1) * POSE_PAD, (y2 - y1) * POSE_PAD
        if w * POSE_SIZE[1] > h * POSE_SIZE[0]:
            h = w * POSE_SIZE[1] / POSE_SIZE[0]
        else:
            w = h * POSE_SIZE[0] / POSE_SIZE[1]
        left, top = cx - w / 2, cy - h / 2
        crop = picture.crop((round(left), round(top), round(left + w), round(top + h))).resize(POSE_SIZE, Image.BILINEAR)
        crops.append((np.asarray(crop, np.float32) - POSE_MEAN) / POSE_STD)
        frames.append((left, top, w, h))
    batch = np.stack(crops).transpose(0, 3, 1, 2).astype(np.float32)
    session = _load_pose()
    with _gate:
        simcc_x, simcc_y = session.run(None, {session.get_inputs()[0].name: batch})
    found = []
    for n, (left, top, w, h) in enumerate(frames):
        xs, ys = simcc_x[n].argmax(1), simcc_y[n].argmax(1)
        scores = np.minimum(simcc_x[n].max(1), simcc_y[n].max(1))
        # Each axis has 2 bins a pixel of the model's picture.
        pixels = [(left + xs[k] / 2 / POSE_SIZE[0] * w, top + ys[k] / 2 / POSE_SIZE[1] * h, float(scores[k]))
                  for k in range(len(KEYPOINTS))]
        found.append({"points": [(round(float(x) / width, 4), round(float(y) / height, 4), round(score, 2))
                                 for x, y, score in pixels],
                      **body_flags(pixels)})
    return found


def body_flags(points):
    """hand_up, lying and sitting from 17 body points in pixels, each True, False or None."""
    seen = [point if point[2] >= POINT_MIN else None for point in points]

    def middle(*names):
        found = [seen[KEYPOINTS.index(name)] for name in names if seen[KEYPOINTS.index(name)]]
        return (sum(p[0] for p in found) / len(found), sum(p[1] for p in found) / len(found)) if found else None

    shoulders, hips = middle("left shoulder", "right shoulder"), middle("left hip", "right hip")
    knees, nose = middle("left knee", "right knee"), middle("nose")
    wrists = [seen[KEYPOINTS.index(name)] for name in ("left wrist", "right wrist")]
    flags = {"hand_up": None, "lying": None, "sitting": None}
    torso = math.hypot(hips[0] - shoulders[0], hips[1] - shoulders[1]) if shoulders and hips else 0.0
    if torso > 0:
        # A body that lies down has its shoulders beside its hips, not above them.
        flags["lying"] = abs(hips[0] - shoulders[0]) > 1.5 * abs(hips[1] - shoulders[1])
    if shoulders and (torso > 0 or nose) and any(wrists):
        # A raised hand is well above the shoulders, as when someone calls a waiter or waves.
        above = shoulders[1] - (0.3 * torso if torso > 0 else abs(shoulders[1] - nose[1]))
        flags["hand_up"] = any(wrist and wrist[1] < above for wrist in wrists)
    if torso > 0 and knees and not flags["lying"]:
        # A thigh hangs down about one torso long in a person who stands, and lies almost flat in one who sits.
        flags["sitting"] = (knees[1] - hips[1]) < 0.55 * torso
    return {name: None if value is None else bool(value) for name, value in flags.items()}


def download(name=MODEL):
    """Gets one model from the official release into models/, and refuses it if its sha256 differs."""
    if name not in SHA256:
        raise SystemExit(f"Unknown model {name}. Known: {', '.join(SHA256)}.")
    target = MODELS / f"{name}.onnx"
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == SHA256[name]:
        print(f"{target} is already here.")
        return target
    data = urllib.request.urlopen(RELEASE.format(name), timeout=120).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[name]:
        raise SystemExit(f"{name}: the download has sha256 {digest}, not {SHA256[name]}. Nothing was saved.")
    MODELS.mkdir(exist_ok=True)
    part = target.with_suffix(".part")
    part.write_bytes(data)
    part.replace(target)
    print(f"Saved {target} ({len(data) // 1_000_000} MB).")
    return target


def download_pose():
    """Gets RTMPose-m from OpenMMLab's release into models/, and refuses it if its sha256 differs."""
    target = pose_path()
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == POSE_SHA256:
        print(f"{target} is already here.")
        return target
    data = urllib.request.urlopen(POSE_RELEASE, timeout=300).read()
    if hashlib.sha256(data).hexdigest() != POSE_ZIP_SHA256:
        raise SystemExit(f"{POSE_MODEL}: the download has another sha256. Nothing was saved.")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        model = archive.read(next(name for name in archive.namelist() if name.endswith("end2end.onnx")))
    if hashlib.sha256(model).hexdigest() != POSE_SHA256:
        raise SystemExit(f"{POSE_MODEL}: the model in the download has another sha256. Nothing was saved.")
    MODELS.mkdir(exist_ok=True)
    part = target.with_suffix(".part")
    part.write_bytes(model)
    part.replace(target)
    print(f"Saved {target} ({len(model) // 1_000_000} MB).")
    return target


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        download(sys.argv[2] if len(sys.argv) > 2 else MODEL)
        download_pose()
    elif len(sys.argv) > 1:
        label = label_for(sys.argv[2]) if len(sys.argv) > 2 else None
        for item in detect(Path(sys.argv[1]).read_bytes(), [label] if label else None):
            print(item["label"], item["confidence"], item["box"])
    else:
        print(__doc__)
