import requests, time
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    DISTRICTS = range(1, 13)
    features = []
    total_skipped = 0

    log("Fetching Caltrans California CCTV (all 12 districts, no key)...")
    for district in DISTRICTS:
        url = f"https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02d}.json"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
            resp.raise_for_status()
            cctv_list = resp.json().get("data", [])
        except Exception as e:
            log(f"  Caltrans D{district:02d} failed: {e}", "WARN")
            continue

        skipped = 0
        for item in cctv_list:
            try:
                cam = item.get("cctv", {})
                if cam.get("inService", "true") == "false":
                    skipped += 1
                    continue

                loc = cam.get("location", {})
                lat, lon = float(loc.get("latitude", 0)), float(loc.get("longitude", 0))
                if lat == 0 and lon == 0:
                    skipped += 1
                    continue

                img_data = cam.get("imageData", {})
                image_url = img_data.get("static", {}).get("currentImageURL", "")
                stream_url = img_data.get("streamingVideoURL", "")
                
                features.append(build_feature(
                    cam_id=f"d{district:02d}_{cam.get('index', 'x')}",
                    name=loc.get("locationName", f"Caltrans D{district:02d}"),
                    lat=lat, lon=lon, feed_url=image_url, stream_url=stream_url,
                    cam_type="traffic", city=loc.get("nearbyPlace", ""), country="US",
                    source="caltrans", route=loc.get("route", "")
                ))
            except Exception:
                skipped += 1
                continue

        total_skipped += skipped
        time.sleep(config.get("REQUEST_DELAY", 0.1))

    log(f"Caltrans: {len(features)} total CA cameras ({total_skipped} skipped)", "OK")
    return features
