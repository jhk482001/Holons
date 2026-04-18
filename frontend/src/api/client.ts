/**
 * Minimal fetch wrapper for the v2 backend.
 * All routes are proxied through /api → localhost:8087 via Vite dev proxy.
 */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const err = await res.json();
      if (err.error) msg = err.error;
    } catch {}
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

// ============================================================================
// Typed endpoints
// ============================================================================

export interface Agent {
  id: number;
  user_id: number;
  name: string;
  role_title: string | null;
  description: string | null;
  system_prompt: string | null;
  is_lead: boolean;
  primary_model_id: string | null;
  model_client_id: number | null;
  status: string;
  max_queue_depth: number;
  avatar_config: Record<string, string>;
  tool_config?: string[];
  daily_token_quota?: number | null;
  daily_cost_quota?: number | null;
  monthly_token_quota?: number | null;
  monthly_cost_quota?: number | null;
  // Present on /api/agents list responses: true when the current user is
  // not the owner (i.e., this agent was shared/borrowed into their list).
  borrowed?: boolean;
  owner_username?: string;
  owner_display_name?: string;
}

export interface ToolSpec {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export const ToolsAPI = {
  list: () => api.get<ToolSpec[]>("/tools"),
};

export interface McpServer {
  id: number;
  name: string;
  url: string;
  enabled: boolean;
  has_auth: boolean;
  created_at: string;
}

export const McpAPI = {
  list: (agentId: number) => api.get<McpServer[]>(`/agents/${agentId}/mcp_servers`),
  create: (agentId: number, data: { name: string; url: string; auth_header?: string }) =>
    api.post<{ id: number }>(`/agents/${agentId}/mcp_servers`, data),
  update: (agentId: number, sid: number, data: Partial<McpServer & { auth_header: string }>) =>
    api.put<{ ok: true }>(`/agents/${agentId}/mcp_servers/${sid}`, data),
  delete: (agentId: number, sid: number) =>
    api.del<{ ok: true }>(`/agents/${agentId}/mcp_servers/${sid}`),
  probe: (agentId: number, sid: number) =>
    api.post<{ ok: boolean; count?: number; tools?: Array<{ name: string; description: string }>; error?: string }>(
      `/agents/${agentId}/mcp_servers/${sid}/probe`,
    ),
};

export interface Run {
  id: number;
  workflow_id: number;
  user_id: number;
  initial_input: string;
  final_output: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  iterations: number;
}

export interface Workflow {
  id: number;
  name: string;
  description: string | null;
  loop_enabled: boolean;
  max_loops: number;
  source: string;
  is_draft: boolean;
  is_template?: boolean;
  parent_workflow_id?: number | null;
  owner_username?: string;
  owner_display_name?: string;
  nodes?: WorkflowNode[];
}

export interface WorkflowNode {
  id: number;
  workflow_id: number;
  position: number;
  node_type: "agent" | "group";
  agent_id: number | null;
  group_id: number | null;
  label: string | null;
  prompt_template: string | null;
  system_prompt_override?: string | null;
  pos_x: number;
  pos_y: number;
  group?: WorkflowGroupBundle;
}

export interface WorkflowGroupBundle {
  id: number;
  name: string | null;
  mode: "parallel" | "sequential" | null;
  aggregator_agent_id: number | null;
  members: {
    id: number;
    agent_id: number;
    position: number;
    custom_prompt: string | null;
    agent_name: string;
    role_title: string | null;
    avatar_config: Record<string, string>;
  }[];
}

export interface Notification {
  id: number;
  user_id: number;
  type: string;
  severity: string;
  title: string;
  body: string;
  status: string;
  created_at: string;
  related_agent_id: number | null;
  related_run_id?: number | null;
  related_workflow_id?: number | null;
  related_escalation_id?: number | null;
  action_payload?: Record<string, unknown> | null;
}

export interface LeadMessage {
  id: number;
  role: "user" | "lead" | "system";
  content: string;
  proposed_workflow_id: number | null;
  cancelled: boolean;
  created_at: string;
  metadata?: {
    event?: "run_event" | "run_complete" | "run_failed";
    run_id?: number;
    workflow_id?: number;
    workflow_name?: string;
  } | null;
}

export interface LeadThread {
  thread_id: string;
  title: string | null;
  status: string;
  updated_at: string;
  msg_count: number;
}

export const AgentsAPI = {
  list: () => api.get<Agent[]>("/agents"),
  get: (id: number) => api.get<Agent & { queue_depth: number; running_task: unknown }>(`/agents/${id}`),
  create: (data: Partial<Agent>) => api.post<{ id: number }>("/agents", data),
  update: (id: number, data: Partial<Agent>) => api.put<{ ok: true }>(`/agents/${id}`, data),
  delete: (id: number) => api.del<{ ok: true }>(`/agents/${id}`),
  queue: (id: number) => api.get<unknown[]>(`/agents/${id}/queue`),
  skills: (id: number) => api.get<unknown[]>(`/agents/${id}/skills`),
  chat: (id: number, message: string, thread_id?: string) =>
    api.post<LeadChatResponse>(`/agents/${id}/chat`, { message, thread_id }),
  chatWithSignal: async (
    id: number,
    message: string,
    thread_id: string | undefined,
    signal: AbortSignal
  ): Promise<LeadChatResponse> => {
    const res = await fetch(`/api/agents/${id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ message, thread_id }),
      signal,
    });
    if (!res.ok) {
      throw new ApiError(res.status, `${res.status} ${res.statusText}`);
    }
    return (await res.json()) as LeadChatResponse;
  },
  threads: (id: number) => api.get<LeadThread[]>(`/agents/${id}/threads`),
  runs: (id: number) =>
    api.get<Array<{
      id: number;
      workflow_id: number;
      workflow_name: string;
      status: string;
      started_at: string;
      finished_at: string | null;
      total_cost_usd: number;
      total_input_tokens: number;
      total_output_tokens: number;
      my_steps: number;
    }>>(`/agents/${id}/runs`),
};

export const WorkflowsAPI = {
  list: (scope?: "mine" | "templates") =>
    api.get<Workflow[]>(`/workflows${scope ? `?scope=${scope}` : ""}`),
  get: (id: number) => api.get<Workflow>(`/workflows/${id}`),
  clone: (id: number, name?: string) =>
    api.post<{ id: number }>(`/workflows/${id}/clone`, name ? { name } : {}),
  run: (id: number, input: string, priority: string = "normal", thread_id?: string) =>
    api.post<{ run_id: number; status: string }>(`/workflows/${id}/run`, { input, priority, thread_id }),
  runs: (id: number) =>
    api.get<Array<{
      id: number;
      status: string;
      started_at: string;
      finished_at: string | null;
      total_cost_usd: number;
      total_input_tokens: number;
      total_output_tokens: number;
      iterations: number;
      trigger_source: string;
    }>>(`/workflows/${id}/runs`),
};

export interface Group {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  mode: "parallel" | "sequential";
  aggregator_agent_id: number | null;
  member_count?: number;
  members?: GroupMember[];
}

export interface GroupMember {
  id: number;
  agent_id: number;
  position: number;
  custom_prompt: string | null;
  agent_name?: string;
  role_title?: string | null;
  avatar_config?: Record<string, string>;
}

export const GroupsAPI = {
  list: () => api.get<Group[]>("/groups"),
  get: (id: number) => api.get<Group>(`/groups/${id}`),
  create: (data: {
    name: string;
    description?: string;
    mode: "parallel" | "sequential";
    aggregator_agent_id?: number | null;
    member_agent_ids?: number[];
  }) => api.post<{ id: number }>("/groups", data),
  update: (id: number, data: Partial<Group> & { member_agent_ids?: number[] }) =>
    api.put<{ ok: true }>(`/groups/${id}`, data),
  delete: (id: number) => api.del<{ ok: true }>(`/groups/${id}`),
  chatThread: (id: number) =>
    api.get<{ thread_id: number }>(`/groups/${id}/chat/thread`),
};

export interface GroupChatMessage {
  id: number;
  role: "user" | "agent";
  agent_id: number | null;
  agent_name?: string | null;
  avatar_config?: Record<string, string> | null;
  content: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export interface ProjectMember {
  id?: number;
  agent_id: number;
  daily_alloc_pct: number;
  monthly_alloc_pct: number;
  agent_name?: string;
  role_title?: string | null;
  avatar_config?: Record<string, string>;
}

export interface Project {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  goal: string | null;
  status: "active" | "paused" | "done" | "archived";
  coordinator_agent_id: number | null;
  created_at: string;
  updated_at: string;
  member_count?: number;
  today_cost?: number;
  runs_count?: number;
  members?: ProjectMember[];
  recent_runs?: Array<{
    id: number;
    status: string;
    started_at: string;
    finished_at: string | null;
    total_cost_usd: number;
    workflow_name: string | null;
  }>;
}

export const ProjectsAPI = {
  list: (status?: string) =>
    api.get<Project[]>(`/projects${status ? `?status=${status}` : ""}`),
  get: (id: number) => api.get<Project>(`/projects/${id}`),
  create: (data: Partial<Project> & { members?: ProjectMember[] }) =>
    api.post<{ id: number }>("/projects", data),
  update: (id: number, data: Partial<Project> & { members?: ProjectMember[] }) =>
    api.put<{ ok: true }>(`/projects/${id}`, data),
  delete: (id: number) => api.del<{ ok: true }>(`/projects/${id}`),
  chatThread: (id: number) =>
    api.get<{ thread_id: string }>(`/projects/${id}/chat/thread`),
  chatMessages: (id: number) =>
    api.get<{ thread_id: string; messages: any[] }>(`/projects/${id}/chat/messages`),
  chatSend: (id: number, message: string, thread_id?: string) =>
    api.post<{
      thread_id: string;
      response: string;
      proposed_workflow: any;
      proposed_workflow_id: number | null;
    }>(`/projects/${id}/chat`, { message, thread_id }),
};

export interface UsageDailyRow {
  date: string;
  key: number | null;
  label: string;
  tokens: number;
  cost: number;
}

export const UsageAPI = {
  daily: (params: {
    group_by: "project" | "agent" | "group" | "workflow";
    days?: number;
    project_id?: number | "null";
    agent_id?: number;
    workflow_id?: number;
  }) => {
    const qs = new URLSearchParams();
    qs.set("group_by", params.group_by);
    if (params.days) qs.set("days", String(params.days));
    if (params.project_id !== undefined) qs.set("project_id", String(params.project_id));
    if (params.agent_id !== undefined) qs.set("agent_id", String(params.agent_id));
    if (params.workflow_id !== undefined) qs.set("workflow_id", String(params.workflow_id));
    return api.get<{ group_by: string; days: number; rows: UsageDailyRow[] }>(
      `/usage/daily?${qs.toString()}`,
    );
  },
};

export const GroupChatAPI = {
  messages: (threadId: number) =>
    api.get<{ thread_id: number; group_id: number; messages: GroupChatMessage[] }>(
      `/group-chat/${threadId}/messages`,
    ),
  send: (threadId: number, message: string) =>
    api.post<{
      user_message: GroupChatMessage;
      replies: GroupChatMessage[];
      mode: "parallel" | "sequential";
    }>(`/group-chat/${threadId}/send`, { message }),
  continueRounds: (threadId: number, rounds: number) =>
    api.post<{
      replies: GroupChatMessage[];
      rounds: number;
      mode: "parallel" | "sequential";
    }>(`/group-chat/${threadId}/continue`, { rounds }),
};

export interface RunListItem extends Run {
  workflow_name?: string;
}

export interface RunListPage {
  runs: RunListItem[];
  has_more: boolean;
}

export const RunsAPI = {
  list: (opts?: { before_id?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (opts?.limit !== undefined) qs.set("limit", String(opts.limit));
    if (opts?.before_id !== undefined) qs.set("before_id", String(opts.before_id));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return api.get<RunListPage>(`/runs${suffix}`);
  },
  get: (id: number) =>
    api.get<Run & { steps: unknown[]; tasks: unknown[]; workflow_name?: string }>(
      `/runs/${id}`,
    ),
  stop: (id: number) => api.post<{ ok: true }>(`/runs/${id}/stop`),
};

export type LeadChatResponse = {
  thread_id: string;
  response: string;
  proposed_workflow: unknown;
  cost_usd: number;
  tokens: number;
};

export const LeadAPI = {
  chat: (message: string, thread_id?: string) =>
    api.post<LeadChatResponse>("/lead/chat", { message, thread_id }),
  chatWithSignal: async (
    message: string,
    thread_id: string | undefined,
    signal: AbortSignal
  ): Promise<LeadChatResponse> => {
    const res = await fetch(`/api/lead/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ message, thread_id }),
      signal,
    });
    if (!res.ok) {
      throw new ApiError(res.status, `${res.status} ${res.statusText}`);
    }
    return (await res.json()) as LeadChatResponse;
  },
  threads: () => api.get<LeadThread[]>("/lead/threads"),
  messages: (thread_id: string, opts?: { before_id?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (opts?.limit !== undefined) qs.set("limit", String(opts.limit));
    if (opts?.before_id !== undefined) qs.set("before_id", String(opts.before_id));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return api.get<{ messages: LeadMessage[]; has_more: boolean }>(
      `/lead/threads/${thread_id}/messages${suffix}`,
    );
  },
  archive: (thread_id: string) => api.post<{ ok: true }>(`/lead/threads/${thread_id}/archive`),
  pendingCount: () => api.get<{ count: number }>("/lead/pending_count"),
};

