import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://api.tfl.gov.uk/Place/Type/JamCam"
    features = []
    
    log("Fetching TfL London Webcams (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"  TfL London fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            lat = float(cam.get("lat", 0))
            lon = float(cam.get("lon", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("id", "unknown"))
            props = cam.get("additionalProperties", [])
            
            image_url = ""
            video_url = ""
            for p in props:
                if p.get("key") == "imageUrl": image_url = p.get("value", "")
                if p.get("key") == "videoUrl": video_url = p.get("value", "")
            
            features.append(build_feature(
                cam_id=cam_id, name=cam.get("commonName", f"TfL Camera {cam_id}"),
                lat=lat, lon=lon, feed_url=image_url, stream_url=video_url,
                cam_type="traffic", city="London", country="GB", source="tfl_london"
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"TfL London: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
