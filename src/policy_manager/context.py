"""RequestContext — the mutable data object that flows through the policy chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class RequestContext:
    """User-created, user-owned context that travels through every policy.

    Attributes:
        user_id:  Identifier for whoever is making the request (user, service,
                  API key — anything that identifies the caller).
        input:    Arbitrary input payload.  Policies read from here.
        output:   Arbitrary output payload.  The caller sets this after their
                  function runs, before calling ``check_post_exec_policies``.
        metadata: Shared scratchpad for inter-policy communication.
                  Policies may read **and write** to this dict so that
                  upstream policies can pass data to downstream ones.
        timestamp: When the request was created.  Auto-set to *now* (UTC) if
                   not provided.
    """

    user_id: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
