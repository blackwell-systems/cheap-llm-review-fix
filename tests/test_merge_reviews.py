import contextlib
import io
import json
import os
import tempfile
import unittest

from merge_reviews import evidence_found, passes_checks, symbol_check, symbol_column, write_batch

SOURCE = '''def add(a, b):
    total = a + b
    return total


def sub(a, b):
    return a - b
'''


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "m.py"), "w") as handle:
            handle.write(SOURCE)

    def tearDown(self):
        self.tmp.cleanup()


class SymbolCheckTest(RepoTest):
    def test_evidence_inside(self):
        self.assertEqual(symbol_check(self.root, "src/m.py", "add", "total = a + b"), "L1-L3")

    def test_evidence_outside(self):
        self.assertEqual(symbol_check(self.root, "src/m.py", "add", "return a - b"), "L1-L3, evidence outside")

    def test_evidence_partly_outside(self):
        result = symbol_check(self.root, "src/m.py", "add", "total = a + b\nreturn a - b")
        self.assertEqual(result, "L1-L3, evidence partly outside")

    def test_symbol_not_found(self):
        self.assertEqual(symbol_check(self.root, "src/m.py", "nope", "x"), "symbol not found")

    def test_file_not_found(self):
        self.assertEqual(symbol_check(self.root, "src/none.py", "add", "x"), "file not found")

    def test_unknown_language_and_skip(self):
        with open(os.path.join(self.root, "a.rb"), "w") as handle:
            handle.write("def add(a)\n  a\nend\n")
        self.assertEqual(symbol_check(self.root, "a.rb", "add", "a"), "symbol not found")
        self.assertEqual(symbol_check(self.root, "a.rb", "add", "a", skip=True), "not checked")


class SymbolColumnTest(RepoTest):
    def finding(self, test_file):
        return {"file": "src/m.py", "symbol": "add", "evidence": "total = a + b", "test_file": test_file}

    def test_existing_test_file_adds_nothing(self):
        with open(os.path.join(self.root, "test_m.py"), "w") as handle:
            handle.write("")
        self.assertEqual(symbol_column(self.root, self.finding("test_m.py")), "L1-L3")

    def test_missing_test_file_is_flagged_in_the_symbol_column(self):
        self.assertEqual(symbol_column(self.root, self.finding("tests/nope.py")),
                         "L1-L3; test file tests/nope.py not found")

    def test_flag_survives_skipped_symbols_and_no_test_file_is_fine(self):
        self.assertEqual(symbol_column(self.root, self.finding("x.py"), skip=True),
                         "not checked; test file x.py not found")
        self.assertEqual(symbol_column(self.root, self.finding("")), "L1-L3")


class EvidenceTest(RepoTest):
    def test_found_ignoring_whitespace(self):
        self.assertEqual(evidence_found(self.root, "src/m.py", "total   =  a + b"), "ok")

    def test_multiline_ok_partial_and_missing(self):
        self.assertEqual(evidence_found(self.root, "src/m.py", "total = a + b\nreturn total"), "ok")
        self.assertEqual(evidence_found(self.root, "src/m.py", "total = a + b\nreturn invented"), "partial")
        self.assertEqual(evidence_found(self.root, "src/m.py", "return invented"), "NOT FOUND")

    def test_file_missing_and_empty(self):
        self.assertEqual(evidence_found(self.root, "src/none.py", "x"), "file not found")
        self.assertEqual(evidence_found(self.root, "src/m.py", "  \n"), "no evidence")


def finding(fid, model, evidence="ok", symbol="L1-L3", test_file="tests/t.py"):
    return {"id": fid, "model": model, "title": "t", "severity": "high", "confidence": "high", "kind": "bug",
            "file": "src/m.py", "symbol": "add", "evidence": "x", "situation": "s", "fix": "f",
            "test_file": test_file, "related": [], "evidence_check": evidence, "symbol_check": symbol}


class WriteBatchTest(unittest.TestCase):
    def batch(self, groups, confirmed):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "batch.json")
            with contextlib.redirect_stdout(io.StringIO()):
                write_batch(path, groups, confirmed)
            with open(path) as handle:
                return json.load(handle)["findings"]

    def test_agreement_auto_includes_both_versions(self):
        groups = [{"key": ("f", "add"), "title": "t", "findings": [finding("1a", "mimo"), finding("1b", "glm")]}]
        chosen = self.batch(groups, set())
        self.assertEqual([c["_id"] for c in chosen], ["1a", "1b"])
        self.assertEqual(chosen[0]["_model"], "mimo")

    def test_agreement_needs_checks_to_pass(self):
        groups = [{"key": ("f", "add"), "title": "t", "findings": [
            finding("1a", "mimo"), finding("1b", "glm", symbol="L1-L3, evidence outside")]}]
        self.assertEqual([c["_id"] for c in self.batch(groups, set())], ["1a"])
        groups = [{"key": ("f", "add"), "title": "t", "findings": [
            finding("1a", "mimo"), finding("1b", "glm", evidence="NOT FOUND")]}]
        self.assertEqual([c["_id"] for c in self.batch(groups, set())], ["1a"])

    def test_single_model_excluded(self):
        groups = [{"key": ("f", "add"), "title": "t", "findings": [finding("1a", "mimo")]}]
        self.assertEqual(self.batch(groups, set()), [])

    def test_confirm_by_group_and_by_id(self):
        groups = [{"key": ("f", "add"), "title": "t", "findings": [finding("1a", "mimo"), finding("1b", "mimo")]},
                  {"key": ("f", "sub"), "title": "t", "findings": [finding("2a", "mimo"), finding("2b", "glm", evidence="NOT FOUND")]}]
        # group 2 has two models but 2b fails its checks; 2a passes only through agreement? no: 2a needs agreement and passes.
        self.assertEqual([c["_id"] for c in self.batch(groups, {"1"})], ["1a", "1b", "2a"])
        self.assertEqual([c["_id"] for c in self.batch(groups, {"1b"})], ["1b", "2a"])
        self.assertEqual([c["_id"] for c in self.batch(groups, {"2b"})], ["2a", "2b"])

    def test_missing_test_file_stays_out_of_the_auto_batch_but_can_be_confirmed(self):
        flagged = "L1-L3; test file tests/t.py not found"
        groups = [{"key": ("f", "add"), "title": "t", "findings": [
            finding("1a", "mimo", symbol=flagged), finding("1b", "glm", symbol=flagged)]},
            {"key": ("f", "sub"), "title": "t", "findings": [finding("2a", "mimo"), finding("2b", "glm")]}]
        self.assertEqual([c["_id"] for c in self.batch(groups, set())], ["2a", "2b"])
        self.assertEqual([c["_id"] for c in self.batch(groups, {"1"})], ["1a", "1b", "2a", "2b"])
        self.assertFalse(passes_checks(finding("1a", "m", symbol="not checked; test file x.py not found")))

    def test_passes_checks(self):
        self.assertTrue(passes_checks(finding("1a", "m", symbol="not checked")))
        self.assertFalse(passes_checks(finding("1a", "m", symbol="symbol not found")))
        self.assertFalse(passes_checks(finding("1a", "m", evidence="partial")))


if __name__ == "__main__":
    unittest.main()
