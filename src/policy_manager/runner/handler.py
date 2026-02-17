# Copyright (c) 2024 OpenMined
# SPDX-License-Identifier: Apache-2.0
"""Dynamic handler loading for user-defined runner.py files.

Loads Python modules at runtime and extracts the handler function
for execution with policy enforcement.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast


class HandlerLoadError(Exception):
    """Raised when handler cannot be loaded."""

    pass


def load_handler(handler_path: str, work_dir: str) -> Callable[..., Any]:
    """Load handler function from runner.py.

    Dynamically imports the specified Python file and extracts
    the `handler` function. Supports both sync and async handlers.

    Args:
        handler_path: Absolute path to runner.py
        work_dir: Working directory to add to sys.path for imports

    Returns:
        The handler callable

    Raises:
        HandlerLoadError: If handler cannot be loaded

    Example:
        handler = load_handler("/path/to/runner.py", "/path/to")
        result = handler("query", {"user_id": "alice"})
    """
    path = Path(handler_path)

    # Validate path
    if not path.exists():
        raise HandlerLoadError(f"Handler file not found: {handler_path}")

    if not path.is_file():
        raise HandlerLoadError(f"Handler path is not a file: {handler_path}")

    if path.suffix != ".py":
        raise HandlerLoadError(f"Handler must be a .py file: {handler_path}")

    # Add work_dir to sys.path for relative imports within the handler
    if work_dir and work_dir not in sys.path:
        sys.path.insert(0, work_dir)

    try:
        # Load module dynamically using importlib
        spec = importlib.util.spec_from_file_location("runner", handler_path)
        if spec is None:
            raise HandlerLoadError(f"Cannot create module spec: {handler_path}")

        if spec.loader is None:
            raise HandlerLoadError(f"Cannot create module loader: {handler_path}")

        module = importlib.util.module_from_spec(spec)

        # Register module so imports within it work
        sys.modules["runner"] = module

        # Execute module code
        spec.loader.exec_module(module)

        # Extract handler function
        if not hasattr(module, "handler"):
            raise HandlerLoadError(f"runner.py must define a 'handler' function: {handler_path}")

        handler = module.handler

        if not callable(handler):
            raise HandlerLoadError(f"'handler' must be callable: {handler_path}")

        return cast(Callable[..., Any], handler)

    except HandlerLoadError:
        # Re-raise our own errors
        raise
    except SyntaxError as e:
        raise HandlerLoadError(f"Syntax error in handler: {e}") from e
    except ImportError as e:
        raise HandlerLoadError(f"Import error in handler: {e}") from e
    except Exception as e:
        raise HandlerLoadError(f"Failed to load handler: {e}") from e
