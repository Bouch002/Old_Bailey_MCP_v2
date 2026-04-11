"""
Smoke tests — hit the real Old Bailey API.
Run with: uv run pytest tests/test_smoke.py -v -m slow --run-slow
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import server


@pytest.mark.slow
class TestGillanWitness:
    """John Gillan, badge 707 V, police witness 1890–1913."""

    def test_finds_gillan_by_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.find_person(
                    name="Gillan", role="officer", date_from="1890", date_to="1913"
                )
                assert result["total_found"] > 0 or len(result["records"]) > 0

    def test_inspector_gillan_quoted_phrase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.search_proceedings(
                    query='"Inspector Gillan"', date_from="1890", date_to="1913"
                )
                assert result["total"] >= 1

    def test_badge_707_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.search_proceedings(query='"707 V"')
                assert result["total"] >= 1

    def test_second_call_uses_knowledge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                # First call hits API
                result1 = server.find_person(
                    name="Gillan", role="officer", date_from="1890", date_to="1913"
                )
                assert result1["source"] == "api"
                # Second call must use knowledge file
                result2 = server.find_person(
                    name="Gillan", role="officer", date_from="1890", date_to="1913"
                )
                assert result2["source"] == "knowledge"


@pytest.mark.slow
class TestDoddDefendant:
    """William Dodd, defendant, hanged 1777."""

    def test_finds_dodd_as_defendant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.find_person(
                    name="William Dodd", role="defendant", date_from="1770", date_to="1780"
                )
                # The defendant endpoint titles name the primary defendant, not always Dodd.
                # Accept a match in either title or snippet.
                assert any(
                    "Dodd" in r.get("title", "") or "Dodd" in r.get("snippet", "")
                    for r in result["records"]
                )


@pytest.mark.slow
class TestCrossover:
    """Cross-name search."""

    def test_plus_operator_not_and(self):
        """Verify + query returns far fewer hits than unquoted AND."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.search_proceedings(query='+"Gillan" +"inspector"')
                # AND was returning ~182,000 — correct + query should be <10,000
                assert result["total"] < 10_000


@pytest.mark.slow
class TestResources:
    def test_list_known_after_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                server.find_person(name="Gillan", role="officer", date_from="1890", date_to="1913")
                listing = server.list_known()
                assert "Gillan" in listing

    def test_get_known_returns_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                server.find_person(name="Gillan", role="officer", date_from="1890", date_to="1913")
                detail = server.get_known("Gillan")
                data = json.loads(detail)
                assert "records" in data
