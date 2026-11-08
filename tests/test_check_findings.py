import json
import os
import subprocess
import sys
import tempfile
import unittest

from check_findings import build_prompt, find_test_files, report
from model_fix import build_context, finding_context

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODE = "LIMIT = 3\n\n\nclass Cart:\n    def add(self, item):\n        return item\n\n    def total(self):\n        return 0\n"
TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"

FINDING = {"title": "add ignores the limit", "severity": "high", "confidence": "high", "kind": "bug",
           "file": "src/cart.py", "symbol": "Cart.add", "evidence": "return item", "situation": "s",
           "fix": "check LIMIT", "test_file": "tests/test_cart.py", "related": [], "_id": "1a", "_model": "m"}

READY = {"ready": True, "bug_visible": True, "context_missing": ["src/cart.py:LIMIT"], "test_file_ok": True,
         "better_test_file": "", "fix_sound": True, "problems": []}
NOT_READY = {"ready": False, "bug_visible": False, "context_missing": [], "test_file_ok": False,
             "better_test_file": "tests/test_other.py", "fix_sound": True, "problems": ["Wrong test level."]}


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "repo")
        write(self.root, "src/cart.py", CODE)
        write(self.root, "tests/test_cart.py", TEST)
        write(self.root, "tests/test_other.py", TEST)
        write(self.root, "web/app.spec.ts", "")
        write(self.root, "node_modules/x/test_skip.py", "")

    def tearDown(self):
        self.tmp.cleanup()


class PromptTest(RepoTest):
    def test_find_test_files(self):
        self.assertEqual(find_test_files(self.root), ["tests/test_cart.py", "tests/test_other.py", "web/app.spec.ts"])
        self.assertEqual(find_test_files(self.root, ["test_cart.*"]), ["tests/test_cart.py"])

    def test_code_is_the_fixers_exact_context(self):
        prompt = build_prompt(self.root, FINDING, "tests/test_cart.py", [])
        fixer = build_context(self.root, finding_context(FINDING), [])
        self.assertIn(fixer, prompt)
        self.assertIn("OUTLINE: src/cart.py", prompt)
        self.assertIn("LIMIT = 3", prompt)

    def test_outline_files_and_finding_without_private_keys(self):
        prompt = build_prompt(self.root, FINDING, "tests/test_cart.py", ["tests/test_other.py"])
        self.assertIn("OUTLINE: tests/test_other.py", prompt)
        self.assertIn('"title": "add ignores the limit"', prompt)
        self.assertNotIn("_model", prompt)
        self.assertNotIn("Godot", prompt)

    def test_project_notes_are_appended_last(self):
        prompt = build_prompt(self.root, FINDING, "t", [], "Unit tests have no database.")
        self.assertTrue(prompt.rstrip().endswith("Unit tests have no database."))
        self.assertIn("## Project notes", prompt)
        self.assertNotIn("## Project notes", build_prompt(self.root, FINDING, "t", []))

    def test_missing_context_file_is_marked(self):
        finding = dict(FINDING, test_file="tests/test_nope.py")
        prompt = build_prompt(self.root, finding, "t", [])
        self.assertIn("===== MISSING FILE: tests/test_nope.py =====", prompt)
        self.assertIn("def add", prompt)


class CommandTest(RepoTest):
    def run_check(self, answers):
        batch = os.path.join(self.tmp.name, "batch.json")
        write(self.tmp.name, "batch.json", json.dumps({"findings": [FINDING, dict(FINDING, title="second")]}))
        write(self.tmp.name, "notes.md", "Integration tests use the client fixture.")
        write(self.tmp.name, "answer.json", json.dumps(answers))
        out = os.path.join(self.tmp.name, "readiness.md")
        work = os.path.join(self.tmp.name, "work")
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "check_findings.py"), batch, "--root", self.root, "--out", out,
             "--project-notes", os.path.join(self.tmp.name, "notes.md"), "--outline", "tests/test_other.py",
             "--work-dir", work, "--answer-file", os.path.join(self.tmp.name, "answer.json")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as handle:
            return result.stdout, handle.read(), work

    def test_canned_answer_builds_prompts_and_report(self):
        stdout, text, work = self.run_check(READY)
        self.assertIn("2 of 2 findings ready", stdout)
        with open(os.path.join(work, "1.md"), encoding="utf-8") as handle:
            prompt = handle.read()
        self.assertIn("Integration tests use the client fixture.", prompt)
        self.assertIn("OUTLINE: tests/test_other.py", prompt)
        self.assertIn("tests/test_cart.py\ntests/test_other.py\nweb/app.spec.ts", prompt)
        self.assertIn("| 1 | add ignores the limit | yes | yes | yes | yes | src/cart.py:LIMIT |", text)

    def test_report_marks_problems(self):
        text = report([dict(NOT_READY, number=1)], [FINDING], "m", "/w")
        self.assertIn("| 1 | add ignores the limit | **no** | **no** | **no** | yes |  | tests/test_other.py |", text)
        self.assertIn("- **1. add ignores the limit:** Wrong test level.", text)

    def test_report_shows_errors(self):
        text = report([{"number": 1, "error": "timeout"}], [FINDING], "m", "/w")
        self.assertIn("error: timeout", text)


if __name__ == "__main__":
    unittest.main()
