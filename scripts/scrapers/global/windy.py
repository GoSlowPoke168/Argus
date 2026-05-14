"""
Argus — Windy Webcams Global Scraper (Unified)
==============================================
Single-pass recursive grid scraper that combines the old fetch_windy_massive.py
and fetch_windy_dense.py into one plugin.

Phase 1: Scans the entire globe with a configurable grid (default 20°×20°).
Phase 2: Any box returning ≥ SATURATION_THRESHOLD cameras is automatically
         recursively subdivided into 4 quadrants until it's drained or MAX_DEPTH
         is reached.

Config keys (all optional, set in engine.py CONFIG):
    WINDY_API_KEY             — required, set in .env
    WINDY_GRID_SIZE           — degrees per grid cell (default: 20)
    WINDY_SATURATION_THRESHOLD— cameras count that triggers subdivision (default: 999)
    WINDY_MAX_DEPTH           — max recursion depth (default: 5, ~0.6° boxes at depth 5)
    WINDY_BATCH_SIZE          — cameras per API request (default: 50, free-tier max)
    REQUEST_DELAY             — seconds between requests (default: 0.5)
    TIMEOUT                   — request timeout seconds (default: 15)
"""

import requests
import time
from scrapers.utils import log, build_feature, HEADERS

PLUGIN_META = {
    "name":        "Windy Webcams (Global)",
    "key_required": True,
    "key_env":     "WINDY_API_KEY",
    "description": "100k+ global cameras via recursive 20°×20° grid with auto-dense subdivision",
}

_API_URL = "https://api.windy.com/webcams/api/v3/webcams"


def _build_headers(api_key: str) -> dict:
    return {**HEADERS, "x-windy-api-key": api_key}


def _parse_cam(cam: dict) -> dict | None:
    """Parse a raw Windy webcam object into a GeoJSON Feature. Returns None on failure."""
    loc = cam.get("location", {})
    lat = float(loc.get("latitude", 0))
    lon = float(loc.get("longitude", 0))
    if lat == 0 and lon == 0:
        return None

    cam_id    = str(cam.get("webcamId", cam.get("id", "unknown")))
    title     = cam.get("title", f"Windy Camera {cam_id}")
    images    = cam.get("images", {})
    image_url = (
        images.get("current", {}).get("preview", "")
        or images.get("daylight", {}).get("preview", "")
        or ""
    )
    player_url = cam.get("urls", {}).get("provider", "") or ""

    return build_feature(
        cam_id=cam_id,
        name=title,
        lat=lat,
        lon=lon,
        feed_url=image_url,
        cam_type="landmark",
        city=loc.get("city", loc.get("region", "")),
        country=loc.get("country_code", ""),
        source="windy",
        player_url=player_url,
    )


