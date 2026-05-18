#!/usr/bin/env python3
"""Old Bailey Online MCP Server v2 — living research memory."""

import json
import logging
import logging.handlers
import re
import sqlite3
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
DB_FILE = Path(__file__).parent / "oldbailey_cache.db"
INDEX_THRESHOLD = 8

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
    m = re.match(r"^([a-z]|OA)(\d{4})", idkey)
    return int(m.group(2)) if m else None


def _date_in_range(idkey: str, year_from: Optional[int], year_to: Optional[int]) -> bool:
    if year_from is None and year_to is None:
        return True
    year = _year_from_idkey(idkey)
    if year is None:
        return True
    if year_from is not None and year < year_from:
        return False
    if year_to is not None and year > year_to:
        return False
    return True


# ── Database ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_FILE))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _init_db() -> None:
    db = _conn()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS persons (
            key           TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            gedcom_id     TEXT,
            last_searched TEXT
        );

        CREATE TABLE IF NOT EXISTS date_ranges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            person_key  TEXT NOT NULL REFERENCES persons(key),
            date_from   TEXT,
            date_to     TEXT
        );

        CREATE TABLE IF NOT EXISTS records (
            idkey       TEXT PRIMARY KEY,
            person_key  TEXT NOT NULL REFERENCES persons(key),
            year        INTEGER,
            title       TEXT,
            snippet     TEXT,
            collection  TEXT,
            offences    TEXT,
            verdicts    TEXT,
            punishments TEXT,
            image_url   TEXT,
            div1_idkey  TEXT,
            status      TEXT DEFAULT 'reviewed'
        );

        CREATE INDEX IF NOT EXISTS idx_records_person ON records(person_key);
        CREATE INDEX IF NOT EXISTS idx_persons_gedcom ON persons(gedcom_id);
    """)
    db.commit()
    db.close()


def _is_range_covered(
    db: sqlite3.Connection,
    person_key: str,
    date_from: Optional[str],
    date_to: Optional[str],
) -> bool:
    if not date_from and not date_to:
        row = db.execute(
            "SELECT COUNT(*) FROM records WHERE person_key = ?", (person_key,)
        ).fetchone()
        return row[0] > 0
    req_from = int(date_from) if date_from else 0
    req_to = int(date_to) if date_to else 9999
    for r in db.execute(
        "SELECT date_from, date_to FROM date_ranges WHERE person_key = ?", (person_key,)
    ):
        stored_from = int(r["date_from"]) if r["date_from"] else 0
        stored_to = int(r["date_to"]) if r["date_to"] else 9999
        if stored_from <= req_from and stored_to >= req_to:
            return True
    return False


def _merge_results(
    db: sqlite3.Connection,
    person_key: str,
    name: str,
    gedcom_id: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    records: list,
    pending: list,
) -> None:
    db.execute(
        """
        INSERT INTO persons (key, name, gedcom_id, last_searched)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            last_searched = excluded.last_searched,
            gedcom_id = COALESCE(persons.gedcom_id, excluded.gedcom_id)
        """,
        (person_key, name, gedcom_id, date.today().isoformat()),
    )
    if date_from or date_to:
        existing = {
            (r["date_from"], r["date_to"])
            for r in db.execute(
                "SELECT date_from, date_to FROM date_ranges WHERE person_key = ?",
                (person_key,),
            )
        }
        if (date_from, date_to) not in existing:
            db.execute(
                "INSERT INTO date_ranges (person_key, date_from, date_to) VALUES (?, ?, ?)",
                (person_key, date_from, date_to),
            )
    for rec in records + pending:
        db.execute(
            """
            INSERT OR IGNORE INTO records
              (idkey, person_key, year, title, snippet, collection,
               offences, verdicts, punishments, image_url, div1_idkey, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec["idkey"],
                person_key,
                rec.get("year"),
                rec.get("title"),
                rec.get("snippet"),
                rec.get("collection"),
                json.dumps(rec.get("offences", [])),
                json.dumps(rec.get("verdicts", [])),
                json.dumps(rec.get("punishments", [])),
                rec.get("image_url"),
                rec.get("div1_idkey"),
                rec.get("status", "reviewed"),
            ),
        )


def _rows_to_records(rows) -> list:
    return [
        {
            "idkey": r["idkey"],
            "year": r["year"],
            "title": r["title"],
            "snippet": r["snippet"],
            "collection": r["collection"],
            "offences": json.loads(r["offences"] or "[]"),
            "verdicts": json.loads(r["verdicts"] or "[]"),
            "punishments": json.loads(r["punishments"] or "[]"),
            "image_url": r["image_url"],
            "div1_idkey": r["div1_idkey"],
            "status": r["status"],
        }
        for r in rows
    ]


# ── Query builder ────────────────────────────────────────────────────────────

_OFFICER_QUERY = '+(inspector constable sergeant detective "police officer" "P.C." "D.C.")'


