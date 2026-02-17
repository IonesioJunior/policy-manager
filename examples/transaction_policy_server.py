#!/usr/bin/env python3
"""
TransactionPolicy Example — confirms transactions with external ledger.

This example demonstrates how to use the built-in TransactionPolicy with
SyftHub data source and model endpoints. The policy confirms transactions
with an external ledger after successful handler execution.

Required environment variables:
    SYFTHUB_URL      — URL of the SyftHub instance
    SYFTHUB_USERNAME — Your SyftHub username
    SYFTHUB_PASSWORD — Your SyftHub password
    SPACE_URL        — The public URL where this space is reachable
    LEDGER_URL       — Ledger API base URL
    LEDGER_API_TOKEN — API token for ledger authentication

Usage:
    export SYFTHUB_URL="http://localhost:8080"
    export SYFTHUB_USERNAME="your-username"
    export SYFTHUB_PASSWORD="your-password"
    export SPACE_URL="http://localhost:8001"
    export LEDGER_URL="https://api.ledger.example.com"
    export LEDGER_API_TOKEN="at_abc12345_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    python examples/transaction_policy_server.py

How it works:
    1. Client reserves funds with the ledger, receives a transaction_token
    2. Client calls this endpoint with the transaction_token in the request
    3. Handler executes and produces a response
    4. TransactionPolicy confirms the transaction with the ledger (post-execution)
    5. If confirmed, response is delivered; otherwise, denied
"""

from __future__ import annotations

import os

from syfthub_api import Document, Message, SyftAPI

from policy_manager.policies import RateLimitPolicy, TransactionPolicy

# ────────────────────────────────────────────────────────────────────
# Policy Configuration
# ────────────────────────────────────────────────────────────────────

# TransactionPolicy confirms with external ledger after handler success.
# Configuration comes from environment variables or can be passed directly.
transaction_policy = TransactionPolicy(
    name="ledger_confirmation",
    # ledger_url and api_token default to LEDGER_URL and LEDGER_API_TOKEN env vars
    # token_field defaults to "transaction_token"
    timeout=30.0,
    price_per_request=0.05,  # $0.05 per request - declared to SyftAPI
)

# Optional: Add rate limiting as a pre-execution policy
rate_limit_policy = RateLimitPolicy(
    name="api_rate_limit",
    max_requests=100,
    window_seconds=60,
)

# ────────────────────────────────────────────────────────────────────
# Application
# ────────────────────────────────────────────────────────────────────

app = SyftAPI()


@app.datasource(
    slug="paid-docs",
    name="Paid Documents",
    description="Data source that confirms payment before delivering documents.",
    policies=[rate_limit_policy, transaction_policy],
)
async def paid_docs(query: str) -> list[Document]:
    """
    Data source endpoint that returns documents after confirming payment.

    The client must include a `transaction_token` in the request input.
    After this handler succeeds, TransactionPolicy will confirm the
    transaction with the external ledger.

    Args:
        query: The search query from the client.

    Returns:
        List of matching documents.
    """
    # Simulate document retrieval
    return [
        Document(
            document_id="doc-001",
            content=f"Premium document result for: {query}",
            similarity_score=0.95,
        ),
        Document(
            document_id="doc-002",
            content=f"Related premium content for: {query}",
            similarity_score=0.87,
        ),
    ]


@app.model(
    slug="paid-model",
    name="Paid Model",
    description="Model that confirms payment before delivering response.",
    policies=[rate_limit_policy, transaction_policy],
)
async def paid_model(messages: list[Message]) -> str:
    """
    Model endpoint that generates a response after confirming payment.

    The client must include a `transaction_token` in the request input.
    After this handler succeeds, TransactionPolicy will confirm the
    transaction with the external ledger.

    Args:
        messages: The conversation history from the client.

    Returns:
        Generated response string.
    """
    # Extract the last user message
    last_user_msg = next(
        (m.content for m in reversed(messages) if m.role == "user"),
        "",
    )

    # Simulate LLM response generation
    return f"Premium AI response to: {last_user_msg}"


@app.on_startup
async def startup_banner():
    """Display information about the running server and its endpoints."""
    ledger_url = os.getenv("LEDGER_URL", "Not configured")
    space_url = os.getenv("SPACE_URL", "Not set")

    print("=" * 70)
    print("TransactionPolicy Example Server")
    print("=" * 70)
    print(f"Ledger URL: {ledger_url}")
    print(f"Space URL:  {space_url}")
    print("\nEndpoints registered:")
    for endpoint in app.endpoints:
        policies = endpoint.get("policies", [])
        policy_names = [p.name for p in policies]
        print(
            f"  [{endpoint['type'].value}] /{endpoint['slug']}  "
            f"policies=[{', '.join(policy_names)}]"
        )
    print("\nTransaction flow:")
    print("  1. Client reserves funds with ledger → receives transaction_token")
    print("  2. Client calls endpoint with transaction_token in request")
    print("  3. Pre-execution policies run (rate limiting)")
    print("  4. Handler executes and produces response")
    print("  5. Post-execution: TransactionPolicy confirms with ledger")
    print("  6. If confirmed → response delivered; else → denied")
    print("=" * 70)


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────


async def main() -> None:
    """Run the application."""
    await app.run(host="0.0.0.0", port=8001)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
