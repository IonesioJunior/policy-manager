"""Tests for the runner executor."""

import json
import sqlite3

import pytest

from policy_manager.runner.executor import Executor
from policy_manager.runner.schema import (
    ExecutionContextSchema,
    MessageSchema,
    PolicyConfigSchema,
    RunnerInput,
    StoreConfigSchema,
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


class TestManualReviewSubstitution:
    """End-to-end: a manual_review policy substitutes a placeholder response."""

    def _write_handler(self, tmp_path):
        handler_file = tmp_path / "runner.py"
        handler_file.write_text(
            "def handler(query, metadata):\n"
            "    return {'response': 'real answer: ' + query}\n"
        )
        return handler_file

    async def test_held_request_substitutes_response(self, tmp_path):
        handler_file = self._write_handler(tmp_path)
        store_db = str(tmp_path / "store.db")

        input_data = RunnerInput(
            type="data_source",
            query="hello",
            context=ExecutionContextSchema(user_id="alice", endpoint_slug="ep"),
            policies=[PolicyConfigSchema(name="mr", type="manual_review", config={})],
            store=StoreConfigSchema(type="sqlite", path=store_db),
            handler_path=str(handler_file),
            work_dir=str(tmp_path),
        )

        output = await Executor().execute(input_data)

        # The request succeeds with a substituted placeholder body.
        assert output.success is True
        assert output.policy_result is not None
        assert output.policy_result.allowed is True
        assert output.policy_result.policy_name == "mr"
        review_id = output.policy_result.metadata["review_id"]

        # data_source endpoint -> a single-document placeholder list, which
        # passes cleanly through the Go SDK's []Document codec.
        assert isinstance(output.result, list)
        assert "Request submitted to manual review" in output.result[0]["content"]

        # The real handler output is held in the same SQLite file, pending.
        conn = sqlite3.connect(store_db)
        stored_output, pending = conn.execute(
            "SELECT output, pending FROM manual_reviews WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        conn.close()
        assert json.loads(stored_output) == {"response": "real answer: hello"}
        assert pending == 1

    async def test_normal_request_unaffected(self, tmp_path):
        """Without a substituting policy, the handler result is delivered as-is."""
        handler_file = self._write_handler(tmp_path)

        input_data = RunnerInput(
            type="data_source",
            query="hello",
            context=ExecutionContextSchema(user_id="alice", endpoint_slug="ep"),
            handler_path=str(handler_file),
            work_dir=str(tmp_path),
        )

        output = await Executor().execute(input_data)

        assert output.success is True
        assert output.result == {"response": "real answer: hello"}
        assert output.policy_result is not None
        assert output.policy_result.pending is False


class TestPolicyPhase:
    """policy_phase mode: evaluate one chain, never invoke the handler."""

    def _exploding_handler(self, tmp_path):
        """A handler that raises if run — proves policy_phase skips it."""
        handler_file = tmp_path / "runner.py"
        handler_file.write_text(
            "def handler(query, metadata):\n"
            "    raise RuntimeError('handler must not run in policy_phase mode')\n"
        )
        return handler_file

    async def test_pre_phase_allowed_skips_handler(self, tmp_path):
        handler_file = self._exploding_handler(tmp_path)
        input_data = RunnerInput(
            type="model",
            messages=[MessageSchema(role="user", content="hi")],
            context=ExecutionContextSchema(user_id="alice@acme.com", endpoint_slug="ep"),
            handler_path=str(handler_file),
            work_dir=str(tmp_path),
            policy_phase="pre",
        )

        output = await Executor().execute(input_data)

        # Success despite the exploding handler — it was never invoked.
        assert output.success is True
        assert output.policy_result is not None
        assert output.policy_result.allowed is True

    async def test_pre_phase_denied(self, tmp_path):
        handler_file = self._exploding_handler(tmp_path)
        input_data = RunnerInput(
            type="model",
            messages=[MessageSchema(role="user", content="hi")],
            context=ExecutionContextSchema(user_id="alice@acme.com", endpoint_slug="ep"),
            policies=[
                PolicyConfigSchema(
                    name="ag",
                    type="access_group",
                    config={"users": ["bob@acme.com"], "documents": ["d"]},
                )
            ],
            handler_path=str(handler_file),
            work_dir=str(tmp_path),
            policy_phase="pre",
        )

        output = await Executor().execute(input_data)

        assert output.success is False
        assert output.policy_result is not None
        assert output.policy_result.allowed is False

    async def test_post_phase_substitutes_supplied_output(self, tmp_path):
        handler_file = self._exploding_handler(tmp_path)
        store_db = str(tmp_path / "store.db")
        input_data = RunnerInput(
            type="model",
            messages=[MessageSchema(role="user", content="hi")],
            context=ExecutionContextSchema(user_id="alice@acme.com", endpoint_slug="ep"),
            policies=[PolicyConfigSchema(name="mr", type="manual_review", config={})],
            store=StoreConfigSchema(type="sqlite", path=store_db),
            handler_path=str(handler_file),
            work_dir=str(tmp_path),
            policy_phase="post",
            output={"response": "the real agent reply"},
        )

        output = await Executor().execute(input_data)

        # manual_review post-substitutes a placeholder against the supplied
        # output; the handler was never invoked.
        assert output.success is True
        assert isinstance(output.result, str)
        assert "Request submitted to manual review" in output.result

        # The supplied output is what got persisted for review.
        conn = sqlite3.connect(store_db)
        (stored_output,) = conn.execute(
            "SELECT output FROM manual_reviews"
        ).fetchone()
        conn.close()
        assert json.loads(stored_output) == {"response": "the real agent reply"}