def _build_query(name: str, role: str) -> str:
    quoted = f'"{name.strip()}"'
    if role == "officer":
        return f"+{quoted} {_OFFICER_QUERY}"
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


# ── FastMCP server ───────────────────────────────────────────────────────────

mcp = FastMCP(
    "Old Bailey Online",
    instructions=(
        "Historical criminal court records for London, 1674–1913. "
        "Always call oldbailey_search_history first — it checks the local cache "
        "and costs zero API calls. "
        "Use find_person for name lookups. "
        "Use search_proceedings for topic/Lucene queries only. "
        "When using Lucene, use + for required terms — NOT AND."
    ),
)

mcp.add_middleware(
    ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=300))
)
mcp.add_middleware(ResponseLimitingMiddleware(max_size=50_000))
mcp.add_middleware(LoggingMiddleware())

# ── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("oldbailey://known/")
def list_known() -> str:
    """Index of all persons in the local cache. Read before searching."""
    db = _conn()
    persons = db.execute(
        "SELECT key, name, gedcom_id, last_searched FROM persons ORDER BY last_searched DESC"
    ).fetchall()
    if not persons:
        db.close()
        return "No persons in cache yet."
    lines = ["Known persons:\n"]
    for p in persons:
        reviewed = db.execute(
            "SELECT COUNT(*) FROM records WHERE person_key = ? AND status = 'reviewed'",
            (p["key"],),
        ).fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM records WHERE person_key = ? AND status = 'pending_review'",
            (p["key"],),
        ).fetchone()[0]
        ranges = db.execute(
            "SELECT date_from, date_to FROM date_ranges WHERE person_key = ?",
            (p["key"],),
        ).fetchall()
        range_str = (
            ", ".join(f"{r['date_from']}–{r['date_to']}" for r in ranges)
            or "no date filter"
        )
        lines.append(
            f"- {p['name']} ({p['key']}): {reviewed} reviewed, {pending} pending | searched: {range_str}"
        )
    db.close()
    return "\n".join(lines)