def _fetch_box(n, e, s, w, api_key: str, config: dict) -> tuple[list, int]:
    """
    Fetch all cameras in the bounding box via pagination.
    Windy free tier: max 50 per request, max offset 1000.

    Returns: (list_of_features, total_fetched_count)
    """
    batch_size = config.get("WINDY_BATCH_SIZE", 50)
    delay      = config.get("REQUEST_DELAY", 0.5)
    timeout    = config.get("TIMEOUT", 15)
    headers    = _build_headers(api_key)

    offset        = 0
    box_features  = []
    total_fetched = 0

    while offset < 1000:
        params = {
            "limit":   min(batch_size, 1000 - offset),
            "offset":  offset,
            "include": "location,urls,images",
            "bbox":    f"{n},{e},{s},{w}",
        }
        try:
            resp = requests.get(_API_URL, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 429:
                log("Rate limited by Windy — sleeping 10s...", "WARN")
                time.sleep(10)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log(f"Fetch failed [box N:{n} E:{e} S:{s} W:{w}]: {exc}", "ERROR")
            break

        webcams = data.get("webcams", [])
        if not webcams:
            break

        total_fetched += len(webcams)

        for cam in webcams:
            try:
                feature = _parse_cam(cam)
                if feature:
                    box_features.append(feature)
            except Exception:
                continue

        offset += len(webcams)
        time.sleep(delay)

        if len(webcams) < batch_size:
            break  # End of results for this box

    return box_features, total_fetched


def _fetch_recursive(n, e, s, w, depth: int, api_key: str, config: dict,
                     seen_ids: set, all_features: list):
    """
    Fetch cameras from a bounding box, recursively subdividing if saturated.

    If total_fetched >= SATURATION_THRESHOLD and depth < MAX_DEPTH, the box is
    split into 4 equal quadrants (NW, NE, SW, SE) and each is fetched recursively.
    The results from the saturated parent box are discarded — the sub-boxes will
    capture everything more completely.

    If the box is not saturated (or we've hit MAX_DEPTH), its cameras are added
    to all_features, deduplicating by ID.
    """
    saturation = config.get("WINDY_SATURATION_THRESHOLD", 999)
    max_depth  = config.get("WINDY_MAX_DEPTH", 5)

    cameras, total_fetched = _fetch_box(n, e, s, w, api_key, config)

    if total_fetched >= saturation and depth < max_depth:
        mid_lat = (n + s) / 2
        mid_lon = (e + w) / 2
        log(
            f"  [d={depth}] N:{n:.2f} E:{e:.2f} S:{s:.2f} W:{w:.2f} "
            f"hit {total_fetched} — subdividing into 4 quadrants...",
            "WARN"
        )
        # NW quadrant
        _fetch_recursive(n, mid_lon, mid_lat, w,     depth + 1, api_key, config, seen_ids, all_features)
        # NE quadrant
        _fetch_recursive(n, e,       mid_lat, mid_lon, depth + 1, api_key, config, seen_ids, all_features)
        # SW quadrant
        _fetch_recursive(mid_lat, mid_lon, s, w,     depth + 1, api_key, config, seen_ids, all_features)
        # SE quadrant
        _fetch_recursive(mid_lat, e,       s, mid_lon, depth + 1, api_key, config, seen_ids, all_features)
    else:
        # Box is not saturated (or max depth reached) — commit cameras
        added = 0
        for cam in cameras:
            fid = cam["properties"]["id"]
            if fid not in seen_ids:
                seen_ids.add(fid)
                all_features.append(cam)
                added += 1

        if depth > 0 or total_fetched > 0:
            depth_label = f"[d={depth}]" if depth > 0 else ""
            log(
                f"  {depth_label} N:{n:.2f} E:{e:.2f} S:{s:.2f} W:{w:.2f} "
                f"→ {total_fetched} fetched, {added} new unique"
            )


def fetch(config: dict) -> list[dict]:
    """
    Main plugin entry point. Scans the entire globe with a grid of bounding boxes,
    automatically recursively subdividing any box that hits the API cap.

    Called by engine.py — returns a list of GeoJSON Feature dicts.
    """
    api_key = config.get("WINDY_API_KEY")
    if not api_key:
        log("Skipping Windy (no WINDY_API_KEY in .env).", "WARN")
        return []

    grid        = config.get("WINDY_GRID_SIZE", 20)
    total_boxes = (180 // grid) * (360 // grid)
    box_num     = 0

    log(f"Windy global scan: {grid}°×{grid}° grid → {total_boxes} root boxes")
    log(f"Saturation threshold: {config.get('WINDY_SATURATION_THRESHOLD', 999)} | Max depth: {config.get('WINDY_MAX_DEPTH', 5)}")

    seen_ids     = set()
    all_features = []

    for lat in range(90, -90, -grid):
        for lon in range(-180, 180, grid):
            box_num += 1
            n = lat
            s = lat - grid
            w = lon
            e = lon + grid
            log(f"[{box_num}/{total_boxes}] Box N:{n} E:{e} S:{s} W:{w}")
            _fetch_recursive(n, e, s, w, 0, api_key, config, seen_ids, all_features)

    log(f"Windy complete: {len(all_features)} unique cameras across {total_boxes} root boxes", "OK")
    return all_features
