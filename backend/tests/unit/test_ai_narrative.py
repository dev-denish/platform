"""Unit tests for app/services/ai_narrative.py. No real Gemini API call - the
`google.genai.Client` the module imports is monkeypatched to a fake object
(see `_install_fake_gemini`) that returns a canned/inspectable response
without ever making a network call or requiring a real API key. Gemini is
the ONLY narrative backend (decision 2026-08-12) - there is no
backend/model-selection surface left to test.

Highest-value target: `verify_numeric_grounding`, the piece that turns "ask
the model nicely" into an actual guarantee - a model that invents a number
must be caught, and a model that only reformats/rounds a real one must not
be punished for it.

Wave: 11-section report restructure - the model now returns a JSON object of
several named fields per section instead of one bare paragraph. Every fixture
below that exercises `generate_section_summary`/`generate_ai_summaries`
returns valid JSON with exactly the keys `_applicable_narrative_fields` would
request for that fixture's `stats` shape."""
from __future__ import annotations

import json

import pytest

from app.services import ai_narrative as AN

_STATS = {
    "coverage_pct": 98.567,
    "series": {"2024": 0.4, "2025": 0.5, "2026": 0.55},
    "class_area_ha": {"Tree cover": 42.0},
}

# Real hansen_gfc shape (gee_analysis_service.py:823-835) - the concrete
# motivating case for R3's `_prune_prose`: `note` mentions years (2000,
# 2012) that appear NOWHERE else in the dict as a numeric key or value
# (Hansen's own loss year here is 2010).
_HANSEN_STATS = {
    "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
    "gain_area_ha_2000_2012": 5.0, "loss_area_ha_by_year": {"2010": 2.0},
    "coverage_pct": 100.0,
    "note": "Gain is a whole-period 2000-2012 figure only.",
}


def test_gemini_model_constant_is_a_fixed_non_empty_string():
    """Gemini is the only backend (decision 2026-08-12) - `GEMINI_MODEL` is a
    plain constant, not derived from Settings/env - there is nothing left to
    select between."""
    assert isinstance(AN.GEMINI_MODEL, str)
    assert AN.GEMINI_MODEL


def test_grounding_accepts_exact_and_reformatted_numbers():
    text = "In 2026 the mean was 0.55. Coverage was 98.567%. Tree cover held 42.00 ha."
    assert AN.verify_numeric_grounding(text, _STATS) == []


def test_grounding_accepts_reasonable_rounding_of_a_real_number():
    # 98.567 rounded to 1dp/whole number - a model restating it more coarsely,
    # not inventing a new figure.
    text = "Coverage was about 98.6%, roughly 99% of the boundary."
    assert AN.verify_numeric_grounding(text, _STATS) == []


def test_grounding_rejects_a_number_with_no_source_value():
    text = "Tree cover grew by 17 hectares since last year."
    ungrounded = AN.verify_numeric_grounding(text, _STATS)
    assert 17.0 in ungrounded


def test_grounding_ignores_years_that_are_real_series_keys():
    text = "Between 2024 and 2026 the mean rose from 0.4 to 0.55."
    assert AN.verify_numeric_grounding(text, _STATS) == []


def test_grounding_still_rejects_an_invented_year():
    text = "By 2030 the trend is expected to continue."
    assert AN.verify_numeric_grounding(text, _STATS) == [2030.0]


# --------------------------------------------------------------------------
# Wave: 11-section report restructure - _applicable_narrative_fields and
# verify_fields_grounding.
# --------------------------------------------------------------------------


def test_applicable_narrative_fields_always_includes_the_3_base_fields():
    fields = AN._applicable_narrative_fields({"coverage_pct": 90.0})
    assert fields == ["executive_summary", "spatial_distribution", "key_findings"]


def test_applicable_narrative_fields_adds_temporal_analysis_when_supported():
    fields = AN._applicable_narrative_fields(_STATS)
    assert "temporal_analysis" in fields
    assert "change_analysis" not in fields


