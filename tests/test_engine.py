import sys
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import SensorState, pick_frames

Item = namedtuple("Item", "t motion")


def feed(hits, pattern="state", clear=1.0, gap=0.5, strong=False):
    """Feed a list of 0/1 results and return the events with their index."""
    state = SensorState(pattern, clear)
    events = []
    for i, hit in enumerate(hits):
        event = state.observe(i * gap, bool(hit), strong and bool(hit))
        if event:
            events.append((i, event))
    return events


class StateEvents(unittest.TestCase):
    def test_two_hits_turn_on(self):
        self.assertEqual(feed([0, 1, 1]), [(2, "on")])

    def test_one_odd_frame_is_ignored(self):
        self.assertEqual(feed([0, 1, 0, 1, 0, 0]), [])

    def test_held_condition_is_one_occurrence(self):
        self.assertEqual(feed([1] * 30), [(1, "on")])

    def test_it_ends_after_clear_time_with_no_hit(self):
        self.assertEqual(feed([1, 1, 0, 0]), [(1, "on"), (3, "off")])

    def test_a_short_break_does_not_end_it(self):
        self.assertEqual(feed([1, 1, 0, 1, 0, 1], clear=1.0), [(1, "on")])

    def test_clear_time_is_set_per_sensor(self):
        self.assertEqual(feed([1, 1, 0, 0], clear=5.0), [(1, "on")])

    def test_one_strong_hit_is_enough(self):
        self.assertEqual(SensorState().observe(1.0, True, strong=True), "on")

    def test_late_result_is_ignored(self):
        state = SensorState()
        state.observe(2.0, True)
        self.assertIsNone(state.observe(1.0, True))
        self.assertEqual(state.observe(2.5, True), "on")


    def test_a_review_can_start_a_state_after_newer_checks(self):
        state = SensorState("state", 30.0)
        state.observe(10.0, False)
        self.assertEqual(state.late_hit(5.0), "on")
        self.assertEqual(state.on_since, 5.0)
        self.assertIsNone(state.late_hit(6.0))

    def test_a_late_hit_never_moves_the_last_hit_back(self):
        state = SensorState("state", 3.0)
        state.observe(10.0, True, True)
        state.late_hit(5.0)
        self.assertEqual(state.last_hit, 10.0)

    def test_a_late_hit_can_prove_that_the_action_went_on_after_its_best_frame(self):
        state = SensorState("state", 3.0)
        state.observe(1.0, True, True)
        state.late_hit(1.5, until=9.0)
        self.assertIsNone(state.observe(11.0, False))

    def test_a_running_review_holds_a_state_on(self):
        state = SensorState("state", 3.0)
        state.observe(1.0, True, True)
        self.assertIsNone(state.observe(9.0, False, hold=True))
        self.assertEqual(state.observe(10.0, False), "off")

    def test_a_late_hit_counts_one_moment_once(self):
        state = SensorState("moment", 30.0)
        self.assertEqual(state.late_hit(1.0), "moment")
        self.assertIsNone(state.late_hit(2.0))

    def test_a_new_subject_restarts_the_state(self):
        state = SensorState("state", 30.0)
        state.observe(1.0, True, True)
        self.assertEqual(state.next_subject(20.0), "next")
        self.assertEqual(state.on_since, 20.0)
        self.assertTrue(state.on)


class MomentEvents(unittest.TestCase):
    def test_each_separate_hit_counts(self):
        hits = [1, 0, 0, 1, 0, 0, 1]
        events = feed(hits, "moment", clear=1.0, strong=True)
        self.assertEqual(events, [(0, "moment"), (3, "moment"), (6, "moment")])

    def test_hits_inside_the_clear_time_are_one_occurrence(self):
        self.assertEqual(feed([1, 1, 1], "moment", clear=2.0, strong=True), [(0, "moment")])

    def test_a_long_run_of_hits_counts_once(self):
        self.assertEqual(feed([1] * 8, "moment", clear=1.0, strong=True), [(0, "moment")])

    def test_a_weak_hit_needs_a_second_hit(self):
        self.assertEqual(feed([0, 1, 1, 0, 0, 0, 1, 1], "moment", clear=1.0), [(2, "moment"), (7, "moment")])

    def test_a_moment_never_stays_on(self):
        state = SensorState("moment", 1.0)
        state.observe(1.0, True, True)
        self.assertFalse(state.on)


class PickFrames(unittest.TestCase):
    def frames(self, motions):
        return [Item(i * 0.1, m) for i, m in enumerate(motions)]

    def test_few_frames_are_all_kept(self):
        items = self.frames([1, 2])
        self.assertEqual(pick_frames(items, 6, "state"), items)

    def test_a_state_is_spread_evenly(self):
        picked = pick_frames(self.frames([0] * 11), 3, "state")
        self.assertEqual([round(f.t, 1) for f in picked], [0.0, 0.5, 1.0])

    def test_one_state_frame_is_the_newest(self):
        self.assertEqual(pick_frames(self.frames([9, 0, 0]), 1, "state")[0].t, 0.2)

    def test_a_moment_covers_its_whole_burst(self):
        # Contact is early in the burst, and the biggest movement is the hand swinging away.
        motions = [1, 1, 27, 41, 16, 37, 32, 31, 10, 23, 14, 29, 26, 47, 36, 1, 1, 1]
        picked = pick_frames(self.frames(motions), 6, "moment")
        self.assertEqual(len(picked), 6)
        self.assertEqual(picked[0].motion, 27)
        self.assertEqual(picked[-1].motion, 36)
        self.assertEqual(picked, sorted(picked, key=lambda f: f.t))

    def test_a_short_burst_keeps_its_neighbours(self):
        picked = pick_frames(self.frames([0, 0, 0, 0, 0, 30, 0, 0, 0, 0, 0, 0]), 4, "moment")
        self.assertIn(30, [f.motion for f in picked])
        self.assertEqual(len(picked), 4)

    def test_a_moment_at_the_end_stays_in_range(self):
        picked = pick_frames(self.frames([0, 0, 0, 0, 0, 9]), 3, "moment")
        self.assertEqual(picked[-1].motion, 9)
        self.assertEqual(len(picked), 3)


if __name__ == "__main__":
    unittest.main()
