from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "important", "suggestion", "nit"]
PassName = Literal["security", "correctness", "performance", "style", "architecture"]
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    pass_name: PassName
    severity: Severity
    file: str
    line: int
    end_line: int
    title: str
    description: str
    suggestion: str
    confidence: Confidence = "high"
    reasoning: str = Field(default="")
    id: str = Field(default="")

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


class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts
