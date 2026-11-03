"""One record of everything that every rule saw and did. It stays across restarts.

The server writes each event, each action and each fact that a rule records. A report is then a
rule that reads this record, so no rule needs its own report code. The journal belongs to the
person, not to one rule, so a weekly report can use what all of their rules saw.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOURNAL_FILE = ROOT / "data" / "journal.db"
KINDS = ("event", "end", "record", "action", "notify", "say", "draw", "image", "log", "error")
MAX_ROWS = 5000
MAX_TEXT = 300
MAX_DATA_BYTES = 2000
MAX_SUMMARY = 30


class Journal:
    def __init__(self, path=None):
        """With no path, the journal lives only in memory, as in tests."""
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, t REAL NOT NULL, "
                "rule_id TEXT, rule TEXT, kind TEXT NOT NULL, name TEXT, seconds REAL, text TEXT, data TEXT)")
            self.db.execute("CREATE INDEX IF NOT EXISTS by_time ON entries (t)")
            self.db.commit()

    def add(self, kind, rule_id="", rule="", name="", seconds=None, text="", values=None, t=None):
        if kind not in KINDS:
            raise ValueError(f"The journal has no kind {kind}.")
        data = json.dumps(values or {}, ensure_ascii=False, default=str)
        if len(data.encode()) > MAX_DATA_BYTES:
            raise ValueError(f"A record can hold {MAX_DATA_BYTES} bytes.")
        with self.lock:
            self.db.execute(
                "INSERT INTO entries (t, rule_id, rule, kind, name, seconds, text, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time() if t is None else float(t), str(rule_id)[:20], str(rule)[:60], kind,
                 str(name or "")[:60], None if seconds is None else float(seconds), str(text or "")[:MAX_TEXT], data))
            self.db.commit()

    def history(self, kind=None, name=None, rule=None, since=None, until=None, limit=MAX_ROWS):
        """The newest rows that match, oldest first. rule is a rule's name."""
        where, args = [], []
        for column, value in (("kind", kind), ("name", name), ("rule", rule)):
            if value:
                where.append(f"{column} = ?")
                args.append(str(value))
        if since is not None:
            where.append("t >= ?")
            args.append(float(since))
        if until is not None:
            where.append("t <= ?")
            args.append(float(until))
        sql = "SELECT t, rule, kind, name, seconds, text, data FROM entries"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t DESC, id DESC LIMIT ?"
        args.append(max(1, min(int(limit), MAX_ROWS)))
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [{"t": t, "rule": rule, "kind": kind, "name": name, "seconds": seconds, "text": text,
                 "values": json.loads(data or "{}")} for t, rule, kind, name, seconds, text, data in reversed(rows)]

    def summary(self):
        """What the journal holds, for Wonder: each rule, kind and name, with the keys of its values."""
        with self.lock:
            rows = self.db.execute(
                "SELECT rule, kind, name, COUNT(*), MIN(t), MAX(t), "
                "(SELECT data FROM entries AS e WHERE e.rule = entries.rule AND e.kind = entries.kind "
                "AND e.name = entries.name ORDER BY t DESC LIMIT 1) "
                "FROM entries WHERE kind IN ('event', 'record', 'action') "
                "GROUP BY rule, kind, name ORDER BY MAX(t) DESC LIMIT ?", (MAX_SUMMARY,)).fetchall()
        lines = []
        for rule, kind, name, count, first, last, data in rows:
            keys = ", ".join(json.loads(data or "{}").keys())
            when = f"{time.strftime('%d %b', time.localtime(first))} to {time.strftime('%d %b', time.localtime(last))}"
            lines.append(f"- rule {rule!r}, kind {kind!r}, name {name!r}: {count} rows, {when}"
                         + (f", values: {keys}" if keys and kind == "record" else ""))
        return "\n".join(lines)
