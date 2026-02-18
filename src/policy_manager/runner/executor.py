# Copyright (c) 2024 OpenMined
# SPDX-License-Identifier: Apache-2.0
"""Executor for running handlers with policy enforcement.

Orchestrates the full execution flow:
1. Create store from configuration
2. Build PolicyManager with policies
3. Run pre-execution policies
4. Execute user handler
5. Run post-execution policies
6. Return structured result
"""

from __future__ import annotations

import asyncio
from typing import Any

from policy_manager import PolicyManager, RequestContext
from policy_manager.result import PolicyResult
from policy_manager.stores import InMemoryStore, SQLiteStore, Store

from .factory import PolicyFactory, PolicyFactoryError
from .handler import HandlerLoadError, load_handler
from .schema import (
    PolicyResultSchema,
    RunnerInput,
    RunnerOutput,
    StoreConfigSchema,
)


class ExecutionError(Exception):
    """Raised when execution fails."""

    pass


class Executor:
    """Executes handler with policy enforcement.

    Responsibilities:
    - Create store from configuration
    - Build PolicyManager with policies
    - Run pre-exec -> handler -> post-exec chain
    - Translate results to output schema

    The executor is designed for dependency injection to support testing.
    Pass a custom store to the constructor to override store creation.

    Example:
        executor = Executor()
        output = await executor.execute(input_data)

        # For testing with mock store:
        mock_store = InMemoryStore()
        executor = Executor(store=mock_store)
    """

    def __init__(self, store: Store | None = None) -> None:
        """Initialize executor with optional injected store.

        Args:
            store: Optional store to use instead of creating from config.
                   Useful for testing.
        """
        self._injected_store = store

    async def execute(self, input_data: RunnerInput) -> RunnerOutput:
        """Execute the full policy-aware handler flow.

        Args:
            input_data: Complete input from Go SDK

        Returns:
            RunnerOutput with success/failure and result/error details

        Note:
            This method catches all exceptions and returns them as
            RunnerOutput errors, ensuring valid JSON is always returned.
        """
        try:
            return await self._execute_internal(input_data)
        except PolicyFactoryError as e:
            return RunnerOutput(
                success=False,
                error=str(e),
                error_type="PolicyFactoryError",
            )
        except HandlerLoadError as e:
            return RunnerOutput(
                success=False,
                error=str(e),
                error_type="HandlerLoadError",
            )
        except ExecutionError as e:
            return RunnerOutput(
                success=False,
                error=str(e),
                error_type="ExecutionError",
            )
        except Exception as e:
            return RunnerOutput(
                success=False,
                error=str(e),
                error_type=type(e).__name__,
            )

    async def _execute_internal(self, input_data: RunnerInput) -> RunnerOutput:
        """Internal execution logic.

        Separated from execute() to allow exception propagation
        for testing while execute() catches all errors.
        """
        # 1. Create store (use injected or create from config)
        store = self._injected_store or self._create_store(input_data.store)
        owns_store = self._injected_store is None  # We need to close it if we created it

        try:
            # 2. Build PolicyManager with policies
            pm = PolicyManager(store=store)
            factory = PolicyFactory()
            policies = factory.create_all(input_data.policies)

            for policy in policies:
                await pm.add_policy(policy)

            # 3. Build RequestContext
            ctx = RequestContext(
                user_id=input_data.context.user_id,
                input=self._build_input_dict(input_data),
                metadata=input_data.context.metadata.copy(),
            )

            # 4. Pre-execution policies
            pre_result = await pm.check_pre_exec_policies(ctx)
            if not pre_result.allowed:
                return self._policy_denied_output(pre_result)

            # 5. Load and execute handler
            handler_result = await self._run_handler(input_data, ctx)
            ctx.output = (
                handler_result if isinstance(handler_result, dict) else {"result": handler_result}
            )

            # 6. Post-execution policies
            post_result = await pm.check_post_exec_policies(ctx)
            if not post_result.allowed:
                return self._policy_denied_output(post_result)

            # 7. Success
            return RunnerOutput(
                success=True,
                result=handler_result,
                policy_result=PolicyResultSchema(allowed=True),
            )
        finally:
            # Always close the store if we created it
            if owns_store and hasattr(store, "close"):
                await store.close()

    def _create_store(self, config: StoreConfigSchema) -> Store:
        """Create store from configuration.

        Args:
            config: Store configuration

        Returns:
            Store instance
        """
        if config.type == "sqlite":
            if not config.path:
                raise ExecutionError("SQLite store requires 'path' configuration")
            return SQLiteStore(config.path)
        return InMemoryStore()

    def _build_input_dict(self, input_data: RunnerInput) -> dict[str, Any]:
        """Build input dict for RequestContext.

        Args:
            input_data: Runner input

        Returns:
            Dict suitable for RequestContext.input
        """
        result: dict[str, Any] = {
            "type": input_data.type,
        }

        if input_data.query is not None:
            result["query"] = input_data.query

        if input_data.messages is not None:
            result["messages"] = [m.model_dump() for m in input_data.messages]
            # Also flatten messages into "query" for policies that check text content
            # This allows prompt_filter to work with both data_source and model endpoints
            if "query" not in result:
                result["query"] = " ".join(m.content for m in input_data.messages if m.content)

        # Pass transaction token for billing policies (e.g., TransactionPolicy)
        if input_data.transaction_token is not None:
            result["transaction_token"] = input_data.transaction_token

        return result

    async def _run_handler(self, input_data: RunnerInput, ctx: RequestContext) -> Any:
        """Load and execute the user's handler.

        Args:
            input_data: Runner input containing handler path
            ctx: Request context with metadata

        Returns:
            Handler result (any JSON-serializable value)

        Raises:
            HandlerLoadError: If handler cannot be loaded
            ExecutionError: If handler execution fails
        """
        handler = load_handler(input_data.handler_path, input_data.work_dir)

        try:
            # Call handler based on endpoint type
            if input_data.type == "model":
                messages = input_data.messages or []
                # Convert Pydantic models to dicts for handler
                messages_dicts = [m.model_dump() for m in messages]
                result = handler(messages_dicts, ctx.metadata)
            else:  # data_source
                result = handler(input_data.query or "", ctx.metadata)

            # Handle async handlers
            if asyncio.iscoroutine(result):
                result = await result

            return result

        except Exception as e:
            raise ExecutionError(f"Handler execution failed: {e}") from e

    def _policy_denied_output(self, result: PolicyResult) -> RunnerOutput:
        """Convert PolicyResult to RunnerOutput for denial.

        Args:
            result: Policy result from policy chain

        Returns:
            RunnerOutput indicating policy denial
        """
        return RunnerOutput(
            success=False,
            error=result.reason or "Policy denied",
            error_type="PolicyDenied",
            policy_result=PolicyResultSchema(
                allowed=False,
                policy_name=result.policy_name,
                reason=result.reason,
                pending=result.pending,
                metadata=result.metadata,
            ),
        )
