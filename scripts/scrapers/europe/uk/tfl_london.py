import requests
from scrapers.utils import log, build_feature, HEADERS


def fetch(config) -> list[dict]:
    url = "https://api.tfl.gov.uk/Place/Type/JamCam"
    features = []

    log("Fetching TfL London JamCam webcams (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"TfL London fetch failed: {e}", "WARN")
        return []

    skipped = 0
    offline  = 0
    for cam in cams:
        try:
            lat = float(cam.get("lat", 0))
            lon = float(cam.get("lon", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue

            # Parse additionalProperties into a flat dict for easy access
            props = {p["key"]: p.get("value", "") for p in cam.get("additionalProperties", [])}

            # Skip cameras the API marks as unavailable/offline
            if props.get("available", "true").lower() != "true":
                offline += 1
                continue

            cam_id    = str(cam.get("id", "unknown"))
            image_url = props.get("imageUrl", "")
            video_url = props.get("videoUrl", "")

            features.append(build_feature(
                cam_id     = cam_id,
                name       = cam.get("commonName", f"TfL Camera {cam_id}"),
                lat        = lat,
                lon        = lon,
                feed_url   = image_url,
                stream_url = video_url,
                cam_type   = "traffic",
                city       = "London",
                country    = "GB",
                source     = "tfl_london",
            ))
        except Exception:
            skipped += 1
            continue

    log(f"TfL London: {len(features)} cameras loaded ({offline} offline skipped, {skipped} invalid)", "OK")
    return features
