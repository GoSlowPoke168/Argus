import requests
from scrapers.utils import log, build_feature, HEADERS

# Iowa DOT Traffic Cameras via ArcGIS FeatureServer
# ~1,244 cameras statewide with JPEG snapshots and HLS live streams.
_URL = (
    "https://services.arcgis.com/8lRhdTsQyJpO52F1/ArcGIS/rest/services"
    "/Traffic_Cameras_View/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&f=pjson"
)


def fetch(config) -> list[dict]:
    features = []

    log("Fetching Iowa DOT Traffic Cameras (ArcGIS, no key)...")
    try:
        resp = requests.get(_URL, headers=HEADERS, timeout=config.get("TIMEOUT", 20))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log(f"Iowa DOT fetch failed: {e}", "ERROR")
        return []

    raw_features = data.get("features", [])
    if not raw_features:
        log("Iowa DOT: no features returned.", "WARN")
        return []

    skipped = 0
    for item in raw_features:
        try:
            attr = item.get("attributes", {})

            # Prefer the explicit lat/lon attribute fields; fall back to geometry
            lat = attr.get("latitude") or attr.get("lat")
            lon = attr.get("longitude") or attr.get("long")

            if lat is None or lon is None:
                skipped += 1
                continue

            lat = float(lat)
            lon = float(lon)
            if lat == 0 and lon == 0:
                skipped += 1
                continue

            cam_id    = str(attr.get("device_id") or attr.get("FID", "unknown"))
            name      = attr.get("Desc_") or attr.get("ImageName") or f"Iowa Camera {cam_id}"
            image_url = attr.get("ImageURL", "")
            video_url = attr.get("VideoURL", "") or attr.get("VideoUR", "")
            route     = attr.get("Route", "")

            features.append(build_feature(
                cam_id     = cam_id,
                name       = name,
                lat        = lat,
                lon        = lon,
                feed_url   = image_url,
                stream_url = video_url,
                cam_type   = "traffic",
                city       = route or "Iowa",
                country    = "US",
                source     = "iowa_dot",
                route      = route,
            ))
        except Exception:
            skipped += 1
            continue

    log(f"Iowa DOT: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features
