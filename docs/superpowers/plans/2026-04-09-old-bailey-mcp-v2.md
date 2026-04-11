# Old Bailey MCP v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastMCP server for the Old Bailey Online API with persistent knowledge file, GEDCOM integration, 6 tools, 2 resources, and correct Lucene query syntax — replacing a hand-rolled JSON-RPC server that made 3–4 sequential API calls per person lookup.

**Architecture:** Single `server.py` using FastMCP decorators. All tool logic lives in private `_` helper functions so tests can call them directly without MCP machinery. A persistent `knowledge/persons.json` accumulates discoveries across sessions; MCP resources expose it for read-only access. Tools check the knowledge file first and only hit the API for genuinely new queries.

**Tech Stack:** Python 3.13, FastMCP 3.2.3, httpx (sync), python-dotenv, pytest

**Run all tests:** `uv run pytest tests/ -v`
**Run server:** `uv run python server.py`

---

## File Map

| File | Responsibility |
|---|---|
| `server.py` | Entire server — helpers, tools, resources, FastMCP wiring |
| `tests/conftest.py` | sys.path setup so tests can import server |
| `tests/test_utils.py` | HTTP helpers, date utilities |
| `tests/test_knowledge.py` | Knowledge file load/save/merge/range-check |
| `tests/test_gedcom.py` | GEDCOM parser + occupation→role mapping |
| `tests/test_query.py` | Query builder — correct Lucene syntax |
| `tests/test_tools.py` | Tool functions with mocked `_get` |
| `tests/test_smoke.py` | Real API calls (marked slow) |
| `requirements.txt` | fastmcp, httpx, python-dotenv |
| `.env.example` | Template — GEDCOM_FILE= |
| `.gitignore` | Covers .env, knowledge/, *.log |

---

## Task 1: Project Scaffold

**Files:**
- Modify: `pyproject.toml`
- Create: `requirements.txt`
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `tests/conftest.py`

- [ ] **Step 1: Update pyproject.toml**

Replace the contents of `pyproject.toml` with:

```toml
[project]
name = "old-bailey-mcp-v2"
version = "2.0.0"
description = "Old Bailey Online MCP server — living research memory for 240 years of London criminal court records"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "fastmcp>=3.2.3",
    "httpx>=0.28.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=9.0.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Create requirements.txt**

```
fastmcp
httpx
python-dotenv
```

- [ ] **Step 3: Update .gitignore**

Replace contents of `.gitignore` with:

```gitignore
# Local config — contains personal file paths
.env

# Personal research data — never commit
knowledge/

# Logs — contain real names and API queries
*.log
*.log.*

# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/
dist/
```

- [ ] **Step 4: Create .env.example**

```env
# Old Bailey MCP v2 — copy to .env and fill in your values
# Never commit .env

# Optional: path to your GEDCOM file (.ged)
# When set, find_person(gedcom_id=...) auto-fills dates and role from your family tree
GEDCOM_FILE=
```

- [ ] **Step 5: Create tests/conftest.py**

```python
import sys
from pathlib import Path

# Allow tests to import server.py from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 6: Create the knowledge directory marker**

```bash
mkdir -p knowledge
echo '{}' > knowledge/.gitkeep
```

Add `knowledge/.gitkeep` exception to `.gitignore` so the empty dir is tracked but
its contents are not:

```gitignore
knowledge/*
!knowledge/.gitkeep
```

(Replace the `knowledge/` line added in Step 3 with these two lines.)

- [ ] **Step 7: Verify setup**

```bash
uv run python -c "import fastmcp, httpx, dotenv; print('OK')"
uv run pytest tests/ --collect-only
```

Expected: `OK` then `no tests ran`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml requirements.txt .gitignore .env.example tests/conftest.py knowledge/.gitkeep
git commit -m "chore: scaffold v2 project — deps, gitignore, test harness"
```

---

## Task 2: HTTP Utilities + Date Helpers

**Files:**
- Create: `server.py` (initial skeleton)
- Create: `tests/test_utils.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_utils.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'server'` or `ImportError`.

- [ ] **Step 3: Create server.py with the utilities implemented**

Create `server.py`:

```python
#!/usr/bin/env python3
"""Old Bailey Online MCP Server v2 — living research memory."""

