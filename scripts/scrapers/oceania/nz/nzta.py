"""
Argus — New Zealand Traffic Cameras (trafficnz.info)
======================================================
Source: https://trafficnz.info/service/traffic/rest/4/cameras/all
257 active highway cameras across NZ, ~70KB JPEG images.

Replaces the deprecated journeys.nzta.govt.nz/api/webcams endpoint (404).
Filters out cameras marked offline or underMaintenance.
"""

import requests
from scrapers.utils import log, build_feature, HEADERS

PLUGIN_META = {
    "name":         "NZTA New Zealand",
    "key_required": False,
    "description":  "257 NZ highway cameras via trafficnz.info (static JPEG ~70KB)",
}

_API_URL  = "https://trafficnz.info/service/traffic/rest/4/cameras/all"
_IMG_BASE = "https://trafficnz.info"


def fetch(config: dict) -> list[dict]:
    log("Fetching NZ Traffic Cameras (trafficnz.info)...")

    try:
        resp = requests.get(
            _API_URL,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=config.get("TIMEOUT", 15),
        )
        resp.raise_for_status()
        cams = resp.json().get("response", {}).get("camera", [])
    except Exception as exc:
        log(f"Fetch failed: {exc}", "ERROR")
        return []

    features = []
    skipped  = 0

    for cam in cams:
        try:
            # Skip cameras that are offline or under maintenance
            if cam.get("offline") or cam.get("underMaintenance"):
                skipped += 1
                continue

            lat = float(cam.get("latitude") or 0)
            lon = float(cam.get("longitude") or 0)
            if lat == 0.0 and lon == 0.0:
                skipped += 1
                continue

            img_path = cam.get("imageUrl") or ""
            if not img_path:
                skipped += 1
                continue

            # imageUrl is a relative path e.g. "/camera/714.jpg"
            feed_url = _IMG_BASE + img_path

            cam_id  = str(cam.get("id", "unknown"))
            name    = cam.get("name") or f"NZ {cam_id}"
            region  = (cam.get("region") or {}).get("name", "New Zealand")
            highway = cam.get("highway", "")

            features.append(build_feature(
                cam_id    = cam_id,
                name      = name,
                lat       = lat,
                lon       = lon,
                feed_url  = feed_url,
                cam_type  = "traffic",
                city      = region,
                country   = "NZ",
                source    = "nzta",
                route     = highway,
                direction = cam.get("direction", ""),
            ))
        except Exception:
            skipped += 1
            continue

    log(f"NZTA: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
