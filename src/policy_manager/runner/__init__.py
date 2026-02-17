# Copyright (c) 2024 OpenMined
# SPDX-License-Identifier: Apache-2.0
"""Runner submodule for executing handlers with policy enforcement.

This module provides the infrastructure for running Python handlers
with policy enforcement when invoked by the Go SDK.

Usage:
    python -m policy_manager.runner < input.json > output.json

Exports:
    Executor: Main orchestrator for policy-aware execution
    PolicyFactory: Creates policy instances from configuration
    RunnerInput: Input schema from Go SDK
    RunnerOutput: Output schema to Go SDK
"""

from .executor import Executor
from .factory import PolicyFactory
from .handler import HandlerLoadError, load_handler
from .schema import (
    ExecutionContextSchema,
    PolicyConfigSchema,
    PolicyResultSchema,
    RunnerInput,
    RunnerOutput,
    StoreConfigSchema,
)

__all__ = [
    "ExecutionContextSchema",
    "Executor",
    "HandlerLoadError",
    "PolicyConfigSchema",
    "PolicyFactory",
    "PolicyResultSchema",
    "RunnerInput",
    "RunnerOutput",
    "StoreConfigSchema",
    "load_handler",
]
