"""PromptFilterPolicy — regex / callable content filtering on input and output."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext


class PromptFilterPolicy(Policy):
    """Blocks requests whose input or output matches forbidden patterns.

    This is a **stateless** policy — it does not use the store.

    Parameters:
        name:           Unique policy name.
        patterns:       Regex patterns to match against (any match → deny).
        filter_fn:      Optional callable ``(text) -> bool`` where ``True``
                        means the text is **blocked**.
        input_path:     Key in ``context.input`` to check (pre phase).
        output_path:    Key in ``context.output`` to check (post phase).
        check_input:    Whether to check input during ``pre_execute``.
        check_output:   Whether to check output during ``post_execute``.
    """

    def __init__(
        self,
        *,
        name: str = "prompt_filter",
        patterns: list[str] | None = None,
        filter_fn: Callable[[str], bool] | None = None,
        input_path: str = "query",
        output_path: str = "response",
        check_input: bool = True,
        check_output: bool = True,
    ) -> None:
        self._name = name
        self._compiled = [re.compile(p, re.IGNORECASE) for p in (patterns or [])]
        self._filter_fn = filter_fn
        self.input_path = input_path
        self.output_path = output_path
        self.check_input = check_input
        self.check_output = check_output

    @property
    def name(self) -> str:
        return self._name

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "patterns": [p.pattern for p in self._compiled],
            "has_filter_fn": self._filter_fn is not None,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "check_input": self.check_input,
            "check_output": self.check_output,
        }
        return data

    def _is_blocked(self, text: str) -> bool:
        for pat in self._compiled:
            if pat.search(text):
                return True
        return bool(self._filter_fn and self._filter_fn(text))

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        if not self.check_input:
            return PolicyResult.allow(self.name)

        text = context.input.get(self.input_path, "")
        if not isinstance(text, str):
            text = str(text)

        if self._is_blocked(text):
            return PolicyResult.deny(self.name, "Input blocked by content filter")

        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        if not self.check_output:
            return PolicyResult.allow(self.name)

        text = context.output.get(self.output_path, "")
        if not isinstance(text, str):
            text = str(text)

        if self._is_blocked(text):
            return PolicyResult.deny(self.name, "Output blocked by content filter")

        return PolicyResult.allow(self.name)
