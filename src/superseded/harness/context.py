from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml

from superseded.models import Issue, Stage
from superseded.pipeline.prompts import get_prompt_for_stage
from superseded.validation import sanitize_agent_prompt


def _estimate_tokens(text: str) -> int:
    """Approximate token count: words / 0.75."""
    return max(1, len(text.split()) * 4 // 3)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.

    Returns (metadata dict, body text). If no frontmatter is found,
    returns ({}, original content).
    """
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
        if not isinstance(meta, dict):
            return {}, content
        return meta, parts[2].lstrip("\n")
    except yaml.YAMLError:
        return {}, content


class ContextAssembler:
    _STAGE_CATEGORIES: ClassVar[dict[Stage, list[str]]] = {
        Stage.SPEC: ["architecture", "guides"],
        Stage.PLAN: ["architecture", "guides", "adrs"],
        Stage.BUILD: ["architecture", "guides"],
        Stage.VERIFY: ["architecture", "guides"],
        Stage.REVIEW: ["architecture", "guides", "adrs"],
        Stage.SHIP: ["guides", "operations"],
    }

    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._repo_registry: dict[str, Path] = {}
        self.last_token_estimate: int = 0
        self.layer_tokens: dict[str, int] = {}
        self.max_tokens: int = 0  # 0 = unlimited

    def register_repo(self, name: str, repo_path: str) -> None:
        self._repo_registry[name] = Path(repo_path)

    def _get_repo_path(self, repo: str | None = None) -> Path:
        if repo and repo in self._repo_registry:
            return self._repo_registry[repo]
        return self.repo_path

    def _fits_budget(self, layers: list[str]) -> bool:
        if self.max_tokens <= 0:
            return True
        return _estimate_tokens("\n\n---\n\n".join(layers)) <= self.max_tokens

    def _read_if_exists(self, path: Path) -> str | None:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return None

    def _build_agents_md_layer(self, repo: str | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        content = self._read_if_exists(repo_path / "AGENTS.md")
        if content:
            label = f"{repo} repo" if repo else "Repository"
            return f"## {label} Guide (AGENTS.md)\n\n{content}"
        return None

    def _build_docs_index_layer(self, repo: str | None = None, stage: Stage | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        docs_dir = repo_path / "docs"
        if not docs_dir.exists():
            return None

        relevant = set(self._STAGE_CATEGORIES.get(stage, [])) if stage else None

        categories: dict[str, list[tuple[str, str]]] = {}
        uncategorized: list[tuple[str, str]] = []

        for md_file in sorted(docs_dir.glob("**/*.md")):
            rel = md_file.relative_to(docs_dir)
            content = md_file.read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(content)

            summary = meta.get("summary", "").strip()
            if not summary:
                summary = content.split("\n")[0].strip("# ").strip()

            category = meta.get("category", "").strip()
            if relevant and category and category not in relevant:
                continue
            if category and category in ("architecture", "guides", "adrs", "operations"):
                categories.setdefault(category, []).append((str(rel), summary))
            else:
                uncategorized.append((str(rel), summary))

        if not categories and not uncategorized:
            return None

        label = f"{repo} repo" if repo else "Documentation"
        sections: list[str] = [f"## {label} Index\n"]

        category_order = ["architecture", "guides", "adrs", "operations"]
        for cat in category_order:
            if cat in categories:
                sections.append(f"### {cat.title()}")
                for rel, summary in categories[cat]:
                    sections.append(f"- {rel}: {summary}")
                sections.append("")

        if uncategorized:
            sections.append("### Other")
            for rel, summary in uncategorized:
                sections.append(f"- {rel}: {summary}")

        return "\n".join(sections)

    def _build_issue_layer(self, issue: Issue) -> str:
        ticket_path = self.repo_path / issue.filepath
        content = self._read_if_exists(ticket_path)
        if content:
            return f"## Issue Ticket\n\n{content}"
        return f"## Issue Ticket\n\nID: {issue.id}\nTitle: {issue.title}"

    def _build_artifacts_layer(self, artifacts_path: str) -> str | None:
        art_dir = Path(artifacts_path)
        if not art_dir.exists():
            return None
        parts: list[str] = []
        for artifact_file in sorted(art_dir.glob("*.md")):
            content = self._read_if_exists(artifact_file)
            if content:
                parts.append(f"### {artifact_file.name}\n\n{content}")
        if not parts:
            return None
        return "## Previous Stage Artifacts\n\n" + "\n\n".join(parts)

    def _build_rules_layer(self, repo: str | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        content = self._read_if_exists(repo_path / ".superseded" / "rules.md")
        if content:
            return f"## Project Rules (non-negotiable)\n\n{content}"
        return None

    def _build_skill_layer(self, stage: Stage, target_repo: str | None = None) -> str:
        prompt = get_prompt_for_stage(stage)
        repo_context = ""
        if target_repo:
            repo_path = self._get_repo_path(target_repo)
            repo_context = (
                f"\n\n## Target Repository: {target_repo}\n"
                f"You are working in the `{target_repo}` repository at `{repo_path}`.\n"
                f"All git operations (commit, push, PR creation) apply to THIS repository.\n"
                f"Use `gh pr create` to create a PR in this repository."
            )
        return f"## Stage Instructions: {stage.value.upper()}\n\n{prompt}{repo_context}"

    def _build_crg_tools_layer(self) -> str:
        return (
            "## Code Review Graph Tools\n\n"
            "You have access to code analysis via the CRG MCP server:\n\n"
            "- `get_minimal_context_tool(query)` — Ultra-compact context (~100 tokens). "
            "Call this first.\n"
            "- `semantic_search_nodes_tool(query)` — Search code entities by name or meaning.\n"
            "- `query_graph_tool(node, query_type)` — Query callers, callees, tests, imports, "
            "inheritance.\n"
            "- `get_impact_radius_tool(files)` — Blast radius of changed files.\n"
            "- `get_review_context_tool()` — Token-optimised review context with structural summary.\n"
            "- `traverse_graph_tool(node, depth, token_budget)` — BFS/DFS traversal from any node.\n"
            "- `detect_changes_tool()` — Risk-scored change impact analysis.\n"
            "- `list_communities_tool()` — List detected code communities.\n"
            "- `get_architecture_overview_tool()` — Architecture overview from community structure.\n\n"
            "Use `get_minimal_context_tool` or `semantic_search_nodes_tool` to find relevant code "
            "before reading entire files. This saves tokens and finds the right code faster."
        )

    MAX_SESSION_HISTORY_TURNS = 5
    MAX_TURN_CONTENT_LENGTH = 500

    def _build_session_history_layer(
        self, current_stage: Stage, session_turns: list[dict] | None = None
    ) -> str | None:
        if not session_turns:
            return None

        prior_turns = [t for t in session_turns if t["stage"] != current_stage.value]
        if not prior_turns:
            return None

        recent_turns = prior_turns[-self.MAX_SESSION_HISTORY_TURNS :]

        parts: list[str] = []
        current_section = None
        for turn in recent_turns:
            section = f"{turn['stage']} (attempt {turn['attempt'] + 1})"
            if section != current_section:
                current_section = section
                parts.append(f"### {section}")

            role_label = "You asked" if turn["role"] == "user" else "Agent responded"
            content = turn["content"]
            if len(content) > self.MAX_TURN_CONTENT_LENGTH:
                content = (
                    content[:200]
                    + f"\n\n[... {len(content) - 400} chars omitted ...]\n\n"
                    + content[-200:]
                )
            parts.append(f"**{role_label}:**\n{content}")

        if not parts:
            return None
        return "## Previous Session History (summarized)\n\n" + "\n\n".join(parts)

    def _build_error_layer(self, previous_errors: list[str], iteration: int) -> str:
        from collections import Counter

        seen: set[str] = set()
        unique: list[str] = []
        for err in previous_errors:
            normalized = err.strip().lower()
            if normalized not in seen:
                seen.add(normalized)
                unique.append(err)

        freq = Counter(err.strip().lower() for err in previous_errors)
        unique.sort(key=lambda e: -freq[e.strip().lower()])

        error_lines = "\n".join(f"- {err}" for err in unique)
        return (
            f"## Retry Context (attempt {iteration + 1})\n\n"
            f"The previous attempt(s) failed. Fix the following {len(unique)} distinct error(s):\n\n"
            f"{error_lines}\n\n"
            f"Address each error. Do not repeat the same mistakes."
        )

    def _build_answers_layer(self, artifacts_path: str) -> str | None:
        answers_file = Path(artifacts_path) / "answers.md"
        if answers_file.exists():
            content = answers_file.read_text(encoding="utf-8")
            return f"## Human Answers to Your Questions\n\n{content}"
        return None

    def _build_plan_progress_layer(self, artifacts_path: str) -> str | None:
        from superseded.pipeline.plan import read_plan

        plan = read_plan(str(Path(artifacts_path) / "plan.md"))
        if not plan.tasks:
            return None
        progress = f"{plan.completed_count} of {plan.total_count} tasks complete"
        lines = [f"## Plan Progress ({progress})\n"]
        for i, task in enumerate(plan.tasks, 1):
            icon = {"complete": "[x]", "in-progress": "[ ]", "skipped": "[~]"}.get(task.status, "[ ]")
            lines.append(f"- Task {i}: {icon} {task.title} — {task.status}")
        return "\n".join(lines)

    def build(
        self,
        stage: Stage,
        issue: Issue,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        iteration: int = 0,
        session_turns: list[dict] | None = None,
        target_repo: str | None = None,
        crg_search_results: list | None = None,
        crg_enabled: bool = False,
    ) -> str:
        layers: list[str] = []
        self.layer_tokens = {}
        previous_errors = previous_errors or []

        def _add_layer(name: str, content: str | None) -> None:
            if content:
                layers.append(content)
                self.layer_tokens[name] = _estimate_tokens(content)

        _add_layer("AGENTS.md", self._build_agents_md_layer())

        if crg_enabled:
            _add_layer("CRG tools", self._build_crg_tools_layer())
        else:
            docs_index = self._build_docs_index_layer(stage=stage)
            if docs_index:
                _add_layer("docs index", docs_index)

        _add_layer("issue ticket", self._build_issue_layer(issue))

        if target_repo:
            target_agents_md = self._build_agents_md_layer(target_repo)
            if target_agents_md:
                _add_layer(f"AGENTS.md ({target_repo})", target_agents_md)
            target_docs = self._build_docs_index_layer(target_repo, stage=stage)
            if target_docs:
                _add_layer(f"docs ({target_repo})", target_docs)
            target_rules = self._build_rules_layer(target_repo)
            if target_rules:
                _add_layer(f"rules ({target_repo})", target_rules)

        artifacts = self._build_artifacts_layer(artifacts_path)
        if artifacts:
            _add_layer("artifacts", artifacts)

        if stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW):
            plan_progress = self._build_plan_progress_layer(artifacts_path)
            if plan_progress:
                _add_layer("plan progress", plan_progress)

        answers = self._build_answers_layer(artifacts_path)
        if answers:
            _add_layer("answers", answers)

        if not crg_enabled:
            session_history = self._build_session_history_layer(stage, session_turns)
            if session_history:
                _add_layer("session history", session_history)

        rules = self._build_rules_layer()
        if rules:
            _add_layer("rules", rules)

        _add_layer("skill prompt", self._build_skill_layer(stage, target_repo=target_repo))

        if previous_errors:
            _add_layer("error context", self._build_error_layer(previous_errors, iteration))

        if self.max_tokens > 0:
            drop_order = ["session history", "docs index", "skill prompt"]
            for drop_name in drop_order:
                if self._fits_budget(layers):
                    break
                keys = list(self.layer_tokens.keys())
                for i, name in enumerate(keys):
                    if name == drop_name and i < len(layers):
                        layers.pop(i)
                        del self.layer_tokens[name]
                        break

        prompt = "\n\n---\n\n".join(layers)
        result = sanitize_agent_prompt(prompt)
        self.last_token_estimate = _estimate_tokens(result)
        return result
