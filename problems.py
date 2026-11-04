"""What went wrong, in groups, so the page can stay quiet and still keep everything.

A person sees only what needs them: a rule that stopped, an action that failed, a sensor that
cannot check for a while. A missed live check is only a note, because the next check looks again.
Activity > Problems shows every group with its technical detail, and how each model answers.
"""

import json
import statistics
import threading
import time
from collections import deque
from pathlib import Path

LEVELS = ("note", "warn", "error")
MAX_GROUPS = 300
# The same problem again within this time joins its group, so a slow hour is one line, not fifty.
GROUP_GAP_S = 15 * 60
HEALTH_S = 3600
MAX_CALLS = 4000
SAVE_EVERY_S = 5


class Problems:
    def __init__(self, path=None, clock=time.time):
        """With no path, the groups live only in memory, as in tests."""
        self.path = Path(path) if path else None
        self.clock = clock
        self.lock = threading.Lock()
        self.groups = []
        self.next_id = 1
        self.calls = {}
        self.saved_at = 0.0
        if self.path and self.path.exists():
            try:
                self.groups = json.loads(self.path.read_text())[-MAX_GROUPS:]
                self.next_id = max((g["id"] for g in self.groups), default=0) + 1
            except (OSError, ValueError, KeyError, TypeError):
                self.groups = []

    def _save(self, now, force=False):
        if not self.path or (not force and now - self.saved_at < SAVE_EVERY_S):
            return
        self.saved_at = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.groups, ensure_ascii=False))
        except OSError:
            pass

    def add(self, key, title, level="note", source="", detail="", hint="", rule=None, t=None):
        """Returns (group, fresh). A repeat of an open or recent group only counts again."""
        now = t or self.clock()
        level = level if level in LEVELS else "note"
        with self.lock:
            group = next((g for g in reversed(self.groups)
                          if g["key"] == key and (g["open"] or now - g["last"] < GROUP_GAP_S)), None)
            fresh = group is None
            if fresh:
                group = {"id": self.next_id, "key": key, "level": level, "source": source, "title": title,
                         "detail": "", "hint": hint, "rule": getattr(rule, "name", ""),
                         "rule_id": getattr(rule, "id", ""), "first": now, "last": now, "count": 0,
                         "open": False, "resolved": None}
                self.next_id += 1
                self.groups.append(group)
                del self.groups[:-MAX_GROUPS]
            if LEVELS.index(level) > LEVELS.index(group["level"]):
                group["level"] = level
            group.update(title=title, last=now, count=group["count"] + 1, resolved=None,
                         detail=str(detail)[:2000] or group["detail"], hint=hint or group["hint"])
            group["open"] = group["level"] != "note"
            self._save(now, force=fresh or level == "error")
            return dict(group), fresh

    def resolve(self, key, t=None):
        """The trouble passed, such as a model that answers again. The group stays in the log."""
        now = t or self.clock()
        with self.lock:
            done = [g for g in self.groups if g["key"] == key and g["open"]]
            for group in done:
                group.update(open=False, resolved=now)
            if done:
                self._save(now, force=True)
        return bool(done)

    def dismiss(self, group_id):
        """The person read it. It stays in the log."""
        with self.lock:
            group = next((g for g in self.groups if g["id"] == group_id), None)
            if group and group["open"]:
                group.update(open=False, resolved=self.clock())
                self._save(self.clock(), force=True)
            return bool(group)

    def call(self, model, seconds=None, error=None, t=None):
        """One model call for a live check, so the health table can say how each model answers."""
        now = t or self.clock()
        with self.lock:
            calls = self.calls.setdefault(model, deque(maxlen=MAX_CALLS))
            calls.append((now, seconds, error))

    def health(self):
        now = self.clock()
        rows = []
        with self.lock:
            for model, calls in self.calls.items():
                recent = [c for c in calls if now - c[0] <= HEALTH_S]
                if not recent:
                    continue
                times = sorted(c[1] for c in recent if c[1] is not None)
                failed = [c for c in recent if c[2]]
                rows.append({
                    "model": model, "calls": len(recent), "failed": len(failed),
                    "median_s": round(statistics.median(times), 2) if times else None,
                    "p90_s": round(times[int(len(times) * 0.9)], 2) if times else None,
                    "slowest_s": round(times[-1], 2) if times else None,
                    "last_error": failed[-1][2] if failed else "",
                    "last_error_t": failed[-1][0] if failed else None,
                })
        return rows

    def open(self):
        with self.lock:
            return [dict(g) for g in self.groups if g["open"]]

    def public(self, limit=MAX_GROUPS):
        with self.lock:
            groups = [dict(g) for g in reversed(self.groups[-limit:])]
        return {"groups": groups, "health": self.health()}

    def flush(self):
        with self.lock:
            self._save(self.clock(), force=True)
