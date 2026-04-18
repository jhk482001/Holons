/* AgentStudio SPA */
(function () {
  "use strict";

  const API = "/api";
  const state = {
    user: null,
    agents: [],
    groups: [],
    workflows: [],
    runs: [],
    models: [],
    stage: { scope: null, agents: [], activeAgent: null, tab: "chat" },
    editing: null,
  };

  // ------------ utils ------------
  function md(text) {
    if (!text) return "";
    const raw = marked.parse(String(text), { breaks: true, gfm: true });
    return DOMPurify.sanitize(raw);
  }

  function esc(s) {
    return $("<div>").text(s == null ? "" : String(s)).html();
  }

  function avatarUrl(agent) {
    let cfg = {};
    try {
      cfg = typeof agent.avatar_config === "string"
        ? JSON.parse(agent.avatar_config || "{}")
        : (agent.avatar_config || {});
    } catch (e) { cfg = {}; }
    const seed = encodeURIComponent(cfg.seed || agent.name || "agent" + agent.id);
    const params = ["seed=" + seed];
    if (cfg.flip) params.push("flip=true");
    if (cfg.backgroundColor) params.push("backgroundColor=" + cfg.backgroundColor);
    return "https://api.dicebear.com/7.x/open-peeps/svg?" + params.join("&");
  }

  function apiGet(path) {
    return $.ajax({ url: API + path, method: "GET", xhrFields: { withCredentials: true } });
  }
  function apiSend(path, method, data) {
    return $.ajax({
      url: API + path, method, xhrFields: { withCredentials: true },
      contentType: "application/json", data: JSON.stringify(data || {}),
    });
  }

  // ------------ view routing ------------
  function show(view) {
    $(".view").addClass("hidden");
    $("#view-" + view).removeClass("hidden");
    $("#topnav .tabs button").removeClass("active");
    $('#topnav .tabs button[data-view="' + view + '"]').addClass("active");

    if (view === "agents") loadAgents();
    if (view === "groups") loadGroups();
    if (view === "workflows") loadWorkflows();
    if (view === "runs") loadRuns();
    if (view === "stage") loadStage();
  }

  // ------------ auth ------------
  async function checkAuth() {
    try {
      const r = await apiGet("/me");
      if (r.authenticated) {
        state.user = r.user;
        $("#who-label").text(r.user.display_name || r.user.username);
        $("#topnav").removeClass("hidden");
        show("agents");
      } else {
        state.user = null;
        $(".view").addClass("hidden");
        $("#view-login").removeClass("hidden");
      }
    } catch (e) {
      $("#view-login").removeClass("hidden");
    }
  }

  $(document).on("click", "#btn-login", async () => {
    const u = $("#li-username").val(), p = $("#li-password").val();
    try {
      const r = await apiSend("/login", "POST", { username: u, password: p });
      state.user = r;
      $("#who-label").text(r.display_name || r.username);
      show("agents");
    } catch (e) {
      $("#login-msg").text((e.responseJSON && e.responseJSON.error) || "登入失敗");
    }
  });
  $(document).on("click", "#btn-register", async () => {
    const u = $("#li-username").val(), p = $("#li-password").val();
    try {
      const r = await apiSend("/register", "POST", { username: u, password: p });
      state.user = r;
      $("#who-label").text(r.display_name || r.username);
      show("agents");
    } catch (e) {
      $("#login-msg").text((e.responseJSON && e.responseJSON.error) || "註冊失敗");
    }
  });
  $(document).on("click", "#btn-logout", async () => {
    await apiSend("/logout", "POST", {});
    state.user = null;
    location.reload();
  });

  $(document).on("click", "#topnav .tabs button", function () {
    show($(this).data("view"));
  });

  // ------------ models ------------
  async function ensureModels() {
    if (state.models.length) return state.models;
    state.models = await apiGet("/models");
    return state.models;
  }

  // ------------ agents ------------
  async function loadAgents() {
    state.agents = await apiGet("/agents");
    await ensureModels();
    const $list = $("#agents-list").empty();
    if (!state.agents.length) {
      $list.append('<div class="card"><em>還沒有 agent — 點右上「新 Agent」開始</em></div>');
      return;
    }
    state.agents.forEach((a) => {
      const $c = $(`
        <div class="card" data-id="${a.id}">
          <div class="head">
            <div class="avatar"><img src="${avatarUrl(a)}" alt=""></div>
            <div>
              <h4>${esc(a.name)}</h4>
              <div class="role">${esc(a.role_title || "")}</div>
              <div class="role" style="font-size:11px">${esc(a.model_id || "")}</div>
            </div>
          </div>
          <div class="desc">${esc((a.description || "").slice(0, 120))}</div>
          <div class="actions">
            <button class="ghost" data-act="edit">編輯</button>
            <button class="ghost" data-act="chat">對話</button>
            <button class="danger" data-act="del">刪除</button>
          </div>
        </div>`);
      $list.append($c);
    });
  }

  $(document).on("click", "#btn-new-agent", () => openAgentModal(null));
  $(document).on("click", '#agents-list [data-act="edit"]', function () {
    const id = $(this).closest(".card").data("id");
    openAgentModal(state.agents.find((a) => a.id === id));
  });
  $(document).on("click", '#agents-list [data-act="del"]', async function () {
    if (!confirm("確定刪除?")) return;
    const id = $(this).closest(".card").data("id");
    await $.ajax({ url: API + "/agents/" + id, method: "DELETE", xhrFields: { withCredentials: true } });
    loadAgents();
  });
  $(document).on("click", '#agents-list [data-act="chat"]', function () {
    const id = $(this).closest(".card").data("id");
    show("stage");
    setTimeout(() => openOverlayForAgent(id), 200);
  });

  function openAgentModal(agent) {
    ensureModels().then((models) => {
      const isNew = !agent;
      agent = agent || { name: "", role_title: "", description: "", system_prompt: "", few_shot: "", model_id: "claude-sonnet-4.6", avatar_config: {} };
      let avatarCfg = {};
      try { avatarCfg = typeof agent.avatar_config === "string" ? JSON.parse(agent.avatar_config || "{}") : agent.avatar_config || {}; } catch (e) {}
      if (!avatarCfg.seed) avatarCfg.seed = agent.name || "agent";
      const modelOpts = models.map((m) => `<option value="${m.key}" ${m.key === agent.model_id ? "selected" : ""}>${esc(m.label)}</option>`).join("");
      $("#modal-title").text(isNew ? "新增 Agent" : "編輯 Agent");
      $("#modal-body").html(`
        <div class="row2">
          <label>名稱<input id="ag-name" value="${esc(agent.name || "")}"></label>
          <label>職稱 / 角色<input id="ag-role" value="${esc(agent.role_title || "")}"></label>
        </div>
        <label>描述 / 背景<textarea id="ag-desc">${esc(agent.description || "")}</textarea></label>
        <label>System Prompt<textarea id="ag-sys" style="min-height:120px">${esc(agent.system_prompt || "")}</textarea></label>
        <label>Few-shot 範例 (optional)<textarea id="ag-fs" style="min-height:80px">${esc(agent.few_shot || "")}</textarea></label>
        <div class="row2">
          <label>模型<select id="ag-model">${modelOpts}</select></label>
          <label>頭像種子 (Open Peeps)<div style="display:flex;gap:8px;align-items:center"><input id="ag-seed" value="${esc(avatarCfg.seed)}" style="flex:1"><button id="ag-rand" class="ghost">隨機</button></div></label>
        </div>
        <div style="display:flex;justify-content:center;margin:10px"><img id="ag-preview" src="${avatarUrl({ id: agent.id, name: agent.name, avatar_config: avatarCfg })}" style="width:140px;height:140px;border-radius:50%;background:#2b3340"></div>
      `);
      $("#modal-root").removeClass("hidden");
      state.editing = { type: "agent", id: agent.id };

      $("#ag-rand").on("click", (e) => {
        e.preventDefault();
        const s = Math.random().toString(36).slice(2, 10);
        $("#ag-seed").val(s);
        $("#ag-preview").attr("src", avatarUrl({ id: 0, name: s, avatar_config: { seed: s } }));
      });
      $("#ag-seed").on("input", () => {
        const s = $("#ag-seed").val();
        $("#ag-preview").attr("src", avatarUrl({ id: 0, name: s, avatar_config: { seed: s } }));
      });
    });
  }

  async function saveAgent() {
    const payload = {
      name: $("#ag-name").val(),
      role_title: $("#ag-role").val(),
      description: $("#ag-desc").val(),
      system_prompt: $("#ag-sys").val(),
      few_shot: $("#ag-fs").val(),
      model_id: $("#ag-model").val(),
      avatar_config: { seed: $("#ag-seed").val() },
    };
    if (state.editing.id) {
      await apiSend("/agents/" + state.editing.id, "PUT", payload);
    } else {
      await apiSend("/agents", "POST", payload);
    }
    closeModal();
    loadAgents();
  }

  // ------------ groups ------------
  function avatarRowHtml(agents, opts) {
    opts = opts || {};
    const limit = opts.limit || 6;
    const list = agents.slice(0, limit);
    const more = agents.length - list.length;
    let html = '<div class="avatar-row">';
    list.forEach((a, i) => {
      const cls = opts.crownIds && opts.crownIds.includes(a.id) ? " crown" : "";
      html += `<div class="mini${cls}" title="${esc(a.name)}"><img src="${avatarUrl(a)}"></div>`;
    });
    if (more > 0) html += `<div class="label">+${more}</div>`;
    if (opts.label) html += `<div class="label">${esc(opts.label)}</div>`;
    return html + "</div>";
  }

  function getAgent(id) { return state.agents.find((a) => a.id === id); }

  async function loadGroups() {
    state.agents = await apiGet("/agents");
    state.groups = await apiGet("/groups");
    const $list = $("#groups-list").empty();
    if (!state.groups.length) {
      $list.append('<div class="card"><em>還沒有群組 — 點右上建立</em></div>');
      return;
    }
    state.groups.forEach((g) => {
      const agg = getAgent(g.aggregator_agent_id);
      const memberAgents = (g.members || []).map((m) => getAgent(m.agent_id)).filter(Boolean);
      const allShown = agg ? [agg, ...memberAgents] : memberAgents;
      const $c = $(`
        <div class="card" data-id="${g.id}">
          <h4>${esc(g.name)}</h4>
          <div class="role">模式: ${esc(g.mode)} ｜ 彙整: ${esc(agg ? agg.name : "無")}</div>
          ${avatarRowHtml(allShown, { crownIds: agg ? [agg.id] : [], label: `${memberAgents.length} 成員` })}
          <div class="desc">${esc((g.description || "").slice(0, 100))}</div>
          <div class="actions">
            <button class="primary" data-act="edit">視覺編輯</button>
            <button class="danger" data-act="del">刪除</button>
          </div>
        </div>`);
      $list.append($c);
    });
  }

  $(document).on("click", "#btn-new-group", () => openGroupEditor(null));
  $(document).on("click", '#groups-list [data-act="edit"]', function () {
    const id = $(this).closest(".card").data("id");
    openGroupEditor(state.groups.find((g) => g.id === id));
  });
  $(document).on("click", '#groups-list [data-act="del"]', async function () {
    if (!confirm("確定刪除?")) return;
    const id = $(this).closest(".card").data("id");
    await $.ajax({ url: API + "/groups/" + id, method: "DELETE", xhrFields: { withCredentials: true } });
    loadGroups();
  });

  // ----- visual group editor -----
  state.ged = { id: null, name: "", description: "", mode: "parallel", aggId: null, members: [] };

  async function openGroupEditor(group) {
    if (!state.agents || !state.agents.length) state.agents = await apiGet("/agents");
    state.ged = group ? {
      id: group.id, name: group.name || "", description: group.description || "",
      mode: group.mode || "parallel",
      aggId: group.aggregator_agent_id || null,
      members: (group.members || []).map((m) => ({ agent_id: m.agent_id, custom_prompt: m.custom_prompt || "" })),
    } : { id: null, name: "", description: "", mode: "parallel", aggId: null, members: [] };
    show("group-editor");
    $("#ged-name").val(state.ged.name);
    $("#ged-mode").val(state.ged.mode);
    renderGedPalette();
    renderGedBoard();
  }

  function renderGedPalette() {
    const $p = $("#ged-palette-list").empty();
    const memberIds = new Set(state.ged.members.map((m) => m.agent_id));
    const usedAgg = state.ged.aggId;
    state.agents.forEach((a) => {
      const inUse = memberIds.has(a.id) || usedAgg === a.id;
      $p.append(`
        <div class="palette-card" draggable="true" data-id="${a.id}" style="${inUse ? "opacity:.45" : ""}">
          <div class="av"><img src="${avatarUrl(a)}"></div>
          <div class="nm"><strong>${esc(a.name)}</strong><small>${esc(a.role_title || "")}</small></div>
        </div>`);
    });
  }

  function renderGedBoard() {
    const $agg = $("#ged-agg");
    if (state.ged.aggId) {
      const a = getAgent(state.ged.aggId);
      $agg.addClass("has-agent").html(`
        <button class="clear-x" data-clear>×</button>
        <div class="agg-label">👑 彙整 Aggregator</div>
        <div class="filled">
          <div class="av"><img src="${avatarUrl(a)}"></div>
          <div class="nm"><strong>${esc(a.name)}</strong><small>${esc(a.role_title || "")}</small></div>
        </div>
      `);
    } else {
      $agg.removeClass("has-agent").html(`
        <div class="agg-label">👑 彙整 Aggregator</div>
        <div class="agg-empty">把一位 agent 拖到這裡</div>
      `);
    }
    const $m = $("#ged-members").empty().toggleClass("sequential", state.ged.mode === "sequential");
    state.ged.members.forEach((m, idx) => {
      const a = getAgent(m.agent_id);
      if (!a) return;
      $m.append(`
        <div class="member-chip" data-idx="${idx}">
          <button class="x" data-remove>×</button>
          <div class="av"><img src="${avatarUrl(a)}"></div>
          <div class="nm"><strong>${esc(a.name)}</strong><small>${esc(a.role_title || "")}</small></div>
          <textarea placeholder="(可選) prompt 前綴" data-prompt>${esc(m.custom_prompt || "")}</textarea>
        </div>`);
    });
  }

  // drag from palette
  $(document).on("dragstart", "#ged-palette-list .palette-card", function (e) {
    const id = $(this).data("id");
    e.originalEvent.dataTransfer.setData("text/plain", String(id));
    $(this).addClass("dragging");
  });
  $(document).on("dragend", "#ged-palette-list .palette-card", function () { $(this).removeClass("dragging"); });
  $(document).on("dragover", "#ged-members, #ged-agg", function (e) { e.preventDefault(); $(this).addClass("drag-over"); });
  $(document).on("dragleave", "#ged-members, #ged-agg", function () { $(this).removeClass("drag-over"); });
  $(document).on("drop", "#ged-members", function (e) {
    e.preventDefault(); $(this).removeClass("drag-over");
    const id = parseInt(e.originalEvent.dataTransfer.getData("text/plain"));
    if (!id) return;
    if (state.ged.aggId === id) return;
    if (state.ged.members.some((m) => m.agent_id === id)) return;
    state.ged.members.push({ agent_id: id, custom_prompt: "" });
    renderGedBoard(); renderGedPalette();
  });
  $(document).on("drop", "#ged-agg", function (e) {
    e.preventDefault(); $(this).removeClass("drag-over");
    const id = parseInt(e.originalEvent.dataTransfer.getData("text/plain"));
    if (!id) return;
    state.ged.members = state.ged.members.filter((m) => m.agent_id !== id);
    state.ged.aggId = id;
    renderGedBoard(); renderGedPalette();
  });

  $(document).on("click", "#ged-agg [data-clear]", () => {
    state.ged.aggId = null;
    renderGedBoard(); renderGedPalette();
  });
  $(document).on("click", "#ged-members [data-remove]", function () {
    const idx = +$(this).closest(".member-chip").data("idx");
    state.ged.members.splice(idx, 1);
    renderGedBoard(); renderGedPalette();
  });
  $(document).on("input", "#ged-members [data-prompt]", function () {
    const idx = +$(this).closest(".member-chip").data("idx");
    state.ged.members[idx].custom_prompt = $(this).val();
  });
  $(document).on("change", "#ged-mode", function () {
    state.ged.mode = $(this).val();
    renderGedBoard();
  });

  $(document).on("click", "#ged-back", () => show("groups"));
  $(document).on("click", "#ged-save", async () => {
    const payload = {
      name: $("#ged-name").val(),
      description: state.ged.description || "",
      mode: state.ged.mode,
      aggregator_agent_id: state.ged.aggId,
      members: state.ged.members,
    };
    if (!payload.name) { alert("請輸入群組名稱"); return; }
    if (state.ged.id) {
      await apiSend("/groups/" + state.ged.id, "PUT", payload);
    } else {
      await apiSend("/groups", "POST", payload);
    }
    show("groups");
  });

  // ------------ workflows ------------
  function workflowAgents(w) {
    const ids = new Set();
    (w.nodes || []).forEach((n) => {
      if (n.node_type === "agent" && n.agent_id) ids.add(n.agent_id);
      if (n.node_type === "group" && n.group_id) {
        const g = state.groups.find((x) => x.id === n.group_id);
        if (g) {
          (g.members || []).forEach((m) => ids.add(m.agent_id));
          if (g.aggregator_agent_id) ids.add(g.aggregator_agent_id);
        }
      }
    });
    return Array.from(ids).map(getAgent).filter(Boolean);
  }

  async function loadWorkflows() {
    state.agents = await apiGet("/agents");
    state.groups = await apiGet("/groups");
    state.workflows = await apiGet("/workflows");
    const $list = $("#workflows-list").empty();
    if (!state.workflows.length) {
      $list.append('<div class="card"><em>還沒有工作流</em></div>');
      return;
    }
    state.workflows.forEach((w) => {
      const nCount = (w.nodes || []).length;
      const cast = workflowAgents(w);
      const $c = $(`
        <div class="card" data-id="${w.id}">
          <h4>${esc(w.name)}</h4>
          <div class="role">${nCount} 個節點 ${w.loop_enabled ? "｜ 迴圈最多 " + w.max_loops + " 次" : ""}</div>
          ${avatarRowHtml(cast, { label: cast.length + " agents" })}
          <div class="desc">${esc((w.description || "").slice(0, 120))}</div>
          <div class="actions">
            <button class="primary" data-act="edit">視覺編輯</button>
            <button class="ghost" data-act="run">執行</button>
            <button class="danger" data-act="del">刪除</button>
          </div>
        </div>`);
      $list.append($c);
    });
  }

  $(document).on("click", "#btn-new-workflow", () => openWfEditor(null));
  $(document).on("click", '#workflows-list [data-act="edit"]', function () {
    const id = $(this).closest(".card").data("id");
    openWfEditor(state.workflows.find((w) => w.id === id));
  });
  $(document).on("click", '#workflows-list [data-act="del"]', async function () {
    if (!confirm("確定刪除?")) return;
    const id = $(this).closest(".card").data("id");
    await $.ajax({ url: API + "/workflows/" + id, method: "DELETE", xhrFields: { withCredentials: true } });
    loadWorkflows();
  });
  $(document).on("click", '#workflows-list [data-act="run"]', async function () {
    const id = $(this).closest(".card").data("id");
    const input = prompt("輸入初始 prompt / 劇本大綱：", "");
    if (input == null) return;
    alert("已送出執行，請到「紀錄」查看進度。");
    apiSend("/workflows/" + id + "/run", "POST", { input }).then(
      (r) => {
        alert("執行完成: run #" + r.run_id);
        show("runs");
      },
      (e) => alert("執行失敗: " + JSON.stringify(e.responseJSON || e.statusText))
    );
  });

  // ----- visual workflow editor -----
  state.wf = {
    id: null, name: "", description: "",
    loop_enabled: false, max_loops: 1, loop_prompt: "",
    nodes: [], selected: null,
  };

  async function openWfEditor(wf) {
    if (!state.agents.length) state.agents = await apiGet("/agents");
    if (!state.groups.length) state.groups = await apiGet("/groups");
    state.wf = wf ? {
      id: wf.id,
      name: wf.name || "",
      description: wf.description || "",
      loop_enabled: !!wf.loop_enabled,
      max_loops: wf.max_loops || 1,
      loop_prompt: wf.loop_prompt || "",
      nodes: (wf.nodes || []).map((n, i) => ({
        node_type: n.node_type,
        agent_id: n.agent_id,
        group_id: n.group_id,
        prompt_template: n.prompt_template || "",
        label: n.label || "",
        pos_x: n.pos_x || (120 + i * 280),
        pos_y: n.pos_y || 200,
        _key: "n" + Math.random().toString(36).slice(2, 9),
      })),
      selected: null,
    } : {
      id: null, name: "", description: "",
      loop_enabled: false, max_loops: 1, loop_prompt: "",
      nodes: [], selected: null,
    };
    show("wf-editor");
    $("#wfed-name").val(state.wf.name);
    $("#wfed-loop").prop("checked", state.wf.loop_enabled);
    $("#wfed-maxloops").val(state.wf.max_loops);
    $("#wfed-side").addClass("hidden");
    renderWfCanvas();
  }

  function layoutGroupInner(group) {
    const memberAgents = (group.members || []).map((m) => getAgent(m.agent_id)).filter(Boolean);
    const agg = getAgent(group.aggregator_agent_id);
    const memW = 170, memH = 50, gap = 14, aggW = 200, aggH = 60;
    if (group.mode === "sequential") {
      const positions = [];
      let x = 10;
      memberAgents.forEach((a, i) => {
        positions.push({ kind: "member", agent: a, x, y: 10, w: memW, h: memH });
        x += memW + 36;
      });
      let aggPos = null;
      if (agg) aggPos = { kind: "agg", agent: agg, x, y: 8, w: aggW, h: aggH };
      const totalW = (aggPos ? aggPos.x + aggW : x) + 16;
      const totalH = Math.max(memH, aggH) + 24;
      return { positions, aggPos, w: totalW, h: totalH, mode: "sequential" };
    } else {
      // parallel: members in a column on left, agg on right
      const totalH = Math.max(memH + 24, memberAgents.length * (memH + gap) + 14);
      const positions = [];
      memberAgents.forEach((a, i) => {
        positions.push({ kind: "member", agent: a, x: 12, y: 12 + i * (memH + gap), w: memW, h: memH });
      });
      let aggPos = null;
      const aggX = memW + 12 + 90;
      if (agg) aggPos = { kind: "agg", agent: agg, x: aggX, y: (totalH - aggH) / 2, w: aggW, h: aggH };
      const totalW = (aggPos ? aggPos.x + aggW : memW + 24) + 16;
      return { positions, aggPos, w: totalW, h: totalH, mode: "parallel" };
    }
  }

  function renderExpandedGroup(group, node) {
    const layout = layoutGroupInner(group);
    let chips = "";
    const headerBtns = `
      <button class="node-edit-btn" data-edit title="編輯">⚙</button>
    `;
    layout.positions.forEach((p) => {
      const a = p.agent;
      chips += `<div class="inner-member" style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px">
        <div class="av"><img src="${avatarUrl(a)}"></div>
        <div class="nm"><strong>${esc(a.name)}</strong><small>${esc(a.role_title || "")}</small></div>
      </div>`;
    });
    if (layout.aggPos) {
      const a = layout.aggPos.agent;
      chips += `<div class="inner-agg" style="left:${layout.aggPos.x}px;top:${layout.aggPos.y}px;width:${layout.aggPos.w}px;height:${layout.aggPos.h}px">
        <div class="av"><img src="${avatarUrl(a)}"></div>
        <div class="nm"><strong>${esc(a.name)}</strong><small>aggregator</small></div>
      </div>`;
    }
    // build inner SVG arrows
    const NS = "http://www.w3.org/2000/svg";
    const w = layout.w, h = layout.h;
    let svg = `<svg class="inner-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <defs>
        <marker id="iarr-${node._key}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#7aa2ff"/>
        </marker>
      </defs>`;
    const path = (x1, y1, x2, y2) => {
      const dx = Math.max(20, (x2 - x1) / 2);
      svg += `<path d="M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}" stroke="#7aa2ff" stroke-width="2" fill="none" marker-end="url(#iarr-${node._key})"/>`;
    };
    if (layout.mode === "sequential") {
      // chain: m1 → m2 → ... → agg
      const points = layout.positions.concat(layout.aggPos ? [layout.aggPos] : []);
      for (let i = 0; i < points.length - 1; i++) {
        const a = points[i], b = points[i + 1];
        path(a.x + a.w, a.y + a.h / 2, b.x, b.y + b.h / 2);
      }
    } else if (layout.aggPos) {
      // parallel: each member → agg
      layout.positions.forEach((p) => {
        path(p.x + p.w, p.y + p.h / 2, layout.aggPos.x, layout.aggPos.y + layout.aggPos.h / 2);
      });
    }
    svg += "</svg>";

    const title = node.label || group.name;
    return `
      ${headerBtns}
      <div class="nd-type-tag">GROUP · ${esc(group.mode)} <span class="hint">(點擊收合)</span></div>
      <div class="nd-title">${esc(title)}</div>
      <div class="group-inner" style="width:${w}px;height:${h}px">
        ${svg}
        ${chips}
      </div>
    `;
  }

  function renderWfCanvas() {
    const $c = $("#wfed-canvas");
    $c.find(".wf-node").remove();

    // START + END pseudo nodes
    const startX = 60, startY = 80;
    $c.append(`<div class="wf-node start" data-key="__start" style="left:${startX}px;top:${startY}px">START</div>`);

    state.wf.nodes.forEach((n) => {
      let inner = "";
      let expandedCls = "";
      if (n.node_type === "agent") {
        const a = getAgent(n.agent_id);
        inner = `
          <button class="node-edit-btn" data-edit title="編輯">⚙</button>
          <div class="nd-type-tag">AGENT</div>
          <div class="nd-title">${esc(n.label || (a ? a.name : "未指定"))}</div>
          <div class="nd-body">
            <div class="agent-bust"><img src="${a ? avatarUrl(a) : ""}"></div>
            <div class="nd-meta"><strong>${esc(a ? a.name : "(請選擇)")}</strong>${esc(a ? a.role_title || "" : "")}</div>
          </div>`;
      } else {
        const g = state.groups.find((x) => x.id === n.group_id);
        const expanded = !!n._expanded;
        if (expanded) expandedCls = " expanded";
        if (!expanded || !g) {
          // collapsed group view
          const memberAgents = g ? (g.members || []).map((m) => getAgent(m.agent_id)).filter(Boolean) : [];
          const agg = g ? getAgent(g.aggregator_agent_id) : null;
          const all = agg ? [agg, ...memberAgents] : memberAgents;
          const minis = all.slice(0, 6).map((a) => `<div class="mini${agg && a.id === agg.id ? " crown" : ""}"><img src="${avatarUrl(a)}"></div>`).join("");
          const more = all.length > 6 ? `<div style="font-size:11px;color:var(--ink-dim);align-self:center">+${all.length - 6}</div>` : "";
          inner = `
            <button class="node-edit-btn" data-edit title="編輯">⚙</button>
            <div class="nd-type-tag">GROUP <span class="hint">(點擊展開)</span></div>
            <div class="nd-title">${esc(n.label || (g ? g.name : "未指定"))}</div>
            <div class="group-mode">${esc(g ? g.mode : "")}${agg ? " · 👑 " + esc(agg.name) : ""}</div>
            <div class="group-members">${minis}${more}</div>`;
        } else {
          // expanded inline group view
          inner = renderExpandedGroup(g, n);
        }
      }
      const cls = "wf-node " + (n.node_type === "group" ? "group-node" + expandedCls + " " : "") + (state.wf.selected === n._key ? "selected" : "");
      $c.append(`
        <div class="${cls}" data-key="${n._key}" style="left:${n.pos_x}px;top:${n.pos_y}px">
          <div class="wf-handle in"></div>
          ${inner}
          <div class="wf-handle out"></div>
        </div>
      `);
    });

    // END node — auto position to right of last
    const last = state.wf.nodes.length ? state.wf.nodes[state.wf.nodes.length - 1] : null;
    const endX = last ? last.pos_x + 320 : startX + 240;
    const endY = last ? last.pos_y : startY;
    $c.append(`<div class="wf-node end" data-key="__end" style="left:${endX}px;top:${endY}px">END</div>`);

    drawWfConnections();
  }

  function drawWfConnections() {
    const svg = document.getElementById("wfed-svg");
    svg.innerHTML = "";
    const $c = $("#wfed-canvas");
    svg.setAttribute("width", $c.width());
    svg.setAttribute("height", $c.height());

    const NS = "http://www.w3.org/2000/svg";
    const defs = document.createElementNS(NS, "defs");
    defs.innerHTML = `
      <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#7aa2ff"/>
      </marker>
      <marker id="arrow-loop" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#ff7a59"/>
      </marker>
    `;
    svg.appendChild(defs);

    const getEnds = (key) => {
      const el = document.querySelector(`.wf-node[data-key="${key}"]`);
      if (!el) return null;
      const x = el.offsetLeft, y = el.offsetTop, w = el.offsetWidth, h = el.offsetHeight;
      return { lx: x, ly: y + h / 2, rx: x + w, ry: y + h / 2 };
    };

    const drawArrow = (a, b, color = "#7aa2ff", marker = "arrow") => {
      const dx = Math.max(40, Math.abs(b.lx - a.rx) / 2);
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", `M ${a.rx} ${a.ry} C ${a.rx + dx} ${a.ry}, ${b.lx - dx} ${b.ly}, ${b.lx} ${b.ly}`);
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "2");
      path.setAttribute("fill", "none");
      path.setAttribute("marker-end", `url(#${marker})`);
      svg.appendChild(path);
    };

    // build sequence: start -> n0 -> n1 -> ... -> end
    const seq = ["__start", ...state.wf.nodes.map((n) => n._key), "__end"];
    for (let i = 0; i < seq.length - 1; i++) {
      const a = getEnds(seq[i]); const b = getEnds(seq[i + 1]);
      if (a && b) drawArrow(a, b);
    }

    // loop arrow: end → start (curved underneath)
    if (state.wf.loop_enabled && state.wf.nodes.length) {
      const aEnds = getEnds("__end");
      const bEnds = getEnds("__start");
      if (aEnds && bEnds) {
        const ay = aEnds.ry + 60, by = bEnds.ly - 0;
        const path = document.createElementNS(NS, "path");
        const midY = Math.max(aEnds.ry, bEnds.ly) + 180;
        path.setAttribute("d", `M ${aEnds.rx} ${aEnds.ry} C ${aEnds.rx + 80} ${midY}, ${bEnds.lx - 80} ${midY}, ${bEnds.lx} ${bEnds.ly + 4}`);
        path.setAttribute("stroke", "#ff7a59");
        path.setAttribute("stroke-width", "2");
        path.setAttribute("stroke-dasharray", "6 4");
        path.setAttribute("fill", "none");
        path.setAttribute("marker-end", "url(#arrow-loop)");
        svg.appendChild(path);

        const text = document.createElementNS(NS, "text");
        text.setAttribute("x", (aEnds.rx + bEnds.lx) / 2);
        text.setAttribute("y", midY + 20);
        text.setAttribute("fill", "#ff7a59");
        text.setAttribute("font-size", "12");
        text.setAttribute("text-anchor", "middle");
        text.textContent = `⟳ loop max ${state.wf.max_loops} 次`;
        svg.appendChild(text);
      }
    }
  }

  function addWfNode(type) {
    const last = state.wf.nodes[state.wf.nodes.length - 1];
    const x = last ? last.pos_x + 280 : 360;
    const y = last ? last.pos_y : 80;
    const n = {
      node_type: type,
      agent_id: type === "agent" ? (state.agents[0] && state.agents[0].id) : null,
      group_id: type === "group" ? (state.groups[0] && state.groups[0].id) : null,
      prompt_template: "", label: "",
      pos_x: x, pos_y: y,
      _key: "n" + Math.random().toString(36).slice(2, 9),
    };
    state.wf.nodes.push(n);
    state.wf.selected = n._key;
    renderWfCanvas();
    openSideForNode(n);
  }

  $(document).on("click", "#wfed-add-agent", () => addWfNode("agent"));
  $(document).on("click", "#wfed-add-group", () => addWfNode("group"));

  // node selection + side panel
  $(document).on("mousedown", "#wfed-canvas .wf-node", function (e) {
    const key = $(this).data("key");
    if (key === "__start" || key === "__end") return;
    const node = state.wf.nodes.find((n) => n._key === key);
    if (!node) return;
    state.wf.selected = key;
    $("#wfed-canvas .wf-node").removeClass("selected");
    $(this).addClass("selected dragging");
    const startX = e.pageX, startY = e.pageY;
    const origX = node.pos_x, origY = node.pos_y;
    let moved = false;
    const onMove = (ev) => {
      moved = true;
      node.pos_x = Math.max(0, origX + (ev.pageX - startX));
      node.pos_y = Math.max(0, origY + (ev.pageY - startY));
      $(this).css({ left: node.pos_x + "px", top: node.pos_y + "px" });
      drawWfConnections();
    };
    const onUp = () => {
      $(document).off("mousemove.wf mouseup.wf");
      $(this).removeClass("dragging");
      if (!moved && node.node_type === "group") {
        node._expanded = !node._expanded;
        renderWfCanvas();
      }
    };
    $(document).on("mousemove.wf", onMove).on("mouseup.wf", onUp);
    e.preventDefault();
  });

  // ⚙ edit button — opens side panel without expand toggle
  $(document).on("click", "#wfed-canvas .wf-node [data-edit]", function (e) {
    e.stopPropagation();
    const key = $(this).closest(".wf-node").data("key");
    const node = state.wf.nodes.find((n) => n._key === key);
    if (!node) return;
    state.wf.selected = key;
    $("#wfed-canvas .wf-node").removeClass("selected");
    $(this).closest(".wf-node").addClass("selected");
    openSideForNode(node);
  });
  $(document).on("mousedown touchstart", "#wfed-canvas .wf-node [data-edit]", function (e) {
    e.stopPropagation();
  });

  // touch support
  $(document).on("touchstart", "#wfed-canvas .wf-node", function (e) {
    const key = $(this).data("key");
    if (key === "__start" || key === "__end") return;
    const node = state.wf.nodes.find((n) => n._key === key);
    if (!node) return;
    state.wf.selected = key;
    $("#wfed-canvas .wf-node").removeClass("selected");
    $(this).addClass("selected dragging");
    const t = e.originalEvent.touches[0];
    const startX = t.pageX, startY = t.pageY;
    const origX = node.pos_x, origY = node.pos_y;
    let moved = false;
    const onMove = (ev) => {
      moved = true;
      const tt = ev.originalEvent.touches[0];
      node.pos_x = Math.max(0, origX + (tt.pageX - startX));
      node.pos_y = Math.max(0, origY + (tt.pageY - startY));
      $(this).css({ left: node.pos_x + "px", top: node.pos_y + "px" });
      drawWfConnections();
      ev.preventDefault();
    };
    const onUp = () => {
      $(document).off("touchmove.wf touchend.wf");
      $(this).removeClass("dragging");
      if (!moved && node.node_type === "group") {
        node._expanded = !node._expanded;
        renderWfCanvas();
      }
    };
    $(document).on("touchmove.wf", onMove).on("touchend.wf", onUp);
  });

  function openSideForNode(node) {
    $("#wfed-side").removeClass("hidden");
    $("#wfed-side-title").text(node.node_type === "agent" ? "Agent 節點" : "Group 節點");
    let html = "";
    if (node.node_type === "agent") {
      const opts = state.agents.map((a) => `<option value="${a.id}" ${a.id === node.agent_id ? "selected" : ""}>${esc(a.name)}</option>`).join("");
      html += `<label>選擇 Agent<select id="side-agent">${opts}</select></label>`;
    } else {
      const opts = state.groups.map((g) => `<option value="${g.id}" ${g.id === node.group_id ? "selected" : ""}>${esc(g.name)}</option>`).join("");
      html += `<label>選擇群組<select id="side-group">${opts}</select></label>`;
    }
    html += `<label>節點標籤<input id="side-label" value="${esc(node.label || "")}"></label>`;
    html += `<label>此節點 prompt 前綴 (optional)<textarea id="side-prompt">${esc(node.prompt_template || "")}</textarea></label>`;
    html += `<button class="danger" id="side-delete">🗑 刪除節點</button>`;
    $("#wfed-side-body").html(html);
  }

  $(document).on("change", "#side-agent", function () {
    const node = state.wf.nodes.find((n) => n._key === state.wf.selected);
    if (node) { node.agent_id = parseInt($(this).val()); renderWfCanvas(); }
  });
  $(document).on("change", "#side-group", function () {
    const node = state.wf.nodes.find((n) => n._key === state.wf.selected);
    if (node) { node.group_id = parseInt($(this).val()); renderWfCanvas(); }
  });
  $(document).on("input", "#side-label", function () {
    const node = state.wf.nodes.find((n) => n._key === state.wf.selected);
    if (node) { node.label = $(this).val(); renderWfCanvas(); }
  });
  $(document).on("input", "#side-prompt", function () {
    const node = state.wf.nodes.find((n) => n._key === state.wf.selected);
    if (node) { node.prompt_template = $(this).val(); }
  });
  $(document).on("click", "#side-delete", () => {
    state.wf.nodes = state.wf.nodes.filter((n) => n._key !== state.wf.selected);
    state.wf.selected = null;
    $("#wfed-side").addClass("hidden");
    renderWfCanvas();
  });
  $(document).on("click", "#wfed-side-close", () => $("#wfed-side").addClass("hidden"));

  $(document).on("change", "#wfed-loop", function () { state.wf.loop_enabled = $(this).is(":checked"); drawWfConnections(); });
  $(document).on("input", "#wfed-maxloops", function () { state.wf.max_loops = parseInt($(this).val() || 1); drawWfConnections(); });
  $(document).on("input", "#wfed-name", function () { state.wf.name = $(this).val(); });

  $(document).on("click", "#wfed-edit-loop", () => {
    const lp = prompt("迴圈時要附加的 prompt (插在前一輪結果之前):", state.wf.loop_prompt || "");
    if (lp != null) state.wf.loop_prompt = lp;
  });

  $(document).on("click", "#wfed-back", () => show("workflows"));

  $(document).on("click", "#wfed-save", async () => {
    if (!state.wf.name) { alert("請輸入工作流名稱"); return; }
    const payload = {
      name: state.wf.name,
      description: state.wf.description || "",
      loop_enabled: state.wf.loop_enabled,
      max_loops: state.wf.max_loops,
      loop_prompt: state.wf.loop_prompt || "",
      nodes: state.wf.nodes.map((n) => ({
        node_type: n.node_type,
        agent_id: n.agent_id,
        group_id: n.group_id,
        prompt_template: n.prompt_template,
        label: n.label,
        pos_x: Math.round(n.pos_x),
        pos_y: Math.round(n.pos_y),
      })),
    };
    if (state.wf.id) {
      await apiSend("/workflows/" + state.wf.id, "PUT", payload);
    } else {
      const r = await apiSend("/workflows", "POST", payload);
      state.wf.id = r.id;
    }
    alert("已儲存");
  });

  $(document).on("click", "#wfed-run", async () => {
    if (!state.wf.id) { alert("請先儲存工作流"); return; }
    const input = prompt("輸入初始 prompt / 劇本大綱：", "");
    if (input == null) return;
    alert("執行中… 完成後到「紀錄」查看");
    apiSend(`/workflows/${state.wf.id}/run`, "POST", { input }).then(
      (r) => alert("完成 — run #" + r.run_id),
      (e) => alert("失敗: " + JSON.stringify(e.responseJSON || e.statusText))
    );
  });

  // ------------ modal save dispatch ------------
  $(document).on("click", "#modal-close", closeModal);
  $(document).on("click", "#modal-save", () => {
    if (state.editing && state.editing.type === "agent") saveAgent();
  });
  function closeModal() { $("#modal-root").addClass("hidden"); state.editing = null; }

  // ------------ runs ------------
  async function loadRuns() {
    state.agents = await apiGet("/agents");
    state.workflows = await apiGet("/workflows");
    state.runs = await apiGet("/runs");
    const $wf = $("#run-filter-wf").empty().append('<option value="">全部工作流</option>');
    state.workflows.forEach((w) => $wf.append(`<option value="${w.id}">${esc(w.name)}</option>`));
    const $ag = $("#run-filter-agent").empty().append('<option value="">全部 Agent</option>');
    state.agents.forEach((a) => $ag.append(`<option value="${a.id}">${esc(a.name)}</option>`));
    renderRunsList();
  }

  function renderRunsList() {
    const wfId = +$("#run-filter-wf").val() || null;
    const $list = $("#runs-list").empty();
    state.runs.filter((r) => !wfId || r.workflow_id === wfId).forEach((r) => {
      const $row = $(`
        <div class="run-row" data-id="${r.id}">
          <div>
            <strong>Run #${r.id}</strong> — ${esc(r.workflow_name || "")}
            <div class="role">${esc(r.status)} ｜ ${r.iterations || 0} 迴圈 ｜ tokens ${r.total_input_tokens || 0}/${r.total_output_tokens || 0} ｜ $${(r.total_cost_usd || 0).toFixed(4)}</div>
          </div>
          <div class="role">${esc(r.started_at || "")}</div>
        </div>`);
      $list.append($row);
    });
  }
  $(document).on("change", "#run-filter-wf, #run-filter-agent", renderRunsList);

  $(document).on("click", "#runs-list .run-row", async function () {
    const id = $(this).data("id");
    const r = await apiGet("/runs/" + id);
    const agentFilter = +$("#run-filter-agent").val() || null;
    const steps = (r.steps || []).filter((s) => !agentFilter || s.agent_id === agentFilter);
    const $d = $("#run-detail").empty().removeClass("hidden");
    $d.append(`<h3>Run #${r.id} — ${esc(r.workflow_id)}</h3>`);
    $d.append(`<div class="meta">tokens ${r.total_input_tokens}/${r.total_output_tokens} ｜ $${(r.total_cost_usd || 0).toFixed(4)} ｜ ${r.total_duration_ms}ms</div>`);
    $d.append(`<h4>初始輸入</h4><div class="markdown">${md(r.initial_input)}</div>`);
    $d.append(`<h4>最終輸出</h4><div class="markdown">${md(r.final_output)}</div>`);
    $d.append(`<h4>步驟明細 (${steps.length})</h4>`);
    steps.forEach((s) => {
      const a = s.agent_id ? getAgent(s.agent_id) : null;
      const av = a ? `<img src="${avatarUrl(a)}" style="width:48px;height:48px;border-radius:50%;border:2px solid var(--border);background:#2b3340;flex-shrink:0">` : `<div style="width:48px;height:48px;border-radius:8px;background:#2b3340;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--ink-dim)">GROUP</div>`;
      $d.append(`
        <div class="step" style="display:flex;gap:12px">
          ${av}
          <div style="flex:1;min-width:0">
            <div><strong>${esc(s.role_label || "")}</strong> — ${esc(s.agent_name || "(group)")} <span class="meta">[iter ${s.iteration}, pos ${s.node_position}]</span></div>
            <div class="meta">${esc(s.model_id || "")} ｜ ${s.input_tokens}/${s.output_tokens} tok ｜ $${(s.cost_usd || 0).toFixed(5)} ｜ ${s.duration_ms}ms</div>
            <details><summary>prompt</summary><div class="markdown">${md(s.prompt)}</div></details>
            <details open><summary>response</summary><div class="markdown">${md(s.response)}</div></details>
            ${s.agent_id ? `<div class="actions"><button class="ghost" data-act="rate" data-sid="${s.id}">評分/建議</button> <button class="ghost" data-act="retrigger" data-sid="${s.id}">補充 prompt 再執行</button></div>` : ""}
          </div>
        </div>`);
    });
  });

  $(document).on("click", '#run-detail [data-act="rate"]', async function () {
    const sid = $(this).data("sid");
    const rating = prompt("評分 (1-5):", "4");
    if (rating == null) return;
    const suggestion = prompt("建議/筆記:", "") || "";
    await apiSend("/steps/" + sid + "/rate", "POST", { rating, suggestion });
    alert("已記錄。");
  });
  $(document).on("click", '#run-detail [data-act="retrigger"]', async function () {
    const sid = $(this).data("sid");
    const extra = prompt("補充指示 (會附在原 prompt 後):", "");
    if (extra == null) return;
    const r = await apiSend("/steps/" + sid + "/retrigger", "POST", { extra_prompt: extra });
    alert("完成 — 新步驟 #" + r.new_step_id);
  });

  // ------------ stage ------------
  async function loadStage() {
    state.agents = await apiGet("/agents");
    state.workflows = await apiGet("/workflows");
    state.groups = await apiGet("/groups");
    const $sel = $("#stage-scope").empty();
    $sel.append('<option value="">-- 選擇 --</option>');
    state.workflows.forEach((w) => $sel.append(`<option value="wf-${w.id}">工作流: ${esc(w.name)}</option>`));
    state.groups.forEach((g) => $sel.append(`<option value="gr-${g.id}">群組: ${esc(g.name)}</option>`));
    renderBusts([]);
    $("#stage-output").html('<em>選擇一個工作流或群組，下方會出現所有 agent；點他們可與其對話。</em>');
  }

  $(document).on("change", "#stage-scope", function () {
    const v = $(this).val();
    if (!v) { renderBusts([]); return; }
    const [kind, id] = v.split("-");
    let agentIds = [];
    if (kind === "wf") {
      const w = state.workflows.find((x) => x.id === +id);
      (w.nodes || []).forEach((n) => {
        if (n.node_type === "agent" && n.agent_id) agentIds.push(n.agent_id);
        if (n.node_type === "group" && n.group_id) {
          const g = state.groups.find((x) => x.id === n.group_id);
          if (g) {
            (g.members || []).forEach((m) => agentIds.push(m.agent_id));
            if (g.aggregator_agent_id) agentIds.push(g.aggregator_agent_id);
          }
        }
      });
    } else {
      const g = state.groups.find((x) => x.id === +id);
      if (g) {
        (g.members || []).forEach((m) => agentIds.push(m.agent_id));
        if (g.aggregator_agent_id) agentIds.push(g.aggregator_agent_id);
      }
    }
    const unique = Array.from(new Set(agentIds));
    const agents = unique.map((id) => state.agents.find((a) => a.id === id)).filter(Boolean);
    state.stage.agents = agents;
    state.stage.scope = v;
    renderBusts(agents);
  });

  function renderBusts(agents) {
    const $row = $("#stage-bust-row").empty();
    agents.forEach((a) => {
      $row.append(`
        <div class="bust" data-id="${a.id}">
          <div class="avatar"><img src="${avatarUrl(a)}"></div>
          <div class="name">${esc(a.name)}</div>
        </div>`);
    });
  }

  $(document).on("click", "#stage-bust-row .bust", function () {
    const id = +$(this).data("id");
    openOverlayForAgent(id);
  });

  async function openOverlayForAgent(id) {
    state.stage.activeAgent = id;
    $("#stage-bust-row .bust").addClass("dimmed").removeClass("active");
    $(`#stage-bust-row .bust[data-id="${id}"]`).removeClass("dimmed").addClass("active");
    const agent = state.agents.find((a) => a.id === id);
    $("#overlay-name").html(`<strong>${esc(agent.name)}</strong><span class="role"> — ${esc(agent.role_title || "")}</span>`);
    $("#stage-overlay").removeClass("hidden");
    state.stage.tab = "chat";
    $(".overlay-head .tabbtn").removeClass("active"); $('.overlay-head .tabbtn[data-tab="chat"]').addClass("active");
    await renderOverlayBody();
  }

  $(document).on("click", "#overlay-close", () => {
    $("#stage-overlay").addClass("hidden");
    $("#stage-bust-row .bust").removeClass("dimmed active");
    state.stage.activeAgent = null;
  });
  $(document).on("click", ".overlay-head .tabbtn", async function () {
    state.stage.tab = $(this).data("tab");
    $(".overlay-head .tabbtn").removeClass("active");
    $(this).addClass("active");
    await renderOverlayBody();
  });

  async function renderOverlayBody() {
    const aid = state.stage.activeAgent;
    const $b = $("#overlay-body").empty();
    if (state.stage.tab === "chat") {
      const history = await apiGet(`/agents/${aid}/chat`);
      history.forEach((c) => {
        $b.append(`<div class="bubble ${c.role}"><div class="meta">${c.role} ｜ ${esc(c.created_at || "")}</div><div class="markdown">${md(c.content)}</div>${c.image_url ? `<img src="${c.image_url}" style="max-width:260px;border-radius:8px;margin-top:6px">` : ""}</div>`);
      });
      $("#overlay-compose").show();
      $b.scrollTop($b[0].scrollHeight);
    } else {
      const steps = await apiGet(`/agents/${aid}/steps`);
      if (!steps.length) $b.append("<em>這位 agent 尚未在任何工作流中處理過。</em>");
      steps.forEach((s) => {
        $b.append(`
          <div class="bubble assistant">
            <div class="meta">${esc(s.role_label || "")} ｜ run #${s.run_id} iter ${s.iteration} ｜ ${esc(s.model_id || "")} ｜ ${s.input_tokens}/${s.output_tokens} tok ｜ $${(s.cost_usd || 0).toFixed(5)}</div>
            <details><summary>prompt</summary><div class="markdown">${md(s.prompt)}</div></details>
            <details open><summary>response</summary><div class="markdown">${md(s.response)}</div></details>
            <div class="actions" style="display:flex;gap:6px;margin-top:6px">
              <button class="ghost" data-stage-act="rate" data-sid="${s.id}">評分</button>
              <button class="ghost" data-stage-act="retrigger" data-sid="${s.id}">補充 prompt 重跑</button>
            </div>
          </div>`);
      });
      $("#overlay-compose").hide();
    }
  }

  $(document).on("click", '[data-stage-act="rate"]', async function () {
    const sid = $(this).data("sid");
    const rating = prompt("評分 (1-5):", "4");
    if (rating == null) return;
    const suggestion = prompt("建議/筆記:", "") || "";
    await apiSend("/steps/" + sid + "/rate", "POST", { rating, suggestion });
    alert("已記錄。");
  });
  $(document).on("click", '[data-stage-act="retrigger"]', async function () {
    const sid = $(this).data("sid");
    const extra = prompt("補充指示:", "");
    if (extra == null) return;
    const r = await apiSend("/steps/" + sid + "/retrigger", "POST", { extra_prompt: extra });
    alert("完成 — 新步驟 #" + r.new_step_id);
    renderOverlayBody();
  });

  let pendingImage = null;
  $(document).on("click", "#overlay-attach", () => $("#overlay-file").click());
  $(document).on("change", "#overlay-file", async function () {
    const f = this.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    const r = await $.ajax({ url: API + "/upload", method: "POST", data: fd, processData: false, contentType: false, xhrFields: { withCredentials: true } });
    pendingImage = r.url;
    alert("已附加圖片: " + r.filename);
  });

  $(document).on("click", "#overlay-send", async () => {
    const aid = state.stage.activeAgent;
    const msg = $("#overlay-msg").val();
    if (!msg) return;
    $("#overlay-send").prop("disabled", true).text("傳送中…");
    try {
      await apiSend(`/agents/${aid}/chat`, "POST", { message: msg, image_url: pendingImage });
      $("#overlay-msg").val("");
      pendingImage = null;
      await renderOverlayBody();
    } finally {
      $("#overlay-send").prop("disabled", false).text("送出");
    }
  });

  $(document).on("click", "#btn-stage-run", async () => {
    const v = $("#stage-scope").val();
    if (!v || !v.startsWith("wf-")) { alert("請先選擇一個工作流"); return; }
    const wid = v.substring(3);
    const input = $("#stage-input").val() || "";
    $("#stage-output").html("<em>執行中…</em>");
    try {
      const r = await apiSend(`/workflows/${wid}/run`, "POST", { input });
      $("#stage-output").html(md(r.final_output));
    } catch (e) {
      $("#stage-output").html("<pre>" + esc(JSON.stringify(e.responseJSON || e.statusText)) + "</pre>");
    }
  });

  // ------------ boot ------------
  $(checkAuth);
})();