def test_applicable_narrative_fields_adds_change_analysis_when_supported():
    fields = AN._applicable_narrative_fields(_HANSEN_STATS)
    assert "change_analysis" in fields
    assert "temporal_analysis" not in fields


def test_verify_fields_grounding_returns_empty_dict_when_every_field_grounds():
    fields = {
        "executive_summary": "Coverage was 98.567%.",
        "spatial_distribution": "Tree cover held 42.00 ha.",
    }
    assert AN.verify_fields_grounding(fields, _STATS) == {}


def test_verify_fields_grounding_isolates_the_one_bad_field():
    fields = {
        "executive_summary": "Coverage was 98.567%.",
        "spatial_distribution": "Carbon stock is 5000 tCO2e.",
    }
    ungrounded = AN.verify_fields_grounding(fields, _STATS)
    assert set(ungrounded) == {"spatial_distribution"}
    assert 5000.0 in ungrounded["spatial_distribution"]


# --------------------------------------------------------------------------
# _build_user_message / _SYSTEM_PROMPT
# --------------------------------------------------------------------------


def test_build_user_message_includes_sections_requested():
    prompt = AN._build_user_message(
        "hansen_gfc", "Hansen", "Forest Change", AN._prune_prose(_HANSEN_STATS), None,
        ["executive_summary", "change_analysis"],
    )
    payload = json.loads(prompt)
    assert payload["sections_requested"] == ["executive_summary", "change_analysis"]


def test_system_prompt_rule_9_names_sections_requested_and_key_findings_array():
    assert "sections_requested" in AN._SYSTEM_PROMPT
    assert "key_findings" in AN._SYSTEM_PROMPT
    assert "JSON" in AN._SYSTEM_PROMPT


# --------------------------------------------------------------------------
# _parse_narrative_json
# --------------------------------------------------------------------------

_SECTIONS_3 = ["executive_summary", "spatial_distribution", "key_findings"]


def test_parse_narrative_json_joins_key_findings_bullets_with_newline():
    text = json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.",
        "key_findings": ["One.", "Two.", "Three."],
    })
    fields = AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")
    assert fields["key_findings"] == "One.\nTwo.\nThree."


def test_parse_narrative_json_rejects_malformed_json():
    with pytest.raises(AN.AiNarrativeError, match="malformed"):
        AN._parse_narrative_json("not json", _SECTIONS_3, "ndvi", "phi4-mini:3.8b")


def test_parse_narrative_json_rejects_a_missing_key():
    text = json.dumps({"executive_summary": "A.", "spatial_distribution": "B."})
    with pytest.raises(AN.AiNarrativeError, match="unexpected"):
        AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")


def test_parse_narrative_json_rejects_an_extra_key():
    text = json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.",
        "key_findings": ["One.", "Two.", "Three."], "change_analysis": "Not requested.",
    })
    with pytest.raises(AN.AiNarrativeError, match="unexpected"):
        AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")


def test_parse_narrative_json_rejects_key_findings_with_too_few_bullets():
    text = json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.", "key_findings": ["One."],
    })
    with pytest.raises(AN.AiNarrativeError, match="key_findings"):
        AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")


def test_parse_narrative_json_rejects_key_findings_as_a_plain_string():
    text = json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.", "key_findings": "One. Two.",
    })
    with pytest.raises(AN.AiNarrativeError, match="key_findings"):
        AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")


def test_parse_narrative_json_strips_a_markdown_fence_defensively():
    text = "```json\n" + json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.",
        "key_findings": ["One.", "Two.", "Three."],
    }) + "\n```"
    fields = AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")
    assert fields["executive_summary"] == "A."


def test_parse_narrative_json_ignores_a_trailing_closing_fence():
    """Real dmrv-qa failure (hansen_gfc, 2026-08-12): a real Gemini response
    put the closing ``` fence on its own line AFTER the JSON object - the old
    `json.loads` rejected the whole response as "Extra data", dead-lettering
    a report whose JSON was otherwise perfectly well-formed."""
    text = "```json\n" + json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.",
        "key_findings": ["One.", "Two.", "Three."],
    }) + "\n```\n"
    fields = AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "gemini-3.5-flash")
    assert fields["executive_summary"] == "A."


