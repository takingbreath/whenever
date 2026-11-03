# Rule script test (2026-10-30)

This test asks if a coding model can turn one sentence into sensors plus a small script. Sensors only say what is true now. The script does all counting, timing and memory.

**Limit:** the sensor events are simulated and perfect. This test proves the logic only. It does not test any camera, microphone or sensor words.

| Model | Rules correct | Time per rule | Cost per rule |
|---|---|---|---|
| Muse Wonder 1.0 | 8 of 8 | 6 to 51 s | About $0.024 |
| Muse Spark 1.3 | 5 of 8 on strict scoring, 8 of 8 on logic | 5 to 9 s | About $0.006 |

Spark's 3 strict failures were not logic errors. Two scripts acted when the event ended, 3 s later than our limit. One script used both actions at once, a notification and speech.

The rules cover every 5th time, count to 3, long hold, sight then sound within 10 s, customer wait, a missing step, 6 times in 10 minutes, and time of day.

Scripts ran with no imports, a short list of builtins, and no network or file writes. See `run_rules.py`.

Wonder wrote "a door" as the sensor for "the door stays open". That word would fail on a real camera, and this test cannot catch it.

## Run

```bash
python3 write_rules.py /path/to/output muse-wonder-1.0 muse-spark-1.3
python3 run_rules.py /path/to/output/muse-wonder-1.0.json
```
