"""Ask: a person asks about the past or the present, and Whenever answers with proof.

A Whenever watches for something from now on. Ask looks back. One question goes through three steps:

1. Plan. Spark reads the question, the place it was asked from (a camera, a pointed thing, an alert
   card), and what Whenever already knows: its rules, their journal and the cameras. It says what kind
   of question this is, which cameras and which time span it is about, the thing to find, the claim
   that one frame must prove, and how fast the thing passes through the picture.
2. Find. A question that a rule already counted is answered from the journal, exactly and free. A
   question about now looks at the live picture. A question about the past searches the footage:
   see search.py.
3. Answer. Spark words one sentence from what was found. The answer keeps its proof: the moments, a
   clip, how sure it is and what it cost. The person can turn it into a Whenever.
"""

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

# The server's packages live in vendor/, as for meta_api.
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from PIL import Image  # noqa: E402

# The person's own clock, as for schedules. A hosted server's clock is often UTC.
ZONE = ZoneInfo(os.environ["WHENEVER_TZ"]) if os.environ.get("WHENEVER_TZ") else datetime.now().astimezone().tzinfo
ASKS_DIR_NAME = "asks"
MAX_QUESTION = 400
MAX_ASKS_KEPT = 300
# What one question may spend without asking first. A bigger search shows its price.
ASK_FREE_USD = 0.15
ASK_MAX_USD = 1.00
MAX_MOMENTS = 12
KINDS = ("journal", "now", "past", "since")
ANSWER_AS = ("when", "whether", "count", "how_long", "who", "what", "since_when")
PACES = ("fast", "medium", "slow")

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "rule", "event", "cameras", "since_minutes_ago", "until_minutes_ago", "thing",
                 "claim", "pace", "answer_as", "which", "watch", "unclear"],
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "rule": {"type": "string"},
        "event": {"type": "string"},
        "cameras": {"type": "array", "items": {"type": "string"}},
        "since_minutes_ago": {"type": "number"},
        "until_minutes_ago": {"type": "number"},
        "thing": {"type": "string"},
        "claim": {"type": "string"},
        "pace": {"type": "string", "enum": list(PACES)},
        "answer_as": {"type": "string", "enum": list(ANSWER_AS)},
        "which": {"type": "string", "enum": ["first", "last", "all"]},
        "watch": {"type": "string"},
        "unclear": {"type": "string"},
    },
}

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["answer", "sure"],
    "properties": {"answer": {"type": "string"}, "sure": {"type": "number"}},
}


def clock(t):
    return datetime.fromtimestamp(t, ZONE).strftime("%H:%M")


def day_clock(t, now=None):
    now = now or time.time()
    when = datetime.fromtimestamp(t, ZONE)
    today = datetime.fromtimestamp(now, ZONE).date()
    if when.date() == today:
        return when.strftime("%H:%M")
    if (today - when.date()).days == 1:
        return "yesterday " + when.strftime("%H:%M")
    return when.strftime("%d %b %H:%M")


def span_words(since, until, now=None):
    """A time span in words: "yesterday", "today until 14:05", or "yesterday 12:28 to 00:24"."""
    now = now or time.time()
    start, end = datetime.fromtimestamp(since, ZONE), datetime.fromtimestamp(until, ZONE)
    today = datetime.fromtimestamp(now, ZONE).date()
    midnight = lambda d: d.hour == 0 and d.minute == 0
    if midnight(start) and midnight(end) and (end.date() - start.date()).days == 1:
        return "yesterday" if end.date() == today else start.strftime("%A %d %B")
    if midnight(start) and start.date() == today:
        return f"today until {end:%H:%M}"
    return f"{day_clock(since, now)} to {day_clock(until, now)}"