def test_parse_narrative_json_ignores_trailing_prose_after_the_object():
    """Same real failure mode, worse case: the model appended an explanatory
    sentence after the JSON object despite rule 9 - `raw_decode` stops at the
    end of the first complete JSON value and never even looks at this."""
    text = json.dumps({
        "executive_summary": "A.", "spatial_distribution": "B.",
        "key_findings": ["One.", "Two.", "Three."],
    }) + "\n\nNote: this narrative is descriptive only."
    fields = AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "gemini-3.5-flash")
    assert fields["executive_summary"] == "A."


def test_parse_narrative_json_rejects_an_empty_string_field():
    text = json.dumps({
        "executive_summary": "", "spatial_distribution": "B.",
        "key_findings": ["One.", "Two.", "Three."],
    })
    with pytest.raises(AN.AiNarrativeError, match="executive_summary"):
        AN._parse_narrative_json(text, _SECTIONS_3, "ndvi", "phi4-mini:3.8b")


# --------------------------------------------------------------------------
# End-to-end generate_section_summary / generate_ai_summaries
# --------------------------------------------------------------------------


class _FakeGeminiResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _install_fake_gemini(monkeypatch, generate_content_fn) -> None:
    """Bypasses the real `google.genai.Client` entirely - `generate_content_fn`
    receives the prompt string (`contents`, the exact JSON `_build_user_message`
    built) and either returns the response text or raises, exactly mirroring
    what a real SDK call would do on success/failure. No network, no API key
    needed (also stubs `_resolve_gemini_api_key`)."""
    monkeypatch.setattr(AN, "_resolve_gemini_api_key", lambda: "test-key")

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            return _FakeGeminiResponse(generate_content_fn(contents))

    class _FakeClient:
        def __init__(self, *a, **k) -> None:
            self.models = _FakeModels()

    monkeypatch.setattr(AN.genai, "Client", _FakeClient)


def _json_response_text(**fields) -> str:
    return json.dumps(fields)


def test_generate_section_summary_returns_a_narrative_dict_on_a_grounded_response(monkeypatch):
    _install_fake_gemini(monkeypatch, lambda contents: _json_response_text(
        executive_summary="Coverage was 98.567%.",
        spatial_distribution="Tree cover held 42.00 ha.",
        key_findings=[
            "Coverage was 98.567%.", "Tree cover held 42.00 ha.",
            "The mean rose from 0.40 to 0.55.",
        ],
        temporal_analysis="Between 2024 and 2026 the mean rose from 0.40 to 0.55.",
    ))
    narrative = AN.generate_section_summary("dynamic_world", "Dynamic World", "Land Cover", _STATS)
    assert narrative["executive_summary"] == "Coverage was 98.567%."
    assert narrative["spatial_distribution"] == "Tree cover held 42.00 ha."
    assert narrative["temporal_analysis"] == "Between 2024 and 2026 the mean rose from 0.40 to 0.55."
    assert narrative["key_findings"] == (
        "Coverage was 98.567%.\nTree cover held 42.00 ha.\nThe mean rose from 0.40 to 0.55."
    )


def test_generate_section_summary_rejects_a_response_with_one_ungrounded_field(monkeypatch):
    _install_fake_gemini(monkeypatch, lambda contents: _json_response_text(
        executive_summary="Coverage was 98.567%.",
        spatial_distribution="Carbon stock is 5000 tCO2e.",
        key_findings=["Coverage was 98.567%.", "Tree cover held 42.00 ha.", "No other findings."],
        temporal_analysis="Between 2024 and 2026 the mean rose from 0.40 to 0.55.",
    ))
    with pytest.raises(AN.AiNarrativeError, match="dynamic_world"):
        AN.generate_section_summary("dynamic_world", "Dynamic World", "Land Cover", _STATS)


