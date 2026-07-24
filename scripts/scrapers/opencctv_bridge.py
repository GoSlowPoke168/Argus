"""
OpenCCTV harvester
==================
Pulls the public camera catalog from opencctv.org and stores the **real
underlying source feed_urls** (never opencctv's own proxy URLs). opencctv is
used purely as a discovery index of sources Argus doesn't yet scrape natively.

Mechanics: `/api/cameras/markers` returns every camera id in one call; then
`POST /api/cameras/batch {"ids":[…≤50…]}` returns full records (feed_url,
feed_type, update_rate, force_direct, …). We fetch all id-chunks with bounded
concurrency and 429 backoff, then map each record to a GeoJSON feature.
(`/api/cameras/list` is not usable — it ignores `offset` and always returns the
same first 100 rows.)

Playability flag (`direct_eligible`): opencctv's `force_direct` means the feed
embeds straight from origin. We additionally refuse:
  - `proxy_zone`/`geo_allow` feeds — they need a residential/geo proxy Argus won't run
  - `iframe` feeds — the current frontend can't embed them
Non-eligible cameras are still stored (mapped, greyed out) for a future proxy phase.
"""

import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from scrapers.utils import log, log_progress, build_feature, HEADERS
from scrapers.ipcamlive_resolver import alias_from_url, resolve as resolve_ipcamlive

PLUGIN_META = {
    "name":         "OpenCCTV Harvester",
    "key_required": False,
    "description":  "Harvests opencctv.org's global catalog (real source feed_urls)",
}

API_MARKERS = "https://opencctv.org/api/cameras/markers"
API_BATCH   = "https://opencctv.org/api/cameras/batch"
BATCH_SIZE  = 50          # server caps batch at 50 ids
MAX_WORKERS = 5           # be a polite guest
STREAM_TYPES = {"m3u8", "mp4"}   # frontend HlsPlayer handles these
IMAGE_TYPES  = {"image", "mjpeg"}  # render in <img>


def _get_ids(timeout: int) -> list:
    """All camera ids in one call (markers is a 7-8MB response)."""
    resp = requests.get(API_MARKERS, headers=HEADERS, timeout=max(timeout, 60))
    resp.raise_for_status()
    return resp.json().get("ids", [])


def _post_batch(ids: list, timeout: int) -> list:
    """Full records for up to 50 ids, with 429 backoff+retry."""
    headers = {**HEADERS, "Content-Type": "application/json"}
    for _ in range(3):
        resp = requests.post(API_BATCH, json={"ids": ids}, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            time.sleep(10)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return []


def _direct_eligible(rec: dict, feed_type: str) -> bool:
    if str(rec.get("force_direct")) != "1":
        return False
    if rec.get("proxy_zone") or rec.get("geo_allow"):
        return False          # needs a paid residential/geo proxy — out of scope
    if feed_type == "iframe":
        return False          # frontend can't embed iframes yet
    return True


def _to_feature(rec: dict) -> dict | None:
    try:
        lat = float(rec.get("lat"))
        lon = float(rec.get("lng"))
    except (TypeError, ValueError):
        return None
    if lat == 0 and lon == 0:
        return None

    feed_url = rec.get("feed_url") or ""
    if not feed_url:
        return None

    ft = (rec.get("feed_type") or "image").lower()
    stream_url = feed_url if ft in STREAM_TYPES else ""

    update_rate = rec.get("update_rate")
    try:
        update_rate = int(update_rate) if update_rate is not None else None
    except (TypeError, ValueError):
        update_rate = None

    city = rec.get("city") or ""
    state = rec.get("state") or ""

    return build_feature(
        cam_id          = str(rec.get("id")),
        name            = rec.get("name", "Unknown Node"),
        lat             = lat,
        lon             = lon,
        feed_url        = feed_url,
        stream_url      = stream_url,
        cam_type        = rec.get("category", "traffic"),
        city            = city or state,
        country         = rec.get("country", "XX"),
        source          = f"opencctv_{rec.get('source', 'generic')}",
        feed_type       = ft,
        direct_eligible = _direct_eligible(rec, ft),
        update_rate     = update_rate,
    )


def _resolve_ipcamlive(features: list, timeout: int) -> None:
    """Rewrite in place any `ipcamlive://<alias>` feature into a real, playable
    https m3u8 (concurrently). Unresolved (offline) cameras keep the placeholder
    and stay greyed out. ipcamlive streams are CORS-open, so these play directly."""
    targets = [f for f in features
               if str(f["properties"].get("feedUrl", "")).startswith("ipcamlive://")]
    if not targets:
        return

    log(f"OpenCCTV: resolving {len(targets):,} ipcamlive:// streams to real m3u8...")

    def _one(feat):
        alias = alias_from_url(feat["properties"].get("feedUrl", ""))
        return feat, resolve_ipcamlive(alias, timeout)

    resolved = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_one, f) for f in targets]
        for fut in as_completed(futures):
            feat, m3u8 = fut.result()
            if m3u8:
                p = feat["properties"]
                p["feedUrl"] = m3u8
                p["streamUrl"] = m3u8
                p["feedType"] = "m3u8"
                p["directEligible"] = True
                resolved += 1

    log(f"OpenCCTV: resolved {resolved:,}/{len(targets):,} ipcamlive streams "
        f"({len(targets) - resolved:,} offline)", "OK")


def fetch(config: dict) -> list[dict]:
    timeout = config.get("TIMEOUT", 15)
    # Optional cap for testing: CONFIG["OPENCCTV_MAX"] limits how many cameras we pull.
    max_cams = config.get("OPENCCTV_MAX")

    log("OpenCCTV: fetching camera id index (markers)...")
    ids = _get_ids(timeout)
    if max_cams:
        ids = ids[:int(max_cams)]
    chunks = [ids[i:i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]
    log(f"OpenCCTV: {len(ids):,} ids → {len(chunks):,} batch requests")

    records = []
    done = 0
    total_chunks = len(chunks)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_post_batch, ch, timeout): i for i, ch in enumerate(chunks)}
        for fut in as_completed(futures):
            try:
                records.extend(fut.result())
            except Exception as e:
                log(f"OpenCCTV: a batch failed: {e}", "WARN")
            done += 1
            if done % 50 == 0 or done == total_chunks:
                log_progress("opencctv", done, total_chunks, start)

    features = []
    seen = set()
    for rec in records:
        cid = rec.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        feat = _to_feature(rec)
        if feat:
            features.append(feat)

    _resolve_ipcamlive(features, timeout)

    direct = sum(1 for f in features if f["properties"].get("directEligible"))
    log(f"OpenCCTV: {len(features):,} cameras mapped ({direct:,} direct-displayable)", "OK")
    return features
