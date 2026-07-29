from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.cli.fast_api import get_fast_api_app

from psyclaw import server
from psyclaw.session_service import get_session_service_uri


class PsyclawServerTest(unittest.TestCase):
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

    def test_api_only_app_has_required_routes_without_development_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            with patch.dict("os.environ", {"PSYCLAW_PATIENT_DIR": str(patient_directory)}):
                app = get_fast_api_app(
                    agents_dir=str(server.PROJECT_DIRECTORY),
                    session_service_uri=get_session_service_uri(),
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


if __name__ == "__main__":
    unittest.main()
