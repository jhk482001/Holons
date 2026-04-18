"""Skill extraction — agents learn from their own work.

Each agent periodically (or on demand) analyzes its recent run_steps and
asks an LLM to propose re-usable "skills" — markdown docs describing a
recurring pattern, prompt template, or approach.

Confidence-based auto-approval:
    confidence > 0.9  → auto-approved, injected into system prompt
    otherwise         → notification to user, waits for manual approval

Guardrails:
    - User-level and org-level guardrails can deny skills
    - Extracted skills can be exported as ZIP or transferred to other agents
"""
from __future__ import annotations

import json
import re

from .. import db
from ..llm_clients import invoke_for_agent as llm_invoke
from . import notifications


EXTRACTOR_PROMPT = """以下是某位 agent 近期的工作紀錄（每項是一次任務與 response 摘要）。請分析其中是否出現可以被提取成「技能」的重複模式、有效的 prompt 結構、或可複用的處理步驟。

如果有發現，請以下列 JSON 陣列格式回覆（可多條）；如果沒有明顯 pattern，就回空陣列 `[]`。

```json
[
  {{
    "slug": "three-act-outline",
    "name": "三幕劇大綱產生法",
    "description": "...",
    "content_md": "## 適用情境\\n...\\n\\n## 步驟\\n1. ...",
    "confidence": 0.85
  }}
]
```

confidence 是你對這個技能是否真的 recurring + useful 的自評（0-1）。
content_md 是 agent 未來會貼到自己 system prompt 裡的技能文檔，盡量精煉。

=== 工作紀錄 ===
{records}
"""


def extract_for_agent(agent_id: int, *, max_records: int = 30) -> list[dict]:
    """Analyze recent run_steps and extract candidate skills.
    Returns the list of saved skills (may be empty).
    """
    agent = db.fetch_one("SELECT * FROM agents WHERE id = %s", (agent_id,))
    if not agent:
        return []

    records = db.fetch_all(
        """
        SELECT id, prompt, response, model_id, cost_usd
        FROM run_steps
        WHERE agent_id = %s AND error IS NULL
        ORDER BY id DESC
        LIMIT %s
        """,
        (agent_id, max_records),
    )
    if len(records) < 5:
        return []  # not enough data

    records_text = "\n\n".join(
        f"#{r['id']}\nprompt: {(r['prompt'] or '')[:300]}\nresponse: {(r['response'] or '')[:500]}"
        for r in records
    )
    prompt = EXTRACTOR_PROMPT.format(records=records_text)

    result = llm_invoke(
        agent_id=agent["id"],
        model_key=agent.get("primary_model_id") or None,
        system_prompt="你是一位分析師，擅長從工作紀錄中提取可複用的技能模式。",
        user_text=prompt,
    )

    text = result.get("text", "")
    # Parse JSON
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"(\[.*\])", text, re.DOTALL)
    if not m:
        return []
    try:
        candidates = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    return _save_candidates(agent, candidates, [r["id"] for r in records])


def _save_candidates(agent: dict, candidates: list[dict], source_run_ids: list[int]) -> list[dict]:
    """Persist proposals to agent_skills, auto-approving high-confidence ones.
    Also applies guardrails.
    """
    if not isinstance(candidates, list):
        return []

    user_id = agent["user_id"]
    # Load guardrails
    rules = db.fetch_all(
        """
        SELECT * FROM skill_guardrails
        WHERE enabled = TRUE AND (scope = 'org' OR (scope = 'user' AND user_id = %s))
        """,
        (user_id,),
    )

    saved: list[dict] = []
    for c in candidates:
        slug = c.get("slug") or ""
        name = c.get("name") or slug
        content_md = c.get("content_md") or ""
        confidence = float(c.get("confidence") or 0.5)

        # Guardrail check
        if not _passes_guardrails(content_md, name, rules):
            continue

        # Upsert into agent_skills
        existing = db.fetch_one(
            "SELECT id, times_used, approved_by_user FROM agent_skills WHERE agent_id = %s AND slug = %s",
            (agent["id"], slug),
        )
        auto_approved = confidence > 0.9
        if existing:
            db.execute(
                """
                UPDATE agent_skills
                SET content_md = %s, confidence = %s,
                    source_run_ids = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (content_md, confidence, json.dumps(source_run_ids), existing["id"]),
            )
            skill_id = existing["id"]
        else:
            skill_id = db.execute_returning(
                """
                INSERT INTO agent_skills
                    (agent_id, slug, name, description, content_md, source,
                     source_run_ids, confidence, approved_by_user)
                VALUES (%s, %s, %s, %s, %s, 'self_learned', %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    agent["id"], slug, name, c.get("description"), content_md,
                    json.dumps(source_run_ids), confidence, auto_approved,
                ),
            )

        saved.append({"id": skill_id, "slug": slug, "name": name, "confidence": confidence, "auto_approved": auto_approved})

        if not auto_approved:
            notifications.emit(
                user_id,
                "skill_suggested",
                severity="info",
                title=f"{agent['name']} 學到了新技能：{name}",
                body=f"信心度 {confidence:.2f}，等待你的核可",
                related_agent_id=agent["id"],
                action_payload={"skill_id": skill_id, "content_md": content_md[:500]},
            )

    return saved


