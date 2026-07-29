from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from psyclaw import launcher


class Process:
    def __init__(self, code=None, timeout=False): self.pid, self.code, self.timeout, self.signals = 44, code, timeout, []
    def poll(self): return self.code
    def send_signal(self, value): self.signals.append(value)
    def wait(self, timeout=None):
        if self.timeout: raise subprocess.TimeoutExpired("test", timeout)


class LauncherTest(unittest.TestCase):
    env = {"PATH": "/bin", "PSYCLAW_MODEL": "local/model", "PSYCLAW_API_KEY": "secret"}

    def test_public_environment_never_contains_credentials(self):
        public = launcher.frontend_environment(self.env, True)
        self.assertNotIn("PSYCLAW_API_KEY", public)
        self.assertEqual(public["VITE_STT_URL"], "http://127.0.0.1:8001")

    def test_frontend_marker_tracks_the_lockfile_and_failed_install_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, "FRONTEND", Path(directory)):
            (launcher.FRONTEND / "package-lock.json").write_text("one")
            calls = []
            def command(args, **kwargs): calls.append(args); return subprocess.CompletedProcess(args, 1)
            with self.assertRaisesRegex(launcher.LauncherError, "Frontend setup failed"):
                launcher.install_frontend(command, self.env)
            self.assertFalse(launcher.lock_marker().exists())
            def success(args, **kwargs): calls.append(args); return subprocess.CompletedProcess(args, 0)
            (launcher.FRONTEND / "node_modules").mkdir()
            launcher.install_frontend(success, self.env)
            launcher.install_frontend(success, self.env)
            (launcher.FRONTEND / "package-lock.json").write_text("two")
            launcher.install_frontend(success, self.env)
            self.assertEqual(calls, [["npm", "ci"], ["npm", "ci"], ["npm", "ci"]])

    def test_missing_or_partial_settings_fail_before_starting(self):
        for env in ({}, {**self.env, "PSYCLAW_STT_MODEL": "provider/model"}):
            with self.subTest(env=env), self.assertRaises(launcher.LauncherError):
                launcher.run(env, popen=lambda *args, **kwargs: self.fail("started"), which=lambda _: "/bin/npm")

    def test_nominal_order_and_health_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, "FRONTEND", Path(directory)):
            (launcher.FRONTEND / "package-lock.json").write_text("lock")
            (launcher.FRONTEND / "node_modules").mkdir()
            (launcher.lock_marker()).write_text(launcher.lock_digest())
            started = []
            processes = [Process(), Process(), Process()]
            def popen(args, **kwargs):
                started.append(args)
                return processes[len(started) - 1]
            launcher.run({**self.env, "PSYCLAW_STT_MODEL": "x/y", "PSYCLAW_STT_API_KEY": "key"}, popen=popen, ready=lambda: True, sleep=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
            self.assertEqual(started, [launcher.server_command(), launcher.stt_command(), launcher.ui_command()])

    def test_services_use_a_group_and_windows_timeout_uses_taskkill_tree(self):
        process = Process(timeout=True)
        calls = []
        with patch.object(launcher.os, "name", "nt"):
            launcher.stop([("ui", process)], command=lambda args, **kwargs: calls.append(args))
        self.assertEqual(calls, [["taskkill", "/PID", "44", "/T", "/F"]])


if __name__ == "__main__": unittest.main()
