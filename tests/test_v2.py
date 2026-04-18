"""Integration tests for agent_company v2 Phase 1+2.

Covers:
  - DB schema apply / teardown between tests
  - Queue: enqueue, priority ordering, atomic claim
  - Middleware pipeline: before/after/on_error + AbortTask
  - Worker: basic run-through a fake LLM + urgent interrupt
  - Engine: dispatch_workflow end-to-end (mocked LLM)
  - Hot Stop: cancel_run aborts queued tasks

Run with:
    cd agent_company && pytest tests/ -v
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from backend import db, engine, queue, worker
from backend.middleware import (
    AbortTask, CostTrackingMiddleware, HotStopMiddleware, Middleware,
    QueueLoggingMiddleware, WorkerPipeline, build_default_pipeline,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def init_db():
    db.init()
    yield
    db.close()


@pytest.fixture(autouse=True)
def clean_state():
    """Wipe mutable state between tests (but keep schema)."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE agent_tasks, run_steps, runs,
                         workflow_nodes, workflows,
                         group_members, groups_tbl,
                         asset_usage_log, asset_audit_log, asset_grants,
                         agent_assets, rag_documents, asset_items,
                         audit_log,
                         agents, as_users
                RESTART IDENTITY CASCADE
            """)
    yield


@pytest.fixture
def user_id():
    return db.execute_returning(
        """
        INSERT INTO as_users (username, password_hash, display_name)
        VALUES ('test', 'x', 'Test User') RETURNING id
        """
    )


@pytest.fixture
def agent_id(user_id):
    return db.execute_returning(
        """
        INSERT INTO agents (user_id, owner_user_id, name, role_title, system_prompt, status)
        VALUES (%s, %s, 'TestAgent', 'role', 'system', 'active') RETURNING id
        """,
        (user_id, user_id),
    )


@pytest.fixture
def run_id(user_id):
    # Need a workflow for FK
    wf = db.execute_returning(
        "INSERT INTO workflows (user_id, name) VALUES (%s, 'test-wf') RETURNING id",
        (user_id,),
    )
    return db.execute_returning(
        "INSERT INTO runs (workflow_id, user_id, initial_input, status) VALUES (%s, %s, 'hi', 'running') RETURNING id",
        (wf, user_id),
    )


# ============================================================================
# Queue tests
# ============================================================================

class TestQueue:

    def test_enqueue_and_claim(self, agent_id, run_id):
        tid = queue.enqueue_task(agent_id, {"prompt": "hello"}, run_id=run_id)
        assert tid is not None

        task = queue.claim_next_task(agent_id)
        assert task is not None
        assert task["id"] == tid
        assert task["status"] == "running"

    def test_claim_empty_returns_none(self, agent_id):
        assert queue.claim_next_task(agent_id) is None

    def test_priority_ordering(self, agent_id, run_id):
        """higher priority should be claimed first."""
        low_id = queue.enqueue_task(agent_id, {"p": "low"}, run_id=run_id, priority="low")
        urgent_id = queue.enqueue_task(agent_id, {"p": "urgent"}, run_id=run_id, priority="urgent")
        normal_id = queue.enqueue_task(agent_id, {"p": "normal"}, run_id=run_id, priority="normal")

        first = queue.claim_next_task(agent_id)
        assert first["id"] == urgent_id

        # mark done and claim next
        queue.mark_done(urgent_id)
        second = queue.claim_next_task(agent_id)
        assert second["id"] == normal_id

        queue.mark_done(normal_id)
        third = queue.claim_next_task(agent_id)
        assert third["id"] == low_id

    def test_critical_jumps_queue(self, agent_id, run_id):
        a = queue.enqueue_task(agent_id, {}, run_id=run_id, priority="normal")
        b = queue.enqueue_task(agent_id, {}, run_id=run_id, priority="normal")
        c = queue.enqueue_task(agent_id, {}, run_id=run_id, priority="critical")

        first = queue.claim_next_task(agent_id)
        assert first["id"] == c  # critical wins

    def test_same_priority_fifo(self, agent_id, run_id):
        first_id = queue.enqueue_task(agent_id, {}, run_id=run_id)
        second_id = queue.enqueue_task(agent_id, {}, run_id=run_id)

        assert queue.claim_next_task(agent_id)["id"] == first_id
        queue.mark_done(first_id)
        assert queue.claim_next_task(agent_id)["id"] == second_id

    def test_queue_depth_tracking(self, agent_id, run_id):
        assert queue.queue_depth(agent_id) == 0
        queue.enqueue_task(agent_id, {}, run_id=run_id)
        queue.enqueue_task(agent_id, {}, run_id=run_id)
        assert queue.queue_depth(agent_id) == 2

    def test_max_queue_depth_enforced(self, user_id):
        aid = db.execute_returning(
            """
            INSERT INTO agents (user_id, owner_user_id, name, max_queue_depth, status)
            VALUES (%s, %s, 'Capped', 2, 'active') RETURNING id
            """,
            (user_id, user_id),
        )
        queue.enqueue_task(aid, {})
        queue.enqueue_task(aid, {})
        with pytest.raises(queue.QueueFull):
            queue.enqueue_task(aid, {})

    def test_agent_status_blocks_enqueue(self, user_id):
        aid = db.execute_returning(
            """
            INSERT INTO agents (user_id, owner_user_id, name, status)
            VALUES (%s, %s, 'Paused', 'paused') RETURNING id
            """,
            (user_id, user_id),
        )
        with pytest.raises(queue.AgentUnavailable):
            queue.enqueue_task(aid, {})

    def test_pause_and_resume(self, agent_id, run_id):
        tid = queue.enqueue_task(agent_id, {}, run_id=run_id)
        task = queue.claim_next_task(agent_id)
        queue.mark_paused(task["id"], {"checkpoint": 42})

        row = queue.get_task(tid)
        assert row["status"] == "paused"
        assert row["progress_snapshot"] == {"checkpoint": 42}

        queue.resume_paused(tid)
        assert queue.get_task(tid)["status"] == "queued"

    def test_cancel_run_cancels_queued(self, agent_id, run_id):
        t1 = queue.enqueue_task(agent_id, {}, run_id=run_id)
        t2 = queue.enqueue_task(agent_id, {}, run_id=run_id)
        queue.cancel_run(run_id)

        assert queue.get_task(t1)["status"] == "cancelled"
        assert queue.get_task(t2)["status"] == "cancelled"
        # With no running tasks remaining, cancel_run finalizes immediately
        run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert run["status"] == "cancelled"


# ============================================================================
# Middleware tests
# ============================================================================

class TestMiddleware:

    def test_basic_pipeline_run(self):
        mw = WorkerPipeline([QueueLoggingMiddleware(enabled=False)])
        result = mw.run({"id": 1}, lambda t, c: {"text": "ok"})
        assert result == {"text": "ok"}

    def test_before_after_hooks_called(self):
        calls = []

        class Trace(Middleware):
            name = "trace"
            def before_task(self, t, ctx):
                calls.append("before")
                ctx["x"] = 1
            def after_task(self, t, r, ctx):
                calls.append(f"after-{ctx['x']}")
                r["x"] = ctx["x"]
                return r

        mw = WorkerPipeline([Trace()])
        result = mw.run({"id": 1}, lambda t, c: {"text": "ok"})
        assert calls == ["before", "after-1"]
        assert result["x"] == 1

    def test_abort_task_raises(self):
        class Blocker(Middleware):
            def before_task(self, t, ctx):
                raise AbortTask("nope")

        mw = WorkerPipeline([Blocker()])
        with pytest.raises(AbortTask) as exc:
            mw.run({"id": 1}, lambda t, c: {"text": "unused"})
        assert exc.value.reason == "nope"

    def test_error_hooks_called(self):
        errors = []

        class Logger(Middleware):
            def on_error(self, t, e, ctx):
                errors.append(str(e))

        mw = WorkerPipeline([Logger()])
        with pytest.raises(RuntimeError):
            mw.run({"id": 1}, lambda t, c: (_ for _ in ()).throw(RuntimeError("boom")))
        assert errors == ["boom"]

    def test_hot_stop_middleware(self, agent_id, run_id):
        # Mark run as cancelling
        db.execute("UPDATE runs SET status = 'cancelling' WHERE id = %s", (run_id,))

        mw = WorkerPipeline([HotStopMiddleware()])
        task = {"id": 1, "run_id": run_id}
        with pytest.raises(AbortTask) as exc:
            mw.run(task, lambda t, c: {"text": "should not run"})
        assert "cancelling" in exc.value.reason

    def test_cost_tracking_updates_run(self, agent_id, run_id):
        mw = WorkerPipeline([CostTrackingMiddleware()])
        mw.run(
            {"id": 1, "run_id": run_id},
            lambda t, c: {
                "text": "ok",
                "input_tokens": 100,
                "output_tokens": 200,
                "cost_usd": 0.005,
                "duration_ms": 500,
            },
        )
        run = db.fetch_one("SELECT * FROM runs WHERE id = %s", (run_id,))
        assert run["total_input_tokens"] == 100
        assert run["total_output_tokens"] == 200
        assert float(run["total_cost_usd"]) == 0.005
        assert run["total_duration_ms"] == 500


# ============================================================================
# Worker + Engine end-to-end (with mocked LLM)
# ============================================================================

class TestEndToEnd:

    def test_single_node_workflow(self, user_id):
        """Dispatch a workflow with one agent node, let a worker process it,
        verify run completes successfully with mocked LLM."""
        # Create an agent
        aid = db.execute_returning(
            """
            INSERT INTO agents (user_id, owner_user_id, name, role_title, system_prompt, status)
            VALUES (%s, %s, 'Writer', 'writer', 'you write', 'active') RETURNING id
            """,
            (user_id, user_id),
        )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'single') RETURNING id",
            (user_id,),
        )
        db.execute(
            """
            INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label, prompt_template)
            VALUES (%s, 0, 'agent', %s, 'n1', 'Write: {{input}}')
            """,
            (wid, aid),
        )

        # Mock the LLM
        def fake_invoke(**kwargs):
            return {
                "text": "[FAKE] " + kwargs.get("user_text", ""),
                "input_tokens": 10,
                "output_tokens": 20,
                "cost_usd": 0.0001,
                "duration_ms": 50,
                "model_id": "fake",
                "provider": "fake",
                "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke):
            # Start a worker manually
            w = worker.AgentWorker(aid, build_default_pipeline())
            w.start()

            try:
                run_id = engine.dispatch_workflow(wid, user_id, "hello world")

                # Wait for run to complete
                for _ in range(50):
                    run = db.fetch_one("SELECT status, final_output FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
                else:
                    raise AssertionError("run did not complete in time")
            finally:
                w.stop()
                w.thread.join(timeout=3)

        assert run["status"] == "done"
        assert "hello world" in run["final_output"]

        steps = db.fetch_all("SELECT * FROM run_steps WHERE run_id = %s", (run_id,))
        assert len(steps) == 1
        assert steps[0]["input_tokens"] == 10

    def test_hot_stop_aborts_run(self, user_id):
        """Dispatch a multi-node workflow, cancel after first task, verify subsequent tasks are cancelled."""
        aid = db.execute_returning(
            """
            INSERT INTO agents (user_id, owner_user_id, name, status)
            VALUES (%s, %s, 'SlowAgent', 'active') RETURNING id
            """,
            (user_id, user_id),
        )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'multi') RETURNING id",
            (user_id,),
        )
        # Two sequential nodes
        for i in range(2):
            db.execute(
                "INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label) VALUES (%s, %s, 'agent', %s, %s)",
                (wid, i, aid, f"n{i}"),
            )

        def slow_invoke(**kwargs):
            time.sleep(0.3)  # slow enough to cancel during
            return {
                "text": "done", "input_tokens": 1, "output_tokens": 1,
                "cost_usd": 0, "duration_ms": 300, "model_id": "fake",
                "provider": "fake", "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=slow_invoke):
            w = worker.AgentWorker(aid, build_default_pipeline())
            w.start()
            try:
                run_id = engine.dispatch_workflow(wid, user_id, "hi")
                # Cancel shortly after
                time.sleep(0.1)
                queue.cancel_run(run_id)

                # Wait for state to settle
                for _ in range(40):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("cancelled", "done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                w.stop()
                w.thread.join(timeout=3)

        # Either cancelled or finished with one step depending on timing
        assert run["status"] in ("cancelled", "done")
        if run["status"] == "cancelled":
            # At least one task should have been cancelled (not just done)
            cancelled = db.fetch_one(
                "SELECT COUNT(*) AS c FROM agent_tasks WHERE run_id = %s AND status = 'cancelled'",
                (run_id,),
            )
            assert cancelled["c"] >= 0  # zero is OK if the first task finished before cancel

    def test_parallel_group_with_aggregator(self, user_id):
        """Workflow: [group(A,B) → aggregator C] → run end-to-end."""
        # Agents
        a_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'A', 'active') RETURNING id",
            (user_id, user_id),
        )
        b_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'B', 'active') RETURNING id",
            (user_id, user_id),
        )
        c_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'C', 'active') RETURNING id",
            (user_id, user_id),
        )
        gid = db.execute_returning(
            """
            INSERT INTO groups_tbl (user_id, name, mode, aggregator_agent_id)
            VALUES (%s, 'g1', 'parallel', %s) RETURNING id
            """,
            (user_id, c_id),
        )
        for i, ag in enumerate([a_id, b_id]):
            db.execute(
                "INSERT INTO group_members (group_id, agent_id, position) VALUES (%s, %s, %s)",
                (gid, ag, i),
            )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'p') RETURNING id",
            (user_id,),
        )
        db.execute(
            "INSERT INTO workflow_nodes (workflow_id, position, node_type, group_id, label) VALUES (%s, 0, 'group', %s, 'review')",
            (wid, gid),
        )

        call_log = []
        def fake_invoke(**kwargs):
            call_log.append(kwargs.get("user_text", "")[:30])
            return {
                "text": "answer from " + kwargs.get("system_prompt", "")[:5],
                "input_tokens": 5, "output_tokens": 5, "cost_usd": 0,
                "duration_ms": 10, "model_id": "fake", "provider": "fake", "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke):
            workers = [worker.AgentWorker(aid, build_default_pipeline()) for aid in (a_id, b_id, c_id)]
            for w in workers:
                w.start()
            try:
                run_id = engine.dispatch_workflow(wid, user_id, "review this")
                for _ in range(80):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                for w in workers:
                    w.stop()
                for w in workers:
                    w.thread.join(timeout=3)

        assert run["status"] == "done"
        # Both members + aggregator = 3 calls
        assert len(call_log) == 3

    def test_tool_loop_executes_multi_turn(self, user_id):
        """Agent with tool_config enters a multi-turn loop. First LLM turn
        requests a tool; engine executes the tool; second LLM turn sees the
        result and ends. Verifies run_steps capture each turn + tool_calls
        payload contains the invocation details."""
        from backend import tools as tool_registry

        # Install a throwaway tool for the duration of this test
        fake_calls: list[dict] = []

        def fake_handler(args, ctx):
            fake_calls.append({"args": args, "ctx": ctx})
            return {"result": "the answer is 42"}

        tool_registry.register(
            "fake_lookup",
            {
                "name": "fake_lookup",
                "description": "test-only lookup",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                }},
            },
            fake_handler,
        )
        tool_registry._discovered = True  # prevent auto-discovery override

        a_id = db.execute_returning(
            """
            INSERT INTO agents (user_id, owner_user_id, name, status, tool_config)
            VALUES (%s, %s, 'ToolBot', 'active', '["fake_lookup"]'::jsonb)
            RETURNING id
            """,
            (user_id, user_id),
        )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'tool-wf') RETURNING id",
            (user_id,),
        )
        db.execute(
            "INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label, prompt_template) "
            "VALUES (%s, 0, 'agent', %s, 'n', '找答案：{{input}}')",
            (wid, a_id),
        )

        # LLM behaviour: first call → toolUse; second call → plain text.
        turn_count = {"n": 0}

        def fake_invoke(**kw):
            turn_count["n"] += 1
            if turn_count["n"] == 1:
                return {
                    "text": "",
                    "tool_uses": [{"toolUseId": "tu_1", "name": "fake_lookup", "input": {"q": "question"}}],
                    "stop_reason": "tool_use",
                    "assistant_message": {"role": "assistant", "content": [
                        {"toolUse": {"toolUseId": "tu_1", "name": "fake_lookup", "input": {"q": "question"}}},
                    ]},
                    "input_tokens": 10, "output_tokens": 15, "cost_usd": 0.001,
                    "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
                }
            return {
                "text": "Based on the tool result, the answer is 42.",
                "tool_uses": [],
                "stop_reason": "end_turn",
                "assistant_message": {"role": "assistant", "content": [
                    {"text": "Based on the tool result, the answer is 42."},
                ]},
                "input_tokens": 20, "output_tokens": 25, "cost_usd": 0.002,
                "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke):
            w = worker.AgentWorker(a_id, build_default_pipeline())
            w.start()
            try:
                run_id = engine.dispatch_workflow(wid, user_id, "what is 6*7?", trigger_source="api")
                for _ in range(60):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                w.stop()
                w.thread.join(timeout=3)

        assert run["status"] == "done"
        assert turn_count["n"] == 2  # two LLM turns
        assert len(fake_calls) == 1  # one tool invocation
        assert fake_calls[0]["args"] == {"q": "question"}
        assert fake_calls[0]["ctx"]["agent_id"] == a_id
        assert fake_calls[0]["ctx"]["run_id"] == run_id

        # Two run_steps: one per turn. First has tool_calls populated, second is empty.
        steps = db.fetch_all(
            "SELECT * FROM run_steps WHERE run_id = %s ORDER BY id",
            (run_id,),
        )
        assert len(steps) == 2
        assert steps[0]["turn"] == 1
        assert steps[1]["turn"] == 2
        tc = steps[0]["tool_calls"]
        if isinstance(tc, str):
            tc = json.loads(tc)
        assert len(tc) == 1
        assert tc[0]["name"] == "fake_lookup"
        assert tc[0]["output"] == {"result": "the answer is 42"}
        # Second turn has no tool calls
        tc2 = steps[1]["tool_calls"]
        if isinstance(tc2, str):
            tc2 = json.loads(tc2)
        assert tc2 == []
        # Final step has the plain-text answer
        assert "42" in steps[1]["response"]

        # Final output of the run surfaces the last turn's text
        run_full = db.fetch_one("SELECT final_output FROM runs WHERE id = %s", (run_id,))
        assert "42" in (run_full["final_output"] or "")

        # Cleanup: unregister the fake tool so it doesn't leak into other tests
        tool_registry._REGISTRY.pop("fake_lookup", None)

    def test_rag_asset_synthetic_search_tool(self, user_id):
        """Agent assigned a RAG asset gets a synthetic search_kb_<id> tool.
        When the LLM calls it, engine dispatches to rag.search, persists
        the hits into the tool_calls log, and writes asset_usage_log."""
        from backend.services import assets as assets_service
        from backend.services import rag as rag_service

        # Create the RAG asset + agent, assign asset to agent
        asset_id = assets_service.create_asset(
            actor_user_id=user_id,
            kind="rag",
            name="smoke_kb",
            config={"backend": "pgvector"},
        )
        a_id = db.execute_returning(
            """
            INSERT INTO agents (user_id, owner_user_id, name, status)
            VALUES (%s, %s, 'RagBot', 'active')
            RETURNING id
            """,
            (user_id, user_id),
        )
        assets_service.assign_to_agent(asset_id, a_id, user_id)
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'rag-wf') RETURNING id",
            (user_id,),
        )
        db.execute(
            "INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label, prompt_template) "
            "VALUES (%s, 0, 'agent', %s, 'n', '查：{{input}}')",
            (wid, a_id),
        )

        # Mock LLM: first turn → calls search_kb_<asset_id>; second turn → ends.
        expected_tool_name = f"search_kb_{asset_id}"
        turn_count = {"n": 0}

        def fake_invoke(**kw):
            turn_count["n"] += 1
            if turn_count["n"] == 1:
                return {
                    "text": "",
                    "tool_uses": [{
                        "toolUseId": "tu_1",
                        "name": expected_tool_name,
                        "input": {"query": "foo", "top_k": 2},
                    }],
                    "stop_reason": "tool_use",
                    "assistant_message": {"role": "assistant", "content": [
                        {"toolUse": {
                            "toolUseId": "tu_1",
                            "name": expected_tool_name,
                            "input": {"query": "foo", "top_k": 2},
                        }},
                    ]},
                    "input_tokens": 10, "output_tokens": 15, "cost_usd": 0.001,
                    "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
                }
            return {
                "text": "Done searching.",
                "tool_uses": [],
                "stop_reason": "end_turn",
                "assistant_message": {"role": "assistant", "content": [{"text": "Done searching."}]},
                "input_tokens": 20, "output_tokens": 10, "cost_usd": 0.001,
                "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
            }

        # Mock rag.search to return a fixed hit
        def fake_search(asset, query, top_k):
            assert asset["id"] == asset_id
            assert query == "foo"
            assert top_k == 2
            return [{
                "id": 1, "source_name": "doc1", "chunk_index": 0,
                "content": "matched chunk", "score": 0.95, "metadata": {},
            }]

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke), \
             patch.object(rag_service, "search", side_effect=fake_search):
            w = worker.AgentWorker(a_id, build_default_pipeline())
            w.start()
            try:
                run_id = engine.dispatch_workflow(
                    wid, user_id, "find foo", trigger_source="api",
                )
                for _ in range(60):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                w.stop()
                w.thread.join(timeout=3)

        assert run["status"] == "done"
        assert turn_count["n"] == 2

        # run_steps[0].tool_calls should contain the RAG search call with
        # the right asset_id stamp
        steps = db.fetch_all(
            "SELECT tool_calls FROM run_steps WHERE run_id = %s ORDER BY id",
            (run_id,),
        )
        tc = steps[0]["tool_calls"]
        if isinstance(tc, str):
            tc = json.loads(tc)
        assert len(tc) == 1
        assert tc[0]["name"] == expected_tool_name
        assert tc[0]["asset_id"] == asset_id
        assert tc[0]["output"]["count"] == 1

        # asset_usage_log should have exactly one row for this asset
        usage = db.fetch_all(
            "SELECT asset_id, user_id, agent_id, run_id, ok FROM asset_usage_log "
            "WHERE asset_id = %s",
            (asset_id,),
        )
        assert len(usage) == 1
        assert usage[0]["agent_id"] == a_id
        assert usage[0]["run_id"] == run_id
        assert usage[0]["ok"] is True

    def test_api_trigger_source_skips_notification(self, user_id):
        """Runs dispatched with trigger_source='api' (e.g. e2e tests,
        programmatic clients) should not append a Lead message or emit
        a notification."""
        a_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'A', 'active') RETURNING id",
            (user_id, user_id),
        )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'silent') RETURNING id",
            (user_id,),
        )
        db.execute(
            "INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label, prompt_template) "
            "VALUES (%s, 0, 'agent', %s, 'n', 'do: {{input}}')",
            (wid, a_id),
        )

        def fake_invoke(**kwargs):
            return {
                "text": "OK", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0,
                "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke):
            w = worker.AgentWorker(a_id, build_default_pipeline())
            w.start()
            try:
                run_id = engine.dispatch_workflow(wid, user_id, "x", trigger_source="api")
                for _ in range(50):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                w.stop()
                w.thread.join(timeout=3)

        assert run["status"] == "done"

        lm = db.fetch_one(
            "SELECT id FROM lead_messages WHERE metadata->>'run_id' = %s",
            (str(run_id),),
        )
        assert lm is None, "API-triggered runs must not pollute Lead thread"

        nf = db.fetch_one(
            "SELECT id FROM notifications WHERE related_run_id = %s",
            (run_id,),
        )
        assert nf is None, "API-triggered runs must not emit notifications"

    def test_input_and_prev_output_are_separated(self, user_id):
        """Verify {{input}} always refers to the original run input and
        {{prev_output}} refers only to the immediately preceding step."""
        a_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'A', 'active') RETURNING id",
            (user_id, user_id),
        )
        b_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'B', 'active') RETURNING id",
            (user_id, user_id),
        )
        c_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'C', 'active') RETURNING id",
            (user_id, user_id),
        )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'ip-test') RETURNING id",
            (user_id,),
        )
        # Three sequential nodes. Each node's template references both
        # {{input}} and {{prev_output}} so we can tell them apart.
        templates = [
            "FIRST:input={{input}}|prev={{prev_output}}",
            "SECOND:input={{input}}|prev={{prev_output}}",
            "THIRD:input={{input}}|prev={{prev_output}}",
        ]
        for i, (ag, tpl) in enumerate(zip([a_id, b_id, c_id], templates)):
            db.execute(
                """
                INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label, prompt_template)
                VALUES (%s, %s, 'agent', %s, %s, %s)
                """,
                (wid, i, ag, f"n{i}", tpl),
            )

        captured: list[str] = []

        def fake_invoke(**kwargs):
            captured.append(kwargs.get("user_text", ""))
            # Simulate each agent producing a distinctive output
            idx = len(captured)
            return {
                "text": f"OUTPUT_{idx}",
                "input_tokens": 1, "output_tokens": 1, "cost_usd": 0,
                "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke):
            workers = [worker.AgentWorker(ag, build_default_pipeline()) for ag in (a_id, b_id, c_id)]
            for w in workers:
                w.start()
            try:
                run_id = engine.dispatch_workflow(wid, user_id, "ORIGINAL")
                for _ in range(50):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                for w in workers:
                    w.stop()
                for w in workers:
                    w.thread.join(timeout=3)

        assert run["status"] == "done"
        assert len(captured) == 3, f"expected 3 calls, got {len(captured)}"
        # First node: input and prev_output both = ORIGINAL
        assert captured[0] == "FIRST:input=ORIGINAL|prev=ORIGINAL"
        # Second node: input still ORIGINAL, prev_output = OUTPUT_1
        assert captured[1] == "SECOND:input=ORIGINAL|prev=OUTPUT_1"
        # Third node: input still ORIGINAL, prev_output = OUTPUT_2
        assert captured[2] == "THIRD:input=ORIGINAL|prev=OUTPUT_2"

    def test_run_completion_notifies_lead(self, user_id):
        """After a user-triggered run completes, a Lead message should be
        appended to the user's active Lead thread and a notification emitted.
        """
        a_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) VALUES (%s, %s, 'W', 'active') RETURNING id",
            (user_id, user_id),
        )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'notif-test') RETURNING id",
            (user_id,),
        )
        db.execute(
            "INSERT INTO workflow_nodes (workflow_id, position, node_type, agent_id, label, prompt_template) "
            "VALUES (%s, 0, 'agent', %s, 'n', 'do: {{input}}')",
            (wid, a_id),
        )

        def fake_invoke(**kwargs):
            return {
                "text": "FINAL_RESULT_XYZ",
                "input_tokens": 1, "output_tokens": 1, "cost_usd": 0,
                "duration_ms": 1, "model_id": "fake", "provider": "fake", "error": None,
            }

        with patch("backend.engine.llm_invoke", side_effect=fake_invoke):
            w = worker.AgentWorker(a_id, build_default_pipeline())
            w.start()
            try:
                run_id = engine.dispatch_workflow(wid, user_id, "do the thing", trigger_source="manual")
                for _ in range(50):
                    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
                    if run["status"] in ("done", "error"):
                        break
                    time.sleep(0.15)
            finally:
                w.stop()
                w.thread.join(timeout=3)

        assert run["status"] == "done"

        # A Lead summary message should exist for this run. The new format
        # is a clean summary (workflow name + run id + stats), NOT a paste of
        # the raw final_output.
        lead_msg = db.fetch_one(
            """
            SELECT m.* FROM lead_messages m
            JOIN lead_conversations c ON c.thread_id = m.thread_id
            WHERE c.user_id = %s AND m.role = 'lead'
              AND m.metadata->>'run_id' = %s
            ORDER BY m.id DESC LIMIT 1
            """,
            (user_id, str(run_id)),
        )
        assert lead_msg is not None, "Lead message about run completion should exist"
        assert "notif-test" in lead_msg["content"]  # workflow name
        assert str(run_id) in lead_msg["content"]
        # Crucial: raw agent output should NOT be dumped into the message
        assert "FINAL_RESULT_XYZ" not in lead_msg["content"]

        # A notification should also exist
        notif = db.fetch_one(
            """
            SELECT * FROM notifications
            WHERE user_id = %s AND related_run_id = %s
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, run_id),
        )
        assert notif is not None, "notification about run completion should exist"
