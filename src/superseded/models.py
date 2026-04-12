from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Any, Literal

import frontmatter
from pydantic import BaseModel, Field


class IssueStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in-progress"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


class Stage(StrEnum):
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
    repos: list[str] = Field(default_factory=list)
    github_url: str = ""

    @classmethod
    def from_frontmatter(cls, content: str, filepath: str = "") -> Issue:
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
            repos=post.get("repos", []),
            github_url=post.get("github_url", ""),
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


class SessionTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    stage: Stage
    attempt: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    event_type: Literal["stdout", "stderr", "status", "error"]
    content: str = ""
    stage: Stage
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineMetrics(BaseModel):
    total_issues: int
    issues_by_status: dict[str, int]
    stage_success_rates: dict[str, float]
    avg_stage_duration_ms: dict[str, float]
    total_retries: int
    retries_by_stage: dict[str, int]
    recent_events: list[AgentEvent] = Field(default_factory=list)
