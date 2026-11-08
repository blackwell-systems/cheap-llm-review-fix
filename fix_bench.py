#!/usr/bin/env python3
"""Score a model as a fixer on a fix benchmark.

A bench is a folder with
  findings.json  review-format findings (as `merge_reviews.py --batch-out` writes), shuffled
  key.json       what each finding really is; never shown to the fixer:
                 {"base_commit": "<sha>", "items": [{"n": 1, "kind": "real", "difficulty": "easy",
                  "latent": false, "title": "..."}, ...]}
                 n is the finding's number (from 1) in findings.json; kind is real, fake or
                 debatable; latent marks hardening against misuse (declining it can be sound)
See docs/building-a-bench.md for how to build a good one.

Each run adds a fresh git worktree of --repo at the key's base commit (where the real bugs
exist), runs the whole findings file through `fix_batch.py`, saves each fix's diff and result in
`<bench>/results/<label>/run<N>/`, scores it against the key, appends a line to
`<bench>/scoreboard.md` and removes the worktree.

A real bug counts as fixed when its tests pass; a fake one counts as refused when the model
answers "not a bug". Passing tests don't prove a fix is good: read the saved diffs
(`results/<label>/run<N>/diffs/`) before trusting a score, especially a fake that was "fixed".

  fix_bench.py --bench bench/ --repo ../myproj --label haiku-5.5 --runs 3 \\
      --test-cmd "pytest {test_file}" --rules examples/rules_python.md
  fix_bench.py --bench bench/ --rescore      # rebuild the scoreboard from the saved results
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("real", "fake", "debatable")

HEADER = ("# Fix benchmark scoreboard\n\nReal: definite bugs fixed with tests passing. Latent: hardening "
          "against misuse, where declining can be sound. Fakes refused: answered \"not a bug\"; fakes fixed: "
          "changed correct code. Not run: the finding had no result (error or missing file). Read the "
          "diffs before trusting a score.\n\n| Date | Model | Run | Real fixed (by difficulty) | Latent fixed | "
          "Fakes refused | Fakes fixed | Not run | Time | Cost |\n|---|---|---|---|---|---|---|---|---|---|\n")


def load_key(path: str) -> dict:
    """The key file, checked: a base commit, and items with a number and a kind."""
    with open(path, encoding="utf-8") as handle:
        key = json.load(handle)
    if not key.get("base_commit"):
        raise ValueError(f"{path}: no base_commit")
    seen = set()
    for item in key.get("items", []):
        if item.get("kind") not in KINDS:
            raise ValueError(f"{path}: item {item.get('n')}: kind must be one of {', '.join(KINDS)}")
        if not isinstance(item.get("n"), int) or item["n"] in seen:
            raise ValueError(f"{path}: item numbers must be unique integers (bad: {item.get('n')})")
        seen.add(item["n"])
        item.setdefault("difficulty", "fake" if item["kind"] == "fake" else "unrated")
        item.setdefault("latent", False)
    return key


def score(key: dict, out_dir: str, stem: str = "findings") -> dict:
    """Totals for one run from the result records model_fix wrote
    (`<out_dir>/<stem>-<n>.result.json`)."""
    totals: dict = {"real": 0, "fixed": 0, "latent": 0, "latent_fixed": 0, "fake": 0, "refused": 0,
                    "fake_fixed": 0, "not_run": 0, "seconds": 0, "cost": 0.0, "by_difficulty": {}, "items": [],
                    "errors": []}
    for item in key["items"]:
        path = os.path.join(out_dir, f"{stem}-{item['n']}.result.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                result = json.load(handle)
        else:
            result = {"outcome": "missing"}
        outcome = result["outcome"]
        if outcome in ("missing", "error"):
            totals["not_run"] += 1
            totals["errors"].append(f"{item['n']}: {result.get('reason', outcome)}")
        totals["seconds"] += result.get("seconds", 0)
        totals["cost"] += result.get("cost", 0.0)
        totals["items"].append({"n": item["n"], "kind": item["kind"], "difficulty": item["difficulty"],
                                "latent": item["latent"], "outcome": outcome, "commit": result.get("commit", "")})
        if item["kind"] == "debatable":
            continue
        if item["kind"] == "real" and item["latent"]:
            # Hardening against misuse: declining it can be a sound judgment, so it is scored apart.
            totals["latent"] += 1
            totals["latent_fixed"] += outcome == "passed"
        elif item["kind"] == "real":
            totals["real"] += 1
            level = totals["by_difficulty"].setdefault(item["difficulty"], [0, 0])
            level[1] += 1
            if outcome == "passed":
                totals["fixed"] += 1
                level[0] += 1
        else:
            totals["fake"] += 1
            totals["refused"] += outcome == "declined"
            totals["fake_fixed"] += outcome == "passed"
    totals["cost"] = round(totals["cost"], 4)
    return totals


def scoreboard_line(date: str, label: str, run: int, totals: dict) -> str:
    levels = ", ".join(f"{level} {won}/{total}" for level, (won, total) in sorted(totals["by_difficulty"].items()))
    return (f"| {date} | {label} | {run} | {totals['fixed']}/{totals['real']} ({levels}) | "
            f"{totals['latent_fixed']}/{totals['latent']} | {totals['refused']}/{totals['fake']} | "
            f"{totals['fake_fixed']} | {totals['not_run']} | {totals['seconds']} s | ${totals['cost']} |")


def run_dirs(bench: str) -> list[tuple[str, int, str]]:
    """(label, run number, folder) for every saved run, in order."""
    found = []
    for folder in glob.glob(os.path.join(bench, "results", "*", "run*")):
        match = re.fullmatch(r"run(\d+)", os.path.basename(folder))
        if match and glob.glob(os.path.join(folder, "*.result.json")):
            found.append((os.path.basename(os.path.dirname(folder)), int(match.group(1)), folder))
    return sorted(found)


def rescore(bench: str, key: dict) -> list[str]:
    """Rebuilds scoreboard.md and every score.json from the saved result files
    with the current key. Each run keeps the date of its first scoring."""
    lines = []
    for label, run, folder in run_dirs(bench):
        totals = score(key, folder)
        score_path = os.path.join(folder, "score.json")
        date = None
        if os.path.exists(score_path):
            with open(score_path, encoding="utf-8") as handle:
                date = json.load(handle).get("date")
        totals["date"] = date or datetime.date.fromtimestamp(os.path.getmtime(folder)).isoformat()
        with open(score_path, "w", encoding="utf-8") as handle:
            json.dump(totals, handle, indent=1)
        lines.append(scoreboard_line(totals["date"], label, run, totals))
    with open(os.path.join(bench, "scoreboard.md"), "w", encoding="utf-8") as handle:
        handle.write(HEADER + "\n".join(lines) + ("\n" if lines else ""))
    return lines


def git(*args: str, cwd: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def save_diffs(totals: dict, worktree: str, out_dir: str) -> None:
    """Each committed fix's diff, for the quality review the score can't replace."""
    diffs = os.path.join(out_dir, "diffs")
    os.makedirs(diffs, exist_ok=True)
    for item in totals["items"]:
        if item["commit"]:
            with open(os.path.join(diffs, f"{item['n']:02d}-{item['kind']}.diff"), "w", encoding="utf-8") as handle:
                handle.write(git("show", item["commit"], cwd=worktree))


def batch_command(args: argparse.Namespace, findings: str, worktree: str, out_dir: str) -> list[str]:
    cmd = [sys.executable, os.path.join(HERE, "fix_batch.py"), findings, "--worktree", worktree,
           "--model", args.model, "--out-dir", out_dir]
    for provider in args.provider:
        cmd += ["--provider", provider]
    if args.strict_schema:
        cmd.append("--strict-schema")
    for flag, value in (("--test-cmd", args.test_cmd), ("--rules", args.rules), ("--tries", args.tries),
                        ("--max-tokens", args.max_tokens)):
        if value:
            cmd += [flag, str(value)]
    for path in args.outline:
        cmd += ["--outline", path]
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench", required=True, help="folder with findings.json and key.json (results go here)")
    parser.add_argument("--repo", default=".", help="the git repository the bench is about (default: .)")
    parser.add_argument("--rescore", action="store_true", help="rebuild the scoreboard from saved results and exit")
    parser.add_argument("--model", help="passed to fix_batch.py (its default: Claude Haiku 5.5)")
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--strict-schema", action="store_true")
    parser.add_argument("--label", help="short name for the results folder and scoreboard")
    parser.add_argument("--runs", type=int, default=1, help="fresh worktrees to run (three smooth out luck)")
    parser.add_argument("--test-cmd", help="passed on: test command template run in the worktree")
    parser.add_argument("--rules", help="passed on: project rules file")
    parser.add_argument("--tries", type=int, help="passed on: attempts per finding")
    parser.add_argument("--max-tokens", type=int, help="passed on: output limit per call")
    parser.add_argument("--outline", action="append", default=[],
                        help="passed on: file to outline for every fixer (test helpers); repeatable")
    parser.add_argument("--keep", action="store_true", help="keep the worktrees")
    args = parser.parse_args()

    bench = os.path.abspath(args.bench)
    key = load_key(os.path.join(bench, "key.json"))
    if args.rescore:
        print("\n".join(rescore(bench, key)))
        return
    if not args.model or not args.label:
        parser.error("--model and --label are required")
    repo = os.path.abspath(args.repo)
    args.rules = os.path.abspath(args.rules) if args.rules else None
    findings = os.path.join(bench, "findings.json")
    stem = os.path.splitext(os.path.basename(findings))[0]
    board = os.path.join(bench, "scoreboard.md")
    for run in range(1, args.runs + 1):
        name = f"bench-{args.label}-{run}"
        scratch = tempfile.mkdtemp(prefix="bench-")
        worktree = os.path.join(scratch, "worktree")
        out_dir = os.path.join(bench, "results", args.label, f"run{run}")
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        os.makedirs(out_dir)
        git("worktree", "add", "-q", "-b", name, worktree, key["base_commit"], cwd=repo)
        try:
            subprocess.run(batch_command(args, findings, worktree, out_dir), cwd=repo, capture_output=True, text=True)
            totals = score(key, out_dir, stem)
            totals["date"] = datetime.date.today().isoformat()
            save_diffs(totals, worktree, out_dir)
        finally:
            if not args.keep:
                git("worktree", "remove", "--force", worktree, cwd=repo)
                git("branch", "-D", name, cwd=repo)
                shutil.rmtree(scratch, ignore_errors=True)
        with open(os.path.join(out_dir, "score.json"), "w", encoding="utf-8") as handle:
            json.dump(totals, handle, indent=1)
        if totals["errors"]:
            print(f"warning: {totals['not_run']} findings didn't run: {'; '.join(totals['errors'])[:500]}")
        if not os.path.exists(board):
            with open(board, "w", encoding="utf-8") as handle:
                handle.write(HEADER)
        line = scoreboard_line(totals["date"], args.label, run, totals)
        with open(board, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)


if __name__ == "__main__":
    main()