def test_generate_section_summary_raises_cleanly_on_a_gemini_call_failure(monkeypatch):
    def _raise(contents):
        raise TimeoutError("read timed out")

    _install_fake_gemini(monkeypatch, _raise)
    with pytest.raises(AN.AiNarrativeError, match="timed out"):
        AN.generate_section_summary("hansen_gfc", "Hansen", "Forest Change", _STATS)


def test_generate_ai_summaries_is_all_or_nothing_on_a_mid_batch_failure(monkeypatch):
    """Second section's response has one ungrounded field - the whole call
    must raise, and never hand back a dict with just the first section's
    narrative in it (see module docstring's FAILURE SEMANTICS)."""
    responses = iter(
        [
            _json_response_text(
                executive_summary="Coverage was 98.567%.",
                spatial_distribution="Tree cover held 42.00 ha.",
                key_findings=[
                    "Coverage was 98.567%.", "Tree cover held 42.00 ha.",
                    "The mean rose from 0.40 to 0.55.",
                ],
                temporal_analysis="Between 2024 and 2026 the mean rose from 0.40 to 0.55.",
            ),
            _json_response_text(
                executive_summary="Coverage was 98.567%.",
                spatial_distribution="Tree cover held 42.00 ha.",
                key_findings=["Coverage was 98.567%.", "Carbon removed: 9999 tCO2e.", "No other findings."],
                temporal_analysis="Between 2024 and 2026 the mean rose from 0.40 to 0.55.",
            ),
        ]
    )
    _install_fake_gemini(monkeypatch, lambda contents: next(responses))

    sections = [
        ("dynamic_world", "Dynamic World", "Land Cover", _STATS),
        ("hansen_gfc", "Hansen", "Forest Change", _STATS),
    ]
    with pytest.raises(AN.AiNarrativeError, match="hansen_gfc"):
        AN.generate_ai_summaries(sections)


def test_generate_ai_summaries_fails_when_total_budget_is_already_spent(monkeypatch):
    _install_fake_gemini(monkeypatch, lambda contents: _json_response_text(
        executive_summary="Coverage was 98.567%.", spatial_distribution="x.",
        key_findings=["a.", "b.", "c."],
    ))
    sections = [("dynamic_world", "Dynamic World", "Land Cover", _STATS)]
    with pytest.raises(AN.AiNarrativeError, match="budget"):
        AN.generate_ai_summaries(sections, total_budget_s=0.0)


# --------------------------------------------------------------------------
# R3: _prune_prose strips note/summary/methodology before either the prompt
# or the grounding check sees them.
# --------------------------------------------------------------------------


def test_prune_prose_strips_note_summary_methodology_but_keeps_numeric_fields():
    stats = {
        **_HANSEN_STATS,
        "summary": "2026: NDVI averages 0.55 - moderate. Descriptive only.",
        "methodology": {"dataset": "Esri", "years_computed": [2023], "resolution_m": 10},
    }
    pruned = AN._prune_prose(stats)

    assert "note" not in pruned
    assert "summary" not in pruned
    assert "methodology" not in pruned
    # Every numeric/structural field survives untouched.
    for key in (
        "canopy_cover_threshold_pct", "baseline_forest_area_ha",
        "gain_area_ha_2000_2012", "loss_area_ha_by_year", "coverage_pct",
    ):
        assert pruned[key] == stats[key]
    assert set(pruned) == set(stats) - {"note", "summary", "methodology"}


def test_prune_prose_is_a_no_op_when_no_prose_keys_are_present():
    stats = {"class_area_ha": {"Trees": 20.0}, "coverage_pct": 90.0}
    assert AN._prune_prose(stats) == stats


