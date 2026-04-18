"""Per-agent worker loop.

Every active agent gets its own background thread. The thread polls
the queue for that agent, runs tasks through the middleware pipeline,
and handles urgent interrupt signals.

Design:
- One `threading.Thread` per agent
- A `WorkerRegistry` tracks all workers and can stop them
- `threading.Event` per agent signals urgent interrupts
- Urgent interrupts work at step boundary (not LLM call level)
- When an urgent task is enqueued, the worker's interrupt flag is set;
  when the current task finishes its current step, it checks the flag
  and pauses itself, saving progress_snapshot
- After urgent completes, the paused task is automatically resumed
"""
from __future__ import annotations

import logging
import threading
import time

from . import db, queue
from .middleware import AbortTask, WorkerPipeline, build_default_pipeline

log = logging.getLogger("agent_company.worker")


# ============================================================================
# Worker registry — keeps track of all running workers
# ============================================================================

class WorkerRegistry:
    def __init__(self):
        self._workers: dict[int, "AgentWorker"] = {}
        self._lock = threading.Lock()

    def start_agent(self, agent_id: int, pipeline: WorkerPipeline | None = None) -> "AgentWorker":
        with self._lock:
            if agent_id in self._workers:
                return self._workers[agent_id]
            worker = AgentWorker(agent_id, pipeline or build_default_pipeline())
            worker.start()
            self._workers[agent_id] = worker
            return worker

    def stop_agent(self, agent_id: int) -> None:
        with self._lock:
            worker = self._workers.pop(agent_id, None)
        if worker:
            worker.stop()

    def get(self, agent_id: int) -> "AgentWorker | None":
        return self._workers.get(agent_id)

    def signal_urgent(self, agent_id: int) -> None:
        """Signal the worker to check for urgent tasks after its current step."""
        worker = self._workers.get(agent_id)
        if worker:
            worker.urgent_event.set()

    def start_all_active(self) -> int:
        """On startup, launch a worker for every active agent in the DB."""
        rows = db.fetch_all(
            "SELECT id FROM agents WHERE status = 'active'",
        )
        count = 0
        for row in rows:
            self.start_agent(row["id"])
            count += 1
        log.info("started %d agent workers", count)
        return count

    def stop_all(self, timeout: float = 30) -> None:
        """Signal all workers to stop, then wait up to `timeout` seconds for
        in-flight tasks to finish. Workers check _stop between poll cycles
        and will exit after their current task completes."""
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()
        per_worker = max(3, timeout / max(1, len(workers)))
        for w in workers:
            w.thread.join(timeout=per_worker)
            if w.thread.is_alive():
                log.warning("worker %s did not stop within %.0fs", w.agent_id, per_worker)


_registry = WorkerRegistry()


def registry() -> WorkerRegistry:
    return _registry


# ============================================================================
# Per-agent worker
# ============================================================================

class AgentWorker:
    """A single agent's execution thread.

    Concurrency = 1 by design (one thing at a time, like a real employee).
    """

    POLL_INTERVAL_SECONDS = 0.75

    def __init__(self, agent_id: int, pipeline: WorkerPipeline):
        self.agent_id = agent_id
        self.pipeline = pipeline
        self.urgent_event = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"agent-{agent_id}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        log.info("worker %s starting", self.agent_id)
        while not self._stop.is_set():
            try:
                task = queue.claim_next_task(self.agent_id)
            except Exception as e:
                log.exception("worker %s claim error: %s", self.agent_id, e)
                time.sleep(2)
                continue

            if task is None:
                # nothing to do — sleep briefly
                self._stop.wait(self.POLL_INTERVAL_SECONDS)
                continue

            # If we picked up a paused task, resume from snapshot
            resuming = task.get("progress_snapshot") is not None

            try:
                self._execute_task(task, resuming=resuming)
            except Exception as e:
                msg = str(e)
                # Quota mid-flight: park the task as 'paused' with the reason
                # rather than burning the run. User can resume after raising
                # the cap or waiting for daily window.
                if "quota blocked" in msg.lower():
                    log.warning("worker %s task %s blocked by quota: %s",
                                self.agent_id, task.get("id"), msg)
                    try:
                        from . import db as _db
                        _db.execute(
                            "UPDATE agent_tasks SET status = 'paused', "
                            "last_error = %s WHERE id = %s",
                            (msg[:500], task["id"]),
                        )
                    except Exception:
                        log.exception("failed to mark task paused on quota")
                else:
                    log.exception("worker %s task %s crashed: %s",
                                  self.agent_id, task.get("id"), e)
                    try:
                        queue.mark_failed(task["id"], str(e))
                        self._notify_engine_error(task, e)
                    except Exception:
                        log.exception("failed to mark task failed")

            # After task ends, check for urgent signal
            if self.urgent_event.is_set():
                self.urgent_event.clear()
                # No explicit action needed — the loop will naturally pick up
                # the urgent task next because it has higher priority_num

        log.info("worker %s stopped", self.agent_id)

    # ------------------------------------------------------------------
    # Single task execution
    # ------------------------------------------------------------------

    def _execute_task(self, task: dict, resuming: bool = False) -> None:
        task_id = task["id"]
        log.debug("worker %s executing task %s resuming=%s",
                  self.agent_id, task_id, resuming)

        try:
            result = self.pipeline.run(task, self._core_execute)
        except AbortTask as e:
            log.info("task %s aborted: %s", task_id, e.reason)
            if e.status == "cancelled":
                queue.mark_cancelled(task_id, e.reason)
            else:
                queue.mark_failed(task_id, e.reason)
            self._notify_engine_aborted(task, e)
            return

        # Check urgent interrupt at step boundary
        if self.urgent_event.is_set() and self._should_pause_for_urgent(task):
            log.info("task %s paused for urgent interrupt", task_id)
            snapshot = {
                "last_result": result,
                "resumed_at": time.time(),
            }
            queue.mark_paused(task_id, snapshot)
            return

        queue.mark_done(task_id, result)
        self._notify_engine_done(task, result)

    def _should_pause_for_urgent(self, current_task: dict) -> bool:
        """Check whether there's actually an urgent task pending that's
        higher priority than the current one. Prevents false interrupts.
        """
        cur_pri = current_task.get("priority_num", 2)
        row = db.fetch_one(
            """
            SELECT MIN(id) AS id
            FROM agent_tasks
            WHERE agent_id = %s AND status = 'queued' AND priority_num > %s
            """,
            (self.agent_id, cur_pri),
        )
        return bool(row and row.get("id"))

    # ------------------------------------------------------------------
    # Core LLM invocation
    # ------------------------------------------------------------------

    def _core_execute(self, task: dict, ctx: dict) -> dict:
        """Actually call the LLM for this task.

        Delegated to engine.execute_task so the worker stays dumb and the
        engine handles how to turn a task payload into an LLM call +
        what to do with the result (e.g. trigger next workflow node).
        """
        from . import engine
        return engine.execute_task(task, ctx)

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------

    def _notify_engine_done(self, task: dict, result: dict) -> None:
        try:
            from . import engine
            engine.on_task_complete(task, result)
        except Exception as e:
            log.exception("engine.on_task_complete raised: %s", e)

    def _notify_engine_aborted(self, task: dict, error: Exception) -> None:
        try:
            from . import engine
            engine.on_task_aborted(task, error)
        except Exception as e:
            log.exception("engine.on_task_aborted raised: %s", e)

    def _notify_engine_error(self, task: dict, error: Exception) -> None:
        try:
            from . import engine
            engine.on_task_failed(task, error)
        except Exception as e:
            log.exception("engine.on_task_failed raised: %s", e)
