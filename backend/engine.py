"""Workflow execution engine (v2 — queue-based).

In v1 the engine was synchronous: a single call ran the entire workflow
in one function, using a ThreadPoolExecutor for parallel group members.

In v2 the engine is a *dispatcher*. It:
  1. Creates a run record
  2. Enqueues the FIRST workflow step(s) into the relevant agents' queues
  3. Returns immediately with the run_id

Workers then execute tasks and call back into `on_task_complete`, which
figures out the next step(s) and enqueues them. When all steps are done
(and loops are exhausted) the run is marked `done`.

Flow callbacks:
  execute_task(task, ctx)          ← called by worker to actually invoke LLM
  on_task_complete(task, result)    ← called by worker after successful execution
  on_task_failed(task, error)       ← called by worker after failure
  on_task_aborted(task, error)      ← called by worker after AbortTask (e.g. hot stop)
"""
from __future__ import annotations

import json
import logging
import time

from . import db, queue
from .llm_clients import invoke_for_agent as llm_invoke

log = logging.getLogger("agent_company.engine")


# ============================================================================
# Dispatch: create a run and enqueue the first step(s)
# ============================================================================

def dispatch_workflow(
    workflow_id: int,
    user_id: int,
    initial_input: str,
    *,
    trigger_source: str = "manual",
    trigger_context: dict | None = None,
    priority: str = "normal",
    project_id: int | None = None,
) -> int:
    """Kick off a workflow. Returns the new run_id immediately.

    The actual execution happens asynchronously via workers. Poll the
    run row to see when it finishes.

    If `project_id` is given, the run and every step it spawns are
    attributed to that project, and quota enforcement will check each
    agent's project allocation at dispatch time before enqueueing.
    """
    from .services import user_quotas
    user_quotas.check_dispatch(user_id)

    # Create the run record (with optional project attribution)
    run_id = db.execute_returning(
        """
        INSERT INTO runs (workflow_id, user_id, initial_input, status,
                          trigger_source, trigger_context, iterations,
                          project_id)
        VALUES (%s, %s, %s, 'running', %s, %s::jsonb, 1, %s)
        RETURNING id
        """,
        (workflow_id, user_id, initial_input, trigger_source,
         json.dumps(trigger_context or {}), project_id),
    )

    # Load the workflow's nodes (top-level only: parent_group_id IS NULL)
    nodes = db.fetch_all(
        """
        SELECT *
        FROM workflow_nodes
        WHERE workflow_id = %s AND parent_group_id IS NULL
        ORDER BY position ASC
        """,
        (workflow_id,),
    )
    if not nodes:
        db.execute(
            "UPDATE runs SET status='error', error_message='workflow has no nodes', finished_at=NOW() WHERE id=%s",
            (run_id,),
        )
        return run_id

    # Enqueue the first node. Both {{input}} and {{prev_output}} refer to
    # the same thing on the first node (the user's initial input).
    _enqueue_node(
        nodes[0], run_id,
        original_input=initial_input,
        prev_output=initial_input,
        priority=priority,
    )
    log.info("dispatched workflow %s → run %s", workflow_id, run_id)
    return run_id


def _enqueue_node(
    node: dict,
    run_id: int,
    *,
    original_input: str,
    prev_output: str,
    priority: str = "normal",
    iteration: int = 1,
) -> list[int]:
    """Enqueue a single workflow node. Returns the list of task_ids created.

    Agent node → 1 task
    Group node → N tasks (one per member, potentially in parallel)

    `original_input` is the initial run input (what the user typed). It
    remains stable across the whole run and is what `{{input}}` renders to.
    `prev_output` is the output of the immediately preceding node and is
    what `{{prev_output}}` renders to.
    """
    # Review nodes execute like agent nodes; the _advance_to_next_node
    # post-processor reads their output and routes accordingly.
    if node["node_type"] in ("agent", "review"):
        return [_enqueue_agent_node(
            node, run_id,
            original_input=original_input,
            prev_output=prev_output,
            priority=priority,
            iteration=iteration,
        )]
    elif node["node_type"] == "group":
        return _enqueue_group_node(
            node, run_id,
            original_input=original_input,
            prev_output=prev_output,
            priority=priority,
            iteration=iteration,
        )
    else:
        log.error("unknown node_type %s", node["node_type"])
        return []


def _enqueue_agent_node(
    node: dict, run_id: int,
    *,
    original_input: str,
    prev_output: str,
    priority: str,
    iteration: int,
) -> int:
    """Enqueue a single agent task."""
    payload = {
        "kind": "agent_node",
        "node_id": node["id"],
        "node_position": node["position"],
        "workflow_id": node["workflow_id"],
        "agent_id": node["agent_id"],
        "prompt": _render_prompt(node.get("prompt_template"), original_input, prev_output),
        "label": node.get("label"),
        "iteration": iteration,
        "original_input": original_input,
    }
    # Pass the system_prompt_override if the node has one
    if node.get("system_prompt_override"):
        payload["system_prompt_override"] = node["system_prompt_override"]
    return queue.enqueue_task(
        agent_id=node["agent_id"],
        payload=payload,
        run_id=run_id,
        priority=priority,
        source=f"workflow_{iteration}",
    )


