# A small, labelled evaluation

Two Whenevers ran once on 16 short clips through the real pipeline: `camera.py` played each clip as a camera, the server checked it with Muse Spark, and the Wonder-written rule decided and alerted. A basic motion trigger ran on the same clips. We labelled every clip before the run: [cases.json](cases.json) was committed in `b2f9431` at 03:51 IST, and the run started at 04:11 IST on 5 November 2026. (The note inside cases.json says 03:40, the time the file was written.)

**The two requests**
- Gate: "Tell me when someone opens the gate, with the picture."
- Hand: "When someone raises a hand to call for attention, alert the Waiter at once. If nobody comes to them within 1 minute, alert the Manager."

The gate rule is the one on the demo server, written by Wonder from those words. Wonder wrote the hand rule for this run.

## Results

| Measure | What to report | Gate (11 clips) | Hand (5 clips) | Both (16 clips) |
|---|---|---|---|---|
| Correct alerts | Events correctly detected | 6 of 6 | 1 of 2 | 7 of 8 |
| Missed events | Events that should have alerted | 0 | 1 | 1 |
| False alerts | Alerts without the requested event | 1 | 4 | 5 |
| Delay | Time from qualifying event to notification | median 8.3 s (4.7 to 14.3 s) | 14.1 s (one alert) | median 8.3 s |
| Cost | Observed API cost for the evaluated footage | $1.33 for both rules on 10.7 minutes of evaluation video (8.4 min gate, 2.4 min hand, with 6 s gaps between clips), from the server's counters | | |

Writing the hand rule cost $0.62 more, by the counter of that time. That counter left out the two reviewers' calls, so the real writing cost was higher. Commit `a1d2b0a` fixed the counter after the run.

**Sample size.** 16 clips: 8 should alert and 8 should not. The 11 gate clips come from one real camera at the builder's home on 4 November 2026, by day, at dusk and at night. The hand clips are 4 short real webcam clips of the builder at home and 1 AI-made cafe video.

## Each clip

| Clip | What happens | Expected | Whenever | Motion trigger |
|---|---|---|---|---|
| G01 | Day. A man opens one gate leaf to talk to people outside. | alert | alert, 8.1 s | 1 alert, 8 s before the gate opens |
| G02 | Day, sun glare. A man opens the gate and walks back. | alert | alert, 6.9 s | 1 alert, before the gate opens |
| G03 | Day. Two people at the gate; it opens and a woman comes in. | alert | alert, 14.3 s | 1 alert, before the gate opens |
| G04 | Night. A man opens the gate; a car drives in with headlights. | alert | alert, 8.3 s | 1 alert |
| G05 | Night. A man opens the gate; a car drives in with headlights. | alert | alert, 8.7 s, **and a second alert 21 s later for the same opening** | 2 alerts, the first before the gate opens |
| G06 | Night. Red tail-light glow moves over the closed gate. | none | none | 1 false alert |
| G07 | Night. Closed gate, small light changes, nobody. | none | none | 1 false alert |
| G08 | Night. Headlights outside, infrared switches to colour; gate closed. | none | none | 1 false alert |
| G09 | Day. A cyclist passes outside; gate closed. | none | none | 1 false alert |
| G10 | Day, glare. A motorcycle passes outside; gate closed. | none | none | 1 false alert |
| G11 | Dusk. A man opens the gate and walks in; infrared switches on. | alert | alert, 4.7 s | 1 alert |
| H3 | A man holds a phone to his ear. | none | none | 1 false alert |
| H4 | A man rests a hand on his head and scratches it. | none | **2 false alerts** ("hand at eye or forehead level") | 1 false alert |
| H5 | A man raises an open palm above his head for about a second, then stretches and drinks. | alert | **missed** | 1 alert, before the hand goes up |
| H6 | Two people: the man in front raises his arm and waves; the other sits behind. | alert | alert, 14.1 s | 1 alert, before the arm goes up |
| H7 | AI-made cafe: a couple talks, a waiter brings food, a group sits down. | none | **1 false alert** (a man pulling a chair) | 2 false alerts |

One more hand alert came from outside the hand clips. The hand rule was written without a camera name, so it also watched the gate camera, and it alerted when a man there lifted both hands to close the gate (in G04). It counts as a false alert above.

**The escalation.** Wonder's hand rule sent "No one came within 1 minute" twice, 60 s after two of its false alerts. It sent none after the correct alert in H6, where a second person was in view. These clips were too short to label the escalation, so it is not scored. [examples/cafe-hand-raise](../examples/cafe-hand-raise) tests the timer, the cancellation and the escalation of a generated rule on recorded inputs, without API calls.

## The baseline: a basic motion trigger

The motion trigger is the change measure that `camera.py` uses to wake checks: the largest mean difference of one 8 x 8 block in a 64 x 36 grey picture, above 5, at 2 frames a second, with one alert and then 30 s of quiet. Each clip ran on its own. See [motion_baseline.py](motion_baseline.py).

| | Whenever | Motion trigger |
|---|---|---|
| Alerts on the 8 clips where the requested event does not happen | 3 (a hand on the head twice, a man pulling a chair) | 9, with at least 1 on every one of the 8 clips |
| Other false alerts | 2 (a second alert for one gate opening, and the hand rule on the gate camera) | not counted: motion has no rule to break |
| Gate clips with only lighting changes or passers-by (G06 to G10) | 0 alerts | 5 alerts, 1 on each |
| Alerts tied to the requested event on the 8 clips where it happens | 7, each after the event | 6 of the 9 alerts on those clips came before the event, when someone only walked into view |

Motion says that something changed. It cannot tell a gate that opens from a car's lights on a closed gate, a cyclist outside, or a camera that switches to infrared. That is the work that needs a model: Muse Spark answers the rule's question about the frames, and the rule's code decides what to do with the answer.

## Limits

- **Small sample.** 16 clips, from one gate camera and one person at a webcam. The results describe these clips, not accuracy at any site.
- **One run.** Muse Spark's answers can differ between runs. We did not repeat the run.
- **The builder labelled the clips,** from the footage, before the run. No second person checked the labels.
- **The hand rule is weak** on this footage: 4 false alerts and 1 miss in 5 clips. A hand at the head looks like a raised hand in a single frame. The raised palm in H5 lasted about a second at 2 webcam frames a second.
- **Delay** runs from the labelled moment to the alert row. The clock is uncertain by about 1.5 s: the first frame reached the server 1.4 s after the camera reader started.
- **The cafe clip is AI-made,** with people who appear and jump. The gate and webcam clips are real.
- **The footage is not in this repository.** It shows the builder's home. [run-2026-11-05.json](run-2026-11-05.json) holds every alert row of the run, the clip timeline and the motion results, so the scores can be checked.

## Run the scoring again

```bash
python3 eval/score.py eval/run-2026-11-05.json
```

It reads the labels in [cases.json](cases.json) and prints each clip and the summary. The scored output of this run is in [results-2026-11-05.json](results-2026-11-05.json).
