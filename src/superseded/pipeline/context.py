from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from superseded.models import Issue, Stage
from superseded.pipeline.prompts import get_prompt_for_stage


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
        entries: list[str] = []
        for md_file in sorted(docs_dir.glob("**/*.md")):
            rel = md_file.relative_to(repo_path)
            first_line = (
                md_file.read_text(encoding="utf-8").split("\n")[0].strip("# ").strip()
            )
            entries.append(f"- {rel}: {first_line}")
        if not entries:
            return None
        label = f"{repo} repo" if repo else "Documentation"
        return f"## {label} Documentation Index\n\n" + "\n".join(entries)

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

    def _build_skill_layer(self, stage: Stage) -> str:
        prompt = get_prompt_for_stage(stage)
        return f"## Stage Instructions: {stage.value.upper()}\n\n{prompt}"

    def _run_async(self, coro: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()

    def _build_session_history_layer(
        self, issue_id: str, current_stage: Stage, db: Any
    ) -> str | None:
        if db is None:
            return None

        turns = self._run_async(db.get_session_turns(issue_id))

        prior_turns = [t for t in turns if t["stage"] != current_stage.value]
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

    def build(
        self,
        stage: Stage,
        issue: Issue,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        iteration: int = 0,
        db: Any = None,
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

        session_history = self._build_session_history_layer(issue.id, stage, db)
        if session_history:
            layers.append(session_history)

        rules = self._build_rules_layer()
        if rules:
            layers.append(rules)

        layers.append(self._build_skill_layer(stage))

        if previous_errors:
            layers.append(self._build_error_layer(previous_errors, iteration))

        return "\n\n---\n\n".join(layers)
