from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from psyclaw import launcher


class FakeProcess:
    def __init__(self, return_code: int | None = None) -> None:
        self.pid = 1234
        self.return_code = return_code
        self.terminated = False
        self.killed = False
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def kill(self) -> None:
        self.killed = True
        self.return_code = 0

    def send_signal(self, signal_number: int) -> None:
        self.signals.append(signal_number)
        self.return_code = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.return_code or 0


class LauncherTest(unittest.TestCase):
    environment = {
        "PATH": "/test/bin",
        "PSYCLAW_MODEL": "local/model",
        "PSYCLAW_API_KEY": "never-expose-this-key",
        "PSYCLAW_API_BASE": "http://127.0.0.1:1234/v1",
    }

    def test_frontend_environment_contains_only_public_connection_values(self) -> None:
        public_environment = launcher.frontend_environment(self.environment)

        self.assertEqual(
            public_environment,
            {
                "PATH": "/test/bin",
                "VITE_ADK_URL": "http://127.0.0.1:8000",
                "VITE_ADK_APP_NAME": "psyclaw",
                "VITE_ADK_USER_ID": "local-user",
            },
        )
        self.assertNotIn("never-expose-this-key", public_environment.values())
        self.assertFalse(any(key.startswith("PSYCLAW_") for key in public_environment))

    def test_installs_locked_frontend_packages_only_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            frontend_directory = Path(temporary_directory)
            calls: list[tuple[list[str], Path]] = []

            def run_command(command, *, cwd, **_kwargs):
                calls.append((command, cwd))
                return subprocess.CompletedProcess(command, 0)

            with patch.object(launcher, "FRONTEND_DIRECTORY", frontend_directory):
                launcher.install_frontend_if_needed(
                    run_command=run_command,
                    environment=self.environment,
                )
                (frontend_directory / "node_modules").mkdir()
                launcher.install_frontend_if_needed(
                    run_command=run_command,
                    environment=self.environment,
                )

        self.assertEqual(calls, [(["npm", "ci"], frontend_directory)])

    def test_missing_model_fails_before_starting_any_process(self) -> None:
        with self.assertRaisesRegex(launcher.LauncherError, "PSYCLAW_MODEL"):
            launcher.run_local_product(
                environment={"PATH": "/test/bin"},
                popen=lambda *_args, **_kwargs: self.fail("must not start"),
                which=lambda _command: "/test/bin/npm",
            )

    def test_starts_api_optional_stt_and_ui_without_putting_secret_in_ui_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            frontend_directory = Path(temporary_directory)
            (frontend_directory / "node_modules").mkdir()
            started: list[tuple[list[str], dict[str, str], dict[str, object]]] = []
            processes = [FakeProcess(), FakeProcess(), FakeProcess()]

            def popen(command, *, env, **_kwargs):
                started.append((command, env, _kwargs))
                return processes[len(started) - 1]

            def stop_after_start(_seconds: float) -> None:
                raise KeyboardInterrupt

            environment = {
                **self.environment,
                "PSYCLAW_STT_MODEL": "provider/stt-model",
                "PSYCLAW_STT_API_KEY": "another-private-key",
            }
            with patch.object(launcher, "FRONTEND_DIRECTORY", frontend_directory):
                launcher.run_local_product(
                    environment=environment,
                    popen=popen,
                    health_check=lambda _url: True,
                    which=lambda _command: "/test/bin/npm",
                    sleep=stop_after_start,
                )

        self.assertEqual(len(started), 3)
        self.assertEqual(started[0][0], launcher.server_command())
        self.assertEqual(started[1][0], launcher.stt_command())
        self.assertEqual(started[2][0], launcher.frontend_command())
        self.assertTrue(all(call[2].get("start_new_session") for call in started))
        self.assertEqual(started[2][1]["VITE_STT_URL"], "http://127.0.0.1:8001")
        self.assertNotIn("PSYCLAW_API_KEY", started[2][1])
        self.assertNotIn("PSYCLAW_STT_API_KEY", started[2][1])
        self.assertTrue(all(process.terminated for process in processes))
        self.assertFalse(
            any("never-expose-this-key" in part for command, _, _ in started for part in command)
        )

    def test_partial_stt_configuration_fails_before_starting_any_process(self) -> None:
        with self.assertRaisesRegex(launcher.LauncherError, "Set both"):
            launcher.run_local_product(
                environment={**self.environment, "PSYCLAW_STT_MODEL": "provider/model"},
                popen=lambda *_args, **_kwargs: self.fail("must not start"),
                which=lambda _command: "/test/bin/npm",
            )

    def test_health_startup_failure_stops_the_api_without_starting_the_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            frontend_directory = Path(temporary_directory)
            (frontend_directory / "node_modules").mkdir()
            api_process = FakeProcess()
            started: list[list[str]] = []

            def popen(command, **_kwargs):
                started.append(command)
                return api_process

            with patch.object(launcher, "FRONTEND_DIRECTORY", frontend_directory):
                with self.assertRaisesRegex(launcher.LauncherError, "did not become ready"):
                    launcher.run_local_product(
                        environment=self.environment,
                        popen=popen,
                        health_check=lambda _url: False,
                        which=lambda _command: "/test/bin/npm",
                    )

        self.assertEqual(started, [launcher.server_command()])
        self.assertTrue(api_process.terminated)

    def test_posix_cleanup_signals_the_process_group_before_a_kill_fallback(self) -> None:
        process = FakeProcess()
        process.pid = 4321
        signals: list[tuple[int, int]] = []

        launcher.stop_processes(
            [("local server", process)],
            platform="posix",
            kill_process_group=lambda pid, signal_number: signals.append((pid, signal_number)),
        )

        self.assertEqual(signals, [(4321, launcher.signal.SIGTERM)])
        self.assertFalse(process.terminated)

    def test_windows_cleanup_uses_the_new_process_group_signal(self) -> None:
        process = FakeProcess()

        launcher.stop_processes(
            [("local server", process)],
            platform="nt",
        )

        self.assertEqual(
            process.signals,
            [getattr(launcher.signal, "CTRL_BREAK_EVENT", launcher.signal.SIGTERM)],
        )

    def test_child_exit_is_reported_and_all_children_are_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            frontend_directory = Path(temporary_directory)
            (frontend_directory / "node_modules").mkdir()
            api_process = FakeProcess()
            ui_process = FakeProcess(return_code=1)
            processes = iter((api_process, ui_process))

            with patch.object(launcher, "FRONTEND_DIRECTORY", frontend_directory):
                with self.assertRaisesRegex(launcher.LauncherError, "Assistant UI stopped"):
                    launcher.run_local_product(
                        environment=self.environment,
                        popen=lambda *_args, **_kwargs: next(processes),
                        health_check=lambda _url: True,
                        which=lambda _command: "/test/bin/npm",
                    )

        self.assertTrue(api_process.terminated)


if __name__ == "__main__":
    unittest.main()
