"""Journal fault/concurrency checks; these do not prove provider compatibility."""
import concurrent.futures
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.runtime_spike.journal import AdmissionError, Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "journal.sqlite3"
        self.j = Journal(self.path)
        self.identity = {"project_id": "one", "scope_version": 1, "base_sha": "a" * 40}
        self.limits = {"model_calls": 2, "tool_calls": 2, "active_s": 100, "output_tokens": 1024}
        self.j.create("s", self.identity, self.limits)
        self.j.start("s", 1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reservation_survives_restart_and_unknown_is_not_zero(self):
        rid = self.j.reserve("s", 1, "model", "m")
        reopened = Journal(self.path)
        report = reopened.inspect("s")
        self.assertEqual(report["model_calls"], 1)
        self.assertEqual(report["reservations"][0]["id"], rid)
        self.assertIsNone(report["reservations"][0]["result"])
        reopened.reserve("s", 1, "model", "retry")
        with self.assertRaises(AdmissionError):
            reopened.reserve("s", 1, "model", "third")

    def test_concurrent_admission_never_exceeds_cap(self):
        def reserve(_):
            try:
                return Journal(self.path).reserve("s", 1, "tool", "t")
            except AdmissionError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(16)))
        self.assertEqual(sum(r is not None for r in results), 2)
        self.assertEqual(self.j.inspect("s")["tool_calls"], 2)

    def test_persisted_answer_duplicate_has_one_generation_and_keeps_budget(self):
        self.j.reserve("s", 1, "tool", "input")
        iid = self.j.request_input("s", 1, "zero quantity?", {"sha": "a" * 40})
        reopened = Journal(self.path)
        first = reopened.answer("s", iid, "answer-1", "remove row", self.identity)
        second = reopened.answer("s", iid, "answer-1", "remove row", self.identity)
        self.assertEqual(first, {"duplicate": False, "generation": 2})
        self.assertEqual(second, {"duplicate": True, "generation": 2})
        reopened.start("s", 2)
        self.assertEqual(reopened.inspect("s")["tool_calls"], 1)
        with self.assertRaises(AdmissionError):
            reopened.reserve("s", 1, "tool", "stale")
        with self.assertRaises(AdmissionError):
            reopened.event("s", 1, "candidate", {})

    def test_late_input_does_not_resurrect_stopped_run(self):
        iid = self.j.request_input("s", 1, "q", {})
        self.j.pause("s", 1, "stopped")
        with self.assertRaises(AdmissionError):
            self.j.answer("s", iid, "a", "yes", self.identity)

    def test_scope_and_base_change_requires_replan(self):
        iid = self.j.request_input("s", 1, "q", {})
        for identity in ({**self.identity, "scope_version": 2}, {**self.identity, "base_sha": "b" * 40}):
            with self.assertRaises(AdmissionError):
                self.j.answer("s", iid, "a", "yes", identity)
        self.assertEqual(self.j.inspect("s")["generation"], 1)

    def test_waiting_excluded_active_duration_enforced(self):
        with patch("app.runtime_spike.journal.time.time", return_value=time.time() + 10):
            iid = self.j.request_input("s", 1, "q", {})
        elapsed = self.j.inspect("s")["active_s"]
        with patch("app.runtime_spike.journal.time.time", return_value=time.time() + 2000):
            self.assertEqual(self.j.inspect("s")["active_s"], elapsed)
            self.j.answer("s", iid, "a", "yes", self.identity)
            self.j.start("s", 2)
        with patch("app.runtime_spike.journal.time.time", return_value=time.time() + 3000):
            with self.assertRaises(AdmissionError):
                self.j.reserve("s", 2, "model", "late")

    def test_late_accounting_does_not_change_stopped_state(self):
        rid = self.j.reserve("s", 1, "model", "m")
        self.j.pause("s", 1, "stopped")
        self.j.finish(rid, {"usage": {"completion_tokens": 5}})
        self.j.finish(rid, {"usage": {"completion_tokens": 999}})
        row = self.j.inspect("s")
        self.assertEqual(row["status"], "stopped")
        self.assertEqual(row["reservations"][0]["result"]["usage"]["completion_tokens"], 5)
        with self.assertRaises(AdmissionError):
            self.j.event("s", 1, "completed", {})

    def test_finite_limits_required(self):
        for value in (float("inf"), float("nan"), 0, -1, True):
            with self.assertRaises(ValueError):
                self.j.create("bad", {}, {**self.limits, "active_s": value})

    def test_stream_cursor_and_scope_isolation(self):
        self.j.event("s", 1, "delta", {"text": "one"})
        cursor = self.j.stream("s")[0]["cursor"]
        self.j.event("s", 1, "delta", {"text": "two"})
        self.assertEqual(len(self.j.stream("s", cursor)), 1)
        self.assertEqual(self.j.stream("other"), [])

    def test_budget_extension_audited_idempotent_and_does_not_reset_usage(self):
        self.j.reserve("s", 1, "model", "m")
        self.j.pause("s", 1, "failed")
        for duplicate in (False, True):
            r = self.j.extend_budget("s", "operator-1", model_calls=3, tool_calls=4, reason="operator approved")
            self.assertEqual(r["duplicate"], duplicate)
            self.assertEqual(r["limits"]["model_calls"], 5)
        self.assertEqual(self.j.inspect("s")["model_calls"], 1)
        with self.assertRaises(AdmissionError):
            self.j.extend_budget("s", "operator-1", model_calls=10, tool_calls=4, reason="operator approved")

    def test_failed_restart_preserves_caps_and_fences_old_generation(self):
        self.j.reserve("s", 1, "model", "m")
        self.j.pause("s", 1, "failed")
        gen = Journal(self.path).restart_failed("s", self.identity)
        self.assertEqual(gen, 2)
        self.j.start("s", 2)
        self.j.reserve("s", 2, "model", "retry")
        with self.assertRaises(AdmissionError):
            self.j.reserve("s", 2, "model", "third")


if __name__ == "__main__":
    unittest.main()
