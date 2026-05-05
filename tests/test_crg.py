from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from superseded.harness.crg import CRGClient


class TestCRGClient:
    def test_available_when_crg_in_path(self):
        with patch("shutil.which", return_value="/usr/bin/code-review-graph"):
            client = CRGClient("/tmp/test")
            assert client.available is True

    def test_not_available_when_crg_missing(self):
        with patch("shutil.which", return_value=None):
            client = CRGClient("/tmp/test")
            assert client.available is False

    def test_is_built_false_when_no_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CRGClient(tmp)
            assert client.is_built() is False

    def test_is_built_true_when_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".code-review-graph").mkdir()
            client = CRGClient(tmp)
            assert client.is_built() is True

    def test_is_stale_when_no_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CRGClient(tmp)
            assert client.is_stale() is True

    def test_is_stale_false_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".code-review-graph"
            idx.mkdir()
            db = idx / "graph.db"
            db.write_text("test")
            client = CRGClient(tmp)
            assert client.is_stale(max_age_minutes=60) is False

    def test_is_stale_true_when_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".code-review-graph"
            idx.mkdir()
            db = idx / "graph.db"
            db.write_text("test")
            old_time = __import__("time").time() - 7200
            os.utime(db, (old_time, old_time))
            client = CRGClient(tmp)
            assert client.is_stale(max_age_minutes=60) is True
