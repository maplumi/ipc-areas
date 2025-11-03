#!/usr/bin/env python3
"""
IPC Areas GeoJSON Download and Organization Script

This script downloads IPC area data for countries from the IPC API,
converts them to TopoJSON format, stores a per-year snapshot for each
assessment year, writes a deduplicated country-wide combined file, and
builds a global aggregate across all countries.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import sys
import csv
import json
import requests
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import topojson as tp

try:
    from .simplify_ipc_global_areas import simplify_topojson
except ImportError:  # pragma: no cover - script executed directly
    from simplify_ipc_global_areas import simplify_topojson

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
COUNTRIES_CSV = REPO_ROOT / "countries.csv"
COUNTRY_FILENAME_SUFFIX = "_areas.topojson"
COUNTRY_COMBINED_SUFFIX = "_combined_areas.topojson"
GLOBAL_FILENAME = "global_areas.topojson"
GLOBAL_OUTPUT_PATH = DATA_DIR / GLOBAL_FILENAME
GLOBAL_INFO = {"name": "Global", "iso2": "GL", "iso3": "GLB"}

# Configuration
API_BASE_URL = "https://api.ipcinfo.org/areas"
YEARS_TO_TRY = list(range(2025, 2019, -1))


def normalize_years(years: Optional[List[int]]) -> List[int]:
    """Return a sanitized list of assessment years, preserving order."""
    if years is None or not years:
        return list(YEARS_TO_TRY)

    seen = set()
    normalized: List[int] = []
    for year in years:
        value = int(year)
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)

    if not normalized:
        raise ValueError("At least one valid assessment year must be provided")

    return normalized


def resolve_release_tag() -> str:
    """Determine the CDN release tag.

    Priority order:
      1. CDN_RELEASE_TAG environment variable
      2. Current Git tag (git describe --tags --abbrev=0)
      3. Current Git branch
      4. Short commit hash
      5. Fallback to 'main'
    """

    env_tag = os.getenv("CDN_RELEASE_TAG")
    if env_tag:
        return env_tag

    git_cmds = [
        ["git", "describe", "--tags", "--abbrev=0"],
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        ["git", "rev-parse", "--short", "HEAD"],
    ]

    for cmd in git_cmds:
        try:
            result = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, cwd=REPO_ROOT)
            tag = result.decode().strip()
            if tag and tag != "HEAD":
                return tag
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    return "main"


CDN_RELEASE_TAG = resolve_release_tag()


def resolve_ipc_key() -> Optional[str]:
    """Resolve IPC API key from environment or user environment variables."""
    key = os.getenv("IPC_KEY")
    if key:
        return key

    if os.name == "nt":
        try:
            import winreg  # lazy import to keep non-Windows platforms clean

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg_key:
                value, _ = winreg.QueryValueEx(reg_key, "IPC_KEY")
                if value:
                    return value
        except FileNotFoundError:
            # No user environment variables defined
            pass
        except OSError as exc:
            print(f"Warning: unable to read user environment variables: {exc}")

    return None

class IPCAreaDownloader:
    def __init__(
        self,
        years_to_try: Optional[List[int]] = None,
        *,
        precision: int = 4,
        simplify_tolerance: float = 0.0,
    ):
        self.ipc_key = resolve_ipc_key()
        if not self.ipc_key:
            raise ValueError("IPC_KEY environment variable is required")
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'IPC-Areas-Downloader/1.0'
        })

        # Create data directory
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(exist_ok=True)
        self.index_entries: List[Dict[str, Any]] = []
        self.cdn_release_tag = CDN_RELEASE_TAG
        self.years_to_try = normalize_years(years_to_try)
        self.precision = int(precision)
        self.simplify_tolerance = float(simplify_tolerance)
        self.country_combined_files: List[Path] = []

        if not self.years_to_try:
            raise ValueError("At least one assessment year must be configured")
        if self.precision < 0:
            raise ValueError("Precision must be non-negative")
        if self.simplify_tolerance < 0:
            raise ValueError("Simplification tolerance must be non-negative")

    @staticmethod
    def normalize_title(title: Optional[str]) -> str:
        if not title:
            return ""
        return " ".join(title.split()).strip().lower()

    @staticmethod
    def feature_key(feature: Dict[str, Any]) -> str:
        props = feature.get('properties') or {}
        area_id = props.get('id')
        if area_id is not None:
            iso3 = (props.get('iso3') or '').strip().lower()
            id_value = str(area_id).strip()
            return f"id::{iso3}::{id_value}"

        title_key = IPCAreaDownloader.normalize_title(props.get('title'))
        if title_key:
            return f"title::{title_key}"

        geometry = feature.get('geometry')
        if geometry:
            geometry_str = json.dumps(geometry, sort_keys=True)
            digest = hashlib.sha1(geometry_str.encode('utf-8')).hexdigest()
            return f"geometry::{digest}"

        fallback_str = json.dumps(feature, sort_keys=True)
        digest = hashlib.sha1(fallback_str.encode('utf-8')).hexdigest()
        return f"feature::{digest}"

    def load_countries(self) -> Dict[str, Dict]:
        """Load country data from CSV file."""
        countries = {}
        
        try:
            with open(COUNTRIES_CSV, 'r', encoding='utf-8-sig', newline='') as file:
                reader = csv.DictReader(file)
                if reader.fieldnames:
                    reader.fieldnames = [field.strip() for field in reader.fieldnames]

                for row in reader:
                    if not row:
                        continue

                    alpha_2 = (row.get('Alpha_2_Code') or '').strip()
                    alpha_3 = (row.get('Alpha_3_Code') or '').strip()
                    name = (row.get('English_Short_Name') or '').strip()

                    if not alpha_2 or not alpha_3:
                        print("    Skipping row with missing ISO codes")
                        continue

                    countries[alpha_2] = {
                        'name': name or alpha_2,
                        'iso2': alpha_2,
                        'iso3': alpha_3
                    }
        except FileNotFoundError:
            print("Error: countries.csv file not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading countries.csv: {e}")
            sys.exit(1)
            
        return countries

    def load_existing_features(self, filepath: Path) -> List[Dict[str, Any]]:
        try:
            with open(filepath, 'r', encoding='utf-8') as fh:
                topo_payload = json.load(fh)
            topology = tp.Topology(topo_payload, topology=True, prequantize=False)
            geojson_payload = json.loads(topology.to_geojson())
            features = geojson_payload.get('features', []) if isinstance(geojson_payload, dict) else []
            return [feature for feature in features if isinstance(feature, dict)]
        except Exception as exc:
            print(f"    Warning: unable to read existing dataset {filepath}: {exc}")
            return []

    @staticmethod
    def extract_year_from_path(filepath: Path, iso3: str) -> Optional[int]:
        name = filepath.name
        if not name.startswith(f"{iso3}_") or not name.endswith(COUNTRY_FILENAME_SUFFIX):
            return None

        core = name[len(iso3) + 1 : -len(COUNTRY_FILENAME_SUFFIX)]
        try:
            return int(core)
        except ValueError:
            return None

    def merge_features(self,
                       aggregate: Dict[str, Dict[str, Any]],
                       features: List[Dict[str, Any]],
                       *,
                       priority: int,
                       source_year: Optional[int],
                       source_label: str) -> Dict[str, int]:
        stats = {"added": 0, "updated": 0, "skipped": 0}

        for feature in features:
            feature_copy = copy.deepcopy(feature)
            props = feature_copy.get('properties') or {}
            key = self.feature_key(feature_copy)
            candidate = {
                "feature": feature_copy,
                "priority": priority,
                "source_year": props.get('year') if props.get('year') is not None else source_year,
                "source_label": source_label,
                "title": props.get('title')
            }

            existing = aggregate.get(key)
            if existing is None:
                aggregate[key] = candidate
                stats["added"] += 1
                continue

            replace = False
            if priority > existing.get('priority', -1):
                replace = True
            elif priority == existing.get('priority', -1):
                candidate_year = candidate.get('source_year') or 0
                existing_year = existing.get('source_year') or 0
                if candidate_year >= existing_year:
                    replace = True

            if replace:
                aggregate[key] = candidate
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

        return stats
    
    def download_areas(self, country_code: str, year: int) -> Optional[Dict[str, Any]]:
        """Download IPC areas data for a specific country and year."""
        params = {
            'format': 'geojson',
            'country': country_code,
            'year': year,
            'type': 'A',
            'key': self.ipc_key
        }
        
        try:
            print(f"  Downloading data for {country_code} - {year}...")
            response = self.session.get(API_BASE_URL, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if (
                    data
                    and isinstance(data, dict)
                    and isinstance(data.get('features'), list)
                    and data['features']
                ):
                    return data
                print(f"    No data available for {country_code} in {year}")
                return None
            else:
                print(f"    HTTP {response.status_code} for {country_code} - {year}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"    Request failed for {country_code} - {year}: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"    Invalid JSON response for {country_code} - {year}: {e}")
            return None
    
    def filter_and_process_areas(self, areas_data: Dict[str, Any], country_info: Dict[str, str], year: int) -> Optional[Dict[str, Any]]:
        """Filter and process areas data to retain only required fields."""
        features = []
        seen_geometries = set()
        
        for feature in areas_data.get('features', []):
            try:
                geometry = feature.get('geometry')
                if not geometry:
                    continue

                geometry_type = geometry.get('type')
                if geometry_type not in {'Polygon', 'MultiPolygon'}:
                    continue

                coordinates = geometry.get('coordinates')
                if not coordinates:
                    continue
                
                geometry_str = json.dumps(geometry, sort_keys=True)
                
                if geometry_str in seen_geometries:
                    continue
                
                seen_geometries.add(geometry_str)
                
                source_props = feature.get('properties') or {}
                properties = {
                    'title': source_props.get('title') or '',
                    'country': source_props.get('country') or country_info['iso2'],
                    'iso3': country_info['iso3'],
                    'year': source_props.get('year') or year
                }

                if source_props.get('id') is not None:
                    properties['id'] = source_props['id']
                
                features.append({
                    'type': 'Feature',
                    'geometry': geometry,
                    'properties': properties
                })
                
            except Exception as e:
                print(f"    Error processing area: {e}")
                continue
        
        if not features:
            return None
            
        geojson = {
            'type': 'FeatureCollection',
            'features': features
        }
        
        return geojson
    
    def convert_to_topojson(self, geojson: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convert GeoJSON to TopoJSON format."""
        try:
            # Use topojson library to convert
            topology = tp.Topology(geojson, prequantize=False)
            return topology.to_dict()
        except Exception as e:
            print(f"    Error converting to TopoJSON: {e}")
            return None
    
    @staticmethod
    def save_topojson(topojson_data: Dict[str, Any], filepath: Path) -> Optional[Path]:
        """Save TopoJSON data to the requested location."""
        filepath.parent.mkdir(exist_ok=True, parents=True)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(topojson_data, f, separators=(',', ':'))

            print(f"    Saved: {filepath}")
            return filepath
        except Exception as e:
            print(f"    Error saving {filepath}: {e}")
            return None

    def simplify_output(self, topo_path: Path) -> None:
        """Apply rounding/simplification to a TopoJSON file."""
        try:
            simplify_topojson(
                topo_path,
                precision=self.precision,
                simplify_tolerance=self.simplify_tolerance,
                quiet=True,
            )
        except Exception as exc:  # noqa: BLE001 - log and continue
            print(f"    Warning: simplification skipped for {topo_path}: {exc}")

    def add_index_entry(
        self,
        country_info: Dict[str, str],
        year: Optional[int],
        filepath: Path,
        feature_count: Optional[int],
        updated_at: Optional[str] = None,
        *,
        variant: str = "year"
    ) -> None:
        """Add an entry to the index for future discovery."""
        try:
            relative_path = filepath.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            relative_path = filepath.as_posix()
        file_name = filepath.name
        cdn_url = (
            f"https://cdn.jsdelivr.net/gh/maplumi/ipc-areas@{self.cdn_release_tag}/"
            f"{relative_path}"
        )

        if feature_count is None:
            feature_count = self.infer_feature_count(filepath)

        if updated_at is None:
            updated_at = datetime.utcnow().isoformat(timespec='seconds') + 'Z'

        entry = {
            "country": country_info.get('name', country_info['iso2']),
            "iso2": country_info['iso2'],
            "iso3": country_info['iso3'],
            "year": year,
            "relative_path": relative_path,
            "file_name": file_name,
            "feature_count": feature_count,
            "cdn_url": cdn_url,
            "updated_at": updated_at,
            "variant": variant
        }

        self.index_entries.append(entry)

    @staticmethod
    def infer_feature_count(filepath: Path) -> Optional[int]:
        """Infer feature count from an existing TopoJSON payload."""
        try:
            with open(filepath, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            objects = data.get('objects') if isinstance(data, dict) else None
            if not isinstance(objects, dict) or not objects:
                return None

            first_object = next(iter(objects.values()), None)
            geometries = first_object.get('geometries') if isinstance(first_object, dict) else None
            if isinstance(geometries, list):
                return len(geometries)
        except Exception:
            return None

        return None
    
    def process_country(self, country_code: str, country_info: Dict[str, str]) -> bool:
        """Process a single country - download, store per-year, and build combined dataset."""
        print(f"\nProcessing {country_info['name']} ({country_code})...")

        iso3 = country_info['iso3']
        country_dir = self.data_dir / iso3
        country_dir.mkdir(exist_ok=True)

        legacy_combined = country_dir / f"{iso3}{COUNTRY_FILENAME_SUFFIX}"
        modern_combined = country_dir / f"{iso3}{COUNTRY_COMBINED_SUFFIX}"
        if legacy_combined.exists() and not modern_combined.exists():
            try:
                legacy_combined.rename(modern_combined)
                print(
                    f"    Renamed legacy combined dataset {legacy_combined.name} "
                    f"-> {modern_combined.name}"
                )
            except OSError as exc:  # noqa: BLE001
                print(
                    f"    Warning: unable to rename legacy combined dataset {legacy_combined}: {exc}"
                )
        elif legacy_combined.exists() and modern_combined.exists():
            try:
                legacy_combined.unlink()
                print(f"    Removed legacy dataset {legacy_combined.name}")
            except OSError as exc:  # noqa: BLE001
                print(f"    Warning: unable to remove legacy dataset {legacy_combined}: {exc}")

        aggregated: Dict[str, Dict[str, Any]] = {}
        year_feature_counts: Dict[int, Dict[str, Any]] = {}
        combined_path = modern_combined

        if combined_path.exists():
            legacy_features = self.load_existing_features(combined_path)
            if legacy_features:
                stats = self.merge_features(
                    aggregated,
                    legacy_features,
                    priority=-5,
                    source_year=None,
                    source_label="legacy_combined",
                )
                if stats["added"] or stats["updated"]:
                    print(
                        f"    Legacy combined dataset provided {stats['added']} "
                        f"baseline and {stats['updated']} refreshed feature(s)"
                    )

        # Seed aggregate with any existing per-year datasets
        existing_year_files = sorted(country_dir.glob(f"{iso3}_*{COUNTRY_FILENAME_SUFFIX}"))
        for path in existing_year_files:
            year = self.extract_year_from_path(path, iso3)
            if year is None:
                continue

            features = self.load_existing_features(path)
            if features:
                stats = self.merge_features(
                    aggregated,
                    features,
                    priority=0,
                    source_year=year,
                    source_label=f"existing:{year}"
                )
                if stats["added"] or stats["updated"]:
                    print(
                        f"    Existing year {year} dataset contributed "
                        f"{stats['added']} new and {stats['updated']} updated features"
                    )
                year_feature_counts[year] = {
                    "path": path,
                    "feature_count": len(features)
                }

        # Download requested assessment years
        for year in self.years_to_try:
            areas_data = self.download_areas(country_code, year)

            if not areas_data:
                time.sleep(0.5)
                continue

            geojson = self.filter_and_process_areas(areas_data, country_info, year)
            if not geojson or not geojson['features']:
                print(f"    No valid polygon features found for year {year}")
                time.sleep(0.5)
                continue

            topojson_data = self.convert_to_topojson(geojson)
            if not topojson_data:
                print(f"    Failed to convert downloaded features for year {year}")
                time.sleep(0.5)
                continue

            year_path = country_dir / f"{iso3}_{year}{COUNTRY_FILENAME_SUFFIX}"
            saved_year_path = self.save_topojson(topojson_data, year_path)
            if saved_year_path:
                year_feature_counts[year] = {
                    "path": saved_year_path,
                    "feature_count": len(geojson['features'])
                }

            stats = self.merge_features(
                aggregated,
                geojson['features'],
                priority=10,
                source_year=year,
                source_label=f"download:{year}"
            )
            print(
                f"    Year {year}: {len(geojson['features'])} features retrieved "
                f"({stats['added']} new, {stats['updated']} updated)"
            )

            time.sleep(0.5)

        if not aggregated:
            print(f"    No data found for {country_info['name']} in any year")
            return False

        years_seen = [
            entry.get('source_year')
            for entry in aggregated.values()
            if entry.get('source_year') is not None
        ]
        representative_year = max(years_seen) if years_seen else None

        sorted_entries = sorted(aggregated.items(), key=lambda item: item[0])
        final_features = [entry['feature'] for _, entry in sorted_entries]
        final_geojson = {
            'type': 'FeatureCollection',
            'features': final_features
        }

        topojson_data = self.convert_to_topojson(final_geojson)
        if not topojson_data:
            print(f"    Failed to convert merged features to TopoJSON for {country_info['name']}")
            return False

        saved_combined = self.save_topojson(topojson_data, combined_path)
        if not saved_combined:
            print(f"    Failed to save merged dataset for {country_info['name']}")
            return False

        self.simplify_output(saved_combined)
        self.country_combined_files.append(saved_combined)

        feature_count = len(final_features)
        available_years = sorted(year_feature_counts.keys())
        if not available_years and years_seen:
            available_years = sorted({year for year in years_seen if isinstance(year, int)})

        for year in available_years:
            stats = year_feature_counts[year]
            self.add_index_entry(
                country_info,
                year,
                stats['path'],
                stats.get('feature_count'),
                variant="year"
            )

        self.add_index_entry(
            country_info,
            representative_year,
            saved_combined,
            feature_count,
            variant="combined"
        )

        print(
            f"    Combined dataset saved with {feature_count} features "
            f"across {len(available_years)} assessment year(s)"
        )

        return True

    def build_global_dataset(self) -> None:
        """Combine all country-level combined files into a global dataset."""
        print("\nBuilding global dataset...")

        combined_files = list(self.country_combined_files)
        if not combined_files:
            for country_dir in sorted(DATA_DIR.iterdir()):
                if not country_dir.is_dir():
                    continue
                iso3 = country_dir.name
                candidate = country_dir / f"{iso3}{COUNTRY_COMBINED_SUFFIX}"
                if candidate.exists():
                    combined_files.append(candidate)

        if not combined_files:
            print("  Warning: no combined country datasets found – global file not updated")
            return

        aggregated: Dict[str, Dict[str, Any]] = {}

        for path in sorted(combined_files):
            features = self.load_existing_features(path)
            if not features:
                continue

            self.merge_features(
                aggregated,
                features,
                priority=0,
                source_year=None,
                source_label=path.name,
            )

        if not aggregated:
            print("  Warning: no features discovered while building the global dataset")
            return

        sorted_entries = sorted(aggregated.items(), key=lambda item: item[0])
        final_features = [entry['feature'] for _, entry in sorted_entries]
        final_geojson = {
            'type': 'FeatureCollection',
            'features': final_features,
        }

        topojson_data = self.convert_to_topojson(final_geojson)
        if not topojson_data:
            print("  Warning: failed to convert combined global features to TopoJSON")
            return

        saved_global = self.save_topojson(topojson_data, GLOBAL_OUTPUT_PATH)
        if not saved_global:
            print("  Warning: unable to save global dataset")
            return

        self.simplify_output(saved_global)

        years_seen = [
            entry.get('source_year')
            for entry in aggregated.values()
            if entry.get('source_year') is not None
        ]
        representative_year = max(years_seen) if years_seen else None

        self.add_index_entry(
            GLOBAL_INFO,
            representative_year,
            saved_global,
            len(final_features),
            variant="global",
        )

        legacy_path = DATA_DIR / "ipc_global_areas.topojson"
        if legacy_path.exists() and legacy_path != saved_global:
            try:
                legacy_path.unlink()
                print(f"  Removed legacy global dataset {legacy_path}")
            except OSError as exc:  # noqa: BLE001
                print(f"  Warning: unable to remove legacy global dataset {legacy_path}: {exc}")

        print(f"  Global dataset saved to {saved_global} with {len(final_features)} features")
    def write_index_file(self) -> None:
        """Write or update the TopoJSON index file."""
        index_path = self.data_dir / "index.json"

        index_payload = {
            "generated_at": datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            "cdn_release_tag": self.cdn_release_tag,
            "total_files": len(self.index_entries),
            "items": sorted(
                self.index_entries,
                key=lambda entry: (
                    entry.get('iso3', ''),
                    entry.get('variant', ''),
                    entry.get('year') if isinstance(entry.get('year'), int) else -1,
                    entry.get('file_name', ''),
                )
            )
        }

        try:
            with open(index_path, 'w', encoding='utf-8') as fh:
                json.dump(index_payload, fh, indent=2)
            print(f"Index updated: {index_path}")
        except Exception as exc:
            print(f"Error writing index file: {exc}")
    
    def run(self):
        """Main execution method."""
        print("IPC Areas Download Script")
        print("=" * 50)
        
        # Load countries data
        print("Loading countries data...")
        countries = self.load_countries()
        print(f"Loaded {len(countries)} countries")
        print(
            "Assessment years: "
            + ", ".join(str(year) for year in self.years_to_try)
        )
        
        # Process each country
        successful = 0
        failed = 0
        
        for country_code, country_info in countries.items():
            try:
                if self.process_country(country_code, country_info):
                    successful += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"Error processing {country_info['name']}: {e}")
                failed += 1
            
            # Rate limiting
            time.sleep(1)

        self.build_global_dataset()
        self.write_index_file()

        print(f"\n" + "=" * 50)
        print(f"Processing complete!")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")
        print(f"Data saved in: {self.data_dir.absolute()}")

def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and consolidate IPC area datasets")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        help="Override the list of assessment years to attempt (e.g. --years 2025 2024)",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=4,
        help="Decimal precision applied during simplification (default: 4)",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0,
        help="Simplification tolerance applied to combined outputs (default: 0)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_cli_args(argv)

    try:
        downloader = IPCAreaDownloader(
            years_to_try=args.years,
            precision=args.precision,
            simplify_tolerance=args.simplify_tolerance,
        )
        downloader.run()
    except KeyboardInterrupt:
        print("\nScript interrupted by user")
        return 1
    except Exception as exc:
        print(f"Script failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
