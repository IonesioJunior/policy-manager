"""
policy_manager — Hello World

Everything is a policy. Policies chain in registration order,
mutating the context as they go. First denial stops the chain.
"""

import asyncio

from policy_manager import PolicyManager, RequestContext
from policy_manager.policies import (
    AccessGroupPolicy,
    CustomPolicy,
    RateLimitPolicy,
    TokenLimitPolicy,
)

# ─── Your functions (anything — completely decoupled from the framework) ───


def search_documents(query: str, doc_ids: list[str]) -> dict:
    return {
        "response": f"Found results for '{query}' across {len(doc_ids)} docs.",
        "sources": doc_ids,
    }


def notify_admin(result) -> None:
    print(f"  [DENIED] policy={result.policy_name}  reason={result.reason}")


async def main():
    # ──────────────────────────────────────
    #  1. Create the manager
    # ──────────────────────────────────────
    pm = PolicyManager()

    # ──────────────────────────────────────
    #  2. Register policies (order = chain order)
    # ──────────────────────────────────────
    await pm.add_policy(
        AccessGroupPolicy(
            name="engineering_docs",
            users=["alice@acme.com", "bob@acme.com"],
            documents=["doc_arch", "doc_api", "doc_runbook"],
        )
    )

    await pm.add_policy(
        RateLimitPolicy(
            name="standard_rate_limit",
            max_requests=3,
            window_seconds=60,
        )
    )

    await pm.add_policy(
        TokenLimitPolicy(
            name="output_cap",
            max_output_tokens=200,
        )
    )

    await pm.add_policy(
        CustomPolicy(
            name="non_empty_query",
            phase="pre",
            check=lambda ctx: len(ctx.input.get("query", "")) > 0,
            deny_reason="Query must not be empty",
        )
    )

    # ──────────────────────────────────────
    #  3. Allowed request
    # ──────────────────────────────────────
    print("=== Allowed request ===\n")

    ctx = RequestContext(
        user_id="alice@acme.com",
        input={"query": "system architecture overview"},
    )

    pre_result = await pm.check_pre_exec_policies(ctx)

    if pre_result.allowed:
        resolved_docs = ctx.metadata.get("resolved_documents", [])
        response = search_documents(ctx.input["query"], resolved_docs)

        ctx.output = response

        post_result = await pm.check_post_exec_policies(ctx)

        if post_result.allowed:
            print(f"  Response: {response}")
        else:
            notify_admin(post_result)
    else:
        notify_admin(pre_result)

    # ──────────────────────────────────────
    #  4. Unknown user — denied at first
    #     policy in the chain
    # ──────────────────────────────────────
    print("\n=== Unknown user ===\n")

    ctx2 = RequestContext(
        user_id="eve@external.com",
        input={"query": "give me secrets"},
    )

    pre_result = await pm.check_pre_exec_policies(ctx2)

    if pre_result.allowed:
        print("  Should not reach here")
    else:
        notify_admin(pre_result)

    # ──────────────────────────────────────
    #  5. Rate limit exhaustion
    # ──────────────────────────────────────
    print("\n=== Rate limit exhaustion ===\n")

    for i in range(4):
        ctx_rl = RequestContext(
            user_id="bob@acme.com",
            input={"query": f"request #{i + 1}"},
        )
        pre_result = await pm.check_pre_exec_policies(ctx_rl)

        if pre_result.allowed:
            print(f"  Request #{i + 1}: allowed")
        else:
            print(f"  Request #{i + 1}: denied — {pre_result.reason}")

    # ──────────────────────────────────────
    #  6. Post-execution denial (output too long)
    # ──────────────────────────────────────
    print("\n=== Output too long ===\n")

    ctx3 = RequestContext(
        user_id="alice@acme.com",
        input={"query": "everything about the API"},
    )

    pre_result = await pm.check_pre_exec_policies(ctx3)

    if pre_result.allowed:
        ctx3.output = {"response": "x" * 300}  # exceeds 200-char limit

        post_result = await pm.check_post_exec_policies(ctx3)

        if post_result.allowed:
            print("  Delivered")
        else:
            notify_admin(post_result)

    # ──────────────────────────────────────
    #  7. Runtime membership management
    # ──────────────────────────────────────
    print("\n=== Runtime management ===\n")

    eng = pm.get_policy("engineering_docs")
    await eng.add_users(["charlie@acme.com"])

    ctx4 = RequestContext(
        user_id="charlie@acme.com",
        input={"query": "onboarding"},
    )

    pre_result = await pm.check_pre_exec_policies(ctx4)
    print(f"  Charlie allowed: {pre_result.allowed}")
    print(f"  Charlie docs:    {ctx4.metadata.get('resolved_documents')}")

    print("Policy JSON: ", pm.export())


if __name__ == "__main__":
    asyncio.run(main())
