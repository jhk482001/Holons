# 實作文件 — agent_company

> 最後更新：2026-04-16
> 讀者：將要動手改 code 的工程師 / AI 協作者

本文件是「程式在哪、怎麼跑、怎麼改」的索引。設計決策請看 `design.md`，使用者功能請看 `features.md`。

---

## 1. 快速開始

### 1.1 本機開發

```bash
# 1. 環境準備
cd agent_company
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. DB (docker-compose 起 Postgres + pgvector)
docker compose up -d postgres

# 3. 設定 env
cp env.config.example env.config  # 若有，否則手動建
# 必填：DATABASE_URL / SECRET_KEY / AWS_ACCESS_KEY / AWS_SECRET_KEY

# 4. 初始化 schema + 種子資料
python3 seed_v2.py

# 5. 啟動 backend（port 8087）
python3 -m backend.app

# 6. 另開 terminal 啟動 frontend（port 5173）
cd frontend && npm install && npm run dev
```

打開瀏覽器 `http://localhost:5173`，預設登入 `alice / password`。

### 1.2 Docker 方式

```bash
docker compose up --build
```

會起 postgres + backend + frontend 三個服務。frontend 走 nginx serve build artifact。

### 1.3 測試

```bash
# Python 單元 / 整合測試
python3 -m pytest tests/ -v

# Playwright e2e（需要 backend + frontend 都在跑）
python3 -m pytest tests/test_e2e.py -v
```

目前狀態：**114 個 backend 測試 + 47 個 e2e 測試全綠**。跑 e2e 之前請確認 port 8087 沒有別的 backend 進程（否則會搶佔 worker）。

---

## 2. 目錄結構

```
agent_company/
├── backend/
│   ├── app.py                # Flask routes（所有 REST endpoint，~2500 行）
│   ├── schema.py             # DB schema 定義 + create_all() + 種子呼叫
│   ├── db.py                 # psycopg2 wrapper (fetch_one / fetch_all / execute)
│   ├── config.py             # 環境變數載入
│   ├── engine.py             # Workflow 執行引擎（dispatch / execute / advance）
│   ├── queue.py              # agent_tasks 佇列的 SKIP LOCKED 實作
│   ├── worker.py             # 背景 worker thread（app 啟動時起）
│   ├── bedrock_client.py     # AWS Bedrock 封裝
│   ├── middleware.py         # Session auth / CSRF / audit hook
│   ├── services/
│   │   ├── assets.py             # Asset CRUD + grant 邏輯
│   │   ├── asset_seeds.py        # 預設 skill/tool/mcp 種子資料
│   │   ├── asset_crypto.py       # Fernet 對稱加密 credential
│   │   ├── avatar.py             # Peeps SVG 合成
│   │   ├── escalation.py         # Agent 自動 pause / escalation 邏輯
│   │   ├── feature_flags.py      # System feature flag 管理
│   │   ├── lead_agent.py         # Lead 總機 dispatcher
│   │   ├── lead_proxy.py         # Lead 代答決策
│   │   ├── mcp_client.py         # MCP Streamable HTTP client
│   │   ├── notifications.py      # Notification CRUD
│   │   ├── quotas.py             # Agent-level quota 檢查
│   │   ├── user_quotas.py        # User-level quota 檢查
│   │   ├── rag.py                # RAG 統一介面（dispatch 到 backend）
│   │   ├── rag_external.py       # Bedrock KB / Pinecone 實作
│   │   ├── scheduler.py          # Cron 排程 tick
│   │   ├── sharing.py            # agent_shares / external_agent_links
│   │   └── skill_extractor.py    # Skill 自動提取（LLM call）
│   └── tools/
│       ├── current_time.py
│       ├── http_get.py
│       └── search_skills.py
├── frontend/
│   └── src/
│       ├── App.tsx              # Router + Layout + Query client
│       ├── auth.ts              # useMe() / useIsAdmin() hooks
│       ├── api/client.ts        # API client + TypeScript types (~750 行)
│       ├── pages/               # 一個檔一個頁面
│       │   ├── Dashboard.tsx
│       │   ├── DialogCenter.tsx        # 對話中心主畫面
│       │   ├── Agents.tsx
│       │   ├── AgentDetail.tsx
│       │   ├── Workflows.tsx
│       │   ├── WorkflowEditor.tsx      # 視覺化編輯器
│       │   ├── Groups.tsx
│       │   ├── Runs.tsx
│       │   ├── RunDetail.tsx
│       │   ├── Records.tsx             # Runs + Audit 的容器
│       │   ├── Automation.tsx          # Workflows + Schedules 的容器
│       │   ├── Schedules.tsx
│       │   ├── Skills.tsx              # Agent skills 管理頁
│       │   ├── Library.tsx             # Asset Library (4 tabs)
│       │   ├── Settings.tsx            # Settings 容器（多 tab）
│       │   ├── Escalations.tsx
│       │   └── Login.tsx
│       ├── components/
│       │   ├── Avatar.tsx              # 共用小圓頭像
│       │   ├── Avatar.css
│       │   ├── AvatarBuilder.tsx       # agent 建立時的頭像選擇 UI
│       │   ├── Gantt.tsx
│       │   ├── LoadHeatmap.tsx
│       │   ├── Layout.tsx              # 左側邊欄 + 右側內容
│       │   ├── Modal.tsx
│       │   ├── Markdown.tsx            # react-markdown + remark-gfm
│       │   ├── NotificationBell.tsx
│       │   ├── RunFlowDiagram.tsx      # Run Detail 的流程圖
│       │   ├── QuotaEditor.tsx
│       │   ├── SharingEditor.tsx
│       │   ├── WorkingHoursEditor.tsx
│       │   ├── WorkflowBubble.tsx      # Dialog 裡展示 workflow 的泡泡
│       │   ├── AgentAssetsEditor.tsx
│       │   ├── AgentEditors.tsx        # DialogCenter 嵌入的 agent 設定編輯器
│       │   └── settings/               # Settings 分 tab 元件
│       │       ├── PersonalTab.tsx
│       │       ├── UserManagementTab.tsx
│       │       └── SystemSettingsTab.tsx
│       └── styles/              # 全域 CSS 變數
├── tests/
│   ├── test_api_crud.py        # REST CRUD
│   ├── test_e2e.py             # Playwright e2e
│   ├── test_services.py        # services/* 的單元測試
│   └── test_v2.py              # Phase 2+ 的整合測試
├── mcp_test/                   # MCP server 的獨立驗證工具
├── rag_test/                   # RAG 的獨立驗證工具
├── docker/                     # Dockerfile 與 compose sidecar
├── scripts/                    # 雜項 maintenance script
├── static/                     # peeps_parts/ + uploaded files
├── seed_v2.py                  # 本機初始化腳本（呼叫 schema.create_all + seeds）
├── seed_and_run.py             # demo 用，一次 seed + 跑
├── requirements.txt
├── docker-compose.yml
└── env.config                  # 環境變數（不進 git）
```