import json
import logging
import logging.handlers
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import CallToolSettings, ResponseCachingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────

BASE_URL = "https://www.dhi.ac.uk/api/data"
KNOWLEDGE_FILE = Path(__file__).parent / "knowledge" / "persons.json"
GEDCOM_FILE: Optional[str] = os.getenv("GEDCOM_FILE")
INDEX_THRESHOLD = 8  # switch to index mode (short snippets + pending log) above this

# ── Logging ──────────────────────────────────────────────────────────────────

_LOG_FILE = Path(__file__).parent / "oldbailey_mcp.log"
_fmt = logging.Formatter(
    "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_fmt)
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(_fmt)
log = logging.getLogger("oldbailey_mcp")
log.setLevel(logging.DEBUG)
log.addHandler(_file_handler)
log.addHandler(_stderr_handler)

# ── HTTP layer ───────────────────────────────────────────────────────────────

_http = httpx.Client(timeout=15.0, headers={"User-Agent": "OldBaileyMCP/2.0"})


def _get(endpoint: str, params: dict) -> dict:
    clean = {k: v for k, v in params.items() if v is not None}
    url = f"{BASE_URL}/{endpoint}"
    log.debug("GET %s %s", endpoint, clean)
    resp = _http.get(url, params=clean)
    resp.raise_for_status()
    return resp.json()


def _extract_hits(raw: dict) -> dict:
    total = raw.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        total = total.get("value", 0)
    hits = raw.get("hits", {}).get("hits", [])
    return {"total": total, "hits": hits}


def _year_from_idkey(idkey: Optional[str]) -> Optional[int]:
    if not idkey:
        return None
    m = re.search(r"(\d{4})", idkey)
    return int(m.group(1)) if m else None


def _date_in_range(idkey: str, year_from: Optional[int], year_to: Optional[int]) -> bool:
    if year_from is None and year_to is None:
        return True
    year = _year_from_idkey(idkey)
    if year is None:
        return True  # can't determine — include rather than drop
    if year_from is not None and year < year_from:
        return False
    if year_to is not None and year > year_to:
        return False
    return True
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
uv run pytest tests/test_utils.py -v
```

Expected: all 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_utils.py tests/conftest.py
git commit -m "feat: HTTP utilities and date helpers with tests"
```

---

## Task 3: Knowledge File

**Files:**
- Modify: `server.py` (add knowledge helpers)
- Create: `tests/test_knowledge.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_knowledge.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_knowledge.py -v
```

Expected: `AttributeError: module 'server' has no attribute '_load_knowledge'`

- [ ] **Step 3: Add knowledge helpers to server.py**

Add after the `_date_in_range` function:

```python
# ── Knowledge file ───────────────────────────────────────────────────────────

def _load_knowledge() -> dict:
    if not KNOWLEDGE_FILE.exists():
        return {}
    try:
        return json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_knowledge(data: dict) -> None:
    KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _is_range_covered(
    person: dict, date_from: Optional[str], date_to: Optional[str]
) -> bool:
    if not date_from and not date_to:
        return bool(person.get("records") or person.get("pending_review"))
    req_from = int(date_from) if date_from else 0
    req_to = int(date_to) if date_to else 9999
    for r in person.get("date_ranges_covered", []):
        stored_from = int(r[0]) if r[0] else 0
        stored_to = int(r[1]) if r[1] else 9999
        if stored_from <= req_from and stored_to >= req_to:
            return True
    return False


def _merge_results(
    knowledge: dict,
    key: str,
    name: str,
    gedcom_id: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    records: list,
    pending: list,
) -> None:
    if key not in knowledge:
        knowledge[key] = {
            "name": name,
            "gedcom_id": gedcom_id,
            "last_searched": date.today().isoformat(),
            "date_ranges_covered": [],
            "records": [],
            "pending_review": [],
        }
    person = knowledge[key]
    person["last_searched"] = date.today().isoformat()
    if date_from or date_to:
        person["date_ranges_covered"].append([date_from, date_to])
    existing = {r["idkey"] for r in person["records"]}
    for rec in records:
        if rec["idkey"] not in existing:
            person["records"].append(rec)
            existing.add(rec["idkey"])
    pending_keys = {r["idkey"] for r in person.get("pending_review", [])}
    for rec in pending:
        if rec["idkey"] not in existing and rec["idkey"] not in pending_keys:
            person["pending_review"].append(rec)
            pending_keys.add(rec["idkey"])
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
uv run pytest tests/test_knowledge.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_knowledge.py
git commit -m "feat: knowledge file persistence — load, save, merge, range-check"
```

---

## Task 4: GEDCOM Parser

**Files:**
- Modify: `server.py` (add GEDCOM helpers)
- Create: `tests/test_gedcom.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gedcom.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_gedcom.py -v
```

Expected: `AttributeError: module 'server' has no attribute '_parse_gedcom'`

- [ ] **Step 3: Add GEDCOM helpers to server.py**

Add after the `_merge_results` function:

```python
# ── GEDCOM parser ────────────────────────────────────────────────────────────

_OFFICER_TERMS = {"police", "constable", "inspector", "detective", "sergeant", "officer"}


def _parse_gedcom(gedcom_id: str) -> dict:
    """Extract birth year, death year, occupation for a GEDCOM individual."""
    if not GEDCOM_FILE or not Path(GEDCOM_FILE).exists():
        return {}
    result: dict = {"birth_year": None, "death_year": None, "occupation": None}
    in_target = False
    in_birt = False
    in_deat = False
    try:
        with open(GEDCOM_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split(" ", 2)
                if len(parts) < 2:
                    continue
                level, tag = parts[0], parts[1]
                value = parts[2].strip() if len(parts) > 2 else ""
                if level == "0":
                    if gedcom_id in line:
                        in_target = "INDI" in line
                    elif in_target:
                        break
                    in_birt = in_deat = False
                    continue
                if not in_target:
                    continue
                if level == "1":
                    in_birt = tag == "BIRT"
                    in_deat = tag == "DEAT"
                    if tag == "OCCU":
                        result["occupation"] = value
                if level == "2" and tag == "DATE":
                    m = re.search(r"\b(\d{4})\b", value)
                    if m:
                        yr = int(m.group(1))
                        if in_birt:
                            result["birth_year"] = yr
                        elif in_deat:
                            result["death_year"] = yr
    except OSError:
        return {}
    # If we found no data at all, return empty rather than nulls
    if not any(result.values()):
        return {}
    return result


def _occupation_to_role(occupation: Optional[str]) -> str:
    if not occupation:
        return "any"
    occ = occupation.lower()
    if any(t in occ for t in _OFFICER_TERMS):
        return "officer"
    return "any"
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
uv run pytest tests/test_gedcom.py -v
```

Expected: all 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_gedcom.py
git commit -m "feat: lightweight GEDCOM parser with occupation-to-role mapping"
```

---

## Task 5: Query Builder

**Files:**
- Modify: `server.py` (add query helpers)
- Create: `tests/test_query.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_query.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_query.py -v
```

Expected: `AttributeError: module 'server' has no attribute '_build_query'`

- [ ] **Step 3: Add query helpers to server.py**

Add after `_occupation_to_role`:

```python
# ── Query builder ────────────────────────────────────────────────────────────

_OFFICER_QUERY = '+(inspector constable sergeant detective "police officer" "P.C." "D.C.")'


def _build_query(name: str, role: str) -> str:
    quoted = f'"{name.strip()}"'
    if role == "officer":
        return f"+{quoted} +{_OFFICER_QUERY}"
    return quoted


def _role_endpoint(role: str) -> str:
    if role == "defendant":
        return "oldbailey_defendant"
    if role == "victim":
        return "oldbailey_victim"
    return "oldbailey_record"


def _format_record(hit: dict, snippet_length: int = 400) -> dict:
    src = hit.get("_source", {})
    idkey = src.get("idkey") or hit.get("_id", "")
    images = src.get("images", [])
    return {
        "idkey": idkey,
        "year": _year_from_idkey(idkey),
        "title": src.get("title", ""),
        "snippet": (src.get("text", "") or "")[:snippet_length],
        "collection": src.get("collection", "proceedings"),
        "offences": src.get("offenceCategories", []),
        "verdicts": src.get("verdictCategories", []),
        "punishments": src.get("punishmentCategories", []),
        "image_url": images[0] if images else None,
        "div1_idkey": src.get("div1_idkey", idkey),
        "status": "reviewed",
    }
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
uv run pytest tests/test_query.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_query.py
git commit -m "feat: query builder with correct Lucene + operator and record formatter"
```

---

## Task 6: FastMCP Server Scaffold + Middleware

**Files:**
- Modify: `server.py` (add FastMCP instance, middleware, resources, entry point)

No new tests for this task — the server scaffold is verified by running it.

- [ ] **Step 1: Add FastMCP wiring to server.py**

Add after `_format_record`:

```python
# ── FastMCP server ───────────────────────────────────────────────────────────

mcp = FastMCP(
    "Old Bailey Online",
    instructions=(
        "Historical criminal court records for London, 1674–1913. "
        "Always use find_person for name lookups — it checks the knowledge file first "
        "so repeated searches cost zero API calls. "
        "Use search_proceedings for topic/Lucene queries only. "
        "When using Lucene, use + for required terms — NOT AND."
    ),
)

mcp.add_middleware(
    ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=300))
)
mcp.add_middleware(ResponseLimitingMiddleware(max_size=50_000))
mcp.add_middleware(LoggingMiddleware())
```

Add at the very bottom of the file (after all tool/resource definitions which come next):

```python
# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("SERVER START oldbailey-mcp-v2  logfile=%s", _LOG_FILE)
    mcp.run()
