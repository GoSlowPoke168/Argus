"""
Argus — Global Camera Intelligence
Data Pipeline
===========================================
Fetches public open-data camera feeds from multiple free sources and
compiles them into a single cameras.geojson file for the Argus React frontend.

SOURCES:
  1. BC DriveBC API       - ~1,040 cameras, BC Canada. FREE, no key.
  2. Windy Webcams API    - 100,000+ cameras globally, many with MJPEG video.
                           FREE key at: https://api.windy.com/  (just sign up)


HOW TO RUN:
  1. pip install requests

  2. Add your free API keys in the CONFIG below.
     - Windy key: https://api.windy.com  (sign up → My Profile → API Key)


  3. python fetch_cameras.py

  4. copy cameras.geojson public\\cameras.geojson
"""

import os
import requests
import json
import time

# ─────────────────────────────────────────────────────────
# CONFIG — Keys will be loaded from .env if present
# ─────────────────────────────────────────────────────────
CONFIG = {
    "WINDY_API_KEY": None,

    "TIMEOUT": 20,
    "REQUEST_DELAY": 0.5,
}

# Simple helper to load .env file manually (to avoid external dependencies)
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

load_env()
CONFIG["WINDY_API_KEY"] = os.getenv("WINDY_API_KEY")


HEADERS = {
    "User-Agent": "Argus/1.0 (Open Data Camera Aggregator; Educational Project)"
}


def log(msg: str, level: str = "INFO"):
    symbols = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗"}
    print(f"  {symbols.get(level, '·')} [{level}] {msg}")


