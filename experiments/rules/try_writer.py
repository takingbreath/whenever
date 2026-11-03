"""Runs the Wonder writer on hard sentences, outside the server. It uses the paid API."""

import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import sandbox
from meta_api import Client, load_key
from writer import write_rule

client = Client(load_key(ROOT / ".env"))
for sentence in sys.argv[1:]:
    rule_id = "try-" + uuid.uuid4().hex[:6]
    started = time.perf_counter()
    folder = sandbox.create(rule_id)
    print(f"\n### {sentence}\nfolder: {folder.name}", flush=True)
    result, cost = write_rule(client, folder, sentence, progress=lambda text: print("  -", text, flush=True))
    print(f"result: {json.dumps(result, ensure_ascii=False)[:600]}")
    print(f"{time.perf_counter() - started:.0f} s, ${cost:.3f}")
