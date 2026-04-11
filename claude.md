# Old Bailey MCP Server — v2 Rebuild Plan

## What this document is

This is the build brief for **v2 of the Old Bailey MCP server**. It replaces the hand-rolled
JSON-RPC implementation (`server.py` v1) with a proper implementation using the official
**MCP Python SDK**. Read this before writing any code.

---

## Context — what already exists

A working v1 server lives at `../Old_Bailey_MCP/server.py`. It wraps the Old Bailey Online
API (240 years of London criminal court records, 1674–1913) and exposes it as six MCP tools.
It works, but has structural problems that cause real failures in use.

### Known problems with v1

| Problem | Impact |
|---|---|
| Date filtering is client-side only | Fetches up to 200 records and filters locally. If the target record isn't in the first 200, it's silently missed. |
| No witness/officer search | `search_defendant` only finds defendants. Police witnesses, victims, arresting officers are only findable via free-text `search_records`, which has no name-quoting — "John Gillan" matches anything containing "John" or "Gillan". |
| `search_all` is a default trap | Runs 3 sequential HTTP calls. Claude and the GEDCOM MCP both over-use it, causing slow, repetitive searches. |
| Schema type bugs | `size` was typed `"number"` in some tools but code expected integers — caused MCP validation errors. |
| No caching | Identical queries are re-fetched on every call. GEDCOM MCP searches the same names repeatedly across a session. |
| Hand-rolled JSON-RPC | Manual protocol loop — fragile, verbose, no type safety, easy to introduce bugs. |

---

## What we are building

A **rebuilt single-file MCP server** using the official `mcp` Python SDK (stdio transport),
targeting Claude Desktop and Claude Code. Same deployment model as v1 — no web server, no
database, just a Python script that speaks MCP over stdin/stdout.

### Technology

- **Runtime:** Python 3.10+
- **MCP SDK:** `fastmcp` (FastMCP — `pip install fastmcp`)
- **HTTP:** `httpx` for async-capable, clean HTTP calls (replaces `urllib`)
- **No other dependencies** — must stay lightweight and easy to deploy

### Transport

stdio (same as v1). Claude Desktop config is unchanged:

```json
{
  "mcpServers": {
    "oldbailey": {
      "command": "python",
      "args": ["path/to/server.py"]
    }
  }
}
```

---

## The Old Bailey API

Base URL: `https://www.dhi.ac.uk/api/data`

### Known endpoints

| Endpoint | Collection | Notes |
|---|---|---|
| `oldbailey_record` | Proceedings (trials, verdicts, punishments) | Main corpus 1674–1913 |
| `oldbailey_record_single` | Single record fetch by `idkey` | e.g. `t18990109-146` |
| `oldbailey_oa` | Ordinary's Accounts (chaplain interviews) | Death-row biographies 1676–1772 |
| `oldbailey_assocrec` | Associated Records | Petitions, depositions, correspondence |

### Query parameters (known)

- `text` — free-text / Lucene query string. Supports quoted phrases: `"John Gillan"`
- `size` — number of results (integer)
- `from` — pagination offset (integer)
- `offcat` — offence category filter (structured): `theft`, `kill`, `sexual`, `deception`, `breaking peace`, `damage`, `royal offences`, `miscellaneous`

### API limitations to work around

- **No server-side date range filter** — must be applied client-side from `idkey`
- **No role filter** (defendant vs witness vs victim) — all roles live in the same full text
- **No server-side boolean AND** that works reliably — exact phrase queries (`"name"`) work; `AND` between terms is unreliable across the corpus
- **Witness names are in full text, not structured fields** — `defendantNames` is a structured field; witness/officer names are only in the raw `text` body

### idkey format

```
t18990109-146   → trial, 9 Jan 1899, case 146
f18990109-1     → front matter
s18990109-1     → punishment summary
OA17210517      → Ordinary's Account, 17 May 1721
ar_24593_11886  → associated record
```

Year is always digits 1–4 of the numeric portion — extractable with `r'(\d{4})'`.

---

## Tools to build (v2)

### 1. `search_person`  *(new — replaces search_defendant)*

The primary tool for genealogical name lookup. Searches for a person by quoted name across
**all roles** — defendant, witness, victim, arresting officer, character witness, juror.

**Why:** The core genealogical use case is "find this ancestor in Old Bailey records" regardless
of why they appear. v1 forced callers to know in advance whether the person was a defendant.

```
Parameters:
  name        (str, required)  — full name or surname, quoted for exact match
  role        (str, optional)  — hint: "defendant" | "witness" | "victim" | "any" (default: "any")
  date_from   (str, optional)  — earliest year e.g. "1850"
  date_to     (str, optional)  — latest year e.g. "1900"
  offence     (str, optional)  — offence category (only meaningful when role=defendant)
  size        (int, default=10)
  from_       (int, default=0)
```

When `role="defendant"`, use the structured `defendantNames` field behaviour (existing v1
approach). When `role="witness"` or `"any"`, search full text with quoted phrase.

### 2. `get_record`  *(keep, minor cleanup)*

