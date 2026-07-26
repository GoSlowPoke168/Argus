#!/usr/bin/env python3
"""
Argus — SQLite camera store (system of record)
==============================================
Replaces the "one giant in-memory geojson" model. Scrapers upsert into SQLite;
`public/cameras.geojson`, `public/summary.json` and the vector tiles are all
*generated exports* off this DB.

Kept intentionally small: known feature fields become columns, everything else
(highway/route/region/…) rides along in a JSON `props_extra` column so we never
lose a source's custom properties.
"""

import os
import json
import tempfile
import time
import sqlite3
from urllib.parse import urlparse

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cameras.db")

# Cameras per detail chunk (see export_compact). Small enough that opening a camera
# costs one ~100KB request, large enough to keep the file count in the hundreds.
DETAIL_CHUNK = 1000

# Feature property keys that get their own column. Everything else in a
# feature's properties is preserved in props_extra (JSON).
_COLUMN_PROPS = {
    "name", "type", "city", "country", "feedUrl", "playerUrl",
    "streamUrl", "feedType", "source", "directEligible", "updateRate",
}

# ── Domains historically confirmed embeddable (mirrors src/App.tsx
# WORKING_IMAGE_DOMAINS). Used ONLY to infer direct_eligible when migrating
# pre-existing cameras that predate the flag, so display behavior is preserved.
_LEGACY_WORKING_DOMAINS = [
    "drivebc.ca", "cwwp2.dot.ca.gov", "images.data.gov.sg", "imgproxy.windy.com",
    "webcams.nyctmc.org", "nzta.govt.nz", "tfl.gov.uk", "amazonaws.com", "cloudfront.net",
    "fl511.com", "udottraffic.utah.gov", "511ny.org", "wsdot.wa.gov", "carsprogram.org",
    "tripcheck.com", "skyvdn.com", "tnsnapshots.com", "az511.com", "idrivearkansas.com",
    "iowadot.gov", "dot.state.oh.us", "nebraska.gov", "deldot.gov", "kcscout.net",
    "trimarc.org", "wyoroad.info", "dot.nd.gov", "streamlock.net", "iteris-atis.com",
    "trafficnz.info",
]


