# IPC Areas Data Toolkit

This repository produces and distributes merged IPC (Integrated Food Security Phase Classification) area boundaries. It includes automation for gathering country-level datasets from the IPC API, harmonising them, and publishing a global aggregate that is ready for use in downstream applications.

## Data Consumption

- **Country TopoJSON**: Each country lives under `data/{ISO3}/{ISO3}_{YEAR}_areas.topojson`. The `YEAR` component reflects the most recent IPC assessment that delivered data during the last run. More areas data are also sourced from older IPC assessements upto 2020. Only unique area names are retained.
- **Global TopoJSON**: `data/ipc_global_areas.topojson` contains all areas deduplicated across countries. Coordinates are rounded to four decimal places and can be optionally simplified for lighter payloads.
- **Index File**: `data/index.json` lists every exported country dataset including feature counts, CDN URLs (if `CDN_RELEASE_TAG` was set), and timestamps. Use this file for programmatic discovery.
- **Coordinate Precision**: Country files preserve full precision from the API. The global file defaults to four decimal places; adjust via `scripts/simplify_ipc_global_areas.py` if you need alternative precision.
- **Access via CDN**: When the repository is tagged, `https://cdn.jsdelivr.net/gh/maplumi/ipc-areas@<TAG>/data/...` exposes the same hierarchy. Set `CDN_RELEASE_TAG` during generation to control the pointer the index will embed.

## Development / Extending Features

- **Environment Setup**
   - Python 3.8+ recommended
   - Install dependencies: `pip install -r requirements.txt`
   - Set `IPC_KEY` via environment variables (Windows PowerShell example: `$env:IPC_KEY = "your_api_key"`)
- **Running Scripts**
   - Fetch/merge country datasets: `python scripts/download_ipc_areas.py`
   - Regenerate the global aggregate: `python scripts/combine_ipc_areas.py`
   - Re-simplify an existing global file: `python scripts/simplify_ipc_global_areas.py --help`
- **Main Workflow (`scripts/download_ipc_areas.py`)**
   - Reads `countries.csv`
   - Attempts downloads from 2025 down to 2020 for each ISO2 code
   - Merges newly discovered features with existing TopoJSON, avoiding duplication by geometry/title key
   - Converts to TopoJSON and updates `data/index.json`
- **Combining Data (`scripts/combine_ipc_areas.py`)**
   - Aggregates every country file into `data/ipc_global_areas.topojson`
   - Exposes CLI flags for precision (`--precision`) and simplification (`--simplify-tolerance`) via the shared simplification helpers
   - Use `--skip-simplify` (alias `--skip-minify`) if you plan to post-process separately
- **Simplification Helpers (`scripts/simplify_ipc_global_areas.py`)**
   - Provides reusable `minify_topojson` and CLI utilities to round coordinates and optionally apply Shapely-based simplification
   - Defaults to overwriting the input file; pass `--output` to write elsewhere
- **Extending for New Years or Formats**
   - Update `YEARS_TO_TRY` in `scripts/download_ipc_areas.py` if IPC releases additional assessments
   - Modify `feature_key` logic to include other identifiers (e.g., admin codes) if available
   - Introduce new exporters by reading from the common GeoJSON feature collections produced before the TopoJSON conversion
- **Testing & Validation**
   - Run the downloader and combiner scripts locally to ensure new logic respects rate limits and geometry constraints
   - Verify `data/index.json` and `data/ipc_global_areas.topojson` diff sizes to confirm changes behave as expected
- **Publishing**
   - Tag releases (`git tag -a vX.Y.Z`) after regenerating data
   - Push branch and tags (`git push origin main && git push origin vX.Y.Z`) so the CDN links stay in sync

For IPC API issues, contact the IPC Info team. For repository-specific questions, open an issue or consult inline logging output produced during the scripts’ execution.