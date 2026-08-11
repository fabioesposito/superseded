from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Severity = Literal["critical", "important", "suggestion", "nit"]
PassName = Literal["security", "correctness", "performance", "style", "architecture"]
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    pass_name: PassName
    severity: Severity
    file: str
    line: int
    end_line: int | None = None
    title: str
    description: str
    suggestion: str
    confidence: Confidence = "high"
    reasoning: str = Field(default="")
    id: str = Field(default="")
    verification: Literal["kept", "dropped"] | None = None
    verified_severity: Severity | None = None
    verification_reason: str | None = None

    @model_validator(mode="after")
    def _default_end_line(self) -> Finding:
        # Agents sometimes omit end_line; treat a single-line span (end == start)
        # as the default so a missing field never drops an otherwise-valid finding.
        if self.end_line is None:
            self.end_line = self.line
        return self

    def model_post_init(self, __context) -> None:
        if not self.id:
            raw = f"{self.pass_name}-{self.file}-{self.line}-{self.title}"
            self.id = f"{self.pass_name}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    @property
    def dedup_key(self) -> str:
        """Stable identity for cross-pass deduplication.

        Excludes ``pass_name`` deliberately: two different passes flagging the
        same file/line/title describe the same underlying issue and should
        collapse during merge. ``id`` keeps ``pass_name`` because the memory
        store keys persisted rows by it.
        """
        med = hashlib.sha256(f"{self.file}-{self.line}-{self.title}".encode()).hexdigest()[:16]
        return med


class ReviewUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    per_pass: dict[str, tuple[int, int]] = Field(default_factory=dict)


class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dropped_findings: list[Finding] = Field(default_factory=list)
    usage: ReviewUsage = Field(default_factory=ReviewUsage)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts
