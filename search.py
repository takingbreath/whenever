"""Search the footage for an answer, the way a careful person would: look widely and cheaply first, then
look closer only where something may be.

The saved footage is a list of frames: while a camera's picture changes, several a second, each with
that camera's own full frame; in a still picture, one frame a minute. So the frames themselves say when
each camera was busy. Ask turns them into episodes, one for each stretch of change in one camera.

A paid model call is the cost, so one call judges a contact sheet of up to 9 moments, each labelled with
its number, camera and time (the grid idea of T*). Spark says which moments show the claim, which ones
it cannot tell and should see closer, and how sure it is. Whenever then looks closer where Spark asked,
with more frames of that episode or with frames from the full recording, and stops as soon as it is sure
(the loop of VideoAgent). The pace in the plan sets how closely it looks: a person walking past needs
the frames of each episode, a parcel left at the door needs one frame now and then.

"When did this arrive?" about a pointed thing in a camera that stays still uses no model to search. The
place the person pointed at is compared, frame by frame, with how it looks now. Whenever steps back in
time, 1, 2, 4, 8 minutes and more, until the place looks different, then halves the gap until it finds
the change. Spark only confirms the two frames on each side of it.
"""

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps  # noqa: E402

SESSION = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
# A camera's episode ends after this long with no change in it.
EPISODE_GAP_S = 8.0
TILE = (400, 225)
SHEET_COLUMNS = 3
SHEET_SIZE = 9
# How many sheets one question may use before it asks the person. About 0.5 cents each.
FREE_SHEETS = 6
MAX_SHEETS = 40
SHEET_USD = 0.005
VERIFY_USD = 0.004
MAX_VERIFY = 3
# Hits the full-size look rejects do not end a search. It goes on, up to this many full-size looks in all.
MAX_VERIFY_TOTAL = 9
# With the moment index, this many of the best-ranked moments are looked at first. Of those, a question
# about the last or the first time takes the newest or the oldest first.
TOP_RANKED = 27
# Of one episode, only this many go in the best-ranked moments, so one long visit does not fill the sheets.
TOP_PER_EPISODE = 2
# A ranked moment uses the index row of its camera closest in time, within this.
NEAR_S = 3.0
# A moment is scored against how its camera usually looks around then (day, night, rain), within this.
USUAL_S = 1800.0
# Moments the index does not have yet are embedded during the question, up to this many and this long.
INDEX_NOW_MAX = 300
INDEX_NOW_S = 30.0
# A question about something that passes fast looks at more moments of each episode.
PER_EPISODE = {"fast": 3, "medium": 2, "slow": 1}
# In still footage, a slow thing is looked for this often.
STILL_EVERY_S = {"fast": 0, "medium": 0, "slow": 600}
CLIP_S = (4.0, 10.0)
RECORDING_FPS = 2

SHEET_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["hits", "closer", "sure", "notes"],
    "properties": {
        "hits": {"type": "array", "items": {"type": "integer"}},
        "closer": {"type": "array", "items": {"type": "integer"}},
        "sure": {"type": "integer"},
        "notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["tile", "note"],
            "properties": {"tile": {"type": "integer"}, "note": {"type": "string"}}}},
    },
}

PAIR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["first_has_it", "second_has_it", "note"],
    "properties": {"first_has_it": {"type": "boolean"}, "second_has_it": {"type": "boolean"}, "note": {"type": "string"}},
}


def hms(t):
    from ask import ZONE
    return datetime.fromtimestamp(t, ZONE).strftime("%H:%M:%S")


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class Footage:
    """The saved frames of every session in a time span, with the cameras that were busy in each."""

    def __init__(self, root, wall):
        self.root = Path(root)
        self.wall = wall
        self.full = {}

    def sessions(self, since, until):
        names = sorted(p.name for p in self.root.iterdir() if p.is_dir() and SESSION.fullmatch(p.name))
        starts = [datetime.strptime(n[:15], "%Y%m%d-%H%M%S").timestamp() for n in names]
        found = []
        for index, name in enumerate(names):
            end = starts[index + 1] if index + 1 < len(names) else float("inf")
            if starts[index] <= until and end >= since:
                found.append(name)
        return found

    def frames(self, since, until):
        found = []
        for session in self.sessions(since, until):
            folder = self.root / session
            try:
                full = {}
                for path in (folder / "frames" / "full").iterdir():
                    stem, _, tile = path.stem.rpartition("_")
                    if tile.isdigit():
                        full.setdefault(stem, set()).add(int(tile))
            except OSError:
                full = {}
            self.full[session] = full
            try:
                lines = (folder / "frames.jsonl").read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                t = record.get("t") or 0
                if since <= t <= until:
                    name = record["name"]
                    found.append({"t": t, "name": name, "session": session, "motion": record.get("motion") or 0,
                                  "busy": full.get(name[:-4], set())})
        found.sort(key=lambda f: f["t"])
        return found

    def path(self, frame, tile=None):
        frames = self.root / frame["session"] / "frames"
        if tile is not None and tile in frame["busy"]:
            full = frames / "full" / f"{frame['name'][:-4]}_{tile}.jpg"
            if full.exists():
                return full, True
        sharp = frames / "sharp" / frame["name"]
        if sharp.exists():
            return sharp, False
        plain = frames / frame["name"]
        return (plain, False) if plain.exists() else (None, False)

    def picture(self, frame, tile=None, draft=(1440, 810)):
        """One camera's picture at one moment: its own full frame if kept, or its tile of the wall."""
        path, full = self.path(frame, tile)
        if path is None:
            return None
        picture = Image.open(path)
        if full:
            picture.draft("RGB", draft)
        picture = picture.convert("RGB")
        if tile is None or full:
            return picture
        columns, rows = self.wall
        if columns * rows <= 1:
            return picture
        width, height = picture.width / columns, picture.height / rows
        column, row = tile % columns, tile // columns
        return picture.crop((round(column * width), round(row * height), round((column + 1) * width),
                             round((row + 1) * height)))


def episodes(frames, tile, gap=EPISODE_GAP_S):
    """Stretches of change in one camera: lists of frames, oldest first."""
    found, current = [], []
    for frame in frames:
        if tile not in frame["busy"]:
            continue
        if current and frame["t"] - current[-1]["t"] > gap:
            found.append(current)
            current = []
        current.append(frame)
    if current:
        found.append(current)
    return found


def spread(items, count):
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def contact_sheet(tiles):
    """Up to 9 moments in a grid, each with its number, camera and time in its corner."""
    rows = (len(tiles) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    sheet = Image.new("RGB", (SHEET_COLUMNS * TILE[0] + (SHEET_COLUMNS - 1) * 6, rows * TILE[1] + (rows - 1) * 6), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    face = font(20)
    for index, tile in enumerate(tiles):
        picture = ImageOps.contain(tile["picture"], TILE)
        left = (index % SHEET_COLUMNS) * (TILE[0] + 6)
        top = (index // SHEET_COLUMNS) * (TILE[1] + 6)
        sheet.paste(picture, (left + (TILE[0] - picture.width) // 2, top + (TILE[1] - picture.height) // 2))
        label = f"{index + 1} · {tile['camera']} · {hms(tile['t'])}"
        box = draw.textbbox((left + 8, top + 6), label, font=face)
        draw.rectangle((box[0] - 5, box[1] - 3, box[2] + 5, box[3] + 3), fill=(0, 0, 0))
        draw.text((left + 8, top + 6), label, fill=(255, 214, 0), font=face)
    out = BytesIO()
    sheet.save(out, "JPEG", quality=84)
    return out.getvalue()


def jpeg(picture, quality=88):
    out = BytesIO()
    picture.save(out, "JPEG", quality=quality)
    return out.getvalue()


class Search:
    """One question about the past, from the plan to the answer."""

    def __init__(self, asks, job):
        self.asks = asks
        self.app = asks.app
        self.job = job
        self.plan = job["plan"]
        self.footage = Footage(self.app.evidence.root, self.app.wall)
        self.cameras = self.plan["cameras"] or list(self.app.cameras)
        self.tiles = {name: self.app.cameras.index(name) for name in self.cameras if name in self.app.cameras}
        self.sheets = 0
        self.looked = 0
        self.seen = set()
        self.verified = 0
        self.kept = None
        self.rejected = []

    def step(self, text):
        self.asks.step(self.job, text)

    def run(self):
        if self.plan["kind"] == "since":
            return Since(self).run()
        frames = self.footage.frames(self.plan["since"], self.plan["until"])
        self.job["span"] = [self.plan["since"], self.plan["until"]]
        if not frames:
            return self.asks.finish(self.job, "There is no saved footage from that time.", 0.0, [])
        queue = self.candidates(frames)
        self.step(f"Found {len(queue)} moments with movement or change in {', '.join(self.tiles) or 'the cameras'}")
        queue = self.rank(queue)
        seen = self.job.setdefault("seen", set())
        queue = [item for item in queue if moment_key(item) not in seen]
        if not queue:
            facts = {"looked_at": 0, "span": [hms(self.plan["since"]), hms(self.plan["until"])],
                     "note": "Nothing moved in these cameras in this time."}
            words, sure = self.asks.word(self.job, facts, [])
            return self.asks.finish(self.job, words, sure, [], looked=0)
        need = (len(queue) + SHEET_SIZE - 1) // SHEET_SIZE
        allowed = self.allowed_sheets()
        if self.plan["answer_as"] in ("count", "how_long") or self.plan["which"] == "all":
            if need > allowed and not self.job.get("approved_usd"):
                self.job.update(status="needs", estimate_usd=round(need * SHEET_USD + MAX_VERIFY * VERIFY_USD, 3),
                                looked=len(queue))
                return None
        hits = self.look(queue, allowed)
        return self.answer(hits, frames)

    def allowed_sheets(self):
        approved = self.job.get("approved_usd")
        if approved:
            return min(MAX_SHEETS, int(approved / SHEET_USD))
        return FREE_SHEETS

    # ----- choose what to look at -----

    def candidates(self, frames):
        """The moments worth a look, in the order that answers the question soonest."""
        pace = self.plan["pace"]
        per = PER_EPISODE[pace]
        items = []
        for name, tile in self.tiles.items():
            for episode in episodes(frames, tile):
                picks = spread(episode, per)
                items.append({"camera": name, "tile": tile, "episode": episode, "frames": picks, "t": episode[0]["t"]})
            every = STILL_EVERY_S[pace]
            if every:
                last = -1e18
                for frame in frames:
                    if frame["t"] - last >= every:
                        last = frame["t"]
                        items.append({"camera": name, "tile": tile, "episode": [frame], "frames": [frame], "t": frame["t"]})
        which = self.plan["which"]
        if which == "last":
            items.sort(key=lambda item: -item["t"])
        elif which == "first":
            items.sort(key=lambda item: item["t"])
        else:
            items = coverage_order(items, self.plan["since"], self.plan["until"])
        queue, keys = [], set()
        for item in items:
            for frame in item["frames"]:
                moment = {"camera": item["camera"], "tile": item["tile"], "frame": frame, "t": frame["t"],
                          "episode": item["episode"]}
                # A still look every few minutes can land on a frame that an episode already gave.
                if moment_key(moment) not in keys:
                    keys.add(moment_key(moment))
                    queue.append(moment)
        return queue

    def rank(self, queue):
        """Puts the moments that look like the question first, for free, with the PE-Core index. Without it,
        the order stays: by time and by how much moved.

        Moments the index does not have yet are embedded now, on this computer. Each moment is scored
        against how its own camera usually looks around then, so a camera whose view always looks a bit
        like the words (a gate, for a question about the gate) does not win on its view alone. Only what
        is new in the picture counts.
        """
        app = self.app
        embedder = getattr(app, "embedder", None)
        if not queue or embedder is None or not embedder.available():
            return queue
        try:
            import numpy as np
            words = photo_of(self.plan["thing"]) if self.plan["thing"] else ""
            phrases = [p for p in (words, self.plan["claim"]) if p] or [self.job["question"]]
            text = sum(embedder.text(p) for p in phrases)
            # A pointed thing is also found by its look: its own picture joins the words.
            thing = self.job["context"].get("thing") or {}
            point = app.points.get(thing.get("id")) if thing.get("id") else None
            if point and (point.get("crop") or point.get("view")):
                crop = point.get("crop")
                picture = Image.open(BytesIO(base64.b64decode(crop) if isinstance(crop, str) else crop or point["view"]))
                text = text / max(float(np.linalg.norm(text)), 1e-6) + embedder.images([picture])[0]
            text = text / max(float(np.linalg.norm(text)), 1e-6)
            tiles = sorted(self.tiles.values())
            index = app.moments.vectors(self.plan["since"], self.plan["until"], tiles)
            scores = moment_scores(queue, index, text)
            missing = [item for item, s in zip(queue, scores) if s is None]
            index_now = getattr(app.moments, "index_now", None)
            if missing and index_now:
                # One moment of each episode first, so the time allowed covers as many visits as it can.
                firsts, others, episodes_seen = [], [], set()
                for item in missing:
                    episode = (item["tile"], item["episode"][0]["t"] if item["episode"] else item["t"])
                    (others if episode in episodes_seen else firsts).append(item)
                    episodes_seen.add(episode)
                wanted = list({(i["frame"]["session"], i["frame"]["name"], i["tile"]): (i["frame"], i["tile"])
                               for i in firsts + others}.values())[:INDEX_NOW_MAX]
                self.step(f"Indexing {len(wanted)} moments on this computer first, for free")
                if index_now(wanted, INDEX_NOW_S):
                    index = app.moments.vectors(self.plan["since"], self.plan["until"], tiles)
                    scores = moment_scores(queue, index, text)
        except Exception:
            return queue
        known = sum(1 for s in scores if s is not None)
        if not known:
            return queue
        for item, score in zip(queue, scores):
            item["score"] = 0.0 if score is None else score
        ranked = sorted(queue, key=lambda item: -item["score"])
        top, rest, per = [], [], {}
        for item in ranked:
            episode = (item["tile"], item["episode"][0]["t"] if item["episode"] else item["t"])
            if len(top) < TOP_RANKED and per.get(episode, 0) < TOP_PER_EPISODE:
                per[episode] = per.get(episode, 0) + 1
                top.append(item)
            else:
                rest.append(item)
        if self.plan["which"] == "last":
            top.sort(key=lambda item: -item["t"])
        elif self.plan["which"] == "first":
            top.sort(key=lambda item: item["t"])
        about = words[len("a photo of "):] if words else f"\"{phrases[0]}\""
        self.step(f"Ranked {known} moments by how much they look like {about}, for free, with Meta's Perception Encoder")
        return top + rest

    # ----- look -----

    def look(self, queue, allowed):
        hits = []
        sure = 1
        queue = list(queue)
        while queue and self.sheets < allowed:
            batch, queue = queue[:SHEET_SIZE], queue[SHEET_SIZE:]
            tiles = []
            for item in batch:
                picture = item.get("picture") or self.footage.picture(item["frame"], item["tile"])
                if picture is None:
                    continue
                tiles.append({**item, "picture": picture})
            if not tiles:
                continue
            result = self.judge(tiles)
            self.sheets += 1
            self.looked += len(tiles)
            # A question that goes on after "look at the rest" does not pay for the same moments twice.
            self.job.setdefault("seen", set()).update(moment_key(tile) for tile in tiles)
            notes = {n["tile"]: n["note"] for n in result["notes"]}
            found = [tiles[i - 1] for i in result["hits"] if 1 <= i <= len(tiles)]
            for tile in found:
                tile["note"] = notes.get(tiles.index(tile) + 1, "")
            hits.extend(found)
            sure = max(sure, result["sure"]) if found else sure
            closer = [tiles[i - 1] for i in result["closer"] if 1 <= i <= len(tiles) and tiles[i - 1] not in found]
            self.step(f"Looked at {self.looked} moments: {len(hits)} show it" + (f", {len(closer)} to see closer" if closer else ""))
            # Look closer where Spark could not tell: frames from the camera's own recording when it still
            # has that moment, at 2 a second, or else the other saved frames of that episode. They go first.
            more = []
            for tile in closer:
                recorded = recording_frames(self.app, tile["camera"], tile["t"] - 3, tile["t"] + 3)
                if recorded:
                    for shot in spread(recorded, 4):
                        more.append({"camera": tile["camera"], "tile": tile["tile"], "frame": tile["frame"],
                                     "t": shot["t"], "episode": tile["episode"], "picture": shot["picture"]})
                    continue
                for frame in spread(tile["episode"], 4):
                    key = (tile["tile"], frame["name"])
                    if key not in self.seen and frame is not tile["frame"]:
                        self.seen.add(key)
                        more.append({"camera": tile["camera"], "tile": tile["tile"], "frame": frame, "t": frame["t"],
                                     "episode": tile["episode"]})
            queue = more + queue
            if self.enough(hits, sure):
                # A sheet shows each moment small. The best hits are judged at full size before the search
                # stops. If none holds, it goes on, so a false hit does not end it.
                kept = self.confirmed(hits)
                if kept or self.verified >= MAX_VERIFY_TOTAL:
                    self.kept = kept
                    break
                hits, sure = [], 1
        if queue and self.kept is None:
            self.step(f"Stopped after {self.sheets} looks, with {len(queue)} moments not yet seen")
        self.job["unseen"] = len(queue)
        return hits

    def enough(self, hits, sure):
        if not hits:
            return False
        if self.plan["answer_as"] in ("count", "how_long") or self.plan["which"] == "all":
            return False
        return sure >= 2

    def judge(self, tiles):
        sheet = contact_sheet(tiles)
        claim = self.plan["claim"] or self.job["question"]
        prompt = (f"Each numbered picture is one camera at one moment, with its camera and time in its corner.\n"
                  f"Claim: {claim}\n"
                  "hits: the numbers of the pictures where you can clearly see that the claim is true.\n"
                  "closer: the numbers where it may be true but the thing is too small, blurred, dark or cut off "
                  "to be sure, so a closer look could tell.\n"
                  "sure: 3 if you are certain about the hits, 2 if fairly sure, 1 if you are guessing.\n"
                  "notes: for each hit, one short line on what you see, such as who or what and where. "
                  "Do not name or identify people; describe them, such as a man in a white shirt.")
        answer, cost = self.app.client.structured(prompt, SHEET_SCHEMA, frames=[sheet], name="sheet", max_tokens=2000)
        self.asks.spend(self.job, cost)
        return answer

    # ----- answer -----

    def confirmed(self, hits):
        """A sheet shows each moment small, and Spark can see a figure in leaves. The best hit of each kept
        occurrence is judged alone, at full size, before Whenever says it happened."""
        hits.sort(key=lambda h: h["t"])
        occurrences = group(hits)
        if self.plan["which"] == "last":
            order = list(reversed(occurrences))
        else:
            order = list(occurrences)
        kept, checked = [], 0
        claim = self.plan["claim"] or self.job["question"]
        for occ in order:
            if checked >= MAX_VERIFY or self.verified >= MAX_VERIFY_TOTAL:
                kept.append(occ)
                continue
            best = occ[len(occ) // 2]
            picture = best.get("picture") or self.footage.picture(best["frame"], best["tile"])
            if picture is None:
                continue
            verdict, cost = self.app.client.verify(claim, jpeg(ImageOps.contain(picture, (1440, 1440))))
            self.asks.spend(self.job, cost)
            checked += 1
            self.verified += 1
            if verdict["passed"] and verdict["confidence"] >= 0.6:
                best["note"] = verdict["seen"][:200] or best.get("note", "")
                best["sure"] = verdict["confidence"]
                kept.append(occ)
                if self.plan["which"] in ("last", "first") and self.plan["answer_as"] not in ("count", "how_long"):
                    break
            else:
                self.step(f"A closer look at {hms(best['t'])} did not confirm it: {verdict['seen'][:90]}")
                self.rejected.append({"at": hms(best["t"]), "camera": best["camera"], "seen": verdict["seen"][:240]})
        kept.sort(key=lambda o: o[0]["t"])
        return kept

    def answer(self, hits, frames):
        occurrences = self.kept if self.kept is not None else self.confirmed(hits)
        if self.plan["which"] == "last":
            occurrences = occurrences[-1:] if self.plan["answer_as"] not in ("count",) else occurrences
        elif self.plan["which"] == "first":
            occurrences = occurrences[:1]
        moments = []
        for index, occ in enumerate(occurrences[:8]):
            best = occ[len(occ) // 2]
            picture = best.get("picture") or self.footage.picture(best["frame"], best["tile"])
            name = self.asks.keep(self.job, f"m{index}.jpg", jpeg(picture)) if picture else None
            moment = {"t": occ[0]["t"], "end": occ[-1]["t"], "camera": best["camera"],
                      "picture": self.asks.url(self.job, name) if name else None, "note": best.get("note", "")}
            if index == 0:
                clip = self.clip(best["camera"], best["tile"], occ[0]["t"], occ[-1]["t"], frames)
                if clip:
                    moment["clip"] = self.asks.url(self.job, self.asks.keep(self.job, "clip.mp4", clip))
                    moment["clip_start"] = self.clip_start
            moments.append(moment)
        unseen = self.job.get("unseen", 0)
        facts = {"span": [hms(self.plan["since"]), hms(self.plan["until"])], "cameras": list(self.tiles),
                 "looked_at": len(self.job.get("seen", ())) or self.looked, "not_seen": unseen,
                 "found": [{"from": hms(o[0]["t"]), "to": hms(o[-1]["t"]), "camera": o[0]["camera"],
                            "note": next((h.get("note") for h in o if h.get("note")), "")} for o in occurrences[:20]],
                 "count": len(occurrences), "rejected_looks": self.rejected[:5]}
        words, sure = self.asks.word(self.job, facts, moments)
        more = {"unseen": unseen, "more_usd": None, "more_moments": 0}
        if occurrences:
            # How sure: the full-size look, not the model's view of its own words.
            sure = min(sure, max(h.get("sure", 0.6) for o in occurrences for h in o))
        elif unseen:
            sure = min(sure, 0.5)
            # Nothing yet, and the rest is not seen: the person can pay to see it.
            sheets = min(MAX_SHEETS, (unseen + SHEET_SIZE - 1) // SHEET_SIZE)
            more["more_usd"] = round(sheets * SHEET_USD + MAX_VERIFY * VERIFY_USD, 3)
            more["more_moments"] = min(unseen, sheets * SHEET_SIZE)
        return self.asks.finish(self.job, words, sure, moments, looked=facts["looked_at"], **more)

    def clip(self, camera, tile, start, end, frames):
        """A clip of the moment: the camera's own video when the recording still has it, or else its frames."""
        start, end = start - CLIP_S[0], max(end, start) + CLIP_S[1]
        clip = recording_clip(self.app, camera, start, end)
        if clip:
            self.clip_start = round(start, 2)   # the page draws boxes over the clip from this time
            return clip
        chosen = [f for f in frames if start <= f["t"] <= end][:120]
        self.clip_start = round(chosen[0]["t"], 2) if chosen else None
        return frames_clip(self.footage, chosen, tile)


def moment_key(item):
    return item["tile"], item["frame"]["name"], round(item["t"], 1)


PLURAL = {"people", "children", "men", "women", "police", "cattle", "geese", "mice", "feet"}


def photo_of(thing):
    """The words PE-Core was trained on: "a photo of a person", "a photo of two cars"."""
    words = " ".join(str(thing).split())
    first = words.split()[0].lower() if words else ""
    if (first in ("a", "an", "the", "some", "any", "my", "our", "his", "her", "their", "two", "three", "four")
            or first.isdigit() or words.lower() in PLURAL or (words.endswith("s") and not words.endswith("ss"))):
        return f"a photo of {words}"
    return f"a photo of {'an' if first[:1] in 'aeiou' else 'a'} {words}"


def moment_scores(queue, index, text):
    """For each queued moment: how much more it looks like the words than its camera usually does around
    then, or None when the index has no row near it."""
    import numpy as np
    likeness = {tile: vectors @ text for tile, (times, vectors) in index.items()}
    every = np.concatenate(list(likeness.values())) if likeness else np.zeros(0)
    overall = float(np.median(every)) if len(every) else 0.0
    scores = []
    for item in queue:
        times, _ = index.get(item["tile"], (None, None))
        if times is None or not len(times):
            scores.append(None)
            continue
        near = int(np.argmin(np.abs(times - item["t"])))
        if abs(times[near] - item["t"]) > NEAR_S:
            scores.append(None)
            continue
        low, high = np.searchsorted(times, [item["t"] - USUAL_S, item["t"] + USUAL_S])
        around = likeness[item["tile"]][low:high]
        usual = float(np.median(around)) if len(around) >= 5 else overall
        scores.append(float(likeness[item["tile"]][near]) - usual)
    return scores


def coverage_order(items, since, until):
    """Spread the first looks over the whole span, then fill in: each pass takes one item from each bin."""
    if not items:
        return []
    bins = max(1, min(len(items), FREE_SHEETS * SHEET_SIZE // 2))
    width = max(1.0, (until - since) / bins)
    buckets = {}
    for item in sorted(items, key=lambda i: -len(i["episode"])):
        buckets.setdefault(int((item["t"] - since) // width), []).append(item)
    ordered = []
    while buckets:
        for key in sorted(list(buckets)):
            ordered.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    return ordered


def group(hits, gap=60.0):
    """Hits close in time in one camera are one occurrence."""
    found = []
    for hit in hits:
        if found and hit["camera"] == found[-1][-1]["camera"] and hit["t"] - found[-1][-1]["t"] <= gap:
            found[-1].append(hit)
        else:
            found.append([hit])
    return found


def recording_clip(app, camera, start, end):
    path = app.streams.get(camera) if isinstance(app.streams, dict) else None
    playback = getattr(app, "playback_url", "")
    if not playback or not path or time.time() - start > app.recording_minutes() * 60 - 5:
        return None
    query = urlencode({"path": path, "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                       "duration": f"{max(1.0, min(end, time.time() - 1.5) - start):.1f}", "format": "mp4"})
    try:
        with urllib.request.urlopen(f"{playback}/get?{query}", timeout=30) as reply:
            raw = reply.read()
    except (urllib.error.URLError, OSError):
        return None
    if len(raw) < 1000 or not shutil.which("ffmpeg"):
        return None
    with tempfile.TemporaryDirectory() as work:
        source, out = Path(work) / "in.mp4", Path(work) / "out.mp4"
        source.write_bytes(raw)
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(source), "-an", "-vf", "scale='min(1280,iw)':-2",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                               "-movflags", "+faststart", str(out)], capture_output=True, timeout=180)
        return out.read_bytes() if not done.returncode and out.exists() else None


def recording_frames(app, camera, start, end, fps=RECORDING_FPS):
    """Frames from the full recording, for a closer look than the saved frames give. [] if gone."""
    path = app.streams.get(camera) if isinstance(app.streams, dict) else None
    playback = getattr(app, "playback_url", "")
    if not playback or not path or time.time() - start > app.recording_minutes() * 60 - 5 or not shutil.which("ffmpeg"):
        return []
    query = urlencode({"path": path, "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                       "duration": f"{max(1.0, end - start):.1f}", "format": "mp4"})
    try:
        with urllib.request.urlopen(f"{playback}/get?{query}", timeout=30) as reply:
            raw = reply.read()
    except (urllib.error.URLError, OSError):
        return []
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "in.mp4"
        source.write_bytes(raw)
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(source), "-vf",
                               f"fps={fps},scale='min(1440,iw)':-2", "-q:v", "4", str(Path(work) / "%04d.jpg")],
                              capture_output=True, timeout=180)
        if done.returncode:
            return []
        found = []
        for index, file in enumerate(sorted(Path(work).glob("*.jpg"))):
            found.append({"t": start + index / fps, "picture": Image.open(file).convert("RGB")})
        return found


def frames_clip(footage, frames, tile):
    if len(frames) < 3 or not shutil.which("ffmpeg"):
        return None
    with tempfile.TemporaryDirectory() as work:
        size, count = None, 0
        for frame in frames:
            picture = footage.picture(frame, tile)
            if picture is None:
                continue
            picture = ImageOps.contain(picture, (1280, 720))
            size = size or (picture.width // 2 * 2, picture.height // 2 * 2)
            picture.resize(size).save(Path(work) / f"{count:04d}.jpg", quality=88)
            count += 1
        if count < 3:
            return None
        span = max(0.5, frames[-1]["t"] - frames[0]["t"])
        rate = max(2, min(8, round(count / span)))
        out = Path(work) / "clip.mp4"
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(rate), "-i", str(Path(work) / "%04d.jpg"),
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                              capture_output=True, timeout=120)
        return out.read_bytes() if not done.returncode and out.exists() else None


# ----- "When did this arrive?" -----

def region(picture, box):
    """The pointed place in a picture, from 0 to 1, as a small edge map: lights change it less than pixels."""
    x1, y1, x2, y2 = box
    width, height = picture.size
    pad_x, pad_y = (x2 - x1) * 0.1, (y2 - y1) * 0.1
    crop = picture.crop((max(0, round((x1 - pad_x) * width)), max(0, round((y1 - pad_y) * height)),
                         min(width, round((x2 + pad_x) * width)), min(height, round((y2 + pad_y) * height))))
    # Evened-out brightness, so the switch between colour and infrared at dusk changes it little.
    grey = ImageOps.equalize(ImageOps.grayscale(crop).resize((64, 64), Image.BILINEAR))
    edges = grey.filter(ImageFilter.FIND_EDGES)
    return list(grey.tobytes()), list(edges.tobytes())


def likeness(a, b):
    """1 when two places look the same, near 0 when they do not. Each part is its own normalized correlation."""
    def ncc(x, y):
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        dx = [v - mx for v in x]
        dy = [v - my for v in y]
        top = sum(p * q for p, q in zip(dx, dy))
        bottom = (sum(p * p for p in dx) * sum(q * q for q in dy)) ** 0.5
        return top / bottom if bottom else 0.0
    return 0.4 * ncc(a[0], b[0]) + 0.6 * ncc(a[1], b[1])


class Since:
    """When a pointed thing arrived: step back until its place looks different, then halve the gap."""

    def __init__(self, search):
        self.s = search
        self.app = search.app
        self.plan = search.plan
        self.job = search.job

    def run(self):
        thing = self.job["context"].get("thing") or {}
        point = self.app.points.get(thing.get("id")) if thing.get("id") else None
        camera = thing.get("camera") or (point or {}).get("camera")
        if not point or camera not in self.app.cameras:
            return self.s.asks.finish(self.job, "Point at the thing again, so Whenever knows where it is.", 0.0, [],
                                      unclear=True)
        if self.app.camera_kind(camera) == "moving":
            self.s.step(f"{camera} moves, so Whenever looks for the {self.plan['thing']} by its look instead")
            self.plan["kind"], self.plan["which"] = "past", "first"
            self.plan["claim"] = self.plan["claim"] or f"The {self.plan['thing']} is in view"
            return Search(self.s.asks, self.job).run()
        tile = self.app.cameras.index(camera)
        box = point["view_box"]
        box = (box["x1"] / box["w"], box["y1"] / box["h"], box["x2"] / box["w"], box["y2"] / box["h"])
        now_picture = Image.open(BytesIO(point.get("view") or point["jpeg"])).convert("RGB")
        reference = list(region(now_picture, box))
        oldest = self.app.oldest_footage() or (time.time() - 3600)
        frames = self.s.footage.frames(max(oldest, self.plan["since"]), time.time())
        self.job["span"] = [frames[0]["t"] if frames else oldest, time.time()]
        if len(frames) < 2:
            return self.s.asks.finish(self.job, "There is not enough saved footage to look back.", 0.0, [])
        self.s.step(f"Comparing the place of the {self.plan['thing']} in {len(frames)} saved frames, newest first")
        scores = {}

        def score(index):
            if index not in scores:
                picture = self.s.footage.picture(frames[index], tile)
                scores[index] = likeness(reference, region(picture, box)) if picture else None
            return scores[index]

        newest = len(frames) - 1
        rounds = 0
        while True:
            rounds += 1
            found = self.change_before(frames, newest, score)
            if found is None:
                when = frames[0]["t"]
                facts = {"thing": self.plan["thing"], "here_since_at_least": hms(when),
                         "oldest_footage": hms(frames[0]["t"]), "note": "It was already there in the oldest saved footage."}
                moments = [self.moment(frames[0], tile, box, "m0.jpg", f"Already there at {hms(when)}")]
                words, sure = self.s.asks.word(self.job, facts, moments)
                return self.s.asks.finish(self.job, words, sure, moments, looked=len(scores))
            low, high = found
            before, after = frames[low], frames[high]
            self.s.step(f"The place changed between {hms(before['t'])} and {hms(after['t'])}; "
                        f"compared {len(scores)} frames for free")
            confirmed = self.confirm(before, after, tile, box)
            # The thing was already there: what changed was something else, such as a light. Look further back,
            # comparing with how the place looked just before that change.
            if confirmed.get("first_has_it") and rounds < 3 and low > 0:
                self.s.step("It was already there before that change, so Whenever looks further back")
                reference[:] = region(self.s.footage.picture(before, tile), box)
                scores.clear()
                newest = low
                continue
            break
        moments = [self.moment(after, tile, box, "m0.jpg", f"There at {hms(after['t'])}"),
                   self.moment(before, tile, box, "m1.jpg",
                               f"{'Already there' if confirmed.get('first_has_it') else 'Not there yet'} at {hms(before['t'])}")]
        clip = self.s.clip(self.app.cameras[tile], tile, before["t"], after["t"], frames)
        if clip:
            moments[0]["clip"] = self.s.asks.url(self.job, self.s.asks.keep(self.job, "clip.mp4", clip))
            moments[0]["clip_start"] = self.s.clip_start
        facts = {"thing": self.plan["thing"], "camera": self.app.cameras[tile], "arrived_between":
                 [hms(before["t"]), hms(after["t"])], "spark_confirms": confirmed,
                 "frames_compared": len(scores)}
        words, sure = self.s.asks.word(self.job, facts, moments)
        if confirmed.get("first_has_it") is False and confirmed.get("second_has_it") is True:
            sure = max(sure, 0.85)
        elif confirmed:
            sure = min(sure, 0.5)
        return self.s.asks.finish(self.job, words, sure, moments, looked=len(scores))

    def change_before(self, frames, newest, score):
        """Steps back from frame newest, 1, 2, 4, 8 minutes and more, until the place looks different, then
        halves the gap. Returns (last index that differs, first index that looks the same), or None."""
        threshold = self.threshold(newest, score)
        step_s, last_same, first_diff = 60.0, newest, None
        t_now = frames[newest]["t"]
        while True:
            index = nearest(frames, t_now - step_s)
            value = score(index)
            if value is not None and value < threshold:
                first_diff = index
                break
            if value is not None:
                last_same = index
            if index == 0:
                return None
            step_s *= 2
        low, high = first_diff, last_same
        while high - low > 1:
            middle = (low + high) // 2
            value = score(middle)
            if value is None or value >= threshold:
                high = middle
            else:
                low = middle
        return low, high

    def threshold(self, newest, score):
        """How different the place must look to count as changed: below how much the recent frames vary."""
        recent = [score(i) for i in range(max(0, newest - 6), newest + 1)]
        recent = [v for v in recent if v is not None]
        floor = min(recent) if recent else 0.8
        return max(0.35, min(0.75, floor - 0.2))

    def confirm(self, before, after, tile, box):
        pictures = []
        for frame in (before, after):
            picture = self.s.footage.picture(frame, tile)
            if picture is None:
                return {}
            pictures.append(marked(picture, box))
        pair = Image.new("RGB", (pictures[0].width + pictures[1].width + 8, max(p.height for p in pictures)), (16, 16, 16))
        pair.paste(pictures[0], (0, 0))
        pair.paste(pictures[1], (pictures[0].width + 8, 0))
        pair = ImageOps.contain(pair, (1600, 700))
        prompt = (f"Two pictures of the same camera, earlier on the left and later on the right. A yellow box marks a "
                  f"place. Is the {self.plan['thing']} in the box in the first picture? In the second? note: one short "
                  "line on what changed.")
        answer, cost = self.app.client.structured(prompt, PAIR_SCHEMA, frames=[jpeg(pair)], name="pair", max_tokens=1200)
        self.s.asks.spend(self.job, cost)
        self.s.step(f"Spark compared the two frames: {answer['note']}")
        return answer

    def moment(self, frame, tile, box, name, note):
        picture = self.s.footage.picture(frame, tile)
        url = self.s.asks.url(self.job, self.s.asks.keep(self.job, name, jpeg(marked(picture, box)))) if picture else None
        return {"t": frame["t"], "camera": self.app.cameras[tile], "picture": url, "note": note}


def nearest(frames, t):
    low, high = 0, len(frames) - 1
    if t <= frames[0]["t"]:
        return 0
    while high - low > 1:
        middle = (low + high) // 2
        if frames[middle]["t"] <= t:
            low = middle
        else:
            high = middle
    return low


def marked(picture, box):
    picture = ImageOps.contain(picture, (960, 540)).copy()
    draw = ImageDraw.Draw(picture)
    x1, y1, x2, y2 = box
    draw.rectangle((x1 * picture.width, y1 * picture.height, x2 * picture.width, y2 * picture.height),
                   outline=(255, 214, 0), width=4)
    return picture
