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


class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts
