"""Unit tests for the pagination envelope's next_offset math."""
from __future__ import annotations

from app.domain.dtos import Page, ProjectSummary


def _page(total, limit, offset):
    return Page[ProjectSummary](items=[], total=total, limit=limit, offset=offset)


def test_next_offset_when_more_pages():
    assert _page(total=100, limit=25, offset=0).next_offset == 25
    assert _page(total=100, limit=25, offset=50).next_offset == 75


def test_next_offset_none_on_last_page():
    assert _page(total=100, limit=25, offset=75).next_offset is None
    assert _page(total=10, limit=25, offset=0).next_offset is None


def test_next_offset_exact_boundary():
    assert _page(total=50, limit=25, offset=25).next_offset is None