@mcp.resource("oldbailey://known/{identifier}")
def get_known(identifier: str) -> str:
    """Full case history for one person from the cache.
    Pass a GEDCOM ID (e.g. @I42@) or a name. No API calls made."""
    db = _conn()
    person = db.execute(
        "SELECT * FROM persons WHERE key = ? OR gedcom_id = ?",
        (identifier, identifier),
    ).fetchone()
    if not person:
        db.close()
        return f"No record found for '{identifier}'."
    rows = db.execute(
        "SELECT * FROM records WHERE person_key = ? ORDER BY year", (person["key"],)
    ).fetchall()
    ranges = db.execute(
        "SELECT date_from, date_to FROM date_ranges WHERE person_key = ?",
        (person["key"],),
    ).fetchall()
    db.close()
    all_records = _rows_to_records(rows)
    return json.dumps(
        {
            "name": person["name"],
            "gedcom_id": person["gedcom_id"],
            "last_searched": person["last_searched"],
            "date_ranges_covered": [[r["date_from"], r["date_to"]] for r in ranges],
            "records": [r for r in all_records if r["status"] == "reviewed"],
            "pending_review": [r for r in all_records if r["status"] == "pending_review"],
        },
        indent=2,
        ensure_ascii=False,
    )


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def oldbailey_search_history(
    gedcom_id: Optional[str] = None,
    keyword: Optional[str] = None,
) -> dict:
    """Query the local Old Bailey cache. Always call this first — zero API calls.

    Filter by gedcom_id to see everything found for a specific ancestor.
    Filter by keyword to search cached titles and snippets.
    Returns all cached persons and their records when no filters are given.
    """
    db = _conn()
    if gedcom_id:
        persons = db.execute(
            "SELECT * FROM persons WHERE gedcom_id = ?", (gedcom_id,)
        ).fetchall()
    else:
        persons = db.execute(
            "SELECT * FROM persons ORDER BY last_searched DESC"
        ).fetchall()

    results = []
    for p in persons:
        if keyword:
            rows = db.execute(
                """SELECT * FROM records WHERE person_key = ?
                   AND (title LIKE ? OR snippet LIKE ?)
                   ORDER BY year""",
                (p["key"], f"%{keyword}%", f"%{keyword}%"),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM records WHERE person_key = ? ORDER BY year", (p["key"],)
            ).fetchall()
        results.append(
            {
                "name": p["name"],
                "gedcom_id": p["gedcom_id"],
                "last_searched": p["last_searched"],
                "record_count": len(rows),
                "records": _rows_to_records(rows),
            }
        )
    db.close()
    return {
        "from_cache": True,
        "total_persons": len(results),
        "persons": results,
    }


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

    Checks the local cache first — zero API calls if already found.
    Returns an index list when results exceed the size threshold; additional
    cases are written to the cache as pending_review for later review.

    STOP after reviewing the returned records. Only call get_record if a
    specific trial needs full text and the snippet is insufficient.
    Do NOT also call search_proceedings for name lookups.

    role options: 'any' (default) | 'defendant' | 'victim' | 'officer'
    """
    person_key = gedcom_id or name

    db = _conn()
    person_exists = db.execute(
        "SELECT key FROM persons WHERE key = ?", (person_key,)
    ).fetchone()
    if person_exists and _is_range_covered(db, person_key, date_from, date_to):
        records = _rows_to_records(
            db.execute(
                "SELECT * FROM records WHERE person_key = ? AND status = 'reviewed' ORDER BY year",
                (person_key,),
            ).fetchall()
        )
        pending_count = db.execute(
            "SELECT COUNT(*) FROM records WHERE person_key = ? AND status = 'pending_review'",
            (person_key,),
        ).fetchone()[0]
        db.close()
        return {
            "source": "cache",
            "name": name,
            "total_found": len(records) + pending_count,
            "records": records,
            "pending_count": pending_count,
        }
    db.close()

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

    if role == "any" and not all_hits:
        for fb_ep in ("oldbailey_defendant", "oldbailey_victim"):
            fb = _extract_hits(_get(fb_ep, {"text": f'"{name.strip()}"', "size": fetch_size}))
            all_hits.extend(fb["hits"])
            if all_hits:
                break

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

    db = _conn()
    _merge_results(db, person_key, name, gedcom_id, date_from, date_to, reviewed, pending)
    db.commit()
    db.close()

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
            f"{len(pending)} additional cases written to cache as pending_review. "
            "Read oldbailey://known/ or call oldbailey_search_history to review them."
        )
    return response


@mcp.tool()
def find_crossover(
    names: list,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Find cases where two or more people appear together.

    Checks cache first — if all names are known, returns shared cases
    instantly with zero API calls. Pass 2–5 names or GEDCOM IDs.

    Use this to find conspiracy thread evidence: names that appear in the same trial.
    """
    if len(names) < 2:
        return {"error": "Provide at least 2 names."}
    if len(names) > 5:
        return {"error": "Maximum 5 names per crossover search."}

    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None

    db = _conn()
    all_known = all(
        db.execute("SELECT key FROM persons WHERE key = ?", (n,)).fetchone()
        for n in names
    )
    if all_known:
        id_sets = [
            {r["idkey"] for r in db.execute(
                "SELECT idkey FROM records WHERE person_key = ?", (n,)
            )}
            for n in names
        ]
        shared_keys = set.intersection(*id_sets)
        first_rows = {
            r["idkey"]: r
            for r in db.execute(
                "SELECT * FROM records WHERE person_key = ?", (names[0],)
            )
        }
        shared = sorted(
            [
                _rows_to_records([first_rows[k]])[0]
                for k in shared_keys
                if k in first_rows and _date_in_range(k, year_from, year_to)
            ],
            key=lambda r: r.get("year") or 0,
        )
        db.close()
        return {"source": "cache", "names": names, "shared_cases": shared}
    db.close()

    # API fallback
    query = " ".join(f'+"{n.strip()}"' for n in names)
    raw = _get("oldbailey_record", {"text": query, "size": 50, "from": 0})
    result = _extract_hits(raw)
    records = [
        _format_record(hit)
        for hit in result["hits"]
        if _date_in_range(
            hit.get("_source", {}).get("idkey") or hit.get("_id", ""),
            year_from, year_to,
        )
    ]

    db = _conn()
    for n in names:
        _merge_results(db, n, n, None, date_from, date_to, records, [])
    db.commit()
    db.close()

    return {"source": "api", "names": names, "shared_cases": records, "total_found": result["total"]}


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
    Use + for required terms — NOT AND: +\"forgery\" +\"Bank of England\"
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
    from_: int = 0,
) -> dict:
    """Search Ordinary's Accounts — Newgate chaplain death-row interviews (1676–1772).

    Rich biographical detail: origins, trade, religion, last confession.
    Only relevant for pre-1773 cases with a death sentence.
    Do NOT call this routinely after every defendant search.
    """
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None
    fetch_size = min(200, size * 4) if (year_from or year_to) else size
    raw = _get("oldbailey_oa", {"text": text, "size": fetch_size, "from": from_})
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
    from_: int = 0,
) -> dict:
    """Search Associated Records — petitions, depositions, correspondence.

    Use when a trial result suggests follow-up documents exist, e.g. a death sentence
    that may have a mercy petition, or when researching evidence chains.
    Do NOT call this routinely — most searches don't need it.
    """
    year_from = int(date_from) if date_from else None
    year_to = int(date_to) if date_to else None
    fetch_size = min(200, size * 4) if (year_from or year_to) else size
    raw = _get("oldbailey_assocrec", {"text": text, "size": fetch_size, "from": from_})
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


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _init_db()
    log.info("SERVER START oldbailey-mcp-v2  db=%s  logfile=%s", DB_FILE, _LOG_FILE)
    mcp.run()
