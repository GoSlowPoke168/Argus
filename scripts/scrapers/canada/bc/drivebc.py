import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://www.drivebc.ca/api/webcams/"
    params = {"format": "json"}
    features = []

    log("Fetching BC DriveBC webcams...")
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        data = resp.json()
        webcams = data.get("webcams", []) if isinstance(data, dict) else data
    except Exception as e:
        log(f"DriveBC fetch failed: {e}", "ERROR")
        return []

    skipped = 0
    for cam in webcams:
        try:
            if cam.get("is_on") is False:
                skipped += 1
                continue

            coords = cam.get("location", {}).get("coordinates", [])
            if len(coords) < 2:
                skipped += 1
                continue
            lon, lat = float(coords[0]), float(coords[1])

            cam_id = str(cam.get("id", "unknown"))
            image_path = cam.get("links", {}).get("imageDisplay", "")
            if image_path:
                image_url = f"https://www.drivebc.ca{image_path}"
            else:
                image_url = ""

            features.append(build_feature(
                cam_id=cam_id, name=cam.get("name", f"DriveBC {cam_id}"),
                lat=lat, lon=lon, feed_url=image_url, cam_type="traffic",
                city="British Columbia", country="CA", source="drivebc",
                highway=str(cam.get("highway", ""))
            ))
        except Exception:
            skipped += 1
            continue

    log(f"DriveBC: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