def test_pruned_hansen_dict_allows_2000_and_2012_via_the_key_name_not_the_note():
    """R6: 2000/2012 ARE legitimately grounded data - they're the real bounds
    of `gain_area_ha_2000_2012`'s key name, not something only the (pruned)
    `note` ever mentioned. Pruning's job is removing the note's STIMULUS that
    got an earlier model to restate those years unprompted (see the
    end-to-end test below) - it never needed to, and never should, remove
    2000/2012 from what's actually traceable in the data itself."""
    pruned = AN._prune_prose(_HANSEN_STATS)
    allowed: set[float] = set()
    AN._flatten_numbers(pruned, allowed)
    assert 2000.0 in allowed
    assert 2012.0 in allowed
    assert 2010.0 in allowed  # the real loss year, still legitimately citable


def test_grounding_accepts_gain_area_ha_2000_2012_key_years_in_output_text():
    """R6 regression: real dead-lettered-job shape. A model restating
    hansen_gfc's real `gain_area_ha_2000_2012` key as a written year range
    must not be rejected - previously `2000.0`/`-2012.0` both failed
    grounding (missing allow-set entry, and a sign-flipped candidate)."""
    text = "Forest gain from 2000 to 2012 totaled 5.00 hectares."
    assert AN.verify_numeric_grounding(text, _HANSEN_STATS) == []

    text_hyphenated = "Over the 2000-2012 period, gain totaled 5.00 hectares."
    assert AN.verify_numeric_grounding(text_hyphenated, _HANSEN_STATS) == []


def test_extract_candidate_numbers_splits_a_hyphenated_year_range_as_positive():
    assert AN._extract_candidate_numbers("the 2000-2012 period") == [2000.0, 2012.0]


def test_extract_candidate_numbers_still_reads_a_genuine_negative_value():
    """The year-range fix must not touch an ordinary negative number - no
    4-digit run on both sides of the hyphen here."""
    assert AN._extract_candidate_numbers("NDVI fell to -0.681.") == [-0.681]
    assert AN._extract_candidate_numbers("dropped to -12.3%.") == [-12.3]


def test_grounding_still_rejects_an_invented_year_range():
    """A year-range-SHAPED number that isn't the real one must still fail -
    the fix accepts the real 2000-2012 range, not any 4-digit-4-digit shape."""
    text = "Gain occurred over the 1990-1995 period."
    ungrounded = AN.verify_numeric_grounding(text, _HANSEN_STATS)
    assert 1990.0 in ungrounded
    assert 1995.0 in ungrounded


def test_build_user_message_no_longer_contains_the_note_text_once_pruned():
    sections_requested = AN._applicable_narrative_fields(_HANSEN_STATS)
    prompt = AN._build_user_message(
        "hansen_gfc", "Hansen", "Forest Change", AN._prune_prose(_HANSEN_STATS), None,
        sections_requested,
    )
    assert "whole-period" not in prompt
    assert "2000-2012" not in prompt
    # The real numeric fields are still there for the model to describe.
    assert "gain_area_ha_2000_2012" in prompt
    assert "loss_area_ha_by_year" in prompt


def test_generate_section_summary_prunes_before_both_prompt_and_grounding(monkeypatch):
    """generate_section_summary must not send raw `stats` (with `note`
    intact) to Gemini - inspects the actual prompt content, not just the
    final result/exception outcome."""
    captured = {}

    def _capture(contents):
        captured["prompt_data"] = json.loads(contents)["data"]
        return _json_response_text(
            executive_summary="Total gain recorded was 5.00 hectares.",
            spatial_distribution="Baseline forest area is 100.00 ha.",
            key_findings=[
                "Total gain recorded was 5.00 hectares.",
                "Baseline forest area is 100.00 ha.", "Loss recorded was 2.00 ha in 2010.",
            ],
            change_analysis="Loss recorded was 2.00 ha in 2010.",
        )

    _install_fake_gemini(monkeypatch, _capture)
    narrative = AN.generate_section_summary("hansen_gfc", "Hansen", "Forest Change", _HANSEN_STATS)
    assert narrative["executive_summary"] == "Total gain recorded was 5.00 hectares."
    assert "note" not in captured["prompt_data"]


