# Old Bailey MCP Server v2

An MCP server that gives Claude access to the **Old Bailey Online** — 240 years of London
criminal court records (1674–1913). Designed for genealogical research: find ancestors as
defendants, victims, witnesses, police officers, or jurors across the full corpus.

This is not just an API proxy. Every search result is saved to a local knowledge file.
The second time you search for the same person, no API call is made at all.

---

## What is the Old Bailey Online?

The [Old Bailey Online](https://www.oldbaileyonline.org/) is a fully searchable edition of
the proceedings of London's central criminal court, covering 197,745 criminal trials from
1674 to 1913. It includes trial transcripts, verdicts, punishments, and scanned images of
the original documents.

---

## Requirements

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Claude Desktop or Claude Code

---

## Installation

```bash
git clone https://github.com/your-repo/Old_Bailey_MCP_v2.git
cd Old_Bailey_MCP_v2
uv sync
```

Or with pip:

```bash
pip install -r requirements.txt
```

Verify it works:

```bash
uv run python -c "import fastmcp, httpx, dotenv; print('OK')"
```

---

## Claude Desktop configuration

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "oldbailey": {
      "command": "uv",
      "args": ["run", "python", "C:/path/to/Old_Bailey_MCP_v2/server.py"]
    }
  }
}
```

Replace `C:/path/to/Old_Bailey_MCP_v2/server.py` with the actual path on your machine.

The config file is usually at:
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Restart Claude Desktop after saving.

---

## GEDCOM integration (optional)

If you have a GEDCOM family tree file (`.ged`), the server can automatically fill in date
ranges and role hints when searching for family members.

Create a `.env` file in the project directory:

```env
GEDCOM_FILE=C:/path/to/your/family.ged
```

Once set, pass a GEDCOM ID when searching:

```
find_person(name="John Gillan", gedcom_id="@I42@")
```

The server reads the `.ged` file directly — no separate GEDCOM tool needed.

---

## Tools

| Tool | When to use |
|---|---|
| `find_person` | Any name lookup — defendant, victim, witness, officer, juror |
| `find_crossover` | Find cases where two or more people appear together |
| `search_proceedings` | Topic, place, offence, or Lucene boolean searches |
| `search_ordinaries` | Death-row chaplain interviews — pre-1773 capital cases only |
| `search_associated` | Petitions, depositions, correspondence linked to a trial |
| `get_record` | Full transcript of one specific case by ID |

**Resources (read without API calls):**
- `oldbailey://known/` — everyone found so far this session
- `oldbailey://known/{name}` — full case history for one person

---

## Example queries

Find a police witness across his career:
```
find_person(name="Gillan", role="officer", date_from="1890", date_to="1913")
```

Find a defendant at a known trial date:
```
find_person(name="William Dodd", role="defendant", date_from="1770", date_to="1780")
```

Find cases where two people appear together:
```
find_crossover(names=["Gillan", "Walsh"])
```

Search for a topic with Lucene syntax:
```
search_proceedings(query='+"Bank of England" +"forgery"', date_from="1800", date_to="1850")
```

Fetch a full trial transcript:
```
get_record(idkey="t18990109-146")
```

---

## Knowledge file

Searches accumulate in `knowledge/persons.json`. This file is gitignored — it contains
your personal research data. It tracks:

- Every case found, with a snippet and a link to the scanned original document
- Which date ranges have already been searched (avoids duplicate API calls)
- Overflow cases in `pending_review` when a search returns more than 8 results

Read `oldbailey://known/` to see everything discovered so far.

---

## Logging

Two log outputs:
- **`oldbailey_mcp.log`** — rotating, full debug output including every API call made
- **stderr** — warnings only, visible in the Claude Desktop MCP panel

The log file is gitignored.

---

## Running tests

```bash
# Unit tests (no network)
uv run pytest tests/ -v --ignore=tests/test_smoke.py

# Smoke tests against the real API
uv run pytest tests/test_smoke.py -v -m slow --run-slow
```