```

- [ ] **Step 2: Add the two MCP resources**

Add after the `mcp.add_middleware` block:

```python
# ── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("oldbailey://known/")
def list_known() -> str:
    """Index of all persons discovered so far. Read this before searching to avoid
    re-fetching data that is already in the knowledge file."""
    knowledge = _load_knowledge()
    if not knowledge:
        return "No persons in knowledge file yet."
    lines = ["Known persons:\n"]
    for key, person in knowledge.items():
        name = person.get("name", key)
        n_records = len(person.get("records", []))
        n_pending = len(person.get("pending_review", []))
        ranges = person.get("date_ranges_covered", [])
        range_str = ", ".join(f"{r[0]}–{r[1]}" for r in ranges) if ranges else "no date filter"
        lines.append(
            f"- {name} ({key}): {n_records} reviewed, {n_pending} pending | searched: {range_str}"
        )
    return "\n".join(lines)


@mcp.resource("oldbailey://known/{identifier}")
def get_known(identifier: str) -> str:
    """Full case history for one person from the knowledge file.
    Pass a GEDCOM ID (e.g. @I42@) or a name. No API calls made."""
    knowledge = _load_knowledge()
    person = knowledge.get(identifier)
    if not person:
        return f"No knowledge found for '{identifier}'."
    return json.dumps(person, indent=2, ensure_ascii=False)
