# ARGUS — Global Camera Intelligence

Argus is a high-performance, tactical surveillance dashboard designed to aggregate and visualize global open-data camera feeds. It provides real-time monitoring of over 80,000+ camera nodes across highways, landmarks, and urban centers worldwide.

![Argus Tactical UI](src/assets/DemoInterface.png)

---

## Core Features

- **Massive Global Scale**: Ingests 80,000+ cameras from 126+ countries around the world.
- **Real-Time Visualization**: High-performance map rendering via Deck.GL and MapLibre.
- **Hybrid Feed Support**: Automatically switches between live HLS video streams (.m3u8) and high-frequency static JPEGs.
- **Intelligent Engine**: Plugin-based architecture for adding new regional data sources (Caltrans, DriveBC, LTA, Windy, etc.).

---

## Project Structure

```text
Argus/
├── public/
│   └── cameras.geojson         # The main camera dataset (auto-generated)
├── scripts/                    # Python Data Pipeline
│   ├── engine.py               # Unified CLI runner — the only script you need
│   ├── scrapers/
│   │   ├── utils.py            # Shared helpers
│   │   ├── global/
│   │   │   └── windy.py        # Windy
│   │   ├── usa/
│   │   │   ├── california/
│   │   │   │   └── caltrans.py # Caltrans
│   │   │   ├── new_york/
│   │   │   │   └── nyc_dot.py  # NYC DOT
│   │   │   ├── iowa/
│   │   │   │   └── iowa511.py  # Iowa DOT (ArcGIS)
│   │   ├── canada/
│   │   │   └── bc/
│   │   │       └── drivebc.py  # DriveBC
│   │   ├── asia/
│   │   │   └── singapore/
│   │   │       └── lta.py      # Singapore LTA
│   │   ├── europe/
│   │   │   └── uk/
│   │   │       └── tfl_london.py # TfL London
│   │   └── oceania/
│   │       └── nz/
│   │           └── nzta.py     # NZTA
│   └── legacy/                 # Retired scripts
├── src/                        # React Frontend
└── .env                        # API Keys
```

---

## Setup & Installation

### 1. Frontend

```bash
npm install
npm run dev
```

### 2. Python Pipeline

**Requirements:**
- Python 3.9+
- `pip install requests`

