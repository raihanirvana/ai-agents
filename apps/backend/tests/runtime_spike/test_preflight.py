import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.runtime_spike.preflight import PIN, collect, local_environment, probe


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "hermes"
        self.source.mkdir()
        self.python = Path(self.tmp.name) / "python"
        self.python.touch()
        self.calls = []
        self.engine = "linux"
        self.head = PIN["commit"]
        self.dirty = ""
        self.metadata = {"python": [3, 13, 16], "version": PIN["package_version"], "direct_url": {
            "url": self.source.as_uri(), "dir_info": {"editable": True}}}

    def fake_run(self, args):
        self.calls.append(args)
        if args[-1] == "--version":
            return 0, "git version test-double"
        if "info" in args:
            return 0, self.engine
        if "image" in args:
            return 0, "sha256:" + "1" * 64
        if "rev-parse" in args:
            return 0, self.head
        if "status" in args:
            return 0, self.dirty
        if "-I" in args:
            return 0, json.dumps(self.metadata)
        raise AssertionError(args)

    def collect(self, **overrides):
        args = dict(provider="openrouter", model="vendor/model", hermes_source=self.source,
                    hermes_python=self.python, system="Linux", python_version=(3, 12, 10),
                    env={"OPENROUTER_API_KEY": "secret-test-key"}, run=self.fake_run, which=lambda x: x)
        args.update(overrides)
        return collect(**args)

    def test_ready_prerequisites_never_claim_real_runtime_verified(self):
        report = self.collect()
        self.assertEqual(report["status"], "prerequisites_ready")
        self.assertIs(report["real_runtime_verified"], False)
        self.assertNotIn("secret-test-key", json.dumps(report))
        self.assertNotIn("secret-test-key", json.dumps(self.calls))

    def test_windows_host_blocked_even_with_working_docker(self):
        self.assertEqual(self.collect(system="Windows")["status"], "blocked")

    def test_windows_engine_cannot_satisfy_linux_sandbox(self):
        self.engine = "windows"
        report = self.collect()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(any("image" in args for args in self.calls))

    def test_absent_key_or_model_does_not_fall_back(self):
        for overrides in ({"env": {}}, {"model": ""}, {"provider": "other"},
                          {"env": {"DEEPSEEK_API_KEY": "other-provider-key"}}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.collect(**overrides)["status"], "blocked")

    def test_dirty_or_different_runtime_source_rejected_without_install_probe(self):
        for head, dirty in (("f" * 40, ""), (PIN["commit"], " M run_agent.py"),
                            (PIN["commit"], "?? injected.py")):
            with self.subTest(head=head, dirty=dirty):
                self.head, self.dirty = head, dirty
                self.calls.clear()
                self.assertEqual(self.collect()["status"], "blocked")
                self.assertFalse(any("-I" in args for args in self.calls))

    def test_package_from_different_checkout_or_version_is_blocked(self):
        for metadata in ({"version": "wrong"}, {"version": PIN["package_version"]},
                         {"version": PIN["package_version"], "direct_url": {"url": "file:///other", "dir_info": {"editable": True}}},
                         ["invalid metadata"]):
            with self.subTest(metadata=metadata):
                self.metadata = metadata
                self.assertEqual(self.collect()["status"], "blocked")

    def test_hermes_venv_interpreter_not_backend_python_gates_pin_range(self):
        report = self.collect(python_version=(3, 14, 4))
        self.assertEqual(report["status"], "prerequisites_ready")
        self.assertEqual(report["hermes_python"], "3.13.16")
        for python in ([3, 14, 0], [3, 10, 12], [], ["x"]):
            with self.subTest(python=python):
                self.metadata = {**self.metadata, "python": python}
                report = self.collect(python_version=(3, 12, 10))
                self.assertEqual(report["status"], "blocked")
                self.assertIsNone(report["hermes_python"])

    def test_venv_interpreter_symlink_is_invoked_without_resolving_to_base_python(self):
        launcher = Path(self.tmp.name) / "venv-python"
        try:
            launcher.symlink_to(self.python)
        except OSError:
            self.skipTest("this host cannot create symlinks")
        self.assertEqual(self.collect(hermes_python=launcher)["status"], "prerequisites_ready")
        calls = [args for args in self.calls if "-I" in args]
        self.assertEqual(calls[0][0], str(launcher.absolute()))
        self.assertNotEqual(calls[0][0], str(launcher.resolve()))

    def test_missing_tools_and_probe_failure_are_blockers(self):
        self.assertEqual(self.collect(which=lambda _: None)["status"], "blocked")
        self.assertEqual(self.collect(run=lambda _: (None, "private error text"))["status"], "blocked")
        self.assertNotIn("private error text", json.dumps(self.collect(run=lambda _: (1, "private error text"))))

    def test_probe_strips_secrets_and_git_injection_environment(self):
        result = subprocess.CompletedProcess(["git"], 1, b"", b"PRIVATE credential")
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "secret-test-key", "GIT_CONFIG_COUNT": "1"}), \
                patch("subprocess.run", return_value=result) as child:
            self.assertEqual(probe(["git", "--version"]), (1, ""))
        kwargs = child.call_args.kwargs
        self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
        self.assertNotIn("GIT_CONFIG_COUNT", kwargs["env"])
        self.assertEqual(kwargs["timeout"], 15)
        self.assertNotIn("shell", kwargs)

    def test_probe_keeps_docker_engine_selection_used_by_sandbox(self):
        result = subprocess.CompletedProcess(["docker"], 0, b"linux", b"")
        selectors = {"DOCKER_HOST": "unix:///run/user/1000/docker.sock", "DOCKER_CONTEXT": "rootless"}
        with patch.dict("os.environ", selectors), patch("subprocess.run", return_value=result) as child:
            probe(["docker", "info"])
        for key, value in selectors.items():
            self.assertEqual(child.call_args.kwargs["env"][key], value)

    def test_timeout_is_reported_without_raw_process_output(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("test", 15, output=b"secret")):
            self.assertEqual(probe(["missing-binary"]), (None, ""))

    def test_local_file_limits_fields_and_disables_secret_interpolation(self):
        path = Path(self.tmp.name) / "provider.env"
        path.write_text('SPIKE_PROVIDER=openrouter\nOPENROUTER_API_KEY=${HOST_SECRET}\nTERMINAL_CWD=/private\n')
        with patch.dict("os.environ", {}, clear=True):
            env = local_environment(path)
            self.assertEqual(env["OPENROUTER_API_KEY"], "${HOST_SECRET}")
            self.assertNotIn("TERMINAL_CWD", env)
            self.assertNotIn("OPENROUTER_API_KEY", __import__("os").environ)

    def test_process_environment_has_priority_over_local_file(self):
        path = Path(self.tmp.name) / "provider.env"
        path.write_text('OPENROUTER_API_KEY=file-key\nSPIKE_MODEL=file-model\n')
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "process-key", "SPIKE_MODEL": ""}, clear=True):
            env = local_environment(path)
            self.assertEqual(env["OPENROUTER_API_KEY"], "process-key")
            self.assertEqual(env["SPIKE_MODEL"], "")


if __name__ == "__main__":
    unittest.main()
