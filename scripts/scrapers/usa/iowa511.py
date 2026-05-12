import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://www.511ia.org/api/v2/cameras"
    features = []
    
    log("Fetching Iowa 511 Cameras (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"  Iowa 511 fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            lat = float(cam.get("latitude", 0))
            lon = float(cam.get("longitude", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("id", "unknown"))
            views = cam.get("views", [])
            if not views:
                skipped += 1
                continue
                
            image_url = views[0].get("url", "")
            
            features.append(build_feature(
                cam_id=cam_id, name=cam.get("name", f"Iowa 511 {cam_id}"),
                lat=lat, lon=lon, feed_url=image_url,
                cam_type="traffic", city="Iowa", country="US",
                source="iowa511"
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"Iowa 511: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
