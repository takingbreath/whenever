"""Moves a camera's lens and works its light, for rules and for the page.

A rule's words never reach a command. Each command is a fixed list, this file checks each argument,
and no shell runs. A camera name comes from the reader, so a rule can only use a real camera. Each
command is one short process, because the camera's library has its own Python.
"""

import re
import select
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMAND = [str(ROOT / ".ezviz" / "bin" / "python"), str(ROOT / "ezviz_control.py")]
SERIAL = re.compile(r"[A-Z0-9]{6,20}")
DIRECTIONS = ("left", "right", "up", "down")
MIN_MOVE_S = 0.2
MAX_MOVE_S = 4.0
MIN_LIGHT_S = 1.0
MAX_LIGHT_S = 60.0
COMMAND_LIMIT_S = 15


class ControlError(ValueError):
    """A camera cannot do this, or it did not answer."""


class Controls:
    def __init__(self):
        self.cameras = {}
        self.lights = {}
        self.turns = {}

    def set_cameras(self, details):
        """details lists what each camera can do. A camera with a bad serial cannot be controlled."""
        found = [item for item in details if SERIAL.fullmatch(str(item.get("serial")))]
        self.cameras = {str(item["name"]): {
            "serial": item["serial"], "moves": bool(item.get("moves")), "light": bool(item.get("light")),
            "guide": str(item.get("guide") or ""),
        } for item in found}
        self.turns = {camera["serial"]: threading.Lock() for camera in self.cameras.values()}

    def public(self):
        """What the page needs. The serial stays here."""
        return [{"name": name, "moves": camera["moves"], "light": camera["light"],
                 "light_on": self._lit(camera["serial"])} for name, camera in self.cameras.items()]

    def describe(self, names):
        """Tells Wonder what each camera can do."""
        lines = []
        for name in names:
            camera = self.cameras.get(name, {})
            abilities = []
            if camera.get("moves"):
                abilities.append("a moving lens, which sees half the width of " + (camera["guide"] or "a fixed lens"))
            if camera.get("light"):
                abilities.append("it has a light")
            lines.append(f"- {name}" + (": " + ", ".join(abilities) if abilities else ""))
        return "\n".join(lines)

    def _able(self, name, ability):
        camera = self.cameras.get(name)
        if not camera or not camera[ability]:
            able = [item for item, details in self.cameras.items() if details[ability]]
            word = "move" if ability == "moves" else "turn on a light"
            raise ControlError(f"{name} cannot {word}. These can: {', '.join(able) or 'none'}.")
        return camera

    def guide(self, name):
        """The fixed lens whose picture gives a moving lens its points."""
        guide = self._able(name, "moves")["guide"]
        if not guide:
            raise ControlError(f"{name} has no fixed lens beside it.")
        return guide

    def _run(self, *args):
        """Runs one command and waits for it. One device does one thing at a time."""
        with self.turns[args[1]]:
            try:
                done = subprocess.run([*COMMAND, *args], cwd=ROOT, capture_output=True, timeout=COMMAND_LIMIT_S)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise ControlError("The camera did not answer.") from e
        if done.returncode:
            raise ControlError("The camera did not take the command.")

    def _start_light(self, serial, seconds):
        """Starts the process that keeps the time. It stays alive if the server stops."""
        process = subprocess.Popen([*COMMAND, "light", serial, "on", f"{seconds:.1f}"], cwd=ROOT, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        ready, _, _ = select.select([process.stdout], [], [], COMMAND_LIMIT_S)
        if not ready or process.stdout.readline().strip() != "on":
            process.kill()
            raise ControlError("The camera did not turn its light on.")
        return process

    def aim(self, name, x, y):
        """Turns a moving lens to centre on a point in its fixed lens's picture."""
        camera = self._able(name, "moves")
        x, y = float(x), float(y)
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ControlError("x and y must be from 0 to 1.")
        self._run("aim", camera["serial"], f"{x:.3f}", f"{y:.3f}")

    def move(self, name, direction, seconds=1.0):
        camera = self._able(name, "moves")
        if direction not in DIRECTIONS:
            raise ControlError(f"A camera moves {', '.join(DIRECTIONS)}.")
        self._run("move", camera["serial"], direction, f"{min(max(float(seconds), MIN_MOVE_S), MAX_MOVE_S):.1f}")

    def light(self, name, on=True, seconds=20.0):
        """A new command replaces the old time. The light always has an end."""
        serial = self._able(name, "light")["serial"]
        with self.turns[serial]:
            old = self.lights.pop(serial, None)
            if old:
                old.kill()
                old.wait()
            if on:
                self.lights[serial] = self._start_light(serial, min(max(float(seconds), MIN_LIGHT_S), MAX_LIGHT_S))
        if not on:
            self._run("light", serial, "off")

    def _lit(self, serial):
        process = self.lights.get(serial)
        return bool(process) and process.poll() is None
