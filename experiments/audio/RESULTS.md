# Audio test results (2026-10-30)

These tests ask if Meta's models can recognize and count sounds that are not speech.

## Part 2: one real cooker whistle

**The real sound differs from our synthetic one.** A real whistle is a wide burst of steam noise, about 3 s long, with most energy high in the range. Our synthetic whistle was a pitched tone. So the Part 1 whistle numbers probably do not apply to a real cooker.

**Limits:** this part uses one real whistle from a 5 s web clip, which is not ours to publish, so it is not in this repo. The 3-whistle and 5-whistle files repeat that one whistle at slightly different pitch and volume. The tap, sizzle and mixer noises are synthetic.

| Route | Real whistle files, exact | Look-alike noises | Notes |
|---|---|---|---|
| Spark 1.2, yes or no | 0 of 12 yes | 0 false yes | Often refuses |
| Spark 1.3, yes or no | 0 of 12 yes | 0 false yes | |
| Spark 1.2, count | 3 of 12 | No false count | Refused on the tap noise |
| Spark 1.3, count | 1 of 12 | 0 on each | Mostly answers 0 |
| Spark 1.3, spectrogram picture | 10 of 12 | Counted 1 on tap, sizzle and mixer | Said 7 to 8 on speech alone |
| Plain code, sound events | 3 of 4 files | Counts every sound | Speech added 2 events |
| Plain code, match to the real whistle | Failed | Sizzle scored 0.97, real copies 0.49 to 0.69 | Cannot tell steam from hiss |
| Voice Transcribe | Empty transcript | | Correct: there is no speech |

In open questions, Spark 1.2 gave "hissing/spraying sound (aerosol spray)" once in 3 tries. It refused the other 2. Spark 1.3 gave "silence", "no audio" and "plastic crinkling".

The yes-or-no and count rows above used the words "pressure cooker whistle". The next table shows that the words matter.

### Wording test

Spark, yes or no, 3 real-whistle files with 3 tries each.

| Words in the question | Spark 1.2 found | Spark 1.3 found |
|---|---|---|
| "a whistle" | 0 of 9 | 0 of 9 |
| "a high-pitched whistle" | 0 of 9 | 0 of 9 |
| "a loud hissing sound" | 6 of 9 | 0 of 9 |
| "the sound of steam escaping" | 9 of 9 | 0 of 9 |

Second run of "the sound of steam escaping" on Spark 1.2:

| File | Said yes | Note |
|---|---|---|
| 3 whistles | 10 of 10 | |
| 3 whistles and speech | 9 of 10 | |
| 5 whistles | 10 of 10 | |
| 1 whistle, 5 s clip | 4 of 10 | 4 refused |
| Sizzle (synthetic) | 7 of 8 | False yes |
| Tap (synthetic) | 1 of 8 | False yes |
| Mixer, speech, bell, room | 0 of 32 | |

Plain code found each event, and Spark 1.2 judged each event 5 times. It gave 28 yes of 60 on single whistles, and the same whistle ranged from 0 of 5 to 5 of 5. It gave 0 yes on speech, tap, mixer and bell events, and 3 of 5 on sizzle. Most-votes counting got only 1 of 3, 2 of 3 and 3 of 5 whistles.

**Conclusion:** Spark 1.2 can recognize this sound as steam, on recordings with several whistles, when the question uses the word "steam". It is weak on one short whistle, and it may confuse steam with sizzling. Spark 1.3 did not work. Counting still needs plain code or a spectrogram picture. All of this comes from one real whistle.

## Live speech

Voice Transcribe on its realtime endpoint, 4 runs each, with the Mac `say` voice. Each row is the delay from the end of the speech to the word on screen.

| Phrase | Word seen | Typical delay | Range |
|---|---|---|---|
| "Dinner is ready" | 4 of 4 | 0.36 s | 0.31 to 0.65 s |
| "Approve this change" | 4 of 4 | 0.05 s | -0.12 to 0.25 s |
| "खाना तैयार है" (Hindi) | 4 of 4 | 0.35 s | 0.03 to 0.59 s |

A partial transcript can show a word just before the audio file reaches the end of the speech. The final turn came about 3.4 s after the speech. It returned empty text on the whistle and bell clips.

## Part 1: synthetic sounds

**Limit:** no test in this part uses a real cooker. The whistle is synthetic, the bell is the Mac Glass sound, the claps are synthetic noise bursts, and the speech is the Mac `say` voice. All whistles come from one generator, so the matching test is easier than real life.

## Results

Whistle count: 5 files with 1, 2, 3, 3 (with speech) and 5 whistles.

| Route | Exact whistle counts | Time | Notes |
|---|---|---|---|
| Spark 1.2, audio file | 11 of 15 | About 3 s | Often says that it cannot hear audio |
| Spark 1.3, audio file | 9 of 15 | About 5 s | Meta's docs say audio is not fully supported in 1.3 |
| Spark 1.2, sound inside mp4 | 2 of 9 | About 3 s | Tested on 3 of the 5 files |
| Spark 1.3, spectrogram picture | 11 of 15 | About 4 s | Reads a shape. It does not name a sound. It said 9 on a file with no whistle. |
| Plain code, sound events | 4 of 5 files | Milliseconds | Counts every sound, so speech adds false events |
| Plain code, match to one example | 4 of 4 files | About 0.05 s per second of sound | The fifth file gave the example. All test whistles are alike. |

Yes or no: "Is this sound heard?"

| Model | Whistle found | Bell found | Claps found | False yes |
|---|---|---|---|---|
| Spark 1.2 | 4 of 10 | 1 of 6 | 2 of 4 | 0 |
| Spark 1.3 | 0 of 10 | 0 of 6 | 0 of 4 | 0 |

Spark naming one sound clip at a time failed. Spark 1.2 mostly refused, and Spark 1.3 mostly said "other".

Muse Voice Transcribe wrote the speech clip exactly, and it returned an empty transcript on the whistle, bell and clap clips. Meta's docs say that it has no sound event detection.

Matching to one example counted whistles and bells correctly. It failed on claps (0 of 3), because a clap is too short for this method.

## Not tested

- More than one real cooker, a real microphone, or a real kitchen.
- Real recordings of a tap, a mixer or frying.
- SAM Audio. It is not on the Meta Model API, so it needs a separate download.

## Run

```bash
python3 experiments/audio/make_sounds.py /path/to/empty/folder
```

Then pass that folder to `test_spark.py`, `test_events.py`, `test_match.py` and `test_spectrogram.py`. `test_real.py` also needs a folder with a real clip as `real.wav`. `test_live_speech.py` needs the `websockets` package. The tests call the paid API, except for the plain-code parts.
