import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.skills import list_skills_in_dir, load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from psyclaw.config import (
    ChatConfiguration,
    ConfigurationError,
    load_chat_configuration,
    load_memory_configuration,
)
from psyclaw.instruction import build_instruction
from psyclaw.note_taker import create_note_taker
from psyclaw.tool_activity import ToolActivityPlugin
from psyclaw.user_tools import (
    list_files,
    read_file,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SKILLS_DIRECTORY = Path(__file__).with_name("skills")


def _create_skill_toolset() -> SkillToolset:
    """Load the local ADK skills catalogue with ADK's native toolset."""
    return SkillToolset(
        skills=[
            load_skill_from_dir(SKILLS_DIRECTORY / name)
            for name in list_skills_in_dir(SKILLS_DIRECTORY)
        ]
    )


def _create_model(configuration: ChatConfiguration) -> LiteLlm:
    """Create LiteLLM with only explicitly configured generic overrides."""
    options: dict[str, str] = {"model": configuration.model}
    if configuration.api_key is not None:
        options["api_key"] = configuration.api_key
    if configuration.api_base is not None:
        options["api_base"] = configuration.api_base
    return LiteLlm(**options)


def _load_configuration(loader) -> ChatConfiguration:
    try:
        return loader(os.environ)
    except ConfigurationError:
        # ``psyclaw-server`` and the normal launcher validate this setting before
        # loading the app. Keeping the ADK object importable also lets discovery
        # and static project checks run without a credentialed local .env file.
        return ChatConfiguration(model="")


def create_chat_model() -> LiteLlm:
    """Create the conversational model from the provider-neutral chat config."""
    return _create_model(_load_configuration(load_chat_configuration))


def create_memory_model() -> LiteLlm:
    """Create an independent memory model with explicit chat-config inheritance."""
    return _create_model(_load_configuration(load_memory_configuration))


def create_root_agent(
    *,
    chat_model: str | BaseLlm | None = None,
    note_taker: Agent | None = None,
) -> Agent:
    """Create fresh ADK instances; an agent cannot be parented more than once."""
    note_taker = note_taker or create_note_taker(create_memory_model())
    return Agent(
        name="psyclaw_agent",
        model=chat_model or create_chat_model(),
        instruction=build_instruction,
        description="Conversational psychologist with read-only local context.",
        tools=[
            list_files,
            read_file,
            _create_skill_toolset(),
        ],
        sub_agents=[note_taker],
    )


root_agent = create_root_agent()


app = App(name="psyclaw", root_agent=root_agent, plugins=[ToolActivityPlugin()])
