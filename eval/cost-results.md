# Cost results

This page covers model spend only. It does not count the server, the network or the cameras. The detection results are in [README.md](README.md).

Each number below is marked:

- **Measured:** read from the server's own spend counter during a real run.
- **Estimate:** worked out from a measured number or from a fixed price, as shown.

## How the server counts money

The server adds up every paid call, and stops paid calls at the daily limit (`WHENEVER_SPEND_LIMIT`, $5 by default).

- **Muse Spark and Muse Wonder** count their real token counts at the published prices ([meta_api.py:38](../meta_api.py#L38), [meta_api.py:130](../meta_api.py#L130)). Spark is $1.25 for a million input tokens and $4.25 for a million output tokens. Wonder is $4.00 for a million input tokens, $0.20 for cached input and $20.00 for output.
- **SAM 3.1** counts a fixed $0.0025 for each call (`PRICE_SEE`, [server.py:268](../server.py#L268)). The code calls this an estimate from the published prices.

So a "measured" cost is measured token use at published prices. It is not a figure from a Meta invoice.

## One-time and per-use costs

Measured on 5 November 2026 on the demo server, in tests that the owner approved.

| What | Cost | Kind |
|---|---|---|
| Wonder writes a gate rule from scratch | $0.16 | Measured |
| Wonder edits a rule | $0.22 | Measured |
| Wonder writes the cafe hand-raise rule ([examples/cafe-hand-raise](../examples/cafe-hand-raise)) | $0.56 | Measured |
| One Ask answer | $0.006 | Measured |
| One point-and-click | about $0.0025 | Estimate: one SAM call at `PRICE_SEE`. Spark then names the clicked thing, in one more call that this number leaves out |

A job that Wonder does not finish still counts the turns it paid for ([writer.py:1066](../writer.py#L1066)).

**A gap that we found, and fixed.** Until commit `a1d2b0a`, the writer's cost added Wonder's own turns only. It left out the two reviewers' Wonder calls and Spark's checks of a recorded example. So the three Wonder costs above are lower than the real spend. Since `a1d2b0a`, a metered client counts every call of a writing job (`Metered` in [writer.py](../writer.py)). Two rules written after the fix, the demo's gate rule for day and night and its cafe food rule, cost $0.73 together.

## Measured runs on 5 November 2026

| Run | Cost | Kind |
|---|---|---|
| The labelled evaluation: two rules on 10.7 minutes of evaluation video ([README.md](README.md)) | $1.33 | Measured, from the evaluation server's counter, before and after the run |
| Wonder writes the evaluation's hand rule | $0.62 | Measured by the counter of that time, so too low (see the gap above) |
| The demo's four rules, woken on a free count, until each had sent real alerts (about 13 minutes with a page open) | about $2.10 | Measured, from the demo's counter |

## Running costs while a page watches

A rule pays for its sensor's checks while it runs. On the demo, nothing pays after 15 minutes with no page open (`WHENEVER_IDLE_PAUSE_S=900` in [deploy/demo/settings.env.example](../deploy/demo/settings.env.example)).

| Rule | Sensor and wake | Cost for an hour | Kind |
|---|---|---|---|
| A gate rule | Woken on a free count (`wake: "count"`) | about $2.40 | Measured rate. The notes do not give the length of that run |
| The cafe hand-raise rule | A Spark state sensor, woken on a change in the Cafe picture | about $5 | Estimate: a 6-minute run cost $0.48, and 10 such runs make $4.80 |
| Four starter rules, 4 November 2026 | Woken on any change, on looping demo video | about $13 | Measured: $13 in one hour, for 3,387 Spark calls |

Notes on these rows:

- The gate rule in [examples/gate-open](../examples/gate-open) wakes on a change at the pointed gate, not on a count. So the $2.40 row is not a measurement of that file.
- The last row comes to about $0.0038 for each Spark call on average. That is arithmetic on the two measured numbers, $13 divided by 3,387.
- Looping video always "changes", so a change wake paid for almost every check. [docs/deployment-evidence.md](../docs/deployment-evidence.md) says what we changed.

## Paid calls in each example rule's own code

These are counts from `python3 examples/run_examples.py`, not dollars. They cover only each rule's program, after its sensor fires. The sensor's own paid checks come on top.

| Example | Paid calls in the rule's code | From the run's notes |
|---|---|---|
| [gate-open](../examples/gate-open) | 0 for each opening. The alert uses the sensor's proof frame | The independent test checks that no paid call comes before the alert |
| [night-driveway-car](../examples/night-driveway-car) | 6 Spark frame checks for each night arrival, 0 for a daytime car | One `ctx.check` over the event's 6 frames. A real server judges each frame alone |
| [cafe-hand-raise](../examples/cafe-hand-raise) | 0 for the timer and the staff looks | The looks use the free detector and `ctx.who`. SAM finds the tables once, and the rule's folder keeps them |

## The free local models

These cost nothing for each call. They use the server's CPU, which is part of the server's cost, and this page does not count it.

| Model | What it does | Time for one call, from the repo |
|---|---|---|
| YOLOX-S | Finds people, cars, tables and the other COCO kinds | about 50 ms for one camera picture (README) |
| RTMPose-m | Body points, a raised hand, sitting, lying | about 15 ms for each person ([detect.py](../detect.py)) |
| YuNet and ArcFace R50 | Faces for `ctx.who` | about 0.03 s for each face ([rule_runtime.py:546](../rule_runtime.py#L546)) |
| PE-Core-S-16-384, int8 | Embeddings for Ask and for looks | about 0.55 s for one picture on one server core ([embed.py](../embed.py)) |

PE-Core is a Meta model, run here on the CPU. The other three are not Meta models.

## What caps the spend

- A daily limit for each server, `WHENEVER_SPEND_LIMIT`. Every paid call stops at it, Wonder and voice too.
- A shared limit for servers that share a budget, `WHENEVER_SPEND_SHARED`. The demo's example settings give the demo $4 a day, inside $10 a day for both servers. These are the values in the example file, and the live values can differ.
- 60 model calls a minute for each rule ([server.py:124](../server.py#L124)).
- Wonder writes at most 2 rules at once.
