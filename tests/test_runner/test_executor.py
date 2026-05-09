"""Tests for the runner executor."""

import pytest

from policy_manager.runner.executor import Executor
from policy_manager.runner.schema import (
    ExecutionContextSchema,
    MessageSchema,
    RunnerInput,
)


class TestBuildInputDict:
    """Tests for Executor._build_input_dict()."""

    @pytest.fixture
    def executor(self):
        return Executor()

    def test_basic_data_source_input(self, executor):
        """Test basic data source input building."""
        input_data = RunnerInput(
            type="data_source",
            query="test query",
            context=ExecutionContextSchema(user_id="alice", endpoint_slug="test"),
            handler_path="/path/to/handler.py",
            work_dir="/path/to",
        )

        result = executor._build_input_dict(input_data)

        assert result["type"] == "data_source"
        assert result["query"] == "test query"

    def test_model_input_with_messages(self, executor):
        """Test model input with messages."""
        input_data = RunnerInput(
            type="model",
            messages=[
                MessageSchema(role="user", content="Hello"),
                MessageSchema(role="assistant", content="Hi there"),
            ],
            context=ExecutionContextSchema(user_id="alice", endpoint_slug="test"),
            handler_path="/path/to/handler.py",
            work_dir="/path/to",
        )

        result = executor._build_input_dict(input_data)

        assert result["type"] == "model"
        assert len(result["messages"]) == 2
        assert result["query"] == "Hello Hi there"
