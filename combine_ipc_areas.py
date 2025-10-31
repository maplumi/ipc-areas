#!/usr/bin/env python3
"""Combine all IPC area TopoJSON datasets into a single global TopoJSON file.

This utility walks the ``data`` directory, converts each country-level TopoJSON to
GeoJSON features, deduplicates them by ISO3 + title (falling back to geometry hash),
and writes a merged TopoJSON collection to ``data/ipc_global_areas.topojson``.
"""

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

DATA_DIR = Path("data")
OUTPUT_FILENAME = "ipc_global_areas.topojson"
GLOBAL_PATH = DATA_DIR / OUTPUT_FILENAME


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


def discover_topojson_files() -> List[Path]:
    """Return all TopoJSON files under data/, excluding the global output itself."""
    if not DATA_DIR.exists():
        raise FileNotFoundError("data directory not found; run download_ipc_areas.py first")

    files: List[Path] = []
    for path in DATA_DIR.rglob("*.topojson"):
        if path.name == OUTPUT_FILENAME:
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def save_topology(features: List[Dict[str, Any]]) -> None:
    """Persist the combined features back to TopoJSON."""
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    topology = tp.Topology(collection, prequantize=False)

    DATA_DIR.mkdir(exist_ok=True)
    with open(GLOBAL_PATH, "w", encoding="utf-8") as handle:
        json.dump(topology.to_dict(), handle, separators=(",", ":"))

    print(f"Wrote {len(features)} features to {GLOBAL_PATH}")


def main() -> int:
    try:
        topo_files = discover_topojson_files()
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

    save_topology(features)
    return 0


if __name__ == "__main__":
    sys.exit(main())
