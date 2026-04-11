import pytest
import server


class TestYearFromIdkey:
    def test_trial_idkey(self):
        assert server._year_from_idkey("t18990109-146") == 1899

    def test_oa_idkey(self):
        assert server._year_from_idkey("OA17210517") == 1721

    def test_punishment_summary(self):
        assert server._year_from_idkey("s17410514-1") == 1741

    def test_defendant_subsection(self):
        assert server._year_from_idkey("t17410514-47-defend361") == 1741

    def test_empty_string(self):
        assert server._year_from_idkey("") is None

    def test_none(self):
        assert server._year_from_idkey(None) is None


class TestDateInRange:
    def test_within_range(self):
        assert server._date_in_range("t18990109-146", 1890, 1900) is True

    def test_before_range(self):
        assert server._date_in_range("t18800101-1", 1890, 1900) is False

    def test_after_range(self):
        assert server._date_in_range("t19100101-1", 1890, 1900) is False

    def test_no_filter(self):
        assert server._date_in_range("t18800101-1", None, None) is True

    def test_only_from(self):
        assert server._date_in_range("t18800101-1", 1890, None) is False
        assert server._date_in_range("t19000101-1", 1890, None) is True

    def test_only_to(self):
        assert server._date_in_range("t19200101-1", None, 1913) is False
        assert server._date_in_range("t19000101-1", None, 1913) is True

    def test_unparseable_idkey_passes_through(self):
        # Can't tell the year — include rather than drop
        assert server._date_in_range("ar_24593_11886", 1890, 1900) is True


class TestExtractHits:
    def test_total_as_int(self):
        raw = {"hits": {"total": 5, "hits": []}}
        result = server._extract_hits(raw)
        assert result["total"] == 5

    def test_total_as_dict(self):
        raw = {"hits": {"total": {"value": 42}, "hits": []}}
        result = server._extract_hits(raw)
        assert result["total"] == 42

    def test_hits_returned(self):
        hit = {"_id": "x", "_source": {"idkey": "t18990109-1", "title": "Test"}}
        raw = {"hits": {"total": 1, "hits": [hit]}}
        result = server._extract_hits(raw)
        assert len(result["hits"]) == 1
        assert result["hits"][0]["_id"] == "x"