```

- [ ] **Step 3: Verify the server starts without error**

```bash
uv run python -c "import server; print('Server imports OK, tools:', [t for t in dir(server) if not t.startswith('__')])"
```

Expected: no errors, prints module attributes including helpers.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: FastMCP scaffold, middleware, and knowledge file resources"
```

---

## Task 7: `find_person` Tool

**Files:**
- Modify: `server.py` (add find_person)
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools.py`:

```python
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

    def test_gedcom_enrichment_sets_dates(self):
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_tools.py::TestFindPerson -v
```

Expected: `AttributeError: module 'server' has no attribute 'find_person'`

- [ ] **Step 3: Add find_person to server.py**

Add after the resources block:

```python
# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def find_person(
    name: str,
    gedcom_id: Optional[str] = None,
    role: str = "any",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    offence: Optional[str] = None,
    size: int = INDEX_THRESHOLD,
) -> dict:
    """Search for a person in Old Bailey records (1674–1913).

    Checks the knowledge file first — zero API calls if already found.
    Returns an index list when results exceed the size threshold; additional
    cases are written to the knowledge file as pending_review for later review.

    STOP after reviewing the returned records. Only call get_record if a
    specific trial needs full text and the snippet is insufficient.
    Do NOT also call search_proceedings for name lookups.

    role options: 'any' (default) | 'defendant' | 'victim' | 'officer'
    """
    key = gedcom_id or name

    # GEDCOM enrichment — auto-fill dates and role from family tree file
    if gedcom_id and GEDCOM_FILE:
        gedcom_data = _parse_gedcom(gedcom_id)
        if date_from is None and gedcom_data.get("birth_year"):
            date_from = str(gedcom_data["birth_year"])
        if date_to is None and gedcom_data.get("death_year"):
            date_to = str(gedcom_data["death_year"])
        if role == "any" and gedcom_data.get("occupation"):
            role = _occupation_to_role(gedcom_data["occupation"])

    # Knowledge-first: return from file if already covered
    knowledge = _load_knowledge()
    if key in knowledge and _is_range_covered(knowledge[key], date_from, date_to):
        person = knowledge[key]
        return {
            "source": "knowledge",
            "name": person.get("name", name),
            "records": person.get("records", []),
            "pending_count": len(person.get("pending_review", [])),
        }

    # API search
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None
    query = _build_query(name, role)
    endpoint = _role_endpoint(role)
    fetch_size = min(200, size * 8) if (year_from or year_to) else min(size * 3, 50)
    params: dict = {"text": query, "size": fetch_size, "from": 0}
    if offence:
        params["offcat"] = offence.lower()

    raw = _get(endpoint, params)
    result = _extract_hits(raw)
    all_hits = list(result["hits"])

    # Fallback for 'any' role — try structured endpoints if full-text returns nothing
    if role == "any" and not all_hits:
        for fb_ep in ("oldbailey_defendant", "oldbailey_victim"):
            fb = _extract_hits(_get(fb_ep, {"text": f'"{name.strip()}"', "size": fetch_size}))
            all_hits.extend(fb["hits"])
            if all_hits:
                break

    # Date filter and split into reviewed / pending
    reviewed, pending = [], []
    for hit in all_hits:
        src = hit.get("_source", {})
        idkey = src.get("idkey") or hit.get("_id", "")
        if not _date_in_range(idkey, year_from, year_to):
            continue
        snippet_len = 150 if len(reviewed) >= size else 400
        rec = _format_record(hit, snippet_length=snippet_len)
        if len(reviewed) < size:
            reviewed.append(rec)
        else:
            rec["status"] = "pending_review"
            pending.append(rec)

    # Persist to knowledge file
    _merge_results(knowledge, key, name, gedcom_id, date_from, date_to, reviewed, pending)
    _save_knowledge(knowledge)

    log.info(
        "find_person name=%r role=%s reviewed=%d pending=%d total_api=%d",
        name, role, len(reviewed), len(pending), result["total"],
    )

    response: dict = {
        "source": "api",
        "name": name,
        "total_found": result["total"],
        "records": reviewed,
    }
    if pending:
        response["mode"] = "index"
        response["pending_logged"] = len(pending)
        response["message"] = (
            f"{len(pending)} additional cases written to knowledge file as pending_review. "
            "Read oldbailey://known/ to review them later."
        )
    return response
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
uv run pytest tests/test_tools.py::TestFindPerson -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_tools.py
git commit -m "feat: find_person tool with knowledge-first logic, GEDCOM enrichment, index mode"
```

---

## Task 8: `find_crossover` Tool

**Files:**
- Modify: `server.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Add tests for find_crossover**