# ─────────────────────────────────────────────────────────
# SOURCE 1: Windy Webcams API (FREE key required)
# The #1 recommended source. 100,000+ cameras globally.
# Many cameras have MJPEG streams (real video, not just snapshots).
# Register at: https://api.windy.com
# ─────────────────────────────────────────────────────────
def fetch_windy_cameras(api_key: str, limit_per_region: int = 500) -> list[dict]:
    """
    Fetches webcams from the Windy Webcams API using bounding boxes.
    Free tier max is 50 per request and max 1000 offset.
    Using regions allows us to bypass the 1000 offset limit globally.
    """
    BATCH_SIZE = 50  # Free tier hard limit
    url = "https://api.windy.com/webcams/api/v3/webcams"
    headers = {**HEADERS, "x-windy-api-key": api_key}

    all_features = []
    
    WINDY_REGIONS = [
        ("North America",  15, -170, 75,  -50),
        ("South America", -60,  -85, 15,  -30),
        ("Europe",         35,  -15, 72,   45),
        ("Africa",        -40,  -20, 40,   55),
        ("Asia",            0,   45, 55,  150),
        ("Oceania",       -50,  110,-10,  180),
    ]

    log(f"Fetching Windy global webcams (target: {limit_per_region} per region)...")

    for region_name, s, w, n, e in WINDY_REGIONS:
        log(f"  Fetching Windy region: {region_name}...")
        offset = 0
        region_features = 0
        
        while region_features < limit_per_region:
            batch_limit = min(BATCH_SIZE, limit_per_region - region_features)
            params = {
                "limit": batch_limit,
                "offset": offset,
                "include": "location,urls,images",
                "bbox": f"{n},{e},{s},{w}"
            }

            try:
                resp = requests.get(url, headers=headers, params=params, timeout=CONFIG["TIMEOUT"])
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as exc:
                log(f"    Windy fetch failed for {region_name} at offset {offset}: {exc}", "ERROR")
                break
            except json.JSONDecodeError as exc:
                log(f"    Windy JSON parse error: {exc}", "ERROR")
                break

            webcams = data.get("webcams", [])
            if not webcams:
                break  # No more results in this region

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

                    all_features.append({
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
                    region_features += 1
                except (ValueError, TypeError, KeyError):
                    continue

            offset += len(webcams)
            time.sleep(CONFIG["REQUEST_DELAY"])

            if len(webcams) < batch_limit:
                break  # Reached end of results for this region

        log(f"  → {region_name}: loaded {region_features} cameras")

    log(f"Windy: {len(all_features)} TOTAL cameras loaded across all regions", "OK")
    return all_features


# ─────────────────────────────────────────────────────────
# SOURCE 2: Caltrans California CCTV (NO KEY REQUIRED)
# All 12 Caltrans districts — ~2,000 cameras covering all California highways.
# Each camera has:
#   - currentImageURL:    JPEG refreshes every 5 seconds (perfect for AI)
#   - streamingVideoURL:  Real HLS .m3u8 live video stream
# ─────────────────────────────────────────────────────────
def fetch_caltrans_cameras() -> list[dict]:
    DISTRICTS = range(1, 13)  # Districts 01–12 cover all of California
    features = []
    total_skipped = 0

    log("Fetching Caltrans California CCTV (all 12 districts, no key)...")
    for district in DISTRICTS:
        url = f"https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02d}.json"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=CONFIG["TIMEOUT"])
            resp.raise_for_status()
            cctv_list = resp.json().get("data", [])
        except Exception as e:
            log(f"  Caltrans D{district:02d} failed: {e}", "WARN")
            continue

        skipped = 0
        for item in cctv_list:
            try:
                cam = item.get("cctv", {})
                if cam.get("inService", "true") == "false":
                    skipped += 1
                    continue

                loc = cam.get("location", {})
                lat = float(loc.get("latitude", 0))
                lon = float(loc.get("longitude", 0))
                if lat == 0 and lon == 0:
                    skipped += 1
                    continue

                img_data = cam.get("imageData", {})
                image_url = img_data.get("static", {}).get("currentImageURL", "")
                stream_url = img_data.get("streamingVideoURL", "")  # HLS .m3u8
                name = loc.get("locationName", f"Caltrans D{district:02d} Camera")
                city = loc.get("nearbyPlace", f"District {district}")

                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "id": f"caltrans_d{district:02d}_{cam.get('index', 'x')}",
                        "name": name,
                        "type": "traffic",
                        "city": city,
                        "country": "US",
                        "feedUrl": image_url,       # 5-second JPEG refresh
                        "streamUrl": stream_url,    # HLS live video (.m3u8)
                        "feedType": "image/jpeg",
                        "route": loc.get("route", ""),
                        "source": "caltrans"
                    }
                })
            except (ValueError, TypeError, KeyError):
                skipped += 1
                continue

        total_skipped += skipped
        log(f"  D{district:02d}: {len([f for f in features if f'caltrans_d{district:02d}' in f['properties']['id']])} cameras")
        time.sleep(0.2)

    log(f"Caltrans: {len(features)} total CA cameras ({total_skipped} skipped)", "OK")
    return features


