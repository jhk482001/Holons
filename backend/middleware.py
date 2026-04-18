"""Middleware pipeline for agent task execution.

Deer-flow style aspect-oriented layering. Each middleware wraps the core
LLM call with before/after/error hooks. Middlewares are composed into a
WorkerPipeline and run in order by worker.py.

Design:
- Middlewares are pure classes, stateless across tasks
- `before_task` can raise to abort the task (e.g. quota exceeded)
- `after_task` can mutate the result (e.g. add cost tracking)
- `on_error` can log / alert / transform exceptions
- Middlewares receive a `ctx` dict that persists across hooks (scratch space)

Currently shipped middlewares:
- HotStopMiddleware — aborts if run is cancelling
- CostTrackingMiddleware — accumulates input/output tokens to run
- QueueLoggingMiddleware — verbose debug output (dev)

Future middlewares will be added by later phases (P6/P7):
- QuotaCheckMiddleware
- WorkingHoursMiddleware
- SkillInjectionMiddleware
- SummarizationMiddleware
- EscalationRouter
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("agent_company.middleware")


class AbortTask(Exception):
    """Raised by a middleware's before_task to abort without executing the LLM call.

    The worker will mark the task as `cancelled` or `failed` depending on `reason`.
    """

    def __init__(self, reason: str, status: str = "cancelled"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class Middleware:
    """Base class. Override hooks as needed."""

    name: str = "middleware"

    def before_task(self, task: dict, ctx: dict) -> None:
        """Called before the LLM invocation.
        Raise AbortTask to skip execution entirely.
        May mutate `task.payload` or set keys in `ctx`.
        """
        pass

    def after_task(self, task: dict, result: dict, ctx: dict) -> dict:
        """Called after a successful LLM invocation.
        Must return `result` (possibly modified).
        """
        return result

    def on_error(self, task: dict, error: Exception, ctx: dict) -> None:
        """Called when the LLM invocation raises.
        Does not suppress the exception; the worker still marks task failed.
        """
        pass


# ============================================================================
# Core pipeline
# ============================================================================

class WorkerPipeline:
    """Runs a list of middlewares around a core `execute_fn`.

    Execution model:
        for m in middlewares:  m.before_task(task, ctx)
        try:
            result = execute_fn(task, ctx)
            for m in reversed(middlewares):
                result = m.after_task(task, result, ctx)
            return result
        except Exception as e:
            for m in reversed(middlewares):
                m.on_error(task, e, ctx)
            raise
    """

    def __init__(self, middlewares: list[Middleware] | None = None):
        self.middlewares: list[Middleware] = list(middlewares or [])

    def add(self, mw: Middleware) -> "WorkerPipeline":
        self.middlewares.append(mw)
        return self

    def run(self, task: dict, execute_fn) -> dict:
        """Run one task through the pipeline.

        `task` is the agent_tasks row as dict.
        `execute_fn(task, ctx)` is the core LLM invocation; must return a
        result dict (e.g. {text, input_tokens, output_tokens, cost_usd, ...}).
        """
        ctx: dict[str, Any] = {}

        try:
            for m in self.middlewares:
                m.before_task(task, ctx)
        except AbortTask as e:
            log.info("task %s aborted by middleware: %s", task.get("id"), e.reason)
            raise

        try:
            result = execute_fn(task, ctx)
        except Exception as e:
            for m in reversed(self.middlewares):
                try:
                    m.on_error(task, e, ctx)
                except Exception as inner:
                    log.warning("middleware %s on_error raised: %s", m.name, inner)
            raise

        for m in reversed(self.middlewares):
            try:
                result = m.after_task(task, result, ctx)
            except Exception as e:
                log.warning("middleware %s after_task raised: %s", m.name, e)

        return result


# ============================================================================
# Built-in middlewares for Phase 1/2
# ============================================================================

class HotStopMiddleware(Middleware):
    """Abort the task if its run has been marked `cancelling`.

    This is how the Hot Stop feature propagates — user clicks stop,
    run.status goes to `cancelling`, and any queued task that tries to
    run next is immediately aborted.
    """

    name = "hot_stop"

    def before_task(self, task: dict, ctx: dict) -> None:
        run_id = task.get("run_id")
        if not run_id:
            return
        from . import db
        row = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
        if row and row["status"] in ("cancelling", "cancelled"):
            raise AbortTask(
                f"run {run_id} is {row['status']}",
                status="cancelled",
            )


class CostTrackingMiddleware(Middleware):
    """Accumulate per-task token/cost numbers into the parent run.

    Reads from `result` dict keys: input_tokens, output_tokens, cost_usd, duration_ms.
    Adds them to the parent `runs` row.
    """

    name = "cost_tracking"

    def after_task(self, task: dict, result: dict, ctx: dict) -> dict:
        run_id = task.get("run_id")
        if not run_id:
            return result
        from . import db
        db.execute(
            """
            UPDATE runs
            SET total_input_tokens  = total_input_tokens  + %s,
                total_output_tokens = total_output_tokens + %s,
                total_cost_usd      = total_cost_usd      + %s,
                total_duration_ms   = total_duration_ms   + %s
            WHERE id = %s
            """,
            (
                int(result.get("input_tokens") or 0),
                int(result.get("output_tokens") or 0),
                float(result.get("cost_usd") or 0),
                int(result.get("duration_ms") or 0),
                run_id,
            ),
        )
        return result


class QuotaCheckMiddleware(Middleware):
    """Verify agent quotas before executing and consume after.

    Uses services.quotas for the heavy lifting. If a quota is already
    exceeded, raises AbortTask with status='failed'.
    """

    name = "quota_check"

    def before_task(self, task: dict, ctx: dict) -> None:
        agent_id = task.get("agent_id")
        if not agent_id:
            return
        from .services import quotas
        breach = quotas.check_before(agent_id)
        if breach:
            raise AbortTask(
                f"quota exceeded: {breach['name']} ({breach['used']}/{breach['limit']})",
                status="failed",
            )

    def after_task(self, task: dict, result: dict, ctx: dict) -> dict:
        agent_id = task.get("agent_id")
        if not agent_id:
            return result
        from .services import quotas
        quotas.consume(
            agent_id=agent_id,
            input_tokens=int(result.get("input_tokens") or 0),
            output_tokens=int(result.get("output_tokens") or 0),
            cost_usd=float(result.get("cost_usd") or 0),
        )
        return result


class QueueLoggingMiddleware(Middleware):
    """Verbose debug log for each task execution. Dev only."""

    name = "queue_logging"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def before_task(self, task: dict, ctx: dict) -> None:
        if self.enabled:
            log.info("▶ task %s agent=%s pri=%s", task.get("id"), task.get("agent_id"), task.get("priority"))

    def after_task(self, task: dict, result: dict, ctx: dict) -> dict:
        if self.enabled:
            log.info("✓ task %s tokens=%s cost=%s",
                     task.get("id"),
                     (result.get("input_tokens", 0) + result.get("output_tokens", 0)),
                     result.get("cost_usd", 0))
        return result

    def on_error(self, task: dict, error: Exception, ctx: dict) -> None:
        if self.enabled:
            log.error("✗ task %s failed: %s", task.get("id"), error)


# ============================================================================
# Default pipeline factory
# ============================================================================

def build_default_pipeline() -> WorkerPipeline:
    """Build the default middleware chain.

    Order matters:
    - Logging wraps everything
    - HotStop bails out early if run is cancelling
    - QuotaCheck bails out early if quota exceeded
    - CostTracking records final tokens/cost to run (after_task)
    """
    return WorkerPipeline([
        QueueLoggingMiddleware(),
        HotStopMiddleware(),
        QuotaCheckMiddleware(),
        CostTrackingMiddleware(),
    ])
