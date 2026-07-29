import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm

from psyclaw.config import ChatConfiguration, ConfigurationError, load_chat_configuration
from psyclaw.instruction import build_instruction
from psyclaw.user_tools import (
    append_file,
    list_files,
    read_file,
    write_file,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _create_chat_model() -> LiteLlm:
    """Create LiteLLM with only explicitly configured generic overrides."""
    try:
        configuration = load_chat_configuration(os.environ)
    except ConfigurationError:
        # ``psyclaw-server`` and the normal launcher validate this setting before
        # loading the app. Keeping the ADK object importable also lets discovery
        # and static project checks run without a credentialed local .env file.
        configuration = ChatConfiguration(model="")

    options: dict[str, str] = {"model": configuration.model}
    if configuration.api_key is not None:
        options["api_key"] = configuration.api_key
    if configuration.api_base is not None:
        options["api_base"] = configuration.api_base
    return LiteLlm(**options)


root_agent = Agent(
    name="psyclaw_agent",
    model=_create_chat_model(),
    instruction=build_instruction,
    description="Conversational assistant with local user memory.",
    tools=[
        list_files,
        read_file,
        write_file,
        append_file,
    ],
)


app = App(name="psyclaw", root_agent=root_agent)
