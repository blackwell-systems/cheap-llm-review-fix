#!/usr/bin/env python3
"""Readiness check: is each finding a good fix task?

For every finding in a batch (review format), a model sees exactly what the
fixer would see (the context `model_fix.py` builds: the named symbol, the
`related` code, the test file, and the outlines) plus the list of test files,
and answers in `readiness_schema.json`: does the code show the bug, is any
needed code missing, can that test file test it, is the suggested fix sound.
A finding that isn't ready makes a fixer fail or refuse for reasons that have
nothing to do with the fixer, so fix or drop those before running a batch or
a benchmark.

--project-notes FILE is appended to the prompt: what the project's test
folders mean, which test level fits which kind of code (see
examples/project_notes_python.md and examples/project_notes_godot.md).

  check_findings.py batch.json --root ../myproj --project-notes notes.md --out readiness.md

The default model is GLM 5.3 (Z.ai); pass --model and --provider for another.
"""
import argparse
import concurrent.futures
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from model_fix import build_context, finding_context, resolve_provider  # noqa: E402

SYSTEM = """You check whether a code-review finding is ready to hand to a fixer model. The fixer will see
exactly the finding and the code in the message, nothing else, and must answer with exact edits plus a
test that fails without the fix, run with the project's test runner. Judge the finding as a task, not
the fixer. Be concrete.
"""

PROMPT = """## Finding

{finding}

## Existing test files

{tests}

## Code the fixer will see

{code}
"""

TEST_PATTERNS = ["test_*", "*_test.*", "*.test.*", "*.spec.*"]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".godot", "target", "dist"}


def find_test_files(root: str, patterns: list[str] | None = None) -> list[str]:
    """Test files under `root` (relative paths, sorted) whose file name matches
    one of the fnmatch `patterns` (default: test_*, *_test.*, *.test.*, *.spec.*)."""
    patterns = patterns or TEST_PATTERNS
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                found.append(os.path.relpath(os.path.join(folder, name), root))
    return sorted(found)


def build_prompt(root: str, finding: dict, tests: str, outlines: list[str], notes: str = "") -> str:
    """The readiness prompt for one finding. The code is the fixer's own
    context (`model_fix.build_context` over `finding_context`); a context file
    that doesn't exist is listed as MISSING FILE, which the model should flag."""
    context = finding_context(finding)
    present = [spec for spec in context if os.path.isfile(os.path.join(root, spec.partition(":")[0]))]
    code = build_context(root, present, outlines)
    code += "".join(f"===== MISSING FILE: {spec} =====\n" for spec in context if spec not in present)
    shown = {k: v for k, v in finding.items() if not k.startswith("_")}
    return PROMPT.format(finding=json.dumps(shown, indent=1), tests=tests, code=code)


def build_system(notes: str = "") -> str:
    """The system prompt: the reviewer's role, plus the project notes (what the
    test folders mean, test levels), which are the same for every finding."""
    if notes.strip():
        return SYSTEM + "\n## Project notes\n\n" + notes.strip() + "\n"
    return SYSTEM


def check(root: str, finding: dict, tests: str, notes: str, args: argparse.Namespace, workdir: str,
          number: int) -> dict:
    prompt_path = os.path.join(workdir, f"{number}.md")
    out_path = os.path.join(workdir, f"{number}.json")
    with open(prompt_path, "w", encoding="utf-8") as handle:
        handle.write(build_prompt(root, finding, tests, args.outline, notes))
    system_path = os.path.join(workdir, f"{number}.system.md")
    with open(system_path, "w", encoding="utf-8") as handle:
        handle.write(build_system(notes))
    if args.answer_file:
        shutil.copyfile(args.answer_file, out_path)
    else:
        cmd = [sys.executable, os.path.join(HERE, "ask_model.py"), "--prompt-file", prompt_path,
               "--system-file", system_path,
               "--json-schema", os.path.join(HERE, "readiness_schema.json"), "--model", args.model,
               "--effort", args.effort, "--out", out_path]
        for provider in args.provider:
            cmd += ["--provider", provider]
        if args.strict_schema:
            cmd.append("--strict-schema")
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            return {"number": number, "error": (result.stderr or result.stdout).strip()[-300:]}
    with open(out_path, encoding="utf-8") as handle:
        answer = json.load(handle)
    answer["number"] = number
    return answer


def report(answers: list[dict], findings: list[dict], model: str, workdir: str) -> str:
    """The Markdown readiness table and the list of problems."""
    lines = ["# Finding readiness", "", f"Model: {model}. Answers in {workdir}.", "",
             "| # | Title | Ready | Bug shown | Test file ok | Fix sound | Missing context | Better test file |",
             "|---|---|---|---|---|---|---|---|"]
    yes = {True: "yes", False: "**no**"}
    for answer, finding in zip(answers, findings):
        if "error" in answer:
            lines.append(f"| {answer['number']} | {finding['title']} | error: {answer['error'][:80]} | | | | | |")
            continue
        lines.append(f"| {answer['number']} | {finding['title']} | {yes[answer['ready']]} | "
                     f"{yes[answer['bug_visible']]} | {yes[answer['test_file_ok']]} | {yes[answer['fix_sound']]} | "
                     f"{', '.join(answer['context_missing'])} | {answer['better_test_file']} |")
    lines += ["", "## Problems", ""]
    for answer, finding in zip(answers, findings):
        if answer.get("problems"):
            lines.append(f"- **{answer['number']}. {finding['title']}:** " + " ".join(answer["problems"]))
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch")
    parser.add_argument("--root", default=".", help="the project (or the worktree the fixer will use)")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--provider", action="append", default=[],
                        help="default: z-ai for z-ai/ models, google-vertex then anthropic for anthropic/ models")
    parser.add_argument("--strict-schema", action="store_true")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--outline", action="append", default=[],
                        help="extra files to outline, as the fixer gets them (pass the fixer's --outline files)")
    parser.add_argument("--project-notes", help="file appended to the prompt: project specifics such as what "
                        "the test folders mean")
    parser.add_argument("--test-glob", action="append", default=[],
                        help="file name pattern for test files (repeatable; default: test_*, *_test.*, "
                        "*.test.*, *.spec.*)")
    parser.add_argument("--work-dir", help="keep the prompts and answers here (default: a temp folder)")
    parser.add_argument("--answer-file", help=argparse.SUPPRESS)  # canned answer instead of a model call (tests)
    parser.add_argument("--out", required=True, help="Markdown report")
    args = parser.parse_args()

    args.provider, args.strict_schema = resolve_provider(args.model, args.provider, args.strict_schema)
    if args.model.startswith("z-ai/") and not args.provider:
        args.provider = ["z-ai"]
    root = os.path.abspath(args.root)
    with open(args.batch, encoding="utf-8") as handle:
        findings = json.load(handle)["findings"]
    notes = ""
    if args.project_notes:
        with open(args.project_notes, encoding="utf-8") as handle:
            notes = handle.read()
    tests = "\n".join(find_test_files(root, args.test_glob))
    workdir = os.path.abspath(args.work_dir) if args.work_dir else tempfile.mkdtemp(prefix="readiness-")
    os.makedirs(workdir, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        answers = list(pool.map(lambda pair: check(root, pair[1], tests, notes, args, workdir, pair[0]),
                                enumerate(findings, 1)))
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(report(answers, findings, args.model, workdir))
    ready = sum(1 for a in answers if a.get("ready"))
    print(f"{ready} of {len(findings)} findings ready -> {args.out}")


if __name__ == "__main__":
    main()
