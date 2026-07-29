from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.adk.cli.fast_api import get_fast_api_app

from psyclaw import server
from psyclaw.session_service import get_session_service_uri


class PsyclawServerTest(unittest.TestCase):
    allowed_origin = "http://127.0.0.1:5173"

    def test_cli_forwards_explicit_options_to_the_api_only_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            with (
                patch.dict("os.environ", {"PSYCLAW_PATIENT_DIR": str(patient_directory)}),
                patch("psyclaw.server.adk_main") as adk_main,
            ):
                server.main(
                    [
                        "--host",
                        "127.0.0.2",
                        "--port",
                        "8123",
                        "--allow-origin",
                        "http://127.0.0.1:5173",
                    ]
                )

        arguments = adk_main.call_args.kwargs["args"]
        self.assertEqual(arguments[0], "api_server")
        self.assertEqual(arguments[arguments.index("--host") + 1], "127.0.0.2")
        self.assertEqual(arguments[arguments.index("--port") + 1], "8123")
        self.assertIn("--allow_origins", arguments)
        self.assertNotIn("--with_ui", arguments)
        self.assertEqual(adk_main.call_args.kwargs["prog_name"], "psyclaw-server")

    def test_defaults_to_loopback_and_never_enables_the_development_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            with patch.dict("os.environ", {"PSYCLAW_PATIENT_DIR": str(patient_directory)}):
                arguments = server.build_server_arguments()

            self.assertEqual(arguments[0], "api_server")
            self.assertEqual(arguments[arguments.index("--host") + 1], "127.0.0.1")
            self.assertIn("--no-reload", arguments)
            self.assertNotIn("web", arguments)
            self.assertNotIn("--with_ui", arguments)
            self.assertNotIn("--a2a", arguments)
            self.assertNotIn("--trigger_sources", arguments)

    def test_rejects_non_loopback_hosts_without_resolving_names(self) -> None:
        for host in ("0.0.0.0", "192.168.1.10", "example.com"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "must be localhost"):
                    server.build_server_arguments(host=host)

    def test_accepts_only_explicit_loopback_hosts(self) -> None:
        for host in ("localhost", "127.0.0.1", "127.12.34.56", "::1"):
            with self.subTest(host=host):
                arguments = server.build_server_arguments(host=host)
                self.assertEqual(arguments[arguments.index("--host") + 1], host)

    def test_api_only_app_has_required_routes_without_development_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            with patch.dict("os.environ", {"PSYCLAW_PATIENT_DIR": str(patient_directory)}):
                app = get_fast_api_app(
                    agents_dir=str(server.AGENT_DIRECTORY),
                    session_service_uri=get_session_service_uri(),
                    allow_origins=[self.allowed_origin],
                    web=False,
                )

        routes = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/health",
                "/list-apps",
                "/run_sse",
                "/apps/{app_name}/users/{user_id}/sessions",
            }.issubset(routes)
        )
        self.assertNotIn("/", routes)
        self.assertNotIn("/dev-ui", routes)
        self.assertFalse(any(route.startswith("/dev-ui/") for route in routes))
        self.assertFalse(any(route.startswith("/builder/") for route in routes))

        with TestClient(app) as client:
            response = client.get("/list-apps")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["psyclaw"])

    def test_cors_allows_only_the_explicit_vite_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            with patch.dict("os.environ", {"PSYCLAW_PATIENT_DIR": str(patient_directory)}):
                app = get_fast_api_app(
                    agents_dir=str(server.AGENT_DIRECTORY),
                    session_service_uri=get_session_service_uri(),
                    allow_origins=[self.allowed_origin],
                    web=False,
                )

        with TestClient(app) as client:
            allowed = client.options(
                "/health",
                headers={
                    "Origin": self.allowed_origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            hostile = client.options(
                "/health",
                headers={
                    "Origin": "https://hostile.example",
                    "Access-Control-Request-Method": "GET",
                },
            )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), self.allowed_origin)
        self.assertNotIn("access-control-allow-origin", hostile.headers)

if __name__ == "__main__":
    unittest.main()
