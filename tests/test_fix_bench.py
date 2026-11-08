import argparse
import json
import os
import subprocess
import tempfile
import unittest

from fix_bench import batch_command, load_key, rescore, run_dirs, save_diffs, score, scoreboard_line

KEY = {"base_commit": "abc123", "items": [
    {"n": 1, "kind": "real", "difficulty": "easy", "latent": False},
    {"n": 2, "kind": "real", "difficulty": "easy", "latent": False},
    {"n": 3, "kind": "real", "difficulty": "hard", "latent": False},
    {"n": 4, "kind": "real", "difficulty": "easy", "latent": True},
    {"n": 5, "kind": "fake", "difficulty": "fake", "latent": False},
    {"n": 6, "kind": "fake", "difficulty": "fake", "latent": False},
    {"n": 7, "kind": "fake", "difficulty": "fake", "latent": False},
    {"n": 8, "kind": "debatable", "difficulty": "medium", "latent": False},
]}

# n -> (outcome, seconds, cost, commit)
RUN1 = {1: ("passed", 10, 0.01, "aaa"), 2: ("failed", 20, 0.02, ""), 3: ("passed", 30, 0.03, "bbb"),
        4: ("declined", 5, 0.005, ""), 5: ("declined", 6, 0.006, ""), 6: ("passed", 7, 0.007, "ccc"),
        8: ("passed", 9, 0.009, "ddd")}  # 7 has no result at all


def write_results(folder, results, stem="findings"):
    os.makedirs(folder, exist_ok=True)
    for n, (outcome, seconds, cost, commit) in results.items():
        record = {"task": f"{stem}-{n}", "outcome": outcome, "seconds": seconds, "cost": cost, "attempts": []}
        if commit:
            record["commit"] = commit
        with open(os.path.join(folder, f"{stem}-{n}.result.json"), "w") as handle:
            json.dump(record, handle)


class ScoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run1 = os.path.join(self.tmp.name, "results", "m", "run1")
        write_results(self.run1, RUN1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_totals(self):
        totals = score(KEY, self.run1)
        self.assertEqual((totals["real"], totals["fixed"]), (3, 2))
        self.assertEqual(totals["by_difficulty"], {"easy": [1, 2], "hard": [1, 1]})
        self.assertEqual((totals["latent"], totals["latent_fixed"]), (1, 0))
        self.assertEqual((totals["fake"], totals["refused"], totals["fake_fixed"]), (3, 1, 1))
        self.assertEqual(totals["not_run"], 1)
        self.assertEqual(totals["errors"], ["7: missing"])
        self.assertEqual(totals["seconds"], 87)
        self.assertEqual(totals["cost"], 0.087)

    def test_debatable_is_listed_but_not_scored(self):
        totals = score(KEY, self.run1)
        self.assertIn({"n": 8, "kind": "debatable", "difficulty": "medium", "latent": False,
                       "outcome": "passed", "commit": "ddd"}, totals["items"])
        self.assertEqual(totals["real"] + totals["fake"] + totals["latent"], 7)

    def test_error_records_count_as_not_run_with_the_reason(self):
        with open(os.path.join(self.run1, "findings-2.result.json"), "w") as handle:
            json.dump({"outcome": "error", "reason": "missing file(s): tests/x.py", "seconds": 0, "cost": 0}, handle)
        totals = score(KEY, self.run1)
        self.assertEqual(totals["not_run"], 2)
        self.assertIn("2: missing file(s): tests/x.py", totals["errors"])

    def test_scoreboard_line(self):
        line = scoreboard_line("2026-11-08", "m", 1, score(KEY, self.run1))
        self.assertEqual(line, "| 2026-11-08 | m | 1 | 2/3 (easy 1/2, hard 1/1) | 0/1 | 1/3 | 1 | 1 | 87 s | $0.087 |")

    def test_other_stem(self):
        other = os.path.join(self.tmp.name, "other")
        write_results(other, {1: ("passed", 1, 0.0, "x")}, stem="batch")
        self.assertEqual(score(KEY, other, "batch")["fixed"], 1)
        self.assertEqual(score(KEY, other)["fixed"], 0)


class RescoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bench = self.tmp.name
        write_results(os.path.join(self.bench, "results", "haiku", "run1"), RUN1)
        write_results(os.path.join(self.bench, "results", "haiku", "run2"), {1: ("passed", 1, 0.1, "e")})
        write_results(os.path.join(self.bench, "results", "mimo", "run1"), {3: ("passed", 1, 0.1, "f")})
        with open(os.path.join(self.bench, "results", "haiku", "run1", "score.json"), "w") as handle:
            json.dump({"date": "2026-11-08"}, handle)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_dirs_in_order(self):
        self.assertEqual([(label, run) for label, run, _ in run_dirs(self.bench)],
                         [("haiku", 1), ("haiku", 2), ("mimo", 1)])

    def test_rescore_rebuilds_the_scoreboard_with_the_current_key(self):
        lines = rescore(self.bench, KEY)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("| 2026-11-08 | haiku | 1 | 2/3"))
        with open(os.path.join(self.bench, "scoreboard.md")) as handle:
            text = handle.read()
        self.assertTrue(text.startswith("# Fix benchmark scoreboard"))
        self.assertEqual(text.count("\n| 20"), 3)
        # A corrected key changes the score without a rerun.
        changed = json.loads(json.dumps(KEY))
        changed["items"][1]["kind"] = "fake"
        changed["items"][1]["difficulty"] = "fake"
        lines = rescore(self.bench, changed)
        self.assertIn("| 2/2 (easy 1/1, hard 1/1) |", lines[0])
        with open(os.path.join(self.bench, "results", "haiku", "run1", "score.json")) as handle:
            saved = json.load(handle)
        self.assertEqual((saved["date"], saved["fixed"]), ("2026-11-08", 2))

    def test_rescore_of_nothing_writes_only_the_header(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(rescore(empty, KEY), [])
            with open(os.path.join(empty, "scoreboard.md")) as handle:
                self.assertIn("| Date | Model |", handle.read())


class LoadKeyTest(unittest.TestCase):
    def load(self, key):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "key.json")
            with open(path, "w") as handle:
                json.dump(key, handle)
            return load_key(path)

    def test_defaults_filled(self):
        key = self.load({"base_commit": "x", "items": [{"n": 1, "kind": "fake"}, {"n": 2, "kind": "real"}]})
        self.assertEqual([(i["difficulty"], i["latent"]) for i in key["items"]], [("fake", False), ("unrated", False)])

    def test_bad_keys_rejected(self):
        with self.assertRaises(ValueError):
            self.load({"items": []})
        with self.assertRaises(ValueError):
            self.load({"base_commit": "x", "items": [{"n": 1, "kind": "maybe"}]})
        with self.assertRaises(ValueError):
            self.load({"base_commit": "x", "items": [{"n": 1, "kind": "real"}, {"n": 1, "kind": "fake"}]})


