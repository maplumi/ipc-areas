#!/usr/bin/env python3
"""Simplify TopoJSON datasets with optional precision reduction.

Reads a TopoJSON file, rounds geometry coordinates to a configurable precision,
optionally simplifies geometries, converts the result back to TopoJSON, and writes
an updated dataset alongside a size report. Helper functions can be imported by
other scripts (e.g., the combiner) to reuse the logic programmatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    import topojson as tp
except ImportError as exc:  # pragma: no cover - the script exits immediately
    raise SystemExit(
        "Missing dependency: install project requirements with 'pip install -r requirements.txt'."
    ) from exc

try:
    from shapely.geometry import shape
    from shapely.geometry.base import BaseGeometry
except ImportError:  # pragma: no cover - simplification is optional
    shape = None  # type: ignore[assignment]
    BaseGeometry = object  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_SOURCE_NAME = "ipc_global_areas.topojson"


def ensure_source(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Could not find {path}")


def load_global_features(source: Path) -> List[Dict[str, Any]]:
    with open(source, "r", encoding="utf-8") as handle:
        topo_payload = json.load(handle)

    topology = tp.Topology(topo_payload, topology=True, prequantize=False)
    geojson_payload = json.loads(topology.to_geojson())

    features = geojson_payload.get("features") if isinstance(geojson_payload, dict) else None
    if not isinstance(features, list):
        return []

    return [feature for feature in features if isinstance(feature, dict)]


def round_nested(value: Any, digits: int) -> Any:
    if isinstance(value, list):
        return [round_nested(item, digits) for item in value]
    if isinstance(value, float):
        return round(value, digits)
    return value


def simplify_geometry(geometry: Dict[str, Any], tolerance: float) -> Dict[str, Any]:
    if tolerance <= 0:
        return geometry

    if shape is None:
        print(
            "Warning: shapely is not installed, skipping simplification step.",
            file=sys.stderr,
        )
        return geometry

    try:
        geom_obj: BaseGeometry = shape(geometry)  # type: ignore[arg-type]
        simplified = geom_obj.simplify(tolerance, preserve_topology=True)
        if simplified.is_empty:
            return geometry
        return json.loads(json.dumps(simplified.__geo_interface__))
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: simplification failed ({exc}); geometry left unchanged.", file=sys.stderr)
        return geometry


def simplify_feature(feature: Dict[str, Any], digits: int, tolerance: float) -> Dict[str, Any]:
    feature_copy = json.loads(json.dumps(feature))  # deep copy to avoid mutating original
    geometry = feature_copy.get("geometry")
    if isinstance(geometry, dict):
        if tolerance > 0:
            geometry = simplify_geometry(geometry, tolerance)
            feature_copy["geometry"] = geometry
        if "coordinates" in geometry:
            geometry["coordinates"] = round_nested(geometry["coordinates"], digits)
    return feature_copy


def build_topology(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    feature_collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    topology = tp.Topology(feature_collection, prequantize=False)
    return topology.to_dict()


def write_output(target: Path, topology: Dict[str, Any]) -> None:
    target.parent.mkdir(exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(topology, handle, separators=(",", ":"))


def simplify_features(
    features: List[Dict[str, Any]],
    *,
    precision: int,
    simplify_tolerance: float,
) -> List[Dict[str, Any]]:
    return [simplify_feature(feature, precision, simplify_tolerance) for feature in features]


def simplify_topojson(
    source: Path,
    *,
    output: Path | None = None,
    precision: int = 4,
    simplify_tolerance: float = 0.0,
    quiet: bool = False,
) -> Dict[str, int | float]:
    ensure_source(source)

    features = load_global_features(source)
    if not features:
        raise ValueError("No features available to simplify")

    processed = simplify_features(
        features,
        precision=precision,
        simplify_tolerance=simplify_tolerance,
    )
    topology = build_topology(processed)

    target = output or source
    write_output(target, topology)

    original_size = source.stat().st_size
    new_size = target.stat().st_size
    saved = original_size - new_size
    ratio = (new_size / original_size) if original_size else 0.0

    stats = {
        "original_size": original_size,
        "new_size": new_size,
        "saved_bytes": saved,
        "size_ratio": ratio,
        "precision": precision,
        "simplify_tolerance": simplify_tolerance,
        "output_path": str(target),
    }

    if not quiet:
        print(
            f"Simplified dataset written to {target} with precision {precision} decimal places"
        )
        if simplify_tolerance > 0:
            print(f"Simplification tolerance applied: {simplify_tolerance}")
        print(
            f"Size reduced from {original_size:,} bytes to {new_size:,} bytes "
            f"({ratio:.2%} of original, saved {saved:,} bytes)"
        )

    return stats


def minify_topojson(
    source: Path,
    *,
    output: Path | None = None,
    precision: int = 4,
    simplify_tolerance: float = 0.0,
    quiet: bool = False,
) -> Dict[str, int | float]:
    """Backward compatible alias for the previous function name."""

    return simplify_topojson(
        source,
        output=output,
        precision=precision,
        simplify_tolerance=simplify_tolerance,
        quiet=quiet,
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--precision",
        type=int,
        default=4,
        help="Number of decimal places to retain in coordinates (default: 4)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for the simplified TopoJSON file (defaults to overwriting the input)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DATA_DIR / DEFAULT_SOURCE_NAME,
        help="Path to the source global TopoJSON file",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0,
        help="Simplification tolerance in coordinate units; set to 0 to disable",
    )
    args = parser.parse_args(argv)

    try:
        simplify_topojson(
            args.input,
            output=args.output,
            precision=args.precision,
            simplify_tolerance=args.simplify_tolerance,
            quiet=False,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