export const NotificationsAPI = {
  list: (status?: string) =>
    api.get<Notification[]>(`/notifications${status ? `?status=${status}` : ""}`),
  unreadCount: () => api.get<{ count: number }>("/notifications/unread_count"),
  markRead: (id: number) => api.post<{ ok: true }>(`/notifications/${id}/read`),
  markAllRead: () => api.post<{ ok: true; marked: number }>(`/notifications/mark_all_read`),
  resolve: (id: number, resolution: string) =>
    api.post<{ ok: true }>(`/notifications/${id}/resolve`, { resolution }),
  dismiss: (id: number) => api.post<{ ok: true }>(`/notifications/${id}/dismiss`),
};

export interface AgentLoadRow {
  id: number;
  name: string;
  role_title: string;
  status: string;
  queue_depth: number;
  max_queue_depth: number;
  today_cost: number;
  is_lead: boolean;
  avatar_config: Record<string, string>;
}

export interface LoadHeatmapRow {
  id: number;
  name: string;
  role_title: string | null;
  is_lead: boolean;
  avatar_config: Record<string, string>;
  values: number[];
}

export const DashboardAPI = {
  summary: () =>
    api.get<{
      active_agents: number;
      total_queue_depth: number;
      today_cost_usd: number;
      today_runs: number;
    }>("/dashboard/summary"),
  agentLoad: () => api.get<AgentLoadRow[]>("/dashboard/agent_load"),
  loadHeatmap: (buckets = 24) =>
    api.get<{ buckets: number; bucket_hours: number; agents: LoadHeatmapRow[] }>(
      `/dashboard/load_heatmap?buckets=${buckets}`,
    ),
};

