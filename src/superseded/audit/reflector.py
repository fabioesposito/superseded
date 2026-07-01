from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.agents.base import Agent
    from superseded.memory.store import MemoryStore

logger = logging.getLogger(__name__)

REFLECTION_THRESHOLD = 5
MAX_RULES = 5


_REFLECTION_PROMPT = """\
You are analyzing past code review outcomes to improve future reviews.

Below are findings that were accepted (helpful) or dismissed across multiple
review passes for this repository.

{accepted_section}{dismissed_section}
Analyze these patterns. Output rules ONLY about patterns that were dismissed
2+ times across the same pass or file pattern. Each rule must be a general
principle the team follows — NOT a specific finding. Rules must be 1 sentence,
imperative tone, and actionable (an AI reviewer should be able to apply it).

Return ONLY a JSON array. No explanation text before or after.

[
  {{
    "rule": "Do not flag naming conventions in API-facing functions",
    "evidence": "2 dismissals: snake_case in api.py, naming in api_helpers.py",
    "confidence": 0.9
  }}
]

If no clear patterns emerge, return: []"""


def _build_reflection_prompt(accepted: list[dict], dismissed: list[dict]) -> str:
    """Format accepted and dismissed findings into the reflection prompt."""
    accepted_section = ""
    if accepted:
        lines = []
        for f in accepted:
            lines.append(f"- [{f['pass']}] {f['title']} ({f['file']}:{f.get('line', '?')})")
        accepted_section = "ACCEPTED findings:\n" + "\n".join(lines) + "\n\n"

    dismissed_section = ""
    if dismissed:
        lines = []
        for f in dismissed:
            lines.append(f"- [{f['pass']}] {f['title']} ({f['file']}:{f.get('line', '?')})")
        dismissed_section = "DISMISSED findings:\n" + "\n".join(lines) + "\n\n"

    return _REFLECTION_PROMPT.format(
        accepted_section=accepted_section,
        dismissed_section=dismissed_section,
    )


class PatternReflector:
    def __init__(self, agent: Agent, store: MemoryStore) -> None:
        self._agent = agent
        self._store = store

    async def maybe_reflect(self, repo: str, cwd: str | Path | None = None) -> list[dict]:
        """If unprocessed feedback >= threshold, run reflection pass.

        Returns newly learned rules (may be empty). Never raises.
        """
        try:
            return await self._do_reflect(repo, cwd)
        except Exception:
            logger.exception("Reflection failed for %s", repo)
            return []

    async def _do_reflect(self, repo: str, cwd: str | Path | None = None) -> list[dict]:
        last_id = await self._store.get_reflection_state(repo)

        async with self._store._db() as db:
            db.row_factory = None
            cursor = await db.execute(
                "SELECT fb.id, fb.finding_id, fb.action, "
                "f.pass, f.severity, f.file, f.line, f.title, f.description "
                "FROM feedback fb "
                "JOIN findings f ON f.id = fb.finding_id "
                "WHERE f.repo = ? AND fb.id > ? "
                "ORDER BY fb.id",
                (repo, last_id),
            )
            rows = await cursor.fetchall()

        if len(rows) < REFLECTION_THRESHOLD:
            return []

        accepted: list[dict] = []
        dismissed: list[dict] = []
        max_fb_id = last_id

        for row in rows:
            fb_id, _finding_id, action, pass_name, _sev, file, line, title, _desc = row
            max_fb_id = max(max_fb_id, fb_id)
            entry = {"pass": pass_name, "title": title, "file": file, "line": line}
            if action == "helpful":
                accepted.append(entry)
            elif action == "dismiss":
                dismissed.append(entry)

        prompt = _build_reflection_prompt(accepted, dismissed)

        try:
            cmd = self._agent.build_command()
            _server_env = {k: v for k, v in os.environ.items() if not k.startswith("SUPERSEDED_")}
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=cwd,
                env=_server_env,
            )
        except FileNotFoundError:
            logger.warning("Agent binary not found: %s", cmd[0] if cmd else "?")
            await self._store.set_reflection_state(repo, max_fb_id)
            return []
        except subprocess.TimeoutExpired:
            logger.warning("Agent timed out during reflection")
            await self._store.set_reflection_state(repo, max_fb_id)
            return []

        if result.returncode != 0:
            logger.warning(
                "Agent exited %d during reflection: %s",
                result.returncode,
                result.stderr[:500] if result.stderr else "",
            )
            await self._store.set_reflection_state(repo, max_fb_id)
            return []

        raw = result.stdout
        try:
            parsed = self._agent.parse_output(raw, "reflection")
        except Exception:
            logger.warning("Failed to parse agent output as JSON")
            await self._store.set_reflection_state(repo, max_fb_id)
            return []

        if not isinstance(parsed, list):
            logger.warning("Agent output is not a list: %s", type(parsed).__name__)
            await self._store.set_reflection_state(repo, max_fb_id)
            return []

        valid_rules: list[dict] = []
        for item in parsed[:MAX_RULES]:
            if not isinstance(item, dict):
                continue
            rule_text = item.get("rule")
            if not rule_text or not isinstance(rule_text, str):
                continue
            rule_text = rule_text.strip()
            if len(rule_text) < 10 or len(rule_text) > 300:
                continue
            evidence = str(item.get("evidence", ""))[:500]
            confidence = item.get("confidence", 1.0)
            if not isinstance(confidence, (int, float)):
                confidence = 1.0
            confidence = max(0.0, min(1.0, float(confidence)))

            async with self._store._db() as db:
                await db.execute(
                    "INSERT INTO learned_rules "
                    "(repo, rule_text, evidence_count, confidence) "
                    "VALUES (?, ?, ?, ?)",
                    (repo, rule_text, 1, confidence),
                )
                await db.commit()
            valid_rules.append(
                {
                    "rule": rule_text,
                    "evidence": evidence,
                    "confidence": confidence,
                }
            )

        await self._store.set_reflection_state(repo, max_fb_id)
        return valid_rules
