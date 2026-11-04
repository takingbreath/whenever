# Examples: three generated rules that run with no API call

Each folder holds one rule that Muse Wonder wrote from one English sentence, as the server saved it. Nothing in `rule.py`, `rule.json` or `wonder_test.py` is edited. The folders hold only code and the rule's settings, with no key.

Run every test of every example with one command:

```bash
python3 examples/run_examples.py
```

It makes no model call, needs no network and no key, and installs nothing. It prints a pass or a fail for each test file and for each case, and exits 1 if any test fails. We ran it with Python 3.9 and Python 3.14.

## What each folder holds

| File | What it is |
|---|---|
| `request.txt` | The person's sentence |
| `rule.json` | What Wonder submitted: the sensors with their timing and watch plan, the plan in plain steps, and Wonder's own steps, with each reviewer's finding |
| `rule.py` | The program that Wonder wrote |
| `wonder_test.py` | The test that Wonder wrote with the rule (`test_rule.py` in the rule's folder) |
| `independent_test.py` | An acceptance test whose expectations come from the request alone, not from `rule.py`. It includes cases where the action must not happen |

`run_examples.py` runs `wonder_test.py` as Wonder ran it: in a fresh folder with `rule.py` and a copy of `rule_runtime.py`, as `sandbox.create` sets up a rule's folder. It runs `independent_test.py` from the example's folder.

`harness.py` drives each rule through the real `rule_runtime.Context`. It uses the same path as a live rule: each `ctx.detect`, `ctx.who`, `ctx.check` and `ctx.frames` goes to a server. Here the server is fake. It answers from a recorded scene: which frames exist, what the free detector finds, who each person is, and which frames pass a picture check. The harness then feeds the rule its events and a tick each second, as the worker does, and stamps each action with its time.

The sensor's name in each independent test comes from `rule.json`, because a request names no sensor.

Each rule passed both reviewers on its final version. The steps in each `rule.json` end with a code review that found no fault.

## gate-open

> Tell me when someone opens the gate, with the picture.

**What it shows:** the shortest chain. A state sensor watches the gate that the person pointed at. A check is paid only when that place changes (`wake: "change"`, with a safety check every 600 s). The code alerts at once with `ctx.event.proof`, the frame that the sensor's paid check passed. It records each opening and how long the gate stayed open. Wonder wrote it on 5 November 2026, after the "alert at once" change in its guide.

**Independent cases:**

- One opening sends one alert at once, with the proof frame, and no paid model call comes first.
- Two openings send two alerts, each with its own picture.
- The gate closing sends no alert.

## night-driveway-car

> At night, tell me when a car drives into the driveway.

**What it shows:** a sensor that the free detector wakes, and a time-of-day condition in code.

- The sensor is a moment: "Is a car driving into the driveway". A check is paid only when the number of cars in the Driveway or Driveway at night view changes (`wake: "count"`, `count: ["car"]`). These are the demo's two driveway clips.
- The code keeps night from 19:00 to 05:59. It drops a second report of the same arrival within 2 s. It checks a frame before it attaches it as the alert's picture.
- `rule.json` shows two reviewer findings. A first version used a presence sensor, which cannot see a second car while the first one stays parked. A later version dropped a car that followed 6 s behind.

**Independent cases:**

- A car at 23:00 sends one alert when it drives in, and nothing more while it stays parked for 5 minutes.
- Cars at 10:00 and 13:00 send no alert.
- Cars at 21:30 and 02:30 each send an alert, so night runs past midnight.
- Two cars 20 s apart send two alerts.
- An alert's picture, if it has one, is a frame of that arrival that shows the car.
- A failed model call for the picture does not stop the alert.

**Findings from reading the code.** These are not test failures, because the words do not ask for them:

- **The alert waits for a picture check.** The code runs `ctx.check` on the event's 6 frames before it sends the alert. A real server judges each frame alone, so that is 6 Spark calls before the alert goes out. Wonder wrote this rule on 4 November 2026, before the guide said "Alert at once" (commit `021dc62`). A rule written now would alert first with `ctx.event.proof`.
- **Night lives in the code, not in the sensor.** The sensor has no time window, so its count wake still pays for checks by day when the number of cars changes. The code only drops the daytime alert. We read this from `rule.json` and `server.py`. We did not measure it.
- **The words do not say when night starts.** 19:00 to 06:00 is Wonder's choice. The independent test leaves dusk and dawn out.

## cafe-hand-raise

> When a guest in the cafe raises a hand to call for service, alert the Waiter at once. If no staff member reaches that table within 1 minute, alert the Manager.

**What it shows:** escalation in stages, with a timer and free looks.

- A Spark sensor watches the Cafe view for a seated guest with a hand at or above the head (`wake: "change"`).
- On the event, the free detector with body points finds the guest whose hand is up, and the table they sit at. The Waiter alert goes out at once, with that guest outlined.
- Each call becomes its own job in `ctx.state`, with a deadline 60 s later.
- `on_tick` runs each second. Every 4 s it looks for staff with the free detector and `ctx.who`. A job ends only when `ctx.who` names a person at that table with the role "staff".
- At 60 s with no staff, the Manager gets one alert.
- `rule.json` shows the reviewer's finding on a first version: any person who stood at the table stopped the Manager alert, even a guest.

[docs/architecture.md](../docs/architecture.md) follows this rule through every stage and quotes its timer.

**Independent cases:**

- A raised hand alerts the Waiter at once and names the table. With no staff, the Manager gets one alert 60 to 62 s later, never before 60 s.
- Staff who reach the table at 40 s stop the Manager alert.
- A staff visit of only 6 s also counts, at each of four start times.
- A tagged regular or an untagged person who stands at the table does not count as staff.
- Staff at another table do not count.
- Staff who come after the minute do not undo the Manager alert, and it goes out once only.
- Two tables that call have their own timers.
- A new call after service starts a new Waiter alert and a new minute.
- A guest who raises the hand again in the same minute keeps the first deadline.

**What the fake cannot show:**

- **Who the staff are.** The fake identity service says who each person is. On a real camera, `ctx.who(look=False)` names a person only from a good face or from a track that a face named before. A good face is at least 28 pixels wide, toward the camera, with a detector score of at least 0.85 (`people.py`). A waiter seen from above, from the side or from the back may stay unnamed. Then the Manager gets an alert although staff came. We did not measure how often this happens.
- **A guest whom the detector misses.** If no person with a raised hand is found in the event's frames, the Waiter alert says that the table could not be identified. No staff arrival can then stop the Manager alert.

## What these tests cover, and what they do not

They cover each rule's own program, with the real `rule_runtime.Context`: what it does, when, and what it must not do. The times are seconds of simulated time.

They do not cover:

- **Whether Spark or SAM sees the event.** No sensor runs here. The harness sends the sensor's event directly. The evaluation in [eval/README.md](../eval/README.md) measures detection.
- **What a rule costs.** The watch plans and the paid checks run only on a server. See [eval/cost-results.md](../eval/cost-results.md), and `replay.py` for a watch plan on saved checks.
- **The server's alert layer.** This includes the 90 s repeat filter, the quiet time, the picture cut and the delivery to phones.
- **The sandbox.** `tests/test_sandbox.py` covers it.

## Results

On 5 November 2026, with Python 3.9.6 and Python 3.14.4, all 6 test files passed: 3 of Wonder's tests and 3 independent tests with 17 cases. The run prints two notes:

- The night rule ran 6 paid Spark frame checks before its alert.
- The cafe rule's timer and staff looks made no paid call. They used the free detector and saved places.