export type UserRole = "admin" | "user";

export interface MeResponse {
  authenticated: boolean;
  id?: number;
  username?: string;
  display_name?: string;
  default_lead_agent_id?: number | null;
  role?: UserRole;
  language?: string;
}

export const AuthAPI = {
  me: () => api.get<MeResponse>("/me"),
  login: (username: string, password: string) =>
    api.post<{ id: number; username: string; display_name: string; role: UserRole }>(
      "/login",
      { username, password },
    ),
  logout: () => api.post<{ ok: true }>("/logout"),
  updateDisplayName: (display_name: string) =>
    api.put<{ ok: true }>("/me", { display_name }),
  updatePassword: (old_password: string, new_password: string) =>
    api.put<{ ok: true }>("/me/password", { old_password, new_password }),
};


// ============================================================================
// Admin — user management (Phase 1.2)
// ============================================================================

export interface AdminUserRow {
  id: number;
  username: string;
  display_name: string | null;
  role: UserRole;
  last_seen_at: string | null;
  created_at: string;
}

export const AdminUsersAPI = {
  list: () => api.get<AdminUserRow[]>("/admin/users"),
  create: (data: {
    username: string;
    password: string;
    display_name?: string;
    role?: UserRole;
  }) => api.post<{ id: number; username: string; role: UserRole }>("/admin/users", data),
  update: (id: number, data: { display_name?: string; role?: UserRole }) =>
    api.put<AdminUserRow>(`/admin/users/${id}`, data),
  remove: (id: number) => api.del<{ ok: true }>(`/admin/users/${id}`),
  resetPassword: (id: number, new_password: string) =>
    api.post<{ ok: true }>(`/admin/users/${id}/reset_password`, { new_password }),
};


