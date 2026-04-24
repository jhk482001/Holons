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
from typing import Any

from .. import db
from ..llm_clients import invoke_for_agent as llm_invoke
from . import notifications


EXTRACTOR_PROMPT = """Below are recent work records from an agent (each is a task + response summary). Analyze them for patterns that could be captured as a re-usable "skill" — a recurring workflow, an effective prompt structure, or a repeatable processing procedure.

**Output language**: produce the skill `name`, `description`, and `content_md` in **{output_language}**. Slug stays lowercase-kebab English. If no clear pattern emerges, return an empty array `[]`.

Reply with a JSON array inside a ```json fence (multiple skills allowed):

```json
[
  {{
    "slug": "three-act-outline",
    "name": "<skill title in {output_language}>",
    "description": "<one-sentence summary in {output_language}>",
    "content_md": "## When to apply\\n...\\n\\n## Steps\\n1. ...",
    "confidence": 0.85
  }}
]
```

- `confidence` (0–1): your self-rating of how recurring + re-usable this pattern really is.
- `content_md`: the markdown doc the agent will paste into its own system prompt — keep it concise and actionable.

=== Work records ===
{records}
"""


def extract_for_agent(agent_id: int, *, max_records: int = 30) -> list[dict]:
    """Analyze recent run_steps and extract candidate skills.
    Returns the list of saved skills (may be empty).

    Side-effect: each saved skill captures the full extraction audit
    (model, tokens, cost, prompt + response previews) so a user can
    inspect any one skill and see exactly where it came from.
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
    # Output language follows the owning user's UI language — molly (en)
    # shouldn't see Chinese skill titles just because the last extractor
    # prompt was Chinese.
    user_row = db.fetch_one(
        "SELECT language FROM as_users WHERE id = %s", (agent["user_id"],),
    )
    lang_code = (user_row or {}).get("language") or "en"
    output_language = {
        "zh-TW": "Traditional Chinese (繁體中文)",
        "zh-CN": "Simplified Chinese",
        "en": "English",
    }.get(lang_code, "English")
    prompt = EXTRACTOR_PROMPT.format(records=records_text, output_language=output_language)

    # 16K output ceiling: skill content_md is meaty markdown — at 4K the
    # JSON gets truncated mid-string, the regex fails silently, and we
    # end up with "no skills" even when Haiku found several. 16K safely
    # covers 3–5 skills per agent without runaway cost.
    result = llm_invoke(
        agent_id=agent["id"],
        model_key=agent.get("primary_model_id") or None,
        system_prompt="You are an analyst skilled at mining recurring, re-usable skill patterns from work records.",
        user_text=prompt,
        max_tokens=16384,
        user_id=agent.get("user_id"),
        kind="skill_extract",
        prefer_user_default=True,
    )

    text = result.get("text", "")
    # Parse JSON. The outer fence is ```json ... ``` but skills frequently
    # embed prompt examples inside their content_md that include their own
    # triple-backticks — a non-greedy match stops at the first inner ```
    # and truncates the JSON. Anchor on the OUTER pair: ```json<newline>
    # at the start, and the LAST standalone ``` in the response as the
    # end. Falls back to the first bare [...] if no fence is present.
    candidates = None
    open_m = re.search(r"```json\s*\n", text)
    if open_m:
        body = text[open_m.end():]
        # Find the last ``` that sits alone on its own line — that's the
        # outer closing fence.
        end_matches = list(re.finditer(r"\n```\s*(?:\n|$)", body))
        if end_matches:
            payload = body[: end_matches[-1].start()]
            try:
                candidates = json.loads(payload)
            except json.JSONDecodeError:
                candidates = None
    if candidates is None:
        m = re.search(r"(\[.*\])", text, re.DOTALL)
        if not m:
            return []
        try:
            candidates = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []

    audit = {
        "model_id": result.get("model_id"),
        "input_tokens": int(result.get("input_tokens") or 0),
        "output_tokens": int(result.get("output_tokens") or 0),
        "cost_usd": float(result.get("cost_usd") or 0.0),
        "prompt_preview": prompt[:2000],
        "response_preview": text[:2000],
    }
    return _save_candidates(agent, candidates, [r["id"] for r in records], audit)


def _save_candidates(agent: dict, candidates: list[dict],
                      source_run_ids: list[int],
                      audit: dict | None = None) -> list[dict]:
    """Persist proposals to agent_skills with full extraction audit.
    Also applies guardrails.

    Auto-approval: if the user's `skills_auto_approve` flag is ON (default),
    every extracted skill is saved as approved so it immediately enters
    the source agent's working set. Users who want human-in-the-loop
    review flip that switch off in Personal settings and extracted skills
    land as proposals (approved_by_user=FALSE) with a notification.
    """
    if not isinstance(candidates, list):
        return []

    audit = audit or {}
    user_id = agent["user_id"]
    user_row = db.fetch_one(
        "SELECT skills_auto_approve FROM as_users WHERE id = %s", (user_id,),
    )
    auto_approve_all = bool((user_row or {}).get("skills_auto_approve", True))

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
        # Default ON: every extraction gets auto-approved. The legacy
        # confidence>0.9 bar is kept only as a safety-net for users who
        # toggled auto-approve OFF — high-confidence skills still go
        # straight in even when the manual-review switch is on.
        auto_approved = auto_approve_all or confidence > 0.9
        if existing:
            db.execute(
                """
                UPDATE agent_skills
                SET content_md = %s, confidence = %s,
                    source_run_ids = %s::jsonb,
                    extraction_model_id = %s,
                    extraction_input_tokens = %s,
                    extraction_output_tokens = %s,
                    extraction_cost_usd = %s,
                    extraction_prompt_preview = %s,
                    extraction_response_preview = %s,
                    extraction_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (content_md, confidence, json.dumps(source_run_ids),
                 audit.get("model_id"),
                 audit.get("input_tokens"),
                 audit.get("output_tokens"),
                 audit.get("cost_usd"),
                 audit.get("prompt_preview"),
                 audit.get("response_preview"),
                 existing["id"]),
            )
            skill_id = existing["id"]
        else:
            skill_id = db.execute_returning(
                """
                INSERT INTO agent_skills
                    (agent_id, slug, name, description, content_md, source,
                     source_run_ids, confidence, approved_by_user,
                     extraction_model_id, extraction_input_tokens,
                     extraction_output_tokens, extraction_cost_usd,
                     extraction_prompt_preview, extraction_response_preview,
                     extraction_at)
                VALUES (%s, %s, %s, %s, %s, 'self_learned', %s::jsonb, %s, %s,
                        %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (
                    agent["id"], slug, name, c.get("description"), content_md,
                    json.dumps(source_run_ids), confidence, auto_approved,
                    audit.get("model_id"),
                    audit.get("input_tokens"),
                    audit.get("output_tokens"),
                    audit.get("cost_usd"),
                    audit.get("prompt_preview"),
                    audit.get("response_preview"),
                ),
            )

        saved.append({
            "id": skill_id, "slug": slug, "name": name,
            "confidence": confidence, "auto_approved": auto_approved,
        })

        if not auto_approved:
            notifications.emit(
                user_id,
                "skill_suggested",
                severity="info",
                title=f"{agent['name']} proposed a new skill: {name}",
                body=f"Confidence {confidence:.2f} — waiting for your review.",
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
    set_approved(skill_id, user_id, True)


def set_approved(skill_id: int, user_id: int, approved: bool) -> None:
    """Enable / disable a skill without deleting it. Disabled skills stop
    being injected into the agent's system prompt on the next run."""
    db.execute(
        """
        UPDATE agent_skills
        SET approved_by_user = %s
        WHERE id = %s AND agent_id IN (SELECT id FROM agents WHERE user_id = %s)
        """,
        (bool(approved), skill_id, user_id),
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

    parts = [base, "", "---", "", "## Your learned skills", ""]
    for s in skills:
        parts.append(f"### {s['name']}  (used {s['times_used']}×)")
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
