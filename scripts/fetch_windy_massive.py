"""
Argus — Global Camera Intelligence
Massive Windy Scraper
===========================================
This script uses a 10x10 degree micro-grid system to bypass the Windy API's 
1,000 offset limitation, allowing Free-tier users to scrape the maximum 
possible number of global webcams.

HOW TO RUN:
  1. Ensure WINDY_API_KEY is set in your .env file.
  2. python fetch_windy_massive.py
  3. The script will save 'windy_massive.geojson'.
"""

import os
import requests
import json
import time

# ─────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────
CONFIG = {
    "WINDY_API_KEY": None,
    "TIMEOUT": 15,
    "REQUEST_DELAY": 0.5,  # Important for Free Tier rate limits
    "GRID_SIZE": 20,       # 20x20 degree boxes (speeds up empty oceans while keeping density low)
}

def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    try:
                        key, value = line.strip().split("=", 1)
                        os.environ[key] = value
                    except ValueError:
                        pass

load_env()
CONFIG["WINDY_API_KEY"] = os.getenv("WINDY_API_KEY")

HEADERS = {
    "User-Agent": "Argus/1.0 (Open Data Camera Aggregator; Educational Project)",
    "x-windy-api-key": CONFIG["WINDY_API_KEY"]
}

def log(msg: str, level: str = "INFO"):
    symbols = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗"}
    print(f"  {symbols.get(level, '·')} [{level}] {msg}")

def fetch_box(n, e, s, w) -> list[dict]:
    """Fetches all cameras in a specific bounding box (up to 1000)."""
    BATCH_SIZE = 50
    url = "https://api.windy.com/webcams/api/v3/webcams"
    
    offset = 0
    box_features = []
    
    while offset < 1000:
        params = {
            "limit": BATCH_SIZE,
            "offset": offset,
            "include": "location,urls,images",
            "bbox": f"{n},{e},{s},{w}"
        }
        
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=CONFIG["TIMEOUT"])
            if resp.status_code == 429:
                log("Rate limited! Sleeping 5s...", "WARN")
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log(f"Fetch failed for box ({n},{e},{s},{w}): {exc}", "ERROR")
            break

        webcams = data.get("webcams", [])
        if not webcams:
            break

        for cam in webcams:
            try:
                location = cam.get("location", {})
                lat = float(location.get("latitude", 0))
                lon = float(location.get("longitude", 0))
                if lat == 0 and lon == 0: continue

                cam_id = str(cam.get("webcamId", cam.get("id", "unknown")))
                title = cam.get("title", f"Windy Camera {cam_id}")
                city = location.get("city", location.get("region", ""))
                country_code = location.get("country_code", "")

                images = cam.get("images", {})
                image_url = (
                    images.get("current", {}).get("preview", "") or
                    images.get("daylight", {}).get("preview", "") or ""
                )
                player_url = cam.get("urls", {}).get("provider", "") or ""

                box_features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "id": f"windy_{cam_id}",
                        "name": title,
                        "type": "landmark",
                        "city": city,
                        "country": country_code,
                        "feedUrl": image_url,
                        "playerUrl": player_url,
                        "feedType": "image/jpeg",
                        "source": "windy"
                    }
                })
            except Exception:
                continue

        offset += len(webcams)
        time.sleep(CONFIG["REQUEST_DELAY"])
        
        if len(webcams) < BATCH_SIZE:
            break

    return box_features

def main():
    if not CONFIG["WINDY_API_KEY"]:
        log("WINDY_API_KEY is missing from .env!", "ERROR")
        return

    print("\n" + "═" * 60)
    print("  Argus — Massive Windy Micro-Grid Scraper")
    print("═" * 60 + "\n")

    grid = CONFIG["GRID_SIZE"]
    all_features = []
    seen_ids = set()
    total_boxes = (180 // grid) * (360 // grid)
    box_num = 0
    
    # Traverse the globe
    for lat in range(90, -90, -grid):
        for lon in range(-180, 180, grid):
            box_num += 1
            n = lat
            s = lat - grid
            w = lon
            e = lon + grid
            
            box_cameras = fetch_box(n, e, s, w)
            
            new_in_box = 0
            for cam in box_cameras:
                if cam["properties"]["id"] not in seen_ids:
                    seen_ids.add(cam["properties"]["id"])
                    all_features.append(cam)
                    new_in_box += 1
                    
            print(f"  [{box_num}/{total_boxes}] Box N:{n} E:{e} S:{s} W:{w} -> {new_in_box} cameras")

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_cameras": len(all_features),
            "sources": ["Windy Massive Scraper"]
        },
        "features": all_features
    }

    output_path = "windy_massive.geojson"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print("\n" + "─" * 60)
    print(f"  ✓ Pipeline complete!")
    print(f"  ✓ Total unique cameras: {len(all_features)}")
    print(f"  ✓ Output: {output_path}")
    print("─" * 60 + "\n")

if __name__ == "__main__":
    main()