Append to `tests/test_tools.py`:

```python
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
                    assert '"Gillan"' in call_text
                    assert '"Walsh"' in call_text

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
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_tools.py::TestFindCrossover -v
```

Expected: `AttributeError: module 'server' has no attribute 'find_crossover'`

- [ ] **Step 3: Add find_crossover to server.py**

Add after `find_person`:

```python
@mcp.tool()
def find_crossover(
    names: list,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Find cases where two or more people appear together.

    Checks knowledge file first — if all names are known, returns shared cases
    instantly with zero API calls. Pass 2–5 names or GEDCOM IDs.

    Use this to find conspiracy thread evidence: names that appear in the same trial.
    """
    if len(names) < 2:
        return {"error": "Provide at least 2 names."}
    if len(names) > 5:
        return {"error": "Maximum 5 names per crossover search."}

    knowledge = _load_knowledge()
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None

    # Knowledge-first: intersect idkey sets if all names known
    all_known = all(n in knowledge for n in names)
    if all_known:
        sets = [
            {r["idkey"] for r in knowledge[n].get("records", [])}
            for n in names
        ]
        shared_keys = set.intersection(*sets)
        # Collect full record details from first person's records
        first_records = {
            r["idkey"]: r for r in knowledge[names[0]].get("records", [])
        }
        shared = [
            first_records[k] for k in shared_keys
            if _date_in_range(k, year_from, year_to)
        ]
        return {
            "source": "knowledge",
            "names": names,
            "shared_cases": sorted(shared, key=lambda r: r.get("year", 0)),
        }

    # API fallback: build compound + query
    parts = [f'+"{n.strip()}"' for n in names]
    query = " ".join(parts)
    fetch_size = 50
    raw = _get("oldbailey_record", {"text": query, "size": fetch_size, "from": 0})
    result = _extract_hits(raw)

    records = []
    for hit in result["hits"]:
        src = hit.get("_source", {})
        idkey = src.get("idkey") or hit.get("_id", "")
        if not _date_in_range(idkey, year_from, year_to):
            continue
        records.append(_format_record(hit))

    # Write to knowledge under each name
    for n in names:
        _merge_results(knowledge, n, n, None, date_from, date_to, records, [])
    _save_knowledge(knowledge)

    return {
        "source": "api",
        "names": names,
        "shared_cases": records,
        "total_found": result["total"],
    }
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
uv run pytest tests/test_tools.py::TestFindCrossover -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_tools.py
git commit -m "feat: find_crossover tool — knowledge-first intersection, API fallback"
```

