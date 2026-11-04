"""Turns raw sensor results into clean events, and chooses which frames a model sees.

There is no network or camera code here. All logic after this point lives in each rule's
own code, which Muse Wonder writes. Wonder also chooses the timing for each sensor.
"""

# One odd frame must not start an occurrence.
HITS_TO_SET = 2

# A burst of movement ends where movement falls below this share of its peak.
BURST_SHARE = 0.2
BURST_MIN = 3


class SensorState:
    """Follows one sensor over time.

    A "state" lasts, such as drinking or an open door. It starts on a hit, and it ends when
    no hit arrives for clear_after_s.

    A "moment" is over quickly, such as a slap or a clap. Each hit is one occurrence, and
    hits closer than clear_after_s are the same occurrence.
    """

    def __init__(self, pattern="state", clear_after_s=3.0):
        self.pattern = pattern
        self.clear_after_s = clear_after_s
        self.on = False
        self.on_since = 0.0
        self.streak = 0
        self.last_hit = None
        self.last_t = None
        self.in_moment = False

    def observe(self, t, hit, strong=False, hold=False):
        """Feed one result. Returns "on", "off", "moment" or None.

        A strong hit starts a moment alone, because a moment can last for one frame. A state
        lasts, so it needs two checks in a row on new frames: the same frames asked twice repeat
        the same mistake, such as headlights that make a closed gate look open.
        hold keeps a state on, because a running review may still prove that it goes on. It
        holds for one more clear time at most, so a slow review cannot keep a state on.
        """
        # A late result can arrive after a newer one.
        if self.last_t is not None and t <= self.last_t:
            return None
        self.last_t = t

        cleared = self.last_hit is not None and t - self.last_hit >= self.clear_after_s
        if cleared:
            self.in_moment = False

        if not hit:
            self.streak = 0
            held = hold and t - self.last_hit < 2 * self.clear_after_s
            if self.on and cleared and not held:
                self.on = False
                return "off"
            return None

        self.streak += 1
        self.last_hit = t
        ready = (strong and self.pattern == "moment") or self.streak >= HITS_TO_SET

        if self.pattern == "moment":
            if ready and not self.in_moment:
                self.in_moment = True
                return "moment"
            return None

        if not self.on and ready:
            self.on = True
            self.on_since = t
            return "on"
        return None

    def _seen_until(self, t):
        self.last_hit = t if self.last_hit is None else max(self.last_hit, t)

    def late_hit(self, t, until=None):
        """A proof from a review. Reviews run beside the checks, so it can follow newer checks.

        t is the frame that best proves it, and until is the newest frame that still shows it.
        """
        self._seen_until(t if until is None else until)
        if self.pattern == "moment":
            if self.in_moment:
                return None
            self.in_moment = True
            return "moment"
        if self.on:
            return None
        self.on = True
        self.on_since = t
        return "on"

    def next_subject(self, t, until=None):
        """A new person or thing took over this state: one occurrence ends, and another starts."""
        self._seen_until(t if until is None else until)
        self.on_since = t
        return "next"


def spread(frames, count):
    if len(frames) <= count:
        return list(frames)
    if count == 1:
        return [frames[-1]]
    gap = (len(frames) - 1) / (count - 1)
    return [frames[round(i * gap)] for i in range(count)]


def movement_burst(frames):
    """The stretch of connected movement around the biggest movement."""
    peak = max(range(len(frames)), key=lambda i: frames[i].motion)
    quiet = max(BURST_MIN, frames[peak].motion * BURST_SHARE)
    first = last = peak
    while first > 0 and frames[first - 1].motion >= quiet:
        first -= 1
    while last < len(frames) - 1 and frames[last + 1].motion >= quiet:
        last += 1
    return frames[first:last + 1]


def pick_frames(frames, count, pattern):
    """Chooses the frames a model sees from all the frames since the last check.

    A state is spread evenly. A moment is spread across its burst of movement. The peak
    alone is not enough: the biggest movement can be the hand swinging away after contact.
    Each frame needs t and motion.
    """
    if len(frames) <= count:
        return list(frames)
    if pattern == "moment":
        burst = movement_burst(frames)
        if len(burst) >= count:
            return spread(burst, count)
        # A short burst keeps its neighbours, because contact can sit at its edge.
        centre = frames.index(burst[len(burst) // 2])
        start = min(max(0, centre - count // 2), len(frames) - count)
        return list(frames[start:start + count])
    return spread(frames, count)
