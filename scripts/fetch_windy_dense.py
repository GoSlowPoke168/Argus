"""
Argus — Windy Dense Region Recursive Scraper
===========================================
This script reads the existing 'windy_massive.geojson' file and exclusively
targets the 14 specific 20x20 degree bounding boxes that hit the 1,000 camera cap.
It recursively subdivides them into smaller boxes until it extracts all cameras.
"""

import os
import requests
import json
import time

CONFIG = {
    "WINDY_API_KEY": None,
    "TIMEOUT": 15,
    "REQUEST_DELAY": 0.5,
}

# The 14 boxes that hit >= 998 cameras in the first run
DENSE_BOXES = [
    (70, 0, 50, -20),
    (70, 20, 50, 0),
    (70, 40, 50, 20),
    (50, -120, 30, -140),
    (50, -100, 30, -120),
    (50, -80, 30, -100),
    (50, -60, 30, -80),
    (50, 0, 30, -20),
    (50, 20, 30, 0),
    (50, 40, 30, 20),
    (50, 140, 30, 120),
    (30, -80, 10, -100),
    (30, 120, 10, 100),
    (10, 120, -10, 100),
]

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
    "User-Agent": "Argus/1.0",
    "x-windy-api-key": CONFIG["WINDY_API_KEY"]
}

def log(msg: str):
    print(f"  {msg}")

def fetch_recursive_box(n, e, s, w, depth=0) -> list[dict]:
    """Recursively fetches cameras. If box hits >950 cameras, subdivide into 4."""
    BATCH_SIZE = 50
    url = "https://api.windy.com/webcams/api/v3/webcams"
    
    offset = 0
    box_features = []
    total_fetched = 0
    
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
                log(f"[{depth}] Rate limited! Sleeping 5s...")
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log(f"[{depth}] Fetch failed for box ({n},{e},{s},{w}): {exc}")
            break

        webcams = data.get("webcams", [])
        if not webcams:
            break

        total_fetched += len(webcams)

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

    # If we hit exactly 1000 (or close to it, to be safe), we must subdivide!
    if total_fetched >= 950:
        log(f"[{depth}] Box N:{n} E:{e} S:{s} W:{w} hit {total_fetched} limit! Subdividing...")
        # Subdivide into 4 quadrants
        mid_lat = (n + s) / 2
        mid_lon = (e + w) / 2
        
        sub_features = []
        # Top Left
        sub_features.extend(fetch_recursive_box(n, mid_lon, mid_lat, w, depth + 1))
        # Top Right
        sub_features.extend(fetch_recursive_box(n, e, mid_lat, mid_lon, depth + 1))
        # Bottom Left
        sub_features.extend(fetch_recursive_box(mid_lat, mid_lon, s, w, depth + 1))
        # Bottom Right
        sub_features.extend(fetch_recursive_box(mid_lat, e, s, mid_lon, depth + 1))
        
        return sub_features
    
    log(f"[{depth}] Box N:{n} E:{e} S:{s} W:{w} finished with {total_fetched} cameras.")
    return box_features

def main():
    if not CONFIG["WINDY_API_KEY"]:
        log("WINDY_API_KEY is missing from .env!")
        return

    print("\n" + "═" * 60)
    print("  Argus — Dense Region Recursive Scraper")
    print("═" * 60 + "\n")

    input_path = "public/windy_massive.geojson"
    if not os.path.exists(input_path):
        log(f"Could not find {input_path}!")
        return

    # Load existing cameras
    log("Loading existing cameras...")
    with open(input_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)
    
    all_features = geojson.get("features", [])
    seen_ids = set(f["properties"]["id"] for f in all_features)
    start_count = len(all_features)
    log(f"Found {start_count} existing cameras.\n")

    # Process each dense box
    for i, (n, e, s, w) in enumerate(DENSE_BOXES, 1):
        print(f"--- Processing Dense Box {i}/{len(DENSE_BOXES)} [N:{n} E:{e} S:{s} W:{w}] ---")
        new_cameras = fetch_recursive_box(n, e, s, w)
        
        added_this_box = 0
        for cam in new_cameras:
            if cam["properties"]["id"] not in seen_ids:
                seen_ids.add(cam["properties"]["id"])
                all_features.append(cam)
                added_this_box += 1
                
        print(f"    -> Added {added_this_box} new cameras from this region.\n")

    # Save
    geojson["features"] = all_features
    geojson["metadata"]["total_cameras"] = len(all_features)

    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print("\n" + "─" * 60)
    print(f"  ✓ Pipeline complete!")
    print(f"  ✓ New cameras discovered: {len(all_features) - start_count}")
    print(f"  ✓ Total unique cameras: {len(all_features)}")
    print("─" * 60 + "\n")

if __name__ == "__main__":
    main()