---

## 3. Backend

### 3.1 整體風格

- **Flask + 手寫 psycopg2**，沒有 ORM。所有 SQL 直接在 `app.py` 或 `services/*.py` 裡寫 — 短程式、高可見度。
- **沒有 blueprint**，所有 route 全部在 `app.py`。約 2500 行但依 domain 分段注釋，grep `@app.route` 就能定位。
- **Services** 是「跨 route 共用的 domain logic」容器，沒有 DI framework。
- **Session auth**：`@login_required` decorator + `session["user_id"]`，`middleware.py` 統一處理。
- **Error pattern**：ValueError → 400、PermissionError → 403、raise 其他 → 500。handler 在 `app.py` 頭部。

### 3.2 重要 module 的入口

| 我要做什麼 | 看哪裡 |
|-----------|--------|
| 新增 REST endpoint | `backend/app.py`，grep 同 domain 的 `@app.route` 跟著寫 |
| 改 DB schema | `backend/schema.py`：加 `CREATE TABLE` 到 `SCHEMA_SQL` 或 `ALTER TABLE` 到 `MIGRATIONS_SQL`；重啟 backend 會自動 run |
| 改 workflow 執行邏輯 | `backend/engine.py`：`dispatch_workflow` / `execute_task` / `_advance_to_next_node` |
| 改 agent task 佇列 | `backend/queue.py`：`claim_task` / `mark_done` / `mark_failed` |
| 新增 builtin tool | `backend/tools/` 下加檔，實作 `def handler(args: dict) -> dict`，然後在 `asset_seeds.py` 加 seed |
| 改 Bedrock 呼叫 | `backend/bedrock_client.py`：`call_claude()` 及 tool-use 處理 |
| 改頭像合成 | `backend/services/avatar.py`：`compose_svg()` + 對應的 route `@app.route("/api/avatar/compose")` 在 `app.py` |
| 改 Lead 代答邏輯 | `backend/services/lead_proxy.py` + `lead_agent.py` |
| 改 MCP client | `backend/services/mcp_client.py`（Streamable HTTP 協議） |
| 改 RAG backend | `backend/services/rag.py`（dispatcher）+ `rag_external.py`（具體 backend） |

