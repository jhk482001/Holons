"""SQLite-compatible schema for personal/standalone mode.

This is a translated version of schema.py's Postgres DDL. Key differences:
- BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT
- JSONB → TEXT (stored as JSON strings)
- TIMESTAMPTZ → TEXT (ISO 8601 strings)
- NOW() → datetime('now')
- No vector extension, pg_trgm, or CHECK constraints on enums
- No INTERVAL — use datetime() with modifiers
- No partial indexes (WHERE clause in CREATE INDEX)

The schema is intentionally simpler — personal mode doesn't need the full
enterprise feature set (e.g., pgvector RAG, row-level locking).
"""
from __future__ import annotations

SQLITE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS as_users (
        id                         INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id                  INTEGER,
        username                   TEXT UNIQUE NOT NULL,
        password_hash              TEXT NOT NULL,
        display_name               TEXT,
        role                       TEXT DEFAULT 'admin',
        default_lead_agent_id      INTEGER,
        max_total_queue_depth      INTEGER DEFAULT 5000,
        escalation_policy          TEXT DEFAULT 'lead_first',
        escalation_timeout_seconds INTEGER DEFAULT 600,
        cast_order                 TEXT DEFAULT '[]',
        last_cast_filter           TEXT DEFAULT '{"scope":"all","status":"all"}',
        notification_prefs         TEXT DEFAULT '{}',
        lead_proxy_enabled         INTEGER DEFAULT 1,
        lead_proxy_timeout_minutes INTEGER DEFAULT 10,
        lead_proxy_away_minutes    INTEGER DEFAULT 5,
        last_seen_at               TEXT,
        language                   TEXT DEFAULT 'en',
        lead_max_steps             INTEGER DEFAULT 10,
        lead_max_tokens            INTEGER DEFAULT 50000,
        cast_layout                TEXT DEFAULT '{}',
        created_at                 TEXT DEFAULT (datetime('now')),
        updated_at                 TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agents (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id            INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        owner_user_id      INTEGER REFERENCES as_users(id),
        name               TEXT NOT NULL,
        role_title         TEXT,
        description        TEXT,
        system_prompt      TEXT,
        few_shot           TEXT,
        avatar_config      TEXT DEFAULT '{}',
        is_lead            INTEGER DEFAULT 0,
        primary_model_id   TEXT,
        fallback_model_id  TEXT,
        model_client_id    INTEGER REFERENCES model_clients(id) ON DELETE SET NULL,
        concurrency        INTEGER DEFAULT 1,
        max_queue_depth    INTEGER DEFAULT 1440,
        status             TEXT DEFAULT 'active',
        working_hours      TEXT,
        timezone           TEXT DEFAULT 'Asia/Taipei',
        visibility         TEXT DEFAULT 'private',
        visible_user_ids   TEXT DEFAULT '[]',
        is_shareable       INTEGER DEFAULT 0,
        external_origin    TEXT,
        tool_config        TEXT DEFAULT '[]',
        created_at         TEXT DEFAULT (datetime('now')),
        updated_at         TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS groups_tbl (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        description   TEXT,
        mode          TEXT DEFAULT 'parallel',
        aggregator_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        is_ephemeral  INTEGER DEFAULT 0,
        created_at    TEXT DEFAULT (datetime('now'))
    )
    """,
    # Migration for existing DBs (errors are swallowed by create_all_sqlite).
    "ALTER TABLE groups_tbl ADD COLUMN is_ephemeral INTEGER DEFAULT 0",
    """
    CREATE TABLE IF NOT EXISTS group_chat_threads (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        group_id   INTEGER NOT NULL REFERENCES groups_tbl(id) ON DELETE CASCADE,
        status     TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_chat_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id  INTEGER NOT NULL REFERENCES group_chat_threads(id) ON DELETE CASCADE,
        role       TEXT NOT NULL,
        agent_id   INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        content    TEXT NOT NULL,
        metadata   TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_group_chat_threads_user ON group_chat_threads(user_id, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_group_chat_msgs_thread ON group_chat_messages(thread_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS group_members (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id      INTEGER NOT NULL REFERENCES groups_tbl(id) ON DELETE CASCADE,
        agent_id      INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        position      INTEGER DEFAULT 0,
        custom_prompt TEXT
    )
    """,
    # ---------- projects ----------
    """
    CREATE TABLE IF NOT EXISTS projects (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id              INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name                 TEXT NOT NULL,
        description          TEXT,
        status               TEXT DEFAULT 'active',
        coordinator_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        goal                 TEXT,
        created_at           TEXT DEFAULT (datetime('now')),
        updated_at           TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_members (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id          INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        daily_alloc_pct   REAL DEFAULT 100.0,
        monthly_alloc_pct REAL DEFAULT 100.0,
        created_at        TEXT DEFAULT (datetime('now')),
        UNIQUE(project_id, agent_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_milestones (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        position    INTEGER NOT NULL DEFAULT 0,
        title       TEXT NOT NULL,
        description TEXT,
        status      TEXT DEFAULT 'pending',
        due_date    TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_reports (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id           INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        report_date          TEXT NOT NULL,
        coordinator_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        summary_md           TEXT,
        metrics              TEXT DEFAULT '{}',
        created_at           TEXT DEFAULT (datetime('now')),
        UNIQUE(project_id, report_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_projects_user_status ON projects(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_members_agent ON project_members(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_milestones_project ON project_milestones(project_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_project_reports_project ON project_reports(project_id, report_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS project_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        actor      TEXT,
        event_type TEXT NOT NULL,
        payload    TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_events_project ON project_events(project_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS project_artifacts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id   INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        source     TEXT NOT NULL DEFAULT 'lead_message',
        source_ref INTEGER,
        kind       TEXT NOT NULL,
        title      TEXT,
        payload    TEXT NOT NULL DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_artifacts_project ON project_artifacts(project_id, id DESC)",
    # Attribution columns on existing tables (SQLite swallows duplicate-column errors).
    "ALTER TABLE workflows ADD COLUMN max_review_iterations INTEGER DEFAULT 2",
    # Source attribution: 'manual' / 'lead_generated' / 'imported'. Matches
    # the Postgres CHECK constraint, just without enforcement on SQLite.
    "ALTER TABLE workflows ADD COLUMN source TEXT DEFAULT 'manual'",
    "ALTER TABLE workflows ADD COLUMN loop_prompt TEXT",
    "ALTER TABLE workflows ADD COLUMN parent_workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL",
    "ALTER TABLE runs ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE run_steps ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_run_steps_project_date ON run_steps(project_id, started_at)",
    # Per-agent quota columns. NULL = no cap.
    "ALTER TABLE agents ADD COLUMN daily_token_quota INTEGER",
    "ALTER TABLE agents ADD COLUMN daily_cost_quota REAL",
    "ALTER TABLE agents ADD COLUMN monthly_token_quota INTEGER",
    "ALTER TABLE agents ADD COLUMN monthly_cost_quota REAL",
    # User-level auto-topup policy.
    "ALTER TABLE as_users ADD COLUMN auto_topup_enabled INTEGER DEFAULT 0",
    "ALTER TABLE as_users ADD COLUMN auto_topup_per_topup_cost REAL DEFAULT 1.0",
    "ALTER TABLE as_users ADD COLUMN auto_topup_max_per_day INTEGER DEFAULT 3",
    "ALTER TABLE as_users ADD COLUMN report_webhook_url TEXT",
    # --- Schema drift backfill (2026-04-24) --------------------------------
    # These columns were added to the Postgres schema in backend/schema.py
    # but the SQLite mirror below this line was never updated. Each ALTER
    # is idempotent via create_all_sqlite's try/except. Re-ordering matters
    # only when FK references another column added in the same pass — none
    # of these do.
    "ALTER TABLE as_users ADD COLUMN enable_code_execution INTEGER DEFAULT 0",
    "ALTER TABLE as_users ADD COLUMN skills_auto_approve INTEGER DEFAULT 1",
    "ALTER TABLE lead_conversations ADD COLUMN source_platform TEXT",
    "ALTER TABLE lead_conversations ADD COLUMN source_external_id TEXT",
    "ALTER TABLE model_clients ADD COLUMN last_test_at TEXT",
    "ALTER TABLE model_clients ADD COLUMN last_test_status TEXT",
    "ALTER TABLE model_clients ADD COLUMN last_test_message TEXT",
    "ALTER TABLE schedules ADD COLUMN project_id INTEGER",
    "ALTER TABLE agent_skills ADD COLUMN extraction_model_id TEXT",
    "ALTER TABLE agent_skills ADD COLUMN extraction_input_tokens INTEGER",
    "ALTER TABLE agent_skills ADD COLUMN extraction_output_tokens INTEGER",
    "ALTER TABLE agent_skills ADD COLUMN extraction_cost_usd REAL",
    "ALTER TABLE agent_skills ADD COLUMN extraction_prompt_preview TEXT",
    "ALTER TABLE agent_skills ADD COLUMN extraction_response_preview TEXT",
    "ALTER TABLE agent_skills ADD COLUMN extraction_at TEXT",
    "ALTER TABLE agent_skills ADD COLUMN last_used_at TEXT",
    "ALTER TABLE workflow_nodes ADD COLUMN input_bindings TEXT DEFAULT '[]'",
    "ALTER TABLE agent_tasks ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE runs ADD COLUMN workspace_id INTEGER",
    """
    CREATE TABLE IF NOT EXISTS api_tokens (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name         TEXT,
        token_hash   TEXT UNIQUE NOT NULL,
        last_used_at TEXT,
        created_at   TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id)",
    """
    CREATE TABLE IF NOT EXISTS auto_topup_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        amount_cost_usd REAL NOT NULL,
        event_date      TEXT NOT NULL,
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auto_topup_user_date ON auto_topup_events(user_id, event_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS workflows (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                 INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name                    TEXT NOT NULL,
        description             TEXT,
        is_template             INTEGER DEFAULT 0,
        is_draft                INTEGER DEFAULT 0,
        loop_enabled            INTEGER DEFAULT 0,
        max_loops               INTEGER DEFAULT 1,
        max_review_iterations   INTEGER DEFAULT 2,
        loop_prompt             TEXT,
        source                  TEXT DEFAULT 'manual',
        parent_workflow_id      INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
        created_at              TEXT DEFAULT (datetime('now')),
        updated_at              TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_nodes (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id            INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        position               INTEGER NOT NULL DEFAULT 0,
        node_type              TEXT,
        agent_id               INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        group_id               INTEGER REFERENCES groups_tbl(id) ON DELETE SET NULL,
        parent_group_id        INTEGER REFERENCES groups_tbl(id) ON DELETE SET NULL,
        prompt_template        TEXT,
        system_prompt_override TEXT,
        label                  TEXT,
        pos_x                  INTEGER DEFAULT 0,
        pos_y                  INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id           INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
        user_id               INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        initial_input         TEXT,
        final_output          TEXT,
        status                TEXT DEFAULT 'queued',
        started_at            TEXT DEFAULT (datetime('now')),
        finished_at           TEXT,
        total_input_tokens    INTEGER DEFAULT 0,
        total_output_tokens   INTEGER DEFAULT 0,
        total_cost_usd        REAL DEFAULT 0,
        total_duration_ms     INTEGER DEFAULT 0,
        iterations            INTEGER DEFAULT 1,
        error_message         TEXT,
        trigger_source        TEXT DEFAULT 'manual',
        trigger_context       TEXT DEFAULT '{}',
        project_id            INTEGER REFERENCES projects(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_steps (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        iteration       INTEGER DEFAULT 1,
        node_position   INTEGER,
        group_id        INTEGER,
        agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
        role_label      TEXT,
        prompt           TEXT,
        system_prompt   TEXT,
        response        TEXT,
        model_id        TEXT,
        model_provider  TEXT,
        input_tokens    INTEGER DEFAULT 0,
        output_tokens   INTEGER DEFAULT 0,
        cost_usd        REAL DEFAULT 0,
        duration_ms     INTEGER DEFAULT 0,
        error           TEXT,
        tool_calls      TEXT DEFAULT '[]',
        turn            INTEGER DEFAULT 0,
        started_at      TEXT DEFAULT (datetime('now')),
        finished_at     TEXT,
        project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id           INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        run_id             INTEGER REFERENCES runs(id) ON DELETE CASCADE,
        step_id            INTEGER REFERENCES run_steps(id) ON DELETE SET NULL,
        parent_task_id     INTEGER REFERENCES agent_tasks(id) ON DELETE SET NULL,
        task_type          TEXT DEFAULT 'workflow_step',
        priority           TEXT DEFAULT 'normal',
        priority_num       INTEGER DEFAULT 2,
        status             TEXT DEFAULT 'queued',
        payload            TEXT NOT NULL,
        result             TEXT,
        error_message      TEXT,
        progress_snapshot  TEXT,
        source             TEXT,
        started_at         TEXT,
        finished_at        TEXT,
        created_at         TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chats (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        role       TEXT NOT NULL,
        content    TEXT,
        metadata   TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lead_conversations (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        agent_id   INTEGER REFERENCES agents(id) ON DELETE CASCADE,
        thread_id  TEXT UNIQUE NOT NULL,
        title      TEXT,
        status     TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lead_messages (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id                   TEXT NOT NULL,
        role                        TEXT NOT NULL,
        content                     TEXT,
        metadata                    TEXT DEFAULT '{}',
        cancelled                   INTEGER DEFAULT 0,
        proposed_workflow_id        INTEGER,
        pending_decision_expires_at TEXT,
        created_at                  TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        title      TEXT NOT NULL,
        body       TEXT,
        severity   TEXT DEFAULT 'info',
        category   TEXT,
        link       TEXT,
        status     TEXT DEFAULT 'unread',
        metadata   TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id            INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        workflow_id        INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        name               TEXT,
        trigger_type       TEXT DEFAULT 'cron',
        cron_expression    TEXT,
        interval_seconds   INTEGER,
        default_input      TEXT,
        enabled            INTEGER DEFAULT 1,
        next_run_at        TEXT,
        last_run_at        TEXT,
        created_at         TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_skills (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id          INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        name              TEXT NOT NULL,
        slug              TEXT,
        description       TEXT,
        content_md        TEXT,
        source            TEXT,
        source_run_ids    TEXT DEFAULT '[]',
        confidence        REAL DEFAULT 0.5,
        approved_by_user  INTEGER DEFAULT 0,
        times_used        INTEGER DEFAULT 0,
        created_at        TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_quotas (
        id                         INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id                   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        name                       TEXT,
        window_type                TEXT DEFAULT 'monthly',
        window_start               TEXT,
        window_end                 TEXT,
        max_tokens                 INTEGER,
        max_tpm                    INTEGER,
        max_rpm                    INTEGER,
        max_cost_usd               REAL,
        current_tokens             INTEGER DEFAULT 0,
        current_cost_usd           REAL DEFAULT 0,
        current_window_started_at  TEXT,
        hard_limit                 INTEGER DEFAULT 1,
        enabled                    INTEGER DEFAULT 1,
        created_at                 TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_shares (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id           INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        owner_user_id      INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        borrower_user_id   INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        scope              TEXT DEFAULT 'invoke',
        price_per_call_usd REAL DEFAULT 0,
        max_calls_per_day  INTEGER,
        expires_at         TEXT,
        created_at         TEXT DEFAULT (datetime('now')),
        revoked_at         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_escalations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id     INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        run_id       INTEGER REFERENCES runs(id),
        task_id      INTEGER,
        user_id      INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        kind         TEXT DEFAULT 'uncertain',
        summary      TEXT,
        context_data TEXT DEFAULT '{}',
        status       TEXT DEFAULT 'pending',
        resolution   TEXT,
        resolved_by  INTEGER,
        created_at   TEXT DEFAULT (datetime('now')),
        resolved_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER,
        username       TEXT,
        method         TEXT,
        path           TEXT,
        status_code    INTEGER,
        resource_id    TEXT,
        detail         TEXT DEFAULT '{}',
        created_at     TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_feature_flags (
        feature      TEXT PRIMARY KEY,
        label        TEXT,
        description  TEXT,
        admin_only   INTEGER DEFAULT 0,
        value        TEXT,
        updated_at   TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_quotas (
        user_id                 INTEGER PRIMARY KEY REFERENCES as_users(id) ON DELETE CASCADE,
        daily_token_limit       INTEGER,
        daily_cost_limit_usd    REAL,
        monthly_token_limit     INTEGER,
        monthly_cost_limit_usd  REAL,
        updated_at              TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_items (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        kind                 TEXT NOT NULL,
        name                 TEXT NOT NULL,
        description          TEXT,
        owner_user_id        INTEGER REFERENCES as_users(id),
        enabled              INTEGER DEFAULT 1,
        config               TEXT DEFAULT '{}',
        credential_encrypted TEXT,
        metadata             TEXT DEFAULT '{}',
        created_at           TEXT DEFAULT (datetime('now')),
        updated_at           TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_grants (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id          INTEGER NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        grantee_user_id   INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        granted_by        INTEGER REFERENCES as_users(id),
        created_at        TEXT DEFAULT (datetime('now')),
        UNIQUE(asset_id, grantee_user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_assets (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id  INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        asset_id  INTEGER NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        enabled   INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(agent_id, asset_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_audit_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id        INTEGER NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        actor_user_id   INTEGER,
        action          TEXT NOT NULL,
        before_state    TEXT,
        after_state     TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_usage_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id    INTEGER NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        user_id     INTEGER,
        agent_id    INTEGER,
        run_id      INTEGER,
        called_at   TEXT DEFAULT (datetime('now')),
        duration_ms INTEGER,
        ok          INTEGER DEFAULT 1,
        error       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_clients (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        name                  TEXT NOT NULL,
        kind                  TEXT NOT NULL,
        description           TEXT,
        config                TEXT DEFAULT '{}',
        credential_encrypted  TEXT,
        enabled               INTEGER DEFAULT 1,
        default_for_new_users INTEGER DEFAULT 0,
        created_by            INTEGER REFERENCES as_users(id) ON DELETE SET NULL,
        created_at            TEXT DEFAULT (datetime('now')),
        updated_at            TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_client_grants (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id    INTEGER NOT NULL REFERENCES model_clients(id) ON DELETE CASCADE,
        user_id      INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        granted_by   INTEGER REFERENCES as_users(id) ON DELETE SET NULL,
        created_at   TEXT DEFAULT (datetime('now')),
        UNIQUE(client_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        token       TEXT UNIQUE NOT NULL,
        expires_at  TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now'))
    )
    """,
    # IM (Telegram / Slack / LINE) bindings — personal mode supports these
    # even though most users never wire them up. Without this table the
    # sidecar crashes at startup in im_channels.start_all().
    """
    CREATE TABLE IF NOT EXISTS im_bindings (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id            INTEGER NOT NULL,
        platform           TEXT NOT NULL,
        external_id        TEXT,
        display_name       TEXT,
        enabled            INTEGER DEFAULT 1,
        secret_encrypted   TEXT,
        metadata           TEXT DEFAULT '{}',
        transport          TEXT DEFAULT 'polling',
        created_at         TEXT DEFAULT (datetime('now')),
        updated_at         TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, platform)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_im_bindings_platform_ext ON im_bindings(platform, external_id)",

    # Phase 6 — unified LLM call ledger (mirror of Postgres llm_calls).
    # See backend/schema.py for the authoritative comment on `kind`.
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        agent_id        INTEGER,
        run_id          INTEGER,
        thread_id       TEXT,
        model_client_id INTEGER,
        model_id        TEXT,
        provider        TEXT,
        kind            TEXT NOT NULL,
        input_tokens    INTEGER DEFAULT 0,
        output_tokens   INTEGER DEFAULT 0,
        cost_usd        REAL DEFAULT 0,
        duration_ms    INTEGER,
        error           TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_user_created ON llm_calls(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_kind_created ON llm_calls(kind, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_agent_created ON llm_calls(agent_id, created_at DESC)",

    # Per-user default model client for non-agent LLM paths.
    "ALTER TABLE as_users ADD COLUMN default_model_client_id INTEGER",

    # Soft-warning thresholds on user_quotas (80% default).
    "ALTER TABLE user_quotas ADD COLUMN daily_warn_pct INTEGER DEFAULT 80",
    "ALTER TABLE user_quotas ADD COLUMN monthly_warn_pct INTEGER DEFAULT 80",

    # Indexes (subset — SQLite doesn't need as many)
    "CREATE INDEX IF NOT EXISTS idx_agents_user ON agents(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_queue ON agent_tasks(agent_id, status, priority_num DESC, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_lead_msgs_thread ON lead_messages(thread_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_asset_items_kind ON asset_items(kind, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_desktop_sessions_token ON desktop_sessions(token)",
]


def create_all_sqlite(conn) -> None:
    """Execute all SQLite DDL statements."""
    for stmt in SQLITE_DDL:
        try:
            conn.execute(stmt)
        except Exception as e:
            # Skip errors on "already exists" etc.
            import logging
            logging.getLogger("agent_company.schema_sqlite").debug(
                "SQLite DDL skip: %s (%s)", str(e)[:80], stmt[:60]
            )
    conn.commit()
    # Seed feature flags
    from .services import feature_flags
    feature_flags.seed_flags()
    # Seed assets
    from .services import asset_seeds
    asset_seeds.seed_default_assets()
    # Seed model clients
    from .services import model_clients
    model_clients.seed_default_client_and_backfill()
