import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://api.data.gov.sg/v1/transport/traffic-images"
    features = []
    
    log("Fetching Singapore LTA Traffic Cameras (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items: return []
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
            
            features.append(build_feature(
                cam_id=cam_id, name=f"Singapore Camera {cam_id}",
                lat=lat, lon=lon, feed_url=image_url, cam_type="traffic",
                city="Singapore", country="SG", source="singapore"
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"Singapore LTA: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
