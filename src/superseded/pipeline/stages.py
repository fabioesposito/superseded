from __future__ import annotations

from superseded.models import Stage


class StageDefinition:
    def __init__(self, stage: Stage, name: str, description: str, skill: str) -> None:
        self.stage = stage
        self.name = name
        self.description = description
        self.skill = skill


STAGE_DEFINITIONS: list[StageDefinition] = [
    StageDefinition(
        Stage.SPEC,
        "Spec",
        "Generate a detailed spec from the ticket",
        "spec-driven-development",
    ),
    StageDefinition(
        Stage.PLAN,
        "Plan",
        "Break the spec into implementable tasks",
        "planning-and-task-breakdown",
    ),
    StageDefinition(
        Stage.BUILD, "Build", "Implement the code changes", "incremental-implementation"
    ),
    StageDefinition(
        Stage.VERIFY, "Verify", "Run tests and fix failures", "test-driven-development"
    ),
    StageDefinition(
        Stage.REVIEW,
        "Review",
        "Review code quality and security",
        "code-review-and-quality",
    ),
    StageDefinition(
        Stage.SHIP, "Ship", "Commit and create PR", "git-workflow-and-versioning"
    ),
]
