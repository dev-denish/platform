"""AI-generated report narrative (Wave: ai-report-narrative, Phase 2).

Generates the narrative fields that `report_content.build_section_content`
copies into a `ReportSection` (see that module's docstring) - via Gemini
(Google's hosted API), for ALL 13 catalog analysis types, not just the 5
vegetation indices `app/services/index_summary.py` already covers
deterministically. This is NET-NEW generation from the same validated
`analysis_result.stats` JSON `build_section_content`/`index_summary.py`
already consume (see tests/unit/test_report_content.py for the real shape) -
not a rewrite of `index_summary.py`'s text, and it never touches
`note`/`description`/`disclaimer`.

Decision (2026-08-12): Gemini is the ONLY narrative backend. Two local
Ollama models (phi4-mini, qwen2.5) were evaluated during this wave's demo
phase and are no longer supported - there is no model-selection knob left;
`GEMINI_MODEL` below is a fixed constant, not a config value. Calls Google's
official `google-genai` SDK directly (a hosted third-party API over the
public internet, reached via `google.genai.Client`) - this is NOT the
`app/services/external_fetch.py` `safe_fetch` SSRF-guarded path, but not
because it's exempt from that threat model: it simply isn't a caller-chosen
URL at all, the SDK owns its own endpoint resolution.

DETERMINISTIC GUARANTEE, not just a system-prompt ask: `index_summary.py`'s
whole reason to exist is that its text is byte-reproducible from stored
numbers, auditable by a VVB. An LLM can't give us that, so instead we give a
weaker but real guarantee - every numeric token the model outputs must be
traceable to a number actually present in that section's input JSON (see
`verify_numeric_grounding`). A model that invents a statistic fails this
check exactly like a network error: the section, and therefore the whole
report, is rejected (see FAILURE SEMANTICS below).

R5 fix (real dmrv-qa QA pass, esa_worldcover, 2026-08-11): a real generated
summary read "...cropland took most with over seven thousand square
kilometers or more than seventy-one million acres... Tree cover dominated at
almost eight hundred thousand square kilometers which is roughly eighty-five
times the size of California's Yosemite National Park... permanent water
bodies had no coverage reported here" against real stats of
`Cropland: 7,518.70 ha`, `Tree cover: 7,604.25 ha`,
`Permanent water bodies: 77.71 ha` - wrong by 90x-10,000x, a fabricated
analogy, and a false absence claim about a class the model was actually
given a non-zero figure for. This PASSED `verify_numeric_grounding` and
shipped in a real PDF, because the old `_NUMBER_RE` only matches digit
tokens: "seven thousand"/"eighty-five"/"seventy-one" are never extracted as
candidates, so they're never checked against anything. `verify_numeric_grounding`
now also flags spelled-out number words/phrases unconditionally (see
`_NUMBER_WORD_RE`) - it doesn't parse them into a value, presence alone is
the violation, since rule 5 of `_SYSTEM_PROMPT` tells the model to never use
one. The fabricated-analogy and false-absence failure modes have no digit or
word-number to catch (there is no number "85x Yosemite" anywhere in either
form, and "no coverage" is a wrong claim about real data, not a number at
all) - those two are closed at the prompt level instead (`_SYSTEM_PROMPT`
rules 3 and 4), not by a post-hoc check, because no post-hoc check can
verify a claim is TRUE, only that a cited number exists somewhere in the
input.

R6 fix (real dmrv-qa dead letters, hansen_gfc, 2026-08-11): two real jobs
were rejected with `verify_numeric_grounding` reporting `2000.0` and
`-2012.0` as ungrounded, for a summary that correctly restated hansen_gfc's
real `"gain_area_ha_2000_2012": <ha>` stats key (gee_analysis_service.py's
real construction site) as "gain from 2000 to 2012" / "the 2000-2012 period"
- not a fabrication, real data embedded in the key's NAME rather than in a
nested year key like `loss_area_ha_by_year`'s "2010". Two independent bugs
compounded: (1) `_flatten_numbers` only added a dict key to `allowed` when
the WHOLE key string was numeric, so a compound key like
`gain_area_ha_2000_2012` contributed nothing - fixed by pulling embedded
4-digit runs out of non-numeric keys too (see `_KEY_EMBEDDED_YEAR_RE`); (2)
even with (1) fixed, the model's own output text "2000-2012" was misparsed
by `_NUMBER_RE` as `2000.0` then `-2012.0` (the hyphen read as a unary
minus with no whitespace requirement before it), so the extracted candidate
could never match the now-grounded `2012.0` - fixed by pulling out
4-digit-hyphen-4-digit ranges as two positive numbers before `_NUMBER_RE`
ever sees that hyphen (see `_YEAR_RANGE_RE`). Genuine negative values (e.g.
an NDVI of "-0.681") are a different shape - not a bare 4-digit run on both
sides of the hyphen - and are extracted exactly as before.

R7 fix (real dmrv-qa manual review, hansen_gfc, 2026-08-11): a real generated
summary correctly wrote "The years 2001, 2002, 2009, 2010, 2013, 2014, 2015,
2016, 2019, 2020, 2021, and 2022 are not listed in the loss figures" - true,
careful absence-reporting (exactly what `_SYSTEM_PROMPT` rule 5 wants), about
a real `loss_area_ha_by_year` that only has entries for 2003/2004/2005/2006/
2007/2008/2011/2012/2017/2018/2023/2024/2025. `verify_numeric_grounding`
flagged every one of those absent years as fabricated, because none of them
is a key anywhere in `stats`. Fix: once a stats dict is established as
year-indexed at all (i.e. `_flatten_numbers` found at least one bare numeric
dict key or `_KEY_EMBEDDED_YEAR_RE` compound-key year - see its new
`year_like` output set), any 4-digit whole number the model writes that falls
within `[min(year_like), max(year_like)]` is accepted automatically,
present or absent from the record - describing the dataset's own known
timeframe is not fabricating a new fact. A number OUTSIDE that range (an
invented year like "1885"/"2150", or a real dataset's range not covering the
cited year at all) still gets the full ungrounded treatment. A genuinely
fabricated STATISTIC attached to an in-range year (an area, a count) is
still caught separately, because that number is checked independently and
won't be in `allowed`. Known, accepted imprecision (not a silent gap): this
cannot distinguish "2015" used as a year-reference from "2015" that happens
to be a whole-number area/count value coincidentally inside the year range
(e.g. a model writing "2015 hectares" would incorrectly pass as a year) -
narrow by design, scoped to whole 4-digit numbers only.

R8 fix (real dmrv-qa manual review, multiple analysis types, 2026-08-11):
several real generations echoed the project's own name back for context,
e.g. "This analysis of AI Narrative QA 1786455528 covered 99.7%..." -
`1786455528` is a timestamp-derived suffix of that test project's name, not
an invented statistic, but `verify_numeric_grounding` only ever built its
allow-set from `stats`, never from `project_name`/`analysis_id`/
`analysis_name`/`category` - the other four fields `_build_user_message`
already puts in the model's own context. The model has no way to know its
own given identifiers are off-limits to mention (`_SYSTEM_PROMPT` rule 1
restricts NUMBER usage to the "data" field for computed statistics; it says
nothing about metadata), and restating metadata it was explicitly given is
not a fabrication risk. Fix: `verify_numeric_grounding` now accepts those
four fields as optional keyword args and unions any numbers found in them
into `allowed` alongside `stats`'s own numbers - purely additive, `stats`-
derived grounding is unchanged. `generate_section_summary` passes its own
`analysis_id`/`name`/`category`/`project_name` through at the one real call
site.

FAILURE SEMANTICS: one call per section (isolates which section's output is
bad), but NOT one-fails-skip-that-section-and-continue. If any section times
out, errors, or fails the numeric-grounding check, `generate_ai_summaries`
raises `AiNarrativeError` identifying that section and stops - it never
falls back to `index_summary.py`/no-summary for just that section, because
that would silently ship a part-AI, part-system report while the report
claims to be "the AI narrative" version. All-succeed or the whole call
raises; no partial dict is ever returned to a caller.

Wave: 11-section report restructure. `generate_section_summary` now returns a
dict of up to 5 named fields (`executive_summary`/`spatial_distribution`/
`key_findings` always; `temporal_analysis`/`change_analysis` only when
`report_content.has_temporal_data`/`has_change_data` say the stats support
them) instead of one bare string - STILL one model call per section (budget
math: `TOTAL_BUDGET_S=600` across up to 13 sections already assumes 1 call
each; 5 calls/section would blow it). The model
is told exactly which keys to produce (`sections_requested` in the user
message, rule 9 in `_SYSTEM_PROMPT`) and must return a single JSON object
with exactly those keys. `verify_numeric_grounding` itself is UNCHANGED - all
of R5-R8 is still load-bearing on a single string - `verify_fields_grounding`
just calls it once per field (joining `key_findings`' bullets into one string
first) and the FAILURE SEMANTICS above now apply at field granularity: one
ungrounded field fails the whole section exactly like one ungrounded number
used to fail the whole paragraph.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.logging import get_logger
from app.services.report_content import has_change_data, has_temporal_data

log = get_logger("dmrv.ai_narrative")

__all__ = [
    "AiNarrativeError",
    "GEMINI_MODEL",
    "verify_numeric_grounding",
    "verify_fields_grounding",
    "generate_section_summary",
    "generate_ai_summaries",
]

# The only narrative backend (decision 2026-08-12) - not a config value, since
# there is nothing left to select between. Bump this constant directly if
# Google retires/renames the model; it never varies per-request or per-env.
GEMINI_MODEL = "gemini-3.5-flash"

# Always-requested narrative fields, in a fixed order (the JSON key order the
# model is told to produce) - `temporal_analysis`/`change_analysis` are
# appended only when the stats shape actually supports them (see
# `_applicable_narrative_fields`), the SAME two capability checks
# `report_pdf.py` uses to decide whether to render those headings at all.
_ALWAYS_REQUESTED_FIELDS = ("executive_summary", "spatial_distribution", "key_findings")
_KEY_FINDINGS_FIELD = "key_findings"
_MIN_KEY_FINDINGS = 3
_MAX_KEY_FINDINGS = 5


def _applicable_narrative_fields(stats: dict[str, Any]) -> list[str]:
    fields = list(_ALWAYS_REQUESTED_FIELDS)
    if has_temporal_data(stats):
        fields.append("temporal_analysis")
    if has_change_data(stats):
        fields.append("change_analysis")
    return fields

# carbon-mrv-vm0047 review, R3: top-level `stats` keys that are free-text
# prose rather than numeric/structural data, across all 13 catalog analysis
# types (checked against analysis_catalog.py's real construction sites in
# gee_analysis_service.py and the shapes in tests/unit/test_report_content.py):
#   * "note" - hansen_gfc/esa_worldcover/io_lulc/modis_lulc/all 5 vegetation
#     indices/all 3 raw-imagery browse ids: a dataset-caveat sentence.
#   * "summary" - the 5 vegetation indices only: index_summary.py's own
#     deterministic paragraph.
#   * "methodology" - io_lulc/modis_lulc/the 5 vegetation indices: a dict,
#     but built around dataset labels and formula text (e.g.
#     "NDVI = (NIR - RED) / (NIR + RED)"), not numbers a summary should cite.
# dynamic_world has none of these three keys at all - pruning is a no-op for
# it, which is correct (nothing to strip).
_PROSE_KEYS = frozenset({"note", "summary", "methodology"})


def _prune_prose(stats: dict[str, Any]) -> dict[str, Any]:
    """Strips `_PROSE_KEYS` from `stats` before it reaches either the LLM
    prompt (`_build_user_message`) or the numeric-grounding check
    (`verify_numeric_grounding`) - both MUST see the same pruned dict, never
    a raw one for the prompt and a pruned one for grounding or vice versa,
    or the check stops meaning anything.

    Concrete motivating bug (not just a security tidy-up): hansen_gfc's
    `note` reads "Gain is a whole-period 2000-2012 figure only" - Hansen's
    own numeric loss years are 2003/2010 (whatever the real data is), never
    2000 or 2012, so a model that faithfully restates the note's "2000" or
    "2012" produces a number `verify_numeric_grounding` cannot trace to
    anything else in the dict, and the whole report gets dead-lettered for
    a summary that didn't actually invent anything. Removing the note
    before the model ever sees it removes the only reason it would mention
    those years in the first place.

    Secondary reason: `note`/`summary`/`methodology` carry
    index_summary.py's own VM0047-adjacent vocabulary ("additionality",
    "biomass sampling", forest-definition caveats) - keeping that out of the
    model's context reduces the chance it gets parroted into an
    inappropriate claim (see `_SYSTEM_PROMPT`'s banned-claims list).

    Every numeric/structural field - areas, percentages, series,
    distributions, class breakdowns, coverage - passes through unchanged."""
    return {k: v for k, v in stats.items() if k not in _PROSE_KEYS}


class AiNarrativeError(Exception):
    """One section's Gemini call timed out, errored, or produced a summary
    that could not be numerically grounded in its own input JSON. Always
    raised (never swallowed) - see module docstring's FAILURE SEMANTICS."""


_SYSTEM_PROMPT = (
    "You write one short plain-English paragraph summarising an environmental "
    "monitoring analysis result for a non-technical reader.\n"
    "Rules, follow every one exactly:\n"
    "1. The user message is a JSON object. Its \"data\" field is the ONLY source "
    "of numbers you may use. Never invent, estimate, extrapolate, or restate a "
    "number, percentage, trend, or count that is not present in \"data\".\n"
    "2. Never make a forest-definition, eligibility, additionality, or carbon-credit "
    "determination. Never use permanence or reversal-risk language (e.g. \"stable\", "
    "\"secure\", \"low risk of reversal\"). Never make a leakage claim. Never use "
    "uncertainty or confidence language (e.g. \"within acceptable uncertainty\", \"high "
    "confidence\") - uncertainty here means a specific propagated statistic, not an "
    "adjective you can apply. Never compare a value to a baseline or \"business as "
    "usual\" scenario. Never infer biomass or carbon stock, growth, or removals from a "
    "proxy value such as an index or land-cover class. Never claim or imply causation "
    "(e.g. \"caused by\", \"due to planting\", \"as a result of\") - describe what the "
    "data shows, not why it changed. Never use compliance or verification framing (e.g. "
    "\"verified\", \"audited\", \"compliant\", \"creditable\"). Describe the numbers, do "
    "not certify them.\n"
    "3. Use only the unit implied by each field's own name: a \"_ha\" field is hectares, "
    "a \"_pct\" field is percent, an index value (e.g. NDVI, EVI) is unitless. Never "
    "convert a value to a different unit, and never attach a carbon or biomass unit "
    "(tonnes, tC, tCO2e) to a value that is not already expressed in that unit.\n"
    "4. Never use a comparison, analogy, or real-world size reference that is not "
    "literally present in \"data\" (e.g. \"the size of Yosemite National Park\", "
    "\"equivalent to X football fields\", \"eighty-five times larger than\"). If a "
    "number, place, or object is not itself a value in \"data\", do not mention it.\n"
    "5. Never state that a category, class, or value is absent, zero, missing, or "
    "\"not reported\" unless \"data\" actually shows that value as zero, null, or "
    "omits it entirely. Check the actual figure for every class before describing it "
    "as absent - do not guess or assume \"no coverage\" for a class you have not "
    "carefully checked.\n"
    "6. Always write every number using digits exactly as in \"data\" (e.g. "
    "\"7,518.70 ha\", \"99.7%\"). Never spell a number out in words (\"seven "
    "thousand\", \"eighty-five\"), never convert it into a scale word (\"thousand\", "
    "\"million\") or a different unit (\"square kilometers\", \"acres\") than the one "
    "implied by the field's own name, and never round or approximate it into a "
    "phrase not present in \"data\" (\"almost eight hundred thousand\", \"roughly\").\n"
    "7. Plain English, no jargon, short sentences, active voice.\n"
    "8. No heading, no preamble such as \"Here is a summary:\", no markdown, no "
    "quotation marks. No bullet points in any field except \"key_findings\".\n"
    "9. Output ONLY a single JSON object, no markdown code fencing, no prose outside "
    "it. Its keys are EXACTLY the names listed in the user message's "
    "\"sections_requested\" array - no more, no fewer. Every value is a plain string, "
    "except \"key_findings\", whose value is a JSON array of "
    f"{_MIN_KEY_FINDINGS}-{_MAX_KEY_FINDINGS} short one-sentence strings."
)

# Generous per-call ceiling for one Gemini round trip - this runs inside an
# already-async background job (see workers/report_jobs.py), never inline in
# a request/response, so there's no user staring at a spinner to optimize
# for. Total budget bounds the worst case across up to 13 sections (the whole
# catalog) at something a background job can still reasonably be expected to
# finish within.
PER_SECTION_TIMEOUT_S = 90.0
TOTAL_BUDGET_S = 600.0

# Matches a plain or comma-grouped integer/decimal, optionally negative,
# optionally trailing "%" (coverage_pct etc. often gets narrated as "98%").
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")

# R6 fix, part 2: the SAME hyphen that separates two real years in the
# model's own output text ("gain from 2000-2012") gets misread by `_NUMBER_RE`
# above as a unary minus glued onto the second year - findall on "2000-2012"
# yields "2000" then "-2012", not "2000" and "2012", because `-?\d...`
# happily matches starting at the hyphen with no whitespace requirement
# before it. That flips the sign of a real, grounded number into one that can
# never match (`_grounded` has no tolerance for a sign flip), so the fix in
# `_flatten_numbers` above wouldn't have been enough on its own - this is the
# other half of the same two real dead-lettered jobs.
# Scoped deliberately to a 4-digit/4-digit shape (a year range), not "any
# digit run before a hyphen before a digit run" - a genuine negative number
# such as "-0.681" or "-12.3%" is never `\d{4}-\d{4}` (it has a decimal point,
# or isn't 4 bare digits on both sides) and is untouched by this.
_YEAR_RANGE_RE = re.compile(r"(?<!\d)\d{4}-\d{4}(?!\d)")

# How many decimal places a model's rounded-down restatement of a source
# number might use (e.g. 42.567 -> "42.6" or "43"). Matched against, not
# against the candidate's own decimal count, so "43" from "42.567" still
# passes.
_ROUNDING_TOLERANCE_DIGITS = range(0, 6)

# R5: spelled-out quantities (see module docstring's R5 note for the real
# bug). `_NUMBER_RE` above only ever matches digit tokens, so "seven
# thousand", "seventy-one million", "eighty-five" are invisible to it - not
# a parsing gap worth closing (this deliberately does NOT try to resolve a
# word-number to a value), just a tripwire: `_SYSTEM_PROMPT` rule 6 tells the
# model to never spell a number out, so any match here is an unconditional
# violation, same as a digit with no source value.
_ONES = r"one|two|three|four|five|six|seven|eight|nine"
_TEENS = (
    r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
)
_TENS = r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_SCALES = r"hundred|thousand|million|billion"

_NUMBER_WORD_RE = re.compile(
    r"\b(?:"
    rf"(?:{_TENS})(?:[\s-]+(?:{_ONES}))?\s+(?:{_SCALES})"  # "seventy[-one] million"
    rf"|(?:{_TEENS}|{_ONES})\s+(?:{_SCALES})"  # "seven thousand", "eighteen hundred"
    rf"|(?:{_TENS})[\s-]+(?:{_ONES})"  # "eighty-five"
    rf"|{_SCALES}"  # bare "hundred"/"thousand"/"million"/"billion"
    rf"|half\s+(?:a\s+|of\s+a\s+)?percent"  # "half a percent"
    r")\b",
    re.IGNORECASE,
)


def _find_number_words(text: str) -> list[str]:
    """Every spelled-out quantity phrase in `text` - see `_NUMBER_WORD_RE`.
    Not resolved to a value; presence is the violation."""
    return [m.group(0) for m in _NUMBER_WORD_RE.finditer(text)]


def _is_number_token(s: str) -> bool:
    try:
        float(s.replace(",", ""))
    except ValueError:
        return False
    return True


# R6 fix (real dmrv-qa dead letters, hansen_gfc, 2026-08-11): hansen_gfc's
# real stats carry `"gain_area_ha_2000_2012": <ha>` (gee_analysis_service.py's
# real construction site, ~line 826) - Hansen's gain figure is a single
# whole-period number, and 2000/2012 are the real, non-fabricated bounds of
# that period baked into the KEY NAME itself (checked across all 13 catalog
# types' real construction sites in gee_analysis_service.py: this compound,
# embedded-year key shape is unique to `gain_area_ha_2000_2012` - every other
# digit-bearing key, e.g. `boundary_area_m2`, `treecover2000` (an internal GEE
# band name, never a stats key), has no 4-digit run in it, and every other
# year-indexed dict - `loss_area_ha_by_year`, `class_area_ha_by_year` - uses
# the year as its OWN bare key ("2010"), already covered by the
# `_is_number_token` branch below). A model that faithfully wrote "gain from
# 2000 to 2012" was restating real data, but the old code only ever added a
# dict key to `allowed` when the ENTIRE key string was numeric, so
# `gain_area_ha_2000_2012` contributed nothing - two real jobs were
# dead-lettered over `verify_numeric_grounding` flagging `2000.0`/`-2012.0` as
# invented. `_KEY_EMBEDDED_YEAR_RE` pulls 4-digit runs out of a compound key
# name specifically (not any digit substring - `boundary_area_m2`'s "2" is one
# digit, never matches), so those years land in `allowed` too.
_KEY_EMBEDDED_YEAR_RE = re.compile(r"(?<!\d)\d{4}(?!\d)")


def _flatten_numbers(
    node: Any, out: set[float], year_like: set[float] | None = None
) -> None:
    """Every numeric VALUE in the input JSON, plus dict KEYS that are
    themselves bare numbers (year keys - `series`/`distribution`/
    `class_area_ha_by_year`/`loss_area_ha_by_year` all index by year string,
    e.g. "2026", which a narrative legitimately restates as the year 2026),
    plus 4-digit year-like runs embedded WITHIN a compound key name (see R6
    fix above - e.g. `gain_area_ha_2000_2012` contributes both 2000 and
    2012).

    `year_like` (R7 fix, optional - callers that only want the flat `out`
    set, e.g. existing tests, pass nothing) collects ONLY the two year-shaped
    sources above (bare numeric dict keys, embedded compound-key years) -
    never a plain numeric VALUE - so `verify_numeric_grounding` can derive
    "this dataset's own observed year range" without treating an arbitrary
    value (e.g. `baseline_forest_area_ha: 100.0`) as a year bound."""
    if isinstance(node, bool):
        return  # bool is an int subclass; not a number a summary would cite
    if isinstance(node, int | float):
        out.add(float(node))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                if _is_number_token(key):
                    year = float(key.replace(",", ""))
                    out.add(year)
                    if year_like is not None:
                        year_like.add(year)
                else:
                    for m in _KEY_EMBEDDED_YEAR_RE.finditer(key):
                        year = float(m.group(0))
                        out.add(year)
                        if year_like is not None:
                            year_like.add(year)
            _flatten_numbers(value, out, year_like)
        return
    if isinstance(node, list | tuple):
        for item in node:
            _flatten_numbers(item, out, year_like)


def _extract_candidate_numbers(text: str) -> list[float]:
    candidates = []
    # Pull out "YYYY-YYYY" ranges first, as two positive numbers, then blank
    # them out of the text so `_NUMBER_RE` below never sees that hyphen and
    # misreads it as a unary minus on the second year (see `_YEAR_RANGE_RE`).
    ranges = list(_YEAR_RANGE_RE.finditer(text))
    for m in ranges:
        start, end = m.group(0).split("-")
        candidates.append(float(start))
        candidates.append(float(end))
    if ranges:
        text = _YEAR_RANGE_RE.sub(" ", text)
    for raw in _NUMBER_RE.findall(text):
        cleaned = raw.replace(",", "").rstrip("%")
        if cleaned in ("", "-"):
            continue
        try:
            candidates.append(float(cleaned))
        except ValueError:
            continue
    return candidates


def _grounded(candidate: float, allowed: set[float]) -> bool:
    for value in allowed:
        if math.isclose(candidate, value, rel_tol=1e-9, abs_tol=1e-6):
            return True
        if any(round(value, n) == candidate for n in _ROUNDING_TOLERANCE_DIGITS):
            return True
    return False


# R8 fix: numbers embedded in the model's OWN given context fields
# (`project_name`/`analysis_id`/`analysis_name`/`category` - the same four
# `_build_user_message` sends outside "data") are legitimate for the model
# to echo back for context, not a fabrication - see module docstring's R8
# note for the real "AI Narrative QA 1786455528" case. Reuses `_NUMBER_RE`
# (the same extractor run over the model's OUTPUT) since these fields are
# free text that could contain a comma-grouped or decimal number just like
# the narrative itself, not only bare digit runs.
def _numbers_in_identifier_fields(*fields: str | None) -> set[float]:
    """Digits found in any of `fields` (skip `None`/empty) - unioned into
    `verify_numeric_grounding`'s allow-set, additive only, never subtracted
    from what `stats` already grounds."""
    found: set[float] = set()
    for field in fields:
        if not field:
            continue
        for raw in _NUMBER_RE.findall(field):
            cleaned = raw.replace(",", "").rstrip("%")
            if cleaned in ("", "-"):
                continue
            try:
                found.add(float(cleaned))
            except ValueError:
                continue
    return found


# R7 fix: a bare 4-digit number is only ever eligible for year-range
# leniency (never a decimal, never a 3-digit or 5+-digit number) - see
# module docstring's R7 note. Deliberately as narrow a carve-out as the real
# bug needs: "2015" can pass this way, "2015.5" or "20150" cannot.
def _is_bare_year_shaped(candidate: float) -> bool:
    return candidate == int(candidate) and 1000 <= candidate <= 9999


def verify_numeric_grounding(
    text: str,
    stats: dict[str, Any],
    *,
    analysis_id: str | None = None,
    analysis_name: str | None = None,
    category: str | None = None,
    project_name: str | None = None,
) -> list[float | str]:
    """Returns the numbers/number-words in `text` that could NOT be traced to
    any numeric value (or numeric dict key) in `stats` - empty list means the
    text passes. Exact-match and formatting differences ("12.5" vs "12.50")
    are free (both parse to the same float); a model restating a source
    number rounded to fewer decimal places is also accepted (see
    `_grounded`). A digit-based number with no corresponding source value at
    all is reported, unconditionally (as a `float`).

    R5: also unconditionally reports any spelled-out number word/phrase
    (see `_find_number_words`, as a `str`) - these can never be "grounded"
    by construction, since `_SYSTEM_PROMPT` rule 6 bans them outright rather
    than asking the check to trace a word to a value.

    R7: any bare 4-digit number that falls within `stats`'s own observed
    year range (see `_flatten_numbers`'s `year_like` output, and this
    function's module-docstring R7 note) is accepted even if that exact year
    is absent from `stats` - describing which years a year-indexed dataset
    does/doesn't cover is not fabrication.

    R8: `analysis_id`/`analysis_name`/`category`/`project_name` (all
    optional, all `None` by default - existing callers passing only
    `text`/`stats` are unaffected) are additional legitimate sources for a
    cited number - see module docstring's R8 note and
    `_numbers_in_identifier_fields`."""
    allowed: set[float] = set()
    year_like: set[float] = set()
    _flatten_numbers(stats, allowed, year_like)
    allowed |= _numbers_in_identifier_fields(
        analysis_id, analysis_name, category, project_name
    )
    min_year = min(year_like) if year_like else None
    max_year = max(year_like) if year_like else None

    def _in_observed_year_range(candidate: float) -> bool:
        return (
            min_year is not None
            and _is_bare_year_shaped(candidate)
            and min_year <= candidate <= max_year
        )

    ungrounded: list[float | str] = [
        c
        for c in _extract_candidate_numbers(text)
        if not _grounded(c, allowed) and not _in_observed_year_range(c)
    ]
    ungrounded.extend(_find_number_words(text))
    return ungrounded


def verify_fields_grounding(
    fields: dict[str, str],
    stats: dict[str, Any],
    *,
    analysis_id: str | None = None,
    analysis_name: str | None = None,
    category: str | None = None,
    project_name: str | None = None,
) -> dict[str, list[float | str]]:
    """Wave: 11-section report restructure. Calls the UNCHANGED
    `verify_numeric_grounding` once per named field - `key_findings`'s bullets
    are already joined into one string by the caller before reaching here, so
    no new extraction/checking code path exists for it, same regex machinery
    as every other field. Returns only the fields that failed (empty dict = no
    violations, all fields ground cleanly); the caller treats ANY non-empty
    result as a whole-section failure, same all-or-nothing contract as the
    single-string version, just at field granularity."""
    return {
        field_name: bad
        for field_name, text in fields.items()
        if (
            bad := verify_numeric_grounding(
                text, stats, analysis_id=analysis_id, analysis_name=analysis_name,
                category=category, project_name=project_name,
            )
        )
    }


def _build_user_message(
    analysis_id: str, name: str, category: str, stats: dict[str, Any], project_name: str | None,
    sections_requested: list[str],
) -> str:
    """One JSON object as the entire user message - project/layer names (the
    only user-controlled strings that reach this prompt) sit inside a JSON
    string value alongside the catalog/stats data, never spliced into free
    text, so there is no manual string composition for an adversarial name to
    break out of. This is not a defence against prompt injection (see module
    docstring: not a code-execution risk here) - the numeric-grounding check
    is the real backstop against a bad output, regardless of what the prompt
    says.

    `sections_requested` (Wave: 11-section report restructure) tells the model
    exactly which narrative fields to produce - see rule 9 in `_SYSTEM_PROMPT`
    and `_applicable_narrative_fields`."""
    payload: dict[str, Any] = {
        "analysis_id": analysis_id, "analysis_name": name, "category": category,
    }
    if project_name:
        payload["project_name"] = project_name
    payload["sections_requested"] = sections_requested
    payload["data"] = stats
    return json.dumps(payload, default=str)


def _resolve_gemini_api_key() -> str:
    """`GEMINI_API_KEY` is read as a bare env var, deliberately NOT under the
    `DMRV_` prefix `Settings` otherwise requires - it's a personal API
    credential, not app config, and this is the name Google's own tooling
    already expects. Falls back to `~/.gemini.env` (this demo host's existing
    pattern: a file holding nothing but the raw key, no `KEY=` prefix) only
    when the env var is unset - never the reverse, so an explicit env var
    always wins. Never hardcoded, never committed - if neither is present,
    this raises the same clean `AiNarrativeError` every other failure mode
    here does, not a bare `FileNotFoundError`/`KeyError`."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    gemini_env_path = Path.home() / ".gemini.env"
    if gemini_env_path.exists():
        key = gemini_env_path.read_text().strip()
        if key:
            return key
    raise AiNarrativeError(
        "AI-generated reports require GEMINI_API_KEY, but no GEMINI_API_KEY env var "
        "and no ~/.gemini.env file was found on this host."
    )


def _call_gemini(model: str, user_message: str, analysis_id: str, timeout_s: float) -> str:
    api_key = _resolve_gemini_api_key()
    client = genai.Client(
        api_key=api_key, http_options=genai_types.HttpOptions(timeout=int(timeout_s * 1000))
    )
    config = genai_types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT, temperature=0, response_mime_type="application/json"
    )
    try:
        response = client.models.generate_content(
            model=model, contents=user_message, config=config
        )
    except genai_errors.APIError as e:
        raise AiNarrativeError(
            f"AI narrative generation for section '{analysis_id}' failed calling "
            f"Gemini ({model}): {e}"
        ) from e
    except Exception as e:  # noqa: BLE001 - network/SDK errors get the same clean-failure contract
        raise AiNarrativeError(
            f"AI narrative generation for section '{analysis_id}' failed calling "
            f"Gemini ({model}): {e}"
        ) from e
    return (response.text or "").strip()


# Small defensive strip, not a parser: rule 9 tells the model never to fence
# its JSON in markdown, and `response_mime_type="application/json"` (see
# `_call_gemini`) asks the SDK to enforce that too, but stripping a LEADING
# ``` fence (with or without a "json" language tag) before parsing costs
# nothing when there is no fence, and avoids a real, otherwise-correct
# response getting rejected as "malformed" purely over formatting if either
# guard ever slips. Trailing content (a closing fence, or trailing prose the
# model appended despite rule 9) is handled separately below via
# `raw_decode`, not by trying to regex-strip every shape it could take.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)


def _parse_narrative_json(
    text: str, sections_requested: list[str], analysis_id: str, model: str
) -> dict[str, str]:
    """Parses the model's raw text into exactly the fields in
    `sections_requested` - wrong type, wrong/missing/extra keys, or malformed
    JSON all raise `AiNarrativeError` with the same "unexpected response
    shape" contract `generate_section_summary` has always had for a bad
    response. `key_findings`'s array is joined into one "\\n"-separated
    string here so every downstream consumer (grounding check, `ReportSection.
    narrative`, `report_pdf.py`) sees the same dict[str, str] shape regardless
    of whether it came from this AI path or `report_deterministic_narrative`.

    Real dmrv-qa failure (hansen_gfc, 2026-08-12): a real Gemini response
    ended with a JSON object followed by a closing ``` fence/trailing text on
    its own line - `json.loads` rejected the ENTIRE otherwise-well-formed
    response as "Extra data" at that point, dead-lettering a report whose
    actual JSON was fine. `JSONDecoder.raw_decode` parses the first complete
    JSON value starting at index 0 and simply stops there, deliberately
    ignoring anything after it - exactly right here, since rule 9 says only
    the JSON is meant to be the output; whatever trails it was never meant to
    be part of the response in the first place, not content worth
    preserving or erroring over."""
    cleaned = _JSON_FENCE_RE.sub("", text.strip())
    try:
        parsed, _ = json.JSONDecoder().raw_decode(cleaned)
    except json.JSONDecodeError as e:
        raise AiNarrativeError(
            f"AI narrative generation for section '{analysis_id}' returned malformed JSON "
            f"from {model}: {e}"
        ) from e
    if not isinstance(parsed, dict) or set(parsed) != set(sections_requested):
        raise AiNarrativeError(
            f"AI narrative generation for section '{analysis_id}' returned an unexpected "
            f"response shape from {model} (expected exactly the keys {sections_requested}, "
            f"got {list(parsed) if isinstance(parsed, dict) else type(parsed).__name__})."
        )
    fields: dict[str, str] = {}
    for field_name, value in parsed.items():
        if field_name == _KEY_FINDINGS_FIELD:
            if (
                not isinstance(value, list)
                or not (_MIN_KEY_FINDINGS <= len(value) <= _MAX_KEY_FINDINGS)
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                raise AiNarrativeError(
                    f"AI narrative generation for section '{analysis_id}' returned an invalid "
                    f"\"{_KEY_FINDINGS_FIELD}\" field from {model} (expected a JSON array of "
                    f"{_MIN_KEY_FINDINGS}-{_MAX_KEY_FINDINGS} non-empty strings)."
                )
            fields[field_name] = "\n".join(item.strip() for item in value)
        else:
            if not isinstance(value, str) or not value.strip():
                raise AiNarrativeError(
                    f"AI narrative generation for section '{analysis_id}' returned an empty or "
                    f"non-string \"{field_name}\" field from {model}."
                )
            fields[field_name] = value.strip()
    return fields


def generate_section_summary(
    analysis_id: str,
    name: str,
    category: str,
    stats: dict[str, Any],
    *,
    project_name: str | None = None,
    timeout_s: float = PER_SECTION_TIMEOUT_S,
) -> dict[str, str]:
    """One Gemini call for one section (the only backend - see module
    docstring's 2026-08-12 decision note). Returns the narrative dict for
    exactly `_applicable_narrative_fields(stats)` - see module docstring.
    Raises `AiNarrativeError` (never returns a partial dict) on timeout,
    transport/API error, malformed response, an empty/missing field, or a
    failed numeric-grounding check on ANY field.

    `stats` is pruned via `_prune_prose` ONCE, up front, and that SAME pruned
    dict feeds both the prompt and the grounding check below (R3) - never the
    raw `stats` for one and pruned for the other. Capability gating
    (`_applicable_narrative_fields`) runs on the ORIGINAL `stats`, not pruned -
    `_prune_prose` only strips free-text prose keys, never the structural
    keys (`series`/`class_area_ha_by_year`/`loss_area_ha_by_year`) capability
    detection depends on."""
    sections_requested = _applicable_narrative_fields(stats)
    pruned_stats = _prune_prose(stats)
    user_message = _build_user_message(
        analysis_id, name, category, pruned_stats, project_name, sections_requested
    )
    text = _call_gemini(GEMINI_MODEL, user_message, analysis_id, timeout_s)

    if not text:
        raise AiNarrativeError(
            f"AI narrative generation for section '{analysis_id}' returned an empty response."
        )

    fields = _parse_narrative_json(text, sections_requested, analysis_id, GEMINI_MODEL)

    ungrounded = verify_fields_grounding(
        fields,
        pruned_stats,
        analysis_id=analysis_id,
        analysis_name=name,
        category=category,
        project_name=project_name,
    )
    if ungrounded:
        log.warning(
            "ai_narrative.ungrounded_numbers", analysis_id=analysis_id, fields=ungrounded,
        )
        raise AiNarrativeError(
            f"AI narrative generation for section '{analysis_id}' produced number(s) not "
            f"present in its own input data ({ungrounded}); rejected rather than shipped."
        )
    return fields


def generate_ai_summaries(
    sections: list[tuple[str, str, str, dict[str, Any]]],
    *,
    project_name: str | None = None,
    total_budget_s: float = TOTAL_BUDGET_S,
) -> dict[str, dict[str, str]]:
    """`sections`: `(analysis_id, name, category, stats)` tuples - the same
    fields `generate_report_pdf_bytes` already has in hand per section
    (`analysis_id`, `entry["name"]`, `entry["category"]`, `row["stats"]`).

    All-or-nothing (see module docstring's FAILURE SEMANTICS): the first
    section to fail raises immediately, and no partial
    `{analysis_id: narrative_dict}` dict is ever returned to the caller - only
    a complete one, or an exception. `total_budget_s` bounds the whole batch;
    a section that would start past the deadline fails the same way a slow
    one would, naming itself and how many sections completed before it."""
    deadline = time.monotonic() + total_budget_s
    summaries: dict[str, dict[str, str]] = {}
    for analysis_id, name, category, stats in sections:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AiNarrativeError(
                f"AI narrative generation exceeded its total {total_budget_s:.0f}s budget "
                f"before section '{analysis_id}' could be attempted "
                f"({len(summaries)}/{len(sections)} sections completed)."
            )
        summaries[analysis_id] = generate_section_summary(
            analysis_id, name, category, stats,
            project_name=project_name, timeout_s=min(PER_SECTION_TIMEOUT_S, remaining),
        )
    return summaries