# Isolates R3's mechanism (note-pruning) from R6's mechanism (embedded-year
# keys - see `_KEY_EMBEDDED_YEAR_RE`): no `_2000_2012`-shaped key here, so
# 2000/2012 have exactly one possible source in this dict - the `note` text -
# same as the real pre-R6 hansen_gfc shape would have looked with the gain
# figure keyed as plain `gain_area_ha` rather than `gain_area_ha_2000_2012`.
_STATS_YEARS_ONLY_IN_NOTE = {
    "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
    "gain_area_ha": 5.0, "loss_area_ha_by_year": {"2010": 2.0},
    "coverage_pct": 100.0,
    "note": "Gain is a whole-period 2000-2012 figure only.",
}


def _model_that_parrots_the_note(contents: str) -> str:
    """Stand-in for a real model: if it can see a "note" field in the
    prompt's "data", it restates the note's years verbatim - exactly the
    behaviour that produced the Hansen false-rejection bug (see module
    docstring's FAILURE SEMANTICS / `_prune_prose`'s own docstring)."""
    data = json.loads(contents)["data"]
    if "note" in data:
        gain_text = "Gain occurred over the whole period from 2000 to 2012, totaling 5.00 hectares."
    else:
        gain_text = "Total gain recorded was 5.00 hectares."
    return _json_response_text(
        executive_summary=gain_text,
        spatial_distribution="Baseline forest area is 100.00 ha.",
        key_findings=[gain_text, "Baseline forest area is 100.00 ha.", "Loss recorded was 2.00 ha in 2010."],
        change_analysis="Loss recorded was 2.00 ha in 2010.",
    )


def test_hansen_note_years_previously_caused_a_false_rejection_now_fixed(monkeypatch):
    """BEFORE (simulated directly, bypassing the fix): a model given the raw,
    unpruned Hansen `note` restates its years, and those years are ungrounded
    everywhere else in the dict - a false rejection. Uses
    `_STATS_YEARS_ONLY_IN_NOTE` so the only source of 2000/2012 is the note
    text itself, not `gain_area_ha_2000_2012`'s embedded-year key (R6's
    separate fix - see `test_grounding_accepts_gain_area_ha_2000_2012_key_years_in_output_text`
    for that one).
    AFTER (via the real `generate_section_summary`, which prunes
    internally): the same fake model never sees `note`, so it never mentions
    those years, and the call succeeds."""
    sections_requested = AN._applicable_narrative_fields(_STATS_YEARS_ONLY_IN_NOTE)

    # BEFORE: what the old (unpruned) code path would have produced.
    unpruned_prompt = AN._build_user_message(
        "hansen_gfc", "Hansen", "Forest Change", _STATS_YEARS_ONLY_IN_NOTE, None,
        sections_requested,
    )
    before_text = _model_that_parrots_the_note(unpruned_prompt)
    before_ungrounded = AN.verify_numeric_grounding(before_text, _STATS_YEARS_ONLY_IN_NOTE)
    assert 2000.0 in before_ungrounded
    assert 2012.0 in before_ungrounded

    # AFTER: the real code path, same fake "model", same raw stats argument
    # (exactly what report_service.py passes in) - no rejection.
    _install_fake_gemini(monkeypatch, _model_that_parrots_the_note)
    narrative = AN.generate_section_summary(
        "hansen_gfc", "Hansen", "Forest Change", _STATS_YEARS_ONLY_IN_NOTE
    )
    assert narrative["executive_summary"] == "Total gain recorded was 5.00 hectares."
    for text in narrative.values():
        assert "2000" not in text
        assert "2012" not in text


# --------------------------------------------------------------------------
# R4: expanded banned-claims list in the system prompt.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "permanence", "reversal-risk", "leakage claim", "uncertainty or confidence",
        "baseline", "business as usual", "infer biomass or carbon stock",
        "causation", "compliance or verification framing",
        "unit implied by each field's own name",
        "carbon or biomass unit",
    ],
)
def test_system_prompt_covers_the_expanded_banned_claims_list(phrase):
    assert phrase in AN._SYSTEM_PROMPT


