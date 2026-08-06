"""
Class-code -> (name, color) legends for the land-cover analyses in
app/services/gee_analysis_service.py. Each dataset defines its own
classification scheme - these are NOT interchangeable (see that module's
docstring): a "built" class in Dynamic World and a "Built-up" class in ESA
WorldCover come from independently-trained classifiers and will disagree at
the pixel level, so the UI must always show which product a number came
from, never merge two products' class breakdowns into one number.

Colors are each dataset's own official published palette (Dynamic World /
ESA WorldCover / Esri 10m LULC / MODIS MCD12Q1 LC_Type1 IGBP), not invented.
"""
from __future__ import annotations

# GOOGLE/DYNAMICWORLD/V1, band "label", codes 0-8.
DYNAMIC_WORLD_LEGEND: dict[int, tuple[str, str]] = {
    0: ("water", "#419BDF"),
    1: ("trees", "#397D49"),
    2: ("grass", "#88B053"),
    3: ("flooded_vegetation", "#7A87C6"),
    4: ("crops", "#E49635"),
    5: ("shrub_and_scrub", "#DFC35A"),
    6: ("built", "#C4281B"),
    7: ("bare", "#A59B8F"),
    8: ("snow_and_ice", "#B39FE1"),
}

# ESA/WorldCover/v200, band "Map".
ESA_WORLDCOVER_LEGEND: dict[int, tuple[str, str]] = {
    10: ("Tree cover", "#006400"),
    20: ("Shrubland", "#FFBB22"),
    30: ("Grassland", "#FFFF4C"),
    40: ("Cropland", "#F096FF"),
    50: ("Built-up", "#FA0000"),
    60: ("Bare / sparse vegetation", "#B4B4B4"),
    70: ("Snow and ice", "#F0F0F0"),
    80: ("Permanent water bodies", "#0064C8"),
    90: ("Herbaceous wetland", "#0096A0"),
    95: ("Mangroves", "#00CF75"),
    100: ("Moss and lichen", "#FAE6A0"),
}

# projects/sat-io/open-datasets/landcover/ESRI_Global-LULC_10m_TS, band "b1".
ESRI_LULC_LEGEND: dict[int, tuple[str, str]] = {
    1: ("Water", "#1A5BAB"),
    2: ("Trees", "#358221"),
    4: ("Flooded vegetation", "#87D19E"),
    5: ("Crops", "#FFDB5C"),
    7: ("Built area", "#ED022A"),
    8: ("Bare ground", "#EDE9E4"),
    9: ("Snow and ice", "#F2FAFF"),
    10: ("Clouds", "#C8C8C8"),
    11: ("Rangeland", "#C6AD8D"),
}

# MODIS/061/MCD12Q1, band "LC_Type1" (IGBP classification), codes 1-17.
MODIS_IGBP_LEGEND: dict[int, tuple[str, str]] = {
    1: ("Evergreen Needleleaf Forest", "#05450a"),
    2: ("Evergreen Broadleaf Forest", "#086a10"),
    3: ("Deciduous Needleleaf Forest", "#54a708"),
    4: ("Deciduous Broadleaf Forest", "#78d203"),
    5: ("Mixed Forest", "#009900"),
    6: ("Closed Shrublands", "#c6b044"),
    7: ("Open Shrublands", "#dcd159"),
    8: ("Woody Savannas", "#dade48"),
    9: ("Savannas", "#fbff13"),
    10: ("Grasslands", "#b6ff05"),
    11: ("Permanent Wetlands", "#27ff87"),
    12: ("Croplands", "#c24f44"),
    13: ("Urban and Built-up", "#a5a5a5"),
    14: ("Cropland/Natural Vegetation Mosaic", "#ff6d4c"),
    15: ("Snow and Ice", "#69fff8"),
    16: ("Barren", "#f9ffa4"),
    17: ("Water Bodies", "#1c0dff"),
}


def legend_entries(legend: dict[int, tuple[str, str]]) -> list[dict]:
    return [{"code": code, "name": name, "color": color} for code, (name, color) in legend.items()]
