import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://www.journeys.nzta.govt.nz/api/webcams"
    features = []
    
    log("Fetching NZTA Webcams (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"  NZTA fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            lat = float(cam.get("lat", 0))
            lon = float(cam.get("lng", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("id", "unknown"))
            image_url = cam.get("imageUrl", "")
            if not image_url:
                skipped += 1
                continue
                
            features.append(build_feature(
                cam_id=cam_id, name=cam.get("name", f"NZTA {cam_id}"),
                lat=lat, lon=lon, feed_url=image_url,
                cam_type="traffic", city=cam.get("region", "New Zealand"), country="NZ",
                source="nzta"
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"NZTA: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
