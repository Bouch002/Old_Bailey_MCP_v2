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


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("SERVER START oldbailey-mcp-v2  logfile=%s", _LOG_FILE)
    mcp.run()
