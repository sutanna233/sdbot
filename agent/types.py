from dataclasses import dataclass, field
from typing import Any


@dataclass
class Intent:
    name: str
    confidence: float = 1.0
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentContext:
    policy: str
    history: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRequest:
    user_input: str
    source: str = "cli"
    use_context: bool | None = None
