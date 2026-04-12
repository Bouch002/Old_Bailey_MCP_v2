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
    # Match formats like t18990109-146, OA17210517, s17410514-1, f18990109-1
    # but not ar_24593_11886 (associated records)
    m = re.match(r"^([a-z]|OA)(\d{4})", idkey)
    return int(m.group(2)) if m else None


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
    req_to = int(date_to) if date_to else 9999  # corpus ends 1913; 9999 is safely beyond
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
    if gedcom_id and not person.get("gedcom_id"):
        person["gedcom_id"] = gedcom_id
    person["last_searched"] = date.today().isoformat()
    if date_from or date_to:
        entry = [date_from, date_to]
        if entry not in person["date_ranges_covered"]:
            person["date_ranges_covered"].append(entry)
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


# ── GEDCOM parser ────────────────────────────────────────────────────────────

_OFFICER_TERMS = {"police", "constable", "inspector", "detective", "sergeant", "officer"}


def _parse_gedcom(gedcom_id: str) -> dict:
    """Extract birth year, death year, occupation for a GEDCOM individual."""
    if not GEDCOM_FILE or not Path(GEDCOM_FILE).exists():
        return {}
    result: dict = {"birth_year": None, "death_year": None, "occupation": None}
    found = False
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
                        if in_target:
                            found = True
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
    return result if found else {}


def _occupation_to_role(occupation: Optional[str]) -> str:
    if not occupation:
        return "any"
    occ = occupation.lower()
    if any(t in occ for t in _OFFICER_TERMS):
        return "officer"
    return "any"


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
    if gedcom_id:
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
        records = person.get("records", [])
        pending = person.get("pending_review", [])
        return {
            "source": "knowledge",
            "name": person.get("name", name),
            "total_found": len(records) + len(pending),
            "records": records,
            "pending_count": len(pending),
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

    for n in names:
        _merge_results(knowledge, n, n, None, date_from, date_to, records, [])
    _save_knowledge(knowledge)

    return {
        "source": "api",
        "names": names,
        "shared_cases": records,
        "total_found": result["total"],
    }


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
    log.info("SERVER START oldbailey-mcp-v2  logfile=%s", _LOG_FILE)
    mcp.run()
