from __future__ import annotations

from pathlib import Path

import yaml

from superseded.models import Issue, Stage
from superseded.pipeline.prompts import get_prompt_for_stage
from superseded.validation import sanitize_agent_prompt


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
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._repo_registry: dict[str, Path] = {}

    def register_repo(self, name: str, repo_path: str) -> None:
        self._repo_registry[name] = Path(repo_path)

    def _get_repo_path(self, repo: str | None = None) -> Path:
        if repo and repo in self._repo_registry:
            return self._repo_registry[repo]
        return self.repo_path

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

    def _build_docs_index_layer(self, repo: str | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        docs_dir = repo_path / "docs"
        if not docs_dir.exists():
            return None

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

    def _build_session_history_layer(
        self, current_stage: Stage, session_turns: list[dict] | None = None
    ) -> str | None:
        if not session_turns:
            return None

        prior_turns = [t for t in session_turns if t["stage"] != current_stage.value]
        if not prior_turns:
            return None

        parts: list[str] = []
        current_section = None
        for turn in prior_turns:
            section = f"{turn['stage']} (attempt {turn['attempt'] + 1})"
            if section != current_section:
                current_section = section
                parts.append(f"### {section}")

            role_label = "You asked" if turn["role"] == "user" else "Agent responded"
            content = turn["content"]
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            parts.append(f"**{role_label}:**\n{content}")

        if not parts:
            return None
        return "## Previous Session History\n\n" + "\n\n".join(parts)

    def _build_error_layer(self, previous_errors: list[str], iteration: int) -> str:
        error_lines = "\n".join(f"- {err}" for err in previous_errors)
        return (
            f"## Retry Context (attempt {iteration + 1})\n\n"
            f"The previous attempt failed. Fix the following errors:\n\n{error_lines}\n\n"
            f"Address each error. Do not repeat the same mistakes."
        )

    def _build_answers_layer(self, artifacts_path: str) -> str | None:
        answers_file = Path(artifacts_path) / "answers.md"
        if answers_file.exists():
            content = answers_file.read_text(encoding="utf-8")
            return f"## Human Answers to Your Questions\n\n{content}"
        return None

    def build(
        self,
        stage: Stage,
        issue: Issue,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        iteration: int = 0,
        session_turns: list[dict] | None = None,
        target_repo: str | None = None,
    ) -> str:
        layers: list[str] = []
        previous_errors = previous_errors or []

        agents_md = self._build_agents_md_layer()
        if agents_md:
            layers.append(agents_md)

        docs_index = self._build_docs_index_layer()
        if docs_index:
            layers.append(docs_index)

        layers.append(self._build_issue_layer(issue))

        # Target repo context (if different from primary)
        if target_repo:
            target_agents_md = self._build_agents_md_layer(target_repo)
            if target_agents_md:
                layers.append(target_agents_md)
            target_docs = self._build_docs_index_layer(target_repo)
            if target_docs:
                layers.append(target_docs)
            target_rules = self._build_rules_layer(target_repo)
            if target_rules:
                layers.append(target_rules)

        artifacts = self._build_artifacts_layer(artifacts_path)
        if artifacts:
            layers.append(artifacts)

        answers = self._build_answers_layer(artifacts_path)
        if answers:
            layers.append(answers)

        session_history = self._build_session_history_layer(stage, session_turns)
        if session_history:
            layers.append(session_history)

        rules = self._build_rules_layer()
        if rules:
            layers.append(rules)

        layers.append(self._build_skill_layer(stage, target_repo=target_repo))

        if previous_errors:
            layers.append(self._build_error_layer(previous_errors, iteration))

        prompt = "\n\n---\n\n".join(layers)
        return sanitize_agent_prompt(prompt)