### 3.3 DB schema 概觀

完整定義在 `backend/schema.py`，這裡列出表名與角色：

**使用者與 agent**
- `as_users` — 使用者（注意 prefix，避開 Postgres reserved `user`）
- `agents` — Agent 本體（含 `avatar_config`、`working_hours`、`model_id`、`tool_config`）
- `agent_skills` — Agent 的 skill（含 `status='pending' / 'approved' / 'rejected'`）
- `agent_quotas` — 該 agent 的 monthly / daily 上限
- `user_quotas` — 該 user 的 monthly / daily 上限
- `agent_shares` — Agent 分享給其他 user
- `external_agent_links` — 未登入者經由 signed URL 訪問（框架就位，UI 未完整）
- `skill_guardrails` — Skill 提取的過濾規則
- `agent_escalations` — Agent 自動暫停紀錄
- `agent_mcp_servers` — 早期每-agent MCP 清單，已被 asset_items 取代，留著為相容

**Workflow / Run**
- `workflows` — Workflow 本體
- `workflow_nodes` — Workflow 的節點 DAG
- `runs` — 一次 workflow 執行實例
- `run_steps` — 每個 node 在這次 run 的執行結果（含 tool_calls）
- `agent_tasks` — 佇列表，worker 從這裡 claim
- `groups_tbl` / `group_members` — Group（workflow 節點的另一種實作）

**對話 / 通知 / 排程**
- `lead_conversations` / `lead_messages` — Lead 總機對話
- `chats` — 直接對員工的對話
- `notifications` — 使用者通知
- `schedules` — Cron 排程

**Asset Library**
- `asset_items` — 統一 skill/tool/mcp/rag 表
- `asset_grants` — 資產分享授權
- `agent_assets` — Agent 被指派的 asset
- `asset_audit_log` — Asset 的變更歷史
- `asset_usage_log` — Asset 的每次使用紀錄（供 usage 圖）
- `rag_documents` — pgvector backend 的 chunks + embeddings

**系統**
- `audit_log` — 系統層級 audit
- `system_feature_flags` — 功能開關
- `ratings` / `uploads` — 使用者 rating、檔案上傳

關鍵 index：
- `idx_tasks_queue`：`(agent_id, status, priority_num DESC, created_at)` — worker 佇列取任務的主索引
- `idx_schedules_next`：partial index `WHERE enabled = TRUE`
- `idx_asset_items_kind`：`(kind, enabled)` — Library 按 kind 過濾
- `rag_documents` 上有 `USING ivfflat (embedding vector_cosine_ops)` — 向量近鄰查詢

### 3.4 Workflow 執行路徑（要改 engine 必讀）

```
POST /api/workflows/:id/run  →  engine.dispatch_workflow()
                                    └─ for start node: enqueue_node()
                                        └─ INSERT agent_tasks

背景 worker thread (backend/worker.py):
  while True:
    task = queue.claim_task()           # FOR UPDATE SKIP LOCKED
    try:
      result = engine.execute_task(task, ctx)
      engine.on_task_complete(task, result)
      # ^ 在 on_task_complete 裡呼叫 _advance_to_next_node()
      #   → enqueue 下一個 node 的 task（可能是 agent 或 group）
    except Exception as e:
      engine.on_task_failed(task, e)    # 或 aborted 若 run 被 stop
```

幾個需要留意的 invariant：
- **Worker 絕不 recursive call 下一步**，永遠是 enqueue 讓下一個 tick 拿到 — 保證 worker crash 安全
- **agent_tasks.status 的變更一定在 DB transaction 裡**，跟 run_steps insert / quota charge 一起 commit
- **Group 節點的展開**發生在 `_enqueue_group_node`，會建立多個 member tasks；`_handle_group_member_done` 等所有 member 完成後才 advance
- **Stuck detection**：worker 定期掃 `agent_tasks` 看有沒有 `status='running'` 但 worker 已經 crash 的，超過閾值 reset 為 queued

### 3.5 加新 Asset kind 的話怎麼辦

目前 `asset_items.kind` CHECK 限制四種。加第五種（例如 `workflow_template`）需要：

1. `schema.py`：改 CHECK 約束 → 要 migration
2. `services/assets.py`：若有 kind-specific validation，加 branch
3. `asset_seeds.py`：可選，加 seed
4. `frontend/src/api/client.ts`：`AssetKind` type 加 value
5. `frontend/src/pages/Library.tsx`：`TABS` 加一項、`kindLabel` 加一項、`defaultConfigForKind` 加一項
6. 新 kind 若需要 credential，在 `CreateAssetModal` / `AssetDetailModal` 的 `needsCredential` 判斷加

---

## 4. Frontend

### 4.1 整體風格

- **React 18 + Vite + TypeScript + React Router 6 + TanStack Query v5**
- 沒有 Redux / Zustand / Jotai — 所有 server state 靠 React Query，local UI state 靠 `useState`
- 沒有 CSS framework — 手寫 CSS，全域變數在 `styles/` 下
- **Modal 一律用 `components/Modal.tsx`**，不用 portal library
- **Markdown 一律用 `components/Markdown.tsx`**（react-markdown + remark-gfm）

### 4.2 API client

全部在 `frontend/src/api/client.ts`（~760 行）。模式：

```ts
// Grouped by domain
export const AgentsAPI = {
  list: () => api.get<Agent[]>("/agents"),
  create: (data) => api.post<Agent>("/agents", data),
  update: (id, data) => api.put<Agent>(`/agents/${id}`, data),
  remove: (id) => api.del<{ok:true}>(`/agents/${id}`),
  // ...
};
```

TypeScript interface 就在 API 物件上方。加新 endpoint：先加 interface，再加 API method，最後在 page 元件用。

### 4.3 Query key 慣例

```ts
useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });
useQuery({ queryKey: ["agent", id], queryFn: () => AgentsAPI.get(id) });
useQuery({ queryKey: ["assets", kind], queryFn: () => AssetsAPI.list(kind) });
useQuery({ queryKey: ["asset-audit", id], queryFn: () => AssetsAPI.audit(id) });
```

- 頂層名詞用複數（`agents`、`runs`、`assets`）
- 第二層用 id 或 filter key
- Invalidate 時用 `qc.invalidateQueries({ queryKey: ["assets", kind] })`

### 4.4 路由

定義在 `App.tsx`：

```
/login
/  (redirect → /dialog)
/dialog                  DialogCenter
/dashboard               Dashboard
/agents                  Agents (list)
/agents/:id              AgentDetail
/groups                  Groups
/workflows               Workflows
/workflows/:id           WorkflowEditor
/automation              Automation (tabs: workflows, schedules)
/schedules               Schedules
/records                 Records (tabs: runs, audit)
/runs                    Runs
/runs/:id                RunDetail
/library                 Library (query param ?tab= for skill/tool/mcp/rag)
/skills                  Skills (per-agent skill 管理)
/settings                Settings (tabs: personal, users, system)
```

### 4.5 共用 UI 慣例

**按鈕**：統一用 `.mbtn` class，modifiers `.primary` / `.danger` / `.ghost`

**卡片**：grid 佈局常見 pattern 是 `.xxx-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }`

**Avatar**：任何需要小圓頭像的地方，用 `components/Avatar.tsx`：
```tsx
<Avatar cfg={agent.avatar_config} size={42} title={agent.name} />
```
全身肖像（大 bust）用 `bustUrl()`，小頭像用 `headUrl()`（見 `api/client.ts`）。詳情見 `design.md` 第 7 節。

**Testid 慣例**：每個可互動元素都加 `data-testid="..."`，給 e2e 測試用。命名 pattern：`<domain>-<action>-<id>`，例如 `asset-delete-123`、`library-tab-mcp`。

### 4.6 常見任務

**加一個新頁面**：
1. `pages/NewPage.tsx` 建檔
2. `App.tsx` 加 `<Route>`
3. `Layout.tsx` 左側邊欄加連結（若使用者能直接訪問）

**加一個 form field**：
1. `api/client.ts`：interface 加欄位
2. Backend route 的 payload 解析加欄位
3. DB schema 加 column（若新欄位要存）
4. Modal 元件加 input + state

**加一個 builtin tool**：
1. `backend/tools/foo.py`：`def handler(args: dict) -> dict`
2. `backend/services/asset_seeds.py`：加 seed entry 指向 `backend.tools.foo`
3. 重啟 backend（會重新 seed）
4. admin 在 Library → 工具 看到新項目，可指派給 agent

