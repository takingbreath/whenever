"""Gives each rule its own folder, with its own Python and libraries.

Code in the sandbox can write only in its folder. It cannot read the home folder, so it
cannot read the API key. It has no network, except while pip installs libraries.
"""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES_DIR = ROOT / "rules"
HOME = Path.home()
COMMAND_TIMEOUT_S = 60
INSTALL_TIMEOUT_S = 240
MAX_OUTPUT = 6000

PROFILE = """(version 1)
(allow default)
{network}
(deny file-read* (subpath "{home}"))
(allow file-read-metadata)
(deny file-write*)
(allow file-write* (subpath "{folder}") (literal "/dev/null") (literal "/dev/tty"))
(allow file-read* (subpath "{folder}"))
"""


def create(rule_id):
    folder = RULES_DIR / rule_id
    (folder / "data").mkdir(parents=True)
    (folder / "tmp").mkdir()
    venv.create(folder / ".venv", with_pip=True, system_site_packages=False)
    shutil.copy(ROOT / "rule_runtime.py", folder / "rule_runtime.py")
    return folder


def copy_rule(source, rule_id):
    """Makes the folder for an edited rule. The old rule keeps running from its own folder."""
    source = Path(source)
    folder = RULES_DIR / rule_id

    def skip(path, names):
        return ["frames", "tmp", "Library"] if Path(path) == source else []

    shutil.copytree(source, folder, symlinks=True, ignore=skip)
    (folder / "tmp").mkdir()
    shutil.copy(ROOT / "rule_runtime.py", folder / "rule_runtime.py")
    return folder


def wrap(folder, argv, network=False):
    profile = PROFILE.format(
        network="" if network else "(deny network*)", home=HOME, folder=Path(folder).resolve()
    )
    return ["sandbox-exec", "-p", profile, *argv]


def environment(folder):
    folder = Path(folder).resolve()
    return {
        "PATH": f"{folder}/.venv/bin:/usr/bin:/bin",
        "HOME": str(folder),
        "TMPDIR": str(folder / "tmp"),
        "VIRTUAL_ENV": str(folder / ".venv"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "MPLBACKEND": "Agg",
    }


def run(folder, command, timeout=COMMAND_TIMEOUT_S, network=False):
    """Runs a shell command in the rule folder. Returns (exit code, output)."""
    try:
        result = subprocess.run(
            wrap(folder, ["/bin/sh", "-c", command], network),
            cwd=folder, env=environment(folder), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"Stopped after {timeout} s."
    output = (result.stdout + result.stderr)[-MAX_OUTPUT:]
    return result.returncode, output


def install(folder, packages):
    quoted = " ".join("'" + p.replace("'", "") + "'" for p in packages)
    return run(folder, f"python -m pip install --quiet {quoted}", INSTALL_TIMEOUT_S, network=True)


def start_worker(folder):
    return subprocess.Popen(
        wrap(folder, ["python", "rule_runtime.py"]), cwd=folder, env=environment(folder),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
