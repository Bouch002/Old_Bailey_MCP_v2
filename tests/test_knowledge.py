import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import server


def _with_knowledge(content: dict, fn):
    """Run fn with KNOWLEDGE_FILE pointing at a temp file containing content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "persons.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        with patch.object(server, "KNOWLEDGE_FILE", path):
            return fn()


def _with_empty_knowledge(fn):
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "persons.json"
        with patch.object(server, "KNOWLEDGE_FILE", path):
            return fn()


class TestLoadKnowledge:
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                assert server._load_knowledge() == {}

    def test_loads_existing_data(self):
        data = {"@I42@": {"name": "John Gillan", "records": []}}
        result = _with_knowledge(data, server._load_knowledge)
        assert result["@I42@"]["name"] == "John Gillan"

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            path.write_text("NOT JSON", encoding="utf-8")
            with patch.object(server, "KNOWLEDGE_FILE", path):
                assert server._load_knowledge() == {}


class TestSaveKnowledge:
    def test_saves_and_reloads(self):
        def run():
            data = {"@I42@": {"name": "John Gillan"}}
            server._save_knowledge(data)
            return server._load_knowledge()

        result = _with_empty_knowledge(run)
        assert result["@I42@"]["name"] == "John Gillan"

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                server._save_knowledge({"x": 1})
                assert path.exists()


class TestIsRangeCovered:
    def _person(self, ranges):
        return {
            "records": [{"idkey": "x"}],
            "date_ranges_covered": ranges,
        }

    def test_exact_range_covered(self):
        person = self._person([["1890", "1913"]])
        assert server._is_range_covered(person, "1890", "1913") is True

    def test_wider_range_covers_narrower(self):
        person = self._person([["1880", "1920"]])
        assert server._is_range_covered(person, "1890", "1913") is True

    def test_narrower_range_does_not_cover(self):
        person = self._person([["1895", "1905"]])
        assert server._is_range_covered(person, "1890", "1913") is False

    def test_no_filter_covered_when_records_exist(self):
        person = self._person([["1890", "1913"]])
        assert server._is_range_covered(person, None, None) is True

    def test_no_filter_not_covered_when_no_records(self):
        person = {"records": [], "date_ranges_covered": []}
        assert server._is_range_covered(person, None, None) is False

    def test_open_ended_range(self):
        person = self._person([["1880", None]])
        assert server._is_range_covered(person, "1890", None) is True


class TestMergeResults:
    def test_creates_new_person_entry(self):
        knowledge = {}
        server._merge_results(
            knowledge, "@I42@", "John Gillan", "@I42@",
            "1890", "1913",
            [{"idkey": "t18990109-146", "year": 1899, "title": "Test"}],
            [],
        )
        assert "@I42@" in knowledge
        assert knowledge["@I42@"]["name"] == "John Gillan"
        assert len(knowledge["@I42@"]["records"]) == 1

    def test_deduplicates_records_by_idkey(self):
        knowledge = {}
        rec = {"idkey": "t18990109-146", "year": 1899, "title": "Test"}
        server._merge_results(knowledge, "@I42@", "John Gillan", "@I42@", None, None, [rec], [])
        server._merge_results(knowledge, "@I42@", "John Gillan", "@I42@", None, None, [rec], [])
        assert len(knowledge["@I42@"]["records"]) == 1

    def test_pending_not_duplicated_across_reviewed(self):
        knowledge = {}
        rec = {"idkey": "t18990109-146", "year": 1899, "title": "Test"}
        server._merge_results(knowledge, "@I42@", "John Gillan", "@I42@", None, None, [rec], [])
        # same idkey should not appear in pending
        server._merge_results(knowledge, "@I42@", "John Gillan", "@I42@", None, None, [], [rec])
        assert len(knowledge["@I42@"].get("pending_review", [])) == 0

    def test_records_range_tracked(self):
        knowledge = {}
        server._merge_results(knowledge, "Gillan", "Gillan", None, "1890", "1913", [], [])
        assert ["1890", "1913"] in knowledge["Gillan"]["date_ranges_covered"]
