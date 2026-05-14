import requests, time
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    DISTRICTS = range(1, 13)
    features = []
    total_skipped = 0
    
    # Enhanced headers for better server compatibility
    ENHANCED_HEADERS = {
        **HEADERS,
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache"
    }

    log("Fetching Caltrans California CCTV (all 12 districts, no key)...")
    for district in DISTRICTS:
        url = f"https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02d}.json"
        cctv_list = None
        
        # Retry logic with exponential backoff
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=ENHANCED_HEADERS, timeout=config.get("TIMEOUT", 10))
                resp.raise_for_status()
                cctv_list = resp.json().get("data", [])
                break
            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s backoff
                    continue
                log(f"  Caltrans D{district:02d} timeout after retries", "WARN")
                continue
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                log(f"  Caltrans D{district:02d} failed: {e}", "WARN")
                continue
        
        if cctv_list is None:
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
