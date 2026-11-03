"""Runs model-written scripts on simulated events, with no imports and a short list of builtins."""

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES

SLACK_S = 2
TAIL_S = 120
SAFE = {name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
        for name in ("len", "min", "max", "sum", "abs", "round", "int", "float", "str", "bool", "list",
                     "dict", "set", "tuple", "range", "sorted", "enumerate", "any", "all")}
BANNED = {"open", "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr",
          "globals", "locals", "vars", "input", "type", "object", "breakpoint"}


def check(code):
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global)):
            raise ValueError("banned statement")
        if isinstance(node, ast.Name) and (node.id in BANNED or node.id.startswith("_")):
            raise ValueError(f"banned name {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"banned attribute {node.attr}")
    return tree


class Api:
    def __init__(self):
        self.t = 0
        self.actions = []

    def notify(self, text):
        self.actions.append((self.t, "notify", str(text)[:80]))

    def say(self, text):
        self.actions.append((self.t, "say", str(text)[:80]))


def map_roles(case, sensors):
    mapping = {}
    for role, pattern in case["roles"].items():
        by_id = [s for s in sensors if re.search(pattern, s["id"], re.I)]
        by_text = [s for s in sensors if re.search(pattern, s["look_for"] + " " + s["question"], re.I)]
        found = by_id or by_text
        if found:
            mapping[role] = found[0]["id"]
    return mapping


def simulate(case, program):
    check(program["code"])
    scope = {"__builtins__": SAFE}
    exec(compile(program["code"], "rule", "exec"), scope)
    mapping = map_roles(case, program["sensors"])
    missing = set(case["roles"]) - set(mapping)
    if missing:
        return False, f"no sensor for {missing}", []
    api, state, since = Api(), {}, {}
    by_time = {}
    for t, role, kind in case["events"]:
        by_time.setdefault(t, []).append((role, kind))
    start = min(by_time)
    for t in range(start, max(by_time) + TAIL_S):
        api.t = t
        for role, kind in by_time.get(t, []):
            sensor = mapping[role]
            if kind == "on":
                since[sensor] = t
                if "on_event" in scope:
                    scope["on_event"](sensor, t, state, api)
            elif "on_end" in scope:
                scope["on_end"](sensor, t, t - since.pop(sensor, t), state, api)
        if "on_tick" in scope:
            scope["on_tick"](t, state, api)
    times = [a[0] for a in api.actions]
    ok = len(times) == len(case["want"]) and all(abs(a - b) <= SLACK_S for a, b in zip(times, case["want"]))
    return ok, f"acted at {times}, wanted {case['want']}", api.actions


for path in sys.argv[1:]:
    programs = {p["name"]: p for p in json.loads(Path(path).read_text())}
    passed = 0
    print("\n===", Path(path).stem)
    for case in CASES:
        program = programs[case["name"]]
        try:
            ok, detail, actions = simulate(case, program)
        except Exception as e:
            ok, detail = False, f"ERROR {type(e).__name__}: {str(e)[:120]}"
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} {case['name']}: {detail}")
    print(f"passed {passed} of {len(CASES)}")
