# Copyright (c) 2024 OpenMined
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects for runner input/output.

These Pydantic models define the contract between the Go SDK
and the Python runner. Changes here require corresponding
changes in the Go SDK schemas.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PolicyConfigSchema(BaseModel):
    """Single policy configuration from YAML.

    Attributes:
        name: Unique identifier for this policy instance
        type: Policy type (e.g., "rate_limit", "access_group")
        config: Type-specific configuration parameters
    """

    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class StoreConfigSchema(BaseModel):
    """Store configuration for stateful policies.

    Attributes:
        type: Store type ("memory" or "sqlite")
        path: Path to SQLite database file (for sqlite type)
    """

    type: str = "memory"
    path: str = ""


class ExecutionContextSchema(BaseModel):
    """Request context passed from Go SDK.

    Attributes:
        user_id: Authenticated user identifier
        endpoint_slug: Target endpoint slug
        endpoint_type: Endpoint type ("model" or "data_source")
        metadata: Additional context metadata
    """

    user_id: str
    endpoint_slug: str
    endpoint_type: str = "data_source"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageSchema(BaseModel):
    """Chat message for model endpoints.

    Attributes:
        role: Message role ("system", "user", or "assistant")
        content: Message content
    """

    role: str
    content: str


class RunnerInput(BaseModel):
    """Complete input from Go SDK via stdin.

    This is the top-level input schema that the runner expects.
    The Go SDK serializes this to JSON and writes to stdin.

    Attributes:
        type: Endpoint type ("model" or "data_source")
        query: Query string for data source endpoints
        messages: Message list for model endpoints
        context: Execution context with user and endpoint info
        policies: List of policy configurations to enforce
        store: Store configuration for stateful policies
        handler_path: Absolute path to the handler (runner.py)
        work_dir: Working directory for the handler
    """

    type: str
    query: str | None = None
    messages: list[MessageSchema] | None = None
    context: ExecutionContextSchema
    policies: list[PolicyConfigSchema] = Field(default_factory=list)
    store: StoreConfigSchema = Field(default_factory=StoreConfigSchema)
    handler_path: str
    work_dir: str


class PolicyResultSchema(BaseModel):
    """Policy evaluation result.

    Attributes:
        allowed: Whether the policy chain allowed the request
        policy_name: Name of the policy that made the decision
        reason: Human-readable explanation (for denials)
        pending: Whether the request is pending async resolution
        metadata: Additional policy-specific metadata
    """

    allowed: bool
    policy_name: str = ""
    reason: str = ""
    pending: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunnerOutput(BaseModel):
    """Complete output to Go SDK via stdout.

    The runner always outputs valid JSON matching this schema,
    even on errors. This ensures the Go SDK can always parse
    the response.

    Attributes:
        success: Whether execution completed successfully
        result: Handler result (on success)
        error: Error message (on failure)
        error_type: Error class name (on failure)
        policy_result: Detailed policy evaluation result
    """

    success: bool
    result: Any = None
    error: str = ""
    error_type: str = ""
    policy_result: PolicyResultSchema | None = None
