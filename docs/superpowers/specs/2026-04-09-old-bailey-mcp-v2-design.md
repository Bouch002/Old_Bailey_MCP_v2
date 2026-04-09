# Old Bailey MCP v2 — Design Spec
**Date:** 2026-04-09
**Status:** Approved

---

## Overview

A rebuilt Old Bailey MCP server that acts as a **living research memory** — not just a live
search proxy. Every person found is written to a persistent knowledge file. Subsequent
searches read that file first; API calls only happen for genuinely new queries. The server
works standalone for any user, and gains GEDCOM-enriched auto-search when `GEDCOM_FILE` is
configured.

**Stack:** FastMCP · httpx · python-dotenv · single file (`server.py`)
**Transport:** stdio (Claude Desktop + Claude Code compatible)
**Dependencies:** `fastmcp`, `httpx`, `python-dotenv`

---

## Problems Solved vs v1

| v1 Problem | v2 Fix |
|---|---|
| 3–4 sequential tool calls per person | `find_person` does 1–3 in one tool, 0 if already known |
| `AND` operator broken in API | Use `+` operator (Lucene required terms) |
| No witness/victim search | `oldbailey_victim` endpoint confirmed; role routing per endpoint |
| `search_all` as default trap | Retired; replaced by `find_person` with hard STOP language |
| No caching | FastMCP `ResponseCachingMiddleware` + persistent knowledge file |
| Hand-rolled JSON-RPC | FastMCP decorator-based tools |
| Unknown structured endpoints | 5 additional endpoints confirmed and integrated |
| No image/source links | `images` field captured per record → direct scanned page URL |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Claude / GEDCOM MCP                │
└──────────────────┬──────────────────────────────┘
                   │ MCP (stdio)
┌──────────────────▼──────────────────────────────┐
│           Old Bailey MCP v2                     │
│                                                 │
│  Tools                   Resources              │
│  ──────                  ─────────              │
│  find_person        ──▶  oldbailey://known/     │
│  find_crossover     ──▶  oldbailey://known/{id} │
│  search_proceedings                             │
│  get_record                                     │
│         │                                       │
│         ▼                                       │
│  knowledge/persons.json  ◀── persistent memory  │
│         │                                       │
│         ▼                                       │
│  Old Bailey API (dhi.ac.uk)                     │
│  oldbailey_record        oldbailey_defendant    │
│  oldbailey_record_single oldbailey_victim       │
│  oldbailey_oa            oldbailey_offence      │
│  oldbailey_assocrec      oldbailey_verdict      │
│                          oldbailey_punishment   │
└─────────────────────────────────────────────────┘
         ▲
         │ optional: reads .ged file directly
  GEDCOM_FILE env var
```

---

## API — Confirmed Endpoints

Base URL: `https://www.dhi.ac.uk/api/data`

| Endpoint | Collection | Role |
|---|---|---|
| `oldbailey_record` | Full proceedings | Free-text + witness search |
| `oldbailey_record_single` | Single record by idkey | Full text fetch |
| `oldbailey_defendant` | Defendant subsections only | Structured defendant lookup |
| `oldbailey_victim` | Victim subsections only | Structured victim lookup |
| `oldbailey_offence` | Offence subsections | Offence data per case |
| `oldbailey_verdict` | Verdict subsections | Verdict data per case |
| `oldbailey_punishment` | Punishment subsections | Punishment data per case |
| `oldbailey_oa` | Ordinary's Accounts | Death-row chaplain interviews 1676–1772 |
| `oldbailey_assocrec` | Associated Records | Petitions, depositions, correspondence |

No `oldbailey_witness` endpoint exists — witness searches use `oldbailey_record` with role
keyword enrichment.

### Correct Query Syntax

| Need | Correct syntax | Wrong (v1 used) |
|---|---|---|
| Exact name phrase | `"John Gillan"` | `John Gillan` |
| Both terms required | `+"Gillan" +"inspector"` | `"Gillan" AND "inspector"` |
| Role + name proximity | `"Inspector Gillan"~5` | separate tool calls |
| Name variants | `Gillan*` | nothing |

`AND` between terms returns ~180,000 false positives. `+` operator is correct Lucene syntax
for required terms.

### Known Fields Per Record

- `idkey` — unique record ID (e.g. `t18990109-146`)
- `div1_idkey` — parent trial idkey (on structured sub-endpoints)
- `title` — human-readable title
- `text` — full transcript text
- `collection` — which collection (`defendant`, `victim`, etc.)
- `images` — array of scanned page URLs (direct links to original documents)

### idkey Format

```
t18990109-146     → trial, 9 Jan 1899, case 146
s18990109-1       → punishment summary
OA17210517        → Ordinary's Account, 17 May 1721
ar_24593_11886    → associated record
t17410514-47-defend361  → defendant subsection of trial t17410514-47
```

Year extraction: `re.search(r'(\d{4})', idkey)` — digits 1–4 of numeric portion.

