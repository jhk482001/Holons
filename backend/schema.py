"""PostgreSQL schema for agent_company v2.

All tables are created idempotently on app startup via `create_all()`.
Uses pgvector extension (enabled by docker/init-db.sql) for future RAG.
"""
from __future__ import annotations

# ============================================================================
# DDL — one statement per list item; executed in order on startup
# ============================================================================

DDL: list[str] = [

    # ---------- extensions ----------
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",

    # ---------- as_users ----------
    """
    CREATE TABLE IF NOT EXISTS as_users (
        id                         BIGSERIAL PRIMARY KEY,
        tenant_id                  BIGINT,
        username                   VARCHAR(100) NOT NULL UNIQUE,
        display_name               VARCHAR(200),
        password_hash              VARCHAR(300) NOT NULL,

        default_lead_agent_id      BIGINT,
        max_total_queue_depth      INT DEFAULT 5000,
        escalation_policy          TEXT DEFAULT 'lead_first'
                                     CHECK (escalation_policy IN ('autonomous','lead_first','always_user')),
        escalation_timeout_seconds INT DEFAULT 600,
        cast_order                 JSONB DEFAULT '[]'::jsonb,
        last_cast_filter           JSONB DEFAULT '{"scope":"all","status":"all"}'::jsonb,
        notification_prefs         JSONB DEFAULT '{}'::jsonb,

        created_at                 TIMESTAMPTZ DEFAULT NOW(),
        updated_at                 TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ---------- agents ----------
    """
    CREATE TABLE IF NOT EXISTS agents (
        id                  BIGSERIAL PRIMARY KEY,
        user_id             BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        owner_user_id       BIGINT REFERENCES as_users(id),

        name                VARCHAR(200) NOT NULL,
        role_title          VARCHAR(200),
        description         TEXT,
        system_prompt       TEXT,
        few_shot            TEXT,
        avatar_config       JSONB DEFAULT '{}'::jsonb,

        is_lead             BOOLEAN DEFAULT FALSE,

        primary_model_id    VARCHAR(100),
        fallback_model_id   VARCHAR(100),

        concurrency         INT DEFAULT 1,
        max_queue_depth     INT DEFAULT 1440,

        status              TEXT DEFAULT 'active'
                              CHECK (status IN ('active','paused','offline','off_duty','budget_exceeded','quota_exceeded')),

        working_hours       JSONB,
        timezone            VARCHAR(64) DEFAULT 'Asia/Taipei',

        visibility          TEXT DEFAULT 'private'
                              CHECK (visibility IN ('private','user_list','org_wide')),
        visible_user_ids    JSONB DEFAULT '[]'::jsonb,
        is_shareable        BOOLEAN DEFAULT FALSE,
        external_origin     VARCHAR(300),

        created_at          TIMESTAMPTZ DEFAULT NOW(),
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_user ON agents(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)",

    # ---------- groups_tbl ----------
    """
    CREATE TABLE IF NOT EXISTS groups_tbl (
        id                   BIGSERIAL PRIMARY KEY,
        user_id              BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name                 VARCHAR(200) NOT NULL,
        description          TEXT,
        mode                 TEXT DEFAULT 'parallel' CHECK (mode IN ('parallel','sequential')),
        aggregator_agent_id  BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        is_ephemeral         BOOLEAN DEFAULT FALSE,
        created_at           TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_chat_threads (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        group_id    BIGINT NOT NULL REFERENCES groups_tbl(id) ON DELETE CASCADE,
        status      TEXT DEFAULT 'active',
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_chat_messages (
        id          BIGSERIAL PRIMARY KEY,
        thread_id   BIGINT NOT NULL REFERENCES group_chat_threads(id) ON DELETE CASCADE,
        role        TEXT NOT NULL,
        agent_id    BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        content     TEXT NOT NULL,
        metadata    JSONB DEFAULT '{}'::jsonb,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_group_chat_threads_user ON group_chat_threads(user_id, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_group_chat_msgs_thread ON group_chat_messages(thread_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS group_members (
        id             BIGSERIAL PRIMARY KEY,
        group_id       BIGINT NOT NULL REFERENCES groups_tbl(id) ON DELETE CASCADE,
        agent_id       BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        position       INT NOT NULL DEFAULT 0,
        custom_prompt  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id, position)",

    # ---------- projects ----------
    # Projects are long-lived goals that wrap runs, agents, and a coordinator.
    # Every run / run_step may optionally be attributed to a project; adhoc
    # chats leave project_id NULL.
    """
    CREATE TABLE IF NOT EXISTS projects (
        id                    BIGSERIAL PRIMARY KEY,
        user_id               BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name                  VARCHAR(200) NOT NULL,
        description           TEXT,
        status                TEXT DEFAULT 'active'
                                CHECK (status IN ('active','paused','done','archived')),
        coordinator_agent_id  BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        goal                  TEXT,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        updated_at            TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_projects_user_status ON projects(user_id, status)",
    """
    CREATE TABLE IF NOT EXISTS project_members (
        id                 BIGSERIAL PRIMARY KEY,
        project_id         BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id           BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        daily_alloc_pct    REAL DEFAULT 100.0,
        monthly_alloc_pct  REAL DEFAULT 100.0,
        created_at         TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(project_id, agent_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_members_agent ON project_members(agent_id)",
    """
    CREATE TABLE IF NOT EXISTS project_milestones (
        id          BIGSERIAL PRIMARY KEY,
        project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        position    INT NOT NULL DEFAULT 0,
        title       VARCHAR(200) NOT NULL,
        description TEXT,
        status      TEXT DEFAULT 'pending'
                      CHECK (status IN ('pending','in_progress','done')),
        due_date    DATE,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_milestones_project ON project_milestones(project_id, position)",
    """
    CREATE TABLE IF NOT EXISTS project_reports (
        id                    BIGSERIAL PRIMARY KEY,
        project_id            BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        report_date           DATE NOT NULL,
        coordinator_agent_id  BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        summary_md            TEXT,
        metrics               JSONB DEFAULT '{}'::jsonb,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(project_id, report_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_reports_project ON project_reports(project_id, report_date DESC)",

    # Activity log — paused, resumed, milestone hit, allocation change, …
    """
    CREATE TABLE IF NOT EXISTS project_events (
        id           BIGSERIAL PRIMARY KEY,
        project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        actor        VARCHAR(100),
        event_type   VARCHAR(40) NOT NULL,
        payload      JSONB DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_events_project ON project_events(project_id, created_at DESC)",

    # Artifacts produced by agents inside a project — html prototypes, slide
    # decks, files, markdown docs. Populated by the project coordinator
    # (lead_agent.chat with project_id) whenever the agent emits an
    # artifact-<kind> fence. `payload` is the raw {kind, …} dict that the
    # frontend ArtifactBubble component already understands.
    """
    CREATE TABLE IF NOT EXISTS project_artifacts (
        id           BIGSERIAL PRIMARY KEY,
        project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id     BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        source       VARCHAR(30) NOT NULL DEFAULT 'lead_message',
        source_ref   BIGINT,
        kind         VARCHAR(20) NOT NULL,
        title        TEXT,
        payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_artifacts_project ON project_artifacts(project_id, id DESC)",

    # ---------- workflows ----------
    """
    CREATE TABLE IF NOT EXISTS workflows (
        id                  BIGSERIAL PRIMARY KEY,
        user_id             BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name                VARCHAR(200) NOT NULL,
        description         TEXT,
        loop_enabled        BOOLEAN DEFAULT FALSE,
        max_loops           INT DEFAULT 1,
        loop_prompt         TEXT,
        source              TEXT DEFAULT 'manual'
                              CHECK (source IN ('manual','lead_generated','imported')),
        is_draft            BOOLEAN DEFAULT FALSE,
        is_template         BOOLEAN DEFAULT FALSE,
        parent_workflow_id  BIGINT REFERENCES workflows(id) ON DELETE SET NULL,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_workflows_user ON workflows(user_id)",
    """
    CREATE TABLE IF NOT EXISTS workflow_nodes (
        id                BIGSERIAL PRIMARY KEY,
        workflow_id       BIGINT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        position          INT NOT NULL DEFAULT 0,
        node_type         TEXT CHECK (node_type IN ('agent','group')),
        agent_id          BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        group_id          BIGINT REFERENCES groups_tbl(id) ON DELETE SET NULL,
        parent_group_id   BIGINT REFERENCES groups_tbl(id) ON DELETE SET NULL,
        prompt_template   TEXT,
        label             VARCHAR(200),
        pos_x             INT DEFAULT 0,
        pos_y             INT DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wf_nodes_wf ON workflow_nodes(workflow_id, position)",

    # Phase 8: allow workflow nodes to temporarily override the agent's
    # system_prompt for cross-domain task assignment.
    "ALTER TABLE workflow_nodes ADD COLUMN IF NOT EXISTS system_prompt_override TEXT",

    # ---------- runs ----------
    """
    CREATE TABLE IF NOT EXISTS runs (
        id                    BIGSERIAL PRIMARY KEY,
        workflow_id           BIGINT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        user_id               BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        initial_input         TEXT,
        final_output          TEXT,
        status                TEXT DEFAULT 'running'
                                CHECK (status IN ('queued','running','done','error','cancelling','cancelled','paused')),
        started_at            TIMESTAMPTZ DEFAULT NOW(),
        finished_at           TIMESTAMPTZ,
        total_input_tokens    BIGINT DEFAULT 0,
        total_output_tokens   BIGINT DEFAULT 0,
        total_cost_usd        NUMERIC(12,6) DEFAULT 0,
        total_duration_ms     BIGINT DEFAULT 0,
        iterations            INT DEFAULT 1,
        error_message         TEXT,
        trigger_source        TEXT DEFAULT 'manual'
                                CHECK (trigger_source IN ('manual','chat','schedule','lead_agent','api')),
        trigger_context       JSONB DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_user_status ON runs(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_wf ON runs(workflow_id)",

    # ---------- run_steps ----------
    """
    CREATE TABLE IF NOT EXISTS run_steps (
        id              BIGSERIAL PRIMARY KEY,
        run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        parent_step_id  BIGINT REFERENCES run_steps(id) ON DELETE SET NULL,
        iteration       INT DEFAULT 1,
        node_position   INT,
        group_id        BIGINT,
        agent_id        BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        role_label      VARCHAR(200),
        prompt          TEXT,
        system_prompt   TEXT,
        response        TEXT,
        model_id        VARCHAR(100),
        model_provider  VARCHAR(50),
        input_tokens    INT DEFAULT 0,
        output_tokens   INT DEFAULT 0,
        cost_usd        NUMERIC(10,6) DEFAULT 0,
        duration_ms     INT DEFAULT 0,
        started_at      TIMESTAMPTZ DEFAULT NOW(),
        error           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_steps_run ON run_steps(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_steps_agent ON run_steps(agent_id)",

    # ---------- agent_tasks (THE QUEUE) ----------
    """
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id                 BIGSERIAL PRIMARY KEY,
        agent_id           BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        run_id             BIGINT REFERENCES runs(id) ON DELETE CASCADE,
        step_id            BIGINT REFERENCES run_steps(id) ON DELETE SET NULL,
        parent_task_id     BIGINT REFERENCES agent_tasks(id) ON DELETE SET NULL,

        task_type          TEXT DEFAULT 'workflow_step'
                             CHECK (task_type IN ('workflow_step','direct_chat','peer_consult','lead_invoke')),

        priority           TEXT DEFAULT 'normal'
                             CHECK (priority IN ('low','normal','high','critical','urgent')),
        priority_num       SMALLINT DEFAULT 2,

        status             TEXT DEFAULT 'queued'
                             CHECK (status IN ('queued','running','paused','done','failed','cancelled')),

        payload            JSONB NOT NULL,
        result             JSONB,
        error_message      TEXT,

        progress_snapshot  JSONB,

        created_at         TIMESTAMPTZ DEFAULT NOW(),
        started_at         TIMESTAMPTZ,
        finished_at        TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_queue ON agent_tasks(agent_id, status, priority_num DESC, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_run ON agent_tasks(run_id)",

    # ---------- chats ----------
    """
    CREATE TABLE IF NOT EXISTS chats (
        id             BIGSERIAL PRIMARY KEY,
        user_id        BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        agent_id       BIGINT REFERENCES agents(id) ON DELETE CASCADE,
        role           TEXT CHECK (role IN ('user','assistant','system')),
        content        TEXT,
        image_url      TEXT,
        input_tokens   INT DEFAULT 0,
        output_tokens  INT DEFAULT 0,
        cost_usd       NUMERIC(10,6) DEFAULT 0,
        duration_ms    INT DEFAULT 0,
        model_id       VARCHAR(100),
        created_at     TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chats_agent ON chats(agent_id, created_at)",

    # ---------- ratings ----------
    """
    CREATE TABLE IF NOT EXISTS ratings (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        step_id     BIGINT REFERENCES run_steps(id) ON DELETE CASCADE,
        rating      INT CHECK (rating BETWEEN 1 AND 5),
        suggestion  TEXT,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ---------- uploads ----------
    """
    CREATE TABLE IF NOT EXISTS uploads (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        filename   VARCHAR(300),
        url        TEXT,
        mime       VARCHAR(100),
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ---------- lead_conversations & lead_messages ----------
    """
    CREATE TABLE IF NOT EXISTS lead_conversations (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        thread_id   VARCHAR(64) NOT NULL,
        title       VARCHAR(300),
        status      TEXT DEFAULT 'active' CHECK (status IN ('active','archived','cancelled')),
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (user_id, thread_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lead_messages (
        id                    BIGSERIAL PRIMARY KEY,
        thread_id             VARCHAR(64) NOT NULL,
        role                  TEXT CHECK (role IN ('user','lead','system')),
        content               TEXT,
        proposed_workflow_id  BIGINT REFERENCES workflows(id) ON DELETE SET NULL,
        tool_calls            JSONB,
        metadata              JSONB DEFAULT '{}'::jsonb,
        cancelled             BOOLEAN DEFAULT FALSE,
        created_at            TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_lead_msgs_thread ON lead_messages(thread_id, created_at)",

    # ---------- agent_skills ----------
    """
    CREATE TABLE IF NOT EXISTS agent_skills (
        id                BIGSERIAL PRIMARY KEY,
        agent_id          BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        slug              VARCHAR(120) NOT NULL,
        name              VARCHAR(200),
        description       TEXT,
        content_md        TEXT,
        source            TEXT DEFAULT 'manual'
                            CHECK (source IN ('manual','self_learned','imported')),
        source_run_ids    JSONB DEFAULT '[]'::jsonb,
        confidence        REAL,
        approved_by_user  BOOLEAN DEFAULT FALSE,
        times_used        INT DEFAULT 0,
        created_at        TIMESTAMPTZ DEFAULT NOW(),
        updated_at        TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (agent_id, slug)
    )
    """,

    # ---------- agent_quotas ----------
    """
    CREATE TABLE IF NOT EXISTS agent_quotas (
        id                         BIGSERIAL PRIMARY KEY,
        agent_id                   BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        name                       VARCHAR(200),
        window_type                TEXT CHECK (window_type IN ('hourly','daily','weekly','monthly','project','lifetime')),
        window_start               TIMESTAMPTZ,
        window_end                 TIMESTAMPTZ,
        max_tokens                 BIGINT,
        max_tpm                    INT,
        max_rpm                    INT,
        max_cost_usd               NUMERIC(12,4),
        current_tokens             BIGINT DEFAULT 0,
        current_cost_usd           NUMERIC(12,4) DEFAULT 0,
        current_window_started_at  TIMESTAMPTZ,
        hard_limit                 BOOLEAN DEFAULT TRUE,
        enabled                    BOOLEAN DEFAULT TRUE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quotas_agent ON agent_quotas(agent_id)",

    # ---------- agent_shares & external_agent_links ----------
    """
    CREATE TABLE IF NOT EXISTS agent_shares (
        id                  BIGSERIAL PRIMARY KEY,
        agent_id            BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        owner_user_id       BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        borrower_user_id    BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        scope               TEXT DEFAULT 'invoke' CHECK (scope IN ('read','invoke','full')),
        price_per_call_usd  NUMERIC(10,4) DEFAULT 0,
        max_calls_per_day   INT,
        expires_at          TIMESTAMPTZ,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        revoked_at          TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_agent_links (
        id               BIGSERIAL PRIMARY KEY,
        local_user_id    BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        remote_endpoint  VARCHAR(500),
        remote_agent_id  VARCHAR(100),
        api_key          VARCHAR(500),
        cached_profile   JSONB,
        last_synced_at   TIMESTAMPTZ,
        enabled          BOOLEAN DEFAULT TRUE,
        created_at       TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ---------- skill_guardrails ----------
    """
    CREATE TABLE IF NOT EXISTS skill_guardrails (
        id           BIGSERIAL PRIMARY KEY,
        scope        TEXT CHECK (scope IN ('org','user')),
        user_id      BIGINT REFERENCES as_users(id) ON DELETE CASCADE,
        rule_type    TEXT CHECK (rule_type IN ('deny_keyword','deny_category','deny_tool','max_confidence','require_review')),
        rule_value   TEXT,
        description  TEXT,
        enabled      BOOLEAN DEFAULT TRUE,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ---------- agent_escalations ----------
    """
    CREATE TABLE IF NOT EXISTS agent_escalations (
        id                  BIGSERIAL PRIMARY KEY,
        task_id             BIGINT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
        run_id              BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        raising_agent_id    BIGINT NOT NULL REFERENCES agents(id),
        task_owner_id       BIGINT NOT NULL REFERENCES as_users(id),
        uncertainty         TEXT,
        context             JSONB,
        route               TEXT CHECK (route IN ('peer_consult','lead','user')),
        consulted_agent_id  BIGINT REFERENCES agents(id),
        status              TEXT DEFAULT 'pending' CHECK (status IN ('pending','resolved','timeout','abandoned')),
        resolution          TEXT,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        resolved_at         TIMESTAMPTZ
    )
    """,

    # ---------- notifications ----------
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id                     BIGSERIAL PRIMARY KEY,
        user_id                BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        type                   TEXT CHECK (type IN (
                                 'queue_conflict','budget_warning','budget_exceeded',
                                 'agent_off_duty','skill_suggested','workflow_failed',
                                 'share_request','lead_proposal','escalation')),
        severity               TEXT DEFAULT 'info' CHECK (severity IN ('info','warn','error','critical')),
        title                  VARCHAR(300),
        body                   TEXT,
        action_payload         JSONB,
        related_run_id         BIGINT REFERENCES runs(id) ON DELETE SET NULL,
        related_agent_id       BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        related_workflow_id    BIGINT REFERENCES workflows(id) ON DELETE SET NULL,
        related_escalation_id  BIGINT REFERENCES agent_escalations(id) ON DELETE SET NULL,
        status                 TEXT DEFAULT 'unread' CHECK (status IN ('unread','read','resolved','dismissed')),
        resolution             TEXT,
        created_at             TIMESTAMPTZ DEFAULT NOW(),
        resolved_at            TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notify_user_status ON notifications(user_id, status, created_at DESC)",

    # ---------- schedules ----------
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id                BIGSERIAL PRIMARY KEY,
        user_id           BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        workflow_id       BIGINT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        name              VARCHAR(200),
        trigger_type      TEXT DEFAULT 'interval' CHECK (trigger_type IN ('cron','interval','once')),
        cron_expression   VARCHAR(100),
        interval_seconds  INT,
        default_input     TEXT,
        priority          TEXT DEFAULT 'normal'
                            CHECK (priority IN ('low','normal','high','critical','urgent')),
        max_queue_depth   INT,
        enabled           BOOLEAN DEFAULT TRUE,
        next_run_at       TIMESTAMPTZ,
        last_run_at       TIMESTAMPTZ,
        created_at        TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_schedules_next ON schedules(enabled, next_run_at) WHERE enabled = TRUE",

    # ---------- Migrations (non-destructive ALTER TABLE) ----------
    # Review loop — node_type may be 'review'. max_review_iterations caps
    # how many REVISE cycles a single position can trigger.
    # The existing CHECK constraint is dropped and re-added with 'review' added.
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.check_constraints
                   WHERE constraint_name = 'workflow_nodes_node_type_check') THEN
            ALTER TABLE workflow_nodes DROP CONSTRAINT workflow_nodes_node_type_check;
        END IF;
    END $$
    """,
    """
    ALTER TABLE workflow_nodes
        ADD CONSTRAINT workflow_nodes_node_type_check
        CHECK (node_type IN ('agent','group','review'))
    """,
    "ALTER TABLE workflows ADD COLUMN IF NOT EXISTS max_review_iterations INT DEFAULT 2",
    # Projects wrapper on existing run/step rows. NULL = adhoc.
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, started_at DESC)",
    "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS idx_run_steps_project_date ON run_steps(project_id, started_at)",
    # Per-agent precise quotas. NULL = no cap.
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS daily_token_quota BIGINT",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS daily_cost_quota REAL",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_token_quota BIGINT",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_cost_quota REAL",
    # User-level auto-topup policy. Off by default.
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS auto_topup_enabled BOOLEAN DEFAULT FALSE",
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS auto_topup_per_topup_cost REAL DEFAULT 1.0",
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS auto_topup_max_per_day INT DEFAULT 3",
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS report_webhook_url TEXT",
    """
    CREATE TABLE IF NOT EXISTS api_tokens (
        id           BIGSERIAL PRIMARY KEY,
        user_id      BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        name         VARCHAR(100),
        token_hash   VARCHAR(128) UNIQUE NOT NULL,
        last_used_at TIMESTAMPTZ,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id)",
    # Ledger for every auto-topup event (rate-limits auto-topup per day +
    # shows the user what's happened).
    """
    CREATE TABLE IF NOT EXISTS auto_topup_events (
        id              BIGSERIAL PRIMARY KEY,
        user_id         BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        agent_id        BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        amount_cost_usd REAL NOT NULL,
        event_date      DATE NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auto_topup_user_date ON auto_topup_events(user_id, event_date DESC)",
    "ALTER TABLE groups_tbl ADD COLUMN IF NOT EXISTS is_ephemeral BOOLEAN DEFAULT FALSE",
    "ALTER TABLE lead_conversations ADD COLUMN IF NOT EXISTS agent_id BIGINT REFERENCES agents(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS idx_lead_conv_agent ON lead_conversations(user_id, agent_id)",
    # IM channel source continuity — when a Lead thread was opened from
    # Telegram / Slack / etc., remember which platform + external chat
    # it came from so replies can be routed back.
    "ALTER TABLE lead_conversations ADD COLUMN IF NOT EXISTS source_platform VARCHAR(30)",
    "ALTER TABLE lead_conversations ADD COLUMN IF NOT EXISTS source_external_id VARCHAR(200)",
    "CREATE INDEX IF NOT EXISTS idx_lead_conv_source ON lead_conversations(source_platform, source_external_id)",
    # Model client health — populated by the per-client "Test" action in
    # Settings → Models. `last_test_status` is one of
    # 'ok' | 'fail' | NULL (= never tested).
    "ALTER TABLE model_clients ADD COLUMN IF NOT EXISTS last_test_at TIMESTAMPTZ",
    "ALTER TABLE model_clients ADD COLUMN IF NOT EXISTS last_test_status VARCHAR(20)",
    "ALTER TABLE model_clients ADD COLUMN IF NOT EXISTS last_test_message TEXT",
    # Scheduled workflow runs may scope to a project — when set, runs
    # dispatched from this schedule carry project_id through for quota
    # attribution, and the coordinator sees them in project usage slices.
    "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS idx_schedules_project ON schedules(project_id) WHERE project_id IS NOT NULL",
    # Skill extractor audit trail — every auto-learned skill now records
    # which model ran the extraction, how many tokens / USD it cost, and
    # a preview of both the prompt and the LLM's reasoning so a user can
    # scrutinise the call later. Complements source_run_ids (already on
    # the table) which captures WHICH runs the skill was mined from.
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_model_id VARCHAR(100)",
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_input_tokens BIGINT",
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_output_tokens BIGINT",
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_cost_usd NUMERIC(12,6)",
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_prompt_preview TEXT",
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_response_preview TEXT",
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS extraction_at TIMESTAMPTZ",
    # Usage tracking parity with asset_items so the Library view shows
    # "used N× · last X ago" for self-learned skills too. Updated by the
    # engine whenever a skill is injected into an agent's system prompt.
    "ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ",
    # Per-user knob — default ON. When off, extracted skills stay as
    # proposals (approved_by_user=FALSE) until the user clicks Approve.
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS skills_auto_approve BOOLEAN DEFAULT TRUE",
    # Backfill: older projects might have a coordinator that isn't in
    # project_members (pre-fix behaviour), which broke quota checks and
    # caused coordinator dispatches to infinite-retry. One-shot add with
    # 100/100 allocation, skipping any pair that already exists.
    """
    INSERT INTO project_members (project_id, agent_id, daily_alloc_pct, monthly_alloc_pct)
    SELECT p.id, p.coordinator_agent_id, 100.0, 100.0
    FROM projects p
    WHERE p.coordinator_agent_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM project_members pm
          WHERE pm.project_id = p.id AND pm.agent_id = p.coordinator_agent_id
      )
    """,
    # IM channel bindings — one row per (user, platform) pair. Bot token
    # / webhook secret / etc. live encrypted in `secret_encrypted`.
    # External id is populated lazily on first `/start` contact so we
    # know which chat to push replies to.
    """
    CREATE TABLE IF NOT EXISTS im_bindings (
        id                 BIGSERIAL PRIMARY KEY,
        user_id            BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        platform           VARCHAR(30) NOT NULL,
        external_id        VARCHAR(200),
        display_name       VARCHAR(200),
        enabled            BOOLEAN DEFAULT TRUE,
        secret_encrypted   TEXT,
        metadata           JSONB DEFAULT '{}'::jsonb,
        created_at         TIMESTAMPTZ DEFAULT NOW(),
        updated_at         TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, platform)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_im_bindings_platform_ext ON im_bindings(platform, external_id)",
    # Transport selector: polling (default, zero setup, fine for dev and
    # single-user hobby deployments) or webhook (needs a public URL but
    # eliminates per-binding long-poll threads — essential at scale).
    "ALTER TABLE im_bindings ADD COLUMN IF NOT EXISTS transport VARCHAR(20) DEFAULT 'polling'",
    # Stage 1 — tool use: record each turn's tool invocations
    "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS tool_calls JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS turn INT DEFAULT 0",
    # Per-agent tool allowlist (list of built-in tool names).
    # Empty / missing list = no tools (falls back to single-turn text path).
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS tool_config JSONB DEFAULT '[]'::jsonb",
    # Stage 5 — MCP server bridge: per-agent external tool servers
    """
    CREATE TABLE IF NOT EXISTS agent_mcp_servers (
        id           BIGSERIAL PRIMARY KEY,
        agent_id     BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        name         VARCHAR(100) NOT NULL,
        url          TEXT NOT NULL,
        auth_header  TEXT,
        enabled      BOOLEAN DEFAULT TRUE,
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        updated_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_mcp_servers_agent ON agent_mcp_servers(agent_id)",
    # Phase 1 — RBAC groundwork: every user has a role (admin or user) and a
    # last_seen_at stamp used by the Lead proxy-answer feature to decide
    # whether the user is currently watching.
    # Add column without default first, upgrade existing rows to admin (they
    # predate the role system), THEN install the default for new rows. The
    # UPDATE is idempotent — after first run there are no NULLs left.
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS role VARCHAR(16)",
    "UPDATE as_users SET role = 'admin' WHERE role IS NULL",
    "ALTER TABLE as_users ALTER COLUMN role SET DEFAULT 'user'",
    "ALTER TABLE as_users ALTER COLUMN role SET NOT NULL",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'as_users_role_chk'
        ) THEN
            ALTER TABLE as_users
                ADD CONSTRAINT as_users_role_chk CHECK (role IN ('admin','user'));
        END IF;
    END $$
    """,
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ",
    # Phase 5.1 — Lead proxy-answer bookkeeping. When Lead needs the user's
    # decision but the user has been offline too long, a scheduler tick
    # auto-generates an answer on their behalf, writes it as a new
    # lead_messages row with metadata.proxy=true, and marks the original
    # pending row as answered by zeroing this field.
    "ALTER TABLE lead_messages ADD COLUMN IF NOT EXISTS pending_decision_expires_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_lead_msgs_pending ON lead_messages(pending_decision_expires_at) WHERE pending_decision_expires_at IS NOT NULL",
    # Also add a per-user toggle for the proxy feature — users can opt out
    # and force every Lead question to block indefinitely.
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS lead_proxy_enabled BOOLEAN DEFAULT TRUE",
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS lead_proxy_timeout_minutes INT DEFAULT 10",
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS lead_proxy_away_minutes INT DEFAULT 5",
    # Phase 5.3 — Browse / API activity audit log. Every authenticated
    # request flows through the before_request hook which writes one row
    # here. Non-mutating GETs are optional (too noisy) — see the
    # AUDIT_METHODS set in app.py for what actually lands in this table.
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id            BIGSERIAL PRIMARY KEY,
        user_id       BIGINT REFERENCES as_users(id) ON DELETE SET NULL,
        method        VARCHAR(10) NOT NULL,
        path          TEXT NOT NULL,
        status_code   INT,
        resource_type VARCHAR(40),
        resource_id   BIGINT,
        metadata      JSONB DEFAULT '{}'::jsonb,
        created_at    TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_user_ts ON audit_log(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_path ON audit_log(path, created_at DESC)",
    # Phase 5.4 — per-user quotas. Checked at dispatch time in engine.py
    # to refuse over-budget runs with HTTP 429.
    """
    CREATE TABLE IF NOT EXISTS user_quotas (
        user_id                 BIGINT PRIMARY KEY REFERENCES as_users(id) ON DELETE CASCADE,
        daily_token_limit       BIGINT,
        daily_cost_limit_usd    NUMERIC(10, 4),
        monthly_token_limit     BIGINT,
        monthly_cost_limit_usd  NUMERIC(12, 4),
        updated_at              TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # Phase 1.3 — system feature flags. Each row toggles whether a feature
    # is admin-only (true) or open to everyone (false). Label is the human
    # name shown in the system settings UI; the feature key is what code
    # checks via services.feature_flags.is_admin_only().
    """
    CREATE TABLE IF NOT EXISTS system_feature_flags (
        feature      VARCHAR(64) PRIMARY KEY,
        label        TEXT NOT NULL,
        description  TEXT,
        admin_only   BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ================================================================
    # Phase 2 — asset library (Skill / Tool / MCP / RAG as first-class
    # shared objects that live at library level, not per-agent).
    #
    # asset_items is the flat library table; agent_assets maps individual
    # agents to library items; asset_grants allows sharing library items
    # to other users (so a non-owner can also assign them to their agents);
    # asset_audit_log captures every mutation; asset_usage_log captures
    # every tool call (feeds the usage charts in the library UI).
    # ================================================================
    """
    CREATE TABLE IF NOT EXISTS asset_items (
        id                    BIGSERIAL PRIMARY KEY,
        kind                  VARCHAR(16) NOT NULL
                                CHECK (kind IN ('skill','tool','mcp','rag')),
        name                  VARCHAR(200) NOT NULL,
        description           TEXT,
        owner_user_id         BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        enabled               BOOLEAN NOT NULL DEFAULT TRUE,
        -- Kind-specific structured payload.
        --   skill: {"content_md": "...", "input_schema": {...}}
        --   tool:  {"module": "backend.tools.http_get", "fn": "handler"}
        --   mcp:   {"url": "...", "auth_header_label": "Bearer ..."}
        --   rag:   {"backend": "pgvector|bedrock_kb|pinecone", "config": {...}, "doc_count": N}
        config                JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- Opaque Fernet-encrypted secret blob (MCP auth header,
        -- RAG API key, etc.). Never round-tripped to the frontend.
        credential_encrypted  TEXT,
        -- Free-form tags, icon, docs url, etc. Round-tripped to UI.
        metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        updated_at            TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_asset_items_kind ON asset_items(kind, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_asset_items_owner ON asset_items(owner_user_id)",

    """
    CREATE TABLE IF NOT EXISTS asset_grants (
        id               BIGSERIAL PRIMARY KEY,
        asset_id         BIGINT NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        grantee_user_id  BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        granted_by       BIGINT NOT NULL REFERENCES as_users(id),
        created_at       TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (asset_id, grantee_user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_asset_grants_grantee ON asset_grants(grantee_user_id)",

    """
    CREATE TABLE IF NOT EXISTS agent_assets (
        id          BIGSERIAL PRIMARY KEY,
        agent_id    BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        asset_id    BIGINT NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (agent_id, asset_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_assets_agent ON agent_assets(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_assets_asset ON agent_assets(asset_id)",

    """
    CREATE TABLE IF NOT EXISTS asset_audit_log (
        id              BIGSERIAL PRIMARY KEY,
        asset_id        BIGINT REFERENCES asset_items(id) ON DELETE SET NULL,
        actor_user_id   BIGINT REFERENCES as_users(id) ON DELETE SET NULL,
        action          VARCHAR(32) NOT NULL,
        before_state    JSONB,
        after_state     JSONB,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_asset_audit_asset ON asset_audit_log(asset_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_asset_audit_actor ON asset_audit_log(actor_user_id, created_at DESC)",

    """
    CREATE TABLE IF NOT EXISTS asset_usage_log (
        id           BIGSERIAL PRIMARY KEY,
        asset_id     BIGINT NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        user_id      BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        agent_id     BIGINT REFERENCES agents(id) ON DELETE SET NULL,
        run_id       BIGINT REFERENCES runs(id) ON DELETE SET NULL,
        turn         INT,
        called_at    TIMESTAMPTZ DEFAULT NOW(),
        duration_ms  INT,
        ok           BOOLEAN DEFAULT TRUE,
        error        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_asset_usage_asset_ts ON asset_usage_log(asset_id, called_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_asset_usage_user_ts ON asset_usage_log(user_id, called_at DESC)",

    # ---------- RAG document store (local pgvector backend) ----------
    # Used by asset_items of kind='rag' whose config.backend='pgvector'.
    # External backends (bedrock_kb, pinecone) store docs in their own
    # services and don't touch this table.
    """
    CREATE TABLE IF NOT EXISTS rag_documents (
        id             BIGSERIAL PRIMARY KEY,
        asset_id       BIGINT NOT NULL REFERENCES asset_items(id) ON DELETE CASCADE,
        source_name    VARCHAR(300),
        chunk_index    INT NOT NULL,
        content        TEXT NOT NULL,
        embedding      vector(1024),
        metadata       JSONB DEFAULT '{}'::jsonb,
        created_at     TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rag_documents_asset ON rag_documents(asset_id)",

    # ---------- Model client connections ----------
    # Phase 7: decouple LLM provider wiring from agents. Each row is a
    # "connection bundle" (provider + region/endpoint + credentials) that
    # admins manage centrally. Agents reference a client by id and pick a
    # model_id from the client's config.models list.
    """
    CREATE TABLE IF NOT EXISTS model_clients (
        id                    BIGSERIAL PRIMARY KEY,
        name                  VARCHAR(200) NOT NULL,
        kind                  VARCHAR(32) NOT NULL
                                CHECK (kind IN ('bedrock','claude_native','openai','azure_openai','gemini','minimax','local')),
        description           TEXT,
        config                JSONB NOT NULL DEFAULT '{}'::jsonb,
        credential_encrypted  TEXT,
        enabled               BOOLEAN DEFAULT TRUE,
        default_for_new_users BOOLEAN DEFAULT FALSE,
        created_by            BIGINT REFERENCES as_users(id) ON DELETE SET NULL,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        updated_at            TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_model_clients_kind ON model_clients(kind, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_model_clients_default ON model_clients(default_for_new_users) WHERE default_for_new_users = TRUE",

    # Per-user access to a model_client. If no grants exist for a client,
    # only admins can see it. default_for_new_users controls whether newly
    # created users automatically get a grant on user creation.
    """
    CREATE TABLE IF NOT EXISTS model_client_grants (
        id           BIGSERIAL PRIMARY KEY,
        client_id    BIGINT NOT NULL REFERENCES model_clients(id) ON DELETE CASCADE,
        user_id      BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        granted_by   BIGINT REFERENCES as_users(id) ON DELETE SET NULL,
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(client_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_model_client_grants_user ON model_client_grants(user_id)",

    # Link agents to a specific model client. Null = legacy default (uses
    # the first default_for_new_users=TRUE client via service-layer lookup).
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS model_client_id BIGINT REFERENCES model_clients(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS idx_agents_model_client ON agents(model_client_id)",

    # ---------- Desktop app sessions ----------
    # Long-lived opaque tokens for the Tauri desktop app. Tokens are
    # validated via the X-Desktop-Token header and bypass cookie-based
    # sessions. Default TTL is 365 days; only explicit logout or expiry
    # invalidates them.
    """
    CREATE TABLE IF NOT EXISTS desktop_sessions (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES as_users(id) ON DELETE CASCADE,
        token       VARCHAR(128) UNIQUE NOT NULL,
        expires_at  TIMESTAMPTZ NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_desktop_sessions_token ON desktop_sessions(token)",

    # Cast layout preferences — shared between web and desktop
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS cast_layout JSONB DEFAULT '{}'::jsonb",

    # User language preference (i18n)
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'en'",

    # Lead workflow planning constraints (per-user, with admin-set defaults)
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS lead_max_steps INT DEFAULT 10",
    "ALTER TABLE as_users ADD COLUMN IF NOT EXISTS lead_max_tokens INT DEFAULT 50000",

    # Feature flag value column for string-type settings (e.g. default_language)
    "ALTER TABLE system_feature_flags ADD COLUMN IF NOT EXISTS value TEXT",

    # IVFFlat index on the embedding column for fast ANN search. Only
    # created when there are rows to train on; otherwise the CREATE INDEX
    # succeeds but the planner won't use it until there are >= lists rows.
    # 100 lists is a reasonable default for up to ~100k vectors.
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = 'idx_rag_documents_embed'
        ) THEN
            CREATE INDEX idx_rag_documents_embed ON rag_documents
                USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
        END IF;
    END $$
    """,
]


def create_all() -> None:
    """Run every DDL statement. Safe to call on every startup."""
    from .db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
    # Seed data-level defaults (feature flags canonical set). Lazy-imported
    # here so this module has no circular dependency with services/.
    from .services import feature_flags, asset_seeds, model_clients
    feature_flags.seed_flags()
    # One-shot migration of legacy per-agent MCP rows into asset library.
    _migrate_agent_mcp_to_asset_items()
    # Seed library defaults (built-in tools + popular MCP placeholders).
    # Safe/idempotent: only inserts rows that don't already exist.
    asset_seeds.seed_default_assets()
    # Seed the default Bedrock model client and backfill any legacy agents
    # that still have model_client_id = NULL so the new dispatch path works
    # without any manual migration step.
    model_clients.seed_default_client_and_backfill()


def _migrate_agent_mcp_to_asset_items() -> None:
    """Copy every row from the legacy agent_mcp_servers table into
    asset_items (kind='mcp') + agent_assets mapping. Idempotent — checks
    for a metadata.migrated_from marker so repeated startups don't
    duplicate rows."""
    from .db import get_conn
    from .services import asset_crypto
    import json

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.id, s.agent_id, s.name, s.url, s.auth_header, s.enabled, "
                "       a.user_id "
                "FROM agent_mcp_servers s "
                "JOIN agents a ON a.id = s.agent_id "
            )
            legacy = cur.fetchall()

            for row in legacy:
                marker = f"legacy_agent_mcp_servers:{row['id']}"
                cur.execute(
                    "SELECT id FROM asset_items "
                    "WHERE kind = 'mcp' AND metadata ->> 'migrated_from' = %s",
                    (marker,),
                )
                if cur.fetchone():
                    continue

                config = {"url": row["url"]}
                meta = {"migrated_from": marker}
                enc = asset_crypto.encrypt(row["auth_header"]) if row["auth_header"] else None
                cur.execute(
                    """
                    INSERT INTO asset_items
                      (kind, name, description, owner_user_id, enabled,
                       config, metadata, credential_encrypted)
                    VALUES ('mcp', %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        row["name"],
                        f"Imported from agent_mcp_servers #{row['id']}",
                        row["user_id"],
                        row["enabled"],
                        json.dumps(config),
                        json.dumps(meta),
                        enc,
                    ),
                )
                new_asset_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO agent_assets (agent_id, asset_id, enabled)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (agent_id, asset_id) DO NOTHING
                    """,
                    (row["agent_id"], new_asset_id, row["enabled"]),
                )
