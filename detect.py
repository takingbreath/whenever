"""Finds common things in a picture on this computer's CPU, for free: YOLOX from Megvii, run as ONNX.

YOLOX is Apache-2.0, its code and its weights (github.com/Megvii-BaseDetection/YOLOX). It knows the 80
kinds of thing in the COCO set, such as person, car, chair, dining table, cup and dog, and gives each one
a box and a confidence. YOLOX-S takes about 60 ms for one picture on a few CPU cores. A rule counts and
places things with it, and pays a model only for what needs understanding, such as whether a table was
served. A watch plan can also wake a paid check only when a count changes.

    python detect.py --download          # gets the model into models/ and checks its sha256
    python detect.py picture.jpg [thing]  # prints what it finds

The model lives in models/, not in Git. Without it, or without onnxruntime and numpy, available() is
False, a watch plan that counts pays for every check, and a rule's ctx.detect gets an error that says so.
"""

import hashlib
import os
import sys
import threading
import urllib.request
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


def available():
    if not model_path().exists():
        return False
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    global _session
    with _loading:
        if _session is None:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = THREADS
            options.inter_op_num_threads = 1
            _session = ort.InferenceSession(str(model_path()), options, providers=["CPUExecutionProvider"])
    return _session


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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        download(sys.argv[2] if len(sys.argv) > 2 else MODEL)
    elif len(sys.argv) > 1:
        label = label_for(sys.argv[2]) if len(sys.argv) > 2 else None
        for item in detect(Path(sys.argv[1]).read_bytes(), [label] if label else None):
            print(item["label"], item["confidence"], item["box"])
    else:
        print(__doc__)
