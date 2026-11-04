"""Builds models/ for Ask's free ranking: Meta's PE-Core-S-16-384 as int8 ONNX, and CLIP's tokenizer vocabulary.

Run it once on any computer with torch, in its own environment:

    uv venv --python 3.12 .pe && uv pip install --python .pe/bin/python torch open_clip_torch onnx onnxruntime
    .pe/bin/python deploy/export_pe.py

Then copy models/ to the server's app folder. The server needs only onnxruntime and numpy.
"""

import shutil
from pathlib import Path

import numpy as np
import onnxruntime as ort
import open_clip
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic

OUT = Path(__file__).resolve().parent.parent / "models"
NAME = "PE-Core-S-16-384"


class Image(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, pixels):
        return self.model.encode_image(pixels)


class Text(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, ids):
        return self.model.encode_text(ids)


def main():
    OUT.mkdir(exist_ok=True)
    model, _, _ = open_clip.create_model_and_transforms(NAME, pretrained="meta")
    model.eval()
    tokenizer = open_clip.get_tokenizer(NAME)
    pixels = torch.randn(2, 3, 384, 384)
    ids = tokenizer(["a person at the gate", "a white car"])
    for module, inputs, name in ((Image(model), pixels, "image"), (Text(model), ids, "text")):
        plain = OUT / f"pe_{name}.onnx"
        torch.onnx.export(module, (inputs,), plain, input_names=["pixels" if name == "image" else "ids"],
                          output_names=["embedding"], opset_version=17, dynamo=False,
                          dynamic_axes={("pixels" if name == "image" else "ids"): {0: "n"}, "embedding": {0: "n"}})
        quantize_dynamic(str(plain), str(OUT / f"pe_{name}.int8.onnx"), weight_type=QuantType.QInt8)
        plain.unlink()
    with torch.no_grad():
        want = model.encode_image(pixels).numpy()
    got = ort.InferenceSession(str(OUT / "pe_image.int8.onnx")).run(None, {"pixels": pixels.numpy()})[0]
    cosine = (got * want).sum(1) / np.linalg.norm(got, axis=1) / np.linalg.norm(want, axis=1)
    print("int8 against torch, cosine:", cosine.round(4))
    shutil.copy(Path(open_clip.__file__).parent / "bpe_simple_vocab_16e6.txt.gz", OUT)
    print("Wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