---

## Task 9: Remaining Tools

**Files:**
- Modify: `server.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Add tests for the remaining four tools**

Append to `tests/test_tools.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_tools.py::TestSearchProceedings tests/test_tools.py::TestSearchOrdinaries tests/test_tools.py::TestSearchAssociated tests/test_tools.py::TestGetRecord -v
```

Expected: `AttributeError` for each missing tool.

- [ ] **Step 3: Add remaining tools to server.py**

Add after `find_crossover`:

```python
@mcp.tool()
def search_proceedings(
    query: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    size: int = INDEX_THRESHOLD,
    from_: int = 0,
) -> dict:
    """Free-text / Lucene search of Old Bailey Proceedings (trials, verdicts, punishments).

    Use for topic searches, place names, offence types, or conspiracy research.
    Use + for required terms — NOT AND: +"forgery" +"Bank of England"
    Do NOT use for simple name lookups — use find_person instead.
    """
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None
    fetch_size = min(200, size * 4) if (year_from or year_to) else size
    raw = _get("oldbailey_record", {"text": query, "size": fetch_size, "from": from_})
    result = _extract_hits(raw)
    results = []
    for hit in result["hits"]:
        src = hit.get("_source", {})
        idkey = src.get("idkey") or hit.get("_id", "")
        if not _date_in_range(idkey, year_from, year_to):
            continue
        results.append(_format_record(hit))
        if len(results) >= size:
            break
    return {"total": result["total"], "results": results}


@mcp.tool()
def search_ordinaries(
    text: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    size: int = INDEX_THRESHOLD,
) -> dict:
    """Search Ordinary's Accounts — Newgate chaplain death-row interviews (1676–1772).

    Rich biographical detail: origins, trade, religion, last confession.
    Only relevant for pre-1773 cases with a death sentence.
    Do NOT call this routinely after every defendant search.
    """
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None
    fetch_size = min(200, size * 4) if (year_from or year_to) else size
    raw = _get("oldbailey_oa", {"text": text, "size": fetch_size, "from": 0})
    result = _extract_hits(raw)
    results = []
    for hit in result["hits"]:
        src = hit.get("_source", {})
        idkey = src.get("idkey") or hit.get("_id", "")
        if not _date_in_range(idkey, year_from, year_to):
            continue
        results.append({
            "idkey": idkey,
            "year": _year_from_idkey(idkey),
            "title": src.get("title", ""),
            "snippet": (src.get("text", "") or "")[:400],
            "image_url": (src.get("images") or [None])[0],
        })
        if len(results) >= size:
            break
    return {"total": result["total"], "results": results}


