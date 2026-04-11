from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field


class PlanTask(BaseModel):
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification: str = ""
    dependencies: list[str] = Field(default_factory=list)
    scope: str = "Medium"


class Plan(BaseModel):
    title: str
    context: str = ""
    tasks: list[PlanTask] = Field(default_factory=list)


def write_plan(path: str, title: str, context: str, tasks: list[PlanTask]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Plan: {title}", "", "## Context", "", context, "", "## Tasks", ""]
    for i, task in enumerate(tasks, 1):
        lines.append(f"### Task {i}: {task.title}")
        lines.append(f"- **Description:** {task.description}")
        criteria = (
            "; ".join(task.acceptance_criteria) if task.acceptance_criteria else "none"
        )
        lines.append(f"- **Acceptance criteria:** {criteria}")
        lines.append(f"- **Verification:** {task.verification or 'none'}")
        deps = ", ".join(task.dependencies) if task.dependencies else "none"
        lines.append(f"- **Dependencies:** {deps}")
        lines.append(f"- **Scope:** {task.scope}")
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")


def read_plan(path: str) -> Plan:
    p = Path(path)
    if not p.exists():
        return Plan(title="", context="", tasks=[])

    content = p.read_text(encoding="utf-8")

    title_match = re.search(r"^# Plan:\s*(.+)$", content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else ""

    context_match = re.search(r"^## Context\s*\n\n(.*?)(?=\n## )", content, re.DOTALSE)
    context = context_match.group(1).strip() if context_match else ""

    task_blocks = re.findall(
        r"### Task \d+:\s*(.+?)\n((?:- \*\*.+?\n)+)", content, re.DOTALSE
    )

    tasks: list[PlanTask] = []
    for task_title, block in task_blocks:
        desc_match = re.search(r"\*\*Description:\*\*\s*(.+)", block)
        criteria_match = re.search(r"\*\*Acceptance criteria:\*\*\s*(.+)", block)
        verify_match = re.search(r"\*\*Verification:\*\*\s*(.+)", block)
        deps_match = re.search(r"\*\*Dependencies:\*\*\s*(.+)", block)
        scope_match = re.search(r"\*\*Scope:\*\*\s*(.+)", block)

        criteria_str = criteria_match.group(1).strip() if criteria_match else ""
        criteria = (
            [c.strip() for c in criteria_str.split(";") if c.strip()]
            if criteria_str and criteria_str != "none"
            else []
        )

        deps_str = deps_match.group(1).strip() if deps_match else "none"
        deps = (
            [d.strip() for d in deps_str.split(",") if d.strip()]
            if deps_str != "none"
            else []
        )

        tasks.append(
            PlanTask(
                title=task_title.strip(),
                description=desc_match.group(1).strip() if desc_match else "",
                acceptance_criteria=criteria,
                verification=verify_match.group(1).strip() if verify_match else "",
                dependencies=deps,
                scope=scope_match.group(1).strip() if scope_match else "Medium",
            )
        )

    return Plan(title=title, context=context, tasks=tasks)
