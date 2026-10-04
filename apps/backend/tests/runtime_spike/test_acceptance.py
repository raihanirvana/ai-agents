"""Fail-closed authoritative report checks, not substitutes for browser execution."""
import copy
import json
import unittest

from app.runtime_spike.acceptance import SUITE, validate_report


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        spec = json.loads((SUITE / "mandatory.json").read_text())
        self.report = {"schema": 1, "invocation_id": "i", "target_id": "t", "suite_digest": "d",
                       "discovered": 4, "executed": 4, "passed": 4, "failed": 0, "skipped": 0,
                       "tests": [{"id": k, "status": "passed", "uac": v} for k, v in spec["mandatory"].items()]}

    def check(self, report):
        return validate_report(report, invocation="i", target_id="t", digest="d")

    def test_complete_pass_and_failure(self):
        self.assertEqual(self.check(self.report), "passed")
        self.report["tests"][1]["status"] = "failed"
        self.report.update(passed=3, failed=1)
        self.assertEqual(self.check(self.report), "failed")

    def test_zero_missing_duplicate_skipped_and_wrong_coverage_incomplete(self):
        changes = [lambda r: r.update(tests=[]), lambda r: r["tests"].pop(),
                   lambda r: r["tests"].append(r["tests"][0]),
                   lambda r: r["tests"][0].update(status="skipped"),
                   lambda r: r["tests"][0].update(uac=[]), lambda r: r.update(executed=0),
                   lambda r: r.update(skipped=1), lambda r: r.update(passed=3),
            lambda r: r.update(discovered=True), lambda r: r["tests"][0].update(id=[]),
            lambda r: r.update(schema=True)]
        for change in changes:
            with self.subTest(change=change):
                r = copy.deepcopy(self.report)
                change(r)
                self.assertEqual(self.check(r), "incomplete")

    def test_wrong_target_suite_invocation_rejected(self):
        for key in ("target_id", "suite_digest", "invocation_id"):
            self.assertEqual(self.check({**self.report, key: "other"}), "incomplete")
        for r in (None, {}, "target says passed", {**self.report, "tests": [None]}):
            self.assertEqual(self.check(r), "incomplete")


if __name__ == "__main__":
    unittest.main()