**Debug run 卡住**：
1. `SELECT * FROM agent_tasks WHERE status='running' ORDER BY created_at;`
2. 看 worker log（backend stdout）
3. 若確定卡死，`UPDATE agent_tasks SET status='queued' WHERE id=?` 讓 worker 重試
4. 或透過 UI 的 Run Detail stop 按鈕

---

## 5. 測試

### 5.1 Backend 測試

- `tests/test_services.py`：service 層單元測試（無 DB 或 mock DB）
- `tests/test_api_crud.py`：Flask test client 打 REST endpoint
- `tests/test_v2.py`：Phase 2+ 的跨 service 整合測試（用真 DB）

重要：跑 engine 或 worker 相關測試前，**務必確定沒有別的 backend 進程在跑**，否則背景 worker 會搶 test 派發的 task：

```bash
lsof -ti :8087 | xargs kill 2>/dev/null
python3 -m pytest tests/test_v2.py -v
```

這個 gotcha 在 Phase 2 和 Phase 5 都踩過，已經寫進 CLAUDE 記憶。

### 5.2 E2E 測試

`tests/test_e2e.py` 用 Playwright 開 headless Chromium 打 `http://localhost:5173`。需要：
1. Backend 在 8087
2. Frontend dev server 在 5173
3. DB 乾淨（或至少有預期 seed 資料）

常見 fixture：預設用 `alice / password` 登入。

---

## 6. 部署

### 6.1 Docker compose

`docker-compose.yml` 定義三個服務：
- `postgres`：pgvector image
- `backend`：Flask app，開 8087
- `frontend`：nginx serve Vite build

啟動：
```bash
docker compose up --build -d
```

### 6.2 環境變數

關鍵必填：
- `DATABASE_URL` 或 `POSTGRES_*` 組合
- `SECRET_KEY`（Flask session 簽章）
- `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` / `AWS_REGION`（Bedrock）
- `ASSET_ENCRYPTION_KEY`（Fernet key for credential 加密；首次啟動若缺會自動生成並寫回 `env.config`）

選填：
- `PORT`（backend 預設 8087）

### 6.3 Production 建議

目前直接用 Flask dev server — **不適合 production**。要上線前必做：
1. 改用 `gunicorn -w 4 -k gthread backend.app:app`
2. Worker 拆成獨立 process（現在跟 API 共進程會阻塞 request）
3. `env.config` 改用 docker secret / AWS Secrets Manager
4. Postgres 加 `pg_dump` cron 備份
5. nginx 前置做 HTTPS terminate + rate limit
6. Bedrock 呼叫加 retry + circuit breaker

---

## 7. 除錯錦囊

**Claude 回應很慢** → 看 `backend/bedrock_client.py` 的 timeout 設定；也許是 Bedrock region 延遲

**Worker 不跑** → `SELECT count(*) FROM agent_tasks WHERE status='queued';` 看有沒有 backlog；worker 活著但 idle 表示條件不符（agent 工時、quota、feature flag）

**E2E 測試間歇失敗** → 通常是多進程搶 task（見 5.1），或 CSS/state 沒 wait 好（在點擊前加 `page.wait_for_selector`）

**Library 的 MCP server 沒連上** → 看 `asset_audit_log` 的 `action='probe_failed'` 或直接用 `mcp_test/` 裡的 standalone 驗證工具

**RAG 查詢無結果** → 確認 `rag_documents.embedding` 有值、backend 是 `pgvector`；若是 Bedrock KB 或 Pinecone 則看 credential 有沒有設

**前端 cache 對不上** → React Query devtools 看 query key；大機率是 mutation 後忘了 invalidate，或是 invalidate key 寫錯

---

## 8. 歷史版本紀錄（濃縮）

- **Phase 1 (~2026-01)**：初版，單 agent 對話 + 基本 workflow
- **Phase 2 (~2026-02)**：Asset Library 引入，MCP support
- **Phase 3 (~2026-02)**：RBAC、Settings 多 tab、User quota
- **Phase 4 (~2026-03)**：RAG（pgvector / Bedrock KB / Pinecone）、Skill 提取與審核
- **Phase 5 (~2026-03)**：Lead proxy 代答系統、通知、Escalation
- **Phase 6 (~2026-04)**：Dashboard 重新設計、導覽結構整合（Records / Automation）、audit log 完整化
- **UX 修正 (2026-04)**：WorkflowEditor zoom bug、圓形頭像置中、Library 排序搜尋編輯

當前狀態：114 backend + 47 e2e 測試全綠。生產部署前尚需做第 6.3 節的 hardening。