class Asks:
    """Each question is a job: the page polls it while it works, and the answer stays in a file."""

    def __init__(self, app, folder):
        self.app = app
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs = {}

    # ----- jobs -----

    def start(self, question, context=None):
        question = " ".join(str(question or "").split())[:MAX_QUESTION]
        if not question:
            raise ValueError("Type a question first.")
        if self.app.stopped():
            raise ValueError("Whenever is paused for today: the model budget is used up.")
        job = {"id": uuid.uuid4().hex[:10], "question": question, "context": clean_context(context),
               "status": "working", "steps": [], "t": time.time(), "cost": 0.0}
        with self.lock:
            self.jobs[job["id"]] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job["id"]

    def go(self, job_id):
        """The person agreed to the price of a bigger search."""
        job = self.jobs.get(job_id) or self._resume(job_id)
        rest = job and job["status"] == "done" and job.get("more_usd")
        if not job or (job["status"] != "needs" and not rest):
            raise ValueError("This search cannot go on any more. Ask the question again.")
        if rest:
            # "Look at the rest": the moments already seen are skipped, so they are not paid for twice.
            job["approved_usd"] = job.pop("more_usd")
            job.update(answer=None, moments=[], unseen=0)
            self.step(job, "Looking at the rest")
        else:
            job["approved_usd"] = job.get("estimate_usd", ASK_MAX_USD)
        job["status"] = "working"
        threading.Thread(target=self._run, args=(job, True), daemon=True).start()
        return job_id

    def _resume(self, job_id):
        """A question that offered more looks before a restart: its plan and the moments it saw are saved."""
        if not re.fullmatch(r"[0-9a-f]{10}", str(job_id)):
            return None
        try:
            kept = json.loads((self.folder / job_id / "resume.json").read_text())
            answer = json.loads((self.folder / job_id / "answer.json").read_text())
        except (OSError, ValueError):
            return None
        job = {**answer, **kept, "seen": {tuple(item) for item in kept.get("seen", [])}}
        with self.lock:
            return self.jobs.setdefault(job_id, job)

    def get(self, job_id):
        job = self.jobs.get(job_id)
        if job:
            return public(job)
        return self.saved(job_id)

    def saved(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{10}", str(job_id)):
            return None
        path = self.folder / job_id / "answer.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def history(self, limit=40):
        found = []
        for path in sorted(self.folder.glob("*/answer.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                found.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return found

    def file(self, job_id, name):
        """A picture or clip of an answer, for the page."""
        if not re.fullmatch(r"[0-9a-f]{10}", str(job_id)) or not re.fullmatch(r"[\w.-]{1,80}\.(jpg|mp4)", str(name)):
            return None
        path = self.folder / job_id / name
        return path if path.is_file() else None

    def step(self, job, text):
        job["steps"].append(str(text)[:200])

    def spend(self, job, cost, sam=0):
        job["cost"] = round(job["cost"] + cost + sam * self.app.price_see, 5)
        with self.app.lock:
            self.app.spark_usd += cost
            self.app.see_calls += sam

    def _run(self, job, resumed=False):
        try:
            if not resumed:
                job["plan"] = self.plan(job)
                self.step(job, understood(job["plan"]))
                if job["plan"]["unclear"]:
                    return self.finish(job, job["plan"]["unclear"], 0.0, [], unclear=True)
            plan = job["plan"]
            if plan["kind"] == "journal":
                return self.from_journal(job, plan)
            if plan["kind"] == "now":
                return self.now(job, plan)
            from search import Search
            return Search(self, job).run()
        except Exception as e:  # an answer must always end, so the page never waits forever
            job["status"] = "error"
            slow = "did not answer" in str(e) or "cut off" in str(e) or "connection" in str(e).lower()
            job["error"] = ("Meta's model did not answer in time. Ask again in a moment." if slow
                            else "Whenever could not answer this. Try asking it another way.")
            self.step(job, "Stopped: " + str(e)[:160])
            self.app.problems.add(f"ask:{type(e).__name__}", "A question could not be answered", "note", "Ask",
                                  str(e)[:300], "Ask again. If it keeps failing, the log shows where it stopped.")

    # ----- plan -----

    def plan(self, job):
        app = self.app
        now = time.time()
        context = job["context"]
        cameras = [f"- {name} ({app.camera_kind(name) or 'fixed'})" for name in app.cameras]
        rules = []
        for rule in list(app.entries.values()):
            sensors = "; ".join(f"{s.id}: {s.target or s.question}"
                                + (" (wakes only when its rule arms it, so its record is not complete)"
                                   if s.armed_until is not None else " (always on)") for s in rule.sensors)
            rules.append(f"- {rule.name}: \"{getattr(rule, 'sentence', '')}\". Sensors: {sensors}. Watches: "
                         f"{', '.join(app.focus_cameras(rule) or app.watched(rule)) or 'every camera'}.")
        oldest = app.oldest_footage()
        place = []
        if context.get("cameras"):
            place.append(f"The person chose these cameras: {', '.join(context['cameras'])}.")
        if context.get("thing"):
            place.append(f"The person pointed at {context['thing']['noun'] or 'a thing'} in {context['thing']['camera']}.")
        if context.get("occurrence"):
            occ = context["occurrence"]
            place.append(f"The person asks about one event of \"{occ.get('rule')}\": from {day_clock(occ['t'], now)}"
                         + (f" to {day_clock(occ['end'], now)}" if occ.get("end") else "") + ".")
        if context.get("since_minutes_ago") is not None:
            place.append(f"The person chose the time span: the last {context['since_minutes_ago']:.0f} minutes.")
        prompt = (
            "You plan how Whenever answers a person's question about their cameras. Whenever keeps the journal "
            "of its rules (each event with its time and how long it lasted, and each fact a rule recorded), "
            f"saved frames back to {day_clock(oldest, now) if oldest else 'now'}, and the full video of the last "
            f"{app.recording_minutes():.0f} minutes.\n"
            f"Now: {datetime.fromtimestamp(now, ZONE):%A %d %B %Y, %H:%M}.\n"
            "Cameras:\n" + "\n".join(cameras) + "\n"
            "Rules:\n" + ("\n".join(rules) or "- none") + "\n"
            f"Journal:\n{app.journal.summary() or '- empty'}\n"
            + ("Where the question was asked: " + " ".join(place) + "\n" if place else "")
            + f"Question: {job['question']}\n\n"
            "Decide:\n"
            "- kind: journal only if an always-on sensor or a record of a rule answers it exactly, such as how many "
            "times the gate opened, or when it last opened. A sensor that wakes only when its rule arms it saw only "
            "some moments, so a question about it, such as when a person was last at the gate, is past. now if it is "
            "about this moment; since if it asks when a pointed thing arrived, left or changed; past for anything "
            "else that needs the footage.\n"
            "- rule and event: for journal, the rule's name and the sensor or record name, exactly as listed. "
            "Otherwise empty.\n"
            "- cameras: the cameras that can show it, by their names. Empty means every camera.\n"
            "- since_minutes_ago and until_minutes_ago: the time span, in minutes before now. \"Last night\" "
            "is 20:00 to 07:00 of the night before. \"Today\" starts at 00:00. Without a time, use the time the "
            "person chose, or else the whole saved footage. until 0 means now.\n"
            "- thing: one short noun phrase that a detector can find, such as person, white car, parcel, dog. "
            "Empty if the question is not about a thing.\n"
            "- claim: one sentence that a single frame can prove true, such as \"A person stands at the gate\". "
            "It is what Whenever checks in each moment.\n"
            "- pace: fast if the thing crosses the picture in a few seconds, such as a person walking past or a "
            "bird; medium for a person who stops, a car that parks, a door; slow for something that appears and "
            "stays, such as a parcel left, or light changing. This sets how closely Whenever looks.\n"
            "- answer_as: what the answer is: when, whether, count, how_long, who, what or since_when.\n"
            "- which: first, last or all of the moments.\n"
            "- watch: the question turned into a Whenever for the future, in the form \"When ..., then alert "
            "my phone\". Empty if that makes no sense.\n"
            "- unclear: if the question cannot be answered as asked, one short question back to the person. "
            "Otherwise empty.")
        answer, cost = app.client.structured(prompt, PLAN_SCHEMA, name="plan", max_tokens=2500)
        self.spend(job, cost)
        return clean_plan(answer, app.cameras, now, context, oldest)

    # ----- answers -----

    def from_journal(self, job, plan):
        app = self.app
        rule = next((r for r in app.entries.values() if r.name == plan["rule"]), None)
        name = plan["event"]
        rows = app.journal.history(rule=plan["rule"], since=plan["since"], until=plan["until"], limit=5000)
        # A sensor named on purpose counts with its steps. Otherwise a step is part of another event.
        named = [r for r in rows if r["kind"] == "event" and r["name"] == name]
        # Spark can name a record, such as gate_opened, where it meant the sensor, gate_open.
        events = named or [r for r in rows if r["kind"] == "event" and not r["values"].get("step")]
        records = [r for r in rows if r["kind"] == "record" and (r["name"] == name or not named)]
        ends = {r["values"]["occurrence"]: r for r in rows if r["kind"] == "end" and r["values"].get("occurrence")}
        facts = {"rule": plan["rule"], "span": [day_clock(plan["since"]), day_clock(plan["until"])],
                 "count": len(events),
                 "times": [day_clock(r["t"]) for r in events][-30:],
                 "lasted_seconds": [round(ends[r["values"]["occurrence"]]["seconds"] or 0)
                                    for r in events if r["values"].get("occurrence") in ends][-30:]
                 if ends else [],
                 "records": [{"t": day_clock(r["t"]), **{k: v for k, v in r["values"].items() if k != "occurrence"}}
                             for r in records][-30:]}
        self.step(job, f"Read {len(events)} events of {plan['rule']} from the journal")
        moments = []
        for event in events[-MAX_MOMENTS:] if plan["which"] != "first" else events[:MAX_MOMENTS]:
            occurrence = event["values"].get("occurrence")
            # The alert's own picture shows only the cameras where it happened; the event's is the whole wall.
            mine = [r for r in rows if occurrence and r["values"].get("occurrence") == occurrence]
            media = next((r["values"] for r in mine if r["values"].get("clip")), None) or \
                next((r["values"] for r in mine if r["kind"] in ("action", "notify") and r["values"].get("picture")), {})
            moments.append({"t": event["t"], "camera": event["values"].get("camera", ""),
                            "picture": media.get("picture") or event["values"].get("picture"),
                            "clip": media.get("clip"), "clip_start": media.get("clip_start"),
                            "note": event["text"], "occurrence": occurrence,
                            "end": (ends.get(occurrence) or {}).get("t")})
        if rule is None and not events:
            return self.finish(job, f"No Whenever named {plan['rule'] or 'that'} has a record of this.", 0.5, [])
        words, sure = self.word(job, facts, moments)
        return self.finish(job, words, sure, moments)

    def now(self, job, plan):
        app = self.app
        picture = job["context"].get("snapshot")
        wall = Image.open(BytesIO(picture)).convert("RGB") if picture else app.newest_wall()
        if wall is None:
            return self.finish(job, "There is no picture from the cameras right now.", 0.0, [])
        views = app.camera_pictures(wall, plan["cameras"])
        jpeg = app.labelled(views)
        answer, cost = app.client.look(job["question"], [jpeg])
        self.spend(job, cost)
        name = self.keep(job, "now.jpg", jpeg)
        moment = {"t": time.time(), "camera": ", ".join(plan["cameras"]) or "all cameras",
                  "picture": self.url(job, name), "note": answer["answer"]}
        return self.finish(job, answer["answer"], answer["confidence"], [moment])

    def word(self, job, facts, moments):
        """One sentence that answers the question from what was found. It names times and says how sure."""
        prompt = (f"A person asked about their cameras: {job['question']}\n"
                  f"Now: {datetime.now(ZONE):%A %H:%M}. Here is what Whenever found, as data:\n"
                  f"{json.dumps(facts, ensure_ascii=False, default=str)[:6000]}\n"
                  "Answer in one or two short plain sentences, with times as HH:MM. Say only what the data "
                  "shows. If it found nothing, say so and say what span it looked at. If not_seen is more than 0, "
            "it found nothing only in the moments it looked at: say how many it looked at and how many it did "
            "not see, such as: I looked at 54 moments from 12:28 to 00:44 and saw no person; 990 are not "
            "seen yet. rejected_looks are moments that a full-size look did not confirm. If one of them shows "
            "the asked thing in another place or another way, such as a dog in the yard and not at the gate, add "
            "one short sentence about it with its time. Set sure from 0 to 1.")
        answer, cost = self.app.client.structured(prompt, ANSWER_SCHEMA, name="answer", max_tokens=1500)
        self.spend(job, cost)
        return str(answer["answer"])[:400], min(max(float(answer["sure"]), 0.0), 1.0)

    def keep(self, job, name, data):
        folder = self.folder / job["id"]
        folder.mkdir(exist_ok=True)
        (folder / name).write_bytes(data)
        return name

    def url(self, job, name):
        return f"/ask-file/{job['id']}/{name}"

    def finish(self, job, words, sure, moments, unclear=False, **extra):
        done = dict(status="done", answer=words, sure=round(float(sure), 2), moments=moments[:MAX_MOMENTS],
                    unclear=unclear, done_t=time.time(), **extra)
        result = public({**job, **done})
        self.keep(job, "answer.json", json.dumps(result, ensure_ascii=False).encode())
        if extra.get("more_usd"):
            # "Look at more" works after a restart too.
            context = {k: v for k, v in (job.get("context") or {}).items() if k != "snapshot"}
            kept = {"question": job["question"], "context": context, "plan": job.get("plan"), "cost": job["cost"],
                    "steps": job["steps"], "seen": sorted(job.get("seen", ()), key=str)}
            self.keep(job, "resume.json", json.dumps(kept, ensure_ascii=False, default=list).encode())
        else:
            (self.folder / job["id"] / "resume.json").unlink(missing_ok=True)
        try:
            self.app.journal.add("ask", "", "Ask", job["plan"].get("kind", "") if job.get("plan") else "",
                                 text=words[:300], values={"ask": job["id"], "question": job["question"][:200],
                                                           "pictures": [m["picture"] for m in moments if m.get("picture")][:4],
                                                           "sure": result["sure"], "cost": result["cost"]})
        except ValueError:
            pass
        self.app.add_feed(None, "answer", words, ask=job["id"], question=job["question"])
        self._trim()
        # Last, so whoever sees "done" finds every file of the answer written.
        job.update(done)
        return result

    def _trim(self):
        folders = sorted((p for p in self.folder.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for old in folders[:-MAX_ASKS_KEPT]:
            for file in old.iterdir():
                file.unlink(missing_ok=True)
            old.rmdir()


def clean_context(context):
    context = dict(context or {})
    cameras = [str(c)[:40] for c in context.get("cameras") or []][:8]
    thing = context.get("thing") if isinstance(context.get("thing"), dict) else None
    occurrence = context.get("occurrence") if isinstance(context.get("occurrence"), dict) else None
    since = context.get("since_minutes_ago")
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), bytes) else None
    return {
        "cameras": cameras,
        "thing": {"id": str(thing.get("id") or "")[:20], "noun": str(thing.get("noun") or "")[:60],
                  "camera": str(thing.get("camera") or "")[:40]} if thing else None,
        "occurrence": {"id": str(occurrence.get("id") or "")[:60], "rule": str(occurrence.get("rule") or "")[:60],
                       "t": float(occurrence.get("t") or 0), "end": float(occurrence.get("end") or 0) or None}
        if occurrence and occurrence.get("t") else None,
        "since_minutes_ago": float(since) if isinstance(since, (int, float)) and 0 < since <= 7 * 24 * 60 else None,
        "snapshot": snapshot,
    }


def clean_plan(plan, cameras, now, context, oldest):
    """The model's plan, held to real cameras and a real time span."""
    named = [c for c in plan.get("cameras") or [] if c in cameras]
    if not named and context.get("cameras"):
        named = [c for c in context["cameras"] if c in cameras]
    if context.get("thing") and context["thing"]["camera"] in cameras and context["thing"]["camera"] not in named:
        named = [context["thing"]["camera"], *named]
    since = now - max(0.0, float(plan.get("since_minutes_ago") or 0)) * 60
    until = now - max(0.0, float(plan.get("until_minutes_ago") or 0)) * 60
    occurrence = context.get("occurrence")
    if occurrence and plan.get("kind") != "journal":
        # A question about one alert looks at that alert's time, with a little before and after.
        since, until = occurrence["t"] - 60, (occurrence.get("end") or occurrence["t"]) + 120
    if not plan.get("since_minutes_ago") and not occurrence:
        since = oldest or now - 3600
    since, until = min(since, until), max(since, until)
    if plan.get("kind") == "now":
        since = until = now
    thing = context.get("thing")
    kind = plan.get("kind") if plan.get("kind") in KINDS else "past"
    if kind == "since" and not thing:
        kind = "past"
    return {
        "kind": kind, "rule": str(plan.get("rule") or "")[:60], "event": str(plan.get("event") or "")[:60],
        "cameras": named, "since": since, "until": until,
        "thing": str((thing or {}).get("noun") or plan.get("thing") or "")[:60],
        "claim": str(plan.get("claim") or "")[:300],
        "pace": plan.get("pace") if plan.get("pace") in PACES else "medium",
        "answer_as": plan.get("answer_as") if plan.get("answer_as") in ANSWER_AS else "what",
        "which": plan.get("which") if plan.get("which") in ("first", "last", "all") else "all",
        "watch": str(plan.get("watch") or "")[:300], "unclear": str(plan.get("unclear") or "")[:300],
    }


def understood(plan):
    """The plan in the person's words, as the first step they see."""
    where = ", ".join(plan["cameras"]) or "every camera"
    span = "now" if plan["kind"] == "now" else span_words(plan["since"], plan["until"])
    if plan["kind"] == "journal":
        return f"Reading the journal of {plan['rule']}, {span}"
    if plan["kind"] == "now":
        return f"Looking at {where} now"
    if plan["kind"] == "since":
        return f"Finding when the {plan['thing']} came, in {where}"
    return f"Searching {where}, {span}, for: {plan['claim'] or plan['thing']}"


def public(job):
    """What the page sees. The snapshot and the plan's private parts stay here."""
    keep = ("id", "question", "status", "steps", "t", "answer", "sure", "moments", "cost", "error", "unclear",
            "estimate_usd", "looked", "span", "done_t", "unseen", "more_usd", "more_moments")
    result = {k: job[k] for k in keep if k in job}
    plan = job.get("plan") or {}
    result["kind"] = plan.get("kind", "")
    result["watch"] = plan.get("watch", "")
    result["cameras"] = plan.get("cameras", [])
    result["context"] = {k: v for k, v in (job.get("context") or {}).items() if k != "snapshot"}
    return result
