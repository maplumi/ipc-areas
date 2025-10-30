#!/usr/bin/env python3
"""
IPC Areas GeoJSON Download and Organization Script

This script downloads IPC area data for countries from the IPC API,
converts them to TopoJSON format, and organizes them by country ISO3 codes.
"""

import os
import sys
import csv
import json
import requests
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import topojson as tp
from shapely.geometry import shape

# Configuration
API_BASE_URL = "https://api.ipcinfo.org/areas"
YEARS_TO_TRY = [2025, 2024, 2023, 2022]
IPC_KEY = os.getenv('IPC_KEY')

class IPCAreaDownloader:
    def __init__(self):
        if not IPC_KEY:
            raise ValueError("IPC_KEY environment variable is required")
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'IPC-Areas-Downloader/1.0'
        })
        
        # Create data directory
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        
    def load_countries(self) -> Dict[str, Dict]:
        """Load country data from CSV file."""
        countries = {}
        
        try:
            with open('countries.csv', 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    alpha_2 = row['Alpha_2_Code'].strip()
                    alpha_3 = row['Alpha_3_Code'].strip()
                    countries[alpha_2] = {
                        'name': row['English_Short_Name'].strip(),
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
    
    def download_areas(self, country_code: str, year: int) -> Optional[Dict]:
        """Download IPC areas data for a specific country and year."""
        params = {
            'format': 'json',
            'country': country_code,
            'year': year,
            'type': 'A',
            'key': IPC_KEY
        }
        
        try:
            print(f"  Downloading data for {country_code} - {year}...")
            response = self.session.get(API_BASE_URL, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                # Check if we have valid data
                if data and isinstance(data, list) and len(data) > 0:
                    return data
                else:
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
    
    def filter_and_process_areas(self, areas_data: List[Dict], country_info: Dict, year: int) -> Dict:
        """Filter and process areas data to retain only required fields."""
        features = []
        seen_geometries = set()
        
        for area in areas_data:
            try:
                # Skip if no geometry or not a polygon
                if 'geometry' not in area or not area['geometry']:
                    continue
                    
                geometry = area['geometry']
                if geometry.get('type') != 'Polygon' and geometry.get('type') != 'MultiPolygon':
                    continue
                
                # Create a hash of the geometry to identify duplicates
                geometry_str = json.dumps(geometry, sort_keys=True)
                geometry_hash = hash(geometry_str)
                
                if geometry_hash in seen_geometries:
                    continue
                    
                seen_geometries.add(geometry_hash)
                
                # Extract required properties
                properties = {
                    'title': area.get('title', ''),
                    'country': country_info['iso2'],
                    'iso3': country_info['iso3'],
                    'year': year
                }
                
                # Create GeoJSON feature
                feature = {
                    'type': 'Feature',
                    'geometry': geometry,
                    'properties': properties
                }
                
                features.append(feature)
                
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
    
    def convert_to_topojson(self, geojson: Dict) -> Dict:
        """Convert GeoJSON to TopoJSON format."""
        try:
            # Use topojson library to convert
            topology = tp.Topology(geojson, prequantize=False)
            return topology.to_dict()
        except Exception as e:
            print(f"    Error converting to TopoJSON: {e}")
            return None
    
    def save_topojson(self, topojson_data: Dict, country_iso3: str, year: int):
        """Save TopoJSON data to file."""
        country_dir = self.data_dir / country_iso3
        country_dir.mkdir(exist_ok=True)
        
        filename = f"{country_iso3}_{year}_areas.topojson"
        filepath = country_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(topojson_data, f, separators=(',', ':'))
            
            print(f"    Saved: {filepath}")
            return True
        except Exception as e:
            print(f"    Error saving {filepath}: {e}")
            return False
    
    def process_country(self, country_code: str, country_info: Dict):
        """Process a single country - download and save data."""
        print(f"\nProcessing {country_info['name']} ({country_code})...")
        
        success = False
        
        for year in YEARS_TO_TRY:
            # Download areas data
            areas_data = self.download_areas(country_code, year)
            
            if areas_data:
                # Process and filter the data
                geojson = self.filter_and_process_areas(areas_data, country_info, year)
                
                if geojson and geojson['features']:
                    # Convert to TopoJSON
                    topojson_data = self.convert_to_topojson(geojson)
                    
                    if topojson_data:
                        # Save the data
                        if self.save_topojson(topojson_data, country_info['iso3'], year):
                            print(f"    Successfully processed {len(geojson['features'])} areas for year {year}")
                            success = True
                            break
                    else:
                        print(f"    Failed to convert to TopoJSON for year {year}")
                else:
                    print(f"    No valid polygon features found for year {year}")
            
            # Small delay between requests
            time.sleep(0.5)
        
        if not success:
            print(f"    No data found for {country_info['name']} in any year")
    
    def run(self):
        """Main execution method."""
        print("IPC Areas Download Script")
        print("=" * 50)
        
        # Load countries data
        print("Loading countries data...")
        countries = self.load_countries()
        print(f"Loaded {len(countries)} countries")
        
        # Process each country
        successful = 0
        failed = 0
        
        for country_code, country_info in countries.items():
            try:
                self.process_country(country_code, country_info)
                successful += 1
            except Exception as e:
                print(f"Error processing {country_info['name']}: {e}")
                failed += 1
            
            # Rate limiting
            time.sleep(1)
        
        print(f"\n" + "=" * 50)
        print(f"Processing complete!")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")
        print(f"Data saved in: {self.data_dir.absolute()}")

if __name__ == "__main__":
    try:
        downloader = IPCAreaDownloader()
        downloader.run()
    except KeyboardInterrupt:
        print("\nScript interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Script failed: {e}")
        sys.exit(1)