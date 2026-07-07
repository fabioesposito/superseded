from __future__ import annotations

import sqlalchemy as sa

from superseded.memory.models import Base


def test_metadata_defines_all_eight_tables():
    expected = {
        "findings",
        "feedback",
        "installations",
        "review_watermarks",
        "review_stats",
        "learned_rules",
        "reflection_state",
        "installation_config",
    }
    assert set(Base.metadata.tables) == expected


def test_findings_comment_id_is_biginteger():
    col = Base.metadata.tables["findings"].c.comment_id
    assert isinstance(col.type, sa.BigInteger)


def test_timestamps_are_timezone_aware():
    col = Base.metadata.tables["findings"].c.created_at
    assert isinstance(col.type, sa.DateTime)
    assert col.type.timezone is True


def test_findings_dismissed_is_boolean():
    col = Base.metadata.tables["findings"].c.dismissed
    assert isinstance(col.type, sa.Boolean)


def test_installation_config_has_fk_to_installations():
    fks = {
        tuple(fk.column.table.name for fk in t.foreign_keys)
        for t in [Base.metadata.tables["installation_config"]]
    }
    assert ("installations",) in fks
