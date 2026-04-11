from __future__ import annotations

import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field


class IssueStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in-progress"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


class Stage(str, Enum):
    SPEC = "spec"
    PLAN = "plan"
    BUILD = "build"
    VERIFY = "verify"
    REVIEW = "review"
    SHIP = "ship"


STAGE_ORDER: list[Stage] = [
    Stage.SPEC,
    Stage.PLAN,
    Stage.BUILD,
    Stage.VERIFY,
    Stage.REVIEW,
    Stage.SHIP,
]


class Issue(BaseModel):
    id: str
    title: str
    status: IssueStatus = IssueStatus.NEW
    stage: Stage = Stage.SPEC
    created: datetime.date = Field(default_factory=datetime.date.today)
    assignee: str = ""
    labels: list[str] = Field(default_factory=list)
    filepath: str = ""

    @classmethod
    def from_frontmatter(cls, content: str, filepath: str = "") -> "Issue":
        post = frontmatter.loads(content)
        return cls(
            id=post.get("id", "SUP-000"),
            title=post.get("title", "Untitled"),
            status=IssueStatus(post.get("status", "new")),
            stage=Stage(post.get("stage", "spec")),
            created=post.get("created", datetime.date.today()),
            assignee=post.get("assignee", ""),
            labels=post.get("labels", []),
            filepath=filepath,
        )

    def next_stage(self) -> Stage | None:
        idx = STAGE_ORDER.index(self.stage)
        if idx + 1 < len(STAGE_ORDER):
            return STAGE_ORDER[idx + 1]
        return None


class StageResult(BaseModel):
    stage: Stage
    passed: bool
    output: str = ""
    error: str = ""
    artifacts: list[str] = Field(default_factory=list)
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class AgentResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    files_changed: list[str] = Field(default_factory=list)


class HarnessIteration(BaseModel):
    attempt: int
    stage: Stage
    previous_errors: list[str] = Field(default_factory=list)


class AgentContext(BaseModel):
    repo_path: str
    issue: Issue
    skill_prompt: str
    artifacts_path: str = ""
    worktree_path: str = ""
    iteration: int = 0
    previous_errors: list[str] = Field(default_factory=list)
