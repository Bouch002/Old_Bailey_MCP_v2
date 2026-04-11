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