def _enqueue_group_node(
    node: dict, run_id: int,
    *,
    original_input: str,
    prev_output: str,
    priority: str,
    iteration: int,
) -> list[int]:
    """Enqueue all members of a group. Aggregator runs later via on_task_complete."""
    group = db.fetch_one("SELECT * FROM groups_tbl WHERE id = %s", (node["group_id"],))
    if not group:
        return []
    members = db.fetch_all(
        "SELECT * FROM group_members WHERE group_id = %s ORDER BY position",
        (group["id"],),
    )

    task_ids = []
    for m in members:
        member_prompt = _render_prompt(
            m.get("custom_prompt") or node.get("prompt_template"),
            original_input,
            prev_output,
        )
        payload = {
            "kind": "group_member",
            "node_id": node["id"],
            "node_position": node["position"],
            "workflow_id": node["workflow_id"],
            "group_id": group["id"],
            "agent_id": m["agent_id"],
            "member_id": m["id"],
            "prompt": member_prompt,
            "label": node.get("label"),
            "iteration": iteration,
            "original_input": original_input,
        }
        if node.get("system_prompt_override"):
            payload["system_prompt_override"] = node["system_prompt_override"]
        tid = queue.enqueue_task(
            agent_id=m["agent_id"],
            payload=payload,
            run_id=run_id,
            priority=priority,
            source=f"group_{group['id']}",
        )
        task_ids.append(tid)
    return task_ids


def _render_prompt(template: str | None, original_input: str, prev_output: str) -> str:
    """Render a node's prompt template. {{input}} → original run input;
    {{prev_output}} → the immediately preceding node's output. If the
    template is empty, default to passing the prev_output through."""
    if not template:
        return prev_output
    return (
        template
        .replace("{{input}}", original_input)
        .replace("{{prev_output}}", prev_output)
    )


# ============================================================================
# Core task execution (called by worker through middleware pipeline)
# ============================================================================

MAX_TOOL_TURNS = 12  # safety cap so a bad agent loop can't run forever


