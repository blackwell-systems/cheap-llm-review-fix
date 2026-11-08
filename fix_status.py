#!/usr/bin/env python3
"""One line per fix in a `model_fix.py` batch: what it is doing now
(asking, with the model's thinking and answer progress; applying; testing)
or how it ended (passed, failed, declined), with attempts, time and cost.

  fix_status.py <out-dir> [<out-dir> ...]
"""
import glob
import json
import os
import re
import sys


def finding_names(out_dir: str) -> list[str]:
    names = {re.sub(r"\.(prompt\d+\.md|answer\d+\.json.*|result\.json|status)$", "", os.path.basename(path))
             for path in glob.glob(os.path.join(out_dir, "*"))}
    return sorted(names, key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])


def line(out_dir: str, name: str) -> str:
    result_path = os.path.join(out_dir, f"{name}.result.json")
    if os.path.exists(result_path):
        with open(result_path, encoding="utf-8") as handle:
            result = json.load(handle)
        steps = ", ".join(step.get("result", "?") for step in result["attempts"])
        extra = f": {result['reason'][:120]}" if result.get("reason") else ""
        commit = f" {result['commit']}" if result.get("commit") else ""
        return f"{result['outcome'].upper()}{commit} ({steps}; {result['seconds']} s, ${result['cost']}){extra}"
    status_path = os.path.join(out_dir, f"{name}.status")
    status = open(status_path, encoding="utf-8").read().strip() if os.path.exists(status_path) else "waiting"
    progress = sorted(glob.glob(os.path.join(out_dir, f"{name}.answer*.json.progress")))
    if progress and (status.endswith("asking") or status == "waiting"):
        status += ": " + open(progress[-1], encoding="utf-8").read().strip()
    return status


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for out_dir in sys.argv[1:]:
        print(f"== {out_dir.rstrip('/')}")
        for name in finding_names(out_dir):
            print(f"  {name}: {line(out_dir, name)}")


if __name__ == "__main__":
    main()
