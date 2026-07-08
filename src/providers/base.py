from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MessageResult:
    content: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None


class LLMProvider(ABC):

    @abstractmethod
    async def create_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> MessageResult:
        ...

    async def create_message_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[dict[str, Any], None]:
        result = await self.create_message(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            system=system,
            max_tokens=max_tokens,
        )
        yield {"type": "done", "content": result.content, "stop_reason": result.stop_reason}

    @abstractmethod
    async def cleanup(self):
        ...
