from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from psyclaw import launcher


class FakeProcess:
    def __init__(self, return_code: int | None = None) -> None:
        self.pid = 44
        self.return_code = return_code

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> None:
        del timeout


class LauncherTest(unittest.TestCase):
    environment = {
        "PATH": "/bin",
        "PSYCLAW_MODEL": "local/model",
        "PSYCLAW_API_KEY": "secret",
    }

    def frontend(self):
        directory = tempfile.TemporaryDirectory()
        frontend = Path(directory.name)
        (frontend / "package-lock.json").write_text("lock")
        (frontend / "node_modules").mkdir()
        return directory, frontend

    def run_with(self, environment, *, ready=True, processes=None):
        started = []
        children = iter(processes or [FakeProcess(), FakeProcess(), FakeProcess()])

        def popen(arguments, **_kwargs):
            started.append(arguments)
            return next(children)

        def interrupt_after_start(_seconds: float) -> None:
            raise KeyboardInterrupt

        with patch("psyclaw.launcher.os.killpg"):
            launcher.run(
                environment,
                popen=popen,
                ready=lambda: ready,
                which=lambda _: "/bin/npm",
                sleep=interrupt_after_start,
            )
        return started

    def test_nominal_start_with_speech_to_text(self) -> None:
        directory, frontend = self.frontend()
        with directory, patch.object(launcher, "FRONTEND", frontend):
            (launcher.lock_marker()).write_text(launcher.lock_digest())
            started = self.run_with({**self.environment, "PSYCLAW_STT_MODEL": "x/y", "PSYCLAW_STT_API_KEY": "key"})
        self.assertEqual(started, [launcher.server_command(), launcher.stt_command(), launcher.ui_command()])

    def test_nominal_start_without_speech_to_text(self) -> None:
        directory, frontend = self.frontend()
        with directory, patch.object(launcher, "FRONTEND", frontend):
            (launcher.lock_marker()).write_text(launcher.lock_digest())
            started = self.run_with(self.environment)
        self.assertEqual(started, [launcher.server_command(), launcher.ui_command()])

    def test_health_failure_stops_before_stt_or_ui(self) -> None:
        directory, frontend = self.frontend()
        api = FakeProcess()
        started = []

        def popen(arguments, **_kwargs):
            started.append(arguments)
            return api

        with directory, patch.object(launcher, "FRONTEND", frontend):
            (launcher.lock_marker()).write_text(launcher.lock_digest())
            with patch("psyclaw.launcher.os.killpg") as killpg:
                with self.assertRaisesRegex(launcher.LauncherError, "did not become ready"):
                    launcher.run(self.environment, popen=popen, ready=lambda: False, which=lambda _: "/bin/npm")
        self.assertEqual(started, [launcher.server_command()])
        killpg.assert_called_once_with(api.pid, launcher.signal.SIGTERM)

    def test_child_exit_cleans_up_other_running_services(self) -> None:
        directory, frontend = self.frontend()
        api = FakeProcess()
        ui = FakeProcess(return_code=1)
        started = []

        def popen(arguments, **_kwargs):
            started.append(arguments)
            return (api, ui)[len(started) - 1]

        with directory, patch.object(launcher, "FRONTEND", frontend):
            (launcher.lock_marker()).write_text(launcher.lock_digest())
            with patch("psyclaw.launcher.os.killpg") as killpg:
                with self.assertRaisesRegex(launcher.LauncherError, "Assistant UI stopped"):
                    launcher.run(self.environment, popen=popen, ready=lambda: True, which=lambda _: "/bin/npm")
        self.assertEqual(started, [launcher.server_command(), launcher.ui_command()])
        killpg.assert_called_once_with(api.pid, launcher.signal.SIGTERM)

    def test_npm_absent_starts_no_process(self) -> None:
        with self.assertRaises(launcher.LauncherError):
            launcher.run(self.environment, popen=lambda *_args, **_kwargs: self.fail("started"), which=lambda _: None)

    def test_public_frontend_environment_excludes_secrets(self) -> None:
        public = launcher.frontend_environment(self.environment, True)
        self.assertNotIn("PSYCLAW_API_KEY", public)
        self.assertEqual(public["VITE_STT_URL"], "http://127.0.0.1:8001")

    def test_frontend_install_marker_follows_the_lockfile(self) -> None:
        directory, frontend = self.frontend()
        calls = []

        def command(arguments, **_kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 0)

        with directory, patch.object(launcher, "FRONTEND", frontend):
            launcher.install_frontend(command, self.environment)
            self.assertTrue(launcher.lock_marker().is_file())
            launcher.install_frontend(command, self.environment)
            (frontend / "package-lock.json").write_text("changed")
            launcher.install_frontend(command, self.environment)
            marker = launcher.lock_marker()
            marker.unlink()

            def failed(arguments, **_kwargs):
                return subprocess.CompletedProcess(arguments, 1)

            with self.assertRaises(launcher.LauncherError):
                launcher.install_frontend(failed, self.environment)
            self.assertFalse(marker.exists())

        self.assertEqual(calls, [["npm", "ci"], ["npm", "ci"]])

    def test_windows_cleanup_kills_every_process_tree(self) -> None:
        commands = []
        with patch.object(launcher.os, "name", "nt"):
            launcher.stop([("api", FakeProcess()), ("ui", FakeProcess())], command=lambda args, **_kwargs: commands.append(args))
        self.assertEqual(commands, [["taskkill", "/PID", "44", "/T", "/F"], ["taskkill", "/PID", "44", "/T", "/F"]])


if __name__ == "__main__":
    unittest.main()
