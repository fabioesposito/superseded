from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo: Mapped[str | None] = mapped_column(String)
    pass_: Mapped[str | None] = mapped_column("pass", String)
    severity: Mapped[str | None] = mapped_column(String)
    file: Mapped[str | None] = mapped_column(String)
    line: Mapped[int | None] = mapped_column(Integer)
    reasoning: Mapped[str] = mapped_column(String, default="", server_default="")
    title: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    comment_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    finding_id: Mapped[str | None] = mapped_column(String, ForeignKey("findings.id"))
    action: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Installation(Base):
    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    app_installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    repos: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewWatermark(Base):
    __tablename__ = "review_watermarks"

    repo: Mapped[str] = mapped_column(String, primary_key=True)
    pr_number: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    head_sha: Mapped[str] = mapped_column(String, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReviewStat(Base):
    __tablename__ = "review_stats"

    repo: Mapped[str] = mapped_column(String, primary_key=True)
    pass_: Mapped[str] = mapped_column("pass", String, primary_key=True)
    severity: Mapped[str] = mapped_column(String, primary_key=True)
    file_pattern: Mapped[str] = mapped_column(
        String, primary_key=True, default="*", server_default="*"
    )
    total: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    accepted: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    dismissed: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearnedRule(Base):
    __tablename__ = "learned_rules"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    repo: Mapped[str] = mapped_column(String, nullable=False)
    rule_text: Mapped[str] = mapped_column(String, nullable=False)
    evidence_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReflectionState(Base):
    __tablename__ = "reflection_state"

    repo: Mapped[str] = mapped_column(String, primary_key=True)
    last_feedback_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_reflection_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InstallationConfig(Base):
    __tablename__ = "installation_config"

    installation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("installations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
