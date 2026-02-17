# Copyright (c) 2024 OpenMined
# SPDX-License-Identifier: Apache-2.0
"""Entry point for policy-manager runner.

Usage:
    python -m policy_manager.runner < input.json > output.json

The runner reads JSON input from stdin, executes the handler with
policy enforcement, and writes JSON output to stdout.

Exit codes:
    0: Success
    1: Failure (error details in JSON output)
"""

from __future__ import annotations

import asyncio
import sys

from .executor import Executor
from .schema import RunnerInput, RunnerOutput


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        # Read input from stdin
        input_json = sys.stdin.read()

        # Validate input against schema
        input_data = RunnerInput.model_validate_json(input_json)

        # Execute with policy enforcement
        executor = Executor()
        output = asyncio.run(executor.execute(input_data))

        # Write output to stdout
        print(output.model_dump_json())

        return 0 if output.success else 1

    except Exception as e:
        # Ensure we always output valid JSON, even on unexpected errors
        error_output = RunnerOutput(
            success=False,
            error=str(e),
            error_type=type(e).__name__,
        )
        print(error_output.model_dump_json())
        return 1


if __name__ == "__main__":
    sys.exit(main())
