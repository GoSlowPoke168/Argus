import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://webcams.nyctmc.org/api/cameras"
    features = []
    
    log("Fetching NYC DOT Traffic Cameras (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"  NYC DOT fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            if str(cam.get("isOnline")).lower() != "true":
                skipped += 1
                continue
                
            lat = float(cam.get("latitude", 0))
            lon = float(cam.get("longitude", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("id", "unknown"))
            image_url = cam.get("imageUrl", "")
            if not image_url:
                skipped += 1
                continue
                
            features.append(build_feature(
                cam_id=cam_id, 
                name=cam.get("name", f"NYC Camera {cam_id}"),
                lat=lat, 
                lon=lon, 
                feed_url=image_url,
                cam_type="traffic", 
                city=cam.get("area", "New York City"), 
                country="US",
                source="nyc_dot"
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"NYC DOT: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
