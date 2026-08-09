"""Renders a vegetation index's year-series trend as a static PNG for the PDF
report - the same `stats["series"]` data IndexTrendChart (AnalysisPanel.jsx)
draws interactively with Recharts, just rasterized server-side since a PDF
page has no JS runtime to render an interactive chart into.

matplotlib's Agg backend (set before any other matplotlib import, module
load time) needs no display/X server - safe in a headless container."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402 - must follow matplotlib.use("Agg")


def render_trend_chart_png(analysis_name: str, series: dict[str, float | None]) -> bytes:
    """`series` is `{year_str: mean_or_None}`, unmodified from stats["series"] -
    a year with no usable pixels (None) is plotted as a gap, never interpolated
    or dropped from the x-axis, so a missing year is visible as missing, not
    silently smoothed over."""
    years = sorted(series, key=int)
    values = [series[y] for y in years]

    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=150)
    plotted_x = [int(y) for y, v in zip(years, values, strict=True) if v is not None]
    plotted_y = [v for v in values if v is not None]
    ax.plot(plotted_x, plotted_y, marker="o", color="#0B6B46", linewidth=2)
    ax.set_title(f"{analysis_name} - year-series trend", fontsize=11)
    ax.set_xlabel("Year", fontsize=9)
    ax.set_ylabel(analysis_name, fontsize=9)
    ax.set_xticks([int(y) for y in years])
    ax.tick_params(axis="both", labelsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
