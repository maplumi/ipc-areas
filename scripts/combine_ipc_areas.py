#!/usr/bin/env python3
"""Combine all IPC area TopoJSON datasets into a single, simplified global file.

This utility walks the ``data`` directory, converts each country-level TopoJSON to
GeoJSON features, deduplicates them by ISO3 + title (falling back to geometry hash),
stores an aggregated TopoJSON file, and optionally simplifies the result using shared
geometry utilities (rounding coordinates and simplifying shapes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import topojson as tp
except ImportError as exc:  # pragma: no cover - immediate exit path
    raise SystemExit(
        "Missing dependency: install requirements with 'pip install -r requirements.txt'."
    ) from exc

try:
    from .simplify_ipc_global_areas import simplify_topojson
except ImportError:  # pragma: no cover - fallback for direct script execution
    from simplify_ipc_global_areas import simplify_topojson

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_OUTPUT_FILENAME = "ipc_global_areas.topojson"


def normalize_title(title: Optional[str]) -> str:
    """Normalize titles for consistent deduplication."""
    if not title:
        return ""
    return " ".join(title.split()).strip().lower()


def feature_key(feature: Dict[str, Any]) -> str:
    """Derive a stable deduplication key for a feature."""
    props = feature.get("properties") or {}
    iso3 = (props.get("iso3") or "").strip().lower()
    title = normalize_title(props.get("title"))
    if iso3 and title:
        return f"{iso3}::{title}"

    geometry = feature.get("geometry")
    if geometry:
        geometry_json = json.dumps(geometry, sort_keys=True)
        digest = hashlib.sha1(geometry_json.encode("utf-8")).hexdigest()
        return f"geometry::{digest}"

    fallback = json.dumps(feature, sort_keys=True)
    digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()
    return f"feature::{digest}"


def load_features_from_topojson(path: Path) -> List[Dict[str, Any]]:
    """Convert a TopoJSON file into a list of GeoJSON features."""
    with open(path, "r", encoding="utf-8") as handle:
        topo_payload = json.load(handle)

    topology = tp.Topology(topo_payload, topology=True, prequantize=False)
    geojson_payload = json.loads(topology.to_geojson())

    features = geojson_payload.get("features") if isinstance(geojson_payload, dict) else None
    if not isinstance(features, list):
        return []

    # Defensive copy to ensure downstream modifications don't affect shared instances.
    return [feature for feature in features if isinstance(feature, dict)]


def collect_all_features(files: Iterable[Path]) -> List[Dict[str, Any]]:
    """Aggregate features from multiple TopoJSON files, deduplicated by key."""
    aggregate: Dict[str, Dict[str, Any]] = {}

    for filepath in files:
        try:
            features = load_features_from_topojson(filepath)
        except Exception as exc:  # noqa: BLE001 - surface path-specific failures
            print(f"Warning: failed to read {filepath}: {exc}", file=sys.stderr)
            continue

        for feature in features:
            key = feature_key(feature)
            if key not in aggregate:
                aggregate[key] = feature

    sorted_items = sorted(aggregate.items(), key=lambda item: item[0])
    return [item[1] for item in sorted_items]


def discover_topojson_files(skip_path: Path) -> List[Path]:
    """Return all TopoJSON files under data/, excluding the target output file."""
    if not DATA_DIR.exists():
        raise FileNotFoundError("data directory not found; run scripts/download_ipc_areas.py first")

    skip_resolved = skip_path.resolve()
    files: List[Path] = []
    for path in DATA_DIR.rglob("*.topojson"):
        if path.resolve() == skip_resolved:
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def save_topology(features: List[Dict[str, Any]], output_path: Path) -> None:
    """Persist the combined features back to TopoJSON."""
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    topology = tp.Topology(collection, prequantize=False)

    output_path.parent.mkdir(exist_ok=True, parents=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(topology.to_dict(), handle, separators=(",", ":"))

    try:
        display_path = output_path.relative_to(REPO_ROOT)
    except ValueError:
        display_path = output_path

    print(f"Wrote {len(features)} features to {display_path}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for the aggregated TopoJSON output (default: data/ipc_global_areas.topojson)",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=4,
        help="Decimal precision for coordinate rounding during minification (default: 4)",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0,
        help="Simplification tolerance applied after combination; set to 0 to disable",
    )
    parser.add_argument(
        "--skip-simplify",
        "--skip-minify",
        dest="skip_simplify",
        action="store_true",
        help="Skip the simplification pass if you plan to process the output separately",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    output_path = args.output or (DATA_DIR / DEFAULT_OUTPUT_FILENAME)
    if not output_path.is_absolute():
        output_path = (REPO_ROOT / output_path).resolve()

    try:
        topo_files = discover_topojson_files(output_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not topo_files:
        print("No TopoJSON files found under data/.", file=sys.stderr)
        return 1

    features = collect_all_features(topo_files)
    if not features:
        print("No features extracted; aborting.", file=sys.stderr)
        return 1

    save_topology(features, output_path)

    if not args.skip_simplify:
        stats = simplify_topojson(
            output_path,
            precision=args.precision,
            simplify_tolerance=args.simplify_tolerance,
            quiet=True,
        )
        ratio = stats.get("size_ratio", 0.0)
        saved = stats.get("saved_bytes", 0)
        print(
            f"Simplified global dataset with precision {args.precision} and tolerance "
            f"{args.simplify_tolerance}; saved {saved:,} bytes ({ratio:.2%} of original)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
