import server


class TestBuildQuery:
    def test_any_role_quotes_name(self):
        assert server._build_query("John Gillan", "any") == '"John Gillan"'

    def test_defendant_role_quotes_name(self):
        assert server._build_query("Gillan", "defendant") == '"Gillan"'

    def test_victim_role_quotes_name(self):
        assert server._build_query("Gillan", "victim") == '"Gillan"'

    def test_officer_role_adds_plus_terms(self):
        query = server._build_query("Gillan", "officer")
        assert query.startswith('+"Gillan"')
        assert "inspector" in query
        assert "constable" in query
        assert "sergeant" in query
        assert "detective" in query

    def test_officer_query_uses_plus_not_and(self):
        query = server._build_query("Gillan", "officer")
        assert " AND " not in query

    def test_officer_query_no_double_plus(self):
        query = server._build_query("Gillan", "officer")
        assert "++" not in query

    def test_strips_whitespace(self):
        assert server._build_query("  Gillan  ", "any") == '"Gillan"'


class TestRoleEndpoint:
    def test_defendant(self):
        assert server._role_endpoint("defendant") == "oldbailey_defendant"

    def test_victim(self):
        assert server._role_endpoint("victim") == "oldbailey_victim"

    def test_officer(self):
        assert server._role_endpoint("officer") == "oldbailey_record"

    def test_any(self):
        assert server._role_endpoint("any") == "oldbailey_record"


class TestFormatRecord:
    def _make_hit(self, idkey, text="test text", images=None):
        return {
            "_id": idkey,
            "_source": {
                "idkey": idkey,
                "title": f"Title for {idkey}",
                "text": text,
                "images": images or [],
                "offenceCategories": ["theft"],
                "verdictCategories": ["guilty"],
                "punishmentCategories": [],
                "collection": "proceedings",
            },
        }

    def test_basic_fields(self):
        hit = self._make_hit("t18990109-146")
        rec = server._format_record(hit)
        assert rec["idkey"] == "t18990109-146"
        assert rec["year"] == 1899
        assert rec["title"] == "Title for t18990109-146"

    def test_snippet_truncated(self):
        hit = self._make_hit("t18990109-146", text="x" * 1000)
        rec = server._format_record(hit, snippet_length=200)
        assert len(rec["snippet"]) == 200

    def test_image_url_extracted(self):
        hit = self._make_hit(
            "t18990109-146", images=["https://example.com/scan.gif"]
        )
        rec = server._format_record(hit)
        assert rec["image_url"] == "https://example.com/scan.gif"

    def test_no_image_returns_none(self):
        hit = self._make_hit("t18990109-146")
        rec = server._format_record(hit)
        assert rec["image_url"] is None
