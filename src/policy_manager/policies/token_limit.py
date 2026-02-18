"""TokenLimitPolicy — enforce input/output token (or character) budgets."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

_NS_PREFIX = "token_limit"


class TokenLimitPolicy(Policy):
    """Enforces maximum token/character counts on input and/or output.

    Which field to read is configurable via ``input_path`` / ``output_path``
    (dot-notation keys into ``context.input`` / ``context.output``).

    If a custom ``token_counter`` callable is provided it will be called
    with the raw text and must return an ``int`` count.  Otherwise the
    policy falls back to ``len(text)`` (character count).

    Parameters:
        name:              Unique policy name.
        max_input_tokens:  Limit for the input side (``None`` = unchecked).
        max_output_tokens: Limit for the output side (``None`` = unchecked).
        input_path:        Key in ``context.input`` holding the input text.
        output_path:       Key in ``context.output`` holding the output text.
        token_counter:     Optional callable ``(str) -> int`` to count tokens.
    """

    _policy_type = "token_limit"
    _policy_description = "Enforces maximum token/character counts on input and output"

    def __init__(
        self,
        *,
        name: str = "token_limit",
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        input_path: str = "query",
        output_path: str = "response",
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self._name = name
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.input_path = input_path
        self.output_path = output_path
        self._counter = token_counter or len

    @property
    def name(self) -> str:
        return self._name

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "has_custom_counter": self._counter is not len,
        }
        return data

    def _count(self, text: str) -> int:
        return self._counter(text)

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        if self.max_input_tokens is None:
            return PolicyResult.allow(self.name)

        text = context.input.get(self.input_path, "")
        if not isinstance(text, str):
            text = str(text)

        count = self._count(text)
        if count > self.max_input_tokens:
            return PolicyResult.deny(
                self.name,
                f"Input tokens ({count}) exceed limit ({self.max_input_tokens})",
                token_count=count,
                limit=self.max_input_tokens,
            )

        context.metadata[f"{self.name}_input_tokens"] = count
        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        if self.max_output_tokens is None:
            return PolicyResult.allow(self.name)

        text = context.output.get(self.output_path, "")
        if not isinstance(text, str):
            text = str(text)

        count = self._count(text)
        if count > self.max_output_tokens:
            return PolicyResult.deny(
                self.name,
                f"Output tokens ({count}) exceed limit ({self.max_output_tokens})",
                token_count=count,
                limit=self.max_output_tokens,
            )

        context.metadata[f"{self.name}_output_tokens"] = count
        return PolicyResult.allow(self.name)
