# Old Bailey MCP — Research Guide

A practical guide to getting the most out of this tool for genealogical research.

---

## Why this version exists — and why you need to understand it

> **NB: Read this before you start searching.**

### The problem with v1

The original server was a thin wrapper around the Old Bailey API. It worked, but it had a
critical flaw: every search was stateless. There was no memory between calls, no caching,
and no way to stop Claude from making the same requests repeatedly.

In practice, what happened was this:

- Claude would use `search_all` as its default tool — a convenience function that fired
  three sequential API calls per search (proceedings, Ordinary's Accounts, associated records)
  whether any of those collections were relevant or not.
- The GEDCOM MCP, which iterates over family members, called the same tool for every person
  in the tree — with no awareness that some names had already been searched that session.
- Name searches without quoted phrases returned tens of thousands of false positives.
  Searching for `John Smith` matched anything containing the word "John" or "Smith" separately.
- Date filtering happened client-side after fetching up to 200 records. If your ancestor
  appeared only in record 201, the search silently returned nothing.
- Witnesses, victims, and police officers were invisible. The only structured name field
  was `defendantNames`. Everyone else could only be found via free-text search — which,
  without quoted phrases, was unreliable.

### The deeper problem: no internal systemic reasoning

These failures were not just bugs in the code. They reflected a more fundamental issue: when
Claude uses an MCP tool, it reasons about what to call based on the tool's description and
its own general knowledge. It does not have the internal model of the data that a human
researcher would build up over time.

Left to its own devices, Claude will:

- **Default to the broadest tool available.** If `search_all` exists, Claude reaches for it
  because it seems like the safest choice. It doesn't know that three API calls is wasteful
  when one would do.
- **Repeat searches it has already done.** Without a memory mechanism, Claude has no way to
  know it searched "John Gillan" five minutes ago. Every new question starts from scratch.
- **Treat all results as equally relevant.** Without date or role context, Claude will process
  a result from 1720 just as seriously as one from 1899 — even when you told it your ancestor
  was born in 1860.
- **Escalate to full-text retrieval unnecessarily.** Claude tends to call `get_record` on
  every result to "be thorough", flooding the context window with full trial transcripts
  when a snippet would have been enough.

V2 was built specifically to work around these failure modes. The knowledge file, the tool
descriptions, the index mode, and the middleware are all countermeasures against the natural
drift patterns of an AI assistant with no persistent state.

**This means your results are only as good as how precisely you ask.** The tool can steer
Claude in the right direction, but it cannot override a vague question.

---

## How to ask well

### Start specific, not broad

Bad:
> "Find my ancestors in the Old Bailey"

This gives Claude nothing to constrain the search. It will likely call `search_proceedings`
with a surname, return thousands of results, and then try to read them all.

Good:
> "Search for John Gillan as a police officer between 1890 and 1913"

This maps directly to a single `find_person` call with `role="officer"`,
`date_from="1890"`, `date_to="1913"`.

### Always give a date range when you have one

The Old Bailey corpus spans 240 years. A common surname like "Smith" or "Jones" will return
hundreds of results. Even an approximate date range — derived from a birth year or a known
event — cuts the noise dramatically.

If you have a GEDCOM file configured, this happens automatically. The server reads birth and
death years from your family tree and applies them without you having to specify.

### Tell Claude what role the person played

The server routes to different API endpoints depending on role:

| Role | What it searches | Use when |
|---|---|---|
| `any` (default) | Full proceedings text | You don't know how they appear |
| `defendant` | Structured defendant field | You know they were charged |
| `victim` | Structured victim field | You know they were the victim |
| `officer` | Full text + police keyword filter | Police, constables, inspectors |

If you know your ancestor was a police officer, saying `role="officer"` is not just a
convenience — it changes the query construction. The server adds required Lucene terms
(`inspector`, `constable`, `sergeant`, `detective`) which would otherwise return results
where those words appear anywhere in the document.

---

## The knowledge file — your research memory

Every search writes results to `knowledge/persons.json`. This is the most important
feature to understand.

**First search:** hits the API, returns results, saves everything to the file.

**Second search for the same person:** reads from the file — zero API calls, instant response.

The file tracks which date ranges have been searched. If you searched 1890–1913 already,
searching 1890–1913 again returns the cached results. But searching 1880–1913 will hit the
API again because the earlier range wasn't covered.

### Index mode and pending cases

When a search returns more than 8 results, the server switches to index mode:

- The first 8 results are returned in full (with 400-character snippets).
- Additional results are saved to `pending_review` in the knowledge file with shorter
  150-character snippets.
- You get a message telling you how many cases were logged for later.

This prevents context floods. You can review the first 8, decide which full transcripts
you want, and then read `oldbailey://known/` to see what's waiting.

**To review pending cases:**

Ask Claude to read `oldbailey://known/Gillan` (or whichever name). This returns the full
knowledge file entry — including every pending case — without making any API calls.

---

## Tool decision guide

```
Is this a NAME search?
├── Yes → find_person
│   ├── Do you know the role? → set role=
│   ├── Do you have dates? → set date_from/date_to
│   └── Do you have a GEDCOM ID? → set gedcom_id= (auto-fills dates + role)
│
├── Are you looking for cases where TWO people appear together?
│   └── find_crossover (checks knowledge file first — zero API if both known)
│
├── Is this a TOPIC, PLACE, or OFFENCE search?
│   └── search_proceedings (use + operator for required terms)
│
├── Was the person sentenced to death before 1773?
│   └── search_ordinaries (Newgate chaplain death-row interviews)
│
├── Did a trial result suggest a petition or follow-up document?
│   └── search_associated
│
└── Do you have a specific idkey and need the full transcript?
    └── get_record (only when the snippet wasn't enough)
```

---

## Lucene query syntax — what works and what doesn't

The Old Bailey API uses Lucene for full-text queries. The syntax matters.

### Quoted phrases — always use them for names

```
"John Gillan"     ← matches this exact phrase
John Gillan       ← matches anything containing "John" OR "Gillan" — useless
```

The server automatically wraps names in quotes when you use `find_person`. But if you use
`search_proceedings` directly, you must quote phrases yourself.

### Required terms — use `+`, not `AND`

```
+"Gillan" +"inspector"     ← both terms must appear — correct
"Gillan" AND "inspector"   ← returns ~180,000 results — broken
```

The `AND` operator is technically Lucene syntax but behaves incorrectly against this corpus.
The `+` operator (required term) works correctly. Use it whenever you need two things to
appear together.

### Proximity search — useful for role + name combinations

```
"Inspector Gillan"~5     ← "Inspector" within 5 words of "Gillan"
```

Useful when the exact phrase order varies in the transcripts.

### Wildcard — useful for name variants

```
Gillan*     ← matches Gillan, Gillane, Gillans, etc.
```

---

## Crossover searches — finding shared cases

`find_crossover` is the fastest way to find cases where two people appear together.

If both names are already in your knowledge file, the server intersects their case sets
locally — no API call at all. You get an instant answer.

If either name is unknown, it builds a compound query:

```
+"Gillan" +"Walsh"
```

This finds every trial where both names appear in the same transcript.

Good uses:
- "Did my great-great-grandfather appear in the same trial as his brother?"
- "Was this inspector involved in cases with this known criminal?"
- "Do any of these family members share a case?"

---

## GEDCOM workflow

If you have a `.ged` file configured, the most efficient workflow for searching family
members is:

1. Open your family tree software and find the individual's GEDCOM ID (e.g. `@I42@`).
2. Ask Claude: `find_person(name="John Gillan", gedcom_id="@I42@")`
3. The server reads birth year, death year, and occupation from your `.ged` file and
   applies them automatically.
4. Results are saved to the knowledge file under the GEDCOM ID.
5. Subsequent searches for `@I42@` return from the file instantly.

This is especially useful when searching multiple family members — the knowledge file
accumulates across the session so each name is only ever looked up once.

---

## Common research scenarios

### Scenario 1: Police ancestor

Your ancestor was a Metropolitan Police officer, active roughly 1885–1910.

```
find_person(name="[surname]", role="officer", date_from="1880", date_to="1915")
```

The `officer` role adds police keyword terms to the query, filtering out irrelevant matches.
If results come back in index mode, read `oldbailey://known/[surname]` to see all cases
including pending ones. Then use `get_record` on the specific trials you want to read in full.

### Scenario 2: Defendant

Your ancestor was charged with a crime. You know roughly when.

```
find_person(name="[full name]", role="defendant", date_from="[year-5]", date_to="[year+5]")
```

The `defendant` role searches the structured `oldbailey_defendant` endpoint — more precise
than full-text for common names.

If the name is common, narrow the date range further. Check the `offences` field in results
— the category (theft, forgery, violent crime etc.) can help identify the right person if
multiple people share the name.

### Scenario 3: Death sentence pre-1773

Your ancestor was hanged at Tyburn or Newgate.

```
find_person(name="[name]", role="defendant", date_from="[year]", date_to="[year]")
```

Then, if a death sentence appears in the results:

```
search_ordinaries(text='"[name]"', date_from="[year]", date_to="[year]")
```

The Ordinary's Accounts contain detailed biographical interviews: origins, trade, religion,
account of the crime, last words. This is often the richest biographical source available
for 18th century ancestors.

### Scenario 4: Family group crossover

You want to know if multiple family members appear in the same cases.

First search each name individually via `find_person` to populate the knowledge file.
Then:

```
find_crossover(names=["[name1]", "[name2]", "[name3]"])
```

Because all names are already known, this returns shared cases instantly.

### Scenario 5: Topic or place research

You want all forgery cases at the Bank of England in the 1800s.

```
search_proceedings(
  query='+"Bank of England" +"forgery"',
  date_from="1800",
  date_to="1900"
)
```

This is the right tool for topic research. Do not use `find_person` for this — it is
designed for names, not topics.

---

## What to do when results seem wrong

**Getting too many irrelevant results:**
- Add `role=` to narrow the endpoint
- Narrow the date range
- Use a more specific quoted phrase (full name rather than surname only)

**Getting zero results:**
- Check the date range isn't too narrow
- Try `role="any"` — maybe they appear in a different role than expected
- Try the surname only rather than full name — transcription variants are common
- Try a wildcard: `search_proceedings(query='"Gillan*"')`

**The same result appearing from 1720 when your ancestor lived in 1890:**
- Add `date_from` and `date_to` — the corpus spans 240 years and surnames recur across generations

**Results look right but Claude is calling get_record on everything:**
- Explicitly tell Claude: "Don't fetch full records — just review the snippets and tell me
  which cases are relevant."
- The tool descriptions include a STOP instruction but Claude can drift from it,
  especially in long conversations.

---

## Reading scanned originals

Every result includes an `image_url` — a direct link to the scanned page of the original
printed proceedings. These are `.gif` files hosted at `dhi.ac.uk`.

The URL encodes the approximate date and page number:
```
https://www.dhi.ac.uk/san/ob/1890s/189901090055.gif
                                  ↑year  ↑date ↑page
```

Ask Claude to include image URLs in its summary if you want to view the originals.
