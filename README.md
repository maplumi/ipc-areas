# IPC Areas GeoJSON Download and Organization

This script downloads IPC (Integrated Food Security Phase Classification) area data for countries and converts them to TopoJSON format for efficient web serving.

## Features

- Downloads IPC area data from the official IPC API
- Tries multiple years (2025, 2024, 2023, 2022) to find available data
- Filters for polygon geometries only (ignoring point data)
- Removes duplicate geometries
- Converts GeoJSON to TopoJSON for smaller file sizes
- Organizes data by country ISO3 codes
- Includes comprehensive error handling and logging

## Prerequisites

1. Python 3.7 or higher
2. IPC API key (get from IPC Info website)

## Installation

1. Clone or download this repository
2. Install required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Set your IPC API key as an environment variable:

**Windows (PowerShell):**
```powershell
$env:IPC_KEY = "your_api_key_here"
```

**Windows (Command Prompt):**
```cmd
set IPC_KEY=your_api_key_here
```

**Linux/macOS:**
```bash
export IPC_KEY="your_api_key_here"
```
The script first checks the current process environment for `IPC_KEY`. On Windows it also falls back to your persisted *User Environment Variables*, so keys added through *System Properties → Environment Variables* are picked up automatically on the next run.

### Optional: CDN Release Tag

If you plan to host the generated files through jsDelivr, the script automatically resolves a tag using this order: `CDN_RELEASE_TAG` environment variable → current Git tag → current branch → short commit hash → `main`. Set the variable manually when you need to override what Git reports:

**PowerShell**
```powershell
$env:CDN_RELEASE_TAG = "v1.0.0"
```

**Bash**
```bash
export CDN_RELEASE_TAG="v1.0.0"
```

If not provided, the script defaults to `main`.

### Optional: Force Refresh

By default, existing TopoJSON files are reused to avoid unnecessary API calls. To force fresh downloads, set:

**PowerShell**
```powershell
$env:IPC_FORCE_DOWNLOAD = "true"
```

**Bash**
```bash
export IPC_FORCE_DOWNLOAD="true"
```

## Usage

Run the script:
```bash
python download_ipc_areas.py
```

The script will:
1. Read country data from `countries.csv`
2. For each country, attempt to download IPC area data
3. Try years 2025, 2024, 2023, and 2022 until data is found
4. Filter and process the data to retain only:
   - Area title (name)
   - Country ISO2 code
   - Country ISO3 code (from CSV)
   - Year
   - Polygon coordinates only
5. Convert to TopoJSON format
6. Save to `data/{ISO3_CODE}/{ISO3_CODE}_{YEAR}_areas.topojson`
7. Update `data/index.json` with metadata for easy file discovery
8. Reuse cached files on subsequent runs unless force refresh is enabled

## Output Structure

```
data/
├── DZA/
│   └── DZA_2025_areas.topojson
├── AGO/
│   └── AGO_2024_areas.topojson
├── BEN/
│   └── BEN_2023_areas.topojson
└── ...
```

## Output Metadata (`data/index.json`)

The script maintains an index file with entries such as:

```json
{
   "generated_at": "2025-10-30T12:34:56Z",
   "cdn_release_tag": "v1.0.0",
   "total_files": 42,
   "items": [
      {
         "country": "Uganda",
         "iso2": "UG",
         "iso3": "UGA",
         "year": 2024,
         "relative_path": "data/UGA/UGA_2024_areas.topojson",
         "file_name": "UGA_2024_areas.topojson",
         "feature_count": 21,
         "cdn_url": "https://cdn.jsdelivr.net/gh/maplumi/ipc-areas@v1.0.0/data/UGA/UGA_2024_areas.topojson",
         "updated_at": "2025-10-30T12:34:56Z"
      }
   ]
}
```

This makes it simple to discover available datasets programmatically and link directly to cached CDN locations.

## Error Handling

The script includes comprehensive error handling for:
- Missing API key
- Network timeouts
- Invalid JSON responses
- Missing country data
- Geometry processing errors

## Rate Limiting

The script includes built-in rate limiting (1-second delays between countries, 0.5-second delays between year attempts) to be respectful of the IPC API.

## File Formats

- **Input**: CSV file with country data
- **API**: JSON from IPC API
- **Processing**: GeoJSON (in memory)
- **Output**: TopoJSON (optimized for web serving)

## API Reference

The script uses the IPC Areas API:
```
https://api.ipcinfo.org/areas?format=geojson&country={ISO2}&year={YEAR}&type=A&key={API_KEY}
```

Parameters:
- `format`: geojson
- `country`: ISO2 country code
- `year`: Year (2022-2025)
- `type`: A (for areas)
- `key`: Your API key

## License

This project is provided as-is for educational and research purposes.

## Support

For IPC API issues, contact the IPC Info team.
For script issues, please check the error messages and ensure all prerequisites are met.