import requests, time
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    api_key = config.get("WINDY_API_KEY")
    if not api_key:
        log("Skipping Windy API (no key set).", "WARN")
        return []

    limit_per_region = config.get("WINDY_LIMIT_PER_REGION", 500)
    BATCH_SIZE = 50
    url = "https://api.windy.com/webcams/api/v3/webcams"
    headers = {**HEADERS, "x-windy-api-key": api_key}
    
    WINDY_REGIONS = [
        ("NA-NW", 45, -170, 75, -110),
        ("NA-NE", 45, -110, 75, -50),
        ("NA-SW", 15, -170, 45, -110),
        ("NA-SE", 15, -110, 45, -50),
        ("EU-NW", 53.5, -15, 72, 15),
        ("EU-NE", 53.5, 15, 72, 45),
        ("EU-SW", 35, -15, 53.5, 15),
        ("EU-SE", 35, 15, 53.5, 45),
        ("SA-NW", -22.5, -85, 15, -57.5),
        ("SA-NE", -22.5, -57.5, 15, -30),
        ("SA-SW", -60, -85, -22.5, -57.5),
        ("SA-SE", -60, -57.5, -22.5, -30),
        ("AF-NW", 0, -20, 40, 17.5),
        ("AF-NE", 0, 17.5, 40, 55),
        ("AF-SW", -40, -20, 0, 17.5),
        ("AF-SE", -40, 17.5, 0, 55),
        ("AS-NW", 27.5, 45, 55, 97.5),
        ("AS-NE", 27.5, 97.5, 55, 150),
        ("AS-SW", 0, 45, 27.5, 97.5),
        ("AS-SE", 0, 97.5, 27.5, 150),
        ("OC-NW", -30, 110, -10, 145),
        ("OC-NE", -30, 145, -10, 180),
        ("OC-SW", -50, 110, -30, 145),
        ("OC-SE", -50, 145, -30, 180),
    ]

    all_features = []
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
                resp = requests.get(url, headers=headers, params=params, timeout=config.get("TIMEOUT", 10))
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log(f"    Windy fetch failed for {region_name}: {exc}", "ERROR")
                break

            webcams = data.get("webcams", [])
            if not webcams: break

            for cam in webcams:
                try:
                    loc = cam.get("location", {})
                    lat, lon = float(loc.get("latitude", 0)), float(loc.get("longitude", 0))
                    if lat == 0 and lon == 0: continue

                    cam_id = str(cam.get("webcamId", cam.get("id", "unknown")))
                    title = cam.get("title", f"Windy Camera {cam_id}")
                    
                    images = cam.get("images", {})
                    image_url = images.get("current", {}).get("preview", "") or images.get("daylight", {}).get("preview", "")
                    player_url = cam.get("urls", {}).get("provider", "")
                    
                    all_features.append(build_feature(
                        cam_id=cam_id, name=title, lat=lat, lon=lon, feed_url=image_url,
                        cam_type="landmark", city=loc.get("city", ""), country=loc.get("country_code", ""),
                        source="windy", player_url=player_url
                    ))
                    region_features += 1
                except Exception:
                    continue

            offset += len(webcams)
            time.sleep(config.get("REQUEST_DELAY", 0.1))
            if len(webcams) < batch_limit: break

        log(f"  → {region_name}: loaded {region_features} cameras")

    log(f"Windy: {len(all_features)} TOTAL cameras loaded", "OK")
    return all_features
