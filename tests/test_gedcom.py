import tempfile
from pathlib import Path
from unittest.mock import patch

import server

SAMPLE_GED = """\
0 HEAD
1 GEDC
2 VERS 5.5
0 @I42@ INDI
1 NAME John /Gillan/
2 GIVN John
2 SURN Gillan
1 SEX M
1 BIRT
2 DATE ABT 1860
1 DEAT
2 DATE 1925
1 OCCU Police Inspector
0 @I99@ INDI
1 NAME William /Dodd/
1 SEX M
1 BIRT
2 DATE 1729
1 DEAT
2 DATE 1777
1 OCCU Clergyman
0 @I55@ INDI
1 NAME Mary /Jones/
1 SEX F
0 TRLR
"""


def _parse_with_sample(gedcom_id: str) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ged", delete=False, encoding="utf-8"
    ) as f:
        f.write(SAMPLE_GED)
        path = f.name
    with patch.object(server, "GEDCOM_FILE", path):
        return server._parse_gedcom(gedcom_id)


class TestParseGedcom:
    def test_birth_year_extracted(self):
        result = _parse_with_sample("@I42@")
        assert result["birth_year"] == 1860

    def test_death_year_extracted(self):
        result = _parse_with_sample("@I42@")
        assert result["death_year"] == 1925

    def test_occupation_extracted(self):
        result = _parse_with_sample("@I42@")
        assert result["occupation"] == "Police Inspector"

    def test_second_individual(self):
        result = _parse_with_sample("@I99@")
        assert result["birth_year"] == 1729
        assert result["death_year"] == 1777
        assert result["occupation"] == "Clergyman"

    def test_missing_occupation_returns_none(self):
        result = _parse_with_sample("@I55@")
        assert result["occupation"] is None

    def test_missing_dates_return_none(self):
        result = _parse_with_sample("@I55@")
        assert result["birth_year"] is None
        assert result["death_year"] is None

    def test_unknown_id_returns_empty(self):
        result = _parse_with_sample("@I999@")
        assert result == {}

    def test_no_gedcom_file_returns_empty(self):
        with patch.object(server, "GEDCOM_FILE", None):
            assert server._parse_gedcom("@I42@") == {}


class TestOccupationToRole:
    def test_inspector_is_officer(self):
        assert server._occupation_to_role("Police Inspector") == "officer"

    def test_constable_is_officer(self):
        assert server._occupation_to_role("Police Constable") == "officer"

    def test_detective_is_officer(self):
        assert server._occupation_to_role("Detective Sergeant") == "officer"

    def test_clergyman_is_any(self):
        assert server._occupation_to_role("Clergyman") == "any"

    def test_empty_is_any(self):
        assert server._occupation_to_role("") == "any"

    def test_none_is_any(self):
        assert server._occupation_to_role(None) == "any"
