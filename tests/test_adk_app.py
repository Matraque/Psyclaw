from pathlib import Path
import unittest

from google.adk.apps import App
from google.adk.cli.utils.agent_loader import AgentLoader

import psyclaw
from psyclaw.agent import app, root_agent
from psyclaw.tool_activity import ToolActivityPlugin


class AdkAppTest(unittest.TestCase):
    def test_app_uses_the_existing_root_agent(self) -> None:
        self.assertIsInstance(app, App)
        self.assertEqual(app.name, "psyclaw")
        self.assertIs(app.root_agent, root_agent)
        self.assertEqual(len(app.plugins), 1)
        self.assertIsInstance(app.plugins[0], ToolActivityPlugin)

    def test_package_and_adk_loader_discover_the_app(self) -> None:
        self.assertIs(psyclaw.app, app)
        loader = AgentLoader(str(Path(__file__).resolve().parents[1]))

        self.assertIs(loader.load_agent("psyclaw"), app)


if __name__ == "__main__":
    unittest.main()
