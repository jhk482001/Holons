"""Schedule service — cron / interval triggers for workflows.

A background thread ticks every 60 seconds, checks the schedules table
for due entries, and dispatches runs via engine.dispatch_workflow.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import db, engine

log = logging.getLogger("agent_company.scheduler")

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

TICK_SECONDS = 30


def _tick() -> int:
    """Check for due schedules and dispatch them. Returns how many fired."""
    now = datetime.now(timezone.utc)
    due = db.fetch_all(
        """
        SELECT * FROM schedules
        WHERE enabled = TRUE
          AND (next_run_at IS NULL OR next_run_at <= %s)
        ORDER BY next_run_at NULLS FIRST
        """,
        (now,),
    )

    fired = 0
    for sched in due:
        try:
            run_id = engine.dispatch_workflow(
                workflow_id=sched["workflow_id"],
                user_id=sched["user_id"],
                initial_input=sched.get("default_input") or "",
                trigger_source="schedule",
                trigger_context={"schedule_id": sched["id"], "name": sched.get("name")},
                priority=sched.get("priority") or "normal",
            )
            log.info("scheduled run %s dispatched from schedule %s", run_id, sched["id"])
            fired += 1
        except Exception as e:
            log.exception("schedule %s failed to dispatch: %s", sched["id"], e)
            continue

        # Advance schedule
        next_at = _next_run_time(sched, now)
        if sched["trigger_type"] == "once":
            db.execute(
                "UPDATE schedules SET enabled = FALSE, last_run_at = %s WHERE id = %s",
                (now, sched["id"]),
            )
        else:
            db.execute(
                "UPDATE schedules SET next_run_at = %s, last_run_at = %s WHERE id = %s",
                (next_at, now, sched["id"]),
            )

    return fired


def _next_run_time(sched: dict, now: datetime) -> datetime:
    if sched["trigger_type"] == "interval" and sched.get("interval_seconds"):
        return now + timedelta(seconds=int(sched["interval_seconds"]))
    if sched["trigger_type"] == "cron" and sched.get("cron_expression"):
        try:
            from croniter import croniter  # type: ignore
            return croniter(sched["cron_expression"], now).get_next(datetime)
        except ImportError:
            log.warning("croniter not installed; falling back to 1 hour")
            return now + timedelta(hours=1)
    return now + timedelta(hours=1)


def _run_daily_project_reports() -> None:
    """Trigger one `project_reports.generate(...)` per active project that
    doesn't yet have a row dated today. Single-threaded, best-effort.
    """
    # Run at most once per tick regardless of how many projects — heavy LLM
    # path. Guard with an in-process cooldown.
    import time as _t
    global _LAST_REPORT_TICK_AT
    now_ts = _t.time()
    if (now_ts - _LAST_REPORT_TICK_AT) < 60 * 30:  # at most every 30 min
        return
    _LAST_REPORT_TICK_AT = now_ts

    rows = db.fetch_all(
        """
        SELECT p.id
        FROM projects p
        WHERE p.status = 'active'
          AND p.coordinator_agent_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM project_reports pr
              WHERE pr.project_id = p.id
                AND pr.report_date = CURRENT_DATE
          )
          -- Only generate a report after some activity (avoid reporting
          -- daily on dormant projects).
          AND EXISTS (
              SELECT 1 FROM run_steps rs
              WHERE rs.project_id = p.id
                AND rs.started_at >= NOW() - INTERVAL '1 day'
          )
        LIMIT 3
        """
    )
    if not rows:
        return
    from . import project_reports as _pr
    for r in rows:
        try:
            _pr.generate(r["id"])
            log.info("daily report generated for project %s", r["id"])
        except Exception as e:
            log.warning("report generation failed for project %s: %s", r["id"], e)


_LAST_REPORT_TICK_AT = 0.0


def _loop() -> None:
    log.info("scheduler starting (tick=%ss)", TICK_SECONDS)
    while not _stop_event.is_set():
        try:
            fired = _tick()
            if fired:
                log.info("scheduler tick fired %d runs", fired)
        except Exception as e:
            log.exception("scheduler tick error: %s", e)
        # Phase 5.1 — Lead proxy-answer check. Lazy-imported to avoid a
        # circular dependency between scheduler and the proxy service.
        try:
            from . import lead_proxy
            proxied = lead_proxy.tick()
            if proxied:
                log.info("lead_proxy tick answered %d pending msgs", proxied)
        except Exception as e:
            log.exception("lead_proxy tick error: %s", e)
        # Daily project reports — once per active project per day. Cheap to
        # check because the SELECT is indexed + UPSERT-style guard inside
        # project_reports.generate() short-circuits if today's row exists.
        try:
            _run_daily_project_reports()
        except Exception as e:
            log.exception("project report tick error: %s", e)

        # Stuck task recovery — reset tasks stuck in 'running' for > 10 min
        # (worker crash, OOM, etc.). These get re-queued for the next pick-up.
        try:
            result = db.execute(
                """
                UPDATE agent_tasks
                   SET status = 'queued'
                 WHERE status = 'running'
                   AND created_at < NOW() - INTERVAL '10 minutes'
                """
            )
            if result and result > 0:
                log.warning("stuck task recovery: reset %d stuck tasks to queued", result)
        except Exception as e:
            log.exception("stuck task recovery error: %s", e)

        # Log retention — delete entries older than 90 days (runs once per tick,
        # but DELETE with a date filter is cheap when there's nothing to delete)
        try:
            deleted_audit = db.execute(
                "DELETE FROM audit_log WHERE created_at < NOW() - INTERVAL '90 days'"
            )
            deleted_asset_audit = db.execute(
                "DELETE FROM asset_audit_log WHERE created_at < NOW() - INTERVAL '90 days'"
            )
            deleted_usage = db.execute(
                "DELETE FROM asset_usage_log WHERE called_at < NOW() - INTERVAL '90 days'"
            )
            total = (deleted_audit or 0) + (deleted_asset_audit or 0) + (deleted_usage or 0)
            if total > 0:
                log.info("log retention: cleaned %d old log rows (90-day policy)", total)
        except Exception as e:
            log.exception("log retention error: %s", e)

        _stop_event.wait(TICK_SECONDS)
    log.info("scheduler stopped")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()
    if _thread:
        _thread.join(timeout=5)


# ============================================================================
# CRUD for schedules (called by Flask routes)
# ============================================================================

def create_schedule(user_id: int, data: dict) -> int:
    now = datetime.now(timezone.utc)
    trigger_type = data.get("trigger_type", "interval")
    if trigger_type == "interval":
        interval = int(data.get("interval_seconds") or 3600)
        next_run = now + timedelta(seconds=interval)
    elif trigger_type == "cron":
        try:
            from croniter import croniter  # type: ignore
            next_run = croniter(data["cron_expression"], now).get_next(datetime)
        except Exception:
            next_run = now + timedelta(hours=1)
    else:
        next_run = now

    return db.execute_returning(
        """
        INSERT INTO schedules
            (user_id, workflow_id, name, trigger_type, cron_expression,
             interval_seconds, default_input, priority, next_run_at, enabled)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        (
            user_id,
            int(data["workflow_id"]),
            data.get("name"),
            trigger_type,
            data.get("cron_expression"),
            data.get("interval_seconds"),
            data.get("default_input"),
            data.get("priority") or "normal",
            next_run,
        ),
    )


def list_schedules(user_id: int) -> list[dict]:
    return db.fetch_all(
        "SELECT * FROM schedules WHERE user_id = %s ORDER BY enabled DESC, next_run_at NULLS LAST",
        (user_id,),
    )


def toggle_schedule(user_id: int, sched_id: int, enabled: bool) -> None:
    db.execute(
        "UPDATE schedules SET enabled = %s WHERE id = %s AND user_id = %s",
        (enabled, sched_id, user_id),
    )


def delete_schedule(user_id: int, sched_id: int) -> None:
    db.execute(
        "DELETE FROM schedules WHERE id = %s AND user_id = %s",
        (sched_id, user_id),
    )