def execute_task(task: dict, ctx: dict) -> dict:
    """Run the LLM for this task. If the agent has tools configured, enter
    a multi-turn loop (LLM → toolUse → execute → toolResult → LLM → ...);
    otherwise the single-shot text path runs (as before).

    Returns a result dict suitable for the worker / middleware. The final
    assistant text (summarised from the last turn) becomes the step's
    response and is passed as prev_output to the next workflow node.
    """
    payload = task["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    agent = db.fetch_one("SELECT * FROM agents WHERE id = %s", (task["agent_id"],))
    if not agent:
        raise RuntimeError(f"agent {task['agent_id']} not found")

    # Agent + project quota gate. We check right before the LLM call so a
    # task that's been queued for a while still respects freshly-updated
    # caps. `can_run` returns early on first breach so the reason is clear.
    run_row = db.fetch_one(
        "SELECT project_id FROM runs WHERE id = %s", (task["run_id"],)
    ) or {}
    project_id = run_row.get("project_id")
    from .services import quotas as _quotas_svc
    gate = _quotas_svc.can_run(agent["id"], project_id=project_id)
    if not gate["ok"]:
        raise RuntimeError(f"quota blocked: {gate['reason']}")

    prompt = payload.get("prompt", "")
    # Phase 8: workflow node can override the agent's system_prompt for
    # cross-domain tasks (e.g., a scriptwriter temporarily acting as a
    # marketing analyst). The override is set by Lead or by the user in
    # the workflow editor.
    system_prompt_override = payload.get("system_prompt_override")
    system_prompt = system_prompt_override or agent.get("system_prompt") or ""
    model_id = agent.get("primary_model_id") or "claude-sonnet-4.6"

    # Parse tool_config list: array of built-in tool names the agent may use
    raw_tools = agent.get("tool_config") or []
    if isinstance(raw_tools, str):
        try:
            raw_tools = json.loads(raw_tools)
        except Exception:
            raw_tools = []
    tool_names: list[str] = list(raw_tools) if isinstance(raw_tools, list) else []

    # Gather MCP tool specs — each entry carries its dispatch info
    mcp_tools: list[dict] = []
    try:
        from .services import mcp_client
        mcp_tools = mcp_client.gather_agent_tools(agent["id"])
    except Exception as e:  # noqa: BLE001
        log.warning("MCP gather failed for agent %s: %s", agent.get("id"), e)

    # Phase 2.6 — asset library integration. Resolve agent's assigned
    # assets and merge into the tool plumbing: MCP assets → mcp_tools,
    # RAG assets → synthetic search_kb tools, skill assets → system prompt
    # additions, tool assets → tool_names. Any failure is non-fatal so the
    # agent still runs with its legacy config if the library is misconfigured.
    asset_ctx: dict = {"mcp": [], "rag": [], "skill_snips": []}
    try:
        asset_ctx = _gather_agent_assets(agent["id"])
        mcp_tools.extend(asset_ctx["mcp"])
        if asset_ctx["skill_snips"]:
            system_prompt = (
                system_prompt + "\n\n" + "\n\n".join(asset_ctx["skill_snips"])
            ).strip()
        for tn in asset_ctx.get("tool_names") or []:
            if tn not in tool_names:
                tool_names.append(tn)
    except Exception as e:  # noqa: BLE001
        log.warning("asset gather failed for agent %s: %s", agent.get("id"), e)

    if tool_names or mcp_tools or asset_ctx["rag"]:
        return _execute_with_tools(task, payload, agent, prompt, system_prompt,
                                    model_id, tool_names, mcp_tools,
                                    rag_assets=asset_ctx["rag"])
    return _execute_single_turn(task, payload, agent, prompt, system_prompt, model_id)


def _gather_agent_assets(agent_id: int) -> dict:
    """Read agent_assets + asset_items for this agent and split into
    tool-loop ingredients. Returns:

        {
            "mcp":        [{server_name, url, auth_header, asset_id, name,
                             description, input_schema}, ...],  # MCP tools
                                                                # the LLM can call
            "rag":        [asset, ...],        # full asset rows for RAG sources
            "tool_names": [str, ...],          # built-in tool registry names
            "skill_snips":[str, ...],          # markdown to inject into prompt
        }

    The MCP shape is deliberately the same as
    `mcp_client.gather_agent_tools()` so the existing engine loop keeps
    working — we just merge into that list.
    """
    from .services import asset_crypto, mcp_client

    rows = db.fetch_all(
        """
        SELECT a.id, a.kind, a.name, a.description, a.config, a.metadata,
               a.credential_encrypted
        FROM agent_assets aa
        JOIN asset_items a ON a.id = aa.asset_id
        WHERE aa.agent_id = %s
          AND aa.enabled = TRUE
          AND a.enabled = TRUE
        """,
        (agent_id,),
    )
    out = {"mcp": [], "rag": [], "tool_names": [], "skill_snips": []}

    for row in rows:
        kind = row["kind"]
        cfg = row["config"] or {}
        if kind == "skill":
            snip = cfg.get("content_md") or cfg.get("prompt") or ""
            if snip:
                out["skill_snips"].append(f"### {row['name']}\n{snip}")
        elif kind == "tool":
            # Tool assets point at a built-in tool by module/fn. Accept
            # either {module, fn} or {name} shape; look up via the tool
            # registry name. For now the registry is keyed by function
            # name, so use config.fn if present.
            fn = cfg.get("fn") or cfg.get("name") or row["name"]
            if fn:
                out["tool_names"].append(fn)
        elif kind == "mcp":
            url = cfg.get("url")
            if not url:
                continue
            auth_header = asset_crypto.decrypt(row.get("credential_encrypted"))
            try:
                # Probe this MCP server's tool list at dispatch time.
                # gather_agent_tools() does this too for legacy per-agent
                # mcp_servers — reuse the underlying helper.
                for spec in mcp_client.list_tools(url, auth_header):
                    out["mcp"].append({
                        "server_name": row["name"].replace(" ", "_").lower(),
                        "url": url,
                        "auth_header": auth_header,
                        "asset_id": row["id"],
                        "name": spec.get("name"),
                        "description": spec.get("description", ""),
                        "input_schema": spec.get("inputSchema", {}),
                    })
            except Exception as e:  # noqa: BLE001
                log.warning("failed to list tools for MCP asset %s: %s", row["id"], e)
        elif kind == "rag":
            out["rag"].append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "config": cfg,
                "credential_encrypted": row.get("credential_encrypted"),
            })
    return out


def _execute_single_turn(task: dict, payload: dict, agent: dict,
                          prompt: str, system_prompt: str, model_id: str) -> dict:
    """Original 1-shot text path. Kept verbatim for back-compat: tool-less
    agents stay cheap and simple."""
    t0 = time.time()
    result = llm_invoke(
        agent_id=agent["id"],
        model_key=model_id,
        system_prompt=system_prompt,
        user_text=prompt,
    )
    duration_ms = int((time.time() - t0) * 1000)

    step_id = db.execute_returning(
        """
        INSERT INTO run_steps
            (run_id, iteration, node_position, group_id, agent_id, role_label,
             prompt, system_prompt, response, model_id, model_provider,
             input_tokens, output_tokens, cost_usd, duration_ms, error,
             project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                (SELECT project_id FROM runs WHERE id = %s))
        RETURNING id
        """,
        (
            task["run_id"],
            payload.get("iteration", 1),
            payload.get("node_position"),
            payload.get("group_id"),
            task["agent_id"],
            payload.get("label") or agent.get("role_title"),
            prompt,
            system_prompt,
            result.get("text", ""),
            result.get("model_id"),
            result.get("provider"),
            result.get("input_tokens", 0),
            result.get("output_tokens", 0),
            result.get("cost_usd", 0),
            duration_ms,
            result.get("error"),
            task["run_id"],
        ),
    )
    db.execute("UPDATE agent_tasks SET step_id = %s WHERE id = %s", (step_id, task["id"]))

    if result.get("error"):
        raise RuntimeError(f"LLM error: {result['error']}")

    return {
        "text": result.get("text", ""),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "cost_usd": result.get("cost_usd", 0),
        "duration_ms": duration_ms,
        "model_id": result.get("model_id"),
        "step_id": step_id,
    }


