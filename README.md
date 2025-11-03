# IPC Areas Data Toolkit

This repository produces and distributes merged IPC (Integrated Food Security Phase Classification) area boundaries. It includes automation for gathering country-level datasets from the IPC API, harmonising them, and publishing a global aggregate that is ready for use in downstream applications.

## Data Consumption

- **Per-Year TopoJSON**: Each assessment lives under `data/{ISO3}/{ISO3}_{YEAR}_areas.topojson`. These files mirror the IPC API responses (filtered to polygons) without post-processing so you can inspect a single release in isolation.
- **Combined Country TopoJSON**: `data/{ISO3}/{ISO3}_combined_areas.topojson` merges all available years for a country, deduplicating by the IPC `id` field and rounding coordinates to the configured precision.
- **Global TopoJSON**: `data/global_areas.topojson` aggregates every combined country file, deduplicates by ISO3+`id`, and applies the same simplification defaults as the country combines.
- **Index File**: `data/index.json` lists every exported dataset (per-year, combined, global) including feature counts, `variant` labels, CDN URLs (if `CDN_RELEASE_TAG` was set), and timestamps. Use this file for programmatic discovery.
- **Coordinate Precision**: Per-year files preserve full precision from the API. Combined country files and the global dataset default to four decimal places; adjust via CLI arguments or `scripts/simplify_ipc_global_areas.py` if you need different rounding/tolerance.
- **Access via CDN**: When the repository is tagged, `https://cdn.jsdelivr.net/gh/maplumi/ipc-areas@<TAG>/data/...` exposes the same hierarchy. Set `CDN_RELEASE_TAG` during generation to control the pointer the index will embed.

## Development / Extending Features

- **Environment Setup**
   - Python 3.8+ recommended
   - Install dependencies: `pip install -r requirements.txt`
   - Set `IPC_KEY` via environment variables (Windows PowerShell example: `$env:IPC_KEY = "your_api_key"`)
- **Running Scripts**
   - Fetch/merge country datasets (per-year, combined, global): `python scripts/download_ipc_areas.py`
   - Rebuild global dataset only (optional): `python scripts/combine_ipc_areas.py`
   - Re-simplify an existing file: `python scripts/simplify_ipc_global_areas.py --help`
- **Main Workflow (`scripts/download_ipc_areas.py`)**
   - Reads `countries.csv`
   - Attempts downloads for the assessment years supplied via `--years` (defaults to the current calendar year when omitted)
   - Saves each year to `data/{ISO3}/{ISO3}_{YEAR}_areas.topojson`, merges all available years by IPC `id`, and writes `data/{ISO3}/{ISO3}_combined_areas.topojson`
   - Builds `data/global_areas.topojson` from the combined country files and updates `data/index.json`
- **Combining Data (`scripts/combine_ipc_areas.py`)**
   - Aggregates combined country files into a new global dataset (defaults to `data/global_areas.topojson`)
   - Exposes CLI flags for precision (`--precision`) and simplification (`--simplify-tolerance`) via the shared simplification helpers
   - Use `--include-per-year` to incorporate individual assessment files if desired, or `--skip-simplify` to bypass the minification pass
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
- **Automation**
   - A scheduled workflow (`.github/workflows/refresh-ipc-areas.yml`) runs every Monday at 06:00 UTC and on manual dispatch
   - Set `IPC_KEY` as a repository secret so the downloader can authenticate; optionally provide a PAT if your org restricts the default token
   - The workflow downloads country datasets (current year by default, full history when dispatched with `full_refresh=true`), refreshes per-year and combined country files, writes `data/global_areas.topojson`, and opens a pull request with the changes

For IPC API issues, contact the IPC Info team. For repository-specific questions, open an issue or consult inline logging output produced during the scripts’ execution.