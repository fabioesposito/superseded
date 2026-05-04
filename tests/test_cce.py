from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from superseded.harness.cce import CCEClient


class TestCCEClient:
    def test_available_when_cce_in_path(self):
        with patch("shutil.which", return_value="/usr/bin/cce"):
            client = CCEClient("/tmp/test")
            assert client.available is True

    def test_not_available_when_cce_missing(self):
        with patch("shutil.which", return_value=None):
            client = CCEClient("/tmp/test")
            assert client.available is False

    def test_is_indexed_false_when_no_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CCEClient(tmp)
            assert client.is_indexed() is False

    def test_is_indexed_true_when_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".context-engine").mkdir()
            client = CCEClient(tmp)
            assert client.is_indexed() is True

    def test_is_stale_when_no_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CCEClient(tmp)
            assert client.is_stale() is True

    def test_is_stale_false_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".context-engine"
            idx.mkdir()
            db = idx / "index.db"
            db.write_text("test")
            client = CCEClient(tmp)
            assert client.is_stale(max_age_minutes=60) is False

    def test_is_stale_true_when_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".context-engine"
            idx.mkdir()
            db = idx / "index.db"
            db.write_text("test")
            old_time = __import__("time").time() - 7200
            os.utime(db, (old_time, old_time))
            client = CCEClient(tmp)
            assert client.is_stale(max_age_minutes=60) is True

    def test_parse_search_results(self):
        client = CCEClient("/tmp/test")
        raw = json.dumps([
            {"file": "main.go", "chunk": "func main() {}", "score": 0.95, "compressed": "func main()"},
            {"file": "auth.go", "chunk": "func login() {}", "score": 0.80, "compressed": "func login()"},
        ])
        results = client._parse_search_results(raw)
        assert len(results) == 2
        assert results[0].file == "main.go"
        assert results[0].score == 0.95
        assert results[1].file == "auth.go"

    def test_parse_search_results_empty(self):
        client = CCEClient("/tmp/test")
        assert client._parse_search_results("") == []
        assert client._parse_search_results("invalid json") == []
        assert client._parse_search_results("null") == []