---

## Tools

### 1. `find_person`

Primary genealogical lookup. Searches by name across all roles. GEDCOM-enriched when
`gedcom_id` supplied and `GEDCOM_FILE` is configured. Returns compact index for large
result sets; writes unseen cases to knowledge file as `pending_review`.

```
Parameters:
  name        (str, required)     — full name or surname
  gedcom_id   (str, optional)     — e.g. "@I42@" — auto-enriches from GEDCOM file
  role        (str, optional)     — "defendant" | "victim" | "officer" | "any" (default: "any")
  date_from   (str, optional)     — earliest year e.g. "1850"
  date_to     (str, optional)     — latest year e.g. "1900"
  offence     (str, optional)     — offence category filter
  size        (int, default=8)    — results to return before switching to index mode
```

**Role → endpoint routing:**

| role | Endpoint(s) used |
|---|---|
| `"defendant"` | `oldbailey_defendant` |
| `"victim"` | `oldbailey_victim` |
| `"officer"` | `oldbailey_record` + `+"name" +("inspector"\|"constable"\|"sergeant"\|"detective")` |
| `"any"` | `oldbailey_record` quoted phrase (1 call); if 0 results, falls back to `oldbailey_defendant` then `oldbailey_victim` (up to 3 calls total) |

**Index mode:** when total results > 8, returns compact list (idkey, year, title, 150-char
snippet, scan image URL) plus count of cases written to knowledge file as `pending_review`.
User picks specific cases; Weaver can surface pending ones as research leads.

**Knowledge-first logic:**
1. Check `knowledge/persons.json` — if person known and date range covered → return file, no API call
2. If not known → search API → merge results into knowledge file → return results

**Returns per record:**
```json
{
  "idkey": "t18990109-146",
  "year": 1899,
  "title": "Trial of Henry Walsh",
  "role_found": "witness",
  "snippet": "...badge 707 V Inspector Gillan stated...",
  "offences": ["theft"],
  "verdicts": ["guilty"],
  "punishments": ["imprisonment"],
  "image_url": "https://www.dhi.ac.uk/san/ob/1890s/189901090055.gif",
  "div1_idkey": "t18990109-146"
}
```

**Description (tool-facing):**
> Use this for any person name lookup. Checks known research memory first — zero API calls
> if already found. Returns an index when results are large so you can pick what matters.
> STOP after reviewing results — only call get_record if a specific trial needs its full
> text and the snippet was insufficient. Do NOT also call search_proceedings for names.

---

### 2. `find_crossover`

Searches for cases where two or more names appear together. Knowledge-first: if all names
are already in `persons.json`, intersects their idkey sets with zero API calls.

```
Parameters:
  names       (list[str], required) — 2–5 names or GEDCOM IDs
  date_from   (str, optional)
  date_to     (str, optional)
```

**Logic:**
- If all names known in knowledge file → intersect idkey sets → return shared cases instantly
- If any name unknown → build `+"name1" +"name2"` query → `oldbailey_record` → one API call
- Results written to knowledge file under each name

**Returns:** cases where all supplied names appear, with year, title, snippet, image URL.

**Use cases:**
- "Did Gillan and Cotton appear in the same trial?"
- GEDCOM sweep — pass multiple family members, find shared cases
- Conspiracy thread evidence — names that keep appearing together

---

### 3. `search_proceedings`

Free-text / Lucene search of the main Proceedings corpus. For topic, place, offence, and
conspiracy research — not name lookup.

```
Parameters:
  query       (str, required)   — Lucene query; use + for required terms
  date_from   (str, optional)
  date_to     (str, optional)
  size        (int, default=8)
  from_       (int, default=0)
```

**Description (tool-facing):**
> For topic searches, place names, offence types, or Lucene boolean queries.
> Use + for required terms: +"Gillan" +"larceny" NOT AND.
> Do NOT use for simple name lookups — use find_person instead.

---

### 4. `search_ordinaries`

Ordinary's Accounts — Newgate chaplain death-row interviews (1676–1772). Rich biographical
detail: origins, trade, religion, last confession. Only relevant for cases pre-1773 with a
death sentence.

```
Parameters:
  text        (str, required)
  date_from   (str, optional)
  date_to     (str, optional)
  size        (int, default=8)
```

---

### 5. `search_associated`

Associated Records — petitions, depositions, correspondence linked to specific trials. Use
when a trial result suggests follow-up documents exist (e.g. death sentence → mercy petition).

```
Parameters:
  text        (str, required)
  date_from   (str, optional)
  date_to     (str, optional)
  size        (int, default=8)
```

---

### 6. `get_record`

Fetches full transcript of one specific record by idkey.

```
Parameters:
  idkey  (str, required)
```

**Description (tool-facing):**
> Only call this when you have a specific idkey from search results AND the snippet was
> insufficient. Do not call for multiple records — read snippets first.

---

## Resources