// ============================================================================
// System feature flags (Phase 1.3)
// ============================================================================

export interface FeatureFlag {
  feature: string;
  label: string;
  description: string | null;
  admin_only: boolean;
  updated_at?: string;
}

export const FeatureFlagsAPI = {
  list: () => api.get<FeatureFlag[]>("/system/feature_flags"),
  setAdminOnly: (feature: string, admin_only: boolean) =>
    api.put<FeatureFlag>(`/system/feature_flags/${feature}`, { admin_only }),
};


// ============================================================================
// Lead proxy-answer (Phase 5.1 / 5.2)
// ============================================================================

export interface ProxyResponseRow {
  id: number;
  thread_id: string;
  content: string;
  metadata: Record<string, unknown>;
  cancelled: boolean;
  created_at: string;
  thread_agent_id: number | null;
}

export interface ProxySettings {
  lead_proxy_enabled?: boolean;
  lead_proxy_timeout_minutes?: number;
  lead_proxy_away_minutes?: number;
}

export const LeadProxyAPI = {
  list: () => api.get<ProxyResponseRow[]>("/lead/proxy_responses"),
  retract: (msg_id: number) =>
    api.post<{ ok: true }>(`/lead/proxy_responses/${msg_id}/retract`),
  settings: () => api.get<ProxySettings>("/lead/proxy_settings"),
  updateSettings: (data: {
    enabled?: boolean;
    timeout_minutes?: number;
    away_minutes?: number;
  }) => api.put<{ ok: true }>("/lead/proxy_settings", data),
};