@mcp.tool()
def search_associated(
    text: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    size: int = INDEX_THRESHOLD,
) -> dict:
    """Search Associated Records — petitions, depositions, correspondence.

    Use when a trial result suggests follow-up documents exist, e.g. a death sentence
    that may have a mercy petition, or when researching evidence chains.
    Do NOT call this routinely — most searches don't need it.
    """
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None
    fetch_size = min(200, size * 4) if (year_from or year_to) else size
    raw = _get("oldbailey_assocrec", {"text": text, "size": fetch_size, "from": 0})
    result = _extract_hits(raw)
    results = []
    for hit in result["hits"]:
        src = hit.get("_source", {})
        idkey = src.get("idkey") or hit.get("_id", "")
        if not _date_in_range(idkey, year_from, year_to):
            continue
        results.append({
            "idkey": idkey,
            "year": _year_from_idkey(idkey),
            "title": src.get("title", ""),
            "snippet": (src.get("text", "") or "")[:400],
            "image_url": (src.get("images") or [None])[0],
        })
        if len(results) >= size:
            break
    return {"total": result["total"], "results": results}


@mcp.tool()
def get_record(idkey: str) -> dict:
    """Fetch the complete text of one specific Old Bailey record by its ID.

    ONLY call this when you have an idkey from search results AND the snippet
    was insufficient to answer the question. Do not call for multiple records.
    """
    raw = _get("oldbailey_record_single", {"idkey": idkey})
    hits = raw.get("hits", {}).get("hits", [])
    if not hits:
        return {"error": f"No record found for idkey={idkey!r}"}
    src = hits[0].get("_source", {})
    images = src.get("images", [])
    return {
        "idkey": idkey,
        "year": _year_from_idkey(idkey),
        "date": src.get("date"),
        "title": src.get("title"),
        "text": src.get("text", ""),
        "defendants": src.get("defendantNames", []),
        "offences": src.get("offenceCategories", []),
        "verdicts": src.get("verdictCategories", []),
        "punishments": src.get("punishmentCategories", []),
        "image_urls": images,
    }
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/test_tools.py -v
```

Expected: all 19 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_tools.py
git commit -m "feat: search_proceedings, search_ordinaries, search_associated, get_record tools"
```

---

## Task 10: Smoke Tests (Real API)

**Files:**
- Create: `tests/test_smoke.py`

These tests hit the real Old Bailey API. Run them manually with `--run-slow`. They verify
the Definition of Done from the spec.

- [ ] **Step 1: Create smoke tests**

Create `tests/test_smoke.py`:

```python
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


def pytest_addoption(parser):
    parser.addoption("--run-slow", action="store_true", default=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (real API calls)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-slow"):
        skip = pytest.mark.skip(reason="Pass --run-slow to run API tests")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)


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
                assert any("Dodd" in r.get("title", "") for r in result["records"])


@pytest.mark.slow
class TestCrossover:
    """Cross-name search."""

    def test_plus_operator_not_and(self):
        """Verify + query returns far fewer hits than unquoted AND."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persons.json"
            with patch.object(server, "KNOWLEDGE_FILE", path):
                result = server.search_proceedings(query='+"Gillan" +"inspector"')
                # AND was returning ~182,000 — correct + query should be <1000
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
```

- [ ] **Step 2: Run unit tests first (fast, no API)**

```bash
uv run pytest tests/ -v --ignore=tests/test_smoke.py
```

Expected: all tests PASS.

- [ ] **Step 3: Run smoke tests against real API**

```bash
uv run pytest tests/test_smoke.py -v -m slow --run-slow
```

Expected: all smoke tests PASS. Watch the log:

```bash
tail -f oldbailey_mcp.log
```

Verify log shows:
- `GET oldbailey_record` on first Gillan call
- No `GET` log entry on second Gillan call (knowledge hit)

- [ ] **Step 4: Final commit**

```bash
git add tests/test_smoke.py
git commit -m "test: smoke tests against real Old Bailey API"
```

- [ ] **Step 5: Verify .gitignore covers all sensitive files**

```bash
git status
```

Confirm `knowledge/`, `.env`, and `*.log` do not appear as untracked files.

---

## Definition of Done Checklist

- [ ] `uv run pytest tests/ -v --ignore=tests/test_smoke.py` — all pass, no warnings
- [ ] `uv run pytest tests/test_smoke.py -v -m slow --run-slow` — all pass
- [ ] Second search of known person: log shows no `GET` request
- [ ] `search_proceedings(query='+"Gillan" +"inspector"')` returns < 10,000 total (not 182,000)
- [ ] `knowledge/` and `.env` absent from `git status`
- [ ] `requirements.txt` contains only `fastmcp`, `httpx`, `python-dotenv`
- [ ] `uv run python server.py` starts without error (Ctrl-C to stop)