def _execute_with_tools(task: dict, payload: dict, agent: dict,
                         prompt: str, system_prompt: str, model_id: str,
                         tool_names: list[str],
                         mcp_tools: list[dict] | None = None,
                         rag_assets: list[dict] | None = None) -> dict:
    """Tool-aware multi-turn execution.

    Loop:
      1. Call LLM with the current messages + toolConfig
      2. Persist a run_step row for this turn (captures text + tool_calls)
      3. If stop_reason is 'tool_use', run each requested tool, append a
         user message with tool_results, and loop
      4. Otherwise return the aggregated result

    Each turn creates its own run_step row so the RunDetail page can show
    the full trace. All turns share the same task_id and run_id.
    """
    from . import tools as tool_registry
    from .services import mcp_client
    from .services import assets as assets_service
    from .services import rag as rag_service

    mcp_tools = mcp_tools or []
    rag_assets = rag_assets or []

    # Build the set of tool specs this agent may use. Built-in tools come
    # first, then MCP tools, then RAG synthetic search_kb tools.
    available_specs = list(tool_registry.get_specs(tool_names))
    # Map tool name → dispatch entry (MCP tools carry their server info)
    mcp_dispatch: dict[str, dict] = {}
    for mt in mcp_tools:
        name = mt.get("name")
        if not name:
            continue
        # Avoid collisions with built-in tools: prefix with mcp__<server>__
        prefixed = f"mcp__{mt['server_name']}__{name}"
        available_specs.append({
            "name": prefixed,
            "description": f"[{mt['server_name']}] {mt.get('description', '')}",
            "inputSchema": {"json": mt.get("input_schema") or {"type": "object"}},
        })
        mcp_dispatch[prefixed] = mt

    # RAG synthetic tools — one per assigned RAG asset. The LLM gets a
    # `search_kb_<N>` tool that takes a `query` and optional `top_k`, and
    # returns the retrieved chunks. We dispatch by tool name.
    rag_dispatch: dict[str, dict] = {}
    for ra in rag_assets:
        tool_name = f"search_kb_{ra['id']}"
        available_specs.append({
            "name": tool_name,
            "description": (
                f"Search knowledge base '{ra['name']}' for relevant chunks. "
                f"{(ra.get('description') or '').strip()}"
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "natural-language query"},
                        "top_k": {"type": "integer", "description": "max chunks to return", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        })
        rag_dispatch[tool_name] = ra

    if not available_specs:
        # Fallback: agent has tool_config but registry returned nothing.
        # Skip the loop and behave like a plain single-turn agent.
        return _execute_single_turn(task, payload, agent, prompt, system_prompt, model_id)

    tool_ctx = {
        "agent_id": agent["id"],
        "agent_user_id": agent["user_id"],
        "run_id": task["run_id"],
        "task_id": task["id"],
    }

    messages: list[dict] = [
        {"role": "user", "content": [{"text": prompt}]},
    ]

    total_in = total_out = 0
    total_cost = 0.0
    first_step_id: int | None = None
    final_text = ""
    t_total0 = time.time()

    for turn in range(1, MAX_TOOL_TURNS + 1):
        t0 = time.time()
        result = llm_invoke(
            agent_id=agent["id"],
            model_key=model_id,
            system_prompt=system_prompt,
            messages=messages,
            tool_config=available_specs,
        )
        turn_duration = int((time.time() - t0) * 1000)

        if result.get("error"):
            # Persist the error turn for traceability
            db.execute_returning(
                """
                INSERT INTO run_steps
                    (run_id, iteration, node_position, group_id, agent_id, role_label,
                     prompt, system_prompt, response, model_id, model_provider,
                     input_tokens, output_tokens, cost_usd, duration_ms, error,
                     turn, tool_calls, project_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                        (SELECT project_id FROM runs WHERE id = %s))
                RETURNING id
                """,
                (
                    task["run_id"], payload.get("iteration", 1),
                    payload.get("node_position"), payload.get("group_id"),
                    task["agent_id"],
                    payload.get("label") or agent.get("role_title"),
                    prompt if turn == 1 else f"(turn {turn})",
                    system_prompt, result.get("text", ""),
                    result.get("model_id"), result.get("provider"),
                    result.get("input_tokens", 0), result.get("output_tokens", 0),
                    result.get("cost_usd", 0), turn_duration,
                    result.get("error"), turn, "[]", task["run_id"],
                ),
            )
            raise RuntimeError(f"LLM error: {result['error']}")

        # Execute any requested tools, building the toolResult content
        tool_uses = result.get("tool_uses") or []
        tool_calls_log: list[dict] = []
        tool_result_blocks: list[dict] = []
        for tu in tool_uses:
            tool_use_id = tu.get("toolUseId")
            name = tu.get("name")
            inp = tu.get("input") or {}
            t_tool0 = time.time()
            tool_err = None
            status = "success"
            asset_id_for_usage: int | None = None
            try:
                if name in mcp_dispatch:
                    # Dispatch to the MCP server that owns this tool
                    mt = mcp_dispatch[name]
                    asset_id_for_usage = mt.get("asset_id")
                    # Strip the "mcp__<server>__" prefix before calling
                    original_name = name.split("__", 2)[-1]
                    mcp_out = mcp_client.call_tool(
                        mt["url"], original_name, inp, mt.get("auth_header"),
                    )
                    tool_out = mcp_out
                    if mcp_out.get("is_error"):
                        status = "error"
                        tool_err = mcp_out.get("text") or "mcp error"
                elif name in rag_dispatch:
                    # Synthetic RAG search tool
                    ra = rag_dispatch[name]
                    asset_id_for_usage = ra.get("id")
                    query = inp.get("query") or ""
                    top_k = int(inp.get("top_k") or 5)
                    hits = rag_service.search(ra, query, top_k=top_k)
                    tool_out = {"hits": hits, "count": len(hits)}
                else:
                    tool_out = tool_registry.call_tool(name, inp, tool_ctx)
            except Exception as ex:  # noqa: BLE001
                tool_out = {"error": str(ex)}
                tool_err = str(ex)
                status = "error"
            tool_duration = int((time.time() - t_tool0) * 1000)
            # Record usage if this call hit an asset (MCP or RAG). Failures
            # are swallowed inside record_usage so tracking can never break
            # a live run.
            if asset_id_for_usage is not None:
                assets_service.record_usage(
                    asset_id_for_usage,
                    agent["user_id"],
                    agent_id=agent["id"],
                    run_id=task["run_id"],
                    turn=turn,
                    duration_ms=tool_duration,
                    ok=(status == "success"),
                    error=tool_err,
                )
            tool_calls_log.append({
                "toolUseId": tool_use_id,
                "name": name,
                "input": inp,
                "output": tool_out,
                "error": tool_err,
                "duration_ms": tool_duration,
                "asset_id": asset_id_for_usage,
            })
            tool_result_blocks.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"text": json.dumps(tool_out, ensure_ascii=False)}],
                    "status": status,
                },
            })

        # Persist this turn's run_step
        step_id = db.execute_returning(
            """
            INSERT INTO run_steps
                (run_id, iteration, node_position, group_id, agent_id, role_label,
                 prompt, system_prompt, response, model_id, model_provider,
                 input_tokens, output_tokens, cost_usd, duration_ms, error,
                 turn, tool_calls, project_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    (SELECT project_id FROM runs WHERE id = %s))
            RETURNING id
            """,
            (
                task["run_id"], payload.get("iteration", 1),
                payload.get("node_position"), payload.get("group_id"),
                task["agent_id"],
                payload.get("label") or agent.get("role_title"),
                prompt if turn == 1 else f"(turn {turn} continuation)",
                system_prompt, result.get("text", ""),
                result.get("model_id"), result.get("provider"),
                result.get("input_tokens", 0), result.get("output_tokens", 0),
                result.get("cost_usd", 0), turn_duration, None,
                turn, json.dumps(tool_calls_log), task["run_id"],
            ),
        )
        if first_step_id is None:
            first_step_id = step_id

        total_in += result.get("input_tokens", 0) or 0
        total_out += result.get("output_tokens", 0) or 0
        total_cost += float(result.get("cost_usd", 0) or 0)
        final_text = result.get("text") or final_text

        # Append the assistant turn + any tool results to history
        messages.append(result.get("assistant_message") or {
            "role": "assistant",
            "content": [{"text": result.get("text", "")}],
        })
        if not tool_uses:
            break  # end_turn — done
        messages.append({"role": "user", "content": tool_result_blocks})

    else:
        # Loop exited via max turns without end_turn
        final_text = (final_text or "") + "\n\n[agent stopped: reached max tool-use turns]"

    # Link the first step to the task (the "anchor" step)
    if first_step_id is not None:
        db.execute("UPDATE agent_tasks SET step_id = %s WHERE id = %s",
                   (first_step_id, task["id"]))

    total_duration_ms = int((time.time() - t_total0) * 1000)
    return {
        "text": final_text,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_usd": round(total_cost, 6),
        "duration_ms": total_duration_ms,
        "model_id": model_id,
        "step_id": first_step_id,
    }