def _origin_host(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _legacy_direct_eligible(feed_url: str, stream_url: str) -> bool:
    """Mirror of the old src/App.tsx isFeedWorking(): a stream, or an
    allowlisted image host, counts as displayable."""
    if stream_url and stream_url.strip():
        return True
    if not feed_url:
        return False
    return any(d in feed_url for d in _LEGACY_WORKING_DOMAINS)


# ─────────────────────────────────────────────────────────
# Connection / schema
# ─────────────────────────────────────────────────────────
def connect(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cameras (
            id                   TEXT PRIMARY KEY,
            name                 TEXT,
            type                 TEXT,
            city                 TEXT,
            country              TEXT,
            feed_url             TEXT,
            player_url           TEXT,
            stream_url           TEXT,
            feed_type            TEXT,
            source               TEXT,
            lon                  REAL,
            lat                  REAL,
            direct_eligible      INTEGER DEFAULT 0,
            update_rate          INTEGER,
            origin_host          TEXT,
            props_extra          TEXT,
            first_seen           TEXT,
            last_seen            TEXT,
            consecutive_failures INTEGER DEFAULT 0,
            deactivated_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cameras_source  ON cameras(source);
        CREATE INDEX IF NOT EXISTS idx_cameras_country ON cameras(country);
        CREATE INDEX IF NOT EXISTS idx_cameras_host    ON cameras(origin_host);
        """
    )
    conn.commit()


# ─────────────────────────────────────────────────────────
# Ingest
# ─────────────────────────────────────────────────────────
def _row_from_feature(feat: dict, now: str) -> dict:
    props = dict(feat.get("properties", {}))
    lon, lat = (feat.get("geometry", {}).get("coordinates", [None, None]) + [None, None])[:2]

    feed_url = props.get("feedUrl", "") or ""
    stream_url = props.get("streamUrl", "") or ""

    # A source may set directEligible explicitly (opencctv uses force_direct).
    # When absent (all native scrapers), infer from the legacy allowlist so
    # display behavior matches the pre-flag app exactly.
    if "directEligible" in props:
        direct = 1 if props.get("directEligible") else 0
    else:
        direct = 1 if _legacy_direct_eligible(feed_url, stream_url) else 0

    update_rate = props.get("updateRate")
    try:
        update_rate = int(update_rate) if update_rate is not None else None
    except (TypeError, ValueError):
        update_rate = None

    extra = {k: v for k, v in props.items() if k not in _COLUMN_PROPS and k != "id"}

    return {
        "id":              props.get("id"),
        "name":            props.get("name"),
        "type":            props.get("type"),
        "city":            props.get("city"),
        "country":         props.get("country"),
        "feed_url":        feed_url,
        "player_url":      props.get("playerUrl", ""),
        "stream_url":      stream_url,
        "feed_type":       props.get("feedType"),
        "source":          props.get("source"),
        "lon":             lon,
        "lat":             lat,
        "direct_eligible": direct,
        "update_rate":     update_rate,
        "origin_host":     _origin_host(feed_url),
        "props_extra":     json.dumps(extra, ensure_ascii=False) if extra else None,
        "last_seen":       now,
    }


def upsert_features(conn: sqlite3.Connection, features: list, commit: bool = True) -> tuple:
    """Insert or update features by id. Returns (added, updated).

    `commit=False` lets a caller batch this with other store ops (e.g.
    delete_sources + dedupe_prefer_native) into one atomic transaction, so a
    killed process rolls back the whole batch instead of leaving it half-applied."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    added = updated = 0
    cur = conn.cursor()
    for feat in features:
        row = _row_from_feature(feat, now)
        if not row["id"]:
            continue
        exists = cur.execute("SELECT 1 FROM cameras WHERE id=?", (row["id"],)).fetchone()
        if exists:
            cur.execute(
                """UPDATE cameras SET name=?, type=?, city=?, country=?, feed_url=?,
                       player_url=?, stream_url=?, feed_type=?, source=?, lon=?, lat=?,
                       direct_eligible=?, update_rate=?, origin_host=?, props_extra=?,
                       last_seen=?, consecutive_failures=0, deactivated_at=NULL
                   WHERE id=?""",
                (row["name"], row["type"], row["city"], row["country"], row["feed_url"],
                 row["player_url"], row["stream_url"], row["feed_type"], row["source"],
                 row["lon"], row["lat"], row["direct_eligible"], row["update_rate"],
                 row["origin_host"], row["props_extra"], row["last_seen"], row["id"]),
            )
            updated += 1
        else:
            cur.execute(
                """INSERT INTO cameras (id, name, type, city, country, feed_url, player_url,
                       stream_url, feed_type, source, lon, lat, direct_eligible, update_rate,
                       origin_host, props_extra, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"], row["name"], row["type"], row["city"], row["country"],
                 row["feed_url"], row["player_url"], row["stream_url"], row["feed_type"],
                 row["source"], row["lon"], row["lat"], row["direct_eligible"],
                 row["update_rate"], row["origin_host"], row["props_extra"],
                 row["last_seen"], row["last_seen"]),
            )
            added += 1
    if commit:
        conn.commit()
    return added, updated


def delete_sources(conn: sqlite3.Connection, sources: list, commit: bool = True) -> int:
    """Delete every camera whose source is in `sources` (replace-source mode).
    Supports trailing '*' prefix matches (e.g. 'opencctv_*').

    `commit=False` — see upsert_features."""
    removed = 0
    cur = conn.cursor()
    for src in sources:
        if src.endswith("*"):
            cur.execute("DELETE FROM cameras WHERE source LIKE ?", (src[:-1] + "%",))
        else:
            cur.execute("DELETE FROM cameras WHERE source=?", (src,))
        removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    if commit:
        conn.commit()
    return removed


def clear_all(conn: sqlite3.Connection, commit: bool = True) -> int:
    cur = conn.cursor()
    n = cur.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    cur.execute("DELETE FROM cameras")
    if commit:
        conn.commit()
    return n


def _feed_key(feed_url: str, origin_host: str) -> str:
    """Identity key for a physical feed: host + path (query/scheme ignored)."""
    if not feed_url:
        return ""
    try:
        path = urlparse(feed_url).path.lower().rstrip("/")
    except Exception:
        return ""
    if not path:
        return ""
    return (origin_host or "") + path


def dedupe_prefer_native(conn: sqlite3.Connection, commit: bool = True) -> int:
    """Drop opencctv_* cameras whose underlying feed matches a native (non-opencctv)
    camera — Argus's own scrapers are authoritative for the sources they cover.

    `commit=False` — see upsert_features."""
    rows = conn.execute("SELECT id, source, feed_url, origin_host FROM cameras").fetchall()
    native_keys = set()
    for r in rows:
        if not (r["source"] or "").startswith("opencctv_"):
            k = _feed_key(r["feed_url"], r["origin_host"])
            if k:
                native_keys.add(k)
    to_delete = []
    for r in rows:
        if (r["source"] or "").startswith("opencctv_"):
            k = _feed_key(r["feed_url"], r["origin_host"])
            if k and k in native_keys:
                to_delete.append((r["id"],))
    if to_delete:
        conn.executemany("DELETE FROM cameras WHERE id=?", to_delete)
        if commit:
            conn.commit()
    return len(to_delete)


# ─────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────
def _atomic_write_json(path: str, obj, **dump_kwargs):
    """Write JSON to `path` via a temp file + atomic rename, so a process killed
    mid-write leaves the previous export intact instead of a truncated file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, **dump_kwargs)
        # On Windows, something (observed: AV/indexer scanning a just-written
        # multi-MB temp file) can hold an exclusive handle in this directory
        # for over a minute; os.replace() raises PermissionError until the
        # scan finishes on its own. Retry with backoff for up to ~2 minutes —
        # this is a one-shot export, so waiting longer is cheap and
        # correctness matters more than speed.
        max_attempts = 40
        for attempt in range(max_attempts):
            try:
                os.replace(tmp_path, path)
                if attempt > 0:
                    print(f"  [INFO] {os.path.basename(path)}: replaced after "
                          f"{attempt} retr{'y' if attempt == 1 else 'ies'} (lock cleared)")
                break
            except PermissionError:
                if attempt == 0:
                    print(f"  [WARN] {os.path.basename(path)}: locked by another "
                          f"process, retrying for up to 2 minutes...")
                if attempt == max_attempts - 1:
                    raise
                time.sleep(3)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _feature_from_row(row: sqlite3.Row) -> dict:
    props = {
        "id":       row["id"],
        "name":     row["name"],
        "type":     row["type"],
        "city":     row["city"],
        "country":  row["country"],
        "feedUrl":  row["feed_url"] or "",
        "playerUrl": row["player_url"] or "",
        "streamUrl": row["stream_url"] or "",
        "feedType": row["feed_type"],
        "source":   row["source"],
        "directEligible": bool(row["direct_eligible"]),
    }
    if row["update_rate"] is not None:
        props["updateRate"] = row["update_rate"]
    if row["props_extra"]:
        try:
            props.update(json.loads(row["props_extra"]))
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
        "properties": props,
    }


def export_geojson(conn: sqlite3.Connection, path: str) -> int:
    rows = conn.execute("SELECT * FROM cameras").fetchall()
    features = [_feature_from_row(r) for r in rows]

    per_source = {}
    for r in rows:
        per_source[r["source"]] = per_source.get(r["source"], 0) + 1

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "generated":         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_cameras":     len(features),
            "sources":           sorted(per_source),
            "per_source_counts": per_source,
        },
        "features": features,
    }
    # Minified: this is a generated artifact (gitignored, consumed by the browser
    # and by tippecanoe), never hand-read or diffed — indentation is pure bloat.
    _atomic_write_json(path, geojson, separators=(",", ":"))
    return len(features)


