import json
import os
import subprocess
import sys
import tempfile
import unittest

from model_fix import apply_patch, failure_lines, is_tab_indented

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


def read(root, path):
    with open(os.path.join(root, path), encoding="utf-8") as handle:
        return handle.read()


def patch(find, replace, file="a.txt"):
    return {"decision": "fix", "summary": "s", "edits": [{"file": file, "find": find, "replace": replace}]}


class ApplyPatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_match_applies(self):
        write(self.root, "a.txt", "one\ntwo\nthree\n")
        self.assertEqual(apply_patch(self.root, patch("two\n", "2\n")), [])
        self.assertEqual(read(self.root, "a.txt"), "one\n2\nthree\n")

    def test_zero_matches_rejected_and_nothing_written(self):
        write(self.root, "a.txt", "one\ntwo\n")
        write(self.root, "b.txt", "x\n")
        both = {"decision": "fix", "summary": "s", "edits": [
            {"file": "b.txt", "find": "x", "replace": "y"},
            {"file": "a.txt", "find": "missing", "replace": "z"}]}
        problems = apply_patch(self.root, both)
        self.assertEqual(len(problems), 1)
        self.assertIn("matches 0 times", problems[0])
        self.assertEqual(read(self.root, "b.txt"), "x\n")
        self.assertEqual(read(self.root, "a.txt"), "one\ntwo\n")

    def test_two_matches_rejected(self):
        write(self.root, "a.txt", "x\nx\n")
        problems = apply_patch(self.root, patch("x", "y"))
        self.assertIn("matches 2 times", problems[0])
        self.assertEqual(read(self.root, "a.txt"), "x\nx\n")

    def test_missing_file_and_escape_rejected(self):
        self.assertTrue(apply_patch(self.root, patch("x", "y", file="nope.txt")))
        self.assertTrue(apply_patch(self.root, patch("x", "y", file="../x.txt")))

    def test_spaces_become_tabs_only_in_a_tab_indented_file(self):
        write(self.root, "tab.gd", "func f():\n\tif x:\n\t\tpass\n\treturn 1\n")
        write(self.root, "space.py", "def f():\n    if x:\n        pass\n    return 1\n")
        edit = ("    if x:\n        pass\n", "    if y:\n        pass\n")
        self.assertEqual(apply_patch(self.root, patch(*edit, file="tab.gd")), [])
        self.assertEqual(read(self.root, "tab.gd"), "func f():\n\tif y:\n\t\tpass\n\treturn 1\n")
        # A space-indented file is never converted: a non-matching find is rejected.
        self.assertTrue(apply_patch(self.root, patch("\tif x:\n\t\tpass\n", "\tif y:\n", file="space.py")))
        self.assertEqual(apply_patch(self.root, patch(*edit, file="space.py")), [])
        self.assertIn("    if y:", read(self.root, "space.py"))

    def test_is_tab_indented(self):
        self.assertTrue(is_tab_indented("a\n\tb\n\t\tc\n  d\n"))
        self.assertFalse(is_tab_indented("a\n    b\n\tc\n    d\n"))
        self.assertFalse(is_tab_indented("a\nb\n"))

    def test_new_file_created_and_existing_refused(self):
        write(self.root, "old.txt", "keep\n")
        made = {"decision": "fix", "summary": "s", "edits": [], "new_files": [{"path": "sub/new.txt", "content": "hi\n"}]}
        self.assertEqual(apply_patch(self.root, made), [])
        self.assertEqual(read(self.root, "sub/new.txt"), "hi\n")
        clash = {"decision": "fix", "summary": "s", "edits": [], "new_files": [{"path": "old.txt", "content": "no\n"}]}
        self.assertTrue(apply_patch(self.root, clash))
        self.assertEqual(read(self.root, "old.txt"), "keep\n")