# ============================================================================
# Callbacks from worker
# ============================================================================

def on_task_complete(task: dict, result: dict) -> None:
    """Called by worker after successful task execution.

    Decides what to enqueue next:
    - If task was an agent_node → enqueue the next top-level workflow node
    - If task was a group_member → check if all members are done; if so,
      enqueue the aggregator (or the next node if no aggregator)
    - If this is the last node → handle loop or mark run done
    """
    payload = task["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    kind = payload.get("kind")
    run_id = task["run_id"]
    workflow_id = payload.get("workflow_id")

    # Check run status — could be cancelled mid-flight
    run = db.fetch_one("SELECT status FROM runs WHERE id = %s", (run_id,))
    if run and run["status"] in ("cancelling", "cancelled"):
        _maybe_finalize_cancelled(run_id)
        return

    if kind == "agent_node":
        _advance_to_next_node(
            workflow_id, run_id,
            from_position=payload["node_position"],
            original_input=payload.get("original_input", ""),
            output_text=result.get("text", ""),
            iteration=payload.get("iteration", 1),
        )
    elif kind == "group_member":
        _handle_group_member_done(task, payload, result)
    elif kind == "aggregator":
        _advance_to_next_node(
            workflow_id, run_id,
            from_position=payload["node_position"],
            original_input=payload.get("original_input", ""),
            output_text=result.get("text", ""),
            iteration=payload.get("iteration", 1),
        )


def on_task_failed(task: dict, error: Exception) -> None:
    """Task execution failed. Mark run as error (simple strategy for now).

    Future: retry policy, notifications, escalation.
    """
    run_id = task["run_id"]
    if run_id:
        db.execute(
            """
            UPDATE runs SET status='error', error_message=%s, finished_at=NOW()
            WHERE id = %s AND status = 'running'
            """,
            (str(error), run_id),
        )
        try:
            _notify_run_complete(run_id, str(error), failed=True)
        except Exception as e:
            log.exception("failed to notify run failure: %s", e)


def on_task_aborted(task: dict, error: Exception) -> None:
    """Task aborted (e.g. by hot stop). Don't treat as error."""
    run_id = task["run_id"]
    if run_id:
        _maybe_finalize_cancelled(run_id)


# ============================================================================
# Advance logic
# ============================================================================

def _advance_to_next_node(
    workflow_id: int, run_id: int,
    from_position: int,
    original_input: str,
    output_text: str, iteration: int,
) -> None:
    """Find the next top-level workflow node after `from_position` and enqueue it.

    If there's no next node, handle loop or mark run done.

    Review-loop extension: if the just-completed node was `node_type='review'`
    and the agent's output begins with `REVISE:` (case-insensitive), we
    re-enqueue the PREVIOUS position with the feedback as prev_output,
    capped by workflows.max_review_iterations per position.
    """
    # Inspect the node we just finished — was it a review?
    just_finished = db.fetch_one(
        "SELECT node_type FROM workflow_nodes WHERE workflow_id = %s "
        "AND parent_group_id IS NULL AND position = %s",
        (workflow_id, from_position),
    )
    if just_finished and just_finished.get("node_type") == "review":
        verdict_text = (output_text or "").lstrip()
        revise = verdict_text[:7].upper().startswith("REVISE")
        if revise:
            # Count how many revisions have already fired for the target step.
            prev_position = max(0, from_position - 1)
            past_iters_row = db.fetch_one(
                """
                SELECT COALESCE(MAX(iteration), 1) AS it
                FROM run_steps
                WHERE run_id = %s AND node_position = %s
                """,
                (run_id, prev_position),
            ) or {}
            past_iters = int(past_iters_row.get("it") or 1)
            wf = db.fetch_one("SELECT max_review_iterations FROM workflows WHERE id = %s",
                              (workflow_id,)) or {}
            cap = int(wf.get("max_review_iterations") or 2)
            if past_iters < cap:
                feedback = output_text.split(":", 1)[1].strip() if ":" in output_text else output_text
                target = db.fetch_one(
                    "SELECT * FROM workflow_nodes WHERE workflow_id = %s "
                    "AND parent_group_id IS NULL AND position = %s",
                    (workflow_id, prev_position),
                )
                if target:
                    revision_input = (
                        f"Coordinator asked you to revise. Their feedback:\n\n"
                        f"{feedback}\n\n"
                        f"Your previous attempt (for reference):\n\n{original_input}"
                    )
                    _enqueue_node(
                        target, run_id,
                        original_input=original_input,
                        prev_output=revision_input,
                        iteration=past_iters + 1,
                    )
                    return
        # APPROVE (or revision cap reached) — fall through to normal advance.

    next_node = db.fetch_one(
        """
        SELECT * FROM workflow_nodes
        WHERE workflow_id = %s AND parent_group_id IS NULL AND position > %s
        ORDER BY position ASC
        LIMIT 1
        """,
        (workflow_id, from_position),
    )

    if next_node:
        _enqueue_node(
            next_node, run_id,
            original_input=original_input,
            prev_output=output_text,
            iteration=iteration,
        )
        return

    # Reached the end of top-level nodes — check loop
    wf = db.fetch_one("SELECT * FROM workflows WHERE id = %s", (workflow_id,))
    if wf and wf["loop_enabled"] and iteration < (wf["max_loops"] or 1):
        # Load first node again
        first = db.fetch_one(
            """
            SELECT * FROM workflow_nodes
            WHERE workflow_id = %s AND parent_group_id IS NULL
            ORDER BY position ASC
            LIMIT 1
            """,
            (workflow_id,),
        )
        if first:
            # Build loop input: prepend loop_prompt + previous output
            loop_prompt = wf.get("loop_prompt") or "Continue iterating on the previous round's result:"
            loop_input = f"{loop_prompt}\n\n=== Previous round output ===\n{output_text}"
            db.execute("UPDATE runs SET iterations = %s WHERE id = %s", (iteration + 1, run_id))
            _enqueue_node(
                first, run_id,
                original_input=original_input,
                prev_output=loop_input,
                iteration=iteration + 1,
            )
            return

    # Truly done
    db.execute(
        """
        UPDATE runs SET status='done', final_output=%s, finished_at=NOW()
        WHERE id = %s AND status = 'running'
        """,
        (output_text, run_id),
    )
    log.info("run %s complete", run_id)
    # Notify Lead + user about completion
    try:
        _notify_run_complete(run_id, output_text)
    except Exception as e:
        log.exception("failed to notify run complete: %s", e)


def _handle_group_member_done(task: dict, payload: dict, result: dict) -> None:
    """When a group member task completes, check if all siblings are done
    and then trigger the aggregator (if configured) or advance.

    Uses a Postgres advisory transaction lock keyed on (run_id, group_id,
    node_position) to prevent race conditions when two members finish
    nearly simultaneously.
    """
    run_id = task["run_id"]
    node_position = payload["node_position"]
    group_id = payload["group_id"]

    # Advisory lock keys: (run_id, compound(group_id, node_position))
    # pg_try_advisory_xact_lock(k1, k2) returns boolean and auto-releases on commit
    lock_key2 = (group_id * 10000 + node_position) % (2**31)

    advance_with: tuple | None = None

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
                (run_id, lock_key2),
            )
            got = cur.fetchone()["got"]
            if not got:
                return  # another thread is handling this group's completion

            # Check if there are still pending siblings
            cur.execute(
                """
                SELECT COUNT(*) AS pending
                FROM agent_tasks
                WHERE run_id = %s
                  AND payload->>'kind' = 'group_member'
                  AND (payload->>'node_position')::int = %s
                  AND (payload->>'group_id')::bigint = %s
                  AND status IN ('queued','running','paused')
                """,
                (run_id, node_position, group_id),
            )
            if cur.fetchone()["pending"] > 0:
                return

            # Idempotency: if aggregator already enqueued, bail out
            cur.execute(
                """
                SELECT id FROM agent_tasks
                WHERE run_id = %s
                  AND payload->>'kind' = 'aggregator'
                  AND (payload->>'node_position')::int = %s
                  AND (payload->>'group_id')::bigint = %s
                LIMIT 1
                """,
                (run_id, node_position, group_id),
            )
            if cur.fetchone():
                return

            # Gather member outputs
            cur.execute(
                """
                SELECT t.id, t.agent_id, s.response, a.name AS agent_name, a.role_title
                FROM agent_tasks t
                LEFT JOIN run_steps s ON s.id = t.step_id
                LEFT JOIN agents a ON a.id = t.agent_id
                WHERE t.run_id = %s
                  AND t.payload->>'kind' = 'group_member'
                  AND (t.payload->>'node_position')::int = %s
                  AND (t.payload->>'group_id')::bigint = %s
                  AND t.status = 'done'
                ORDER BY t.id ASC
                """,
                (run_id, node_position, group_id),
            )
            rows = cur.fetchall()

            combined_parts = []
            for r in rows:
                text = r.get("response") or ""
                combined_parts.append(
                    f"### {r['agent_name']} ({r.get('role_title') or ''})\n{text}"
                )
            combined_text = "\n\n".join(combined_parts)

            cur.execute("SELECT * FROM groups_tbl WHERE id = %s", (group_id,))
            group = cur.fetchone()

            if group and group.get("aggregator_agent_id"):
                agg_input = (
                    f"Below are the outputs from {len(rows)} members. Please synthesize them into a single final response:\n\n"
                    + combined_text
                )
                agg_payload = {
                    "kind": "aggregator",
                    "node_position": node_position,
                    "workflow_id": payload["workflow_id"],
                    "group_id": group_id,
                    "agent_id": group["aggregator_agent_id"],
                    "prompt": agg_input,
                    "label": "aggregator",
                    "iteration": payload.get("iteration", 1),
                    "original_input": payload.get("original_input", ""),
                }
                # Insert directly on this cursor to stay inside the same transaction
                # as the advisory lock; bypasses precheck but we know the group
                # aggregator agent is valid.
                cur.execute(
                    """
                    INSERT INTO agent_tasks
                        (agent_id, run_id, task_type, priority, priority_num, status, payload)
                    VALUES (%s, %s, 'workflow_step', 'normal', 2, 'queued', %s::jsonb)
                    """,
                    (group["aggregator_agent_id"], run_id, json.dumps(agg_payload)),
                )
            else:
                # No aggregator — will advance after commit (outside the lock)
                advance_with = (
                    payload["workflow_id"], run_id, node_position,
                    payload.get("original_input", ""),
                    combined_text, payload.get("iteration", 1),
                )

    # After commit / lock release, advance workflow if needed
    if advance_with:
        _advance_to_next_node(
            advance_with[0], advance_with[1],
            from_position=advance_with[2],
            original_input=advance_with[3],
            output_text=advance_with[4],
            iteration=advance_with[5],
        )


