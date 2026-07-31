from __future__ import annotations

import unittest
from pathlib import Path

from dotenv import dotenv_values

from psyclaw.config import (
    ConfigurationError,
    has_complete_stt_configuration,
    load_chat_configuration,
    load_memory_configuration,
)


class ChatConfigurationTest(unittest.TestCase):
    def test_root_env_example_keeps_speech_to_text_disabled(self) -> None:
        environment = {
            key: value
            for key, value in dotenv_values(
                Path(__file__).resolve().parents[1] / ".env.example"
            ).items()
            if value is not None
        }

        self.assertFalse(has_complete_stt_configuration(environment))

    def test_requires_an_explicit_model_without_choosing_a_provider(self) -> None:
        for environment in ({}, {"PSYCLAW_MODEL": "provider/model-name"}):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    ConfigurationError, "root .env file"
                ):
                    load_chat_configuration(environment)

    def test_loads_generic_optional_overrides(self) -> None:
        configuration = load_chat_configuration(
            {
                "PSYCLAW_MODEL": "local/model",
                "PSYCLAW_API_KEY": "private-test-key",
                "PSYCLAW_API_BASE": "http://127.0.0.1:1234/v1",
            }
        )

        self.assertEqual(configuration.model, "local/model")
        self.assertEqual(configuration.api_key, "private-test-key")
        self.assertEqual(configuration.api_base, "http://127.0.0.1:1234/v1")

    def test_memory_configuration_inherits_chat_settings_and_allows_overrides(self) -> None:
        inherited = load_memory_configuration(
            {
                "PSYCLAW_MODEL": "local/chat",
                "PSYCLAW_API_KEY": "chat-key",
                "PSYCLAW_API_BASE": "http://chat.example/v1",
            }
        )
        self.assertEqual(inherited.model, "local/chat")
        self.assertEqual(inherited.api_key, "chat-key")
        self.assertEqual(inherited.api_base, "http://chat.example/v1")

        dedicated = load_memory_configuration(
            {
                "PSYCLAW_MODEL": "local/chat",
                "PSYCLAW_MEMORY_MODEL": "local/small-memory",
                "PSYCLAW_MEMORY_API_KEY": "memory-key",
                "PSYCLAW_MEMORY_API_BASE": "http://memory.example/v1",
            }
        )
        self.assertEqual(dedicated.model, "local/small-memory")
        self.assertEqual(dedicated.api_key, "memory-key")
        self.assertEqual(dedicated.api_base, "http://memory.example/v1")

    def test_speech_to_text_requires_its_two_explicit_settings(self) -> None:
        self.assertFalse(has_complete_stt_configuration({}))
        for environment in (
            {"PSYCLAW_STT_MODEL": "provider/model"},
            {"PSYCLAW_STT_API_KEY": "private-test-key"},
        ):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(ConfigurationError, "Set both"):
                    has_complete_stt_configuration(environment)
        self.assertTrue(
            has_complete_stt_configuration(
                {
                    "PSYCLAW_STT_MODEL": "provider/model",
                    "PSYCLAW_STT_API_KEY": "private-test-key",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