# --------------------------------------------------------------------------
# R5: spelled-out numbers, fabricated analogies, false-absence claims - the
# real esa_worldcover QA-pass bug (see module docstring's R5 note).
# --------------------------------------------------------------------------

# Real esa_worldcover class breakdown from the QA pass (ha).
_ESA_WORLDCOVER_STATS = {
    "class_area_ha": {
        "Built-up": 528.56, "Cropland": 7518.70, "Grassland": 6453.93,
        "Mangroves": 0.00, "Shrubland": 1893.46, "Tree cover": 7604.25,
        "Snow and ice": 0.00, "Moss and lichen": 0.00,
        "Herbaceous wetland": 0.00, "Permanent water bodies": 77.71,
        "Bare/sparse vegetation": 17.68,
    },
}

# The real bad AI summary text from the QA pass, verbatim (see module
# docstring's R5 note) - contains all three failure modes at once.
_REAL_BAD_ESA_WORLDCOVER_SUMMARY = (
    "cropland took most with over seven thousand square kilometers or more "
    "than seventy-one million acres, while grassland and shrublands were "
    "also significant. Tree cover dominated at almost eight hundred "
    "thousand square kilometers which is roughly eighty-five times the "
    "size of California's Yosemite National Park area in hectares (ha)... "
    "similarly, mosses, lichens, herbaceous wetlands or permanent water "
    "bodies had no coverage reported here."
)


def test_grounding_rejects_the_real_esa_worldcover_bad_summary():
    """Before R5 this passed `verify_numeric_grounding` and shipped in a
    real PDF - none of "seven thousand"/"seventy-one million"/"eighty-five"
    are digit tokens, so the old digit-only check never saw them."""
    ungrounded = AN.verify_numeric_grounding(
        _REAL_BAD_ESA_WORLDCOVER_SUMMARY, _ESA_WORLDCOVER_STATS
    )
    assert ungrounded  # non-empty: rejected


@pytest.mark.parametrize(
    "phrase",
    ["seven thousand", "seventy-one million", "eighty-five", "eight hundred"],
)
def test_grounding_flags_each_spelled_out_number_word_individually(phrase):
    ungrounded = AN.verify_numeric_grounding(phrase, _ESA_WORLDCOVER_STATS)
    assert phrase in ungrounded


def test_grounding_still_accepts_legitimate_digit_numbers_for_the_same_stats():
    """Sanity check: the R5 tripwire must not make the check overly strict -
    real digit-based restatements of the real esa_worldcover figures still
    pass, same as before."""
    text = "Cropland covered 7,518.70 ha and tree cover covered 7,604.25 ha."
    assert AN.verify_numeric_grounding(text, _ESA_WORLDCOVER_STATS) == []


def test_grounding_accepts_a_real_percent_alongside_the_esa_worldcover_stats():
    stats = {**_ESA_WORLDCOVER_STATS, "coverage_pct": 99.7}
    assert AN.verify_numeric_grounding("Coverage was 99.7% of the boundary.", stats) == []


# --------------------------------------------------------------------------
# R7: bare years inside the dataset's own observed year range are grounded
# even when that exact year is absent from the record - real hansen_gfc
# manual-review case (see module docstring's R7 note).
# --------------------------------------------------------------------------

# Real hansen_gfc shape from the manual review: only these years have a
# `loss_area_ha_by_year` entry; `gain_area_ha_2000_2012` embeds 2000/2012.
_HANSEN_STATS_SPARSE_YEARS = {
    "canopy_cover_threshold_pct": 15.0,
    "baseline_forest_area_ha": 100.0,
    "gain_area_ha_2000_2012": 5.0,
    "loss_area_ha_by_year": {
        "2003": 1.0, "2004": 1.0, "2005": 1.0, "2006": 1.0, "2007": 1.0,
        "2008": 1.0, "2011": 1.0, "2012": 1.0, "2017": 1.0, "2018": 1.0,
        "2023": 1.0, "2024": 1.0, "2025": 1.0,
    },
    "coverage_pct": 100.0,
}

