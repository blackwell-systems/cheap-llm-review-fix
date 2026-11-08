#!/usr/bin/env python3
"""Run every finding of a fix batch through `model_fix.py`, one after
another in one worktree, then print the batch status.

The batch is review-format JSON, usually written by
`merge_reviews.py --batch-out` (only confirmed findings: see the README).
Each fix that passes its tests is committed in the worktree; the lead then
reads all the diffs in one pass and merges once. Watch a running batch with
`fix_status.py <out-dir>`.

  fix_batch.py batch.json --worktree ../myproj-fixes --model z-ai/glm-5.3-flash \
      --provider z-ai --test-cmd "pytest {test_file}" --rules examples/rules_python.md \
      --out-dir fixes
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", help="review-format JSON of confirmed findings")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--strict-schema", action="store_true", help="passed on (use for MiMo on DeepInfra)")
    parser.add_argument("--test-cmd", help="test command template passed to model_fix.py "
                        "(default there: ./run_tests.sh {tests})")
    parser.add_argument("--rules", help="project rules file passed to model_fix.py")
    parser.add_argument("--tries", type=int, help="attempts per finding (model_fix.py default: 2)")
    parser.add_argument("--only", default="", help="finding numbers to run (from 1), comma separated")
    args = parser.parse_args()

    with open(args.batch, encoding="utf-8") as handle:
        findings = json.load(handle)["findings"]
    wanted = {int(item) for item in args.only.split(",") if item.strip()}
    for number, finding in enumerate(findings, 1):
        if wanted and number not in wanted:
            continue
        if not finding.get("test_file"):
            print(f"finding {number} ({finding['title']}): skipped, no test_file")
            continue
        cmd = [sys.executable, os.path.join(HERE, "model_fix.py"), "--finding", f"{args.batch}:{number}",
               "--worktree", args.worktree, "--model", args.model, "--out-dir", args.out_dir]
        for provider in args.provider:
            cmd += ["--provider", provider]
        if args.strict_schema:
            cmd.append("--strict-schema")
        if args.test_cmd:
            cmd += ["--test-cmd", args.test_cmd]
        if args.rules:
            cmd += ["--rules", args.rules]
        if args.tries:
            cmd += ["--tries", str(args.tries)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print((result.stdout.strip().splitlines() or [result.stderr.strip()[-300:]])[-1], flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, "fix_status.py"), args.out_dir])


if __name__ == "__main__":
    main()
