"""Meta's Perception Encoder (PE-Core-S-16-384) on this computer's CPU, for Ask's free ranking.

It turns a picture and a phrase into 512 numbers each. Pictures of what a phrase says get numbers close
to that phrase's numbers, so Ask can put the likely moments first before any paid model call. The model
runs as int8 ONNX: 32 MB for pictures and 64 MB for text, about 0.55 s for one picture on one server core.

The models live in models/ (see deploy/export_pe.py), not in Git. Without them, or without onnxruntime and
numpy, available() is False and Ask ranks by time and movement alone.

The tokenizer is a port of the CLIP byte-pair tokenizer from OpenAI CLIP and open_clip (MIT license), so
no torch is needed on the server.
"""

import gzip
import html
import re
import sys
import threading
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

MODELS = Path(__file__).resolve().parent / "models"
IMAGE_MODEL = MODELS / "pe_image.int8.onnx"
TEXT_MODEL = MODELS / "pe_text.int8.onnx"
VOCAB = MODELS / "bpe_simple_vocab_16e6.txt.gz"
SIZE = 384
CONTEXT = 32
THREADS = 1


@lru_cache()
def bytes_to_unicode():
    numbers = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    chars = numbers[:]
    extra = 0
    for b in range(256):
        if b not in numbers:
            numbers.append(b)
            chars.append(256 + extra)
            extra += 1
    return dict(zip(numbers, (chr(c) for c in chars)))


class Tokenizer:
    """CLIP's byte-pair tokenizer: a phrase becomes CONTEXT token ids, start and end marked."""

    PATTERN = re.compile(r"<start_of_text>|<end_of_text>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|(?:[^\s\w]|_)+",
                         re.IGNORECASE)

    def __init__(self, path=VOCAB):
        merges = gzip.open(path).read().decode("utf-8").split("\n")[1:49152 - 256 - 2 + 1]
        merges = [tuple(m.split()) for m in merges]
        vocab = list(bytes_to_unicode().values())
        vocab = vocab + [v + "</w>" for v in vocab] + ["".join(m) for m in merges] + ["<start_of_text>", "<end_of_text>"]
        self.encoder = {token: index for index, token in enumerate(vocab)}
        self.ranks = {merge: index for index, merge in enumerate(merges)}
        self.start, self.end = self.encoder["<start_of_text>"], self.encoder["<end_of_text>"]
        self.cache = {}

    def bpe(self, token):
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda pair: self.ranks.get(pair, float("inf")))
            if best not in self.ranks:
                break
            first, second = best
            merged, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == first and word[i + 1] == second:
                    merged.append(first + second)
                    i += 2
                else:
                    merged.append(word[i])
                    i += 1
            word = tuple(merged)
        self.cache[token] = word
        return word

    def __call__(self, text):
        text = " ".join(html.unescape(html.unescape(str(text))).split()).lower()
        ids = [self.start]
        for token in self.PATTERN.findall(text):
            token = "".join(bytes_to_unicode()[b] for b in token.encode("utf-8"))
            ids.extend(self.encoder[part] for part in self.bpe(token))
        ids.append(self.end)
        if len(ids) > CONTEXT:
            ids = ids[:CONTEXT - 1] + [self.end]
        return ids + [0] * (CONTEXT - len(ids))


class Embedder:
    """Loads the models on first use.

    The lock guards only the loading. A run is thread-safe in ONNX Runtime, so a question's phrase or crop
    does not wait behind the index, which runs in the idle class and can stall when the CPU is busy.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = None
        self.tokenizer = None
        self.failed = None

    def available(self):
        if self.failed:
            return False
        if not (IMAGE_MODEL.exists() and TEXT_MODEL.exists() and VOCAB.exists()):
            return False
        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        with self.lock:
            return self._load_once()

    def _load_once(self):
        if self.sessions is None:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = THREADS
            options.inter_op_num_threads = 1
            try:
                self.sessions = (ort.InferenceSession(str(IMAGE_MODEL), options, providers=["CPUExecutionProvider"]),
                                 ort.InferenceSession(str(TEXT_MODEL), options, providers=["CPUExecutionProvider"]))
                self.tokenizer = Tokenizer()
            except Exception as e:  # a broken model file must not stop the server
                self.failed = str(e)
                raise
        return self.sessions

    def images(self, pictures):
        """Unit vectors (n, 512) for PIL pictures. A picture is squashed to 384 px square, as in training."""
        import numpy as np
        from PIL import Image
        batch = np.stack([(np.asarray(p.convert("RGB").resize((SIZE, SIZE), Image.BILINEAR), dtype=np.float32)
                           / 127.5 - 1.0).transpose(2, 0, 1) for p in pictures])
        image, _ = self._load()
        out = image.run(None, {"pixels": batch})[0]
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-6)

    def text(self, phrase):
        import numpy as np
        _, text = self._load()
        with self.lock:  # the tokenizer's cache is a plain dict
            ids = np.array([self.tokenizer(phrase)], dtype=np.int64)
        out = text.run(None, {"ids": ids})[0][0]
        return out / max(float(np.linalg.norm(out)), 1e-6)
