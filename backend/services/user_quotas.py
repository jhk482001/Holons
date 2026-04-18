"""Per-user quota service.

Quotas gate workflow dispatch: the engine calls `check_dispatch(uid)`
before enqueuing a run, and if the user has already spent beyond their
daily or monthly budget the dispatch is refused.

Spend is read live from run_steps so there's no separate counter to keep
in sync. This is slower than a cached counter but correct-by-construction
and matches how the dashboard summary is computed.

Shape of the user_quotas row::

    {
        user_id: int,
        daily_token_limit:      int | None,
        daily_cost_limit_usd:   float | None,
        monthly_token_limit:    int | None,
        monthly_cost_limit_usd: float | None,
    }

Any None field means "no limit for that axis". A completely missing row
means "no limits at all".
"""
from __future__ import annotations


from .. import db


class QuotaExceeded(Exception):
    def __init__(self, message: str, spent: dict, limits: dict):
        super().__init__(message)
        self.spent = spent
        self.limits = limits


def get_quota(user_id: int) -> dict:
    row = db.fetch_one(
        """
        SELECT user_id, daily_token_limit,
               daily_cost_limit_usd::float AS daily_cost_limit_usd,
               monthly_token_limit,
               monthly_cost_limit_usd::float AS monthly_cost_limit_usd,
               updated_at
        FROM user_quotas WHERE user_id = %s
        """,
        (user_id,),
    )
    if row:
        return row
    return {
        "user_id": user_id,
        "daily_token_limit": None,
        "daily_cost_limit_usd": None,
        "monthly_token_limit": None,
        "monthly_cost_limit_usd": None,
    }


def set_quota(user_id: int, data: dict) -> dict:
    """Upsert the user's quota row. Any field missing from `data` is
    preserved (not cleared) — pass an explicit None to clear a limit."""
    cur = get_quota(user_id)
    merged = {**cur, **{k: data[k] for k in data if k != "user_id"}}
    db.execute(
        """
        INSERT INTO user_quotas
          (user_id, daily_token_limit, daily_cost_limit_usd,
           monthly_token_limit, monthly_cost_limit_usd, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
          daily_token_limit      = EXCLUDED.daily_token_limit,
          daily_cost_limit_usd   = EXCLUDED.daily_cost_limit_usd,
          monthly_token_limit    = EXCLUDED.monthly_token_limit,
          monthly_cost_limit_usd = EXCLUDED.monthly_cost_limit_usd,
          updated_at             = NOW()
        """,
        (
            user_id,
            merged.get("daily_token_limit"),
            merged.get("daily_cost_limit_usd"),
            merged.get("monthly_token_limit"),
            merged.get("monthly_cost_limit_usd"),
        ),
    )
    return get_quota(user_id)


def spend_today(user_id: int) -> dict:
    """Sum of tokens + cost for this user across all runs in the last
    24 hours."""
    row = db.fetch_one(
        """
        SELECT
            COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
            COALESCE(SUM(cost_usd)::float, 0.0) AS cost_usd
        FROM run_steps s
        JOIN runs r ON r.id = s.run_id
        WHERE r.user_id = %s
          AND s.started_at >= NOW() - INTERVAL '1 day'
        """,
        (user_id,),
    ) or {}
    return {
        "tokens": int(row.get("tokens") or 0),
        "cost_usd": float(row.get("cost_usd") or 0),
    }


def spend_this_month(user_id: int) -> dict:
    row = db.fetch_one(
        """
        SELECT
            COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
            COALESCE(SUM(cost_usd)::float, 0.0) AS cost_usd
        FROM run_steps s
        JOIN runs r ON r.id = s.run_id
        WHERE r.user_id = %s
          AND s.started_at >= date_trunc('month', NOW())
        """,
        (user_id,),
    ) or {}
    return {
        "tokens": int(row.get("tokens") or 0),
        "cost_usd": float(row.get("cost_usd") or 0),
    }


def check_dispatch(user_id: int) -> None:
    """Raise QuotaExceeded if the user is already over one of their
    configured limits. Called by engine.dispatch_workflow before the
    run is created."""
    q = get_quota(user_id)
    daily = spend_today(user_id)
    monthly = spend_this_month(user_id)

    def _over(limit, value):
        return limit is not None and value >= float(limit)

    if _over(q.get("daily_token_limit"), daily["tokens"]):
        raise QuotaExceeded(
            "daily token limit reached",
            {"daily": daily, "monthly": monthly},
            q,
        )
    if _over(q.get("daily_cost_limit_usd"), daily["cost_usd"]):
        raise QuotaExceeded(
            "daily cost limit reached",
            {"daily": daily, "monthly": monthly},
            q,
        )
    if _over(q.get("monthly_token_limit"), monthly["tokens"]):
        raise QuotaExceeded(
            "monthly token limit reached",
            {"daily": daily, "monthly": monthly},
            q,
        )
    if _over(q.get("monthly_cost_limit_usd"), monthly["cost_usd"]):
        raise QuotaExceeded(
            "monthly cost limit reached",
            {"daily": daily, "monthly": monthly},
            q,
        )


def summary(user_id: int) -> dict:
    return {
        "quota": get_quota(user_id),
        "daily": spend_today(user_id),
        "monthly": spend_this_month(user_id),
    }
