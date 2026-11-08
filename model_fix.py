#!/usr/bin/env python3
"""Have a model on OpenRouter write one small fix as a patch, then apply it,
test it and commit it in a git worktree.

No agent loop: the lead names the task and the code it touches, this script
hands the model exactly that code (whole files, or single functions found by
name), and the model answers with exact find-and-replace edits in the
`patch_schema.json` format. Each `find` must match exactly once or the patch
is rejected (nothing is written). The test command then runs in the worktree;
on any failure the model gets the error and one more try (--tries). A passing
fix is committed in the worktree for the lead to review and merge.

  model_fix.py --finding batch.json:1 --worktree ../myproj-fixes \
      --test-cmd "pytest {test_file}" --rules examples/rules_python.md --out-dir fixes

The default model is Claude Haiku 5.5 (`anthropic/claude-haiku-5.5`), through
Google Vertex then Anthropic with the provider-enforced schema. For MiMo or
GLM pass --model, --provider and (MiMo on DeepInfra) --strict-schema.

After the named code the model gets an outline (declarations only, with line
numbers, marked not for `find`) of every file it was shown part of, plus any
file given with --outline. If a context file is missing, the result record
says outcome "error" with the reason and the exit status is 1.

--test-cmd is a template: `{tests}` is the filter (--tests, default the test
file's stem) and `{test_file}` its path. Default: `./run_tests.sh {tests}`.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from symbols import BRACE_EXTS, INDENT_EXTS, OUTLINE_LIMIT, outline, symbol_range  # noqa: E402

DEFAULT_TEST_CMD = "./run_tests.sh {tests}"
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
# Providers that enforce the patch schema (Bedrock doesn't support strict mode).
ANTHROPIC_PROVIDERS = ["google-vertex", "anthropic"]

RULES = """You are fixing one small, well-understood problem in a codebase. Rules:
- Change as little as possible; touch only the code shown. Don't refactor or rename.
- Add or extend a test that fails without the fix and passes with it, in the test file shown,
  following its style and using the assertions it already uses.
