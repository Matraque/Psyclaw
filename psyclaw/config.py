"""Private, provider-neutral configuration for Psyclaw's chat model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatConfiguration:
    """The explicit settings Psyclaw passes to LiteLLM for chat."""

    model: str
    api_key: str | None = field(default=None, repr=False)
    api_base: str | None = field(default=None, repr=False)


class ConfigurationError(ValueError):
    """Raised when a required local setting is absent or malformed."""


def load_chat_configuration(environment: Mapping[str, str]) -> ChatConfiguration:
    """Load generic chat settings without selecting a provider or a model."""
    model = _optional_value(environment, "PSYCLAW_MODEL")
    if model is None or model == "provider/model-name":
        raise ConfigurationError("Set PSYCLAW_MODEL in the root .env file, then run Psyclaw again.")
    return ChatConfiguration(
        model=model,
        api_key=_optional_value(environment, "PSYCLAW_API_KEY"),
        api_base=_optional_value(environment, "PSYCLAW_API_BASE"),
    )


def load_memory_configuration(environment: Mapping[str, str]) -> ChatConfiguration:
    """Load memory settings, inheriting every unspecified chat setting.

    A dedicated memory model is optional.  Provider-neutral API overrides use
    the same inheritance rule, so a memory-only setup never needs to duplicate
    the chat credentials.
    """
    chat = load_chat_configuration(environment)
    return ChatConfiguration(
        model=_optional_value(environment, "PSYCLAW_MEMORY_MODEL") or chat.model,
        api_key=_optional_value(environment, "PSYCLAW_MEMORY_API_KEY") or chat.api_key,
        api_base=_optional_value(environment, "PSYCLAW_MEMORY_API_BASE") or chat.api_base,
    )


def has_complete_stt_configuration(environment: Mapping[str, str]) -> bool:
    """Validate the optional STT pair and return whether it is enabled."""
    model = _optional_value(environment, "PSYCLAW_STT_MODEL")
    api_key = _optional_value(environment, "PSYCLAW_STT_API_KEY")
    if (model is None) != (api_key is None):
        raise ConfigurationError(
            "Set both PSYCLAW_STT_MODEL and PSYCLAW_STT_API_KEY, or remove both."
        )
    return model is not None


def _optional_value(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