// ============================================================================
// Audit log (Phase 5.3)
// ============================================================================

export interface AuditEntry {
  id: number;
  user_id: number | null;
  username: string | null;
  method: string;
  path: string;
  status_code: number | null;
  resource_id: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export const AuditAPI = {
  list: (opts?: { before_id?: number; limit?: number; user_id?: number; method?: string }) => {
    const qs = new URLSearchParams();
    if (opts?.limit !== undefined) qs.set("limit", String(opts.limit));
    if (opts?.before_id !== undefined) qs.set("before_id", String(opts.before_id));
    if (opts?.user_id !== undefined) qs.set("user_id", String(opts.user_id));
    if (opts?.method !== undefined) qs.set("method", opts.method);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return api.get<{ entries: AuditEntry[]; has_more: boolean }>(`/audit_log${suffix}`);
  },
};


// ============================================================================
// User quota (Phase 5.4)
// ============================================================================

export interface UserQuotaRow {
  daily_token_limit: number | null;
  daily_cost_limit_usd: number | null;
  monthly_token_limit: number | null;
  monthly_cost_limit_usd: number | null;
}

export interface UserQuotaSummary {
  quota: UserQuotaRow;
  daily: { tokens: number; cost_usd: number };
  monthly: { tokens: number; cost_usd: number };
}

export const UserQuotasAPI = {
  mine: () => api.get<UserQuotaSummary>("/me/quota"),
  getFor: (user_id: number) => api.get<UserQuotaSummary>(`/admin/users/${user_id}/quota`),
  setFor: (user_id: number, data: Partial<UserQuotaRow>) =>
    api.put<UserQuotaRow>(`/admin/users/${user_id}/quota`, data),
};


// ============================================================================
// Asset library (Phase 2) — Skill / Tool / MCP / RAG
// ============================================================================

export type AssetKind = "skill" | "tool" | "mcp" | "rag";

export interface AssetRow {
  id: number;
  kind: AssetKind;
  name: string;
  description: string | null;
  owner_user_id: number;
  owner_username?: string;
  owner_display_name?: string | null;
  enabled: boolean;
  config: Record<string, unknown>;
  metadata: Record<string, unknown>;
  has_credential: boolean;
  created_at?: string;
  updated_at?: string;
  grant_count?: number;
  assigned_agent_count?: number;
  total_calls?: number;
  last_used_at?: string | null;
}

export interface AssetGrant {
  id: number;
  asset_id: number;
  grantee_user_id: number;
  grantee_username: string;
  grantee_display_name: string | null;
  granted_by: number;
  created_at: string;
}

export interface AssetAuditRow {
  id: number;
  asset_id: number;
  actor_user_id: number | null;
  actor_username: string | null;
  action: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  created_at: string;
}

export interface AssetUsageSummary {
  summary: {
    total_calls: number;
    distinct_users: number;
    distinct_agents: number;
    last_used_at: string | null;
  };
  timeseries: { bucket: string; n: number }[];
}

export interface CreateAssetInput {
  kind: AssetKind;
  name: string;
  description?: string;
  config?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  credential?: string;
  enabled?: boolean;
}

export const AssetsAPI = {
  list: (kind?: AssetKind) =>
    api.get<AssetRow[]>(`/assets${kind ? `?kind=${kind}` : ""}`),
  get: (id: number) => api.get<AssetRow>(`/assets/${id}`),
  create: (data: CreateAssetInput) =>
    api.post<{ id: number }>("/assets", data),
  update: (id: number, data: Partial<CreateAssetInput> & { clear_credential?: boolean }) =>
    api.put<AssetRow>(`/assets/${id}`, data),
  remove: (id: number) => api.del<{ ok: true }>(`/assets/${id}`),

  // grants
  listGrants: (id: number) => api.get<AssetGrant[]>(`/assets/${id}/grants`),
  grant: (id: number, grantee_user_id: number) =>
    api.post<{ id: number }>(`/assets/${id}/grants`, { grantee_user_id }),
  revoke: (id: number, grantee_user_id: number) =>
    api.del<{ ok: boolean }>(`/assets/${id}/grants/${grantee_user_id}`),

  // agent assignments
  listAgentsForAsset: (id: number) =>
    api.get<Array<{ id: number; agent_id: number; agent_name: string; enabled: boolean; created_at: string }>>(
      `/assets/${id}/agents`,
    ),
  listAssetsForAgent: (agent_id: number, kind?: AssetKind) =>
    api.get<AssetRow[]>(
      `/agents/${agent_id}/assets${kind ? `?kind=${kind}` : ""}`,
    ),
  assignToAgent: (agent_id: number, asset_id: number) =>
    api.post<{ id: number }>(`/agents/${agent_id}/assets`, { asset_id }),
  unassignFromAgent: (agent_id: number, asset_id: number) =>
    api.del<{ ok: boolean }>(`/agents/${agent_id}/assets/${asset_id}`),

  // audit + usage
  audit: (id: number) => api.get<AssetAuditRow[]>(`/assets/${id}/audit`),
  usage: (id: number, hours = 24) =>
    api.get<AssetUsageSummary>(`/assets/${id}/usage?hours=${hours}`),

  // RAG
  ragIngest: (id: number, data: { source_name: string; text: string; metadata?: Record<string, unknown> }) =>
    api.post<{ chunks_ingested: number }>(`/assets/${id}/rag/ingest`, data),
  ragSearch: (id: number, query: string, top_k = 5) =>
    api.post<{ hits: Array<{ id: number; source_name: string; chunk_index: number; content: string; score: number; metadata: Record<string, unknown> }> }>(
      `/assets/${id}/rag/search`,
      { query, top_k },
    ),
};

// Avatar helpers — routed through same-origin /api/avatar for the web
// console, but the desktop shell needs them rewritten to the sidecar's
// dynamic port. Desktop sets `window.__HOLONS_API_BASE__` at startup; we
// prefix here so <img src> works without requiring each caller to wrap.
declare global {
  interface Window {
    __HOLONS_API_BASE__?: string;
  }
}
function _avatarBase(): string {
  if (typeof window !== "undefined" && window.__HOLONS_API_BASE__) {
    return window.__HOLONS_API_BASE__;
  }
  return "";
}

export interface AvatarConfig {
  body_type?: string;
  body?: string;
  hair?: string;
  face?: string;
  facial_hair?: string;
  accessory?: string;
  bg?: string;
}

export function bustUrl(cfg: AvatarConfig | Record<string, string> = {}, withVb = false): string {
  const params = new URLSearchParams();
  params.set("body_type", (cfg as any).body_type || "body_bust");
  params.set("body", (cfg as any).body || "Shirt");
  params.set("hair", (cfg as any).hair || "Medium");
  params.set("face", (cfg as any).face || "Calm");
  if ((cfg as any).facial_hair) params.set("facial_hair", (cfg as any).facial_hair);
  if ((cfg as any).accessory) params.set("accessory", (cfg as any).accessory);
  if ((cfg as any).bg) params.set("bg", (cfg as any).bg);
  if (withVb) params.set("vb", "0,-100,850,1300");
  return `${_avatarBase()}/api/avatar/compose?${params.toString()}`;
}

/**
 * Circular head-shot URL. Centers on the face (eyes/nose/mouth) so it
 * stays stable across hair styles. Measured via getBBox() in a browser:
 * FACE_TRANSFORM + HEAD_TRANSFORM places the face bbox center at canvas
 * (527, 334) regardless of hair. The 560×560 crop starts at (247, 54)
 * so the face lands at the square center; hair top (y≈62) is fully
 * visible for short/medium hair, while very long hair clips a bit on
 * the left — acceptable for a thumbnail.
 *
 * We also pass an explicit square w/h: without that, the SVG defaults
 * to 850×1200 intrinsic size and preserveAspectRatio letterboxes the
 * square viewBox inside a tall frame, knocking the head off-center.
 */
export function headUrl(
  cfg: AvatarConfig | Record<string, string> = {},
  bg: string = "eaeaea",
): string {
  const params = new URLSearchParams();
  params.set("body_type", (cfg as any).body_type || "body_bust");
  params.set("body", (cfg as any).body || "Shirt");
  params.set("hair", (cfg as any).hair || "Medium");
  params.set("face", (cfg as any).face || "Calm");
  if ((cfg as any).facial_hair) params.set("facial_hair", (cfg as any).facial_hair);
  if ((cfg as any).accessory) params.set("accessory", (cfg as any).accessory);
  params.set("bg", bg);
  // vb center = (120+350, -60+350) = (470, 290). The 700×700 window sizes
  // the head to ~70% of the rendered circle (measured head bbox ≈ 476px
  // vertical / 545px horizontal, ÷ 700 ≈ 68%/78%), leaving a comfortable
  // gray padding around the portrait — closer to a formal profile photo.
  // The x-center (470) is a compromise between face-features center (527)
  // and head-shape center (~440). Measured via getBBox() across Medium/
  // Long/Short/Bald hair so every avatar lands consistently.
  params.set("vb", "120,-60,700,700");
  params.set("w", "700");
  params.set("h", "700");
  return `${_avatarBase()}/api/avatar/compose?${params.toString()}`;
}

export function thumbUrl(category: string, name: string): string {
  return `${_avatarBase()}/api/avatar/thumb/${category}/${encodeURIComponent(name)}`;
}

export const AvatarAPI = {
  parts: () =>
    api.get<
      Record<string, { label: string; required: boolean; parts: string[] }>
    >("/avatar/parts"),
};


// ============================================================================
// Model clients (Phase 7)
// ============================================================================

export type ModelClientKind =
  | "bedrock"
  | "claude_native"
  | "openai"
  | "azure_openai"
  | "gemini"
  | "minimax"
  | "local";

export interface ModelClientKindSchema {
  kind: ModelClientKind;
  label: string;
  credential_fields: string[];
  config_fields: string[];
  hint: string;
}

export interface ModelClientModelEntry {
  id: string;
  label?: string;
  price_in?: number;
  price_out?: number;
}

export interface ModelClientRow {
  id: number;
  name: string;
  kind: ModelClientKind;
  description?: string | null;
  config: {
    region?: string;
    base_url?: string;
    endpoint?: string;
    api_version?: string;
    organization?: string;
    group_id?: string;
    models?: ModelClientModelEntry[];
    deployments?: ModelClientModelEntry[];
    [k: string]: unknown;
  };
  has_credential: boolean;
  enabled: boolean;
  default_for_new_users: boolean;
  created_by?: number | null;
  created_at?: string;
  updated_at?: string;
  grant_count?: number;
  agent_count?: number;
}

export interface ModelClientGrantRow {
  id: number;
  client_id: number;
  user_id: number;
  username: string;
  display_name: string | null;
  granted_by: number | null;
  created_at: string;
}

export interface CreateModelClientInput {
  name: string;
  kind: ModelClientKind;
  description?: string;
  config: Record<string, unknown>;
  credential?: Record<string, string>;
  enabled?: boolean;
  default_for_new_users?: boolean;
}

export const ModelClientsAPI = {
  kinds: () => api.get<ModelClientKindSchema[]>("/model_clients/kinds"),
  list: () => api.get<ModelClientRow[]>("/model_clients"),
  get: (id: number) => api.get<ModelClientRow>(`/model_clients/${id}`),
  create: (data: CreateModelClientInput) =>
    api.post<{ id: number }>("/model_clients", data),
  update: (
    id: number,
    data: Partial<CreateModelClientInput> & { clear_credential?: boolean },
  ) => api.put<ModelClientRow>(`/model_clients/${id}`, data),
  remove: (id: number) => api.del<{ ok: true }>(`/model_clients/${id}`),

  listGrants: (id: number) =>
    api.get<ModelClientGrantRow[]>(`/model_clients/${id}/grants`),
  grant: (id: number, user_id: number) =>
    api.post<{ ok: true }>(`/model_clients/${id}/grants`, { user_id }),
  revoke: (id: number, user_id: number) =>
    api.del<{ ok: true }>(`/model_clients/${id}/grants/${user_id}`),
};
