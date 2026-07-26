"""
Argus — Windy Webcams Global Scraper (Unified)
==============================================
Single-pass recursive grid scraper that combines the old fetch_windy_massive.py
and fetch_windy_dense.py into one plugin.

Phase 1: Scans the entire globe with a configurable grid (default 20°×20°).
Phase 2: Any box returning ≥ SATURATION_THRESHOLD cameras is automatically
         subdivided into 4 quadrants until it's drained or MAX_DEPTH is reached.

Boxes are fetched concurrently, one level of the subdivision tree at a time
(breadth-first) via a bounded thread pool. A shared rate limiter caps the
*aggregate* request rate across all workers so we stay a polite API guest
regardless of the worker count.

Config keys (all optional, set in scraper.py CONFIG):
    WINDY_API_KEY             — required, set in .env
    WINDY_GRID_SIZE           — degrees per grid cell (default: 20)
    WINDY_SATURATION_THRESHOLD— cameras count that triggers subdivision (default: 999)
    WINDY_MAX_DEPTH           — max recursion depth (default: 5, ~0.6° boxes at depth 5)
    WINDY_BATCH_SIZE          — cameras per API request (default: 50, free-tier max)
    WINDY_MAX_WORKERS         — concurrent boxes (default: 6)
    WINDY_RATE_LIMIT_RPS      — aggregate requests/sec cap across all workers (default: 8)
    TIMEOUT                   — request timeout seconds (default: 15)
"""

import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from scrapers.utils import log, log_progress, build_feature, HEADERS

# Refresh the progress bar every N boxes completed within a level.
_PROGRESS_EVERY = 8


class _RateLimiter:
    """Thread-safe aggregate rate cap. Each caller reserves the next time-slot
    under a short lock, then sleeps toward it *outside* the lock so workers
    still overlap network latency while total throughput stays ≤ 1/min_interval."""

    def __init__(self, min_interval: float):
        self._min = min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._min
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

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


def _fetch_box(n, e, s, w, api_key: str, config: dict, limiter: "_RateLimiter") -> tuple[list, int]:
    """
    Fetch all cameras in the bounding box via pagination.
    Windy free tier: max 50 per request, max offset 1000.

    Pagination within a box is inherently sequential (offset chases the previous
    page); concurrency comes from running many boxes at once. `limiter.wait()`
    before each request enforces the shared aggregate rate cap.

    Returns: (list_of_features, total_fetched_count)
    """
    batch_size = config.get("WINDY_BATCH_SIZE", 50)
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
            limiter.wait()
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

        if len(webcams) < batch_size:
            break  # End of results for this box

    return box_features, total_fetched


def _commit_box(box, cameras, total_fetched, seen_ids: set, all_features: list):
    """A non-saturated (or max-depth) box: add its cameras, deduping by ID."""
    n, e, s, w, depth = box
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


def _quadrants(box) -> list:
    """Split a box into its 4 quadrants (NW, NE, SW, SE) at the next depth."""
    n, e, s, w, depth = box
    mid_lat = (n + s) / 2
    mid_lon = (e + w) / 2
    return [
        (n,       mid_lon, mid_lat, w,       depth + 1),  # NW
        (n,       e,       mid_lat, mid_lon, depth + 1),  # NE
        (mid_lat, mid_lon, s,       w,       depth + 1),  # SW
        (mid_lat, e,       s,       mid_lon, depth + 1),  # SE
    ]


def fetch(config: dict) -> list[dict]:
    """
    Main plugin entry point. Scans the entire globe with a grid of bounding boxes,
    subdividing any box that hits the API cap — breadth-first, one depth level at
    a time, with the boxes in each level fetched concurrently.

    Called by scraper.py — returns a list of GeoJSON Feature dicts.

    A saturated box's own results are discarded (its sub-boxes re-cover the region
    more completely); non-saturated boxes commit their cameras, deduped by ID. This
    is the same crawl set and the same commit/subdivide decisions the previous
    single-threaded version made — only the fetching is now parallel.
    """
    api_key = config.get("WINDY_API_KEY")
    if not api_key:
        log("Skipping Windy (no WINDY_API_KEY in .env).", "WARN")
        return []

    grid        = config.get("WINDY_GRID_SIZE", 20)
    saturation  = config.get("WINDY_SATURATION_THRESHOLD", 999)
    max_depth   = config.get("WINDY_MAX_DEPTH", 5)
    workers     = config.get("WINDY_MAX_WORKERS", 6)
    rps         = config.get("WINDY_RATE_LIMIT_RPS", 8)
    total_boxes = (180 // grid) * (360 // grid)

    # Progress-bar ETA target: what the store already held for this source (a re-run
    # lands near the same count), falling back to a static estimate on a first run.
    prior  = (config.get("_PRIOR_SOURCE_COUNTS") or {}).get("windy")
    target = prior if prior and prior > 0 else config.get("WINDY_EXPECTED_TOTAL", 73000)
    start  = time.monotonic()

    log(f"Windy global scan: {grid}°×{grid}° grid → {total_boxes} root boxes")
    log(f"Saturation threshold: {saturation} | Max depth: {max_depth} | "
        f"Workers: {workers} | Rate cap: {rps}/s")

    limiter = _RateLimiter(1.0 / rps if rps > 0 else 0.0)

    # Root frontier: every grid box at depth 0. Tuple = (n, e, s, w, depth).
    frontier = [
        (lat, lon + grid, lat - grid, lon, 0)
        for lat in range(90, -90, -grid)
        for lon in range(-180, 180, grid)
    ]

    seen_ids     = set()
    all_features = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        level = 0
        while frontier:
            log(f"[level {level}] fetching {len(frontier)} box(es) with {workers} workers…")
            # Submit the whole level, then handle each box as it finishes (as_completed)
            # so logs stream continuously and the progress bar updates mid-level — rather
            # than the console going silent until the entire level is done. Children are
            # only queued for the *next* level, so no task ever waits on another task
            # (deadlock-free) and no shared-state locking is needed.
            futures = {
                executor.submit(_fetch_box, b[0], b[1], b[2], b[3], api_key, config, limiter): b
                for b in frontier
            }
            next_frontier = []
            completed = 0
            for fut in as_completed(futures):
                box = futures[fut]
                try:
                    cameras, total_fetched = fut.result()
                except Exception as exc:
                    log(f"Box {box[:4]} failed: {exc}", "ERROR")
                    cameras, total_fetched = [], 0

                if total_fetched >= saturation and box[4] < max_depth:
                    n, e, s, w, depth = box
                    log(f"  [d={depth}] N:{n:.2f} E:{e:.2f} S:{s:.2f} W:{w:.2f} "
                        f"hit {total_fetched} — subdividing into 4 quadrants...", "WARN")
                    next_frontier.extend(_quadrants(box))
                else:
                    _commit_box(box, cameras, total_fetched, seen_ids, all_features)

                completed += 1
                if completed % _PROGRESS_EVERY == 0:
                    log_progress("windy", len(all_features), target, start, approx=True)

            frontier = next_frontier
            level += 1
            log_progress("windy", len(all_features), target, start, approx=True)

    # Final 100% tick so the bar completes on the count we actually landed.
    log_progress("windy", len(all_features), len(all_features), start)
    log(f"Windy complete: {len(all_features)} unique cameras across {total_boxes} root boxes", "OK")
    return all_features
