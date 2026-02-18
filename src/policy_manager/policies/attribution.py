"""AttributionPolicy — verify that proper attribution exists before access."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

# Callback signature: (user_id, attribution_url) -> bool
VerifyCallback = Callable[[str, str], Awaitable[bool]]


class AttributionPolicy(Policy):
    """Requires the caller to have a verified attribution on record.

    On ``pre_execute`` the policy checks whether the user has a verified
    attribution entry.  Verification can be delegated to an async
    ``verify_callback``, or the policy can check for a stored record.

    Parameters:
        name:              Unique policy name.
        verify_callback:   Async callable ``(user_id, url) -> bool``.
                           If ``None``, the policy checks ``context.input``
                           for an ``"attribution_url"`` key and verifies
                           it against the store.
        url_input_key:     Key in ``context.input`` holding the attribution URL.
    """

    _policy_type = "attribution"
    _policy_description = "Requires verified attribution before access"

    def __init__(
        self,
        *,
        name: str = "attribution",
        verify_callback: VerifyCallback | None = None,
        url_input_key: str = "attribution_url",
    ) -> None:
        self._name = name
        self._verify = verify_callback
        self._url_key = url_input_key

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return f"attribution:{self._name}"

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "url_input_key": self._url_key,
            "has_verify_callback": self._verify is not None,
        }
        return data

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        url = context.input.get(self._url_key, "")

        if self._verify:
            verified = await self._verify(context.user_id, url)
        else:
            verified = await self._check_store(context.user_id, url)

        if not verified:
            return PolicyResult.deny(
                self.name,
                "Attribution not verified. Provide a valid attribution URL.",
            )

        context.metadata[f"{self.name}_verified"] = True
        return PolicyResult.allow(self.name)

    # ── store-based fallback ─────────────────────────────────

    async def _check_store(self, user_id: str, url: str) -> bool:
        if not url:
            return False
        state = await self.store.get(self.namespace, user_id)
        verified_urls: list[str] = state.get("verified_urls", []) if state else []
        return url in verified_urls

    async def add_verified_url(self, user_id: str, url: str) -> None:
        """Register an attribution URL as verified for a user."""
        state = await self.store.get(self.namespace, user_id) or {}
        urls: list[str] = state.get("verified_urls", [])
        if url not in urls:
            urls.append(url)
        await self.store.set(self.namespace, user_id, {"verified_urls": urls})