- Keep the file's style and indentation (same indentation characters as the code shown).
- Comments and docs: short and plain.
- Every `find` is whole lines copied exactly from the file as shown, unique in that file.
"""


def context_block(root: str, spec: str) -> str:
    path, _, symbol = spec.partition(":")
    with open(os.path.join(root, path), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    if not symbol:
        return f"===== FILE: {path} (whole file) =====\n" + "\n".join(lines) + f"\n===== END FILE: {path} =====\n"
    ext = os.path.splitext(path)[1].lower()
    span = symbol_range(lines, symbol, path)
    if span is None and ext not in INDENT_EXTS | BRACE_EXTS:
        print(f"warning: symbols are not supported for {ext or path}; showing {path} whole", file=sys.stderr)
        return context_block(root, path)
    if span is None:
        print(f"warning: no symbol '{symbol}' in {path}; left out", file=sys.stderr)
        return ""
    start, end = span
    body = "\n".join(lines[start - 1:end])
    return (f"===== FILE: {path} (lines {start} to {end} of {len(lines)}: `{symbol}`) =====\n{body}\n"
            f"===== END FILE: {path} =====\n")


def outline_block(root: str, path: str, limit: int = OUTLINE_LIMIT) -> str:
    """The declarations of a file (classes, functions, constants, fields ...,
    top level and one level in, with line numbers) so a fixer shown one
    function also knows the members and helpers around it. Empty for a
    language the outline doesn't know."""
    with open(os.path.join(root, path), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    found = outline(lines, path, limit)
    if found is None:
        print(f"warning: no outline for {os.path.splitext(path)[1] or path}; {path} left out", file=sys.stderr)
        return ""
    return (f"===== OUTLINE: {path} (declarations only, with line numbers; not for `find`) =====\n"
            + "\n".join(found) + f"\n===== END OUTLINE: {path} =====\n")


def shown_whole(spec: str) -> bool:
    """Whether the context spec shows its whole file (no symbol, or a language
    without symbol support, where context_block falls back to the whole file)."""
    path, _, symbol = spec.partition(":")
    return not symbol or os.path.splitext(path)[1].lower() not in INDENT_EXTS | BRACE_EXTS


def build_context(root: str, specs: list[str], outlines: list[str] = ()) -> str:
    """The code a fixer sees: each named symbol or whole file, then an outline
    of every file it was shown part of, plus the files named in `outlines`
    (test helpers, for example). Files that don't exist are skipped."""
    text = "".join(context_block(root, spec) for spec in specs)
    whole = {spec.partition(":")[0] for spec in specs if shown_whole(spec)}
    partial = [spec.partition(":")[0] for spec in specs if not shown_whole(spec)]
    for path in dict.fromkeys(partial + list(outlines)):
        if path in whole:
            continue
        if os.path.isfile(os.path.join(root, path)):
            text += outline_block(root, path)
        else:
            print(f"warning: no outline for {path}: file not found", file=sys.stderr)
    return text


def finding_context(finding: dict) -> list[str]:
    """The context specs a fixer is given for a finding: its symbol, the
    `related` code and its test file, each once."""
    specs = [f"{finding['file']}:{finding['symbol']}"] + list(finding.get("related") or [])
    if finding.get("test_file"):
        specs.append(finding["test_file"])
    return list(dict.fromkeys(specs))


def resolve_provider(model: str, providers: list[str], strict: bool) -> tuple[list[str], bool]:
    """Provider order and strict-schema flag for a model. An `anthropic/` model
    with no provider given goes to Google Vertex, then Anthropic, with the
    provider-enforced (strict) schema; anything else is left as given."""
    if model.startswith("anthropic/") and not providers:
        return list(ANTHROPIC_PROVIDERS), True
    return list(providers), strict


def finding_task(finding: dict) -> str:
    """The task text for a structured review finding. The reviewer may be
    wrong (about one claim in six is), so the fixer is asked to check first."""
    return (f"A code reviewer reported this problem. Check it against the code first: if the code is "
            f"already correct and the problem can't happen, answer with decision 'not a bug' and say why.\n\n"
            f"- Title: {finding['title']}\n- Where: `{finding['file']}`, `{finding['symbol']}`\n"
            f"- Evidence:\n```\n{finding['evidence']}\n```\n- Situation: {finding['situation']}\n"
            f"- Suggested fix: {finding['fix']}\n")


FAILURE_WORDS = re.compile(r"FAIL|FAILED|Error|error:|assert|Traceback|panicked")


def failure_lines(output: str, limit: int = 4000) -> str:
    """What the model needs from a failed test run: every line that looks like
    a failure (FAIL, FAILED, Error, error:, assert, Traceback, panicked) with
    the 4 lines after it, plus the last 15 lines (usually the summary), capped
    at about `limit` characters (the tail is always kept)."""
    lines = output.splitlines()
    hits: list[str] = []
    for index, line in enumerate(lines):
        if FAILURE_WORDS.search(line):
            hits += lines[index:index + 5]
    tail = "\n".join(lines[-15:])
    found = "\n".join(dict.fromkeys(hit for hit in hits if hit not in lines[-15:]))
    found = found[:max(0, limit - len(tail) - 1)]
    return (found + "\n" + tail).strip()


def is_tab_indented(text: str) -> bool:
    """Whether most indented lines of the file start with a tab."""
    indented = [line for line in text.splitlines() if line[:1] in (" ", "\t")]
    return sum(1 for line in indented if line[0] == "\t") * 2 > len(indented) if indented else False


def tabbed(text: str) -> str:
    """Leading groups of four spaces become tabs (models write spaces)."""
    return "\n".join(re.sub(r"^((?:    )+)", lambda m: "\t" * (len(m.group(1)) // 4), line)
                     for line in text.split("\n"))


def apply_patch(root: str, patch: dict) -> list[str]:
    """Applies the patch's edits and new files under `root`. Returns problems;
    nothing is written unless every edit matches exactly once."""
    problems: list[str] = []
    pending: dict[str, str] = {}
    for number, edit in enumerate(patch["edits"], 1):
        path = os.path.normpath(edit["file"])
        full = os.path.join(root, path)
        if path.startswith("..") or not os.path.isfile(full):
            problems.append(f"edit {number}: no file {edit['file']}")
            continue
        if full not in pending:
            with open(full, encoding="utf-8") as handle:
                pending[full] = handle.read()
        text, find, replace = pending[full], edit["find"], edit["replace"]
        if text.count(find) != 1 and is_tab_indented(text):
            find, replace = tabbed(find), tabbed(replace)
        count = text.count(find) if find else 0
        if count != 1:
            problems.append(f"edit {number} ({path}): `find` matches {count} times, must match once:\n{edit['find'][:400]}")
            continue
        pending[full] = text.replace(find, replace, 1)
    for item in patch.get("new_files") or []:
        full = os.path.join(root, os.path.normpath(item["path"]))
        if item["path"].startswith("..") or os.path.exists(full):
            problems.append(f"new file {item['path']}: exists already or is outside the repository")
    if problems:
        return problems
    for full, text in pending.items():
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
    for item in patch.get("new_files") or []:
        full = os.path.join(root, os.path.normpath(item["path"]))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(item["content"])
    return []


def set_status(out_dir: str, name: str, text: str) -> None:
    """One line for `fix_status.py`: what this fix is doing now."""
    with open(os.path.join(out_dir, f"{name}.status"), "w", encoding="utf-8") as handle:
        handle.write(text + "\n")


def run(cmd: list[str], cwd: str, timeout: int = 900) -> tuple[int, str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout + result.stderr


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", help="Markdown file describing the fix")
    parser.add_argument("--finding", help="review.json:N, the Nth finding (from 1) of a structured review "
                        "(review_schema.json): its text is the task, its file:symbol, `related` and "
                        "`test_file` are the context, and the test file's name is the test filter")
    parser.add_argument("--worktree", required=True, help="clean git worktree to apply and test in")
    parser.add_argument("--context", action="append", default=[], help="path or path:Symbol to show the model")
    parser.add_argument("--tests", help="the {tests} filter for --test-cmd (default: the stem of the "
                        "finding's test_file)")
    parser.add_argument("--test-file", help="the {test_file} value for --task (a finding supplies its own)")
    parser.add_argument("--test-cmd", default=DEFAULT_TEST_CMD,
                        help="test command template run in the worktree, for example 'pytest {test_file}' "
                        "or 'npm test -- {tests}' (default: %(default)s)")
    parser.add_argument("--rules", help="file of project rules appended to the built-in rules")
    parser.add_argument("--answer-file", help=argparse.SUPPRESS)  # canned patch JSON instead of a model call (tests)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="default: Claude Haiku 5.5 (with no --provider: google-vertex then anthropic, strict "
                        "schema). MiMo and GLM work too: --model xiaomi/mimo-v2.6-pro --provider deepinfra "
                        "--strict-schema, or --model z-ai/glm-5.3-flash --provider z-ai")
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--effort", default="low")
    parser.add_argument("--tries", type=int, default=2)
    parser.add_argument("--outline", action="append", default=[],
                        help="also show this file's declarations (test helpers, for example); repeatable")
    parser.add_argument("--max-tokens", type=int, default=64000,
                        help="output limit per call, reasoning included (a model that thinks past it returns nothing)")
    parser.add_argument("--strict-schema", action="store_true",
                        help="have the provider enforce the patch schema (valid JSON every time; DeepInfra "
                        "supports it for MiMo, Z.ai doesn't for GLM)")
    parser.add_argument("--out-dir", required=True, help="where prompts, answers and the result record go")
    args = parser.parse_args()

    args.provider, args.strict_schema = resolve_provider(args.model, args.provider, args.strict_schema)
    root = os.path.abspath(args.worktree)
    args.out_dir = os.path.abspath(args.out_dir)
    os.makedirs(args.out_dir, exist_ok=True)
    if args.finding:
        review_path, _, index = args.finding.rpartition(":")
        with open(review_path, encoding="utf-8") as handle:
            finding = json.load(handle)["findings"][int(index) - 1]
        name = f"{os.path.splitext(os.path.basename(review_path))[0]}-{index}"
        task = finding_task(finding)
        args.context = finding_context(finding) + args.context
        args.test_file = args.test_file or finding.get("test_file") or None
    elif args.task:
        name = os.path.splitext(os.path.basename(args.task))[0]
        with open(args.task, encoding="utf-8") as handle:
            task = handle.read()
    else:
        sys.exit("Give --task or --finding.")
    # The same code may be named twice (a symbol and its whole file); show it once.
    args.context = list(dict.fromkeys(args.context))
    missing = [spec.partition(":")[0] for spec in args.context
               if not os.path.isfile(os.path.join(root, spec.partition(":")[0]))]
    if missing:
        # Record it, so a batch or the benchmark shows an error rather than nothing.
        record = {"task": name, "model": args.model, "attempts": [], "outcome": "error",
                  "reason": f"missing file(s): {', '.join(missing)}", "seconds": 0, "cost": 0}
        with open(os.path.join(args.out_dir, f"{name}.result.json"), "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1)
        set_status(args.out_dir, name, "error")
        sys.exit(f"{name}: missing file(s) {', '.join(missing)}")
    if not args.tests and args.test_file:
        args.tests = os.path.splitext(os.path.basename(args.test_file))[0]
    if "{tests}" in args.test_cmd and not args.tests:
        sys.exit("No test filter for {tests}: give --tests (or a finding with a test_file).")
    if "{test_file}" in args.test_cmd and not args.test_file:
        sys.exit("No test file for {test_file}: give --test-file (or a finding with a test_file).")
    test_cmd = shlex.split(args.test_cmd.format(tests=shlex.quote(args.tests or ""),
                                                test_file=shlex.quote(args.test_file or "")))
    rules = RULES
    if args.rules:
        with open(args.rules, encoding="utf-8") as handle:
            rules += "\n## Project rules\n\n" + handle.read().strip() + "\n"
    # The role and standing rules go in the system prompt; the task and code in the message.
    system_path = os.path.join(args.out_dir, f"{name}.system.md")
    with open(system_path, "w", encoding="utf-8") as handle:
        handle.write(rules)
    base = "## Task\n\n" + task + "\n\n## Code\n\n" + build_context(root, args.context, args.outline)
    record = {"task": name, "model": args.model, "attempts": [], "outcome": "failed"}
    feedback = ""
    started = time.time()
    for attempt in range(1, args.tries + 1):
        prompt_path = os.path.join(args.out_dir, f"{name}.prompt{attempt}.md")
        answer_path = os.path.join(args.out_dir, f"{name}.answer{attempt}.json")
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write(base + feedback)
        set_status(args.out_dir, name, f"attempt {attempt}: asking")
        cmd = [sys.executable, os.path.join(HERE, "ask_model.py"), "--prompt-file", prompt_path,
               "--system-file", system_path,
               "--json-schema", os.path.join(HERE, "patch_schema.json"), "--model", args.model,
               "--effort", args.effort, "--max-tokens", str(args.max_tokens), "--out", answer_path]
        for provider in args.provider:
            cmd += ["--provider", provider]
        if args.strict_schema:
            cmd.append("--strict-schema")
        if args.answer_file:
            shutil.copyfile(args.answer_file, answer_path)
            code, output = 0, ""
        else:
            code, output = run(cmd, root, timeout=1500)
        step = {"attempt": attempt, "model_output": output.strip()[-300:]}
        record["attempts"].append(step)
        if code != 0:
            step["result"] = "no valid answer"
            feedback = f"\n\n## Your previous answer was rejected\n\n{output.strip()[-1500:]}\nAnswer again.\n"
            continue
        with open(answer_path, encoding="utf-8") as handle:
            patch = json.load(handle)
        step["cost"] = ((patch.get("_meta") or {}).get("usage") or {}).get("cost")
        if patch["decision"] == "not a bug":
            step["result"] = "declined"
            record["outcome"] = "declined"
            record["reason"] = patch["summary"]
            break
        set_status(args.out_dir, name, f"attempt {attempt}: applying")
        problems = apply_patch(root, patch)
        if problems:
            step["result"] = "patch did not apply"
            feedback = ("\n\n## Your previous answer did not apply\n\n" + json.dumps(
                {k: v for k, v in patch.items() if k != "_meta"}, indent=1)[:6000]
                + "\n\nProblems:\n" + "\n".join(problems) + "\nAnswer again with a complete corrected patch.\n")
            continue
        set_status(args.out_dir, name, f"attempt {attempt}: testing")
        code, output = run(test_cmd, root)
        tail = failure_lines(output)
        if code != 0:
            step["result"] = "tests failed"
            step["tests"] = tail[-1500:]
            run(["git", "checkout", "--", "."], root)
            run(["git", "clean", "-fdq"], root)
            feedback = ("\n\n## Your previous answer applied but the tests failed\n\n" + json.dumps(
                {k: v for k, v in patch.items() if k != "_meta"}, indent=1)[:6000]
                + "\n\nTest output (end):\n" + tail[-4000:] + "\n\nAnswer again with a complete corrected patch "
                "against the original code shown above.\n")
            continue
        step["result"] = "passed"
        run(["git", "add", "-A"], root)
        run(["git", "commit", "-qm", f"{name}: {patch['summary'][:200]}"], root)
        record["outcome"] = "passed"
        record["commit"] = run(["git", "rev-parse", "--short", "HEAD"], root)[1].strip()
        break
    record["seconds"] = round(time.time() - started)
    record["cost"] = round(sum(s.get("cost") or 0 for s in record["attempts"]), 5)
    with open(os.path.join(args.out_dir, f"{name}.result.json"), "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1)
    set_status(args.out_dir, name, record["outcome"])
    print(f"{name} [{args.model}]: {record['outcome']} after {len(record['attempts'])} attempt(s), "
          f"{record['seconds']} s, ${record['cost']}")


if __name__ == "__main__":
    main()
