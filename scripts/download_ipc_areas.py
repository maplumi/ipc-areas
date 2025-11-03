#!/usr/bin/env python3
"""
IPC Areas GeoJSON Download and Organization Script

This script downloads IPC area data for countries from the IPC API,
converts them to TopoJSON format, and organizes them by country ISO3 codes.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
COUNTRIES_CSV = REPO_ROOT / "countries.csv"
COUNTRY_FILENAME_SUFFIX = "_areas.topojson"

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
    def __init__(self, years_to_try: Optional[List[int]] = None):
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

        if not self.years_to_try:
            raise ValueError("At least one assessment year must be configured")

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
            return f"id::{iso3}::{str(area_id).strip().lower()}"

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

    def ensure_country_file_path(self, iso3: str) -> Path:
        country_dir = self.data_dir / iso3
        country_dir.mkdir(exist_ok=True)
        target = country_dir / f"{iso3}{COUNTRY_FILENAME_SUFFIX}"

        if target.exists():
            return target

        legacy_pattern = f"{iso3}_*_areas.topojson"
        for legacy in sorted(country_dir.glob(legacy_pattern), reverse=True):
            if legacy == target:
                continue
            try:
                legacy.rename(target)
                print(f"    Renamed {legacy.name} -> {target.name}")
                break
            except OSError as exc:
                print(f"    Warning: unable to rename {legacy} -> {target.name}: {exc}")

        return target

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
    
    def save_topojson(self, topojson_data: Dict[str, Any], country_iso3: str) -> Optional[Path]:
        """Save TopoJSON data to file using the consolidated naming scheme."""
        country_dir = self.data_dir / country_iso3
        country_dir.mkdir(exist_ok=True)

        filename = f"{country_iso3}{COUNTRY_FILENAME_SUFFIX}"
        filepath = country_dir / filename

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(topojson_data, f, separators=(',', ':'))

            print(f"    Saved: {filepath}")
            return filepath
        except Exception as e:
            print(f"    Error saving {filepath}: {e}")
            return None

    def add_index_entry(
        self,
        country_info: Dict[str, str],
        year: int,
        filepath: Path,
        feature_count: Optional[int],
        updated_at: Optional[str] = None
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
            "updated_at": updated_at
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
        """Process a single country - download, merge, and save data."""
        print(f"\nProcessing {country_info['name']} ({country_code})...")

        aggregated: Dict[str, Dict[str, Any]] = {}
        download_year: Optional[int] = None

        country_path = self.ensure_country_file_path(country_info['iso3'])

        if country_path.exists():
            existing_features = self.load_existing_features(country_path)
            if existing_features:
                stats = self.merge_features(
                    aggregated,
                    existing_features,
                    priority=0,
                    source_year=None,
                    source_label="existing"
                )
                if stats["added"] or stats["updated"]:
                    print(
                        "    Existing dataset contributed "
                        f"{stats['added']} new and {stats['updated']} updated features"
                    )
        for year in self.years_to_try:
            areas_data = self.download_areas(country_code, year)

            if areas_data:
                geojson = self.filter_and_process_areas(areas_data, country_info, year)

                if geojson and geojson['features']:
                    if download_year is None:
                        download_year = year
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
                else:
                    print(f"    No valid polygon features found for year {year}")

            time.sleep(0.5)

        if not aggregated:
            print(f"    No data found for {country_info['name']} in any year")
            return False

        target_year = download_year if download_year is not None else None
        if target_year is None:
            years_seen = [
                entry.get('source_year')
                for entry in aggregated.values()
                if entry.get('source_year') is not None
            ]
            if years_seen:
                target_year = max(years_seen)
        if target_year is None:
            print(f"    Unable to determine target year for {country_info['name']}")
            return False

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

        saved_path = self.save_topojson(topojson_data, country_info['iso3'])
        if not saved_path:
            print(f"    Failed to save merged dataset for {country_info['name']}")
            return False

        feature_count = len(final_features)
        self.add_index_entry(country_info, target_year, saved_path, feature_count)
        print(f"    Merged dataset saved for year {target_year} with {feature_count} features")

        return True

    def write_index_file(self) -> None:
        """Write or update the TopoJSON index file."""
        index_path = self.data_dir / "index.json"

        index_payload = {
            "generated_at": datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            "cdn_release_tag": self.cdn_release_tag,
            "total_files": len(self.index_entries),
            "items": sorted(
                self.index_entries,
                key=lambda entry: (entry['iso3'], entry['year'], entry['file_name'])
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
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_cli_args(argv)

    try:
        downloader = IPCAreaDownloader(years_to_try=args.years)
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