Fetch the complete text of a single record by idkey. No changes to logic — just cleaner
implementation via the SDK.

```
Parameters:
  idkey  (str, required)
```

### 3. `search_proceedings`  *(replaces search_records)*

Free-text / Lucene search of the Proceedings. Renamed to be unambiguous.
Quoted phrase support documented clearly. Date filter applied client-side.

```
Parameters:
  text        (str, required)
  date_from   (str, optional)
  date_to     (str, optional)
  size        (int, default=10)
  from_       (int, default=0)
```

### 4. `search_ordinaries`  *(keep, minor cleanup)*

Ordinary's Accounts — Newgate chaplain death-row interviews (1676–1772).
No logic changes, just SDK migration.

```
Parameters:
  text        (str, required)
  date_from   (str, optional)
  date_to     (str, optional)
  size        (int, default=10)
  from_       (int, default=0)
```

### 5. `search_associated`  *(keep, minor cleanup)*

Associated Records — petitions, depositions, correspondence.
No logic changes, just SDK migration.

```
Parameters:
  text        (str, required)
  date_from   (str, optional)
  date_to     (str, optional)
  size        (int, default=10)
  from_       (int, default=0)
```

### 6. `batch_search`  *(new — for GEDCOM integration)*

Accepts a list of names and returns a results map. Designed specifically for the GEDCOM MCP
which iterates over family members and currently makes one round-trip per person, with no
deduplication across calls.

```
Parameters:
  names       (list[str], required) — up to 10 names
  date_from   (str, optional)
  date_to     (str, optional)
  size_each   (int, default=3)      — results per name (keep small)
```

Returns: `{ "John Smith": [...results], "Mary Jones": [...results], ... }`

Internally: runs searches sequentially (API has no batch endpoint). Uses the session cache
to skip names already searched in this session.

---

## Caching

Add a simple **in-memory TTL cache** (5 minute expiry) keyed on `(endpoint, params_hash)`.

- Eliminates repeat fetches from GEDCOM iteration
- No persistence needed — a session cache is enough
- Implementation: a plain dict `{key: (timestamp, data)}`

---

## Logging

Same two-handler setup as v1 (file + stderr). Keep it. It works.

```
oldbailey_mcp.log  — rotating, DEBUG+, 1MB × 3 backups
stderr             — WARNING+ only (surfaces in Claude Desktop MCP panel)
```

---

## File structure

```
oldbailey_mcp_v2/
├── server.py          ← entire implementation (single file)
├── requirements.txt   ← fastmcp, httpx
└── CLAUDE.md          ← this file (rename before use)
```

Do not add tests, docs, or additional modules unless explicitly asked.

---

## Implementation order

1. **Scaffold** — MCP SDK server, `initialize`, `tools/list`, logging
2. **HTTP layer** — `_get()` with httpx, `_extract_hits()`, `_year_from_idkey()`, TTL cache
3. **Core tools** — `search_person`, `get_record`, `search_proceedings`
4. **Secondary tools** — `search_ordinaries`, `search_associated`
5. **Batch tool** — `batch_search`
6. **Tool descriptions** — write these last, after logic is confirmed working. They must steer
   Claude away from `batch_search` for single lookups and away from `search_proceedings` for
   simple name searches.
7. **Smoke test** — John Gillan (witness, 1890–1913 range), William Dodd (defendant, hanged 1777)

---

## GEDCOM integration context

This server is typically called by a separate **GEDCOM MCP** (also Python, stdio) that parses
a `.ged` file and iterates over individuals, searching each one against Old Bailey. The GEDCOM
MCP is the *client*; this server is the *provider*.

Problems the v2 build should solve for that integration:

- **Repetition:** same name searched multiple times in one session → fixed by cache
- **Wrong tool choice:** GEDCOM uses `search_all` by default → retire `search_all`, replace
  with `search_person` which has a clearer contract
- **Witness blindness:** GEDCOM only found defendants → `search_person` with `role="any"` fixes this
- **Result noise:** GEDCOM gets back irrelevant matches when names are common → encourage use
  of `date_from`/`date_to` derived from GEDCOM birth/death dates

---

## What NOT to do

- Do not use `asyncio` — the MCP SDK stdio transport is synchronous; async adds complexity for no gain here
- Do not add a web server, REST API, or database
- Do not add a CLI argument parser — configuration is through the MCP protocol only
- Do not add type stubs, docstrings, or comments beyond what is necessary to understand non-obvious logic
- Do not split into multiple files
- Do not add `search_all` back — it was a mistake. Use `search_person` for names, `search_proceedings` for topics.

---

## Definition of done

- [ ] All six tools listed above are implemented and callable from Claude Desktop
- [ ] `search_person` finds John Gillan (badge 707 V) as a witness in the 1890–1913 range
- [ ] `search_person` finds William Dodd as a defendant (hanged 1777)
- [ ] `batch_search` accepts 3 names and returns a keyed result dict
- [ ] TTL cache prevents duplicate fetches within a session
- [ ] `requirements.txt` contains only `fastmcp` and `httpx`
- [ ] `oldbailey_mcp.log` is in `.gitignore`

