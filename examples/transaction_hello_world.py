#!/usr/bin/env python3
"""
TransactionPolicy — Hello World

Demonstrates the TransactionPolicy which confirms transactions with an
external ledger after successful handler execution.

This example uses a mock HTTP server to simulate the ledger API.

Usage:
    python examples/transaction_hello_world.py
"""

from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from policy_manager import PolicyManager, RequestContext
from policy_manager.policies import RateLimitPolicy, TransactionPolicy

# ────────────────────────────────────────────────────────────────────
# Mock Ledger Server (simulates external ledger API)
# ────────────────────────────────────────────────────────────────────

VALID_TOKENS = {"tok_alice_001", "tok_alice_002", "tok_bob_001"}


class MockLedgerHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler that simulates ledger confirmation endpoint."""

    def do_POST(self):
        if self.path == "/v1/transfers/confirm":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()

            # Check for valid token in request
            if any(tok in body for tok in VALID_TOKENS):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "confirmed"}')
            else:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "invalid_token"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress logging


def start_mock_server(port: int) -> HTTPServer:
    """Start the mock ledger server in a background thread."""
    server = HTTPServer(("127.0.0.1", port), MockLedgerHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ────────────────────────────────────────────────────────────────────
# Application Functions
# ────────────────────────────────────────────────────────────────────


def generate_response(query: str) -> dict:
    """Simulate an LLM or document retrieval response."""
    return {
        "response": f"Premium result for: {query}",
        "sources": ["doc_001", "doc_002"],
    }


def handle_denial(result) -> None:
    """Handle denied requests."""
    print(f"  [DENIED] policy={result.policy_name}  reason={result.reason}")


async def process_request(pm: PolicyManager, ctx: RequestContext) -> None:
    """Run a request through the full policy chain."""
    # Pre-execution policies (rate limiting, etc.)
    pre_result = await pm.check_pre_exec_policies(ctx)

    if not pre_result.allowed:
        handle_denial(pre_result)
        return

    # Handler executes
    response = generate_response(ctx.input["query"])
    ctx.output = response

    # Post-execution policies (transaction confirmation)
    post_result = await pm.check_post_exec_policies(ctx)

    if post_result.allowed:
        print(f"  [SUCCESS] Response delivered: {response['response']}")
        if ctx.metadata.get("transaction_confirmed"):
            print("  [LEDGER] Transaction confirmed with ledger")
    else:
        handle_denial(post_result)


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────


async def main():
    # Start mock ledger server
    mock_port = 19876
    server = start_mock_server(mock_port)
    print(f"Mock ledger server running on http://127.0.0.1:{mock_port}\n")

    # ──────────────────────────────────────
    #  1. Create the manager and policies
    # ──────────────────────────────────────
    pm = PolicyManager()

    await pm.add_policy(
        RateLimitPolicy(
            name="rate_limit",
            max_requests=5,
            window_seconds=60,
        )
    )

    await pm.add_policy(
        TransactionPolicy(
            name="transaction",
            ledger_url=f"http://127.0.0.1:{mock_port}",
            api_token="test_api_token",
            # token_field defaults to "transaction_token"
            price_per_request=0.05,  # $0.05 per request
        )
    )

    # ──────────────────────────────────────
    #  2. Successful transaction
    # ──────────────────────────────────────
    print("=== Successful Transaction ===\n")

    ctx1 = RequestContext(
        user_id="alice@acme.com",
        input={
            "query": "premium content",
            "transaction_token": "tok_alice_001",  # Valid token
        },
    )

    await process_request(pm, ctx1)

    # ──────────────────────────────────────
    #  3. Missing transaction token
    # ──────────────────────────────────────
    print("\n=== Missing Transaction Token ===\n")

    ctx2 = RequestContext(
        user_id="bob@acme.com",
        input={
            "query": "some query",
            # No transaction_token!
        },
    )

    await process_request(pm, ctx2)

    # ──────────────────────────────────────
    #  4. Invalid transaction token
    # ──────────────────────────────────────
    print("\n=== Invalid Transaction Token ===\n")

    ctx3 = RequestContext(
        user_id="eve@external.com",
        input={
            "query": "trying to access",
            "transaction_token": "tok_invalid_xxx",  # Invalid token
        },
    )

    await process_request(pm, ctx3)

    # ──────────────────────────────────────
    #  5. Multiple successful transactions
    # ──────────────────────────────────────
    print("\n=== Multiple Transactions ===\n")

    tokens = ["tok_alice_002", "tok_bob_001"]
    for i, token in enumerate(tokens):
        ctx = RequestContext(
            user_id="alice@acme.com",
            input={
                "query": f"request #{i + 1}",
                "transaction_token": token,
            },
        )
        await process_request(pm, ctx)

    # ──────────────────────────────────────
    #  6. Export policy configuration
    # ──────────────────────────────────────
    print("\n=== Policy Configuration ===\n")

    export = pm.export()
    for policy in export["policies"]:
        print(f"  {policy['name']} ({policy['type']})")
        print(f"    phase: {policy['phase']}")
        print(f"    config: {policy['config']}")

    # Cleanup
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
