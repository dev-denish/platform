"""
Bulk-load India Administrative Boundaries (Block/Village) reference layers.

Why this exists as a standalone script rather than going through
IngestionService/POST /datasets/upload: that path holds an entire upload's
parsed features in memory as Python dicts and writes them with a single
executemany (see ingestion/vector.py's module docstring and
VectorFeatureRepository.insert_many) - correct for "one project's worth of
plot boundaries" (tens to low thousands of features), wrong for Block
(~7,100 features nationwide) and badly wrong for Village (hundreds of
thousands per state, ~537K nationwide once fully rolled out - see the
sourcing research this wave is built from). This script streams
pre-normalized CSVs through VectorFeatureRepository's COPY-based staging
path instead (stage_bulk_features / commit_staged_features).

The CSVs this reads are NOT raw source files - they're produced on the host
by scripts/duckdb_extract_admin_boundaries (duckdb against the india-geodata
LGD_Blocks/LGD_Villages parquet releases + the LGD ground-truth registry
CSVs), each already exactly two columns: geom_geojson, properties_json.
Normalizing property keys (state_lgd_code, district_lgd_code,
block_lgd_code, village_lgd_code - never the source's own column spelling
like dist_lgd/dtcode11/Dist_LGD) happens THERE, once, so every consumer of
vector_feature.properties in this codebase (the district-scope filter, the
village-coverage join) can rely on one stable key set regardless of which
upstream file it came from.

Idempotent per --source-tag: re-running with the same tag deletes the prior
dataset (cascades through spatial_layer -> vector_feature) before
re-inserting, rather than accumulating duplicates - this is a re-run of a
sourcing pipeline, not a user upload with a fixed geometry-vs-project
identity to preserve.

Usage (inside the backend container, where DMRV_DB_* is already set):
    docker compose exec backend python -m scripts.ingest_admin_boundaries \\
        blocks /tmp/admin_boundaries/blocks.csv \\
        --display-name "India - Block Boundaries (LGD)" \\
        --source-tag admin-boundaries-block-lgd \\
        --simplify-tolerance 0.0003

    docker compose exec backend python -m scripts.ingest_admin_boundaries \\
        villages /tmp/admin_boundaries/villages_karnataka.csv \\
        --display-name "India - Village Boundaries (LGD, Karnataka pilot)" \\
        --source-tag admin-boundaries-village-lgd-karnataka

    docker compose exec backend python -m scripts.ingest_admin_boundaries \\
        registry /tmp/admin_boundaries/district_registry.csv \\
        /tmp/admin_boundaries/village_registry.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import sys
import uuid

from app.core.config import get_settings
from app.core.db import Database
from app.repositories.admin_boundaries import AdminBoundaryRegistryRepository
from app.repositories.datasets import DatasetRepository, LayerRepository
from app.repositories.projects import ProjectRepository
from app.repositories.vector_layers import VectorFeatureRepository
from app.services.project_access import (
    REFERENCE_LIBRARY_PROJECT_NAME,
    REFERENCE_LIBRARY_PROJECT_REGION,
)

csv.field_size_limit(sys.maxsize)  # a village/block polygon's GeoJSON can be large


def _resolve_library_project_id(cur) -> uuid.UUID:
    # Same shared project every reference layer attaches to (see
    # resolve_reference_library_project) - find_or_create_by_name directly,
    # skipping that function's actor-authz re-check, which this script (no
    # HTTP actor, no request) doesn't have and doesn't need.
    project_id, _created = ProjectRepository(cur).find_or_create_by_name(
        REFERENCE_LIBRARY_PROJECT_NAME, REFERENCE_LIBRARY_PROJECT_REGION
    )
    return project_id


def _delete_prior(cur, source_tag: str) -> None:
    # ON DELETE CASCADE (spatial_layer.dataset_id, vector_feature.layer_id -
    # see 0010_multi_format_layers) takes the layer/features with it.
    cur.execute("DELETE FROM dataset WHERE source = %s", (source_tag,))


def _row_iter(csv_path: str):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row["geom_geojson"], row["properties_json"]


def ingest_boundary_layer(
    *, kind: str, csv_path: str, display_name: str, source_tag: str,
    requires_district_scope: bool,
    simplify_tolerance: float | None,
) -> None:
    settings = get_settings()
    db = Database(settings)
    db.connect()
    try:
        with db.transaction() as cur:
            _delete_prior(cur, source_tag)
            project_id = _resolve_library_project_id(cur)

            vector_repo = VectorFeatureRepository(cur)
            # stage_key must be unique per run (not yet a layer_id - the
            # layer doesn't exist until bounds are known, see
            # stage_bulk_features' docstring).
            stage_key = uuid.uuid4().hex
            count, bounds = vector_repo.stage_bulk_features(stage_key, _row_iter(csv_path))
            if count == 0:
                raise SystemExit(f"{csv_path}: no rows - refusing to create an empty layer.")
            if bounds is None:
                raise SystemExit(f"{csv_path}: could not compute bounds - check geom_geojson.")

            batch_id = uuid.uuid4()
            dataset_id = DatasetRepository(cur).insert(
                project_id=project_id, dataset_type="Boundary", source=source_tag,
                accuracy_score=None, date_processed=datetime.date.today().isoformat(),
                batch_id=batch_id, is_reference=True, is_adhoc=False,
            )
            cur.execute(
                "UPDATE dataset SET display_name = %s WHERE dataset_id = %s",
                (display_name, dataset_id),
            )
            layer_id = LayerRepository(cur).insert_non_raster(
                dataset_id=dataset_id, layer_kind="vector", crs="EPSG:4326",
                bounds=bounds, requires_district_scope=requires_district_scope,
            )
            inserted = vector_repo.commit_staged_features(stage_key, layer_id, simplify_tolerance)
        print(
            f"OK: {kind} layer_id={layer_id} dataset_id={dataset_id} features={inserted} "
            f"simplify_tolerance={simplify_tolerance}"
        )
    finally:
        db.close()


def ingest_registries(*, district_csv: str, village_csv: str) -> None:
    settings = get_settings()
    db = Database(settings)
    db.connect()
    try:
        with db.transaction() as cur:
            repo = AdminBoundaryRegistryRepository(cur)

            def district_rows():
                with open(district_csv, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        yield (
                            int(row["district_lgd_code"]), row["district_name"],
                            int(row["state_lgd_code"]), row["state_name"],
                        )

            def village_rows():
                with open(village_csv, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        yield (
                            int(row["village_lgd_code"]), row["village_name"],
                            int(row["block_lgd_code"]) if row["block_lgd_code"] else None,
                            row["block_name"] or None,
                            int(row["district_lgd_code"]), row["district_name"],
                            int(row["state_lgd_code"]), row["state_name"],
                        )

            n_districts = repo.load_district_registry(district_rows())
            n_villages = repo.load_village_registry(village_rows())
        print(f"OK: district_registry={n_districts} village_registry={n_villages}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    for kind in ("blocks", "villages"):
        p = sub.add_parser(kind)
        p.add_argument("csv_path")
        p.add_argument("--display-name", required=True)
        p.add_argument("--source-tag", required=True)
        p.add_argument(
            "--simplify-tolerance", type=float, default=None,
            help=(
                "Degrees (geometry is EPSG:4326) - applies ST_SimplifyPreserveTopology "
                "at ingestion. Omit for no simplification. See "
                "VectorFeatureRepository.commit_staged_features' docstring for why this "
                "exists: LGD_Blocks' source geometry averaged 1,485 vertices/polygon "
                "(some over 40,000) - serving that whole-layer OOM-killed the backend. "
                "0.0003 (~30m) is a reasonable starting point for Block; villages are "
                "already far lighter (avg 69 vertices) and may not need it at all."
            ),
        )

    p = sub.add_parser("registry")
    p.add_argument("district_csv")
    p.add_argument("village_csv")

    args = parser.parse_args()
    if args.command in ("blocks", "villages"):
        ingest_boundary_layer(
            kind=args.command, csv_path=args.csv_path, display_name=args.display_name,
            source_tag=args.source_tag, requires_district_scope=(args.command == "villages"),
            simplify_tolerance=args.simplify_tolerance,
        )
    else:
        ingest_registries(district_csv=args.district_csv, village_csv=args.village_csv)


if __name__ == "__main__":
    main()
