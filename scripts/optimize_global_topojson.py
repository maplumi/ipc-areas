#!/usr/bin/env python3
"""One-off helper to shrink and validate the global IPC TopoJSON dataset.

The existing downloader already writes a `global_areas.topojson` file with
rounded coordinates. This utility provides a slightly more aggressive
post-processing pass without modifying the downloader itself. It:

* loads the global TopoJSON dataset
* validates that geometry ids remain unique within each country
* applies additional rounding/simplification using the shared helper

Usage example:

    python scripts/optimize_global_topojson.py --precision 3 --simplify-tolerance 0.0005

By default the output is written to ``data/global_areas_optimized_plus.topojson`` so
the original artefact is kept for comparison.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

try:  # allow execution via `python scripts/optimize_global_topojson.py`
    from .simplify_ipc_global_areas import simplify_topojson
except ImportError:  # pragma: no cover - fallback when not running as package
    from simplify_ipc_global_areas import simplify_topojson

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "global_areas.topojson"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "global_areas_optimized_plus.topojson"


def load_geometries(topo_path: Path) -> List[Dict]:
    with topo_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    objects = payload.get("objects")
    if not isinstance(objects, dict) or not objects:
        raise ValueError("No TopoJSON objects found in dataset")

    first_object = next(iter(objects.values()))
    geometries = first_object.get("geometries") if isinstance(first_object, dict) else None
    if not isinstance(geometries, list):
        raise ValueError("No geometries found in TopoJSON object")

    return geometries


def find_duplicate_ids(geometries: Iterable[Dict]) -> Tuple[List[str], Dict[str, List[str]]]:
    """Return global duplicate ids and duplicates grouped by ISO3 if any."""

    global_ids = Counter()
    per_country: Dict[str, Counter] = defaultdict(Counter)

    for geom in geometries:
        gid = geom.get("id")
        props = geom.get("properties") or {}
        iso3 = props.get("iso3", "UNK") or "UNK"

        if gid is None:
            # Missing ids technically break uniqueness; treat as duplicate markers.
            per_country[iso3]["<missing>"] += 1
            global_ids["<missing>"] += 1
            continue

        gid_str = str(gid)
        global_ids[gid_str] += 1
        per_country[iso3][gid_str] += 1

    global_duplicates = [gid for gid, count in global_ids.items() if count > 1]
    country_duplicates: Dict[str, List[str]] = {}

    for iso3, counter in per_country.items():
        dupes = [gid for gid, count in counter.items() if count > 1]
        if dupes:
            country_duplicates[iso3] = dupes

    return global_duplicates, country_duplicates


def format_dupe_report(dupes: Dict[str, List[str]]) -> str:
    segments = [f"{iso3}: {', '.join(ids)}" for iso3, ids in sorted(dupes.items())]
    return "; ".join(segments)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to the source TopoJSON file (default: {DEFAULT_INPUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    help="Where to write the optimized file (default: data/global_areas_optimized_plus.topojson)",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=3,
        help="Decimal places to retain when rounding coordinates (default: 3)",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0005,
        help="Douglas-Peucker tolerance used during simplification (default: 0.0005)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file instead of writing to a separate output",
    )
    args = parser.parse_args(argv)

    input_path = args.input if args.input.is_absolute() else (REPO_ROOT / args.input)
    output_path = input_path if args.in_place else (args.output if args.output.is_absolute() else REPO_ROOT / args.output)

    if not input_path.exists():
        parser.error(f"Input file not found: {input_path}")

    print(f"Validating ids in {input_path.relative_to(REPO_ROOT)} …")
    geometries = load_geometries(input_path)
    global_dupes, per_country_dupes = find_duplicate_ids(geometries)

    if global_dupes:
        print(f"⚠️  Found {len(global_dupes)} duplicate ids globally")
    else:
        print("✅ All geometry ids are globally unique")

    if per_country_dupes:
        print("⚠️  Duplicates detected within the following countries:")
        print(format_dupe_report(per_country_dupes))
    else:
        print("✅ No per-country duplicate ids detected")

    print(
        "Running additional simplification with precision="
        f"{args.precision}, tolerance={args.simplify_tolerance} …"
    )

    stats = simplify_topojson(
        input_path,
        output=output_path,
        precision=args.precision,
        simplify_tolerance=args.simplify_tolerance,
        quiet=True,
    )

    print(
        "Size reduced from "
        f"{stats['original_size']:,} bytes to {stats['new_size']:,} bytes "
        f"(saved {stats['saved_bytes']:,} bytes; {stats['size_ratio']:.2%} of original)."
    )
    print(f"Optimized file written to {Path(stats['output_path']).resolve()}")

    print("Re-validating ids on the optimized dataset …")
    new_geometries = load_geometries(output_path)
    _, new_country_dupes = find_duplicate_ids(new_geometries)
    if new_country_dupes:
        print("⚠️  Duplicate ids emerged after optimization! Check the output carefully:")
        print(format_dupe_report(new_country_dupes))
        return 1

    print("✅ Optimized dataset retains per-country unique ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