# The real generated sentence, verbatim (see module docstring's R7 note).
_REAL_HANSEN_ABSENT_YEARS_SENTENCE = (
    "The years 2001, 2002, 2009, 2010, 2013, 2014, 2015, 2016, 2019, 2020, "
    "2021, and 2022 are not listed in the loss figures."
)


def test_grounding_accepts_the_real_hansen_absent_years_sentence():
    """R7 regression: correctly describing which years a year-indexed
    dataset does NOT cover must not be flagged as fabrication - the real
    dead-lettered-job sentence, verbatim."""
    assert (
        AN.verify_numeric_grounding(
            _REAL_HANSEN_ABSENT_YEARS_SENTENCE, _HANSEN_STATS_SPARSE_YEARS
        )
        == []
    )


def test_grounding_still_rejects_a_year_outside_the_observed_range():
    """The R7 leniency is scoped to the dataset's own [min, max] year range
    (2000-2025 for `_HANSEN_STATS_SPARSE_YEARS`) - a year outside it, e.g. a
    fabricated "1995", must still fail."""
    text = "Loss was first recorded in 1995, well before the dataset begins."
    ungrounded = AN.verify_numeric_grounding(text, _HANSEN_STATS_SPARSE_YEARS)
    assert 1995.0 in ungrounded


def test_grounding_still_rejects_a_fabricated_statistic_attached_to_an_in_range_year():
    """R7 only excuses the bare YEAR NUMBER, not a statistic attached to it -
    a model inventing an area figure for a real, in-range year must still be
    caught."""
    text = "In 2015, loss reached 999.9 hectares."
    ungrounded = AN.verify_numeric_grounding(text, _HANSEN_STATS_SPARSE_YEARS)
    assert 999.9 in ungrounded


# --------------------------------------------------------------------------
# R8: the model's own given identifiers (project_name/analysis_id/
# analysis_name/category) are legitimate to echo back, not fabrication - real
# "AI Narrative QA 1786455528" manual-review case (see module docstring's R8
# note).
# --------------------------------------------------------------------------


def test_grounding_accepts_digits_from_the_real_project_name():
    text = "This analysis of AI Narrative QA 1786455528 covered 99.7% of the boundary."
    stats = {"coverage_pct": 99.7}
    assert (
        AN.verify_numeric_grounding(
            text, stats, project_name="AI Narrative QA 1786455528"
        )
        == []
    )


def test_grounding_still_rejects_a_fabricated_number_absent_from_stats_and_identifiers():
    text = "Carbon stock reached 4242 tCO2e."
    stats = {"coverage_pct": 99.7}
    ungrounded = AN.verify_numeric_grounding(
        text,
        stats,
        analysis_id="hansen_gfc",
        analysis_name="Hansen Global Forest Change",
        category="Forest Change",
        project_name="AI Narrative QA 1786455528",
    )
    assert 4242.0 in ungrounded


def test_generate_section_summary_accepts_a_response_that_echoes_the_project_name(
    monkeypatch,
):
    """End-to-end regression for R8 via the real call site -
    `generate_section_summary` must pass `project_name` through to
    `verify_numeric_grounding`."""
    _install_fake_gemini(monkeypatch, lambda contents: _json_response_text(
        executive_summary=(
            "This analysis of AI Narrative QA 1786455528 covered 98.567% of the boundary."
        ),
        spatial_distribution="Tree cover held 42.00 ha.",
        key_findings=[
            "Coverage was 98.567%.", "Tree cover held 42.00 ha.",
            "The mean rose from 0.40 to 0.55.",
        ],
        temporal_analysis="Between 2024 and 2026 the mean rose from 0.40 to 0.55.",
    ))
    narrative = AN.generate_section_summary(
        "dynamic_world",
        "Dynamic World",
        "Land Cover",
        _STATS,
        project_name="AI Narrative QA 1786455528",
    )
    assert "1786455528" in narrative["executive_summary"]
