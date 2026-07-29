"""Run Psyclaw's local product with one command."""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv

from psyclaw.config import ConfigurationError, has_complete_stt_configuration, load_chat_configuration

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
HOST, API_PORT, STT_PORT, UI_PORT = "127.0.0.1", 8000, 8001, 5173


class LauncherError(RuntimeError):
    """A short local-startup error."""


def main() -> None:
    load_dotenv(ROOT / ".env")
    try:
        run(os.environ)
    except LauncherError as error:
        print(f"Psyclaw did not start: {error}", file=sys.stderr)
        raise SystemExit(1) from None


def run(environment: Mapping[str, str], *, popen=subprocess.Popen,
        command=subprocess.run, ready: Callable[[], bool] | None = None,
        which=shutil.which, sleep=time.sleep) -> None:
    try:
        load_chat_configuration(environment)
        stt_enabled = has_complete_stt_configuration(environment)
    except ConfigurationError as error:
        raise LauncherError(str(error)) from None
    if which("npm") is None:
        raise LauncherError("Install Node.js (including npm), then run Psyclaw again.")
    install_frontend(command, environment)
    services: list[tuple[str, object]] = []
    try:
        services.append(("local server", start(popen, server_command(), ROOT, environment)))
        if not (ready or wait_for_health)():
            raise LauncherError("The local server did not become ready. Check that port 8000 is free.")
        if stt_enabled:
            services.append(
                ("speech-to-text service", start(popen, stt_command(), ROOT, environment))
            )
        public = frontend_environment(environment, stt_enabled)
        services.append(("Assistant UI", start(popen, ui_command(), FRONTEND, public)))
        print(f"Psyclaw is running at http://{HOST}:{UI_PORT}")
        while True:
            stopped = next((name for name, process in services if process.poll() is not None), None)
            if stopped:
                raise LauncherError(f"The {stopped} stopped. Psyclaw has been closed.")
            sleep(0.2)
    except KeyboardInterrupt:
        pass
    except OSError:
        raise LauncherError("A local Psyclaw process could not start. Check that its port is free.") from None
    finally:
        stop(services, command=command)


def lock_marker() -> Path:
    return FRONTEND / "node_modules" / ".psyclaw-package-lock.sha256"


def lock_digest() -> str:
    return hashlib.sha256((FRONTEND / "package-lock.json").read_bytes()).hexdigest()


def install_frontend(command, environment: Mapping[str, str]) -> None:
    marker = lock_marker()
    if marker.is_file() and marker.read_text().strip() == lock_digest():
        return
    result = command(
        ["npm", "ci"], cwd=FRONTEND,
        env=frontend_environment(environment, False), check=False, text=True,
    )
    if result.returncode != 0:
        raise LauncherError("Frontend setup failed. Run `npm ci` in frontend and try again.")
    marker.write_text(f"{lock_digest()}\n")


def server_command() -> list[str]:
    return [sys.executable, "-m", "psyclaw.server", "--host", HOST, "--port",
            str(API_PORT), "--allow-origin", f"http://{HOST}:{UI_PORT}"]


def stt_command() -> list[str]:
    return [sys.executable, "-m", "uvicorn", "psyclaw.transcription_api:app",
            "--host", HOST, "--port", str(STT_PORT)]


def ui_command() -> list[str]:
    return ["npm", "run", "dev", "--", "--host", HOST, "--port", str(UI_PORT),
            "--strictPort", "--open"]


def frontend_environment(environment: Mapping[str, str], stt_enabled: bool) -> dict[str, str]:
    system_keys = ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "HOME", "USERPROFILE",
                   "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR")
    public = {key: environment[key] for key in system_keys if environment.get(key)}
    public.update({"VITE_ADK_URL": f"http://{HOST}:{API_PORT}",
                   "VITE_ADK_APP_NAME": "psyclaw", "VITE_ADK_USER_ID": "local-user"})
    if stt_enabled:
        public["VITE_STT_URL"] = f"http://{HOST}:{STT_PORT}"
    return public


def start(popen, args: Sequence[str], cwd: Path, env: Mapping[str, str]):
    options = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
               if os.name == "nt" else {"start_new_session": True})
    return popen(list(args), cwd=cwd, env=dict(env), **options)


def wait_for_health() -> bool:
    for _ in range(250):
        try:
            with urlopen(f"http://{HOST}:{API_PORT}/health", timeout=0.2) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except (OSError, URLError):
            time.sleep(0.1)
    return False


def stop(services, *, command=subprocess.run) -> None:
    for _, process in reversed(services):
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
    for _, process in reversed(services):
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                command(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