def _maybe_finalize_cancelled(run_id: int) -> None:
    """If a run is cancelling and no tasks are left running, mark it cancelled."""
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS pending
        FROM agent_tasks
        WHERE run_id = %s AND status IN ('queued','running','paused')
        """,
        (run_id,),
    )
    if row and row["pending"] == 0:
        db.execute(
            "UPDATE runs SET status='cancelled', finished_at=NOW() WHERE id = %s AND status IN ('running','cancelling')",
            (run_id,),
        )


# ============================================================================
# Proactive notifications on run completion
# ============================================================================

def _notify_run_complete(run_id: int, final_output: str, *, failed: bool = False) -> None:
    """When a run finishes, append a short structured summary to the user's
    most recent active Lead thread and emit a bell notification.

    The Lead message is intentionally kept brief — workflow name, run id,
    step/token/cost stats, and a link to the run detail page. It deliberately
    does NOT include the raw final_output (which is often verbose agent prose
    and sometimes agents asking for clarification). Users click through to
    /runs/:id to read the actual output.

    Skips notifications entirely for:
    - trigger_source = 'api'  (programmatic integrations)
    - trigger_source = 'test' (e2e test harness)
    """
    from .services import notifications as notif_service

    run = db.fetch_one(
        "SELECT r.*, w.name AS workflow_name FROM runs r "
        "JOIN workflows w ON w.id = r.workflow_id WHERE r.id = %s",
        (run_id,),
    )
    if not run:
        return

    user_id = run["user_id"]
    workflow_name = run.get("workflow_name") or f"Workflow #{run['workflow_id']}"
    trigger_source = run.get("trigger_source") or "manual"

    if trigger_source in ("api", "test"):
        return

    # If this run was dispatched from a chat thread (WorkflowBubble), it
    # carries the source thread id + the placeholder run_event message id.
    # In that case we simply append a short "complete" line to the same
    # thread — the placeholder is rendered as a live RunStatusCard by the
    # frontend, polling /api/runs/:id, so it morphs from running → done on
    # its own.
    trig_ctx = run.get("trigger_context") or {}
    if isinstance(trig_ctx, str):
        try:
            trig_ctx = json.loads(trig_ctx)
        except Exception:
            trig_ctx = {}
    chat_thread_id = trig_ctx.get("lead_thread_id")

    # --- 1. Pick the thread to post into.
    if chat_thread_id:
        thread_id = chat_thread_id
    else:
        thread = db.fetch_one(
            """
            SELECT thread_id FROM lead_conversations
            WHERE user_id = %s AND status = 'active' AND agent_id IS NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (user_id,),
        )
        if thread:
            thread_id = thread["thread_id"]
        else:
            import uuid
            thread_id = uuid.uuid4().hex[:16]
            db.execute(
                "INSERT INTO lead_conversations (user_id, thread_id, status) VALUES (%s, %s, 'active')",
                (user_id, thread_id),
            )

    if chat_thread_id:
        # Short conversational completion line; the run details are visible
        # in the live RunStatusCard placeholder bubble that's already in the
        # thread.
        body = (
            f"**{workflow_name}** failed — tap the link in the card above for details."
            if failed else
            f"**{workflow_name}** finished. The result is in the card above."
        )
    else:
        # Compact summary for runs dispatched outside chat (scheduler-triggered
        # or direct WorkflowEditor run).
        stats = db.fetch_one(
            """
            SELECT COUNT(*) AS steps,
                   COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                   COALESCE(SUM(cost_usd), 0)::float AS cost,
                   COALESCE(SUM(duration_ms), 0) AS duration_ms
            FROM run_steps WHERE run_id = %s
            """,
            (run_id,),
        ) or {}
        seconds = (stats.get("duration_ms") or 0) / 1000
        duration_str = f"{seconds:.1f}s" if seconds < 60 else f"{seconds/60:.1f}m"
        if failed:
            body = (
                f"The **{workflow_name}** run you dispatched failed.\n\n"
                f"• Run: #{run_id}\n"
                f"• Steps completed: {stats.get('steps', 0)}\n"
                f"• Error: {(final_output or '').strip()[:200]}\n\n"
                f"Open Run #{run_id} for the full log and failure site."
            )
        else:
            body = (
                f"The **{workflow_name}** run you dispatched is complete.\n\n"
                f"• Run: #{run_id}\n"
                f"• Steps: {stats.get('steps', 0)}\n"
                f"• Usage: {stats.get('tokens', 0)} tokens (~${stats.get('cost', 0):.4f})\n"
                f"• Duration: {duration_str}\n\n"
                f"Open Run #{run_id} to see each step's output and the final result."
            )

    db.execute(
        """
        INSERT INTO lead_messages (thread_id, role, content, metadata)
        VALUES (%s, 'lead', %s, %s::jsonb)
        """,
        (thread_id, body, json.dumps({
            "run_id": run_id,
            "workflow_id": run["workflow_id"],
            "event": "run_failed" if failed else "run_complete",
        })),
    )
    db.execute(
        "UPDATE lead_conversations SET updated_at = NOW() WHERE thread_id = %s",
        (thread_id,),
    )

    # --- 2. Emit a bell notification
    notif_service.emit(
        user_id,
        "workflow_failed" if failed else "lead_proposal",
        title=f"{'Run failed' if failed else 'Run complete'}: {workflow_name}",
        body=f"Run #{run_id} {'hit an error' if failed else 'finished'} — tap to view Lead's message.",
        severity="error" if failed else "info",
        related_run_id=run_id,
        related_workflow_id=run["workflow_id"],
        action_payload={"thread_id": thread_id},
    )