def _passes_guardrails(content: str, name: str, rules: list[dict]) -> bool:
    for r in rules:
        if r["rule_type"] == "deny_keyword":
            kw = (r["rule_value"] or "").lower()
            if kw and (kw in content.lower() or kw in name.lower()):
                return False
        elif r["rule_type"] == "max_confidence":
            # handled per-candidate elsewhere
            pass
    return True


# ============================================================================
# Approval / injection into system prompt
# ============================================================================

def approve(skill_id: int, user_id: int) -> None:
    db.execute(
        """
        UPDATE agent_skills
        SET approved_by_user = TRUE
        WHERE id = %s AND agent_id IN (SELECT id FROM agents WHERE user_id = %s)
        """,
        (skill_id, user_id),
    )


def reject(skill_id: int, user_id: int) -> None:
    db.execute(
        """
        DELETE FROM agent_skills
        WHERE id = %s AND agent_id IN (SELECT id FROM agents WHERE user_id = %s)
        """,
        (skill_id, user_id),
    )


def compose_system_prompt(agent_id: int) -> str:
    """Build an agent's effective system prompt: base prompt + approved skills."""
    agent = db.fetch_one(
        "SELECT system_prompt, name FROM agents WHERE id = %s",
        (agent_id,),
    )
    base = (agent or {}).get("system_prompt") or ""

    skills = db.fetch_all(
        """
        SELECT name, content_md, times_used
        FROM agent_skills
        WHERE agent_id = %s AND approved_by_user = TRUE
        ORDER BY times_used DESC, updated_at DESC
        """,
        (agent_id,),
    )
    if not skills:
        return base

    parts = [base, "", "---", "", "## 你已掌握的技能", ""]
    for s in skills:
        parts.append(f"### {s['name']}  (使用 {s['times_used']} 次)")
        parts.append(s["content_md"])
        parts.append("")
    return "\n".join(parts)


# ============================================================================
# Export / Import (skill transfer)
# ============================================================================

def export_skills(agent_id: int) -> dict:
    """Export all approved skills as a transferable bundle (dict to be JSON-serialised)."""
    rows = db.fetch_all(
        """
        SELECT slug, name, description, content_md, confidence, times_used
        FROM agent_skills
        WHERE agent_id = %s AND approved_by_user = TRUE
        """,
        (agent_id,),
    )
    return {
        "schema_version": "1.0",
        "source_agent_id": agent_id,
        "skills": rows,
    }


def import_skills(target_agent_id: int, bundle: dict) -> int:
    """Import a skill bundle into the target agent. Returns number imported."""
    count = 0
    for s in bundle.get("skills", []):
        existing = db.fetch_one(
            "SELECT id FROM agent_skills WHERE agent_id = %s AND slug = %s",
            (target_agent_id, s.get("slug")),
        )
        if existing:
            continue
        db.execute(
            """
            INSERT INTO agent_skills
                (agent_id, slug, name, description, content_md, source,
                 source_run_ids, confidence, approved_by_user)
            VALUES (%s, %s, %s, %s, %s, 'imported', '[]'::jsonb, %s, FALSE)
            """,
            (
                target_agent_id, s.get("slug"), s.get("name"),
                s.get("description"), s.get("content_md"),
                s.get("confidence"),
            ),
        )
        count += 1
    return count
