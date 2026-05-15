"""
Argus — Road511 USA Multi-State Scraper
========================================
Fetches traffic cameras from the Road511 unified API.
API: https://api.road511.com/api/v1/features?type=cameras&jurisdiction={STATE}

Coverage: 20 US states with confirmed working image feeds.
Live video (.m3u8) is only set for stream domains that pass browser CORS checks.
States with no usable feed URLs (TX, NC, WI, GA, NV, PA, MI, ID, LA, MS,
CT, ME, NH, WV, VT) are excluded automatically.

States NOT in the default run (covered by dedicated plugins):
  CA → scrapers/usa/california/caltrans.py
  IA → scrapers/usa/iowa/iowa511.py
  NY → scrapers/usa/new_york/nyc_dot.py

To target specific states (including normally-skipped ones):
  python engine.py --plugins road511_usa --states CA NY TX
"""

import requests
import time
from scrapers.utils import log, build_feature, HEADERS

PLUGIN_META = {
    "name":         "Road511 USA (Multi-State)",
    "key_required": False,
    "description":  "Traffic cameras across 20 US states via Road511 (image + confirmed-live HLS)",
}

_API_BASE = "https://api.road511.com/api/v1/features"

# States with confirmed working image feeds in Road511.
# Ordered roughly by camera count (largest first).
_STATES = [
    "FL", "UT", "WA", "OR", "CO", "SC", "IN", "TN",
    "AZ", "KS", "AR", "OH", "KY", "NE", "DE", "MA",
    "WY", "ND", "SD", "MT",
]

# Stream URL host fragments confirmed UN-playable in the browser.
# Add a new entry here when you discover a stream domain that fails;
# the scraper will then store image-only for those cameras rather than
# a broken stream reference.
#
#   trafficwise.org          — Indiana HLS: HTTP 200 but no CORS headers
#   kdot-sfs                 — Kansas  HLS: HTTP 401 auth required
#   actis.idrivearkansas.com — Arkansas HLS: HTTP 403 auth required
#   api.trafficland.com      — Massachusetts: returns JSON metadata, not .m3u8
#
_BROKEN_STREAM_HOSTS = {
    "trafficwise.org",
    "kdot-sfs",
    "actis.idrivearkansas.com",
    "api.trafficland.com",
}


def _fetch_state(state: str, config: dict) -> tuple[list, int, int]:
    """
    Fetch all cameras for one state with automatic pagination.
    Returns (features, n_with_image, n_with_stream).
    """
    delay   = config.get("REQUEST_DELAY", 0.3)
    timeout = config.get("TIMEOUT", 15)
    limit   = 500
    offset  = 0
    features  = []
    n_image   = 0
    n_stream  = 0

    while True:
        params = {
            "type":         "cameras",
            "jurisdiction": state,
            "limit":        limit,
            "offset":       offset,
        }
        try:
            resp = requests.get(_API_BASE, headers=HEADERS, params=params, timeout=timeout)
            if resp.status_code == 429:
                log(f"  [{state}] Rate limited — sleeping 5s...", "WARN")
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log(f"  [{state}] Fetch failed (offset={offset}): {exc}", "ERROR")
            break

        cams = data.get("data") or []
        if not cams:
            break

        for cam in cams:
            try:
                if not cam.get("is_active", True):
                    continue

                lat = float(cam.get("latitude") or 0)
                lon = float(cam.get("longitude") or 0)
                if lat == 0.0 and lon == 0.0:
                    continue

                props     = cam.get("properties") or {}
                image_url = props.get("image_url") or ""
                video_url = props.get("video_url") or ""

                # Drop stream URLs from known-broken hosts (auth required / no CORS / non-stream)
                if video_url and any(h in video_url for h in _BROKEN_STREAM_HOSTS):
                    video_url = ""

                if not image_url and not video_url:
                    continue

                if image_url: n_image  += 1
                if video_url: n_stream += 1

                features.append(build_feature(
                    cam_id     = str(cam.get("id") or cam.get("source_id") or "unknown"),
                    name       = cam.get("name") or f"Road511 {state}",
                    lat        = lat,
                    lon        = lon,
                    feed_url   = image_url,
                    stream_url = video_url,
                    cam_type   = "traffic",
                    city       = props.get("nearby_place") or props.get("county") or state,
                    country    = "US",
                    source     = f"road511_{state.lower()}",
                    route      = cam.get("road_name") or "",
                    direction  = cam.get("direction") or "",
                ))
            except Exception:
                continue

        offset += len(cams)
        time.sleep(delay)

        if not data.get("has_more", False):
            break

    return features, n_image, n_stream


def fetch(config: dict) -> list[dict]:
    """
    Plugin entry point called by engine.py.

    Respects the ROAD511_TARGET_STATES config key set by --states CLI flag,
    which overrides the default state list and bypasses all exclusion rules.
    This lets you target any state including CA, IA, NY:
        python engine.py --plugins road511_usa --states CA NY TX
    """
    target_states = config.get("ROAD511_TARGET_STATES")

    if target_states:
        states_to_run = [s.upper() for s in target_states]
        log(f"Road511 USA: targeting {len(states_to_run)} state(s): {', '.join(states_to_run)}")
    else:
        states_to_run = _STATES
        log(f"Road511 USA: fetching {len(states_to_run)} states...")

    all_features = []
    seen_ids     = set()

    for i, state in enumerate(states_to_run, 1):
        log(f"[{i}/{len(states_to_run)}] {state}...")
        state_features, n_image, n_stream = _fetch_state(state, config)

        added = 0
        for feat in state_features:
            fid = feat["properties"]["id"]
            if fid not in seen_ids:
                seen_ids.add(fid)
                all_features.append(feat)
                added += 1

        detail = f"image={n_image}"
        if n_stream:
            detail += f", live={n_stream}"
        log(f"  → {state}: {added} cameras ({detail})", "OK")

    log(f"Road511 USA done: {len(all_features)} cameras across {len(states_to_run)} states", "OK")
    return all_features