def export_compact(conn: sqlite3.Connection, path: str) -> int:
    """Split parallel-array payload the frontend loads instead of the geojson.

    Parallel arrays (no repeated JSON keys) with dictionary-encoded source/country
    keep this far smaller than geojson and let deck.gl render from flat arrays
    without materializing one object per camera. It is written as three tiers,
    because ~80% of a single combined payload is per-camera strings that only
    matter for the one camera the user opens:

      cameras.core.json      `path`  — coordinates + small codes. Blocks first paint.
      cameras.labels.json            — name/city, for the hover tooltip. Loads behind core.
      cameras.detail/<n>.json        — id/feed/stream/route/updateRate, DETAIL_CHUNK
                                       cameras per file, fetched when one is opened.

    Returns the camera count.
    """
    rows = conn.execute(
        "SELECT id,name,city,country,feed_url,stream_url,source,lon,lat,"
        "direct_eligible,update_rate,feed_type,props_extra FROM cameras"
    ).fetchall()

    lon, lat, de, live, ur = [], [], [], [], []
    ids, names, feed, stream, route = [], [], [], [], []
    src_idx, cc_idx, ft_idx, city_idx = [], [], [], []
    src_dict, cc_dict, ft_dict, city_dict = {}, {}, {}, {}

    def _di(d, val):
        val = val or ""
        if val not in d:
            d[val] = len(d)
        return d[val]

    for r in rows:
        lon.append(round(r["lon"], 5) if r["lon"] is not None else 0)
        lat.append(round(r["lat"], 5) if r["lat"] is not None else 0)
        de.append(1 if r["direct_eligible"] else 0)
        ur.append(r["update_rate"] or 0)
        ids.append(r["id"])
        names.append(r["name"] or "")
        feed.append(r["feed_url"] or "")
        stream.append(r["stream_url"] or "")
        # The map only needs to know a stream exists (live vs static styling); the
        # URL itself rides along in the detail chunk.
        live.append(1 if r["stream_url"] else 0)
        city_idx.append(_di(city_dict, r["city"]))
        src_idx.append(_di(src_dict, r["source"]))
        cc_idx.append(_di(cc_dict, r["country"]))
        ft_idx.append(_di(ft_dict, r["feed_type"]))
        rt = ""
        if r["props_extra"]:
            try:
                ex = json.loads(r["props_extra"])
                rt = ex.get("route") or (f"HWY {ex['highway']}" if ex.get("highway") else "")
            except (json.JSONDecodeError, TypeError):
                pass
        route.append(rt)

    count = len(ids)
    generated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _keys(d):
        return [k for k, _ in sorted(d.items(), key=lambda x: x[1])]

    _atomic_write_json(path, {
        "count":     count,
        "generated": generated,
        "chunk":     DETAIL_CHUNK,
        "srcDict":   _keys(src_dict),
        "ccDict":    _keys(cc_dict),
        "ftDict":    _keys(ft_dict),
        "lon": lon, "lat": lat, "de": de, "live": live,
        "src": src_idx, "cc": cc_idx, "ft": ft_idx,
    }, separators=(",", ":"))

    directory = os.path.dirname(os.path.abspath(path))
    _atomic_write_json(os.path.join(directory, "cameras.labels.json"), {
        "count": count, "generated": generated,
        "cityDict": _keys(city_dict),
        "name": names, "city": city_idx,
    }, separators=(",", ":"))

    detail_dir = os.path.join(directory, "cameras.detail")
    os.makedirs(detail_dir, exist_ok=True)
    n_chunks = (count + DETAIL_CHUNK - 1) // DETAIL_CHUNK
    for n in range(n_chunks):
        lo, hi = n * DETAIL_CHUNK, min((n + 1) * DETAIL_CHUNK, count)
        _atomic_write_json(os.path.join(detail_dir, f"{n}.json"), {
            "from": lo,
            "id": ids[lo:hi], "feed": feed[lo:hi], "stream": stream[lo:hi],
            "route": route[lo:hi], "ur": ur[lo:hi],
        }, separators=(",", ":"))
    # Drop chunks left over from a larger previous export, or they'd be served
    # alongside the new ones and hand the frontend retired cameras.
    for stale in os.listdir(detail_dir):
        name, ext = os.path.splitext(stale)
        if ext == ".json" and name.isdigit() and int(name) >= n_chunks:
            os.remove(os.path.join(detail_dir, stale))

    return count


