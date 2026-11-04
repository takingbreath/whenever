"""A basic motion trigger on the evaluation videos: the same change measure and wake level as camera.py
(the largest mean difference of one 8x8 block of a 64x36 grey picture, above 5), at 2 frames a second,
with one alert and then 30 s of quiet. Prints each case's motion alerts as JSON."""
import json, subprocess, sys
W, H, CELL, LEVEL, FPS, QUIET = 64, 36, 8, 5.0, 2, 30.0

def change_level(grey, previous):
    best = 0.0
    for top in range(0, H - CELL + 1, CELL):
        for left in range(0, W - CELL + 1, CELL):
            total = 0
            for y in range(top, top + CELL):
                row = y * W
                total += sum(abs(grey[i] - previous[i]) for i in range(row + left, row + left + CELL))
            best = max(best, total / (CELL * CELL))
    return best

out = {}
for video in sys.argv[1:]:
    timeline = json.load(open(video.replace(".mp4", ".timeline.json")))
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", video, "-vf", f"fps={FPS},scale={W}:{H},format=gray",
                          "-f", "rawvideo", "pipe:1"], capture_output=True, check=True).stdout
    frames = [raw[i:i + W * H] for i in range(0, len(raw) - W * H + 1, W * H)]
    # Each case on its own, as if the camera had shown only that scene: no cut into it, no quiet carried over.
    for case, (start, length) in timeline.items():
        alerts, last, previous = [], -1e9, None
        first, end = int(round(float(start) * FPS)), int(float(start + float(length)) * FPS)
        for n in range(first, min(end, len(frames))):
            t = n / FPS - float(start)
            if previous is not None and change_level(frames[n], previous) > LEVEL and t - last >= QUIET:
                alerts.append(round(t, 1))
                last = t
            previous = frames[n]
        out[case] = alerts
print(json.dumps(out))