Read-only. No API calls. Served from `knowledge/persons.json`.

| URI | Returns |
|---|---|
| `oldbailey://known/` | Index of all known persons — name, GEDCOM ID, case count, date range covered |
| `oldbailey://known/{name_or_gedcom_id}` | Full case history for one person — all records, image URLs, pending cases |

Weaver reads these directly to cross-reference persons across cases without tool calls.

---

## Knowledge File

**Path:** `knowledge/persons.json` (gitignored — contains personal research data)

**Structure:**
```json
{
  "@I42@": {
    "name": "John Gillan",
    "gedcom_id": "@I42@",
    "last_searched": "2026-04-09",
    "date_ranges_covered": [["1890", "1913"]],
    "total_api_hits": 39,
    "records": [
      {
        "idkey": "t18990109-146",
        "year": 1899,
        "title": "Trial of Henry Walsh",
        "role_found": "witness",
        "snippet": "...badge 707 V Inspector Gillan stated...",
        "offences": ["theft"],
        "verdicts": ["guilty"],
        "punishments": ["imprisonment"],
        "image_url": "https://www.dhi.ac.uk/san/ob/1890s/189901090055.gif",
        "status": "reviewed"
      }
    ],
    "pending_review": [
      {
        "idkey": "t19020310-88",
        "year": 1902,
        "title": "Trial of James Price",
        "snippet": "...Inspector Gillan gave evidence...",
        "image_url": "https://www.dhi.ac.uk/san/ob/1900s/190203100041.gif",
        "status": "pending_review"
      }
    ]
  }
}
```

- Keyed by GEDCOM ID when available, name otherwise
- `date_ranges_covered` tracks what has been searched — avoids re-querying covered ranges
- `pending_review` cases logged for later without flooding context
- `image_url` per record — direct link to scanned original page

---

## GEDCOM Integration

Optional. Activated by `GEDCOM_FILE` env var pointing to a `.ged` file.

**How it works:**
1. `find_person("John Gillan", gedcom_id="@I42@")` called
2. Server reads `.ged` file directly (lightweight parser, no GEDCOM MCP dependency)
3. Extracts: birth year, death year, occupation from `@I42@` record
4. Auto-sets `date_from`, `date_to`; derives role hint from occupation

**Occupation → role hint mapping:**

| Occupation contains | Role hint |
|---|---|
| police / constable / inspector / detective / sergeant | `"officer"` |
| thief / criminal / burglar | `"defendant"` |
| anything else | `"any"` |

**If GEDCOM person has no birth/death dates:** date filter is skipped; search runs without
date range. If no occupation recorded: role defaults to `"any"`.

**Without GEDCOM_FILE:** all parameters manual, full standalone use.
**Generic:** any user's `.ged` file works — not hardwired to any family.

---

## Environment & Security

```env
# .env — never commit
GEDCOM_FILE=C:/Users/yourname/Projects/your_family.ged
```

**`.gitignore` must include:**
```gitignore
.env
knowledge/
*.log
*.log.*
```

No API keys required — Old Bailey API is public. Only sensitive material is the user's
genealogy data (`GEDCOM_FILE`) and accumulated research (`knowledge/persons.json`).

`python-dotenv` loads `.env` automatically on startup — no shell env var setup needed
for Claude Desktop users.

---

## Middleware (FastMCP)

| Middleware | Config |
|---|---|
| `ResponseCachingMiddleware` | TTL 300s — session-level deduplication |
| `ResponseLimitingMiddleware` | 50KB cap — prevents context floods |
| `LoggingMiddleware` | stderr WARNING+, rotating file DEBUG+ |

---

## File Structure

```
Old_Bailey_MCP_v2/
├── server.py              ← entire implementation
├── .env                   ← local config (gitignored)
├── .gitignore
├── requirements.txt       ← fastmcp, httpx, python-dotenv
├── knowledge/             ← gitignored
│   └── persons.json       ← persistent research memory
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-09-old-bailey-mcp-v2-design.md
```

---

## Definition of Done

- [ ] `find_person("Gillan", gedcom_id="@I42@")` finds John Gillan as a witness in 1890–1913 in one tool call
- [ ] `find_crossover(["Gillan", "Walsh"])` returns `t18990109-146` from knowledge file (zero API calls second time)
- [ ] `search_proceedings` uses `+` operator correctly — `+"Gillan" +"inspector"` returns <100 results not 180,000
- [ ] `get_record("t18990109-146")` returns full text + image URLs
- [ ] Large result set (>8) returns index mode with pending cases written to knowledge file
- [ ] `oldbailey://known/@I42@` resource returns full case history without tool call
- [ ] Second search of known person returns from knowledge file — no API call made (check log)
- [ ] `knowledge/` and `.env` are gitignored
- [ ] GEDCOM_FILE not set → server starts and works without error
- [ ] `requirements.txt` contains only `fastmcp`, `httpx`, `python-dotenv`