**API Keys** — create a `.env` file in the project root:
```env
WINDY_API_KEY=your_key_here
VITE_WINDY_API_KEY=your_key_here
```
> Get a free Windy key at [api.windy.com](https://api.windy.com/) — required only for the `windy` plugin. All other sources are completely free with no key.

---

## Running the Data Pipeline

All scraping is done through a single unified engine. Run commands from the **`scripts/`** directory:

```bash
cd scripts
```

### See all available plugins

```bash
python engine.py --list
```

### Common workflows

| Goal | Command |
|---|---|
| Full global run (everything) | `python engine.py --all` |
| Windy only (the big 100k run) | `python engine.py --plugins windy` |
| All fast sources, skip Windy | `python engine.py --all --exclude windy` |
| Specific sources | `python engine.py --plugins drivebc tfl_london nyc_dot` |
| Remove stale cameras & refresh | `python engine.py --all --replace-source` |
| Nuke and rebuild from scratch | `python engine.py --all --fresh` |
| Custom output path | `python engine.py --all --output ../public/cameras.geojson` |
| Run plugins in parallel | `python engine.py --all --exclude windy --parallel` |

> **Default output:** `public/cameras.geojson` — the React app reads directly from this file. No manual copy step needed.

### Update Modes

| Mode | What it does |
|---|---|
| *(default — upsert)* | Loads existing data, refreshes known cameras by ID, appends new ones. Safe to re-run anytime without losing data. |
| `--replace-source` | Drops all cameras from the sources being run, then inserts fresh results. **Use this to remove stale/offline cameras.** Cameras from other sources are untouched. |
| `--fresh` | Ignores existing file entirely. Writes only what was just fetched. Use to fully rebuild from scratch. |

### Windy Full Global Run (Recommended for 80k+ nodes)

The Windy plugin automatically runs in two phases in a single command — no manual steps:

1. **Phase 1** — Scans the globe with a 20°×20° grid (162 boxes)
2. **Phase 2** — Any box returning ≥999 cameras is automatically recursively subdivided into quadrants until fully drained

```bash
# One command — does both phases automatically
python engine.py --plugins windy
```

> ⚠ This takes **10–30 minutes** depending on your connection and how many dense regions exist. Rate limiting (HTTP 429) is handled automatically with a 10-second backoff.

---

## Data Sources

| Plugin Alias | Source | Region | Camera Type | API Key |
|:---|:---|:---|:---|:---|
| `windy` | [Windy Webcams](https://api.windy.com/) | 🌍 Global | Landmarks, weather, scenic | ✅ Required (Free) |
| `caltrans` | [Caltrans CCTV](https://cwwp2.dot.ca.gov/) | California, USA | Highway / Traffic | ❌ None |
| `nyc_dot` | [NYC TMC](https://webcams.nyctmc.org/) | New York City, USA | Urban / Traffic | ❌ None |
| `drivebc` | [DriveBC](https://www.drivebc.ca/) | British Columbia, CA | Highway / Mountain | ❌ None |
| `singapore_lta` | [Singapore LTA](https://data.gov.sg/) | Singapore | Urban / Traffic | ❌ None |
| `tfl_london` | [Transport for London](https://api.tfl.gov.uk/) | London, UK | JamCam / Traffic | ❌ None |
| `nzta` | [NZTA Journeys](https://www.journeys.nzta.govt.nz/) | New Zealand | Highway | ❌ None |
| `iowa_dot` | [Iowa DOT](https://services.arcgis.com/8lRhdTsQyJpO52F1/ArcGIS/rest/services/Traffic_Cameras_View/FeatureServer/0) | Iowa, USA | Traffic / Highway | ❌ None |

---

## Adding a New Scraper Plugin

The engine auto-loads any plugin registered in `PLUGIN_REGISTRY` inside `engine.py`. Here's how to add one:

### Step 1 — Create the scraper file

Create a new `.py` file under the appropriate region folder in `scripts/scrapers/`. The file **must** export a `fetch(config)` function that returns a list of GeoJSON Feature dicts.

```python
# scripts/scrapers/usa/my_new_source.py
from scrapers.utils import log, build_feature, HEADERS
import requests

PLUGIN_META = {
    "name":        "My New Source",
    "key_required": False,
    "description": "Short description of what this scrapes",
}

def fetch(config: dict) -> list[dict]:
    log("Fetching My New Source...")
    features = []

    try:
        resp = requests.get("https://example.gov/api/cameras", headers=HEADERS,
                            timeout=config.get("TIMEOUT", 15))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"Fetch failed: {e}", "ERROR")
        return []

    for cam in cams:
        try:
            lat = float(cam["lat"])
            lon = float(cam["lon"])
            if lat == 0 and lon == 0:
                continue

            features.append(build_feature(
                cam_id   = str(cam["id"]),
                name     = cam.get("name", "Unknown Camera"),
                lat      = lat,
                lon      = lon,
                feed_url = cam.get("imageUrl", ""),
                cam_type = "traffic",           # "traffic", "landmark", etc.
                city     = cam.get("city", ""),
                country  = "US",
                source   = "my_new_source",     # unique snake_case identifier
            ))
        except Exception:
            continue

    log(f"My New Source: {len(features)} cameras loaded", "OK")
    return features
```

> **`build_feature` reference:**
> ```python
> build_feature(cam_id, name, lat, lon, feed_url, cam_type, city, country, source,
>               player_url="",   # Link to a viewer page (optional)
>               stream_url="",   # HLS .m3u8 URL if available (optional)
>               feed_type="image/jpeg",
>               **kwargs)        # Any extra properties you want on the feature
> ```

### Step 2 — Register it in `engine.py`

Open `scripts/engine.py` and add an entry to `PLUGIN_REGISTRY`:

```python
PLUGIN_REGISTRY = {
    # ... existing entries ...

    "my_new_source": {
        "module":      "scrapers.usa.my_new_source",   # Python module path
        "name":        "My New Source",
        "key":         None,                           # or "MY_API_KEY_ENV_VAR"
        "description": "Short description shown in --list",
    },
}
```

### Step 3 — If it needs an API key

Add the key to `.env`:
```env
MY_API_KEY=your_key_here
```

Then reference it in `engine.py`'s `CONFIG` dict:
```python
CONFIG = {
    # ... existing ...
    "MY_API_KEY": os.getenv("MY_API_KEY"),
}
```

And read it in your plugin via `config.get("MY_API_KEY")`.

### Step 4 — Run it

```bash
cd scripts
python engine.py --plugins my_new_source
```

---

## Environment Configuration

Create a `.env` file in the **project root** (`Argus/.env`):

```env
# Required for the Windy plugin (100k+ global cameras)
WINDY_API_KEY=your_windy_key_here

# Required for the React frontend Windy JIT token fetching
VITE_WINDY_API_KEY=your_windy_key_here
```

> `.env` is git-ignored. Never commit API keys.

---

## License

This project is for educational and open-data visualization purposes only. All camera feeds are sourced from public, non-sensitive government or commercial APIs.
