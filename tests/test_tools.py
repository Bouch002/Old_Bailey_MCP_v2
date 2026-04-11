import json
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import server


def _make_raw(total: int, hits: list) -> dict:
    return {"hits": {"total": {"value": total}, "hits": hits}}


def _make_hit(idkey: str, text: str = "test transcript") -> dict:
    return {
        "_id": idkey,
        "_source": {
            "idkey": idkey,
            "title": f"Trial {idkey}",
            "text": text,
            "images": [f"https://example.com/{idkey}.gif"],
            "offenceCategories": ["theft"],
            "verdictCategories": ["guilty"],
            "punishmentCategories": ["transportation"],
            "collection": "proceedings",
        },
    }


def _empty_knowledge_dir():
    return tempfile.TemporaryDirectory()


class TestFindPerson:
    def test_api_called_with_quoted_name(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(1, [_make_hit("t18990109-146")])
                    server.find_person(name="Gillan")
                    call_args = mock_get.call_args
                    assert '"Gillan"' in call_args[0][1]["text"]

    def test_returns_records(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(1, [_make_hit("t18990109-146")])
                    result = server.find_person(name="Gillan")
                    assert len(result["records"]) == 1
                    assert result["records"][0]["idkey"] == "t18990109-146"

    def test_knowledge_first_skips_api(self):
        existing = {
            "Gillan": {
                "name": "Gillan",
                "gedcom_id": None,
                "last_searched": "2026-01-01",
                "date_ranges_covered": [[None, None]],
                "records": [{"idkey": "t18990109-146", "year": 1899, "title": "X"}],
                "pending_review": [],
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            path.write_text(json.dumps(existing), encoding="utf-8")
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    result = server.find_person(name="Gillan")
                    mock_get.assert_not_called()
                    assert result["source"] == "knowledge"

    def test_index_mode_when_over_threshold(self):
        hits = [_make_hit(f"t1899{i:04d}-1") for i in range(12)]
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(12, hits)
                    result = server.find_person(name="Smith", size=8)
                    assert result.get("mode") == "index"
                    assert len(result["records"]) == 8
                    assert result["pending_logged"] == 4

    def test_writes_to_knowledge_file(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(1, [_make_hit("t18990109-146")])
                    server.find_person(name="Gillan")
                    saved = json.loads(path.read_text())
                    assert "Gillan" in saved

    def test_defendant_role_uses_defendant_endpoint(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(0, [])
                    server.find_person(name="Gillan", role="defendant")
                    endpoint = mock_get.call_args[0][0]
                    assert endpoint == "oldbailey_defendant"

    def test_date_filter_applied(self):
        hits = [
            _make_hit("t18500101-1"),  # 1850 — out of range
            _make_hit("t18990109-146"),  # 1899 — in range
        ]
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(2, hits)
                    result = server.find_person(
                        name="Gillan", date_from="1890", date_to="1913"
                    )
                    assert all(
                        1890 <= r["year"] <= 1913 for r in result["records"]
                    )

    def test_gedcom_enrichment_sets_officer_query(self):
        gedcom_data = {"birth_year": 1860, "death_year": 1925, "occupation": "Police Inspector"}
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    with patch.object(server, "_parse_gedcom", return_value=gedcom_data):
                        mock_get.return_value = _make_raw(0, [])
                        server.find_person(name="Gillan", gedcom_id="@I42@")
                        params = mock_get.call_args[0][1]
                        # officer role from "Police Inspector" — query should have + terms
                        assert '+"Gillan"' in params["text"]


class TestFindCrossover:
    def test_knowledge_intersection_zero_api_calls(self):
        existing = {
            "Gillan": {
                "name": "Gillan", "gedcom_id": None, "last_searched": "2026-01-01",
                "date_ranges_covered": [], "pending_review": [],
                "records": [
                    {"idkey": "t18990109-146", "year": 1899, "title": "X"},
                    {"idkey": "t19000101-1",   "year": 1900, "title": "Y"},
                ],
            },
            "Walsh": {
                "name": "Walsh", "gedcom_id": None, "last_searched": "2026-01-01",
                "date_ranges_covered": [], "pending_review": [],
                "records": [
                    {"idkey": "t18990109-146", "year": 1899, "title": "X"},
                    {"idkey": "t19010101-2",   "year": 1901, "title": "Z"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            path.write_text(json.dumps(existing), encoding="utf-8")
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    result = server.find_crossover(names=["Gillan", "Walsh"])
                    mock_get.assert_not_called()
                    assert len(result["shared_cases"]) == 1
                    assert result["shared_cases"][0]["idkey"] == "t18990109-146"
                    assert result["source"] == "knowledge"

    def test_api_fallback_when_name_unknown(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(1, [_make_hit("t18990109-146")])
                    result = server.find_crossover(names=["Gillan", "Walsh"])
                    mock_get.assert_called_once()
                    call_text = mock_get.call_args[0][1]["text"]
                    assert '+"Gillan"' in call_text
                    assert '+"Walsh"' in call_text

    def test_rejects_fewer_than_two_names(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.find_crossover(names=["Gillan"])
                assert "error" in result

    def test_rejects_more_than_five_names(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.find_crossover(names=["A", "B", "C", "D", "E", "F"])
                assert "error" in result


class TestSearchProceedings:
    def test_passes_query_to_api(self):
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(0, [])
                    server.search_proceedings(query='+"forgery" +"Bank of England"')
                    endpoint, params = mock_get.call_args[0]
                    assert endpoint == "oldbailey_record"
                    assert '+"forgery"' in params["text"]

    def test_date_filter_applied(self):
        hits = [_make_hit("t18200101-1"), _make_hit("t18990109-146")]
        with _empty_knowledge_dir() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                with patch.object(server, "_get") as mock_get:
                    mock_get.return_value = _make_raw(2, hits)
                    result = server.search_proceedings(
                        query="forgery", date_from="1890", date_to="1913"
                    )
                    assert all(r["year"] >= 1890 for r in result["results"])


class TestSearchOrdinaries:
    def test_uses_oa_endpoint(self):
        with patch.object(server, "_get") as mock_get:
            mock_get.return_value = _make_raw(0, [])
            server.search_ordinaries(text="Dodd")
            endpoint = mock_get.call_args[0][0]
            assert endpoint == "oldbailey_oa"


class TestSearchAssociated:
    def test_uses_assocrec_endpoint(self):
        with patch.object(server, "_get") as mock_get:
            mock_get.return_value = _make_raw(0, [])
            server.search_associated(text="petition")
            endpoint = mock_get.call_args[0][0]
            assert endpoint == "oldbailey_assocrec"


class TestGetRecord:
    def test_fetches_single_record(self):
        raw = {
            "hits": {
                "total": {"value": 1},
                "hits": [_make_hit("t18990109-146", text="Full transcript here.")],
            }
        }
        with patch.object(server, "_get") as mock_get:
            mock_get.return_value = raw
            result = server.get_record(idkey="t18990109-146")
            assert result["idkey"] == "t18990109-146"
            assert "Full transcript here." in result["text"]

    def test_missing_record_returns_error(self):
        raw = {"hits": {"total": {"value": 0}, "hits": []}}
        with patch.object(server, "_get") as mock_get:
            mock_get.return_value = raw
            result = server.get_record(idkey="t99999999-1")
            assert "error" in result
