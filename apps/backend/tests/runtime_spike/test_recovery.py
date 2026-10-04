"""Actual Git/SQLite with a sandbox contract double; no provider/QA claim."""
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.runtime_spike.experiment import Experiment, FEATURE, save
from app.workspace import parse_manifest, reference_manifest_dict


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exp = Experiment(Path(self.tmp.name))
        self.exp.sup.sandbox.owned = lambda **kwargs: []
        self.scope = "recover"
        base = self.exp.sup.create_project(self.scope)
        run = self.exp.sup.start_attempt(self.scope, ticket_id="T", scope_version=1, role="developer",
            attempt=1, generation=1, lease_id="L", manifest=parse_manifest(reference_manifest_dict()))
        identity = {"project_id": self.scope, "scope_version": 1, "base_sha": base, "role": "developer", "ticket_id": "T"}
        self.exp.journal.create(self.scope, identity, {"model_calls": 3, "tool_calls": 3, "active_s": 100, "output_tokens": 100})
        self.exp.journal.start(self.scope, 1)
        checkpoint = self.exp.sup.checkpoint(run.ref, run.credential, "fixture checkpoint")
        save(self.exp.path(self.scope), {**identity, "run_id": run.ref.run_id, "credential": run.credential,
            "generation": 1, "candidate": None, "checkpoint": checkpoint, "prompt": FEATURE})
        self.iid = self.exp.journal.request_input(self.scope, 1, "q", checkpoint)

    def tearDown(self):
        self.tmp.cleanup()

    def answer(self, exp=None):
        return (exp or self.exp).resume(self.scope, self.iid, "answer-id", "remove row")

    def test_crash_after_answer_before_generation_rotation_reconciles_once(self):
        with patch.object(self.exp.sup, "renew_generation", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.answer()
        reopened = Experiment(Path(self.tmp.name))
        reopened.sup.sandbox.owned = lambda **kwargs: []
        result = self.answer(reopened)
        self.assertEqual(result, {"duplicate": True, "generation": 2})
        self.assertEqual(reopened.load(self.scope)["generation"], 2)
        self.assertEqual(reopened.journal.inspect(self.scope)["generation"], 2)
        self.assertEqual(self.answer(reopened), result)

    def test_crash_after_rotation_before_session_save_recovers_credential(self):
        from app.runtime_spike import experiment
        with patch.object(experiment, "save", side_effect=RuntimeError("crash before save")):
            with self.assertRaises(RuntimeError):
                self.answer()
        result = self.answer()
        c = self.exp.load(self.scope)
        self.assertTrue(result["duplicate"])
        self.exp.sup.list_files(self.exp.ref(self.scope), c["credential"])
        self.assertEqual(c["generation"], 2)

    def test_answer_retry_during_new_runtime_does_not_resume_again(self):
        self.answer()
        self.exp.journal.start(self.scope, 2)
        c = self.exp.load(self.scope)
        c["pid"] = 12345  # Mere transport state; no fake process is signalled.
        save(self.exp.path(self.scope), c)
        self.assertEqual(self.answer(), {"duplicate": True, "generation": 2})
        self.assertEqual(self.exp.journal.inspect(self.scope)["status"], "running")

    def test_stop_after_crash_before_session_save_still_revokes(self):
        from app.runtime_spike import experiment
        with patch.object(experiment, "save", side_effect=RuntimeError("crash before save")):
            with self.assertRaises(RuntimeError):
                self.answer()
        self.assertEqual(self.exp.load(self.scope)["generation"], 1)
        self.exp.stop(self.scope)
        self.assertEqual(self.exp.journal.inspect(self.scope)["status"], "stopped")
        self.assertIsNone(self.exp.sup._store(self.exp.ref(self.scope)).state()["credential_sha256"])

    def test_rejected_restart_does_not_checkpoint(self):
        ref = self.exp.ref(self.scope)
        attempt_ref = self.exp.sup._store(ref).spec().attempt_ref
        before = self.exp.sup.broker(self.scope).resolve(attempt_ref)
        with self.assertRaisesRegex(Exception, "recovery requires failed scope"):
            self.exp.restart(self.scope)
        self.assertEqual(self.exp.sup.broker(self.scope).resolve(attempt_ref), before)

    def test_transport_failure_after_worker_exit_still_revokes_and_cleans(self):
        # The reader fails before the first poll; the monitoring loop never runs.
        # Real Git/SQLite, process/transport doubles: no provider or Docker claim.
        self.answer()
        reader_failed = threading.Event()
        proc = Mock(pid=12345, returncode=1, stdout=io.BytesIO(b"x" * 65537))

        def exited():
            if not reader_failed.wait(2):
                raise RuntimeError("transport reader did not reach failure")
            return 1

        proc.poll.side_effect = exited
        proc.wait.return_value = 1
        from app.runtime_spike import experiment, preflight
        real_popen = experiment.subprocess.Popen

        def launch(command, *args, **kwargs):
            if str(command[0]).endswith("contract-python"):
                return proc
            return real_popen(command, *args, **kwargs)

        with patch.object(preflight, "collect", return_value={"status": "prerequisites_ready"}), \
             patch.object(preflight, "local_environment", return_value={
                 "SPIKE_PROVIDER": "openrouter", "SPIKE_MODEL": "contract-model",
                 "OPENROUTER_API_KEY": "contract-secret"}), \
             patch.object(experiment.subprocess, "Popen", side_effect=launch), \
             patch.object(experiment.os, "killpg", side_effect=lambda *args: reader_failed.set()), \
             patch.object(self.exp.sup, "stop_run", wraps=self.exp.sup.stop_run) as cleanup:
            result = self.exp.start(self.scope, Path("contract-python"), Path("contract-env"), Path("contract-source"))
        self.assertEqual(result["accounting"]["status"], "failed")
        cleanup.assert_called_once_with(self.exp.ref(self.scope), "budget_or_stop")
        self.assertIsNone(self.exp.sup._store(self.exp.ref(self.scope)).state()["credential_sha256"])


if __name__ == "__main__":
    unittest.main()
