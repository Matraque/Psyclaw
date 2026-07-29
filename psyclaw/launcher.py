"""Run Psyclaw's local product with one command."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv

from psyclaw.config import ConfigurationError, has_complete_stt_configuration, load_chat_configuration


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
FRONTEND_DIRECTORY = PROJECT_DIRECTORY / "frontend"
API_HOST = "127.0.0.1"
API_PORT = 8000
STT_PORT = 8001
UI_PORT = 5173
LOCAL_USER_ID = "local-user"
HEALTH_URL = f"http://{API_HOST}:{API_PORT}/health"


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def send_signal(self, signal_number: int) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class LauncherError(RuntimeError):
    """A short, user-actionable local startup error."""


Popen = Callable[..., Process]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
HealthCheck = Callable[[str], bool]


def main() -> None:
    """Start the local API, optional STT service, and Assistant UI."""
    load_dotenv(PACKAGE_DIRECTORY / ".env")
    try:
        run_local_product(environment=os.environ)
    except LauncherError as error:
        print(f"Psyclaw did not start: {error}", file=sys.stderr)
        raise SystemExit(1) from None


def run_local_product(
    *,
    environment: Mapping[str, str],
    popen: Popen = subprocess.Popen,
    run_command: CommandRunner = subprocess.run,
    health_check: HealthCheck | None = None,
    which: Callable[[str], str | None] = shutil.which,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run the complete local product and always stop its child processes."""
    try:
        load_chat_configuration(environment)
        stt_enabled = has_complete_stt_configuration(environment)
    except ConfigurationError as error:
        raise LauncherError(str(error)) from None

    if which("npm") is None:
        raise LauncherError("Install Node.js (including npm), then run Psyclaw again.")
    if health_check is None:
        health_check = wait_for_health

    processes: list[tuple[str, Process]] = []
    try:
        install_frontend_if_needed(run_command=run_command, environment=environment)
        api_process = start_process(
            popen,
            server_command(),
            cwd=PROJECT_DIRECTORY,
            env=dict(environment),
        )
        processes.append(("local server", api_process))
        if not health_check(HEALTH_URL):
            raise LauncherError("The local server did not become ready. Check that port 8000 is free.")
        ensure_running(processes)

        public_environment = frontend_environment(environment)
        if stt_enabled:
            stt_process = start_process(
                popen,
                stt_command(),
                cwd=PROJECT_DIRECTORY,
                env=dict(environment),
            )
            processes.append(("speech-to-text service", stt_process))
            ensure_running(processes)
            public_environment["VITE_STT_URL"] = f"http://{API_HOST}:{STT_PORT}"

        ui_process = start_process(
            popen,
            frontend_command(),
            cwd=FRONTEND_DIRECTORY,
            env=public_environment,
        )
        processes.append(("Assistant UI", ui_process))
        print(f"Psyclaw is running at http://{API_HOST}:{UI_PORT}")
        monitor_processes(processes, sleep=sleep)
    except KeyboardInterrupt:
        return
    except OSError:
        raise LauncherError("A local Psyclaw process could not start. Check that its port is free.") from None
    finally:
        stop_processes(processes)


def install_frontend_if_needed(
    *,
    run_command: CommandRunner,
    environment: Mapping[str, str],
) -> None:
    """Install locked frontend packages only for the first local run."""
    if (FRONTEND_DIRECTORY / "node_modules").is_dir():
        return
    result = run_command(
        ["npm", "ci"],
        cwd=FRONTEND_DIRECTORY,
        env=frontend_environment(environment),
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise LauncherError("Frontend setup failed. Run `npm ci` in frontend and try again.")


def server_command() -> list[str]:
    """Return the API-only command used by the normal product UI."""
    return [
        sys.executable,
        "-m",
        "psyclaw.server",
        "--host",
        API_HOST,
        "--port",
        str(API_PORT),
        "--allow-origin",
        f"http://{API_HOST}:{UI_PORT}",
    ]


def stt_command() -> list[str]:
    """Return the optional loopback-only speech-to-text command."""
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "psyclaw.transcription_api:app",
        "--host",
        API_HOST,
        "--port",
        str(STT_PORT),
    ]


def frontend_command() -> list[str]:
    """Return Vite's deterministic local Assistant UI command."""
    return [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        API_HOST,
        "--port",
        str(UI_PORT),
        "--strictPort",
        "--open",
    ]


def frontend_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Pass the browser only non-secret local connection settings."""
    result: dict[str, str] = {}
    for key in (
        "PATH",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    ):
        value = environment.get(key)
        if value:
            result[key] = value
    result.update(
        {
            "VITE_ADK_URL": f"http://{API_HOST}:{API_PORT}",
            "VITE_ADK_APP_NAME": "psyclaw",
            "VITE_ADK_USER_ID": LOCAL_USER_ID,
        }
    )
    return result


def process_group_options(platform: str | None = None) -> dict[str, int | bool]:
    """Return standard-library options that isolate a child process group."""
    if (platform or os.name) == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def start_process(
    popen: Popen,
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> Process:
    """Start one product service in its own group so descendants stop together."""
    return popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        **process_group_options(),
    )


def wait_for_health(url: str, *, attempts: int = 250, interval_seconds: float = 0.1) -> bool:
    """Poll the API readiness endpoint without leaking configuration values."""
    for _ in range(attempts):
        try:
            with urlopen(url, timeout=0.2) as response:  # noqa: S310 - loopback constant
                if response.status == 200:
                    return True
        except (OSError, URLError):
            time.sleep(interval_seconds)
    return False


def ensure_running(processes: Sequence[tuple[str, Process]]) -> None:
    """Fail early if a just-started child stops during startup."""
    for name, process in processes:
        if process.poll() is not None:
            raise LauncherError(f"The {name} stopped during startup. Check that its port is free.")


def monitor_processes(
    processes: Sequence[tuple[str, Process]],
    *,
    sleep: Callable[[float], None],
) -> None:
    """Keep the launcher alive until Ctrl-C or one child process stops."""
    while True:
        for name, process in processes:
            if process.poll() is not None:
                raise LauncherError(f"The {name} stopped. Psyclaw has been closed.")
        sleep(0.2)


def stop_processes(
    processes: Sequence[tuple[str, Process]],
    *,
    platform: str | None = None,
    kill_process_group: Callable[[int, int], None] = os.killpg,
) -> None:
    """Terminate all children, including siblings after one fails."""
    is_windows = (platform or os.name) == "nt"
    for _, process in reversed(processes):
        if process.poll() is None:
            try:
                if is_windows:
                    process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
                else:
                    kill_process_group(process.pid, signal.SIGTERM)
            except OSError:
                try:
                    process.terminate()
                except OSError:
                    pass
    for _, process in reversed(processes):
        try:
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    if is_windows:
                        process.kill()
                    else:
                        kill_process_group(process.pid, signal.SIGKILL)
                except OSError:
                    pass