class FailureLinesTest(unittest.TestCase):
    def test_collects_failure_lines_and_tail(self):
        output = "\n".join(["ok 1"] * 30 + ["Traceback (most recent call last):", "  File x", "AssertionError: 1 != 2"]
                           + ["ok"] * 30 + ["Ran 31 tests", "FAILED (failures=1)"])
        text = failure_lines(output)
        self.assertIn("Traceback", text)
        self.assertIn("AssertionError: 1 != 2", text)
        self.assertIn("FAILED (failures=1)", text)
        self.assertNotIn("ok 1", text)

    def test_cap(self):
        output = "\n".join(f"FAIL case {i} " + "x" * 80 for i in range(500)) + "\nlast line"
        text = failure_lines(output)
        self.assertLessEqual(len(text), 4100)
        self.assertIn("last line", text)

    def test_no_markers_gives_tail(self):
        self.assertEqual(failure_lines("a\nb\nc"), "a\nb\nc")


CODE = "def double(x):\n    return x + x\n\n\ndef triple(x):\n    return x * 2\n"

class EndToEndTest(unittest.TestCase):
    """model_fix.py with a canned answer (--answer-file): no network."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        write(self.repo, "calc.py", CODE)
        write(self.repo, "test_calc.py", "import unittest\nfrom calc import triple\n\n\nclass T(unittest.TestCase):\n"
                                          "    def test_nothing(self):\n        self.assertTrue(True)\n")
        self.git("init", "-q")
        self.git("config", "user.name", "T")
        self.git("config", "user.email", "t@example.com")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        write(self.tmp.name, "review.json", json.dumps({"summary": "s", "checked_ok": [], "findings": [{
            "title": "triple is wrong", "severity": "high", "confidence": "high", "kind": "bug",
            "file": "calc.py", "symbol": "triple", "evidence": "return x * 2", "situation": "triple(2) is 4",
            "fix": "multiply by 3", "test_file": "test_calc.py", "related": []}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True).stdout

    def answer(self, new_code):
        edits = [
            {"file": "calc.py", "find": "    return x * 2\n", "replace": new_code},
            {"file": "test_calc.py", "find": "    def test_nothing(self):\n        self.assertTrue(True)\n",
             "replace": "    def test_nothing(self):\n        self.assertTrue(True)\n\n"
                        "    def test_triple(self):\n        self.assertEqual(triple(2), 6)\n"}]
        path = os.path.join(self.tmp.name, "answer.json")
        write(self.tmp.name, "answer.json", json.dumps({"decision": "fix", "summary": "Multiply by three.", "edits": edits}))
        return path

    def run_fix(self, answer, tries="1"):
        out_dir = os.path.join(self.tmp.name, "out")
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "model_fix.py"), "--finding", os.path.join(self.tmp.name, "review.json:1"),
             "--worktree", self.repo, "--out-dir", out_dir, "--answer-file", answer, "--tries", tries,
             "--test-cmd", "python3 -m unittest"],
            capture_output=True, text=True)
        with open(os.path.join(out_dir, "review-1.result.json")) as handle:
            return result, json.load(handle)

    def test_passing_fix_is_committed(self):
        result, record = self.run_fix(self.answer("    return x * 3\n"))
        self.assertEqual(record["outcome"], "passed", result.stdout + result.stderr)
        self.assertEqual(len(self.git("log", "--oneline").splitlines()), 2)
        self.assertIn("x * 3", read(self.repo, "calc.py"))
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def test_failing_fix_is_reverted(self):
        result, record = self.run_fix(self.answer("    return x * 4\n"))
        self.assertEqual(record["outcome"], "failed", result.stdout + result.stderr)
        self.assertEqual(record["attempts"][0]["result"], "tests failed")
        self.assertEqual(len(self.git("log", "--oneline").splitlines()), 1)
        self.assertEqual(read(self.repo, "calc.py"), CODE)
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def test_not_a_bug_is_declined(self):
        path = os.path.join(self.tmp.name, "decline.json")
        write(self.tmp.name, "decline.json", json.dumps({"decision": "not a bug", "summary": "Fine.", "edits": []}))
        result, record = self.run_fix(path)
        self.assertEqual(record["outcome"], "declined")
        self.assertEqual(read(self.repo, "calc.py"), CODE)

    def test_prompt_contains_only_named_code(self):
        self.run_fix(self.answer("    return x * 3\n"))
        with open(os.path.join(self.tmp.name, "out", "review-1.prompt1.md")) as handle:
            prompt = handle.read()
        self.assertIn("def triple", prompt)
        self.assertNotIn("def double", prompt)
        self.assertIn("test_calc.py", prompt)


if __name__ == "__main__":
    unittest.main()
