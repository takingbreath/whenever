"""The moment index: one PE-Core embedding for each camera at each kept moment, made as frames arrive.

It costs no model call. A camera that changes is embedded at most once a second, and a still camera once
a minute, so a day of five cameras is a few thousand rows. Ask ranks the saved moments with it: the moments
whose pictures look like the question come first, before any paid look.

Each row is (t, session, frame name, tile, busy, vector). The vector is 512 float16 values, 1 KB.
"""

import os
import queue
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

BUSY_EVERY_S = 1.0
STILL_EVERY_S = 60.0
# A camera's own full frame comes just after the wall frame, so a frame waits this long to be indexed.
SETTLE_S = 2.5
KEEP_DAYS = 14
QUEUE_SIZE = 400
# The index gives way to everything else on the computer: the cameras, the checks, and other services.
NICE = 15
BACKFILL_PAUSE_S = 0.4
# A question that needs moments the index lacks embeds them on this many threads, at the cameras' own
# priority, so the question is quick and the cameras still get their share.
NOW_WORKERS = 3
NOW_NICE = 10


def low_priority():
    """Lowers this thread alone. Linux gives each thread its own priority.

    The idle class runs the thread only on spare CPU, so the camera decoders keep their share. A nice
    value alone still takes about a quarter of the CPU from them when every core is busy.
    """
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE)
    except (AttributeError, OSError):
        pass
    try:
        os.sched_setscheduler(threading.get_native_id(), os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError):
        pass


def camera_priority():
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NOW_NICE)
    except (AttributeError, OSError):
        pass


class Moments:
    def __init__(self, path, embedder, footage_picture, wall, busy_of):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS moments (t REAL, session TEXT, name TEXT, tile INTEGER, "
                            "busy INTEGER, vector BLOB, PRIMARY KEY (session, name, tile))")
            self.db.execute("CREATE INDEX IF NOT EXISTS moments_t ON moments (t)")
            self.db.commit()
        self.embedder = embedder
        # The last pictures that could not be indexed, so a fault is visible and not only skipped.
        self.errors = deque(maxlen=20)
        self.picture = footage_picture
        self.wall = wall
        self.busy_of = busy_of
        self.last = {}
        self.queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None and self.embedder.available():
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self.thread is not None

    def offer(self, frame):
        """A saved frame: {t, name, session}. The index decides which of its cameras to embed."""
        if self.thread is None:
            return
        try:
            self.queue.put_nowait((time.time(), frame))
        except queue.Full:
            pass

    def _tiles(self, frame):
        """The cameras to embed in this frame: a busy one once a second, a still one once a minute."""
        columns, rows = self.wall()
        tiles = []
        for tile in range(max(1, columns * rows)):
            gap = BUSY_EVERY_S if tile in frame["busy"] else STILL_EVERY_S
            if frame["t"] - self.last.get(tile, -1e18) >= gap:
                self.last[tile] = frame["t"]
                tiles.append(tile)
        return tiles

    def _run(self):
        low_priority()
        while not self.stopped.is_set():
            try:
                arrived, frame = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            wait = SETTLE_S - (time.time() - arrived)
            if wait > 0:
                time.sleep(wait)
            try:
                frame = {**frame, "busy": self.busy_of(frame)}
                tiles = self._tiles(frame)
                if not tiles:
                    continue
                self._embed(frame, tiles)
            except Exception as e:  # one bad picture must not stop the index
                self.errors.append(f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {e}")
                continue

    def _embed(self, frame, tiles):
        pictures = [(tile, self.picture(frame, tile)) for tile in tiles]
        pictures = [(tile, p) for tile, p in pictures if p is not None]
        if pictures:
            vectors = self.embedder.images([p for _, p in pictures])
            self._save([(frame, tile, vector) for (tile, _), vector in zip(pictures, vectors)])
        return len(pictures)

    def _save(self, rows):
        """rows: [(frame, tile, vector)]."""
        if not rows:
            return
        with self.lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO moments VALUES (?, ?, ?, ?, ?, ?)",
                [(frame["t"], frame["session"], frame["name"], tile, int(tile in frame["busy"]),
                  vector.astype("float16").tobytes()) for frame, tile, vector in rows])
            self.db.commit()

    def backfill(self, frames):
        """Indexes saved frames from before this run, slowly, beside the live index. The newest go first,
        because most questions are about the last hours."""
        low_priority()
        last = {}
        with self.lock:
            done = {(s, n, t) for s, n, t in self.db.execute("SELECT session, name, tile FROM moments")}
        columns, rows = self.wall()
        for frame in sorted(frames, key=lambda f: -f["t"]):
            if self.stopped.is_set():
                return
            tiles = []
            for tile in range(max(1, columns * rows)):
                gap = BUSY_EVERY_S * 2 if tile in frame["busy"] else STILL_EVERY_S
                if abs(last.get(tile, 1e18) - frame["t"]) >= gap and (frame["session"], frame["name"], tile) not in done:
                    last[tile] = frame["t"]
                    tiles.append(tile)
            if not tiles:
                continue
            try:
                self._embed(frame, tiles)
            except Exception as e:
                self.errors.append(f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {e}")
                continue
            time.sleep(BACKFILL_PAUSE_S)

    def index_now(self, wanted, seconds):
        """Embeds the moments a question needs and the index lacks, [(frame, tile)], in the order given,
        on a few threads, until the time is up. Returns how many it added."""
        from concurrent.futures import ThreadPoolExecutor
        deadline = time.time() + seconds

        def one(pair):
            frame, tile = pair
            if time.time() > deadline:
                return None
            picture = self.picture(frame, tile)
            if picture is None:
                return None
            return frame, tile, self.embedder.images([picture])[0]

        rows, added = [], 0
        with ThreadPoolExecutor(NOW_WORKERS, initializer=camera_priority) as pool:
            for row in pool.map(one, wanted):
                if row:
                    rows.append(row)
                    added += 1
                if len(rows) >= 30:
                    self._save(rows)
                    rows = []
        self._save(rows)
        return added

    def vectors(self, since, until, tiles):
        """{tile: (times, vectors)} for the rows in a span, oldest first: an array of times and an (n, 512) array."""
        import numpy as np
        marks = ",".join("?" * len(tiles)) or "-1"
        with self.lock:
            rows = self.db.execute(f"SELECT t, tile, vector FROM moments WHERE t >= ? AND t <= ? AND tile IN ({marks}) "
                                   f"ORDER BY t", (since, until, *tiles)).fetchall()
        found = {}
        for t, tile, blob in rows:
            found.setdefault(tile, ([], []))
            found[tile][0].append(t)
            found[tile][1].append(np.frombuffer(blob, dtype="float16"))
        return {tile: (np.array(times), np.stack(vectors).astype("float32")) for tile, (times, vectors) in found.items()}

    def trim(self):
        with self.lock:
            self.db.execute("DELETE FROM moments WHERE t < ?", (time.time() - KEEP_DAYS * 86400,))
            self.db.commit()

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM moments").fetchone()[0]
