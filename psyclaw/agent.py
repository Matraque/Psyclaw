import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm

from psyclaw.instruction import build_instruction
from psyclaw.patient_tools import (
    append_file,
    list_files,
    read_file,
    write_file,
)
from psyclaw.transcript_plugin import TranscriptPlugin, fail_closed_persistence

MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral/mistral-medium-latest")


root_agent = Agent(
    name="psyclaw_agent",
    model=LiteLlm(model=MISTRAL_MODEL),
    instruction=build_instruction,
    description="Mistral AI conversational assistant with local patient-record memory.",
    tools=[
        list_files,
        read_file,
        write_file,
        append_file,
    ],
)


app = App(
    name="psyclaw",
    root_agent=root_agent,
    plugins=[
        TranscriptPlugin(
            conversation_author=root_agent.name,
            persistence_failure_strategy=fail_closed_persistence,
        )
    ],
)
