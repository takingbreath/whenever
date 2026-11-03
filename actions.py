"""The fixed list of things a rule can do on this Mac.

A rule names an action from this list. It can never pass a command.
"""

import subprocess

CHIME = "/System/Library/Sounds/Glass.aiff"

NOTIFY_SCRIPT = [
    "-e", "on run argv",
    "-e", 'display notification (item 1 of argv) with title "Whenever"',
    "-e", "end run",
]


def run(action):
    kind = action["action"]
    text = action.get("text") or "Rule fired"
    if kind == "notify":
        subprocess.Popen(["osascript", *NOTIFY_SCRIPT, "--", text])
        subprocess.Popen(["afplay", CHIME])
    elif kind == "say":
        subprocess.Popen(["say", "--", text])
    elif kind == "lock_screen":
        subprocess.Popen(["pmset", "displaysleepnow"])
    else:
        raise ValueError(f"Unknown action: {kind}")
