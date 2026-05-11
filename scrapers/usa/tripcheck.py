import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://www.tripcheck.com/api/cameras"
    features = []
    
    log("Fetching Oregon TripCheck Cameras (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"  TripCheck fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            # Format is usually an array of items, each item has long/lat and filename
            lat = float(cam.get("latitude", 0))
            lon = float(cam.get("longitude", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("id", "unknown"))
            filename = cam.get("filename", "")
            if not filename:
                skipped += 1
                continue
                
            image_url = f"https://tripcheck.com/RoadCams/cams/{filename}"
            
            features.append(build_feature(
                cam_id=cam_id, name=cam.get("name", f"Oregon Camera {cam_id}"),
                lat=lat, lon=lon, feed_url=image_url,
                cam_type="traffic", city=cam.get("city", "Oregon"), country="US",
                source="tripcheck", route=cam.get("route", "")
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"TripCheck Oregon: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