# ─────────────────────────────────────────────────────────
# SOURCE 3: BC DriveBC Webcams API (No key required)
# ~1,040 official BC Government traffic cameras with live JPEG feeds.
# ─────────────────────────────────────────────────────────
def fetch_drivebc_cameras() -> list[dict]:
    url = "https://www.drivebc.ca/api/webcams/"
    params = {"format": "json"}

    log("Fetching BC DriveBC webcams...")
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=CONFIG["TIMEOUT"])
        resp.raise_for_status()
        cameras = resp.json()
    except requests.exceptions.RequestException as e:
        log(f"DriveBC fetch failed: {e}", "ERROR")
        return []
    except json.JSONDecodeError as e:
        log(f"DriveBC JSON parse error: {e}", "ERROR")
        return []

    if not isinstance(cameras, list):
        return []

    features = []
    skipped = 0
    for cam in cameras:
        try:
            if not cam.get("is_on") or not cam.get("should_appear"):
                skipped += 1
                continue

            coords = cam.get("location", {}).get("coordinates", [])
            if len(coords) < 2:
                skipped += 1
                continue
            lon, lat = float(coords[0]), float(coords[1])

            cam_id = str(cam.get("id", "unknown"))
            name = cam.get("name") or cam.get("caption") or f"BC Camera {cam_id}"
            image_path = cam.get("links", {}).get("imageDisplay", f"/images/{cam_id}.jpg")
            image_path = image_path.split("?")[0]
            feed_url = f"https://www.drivebc.ca{image_path}"

            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": f"drivebc_{cam_id}",
                    "name": name,
                    "type": "traffic",
                    "city": "British Columbia",
                    "country": "CA",
                    "feedUrl": feed_url,
                    "playerUrl": "",
                    "feedType": "image/jpeg",
                    "highway": cam.get("highway", ""),
                    "source": "drivebc"
                }
            })
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue

    log(f"BC DriveBC: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features





# ─────────────────────────────────────────────────────────
# SOURCE 4: Singapore LTA Traffic Images (NO KEY REQUIRED)
# ~90 traffic cameras around Singapore. Live JPEGs.
# ─────────────────────────────────────────────────────────
def fetch_singapore_cameras() -> list[dict]:
    url = "https://api.data.gov.sg/v1/transport/traffic-images"
    features = []
    
    log("Fetching Singapore LTA Traffic Cameras (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=CONFIG["TIMEOUT"])
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return []
        cams = items[0].get("cameras", [])
    except Exception as e:
        log(f"  Singapore fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            lat = float(cam.get("location", {}).get("latitude", 0))
            lon = float(cam.get("location", {}).get("longitude", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("camera_id", "unknown"))
            image_url = cam.get("image", "")
            
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": f"sg_{cam_id}",
                    "name": f"Singapore Camera {cam_id}",
                    "type": "traffic",
                    "city": "Singapore",
                    "country": "SG",
                    "feedUrl": image_url,
                    "playerUrl": "",
                    "feedType": "image/jpeg",
                    "source": "singapore"
                }
            })
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue
            
    log(f"Singapore LTA: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features


# ─────────────────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────────────────
def deduplicate(features: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for f in features:
        fid = f["properties"]["id"]
        if fid not in seen:
            seen.add(fid)
            unique.append(f)
    return unique


# ─────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────
def main():
    print("\n" + "═" * 60)
    print("  Argus — Camera Data Pipeline")
    print("═" * 60 + "\n")

    all_features: list[dict] = []

    # Source 1: Windy (best global source — add key for massive scale)
    if CONFIG["WINDY_API_KEY"]:
        all_features.extend(fetch_windy_cameras(CONFIG["WINDY_API_KEY"], limit_per_region=500))
        time.sleep(CONFIG["REQUEST_DELAY"])
    else:
        log("Skipping Windy API (no key set).", "WARN")
        log("→ Get a FREE key at https://api.windy.com for 100,000+ global cameras + video streams!", "WARN")

    # Source 2: Caltrans California (no key, ~2000 cameras with HLS video)
    all_features.extend(fetch_caltrans_cameras())
    time.sleep(CONFIG["REQUEST_DELAY"])

    # Source 3: DriveBC (always works, no key)
    all_features.extend(fetch_drivebc_cameras())
    time.sleep(CONFIG["REQUEST_DELAY"])

    # Source 4: Singapore LTA (always works, no key)
    all_features.extend(fetch_singapore_cameras())
    time.sleep(CONFIG["REQUEST_DELAY"])



    # Deduplicate
    before = len(all_features)
    all_features = deduplicate(all_features)
    if before - len(all_features) > 0:
        log(f"Removed {before - len(all_features)} duplicates", "WARN")

    sources_used = []
    if CONFIG["WINDY_API_KEY"]:  sources_used.append("Windy Webcams API")
    sources_used.append("Caltrans California")
    sources_used.append("BC DriveBC")
    sources_used.append("Singapore LTA")


    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_cameras": len(all_features),
            "sources": sources_used
        },
        "features": all_features
    }

    output_path = "cameras.geojson"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print("\n" + "─" * 60)
    print(f"  ✓ Pipeline complete!")
    print(f"  ✓ Total cameras: {len(all_features)}")
    print(f"  ✓ Sources: {', '.join(sources_used)}")
    print(f"  ✓ Output: {output_path}")
    print("─" * 60)
    print("\n  Next: copy cameras.geojson to your React app's public/ folder:")
    print("    Windows: copy cameras.geojson public\\cameras.geojson\n")


if __name__ == "__main__":
    main()