class SaveDiffsTest(unittest.TestCase):
    def test_diff_of_each_committed_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout
            run("init", "-q")
            run("config", "user.name", "T")
            run("config", "user.email", "t@example.com")
            with open(os.path.join(repo, "a.txt"), "w") as handle:
                handle.write("one\n")
            run("add", "-A")
            run("commit", "-qm", "init")
            with open(os.path.join(repo, "a.txt"), "w") as handle:
                handle.write("two\n")
            run("commit", "-qam", "fix")
            commit = run("rev-parse", "--short", "HEAD").strip()
            totals = {"items": [{"n": 3, "kind": "real", "commit": commit}, {"n": 4, "kind": "fake", "commit": ""}]}
            out = os.path.join(tmp, "out")
            save_diffs(totals, repo, out)
            self.assertEqual(os.listdir(os.path.join(out, "diffs")), ["03-real.diff"])
            with open(os.path.join(out, "diffs", "03-real.diff")) as handle:
                self.assertIn("+two", handle.read())


class BatchCommandTest(unittest.TestCase):
    def test_options_pass_through(self):
        args = argparse.Namespace(model="m", provider=["p"], strict_schema=True, test_cmd="pytest {test_file}",
                                  rules="/r.md", tries=None, max_tokens=5000, outline=["tests/helpers.py"])
        cmd = batch_command(args, "/b/findings.json", "/w", "/o")
        self.assertEqual(cmd[2:5], ["/b/findings.json", "--worktree", "/w"])
        for pair in (["--model", "m"], ["--provider", "p"], ["--test-cmd", "pytest {test_file}"],
                     ["--rules", "/r.md"], ["--max-tokens", "5000"], ["--outline", "tests/helpers.py"]):
            self.assertIn(pair[0], cmd)
            self.assertEqual(cmd[cmd.index(pair[0]) + 1], pair[1])
        self.assertIn("--strict-schema", cmd)
        self.assertNotIn("--tries", cmd)


if __name__ == "__main__":
    unittest.main()