def export_summary(conn: sqlite3.Connection, path: str) -> dict:
    """Small precomputed rollups so the frontend never iterates all features.
    Country counts are keyed by raw code; the frontend maps codes -> display names."""
    total = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    live = conn.execute("SELECT COUNT(*) FROM cameras WHERE stream_url != ''").fetchone()[0]
    by_country = {r["country"]: r["n"] for r in conn.execute(
        "SELECT country, COUNT(*) n FROM cameras GROUP BY country")}
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM cameras GROUP BY source")}
    by_category = {r["type"]: r["n"] for r in conn.execute(
        "SELECT type, COUNT(*) n FROM cameras GROUP BY type")}

    summary = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total,
        "live": live,
        "static": total - live,
        "byCountry": by_country,
        "bySource": by_source,
        "byCategory": by_category,
    }
    _atomic_write_json(path, summary, indent=2)
    return summary


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    live = conn.execute("SELECT COUNT(*) FROM cameras WHERE stream_url != ''").fetchone()[0]
    direct = conn.execute("SELECT COUNT(*) FROM cameras WHERE direct_eligible=1").fetchone()[0]
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM cameras GROUP BY source ORDER BY n DESC")}
    return {"total": total, "live": live, "direct_eligible": direct, "by_source": by_source}


# ─────────────────────────────────────────────────────────
# Migration: seed DB from an existing geojson
# ─────────────────────────────────────────────────────────
def import_geojson(conn: sqlite3.Connection, path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    features = data.get("features", [])
    added, updated = upsert_features(conn, features)
    return added + updated


if __name__ == "__main__":
    import sys
    # tiny CLI: python store.py import <geojson> | export <geojson> | stats
    if len(sys.argv) >= 2 and sys.argv[1] == "import" and len(sys.argv) >= 3:
        c = connect()
        n = import_geojson(c, sys.argv[2])
        print(f"Imported {n:,} cameras into {DEFAULT_DB}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "export" and len(sys.argv) >= 3:
        c = connect()
        n = export_geojson(c, sys.argv[2])
        print(f"Exported {n:,} cameras to {sys.argv[2]}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "stats":
        c = connect()
        print(json.dumps(stats(c), indent=2))
    else:
        print("usage: store.py [import <geojson> | export <geojson> | stats]")
