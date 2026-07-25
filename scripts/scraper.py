#!/usr/bin/env python3
"""
Argus — Camera Data Pipeline
==============================
Single entry point for all scraper plugins. Supports plugin selection,
upsert/replace-source/fresh merge modes, parallel execution, and per-state
Road511 targeting.

Usage:
  python scraper.py --list
  python scraper.py --stats
  python scraper.py --all
  python scraper.py --plugins windy drivebc
  python scraper.py --all --exclude windy
  python scraper.py --plugins caltrans --replace-source
  python scraper.py --all --fresh
  python scraper.py --all --output ../public/cameras.geojson
  python scraper.py --all --parallel
  python scraper.py --plugins road511_usa --states CO TN DE
"""

import os
import sys
import importlib
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.utils import log, log_progress
import store


# ─────────────────────────────────────────────────────────
# PLUGIN REGISTRY
# ─────────────────────────────────────────────────────────
PLUGIN_REGISTRY = {
    "windy": {
        "module":      "scrapers.global.windy",
        "name":        "Windy Webcams (Global)",
        "key":         "WINDY_API_KEY",
        "description": "73k+ global cameras via recursive 20°×20° grid with auto-dense subdivision",
    },
    "caltrans": {
        "module":      "scrapers.usa.california.caltrans",
        "name":        "Caltrans California",
        "key":         None,
        "description": "~3,300 California highway cameras with HLS live streams",
    },
    "nyc_dot": {
        "module":      "scrapers.usa.new_york.nyc_dot",
        "name":        "NYC DOT",
        "key":         None,
        "description": "~950 New York City traffic cameras",
    },
    "drivebc": {
        "module":      "scrapers.canada.bc.drivebc",
        "name":        "DriveBC BC",
        "key":         None,
        "description": "~1,040 British Columbia highway cameras",
    },
    "singapore_lta": {
        "module":      "scrapers.asia.singapore.lta",
        "name":        "Singapore LTA",
        "key":         None,
        "description": "~90 Singapore urban traffic cameras",
    },
    "tfl_london": {
        "module":      "scrapers.europe.uk.tfl_london",
        "name":        "TfL London JamCam",
        "key":         None,
        "description": "~800 London traffic cameras",
    },
    "nzta": {
        "module":      "scrapers.oceania.nz.nzta",
        "name":        "NZTA New Zealand",
        "key":         None,
        "description": "257 NZ highway cameras via trafficnz.info",
    },
    "iowa_dot": {
        "module":      "scrapers.usa.iowa.iowa511",
        "name":        "Iowa DOT",
        "key":         None,
        "description": "~850 Iowa highway cameras with JPEG snapshots and HLS streams (ArcGIS)",
    },
    "road511_usa": {
        "module":      "scrapers.usa.road511",
        "name":        "Road511 USA (Multi-State)",
        "key":         None,
        "description": "~15,000 cameras across 20 US states. Use --states CO TN DE to target specific states.",
    },
    "opencctv_bridge": {
        "module":      "scrapers.opencctv_bridge",
        "name":        "OpenCCTV Strategic Bridge",
        "key":         None,
        "description": "Synchronizes 200k+ global nodes from the OpenCCTV network",
    },
}


# ─────────────────────────────────────────────────────────
# SOURCE MAP  (plugin alias → geojson source field values)
# Used by replace-source mode to know which cameras to drop.
# ─────────────────────────────────────────────────────────
def _build_source_map(states_override=None):
    src_map = {
        "windy":         "windy",
        "caltrans":      "caltrans",
        "nyc_dot":       "nyc_dot",
        "drivebc":       "drivebc",
        "singapore_lta": "singapore",
        "tfl_london":    "tfl_london",
        "nzta":          "nzta",
        "iowa_dot":      "iowa_dot",
        "opencctv_bridge": "opencctv_*", # trailing * = prefix delete (opencctv_<src>)
    }
    _all_road511_states = [
        "fl","ga","co","in","ut","nv","wa","pa","or","mi","ky","sc","ma","tn",
        "id","az","ks","ar","oh","la","ms","ne","ct","de","wy","me","nh","nd",
        "wv","vt","sd","mt","ca","ia","ny",
    ]
    road511_states = (
        [s.lower() for s in states_override]
        if states_override else _all_road511_states
    )
    src_map["road511_usa"] = [f"road511_{s}" for s in road511_states]
    return src_map


# ─────────────────────────────────────────────────────────
# ENVIRONMENT / CONFIG
# ─────────────────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if not os.path.exists(env_path):
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


_load_env()

CONFIG = {
    "WINDY_API_KEY":              os.getenv("WINDY_API_KEY"),
    "OPENCCTV_MAX":               os.getenv("OPENCCTV_MAX"),  # cap harvest size (testing)
    "TIMEOUT":                    15,
    "REQUEST_DELAY":              0.5,
    "WINDY_GRID_SIZE":            20,
    "WINDY_SATURATION_THRESHOLD": 999,
    "WINDY_MAX_DEPTH":            5,
    "WINDY_BATCH_SIZE":           50,
    "WINDY_MAX_WORKERS":          6,    # concurrent boxes (mirrors opencctv's polite pool)
    "WINDY_RATE_LIMIT_RPS":       8,    # aggregate request/sec cap across all workers
    "WINDY_EXPECTED_TOTAL":       73000,  # progress-bar ETA target when no prior count exists
}


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────
def _parse_args():
    parser = argparse.ArgumentParser(
        prog="scraper.py",
        description="Argus Camera Data Pipeline — unified scraper for all sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Update Modes:
  (default)        Upsert — refresh known cameras by ID, add new ones.
                   Cameras from sources NOT run this time are kept as-is.
  --replace-source Drop all old cameras from the sources being run, then
                   insert fresh results. Use to remove stale/offline cameras.
  --fresh          Ignore the existing file entirely. Write only what was
                   just fetched. Use to nuke and rebuild from scratch.

Examples:
  python scraper.py --all
  python scraper.py --plugins windy drivebc
  python scraper.py --all --exclude windy
  python scraper.py --plugins caltrans drivebc --replace-source
  python scraper.py --all --fresh
  python scraper.py --all --output ../public/cameras.geojson --parallel
  python scraper.py --list
  python scraper.py --stats
  python scraper.py --plugins road511_usa --states CO TN DE
  python scraper.py --plugins road511_usa --states CA NY --replace-source
""",
    )

    # Plugin selection — mutually exclusive
    sel = parser.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all",     action="store_true",
                     help="Run all registered plugins")
    sel.add_argument("--plugins", nargs="+", metavar="ALIAS",
                     help="Run one or more specific plugins (see --list for aliases)")
    sel.add_argument("--list",    action="store_true",
                     help="List all registered plugins and exit")
    sel.add_argument("--stats",   action="store_true",
                     help="Show camera counts by source and region from the store, then exit")
    sel.add_argument("--import",  dest="import_path", metavar="GEOJSON",
                     help="Seed the SQLite store from an existing geojson, then exit")
    sel.add_argument("--export",  action="store_true",
                     help="Export the store to --output geojson + summary.json, then exit (no scraping)")
    sel.add_argument("--resolve-ipcamlive", action="store_true", dest="resolve_ipcamlive",
                     help="Resolve existing ipcamlive:// placeholder streams in the store to real "
                          "m3u8 URLs in place, re-export, then exit (no full re-scrape)")
    sel.add_argument("--probe-image-hosts", action="store_true", dest="probe_image_hosts",
                     help="Probe hosts of gated real-image cameras (direct_eligible=0); mark "
                          "direct-displayable where the host actually serves images, re-export, "
                          "then exit")
    sel.add_argument("--probe-iframe-hosts", action="store_true", dest="probe_iframe_hosts",
                     help="Probe hosts of iframe cameras for framing-blocking headers; mark "
                          "direct-displayable where third-party framing is permitted, re-export, "
                          "then exit")
    sel.add_argument("--resolve-txdot", action="store_true", dest="resolve_txdot",
                     help="Rewrite existing txdot:// placeholder cameras to the real "
                          "its.txdot.gov snapshot API URL in place, re-export, then exit")
    sel.add_argument("--audit-eligible", action="store_true", dest="audit_eligible",
                     help="Re-probe hosts of cameras already marked direct_eligible=1 "
                          "(image/mjpeg/iframe/txdot-json); un-mark and hide any camera "
                          "whose host no longer actually loads, re-export, then exit")

    # Optional exclusion
    parser.add_argument("--exclude", nargs="+", metavar="ALIAS",
                        help="Skip specific plugins (use with --all)")

    # Update mode — mutually exclusive
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--replace-source", action="store_true",
                          help="Drop cameras from the sources being run, then insert fresh results")
    mode_grp.add_argument("--fresh", action="store_true",
                          help="Ignore the existing file; rebuild the dataset from scratch")

    # Output path
    parser.add_argument(
        "--output", default=None, metavar="PATH",
        help="Output GeoJSON path (default: ../public/cameras.geojson)",
    )

    # Parallel execution
    parser.add_argument("--parallel", action="store_true",
                        help="Run plugins concurrently using threads (faster output, interleaved logs)")

    # Road511 per-state override
    parser.add_argument(
        "--states", nargs="+", metavar="STATE",
        help="Limit road511_usa to specific state abbreviations, e.g. --states CO TN DE CA",
    )

    return parser.parse_args()


# ─────────────────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────────────────
def _banner():
    print("\n" + "═" * 65)
    print("  Argus — Camera Data Pipeline")
    print("═" * 65 + "\n")


def _divider(label=""):
    if label:
        print(f"\n  ── {label} {'─' * max(0, 50 - len(label))}")
    else:
        print(f"\n  {'─' * 55}")


def _list_plugins():
    _banner()
    print("  Registered Plugins:\n")
    header = f"  {'ALIAS':<18} {'API KEY':<22} DESCRIPTION"
    print(header)
    print("  " + "─" * (len(header) - 2))
    for alias, info in PLUGIN_REGISTRY.items():
        key_col = f"⚠️  {info['key']}" if info["key"] else "✅ Free (no key)"
        print(f"  {alias:<18} {key_col:<22} {info['description']}")
    print()


def _show_stats():
    """Print a breakdown by source and region from the SQLite store."""
    _banner()

    conn = store.connect()
    total = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    if total == 0:
        print("  [INFO] Store is empty. Run a scrape, or seed it with --import <geojson>.\n")
        conn.close()
        return

    live_count = conn.execute("SELECT COUNT(*) FROM cameras WHERE stream_url != ''").fetchone()[0]
    direct = conn.execute("SELECT COUNT(*) FROM cameras WHERE direct_eligible=1").fetchone()[0]
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM cameras GROUP BY source")}
    by_source_live = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM cameras WHERE stream_url != '' GROUP BY source")}
    conn.close()

    # Country / region groups (opencctv_* rolls up under its own bucket)
    region_groups = {
        "🌍  Global":         ["windy"],
        "🇺🇸  USA":            [k for k in by_source if k.startswith("road511_")]
                             + ["caltrans", "nyc_dot", "iowa_dot"],
        "🇨🇦  Canada":         ["drivebc"],
        "🇬🇧  United Kingdom":  ["tfl_london"],
        "🇸🇬  Singapore":      ["singapore_lta", "singapore"],
        "🇳🇿  New Zealand":    ["nzta"],
        "🛰  OpenCCTV":        [k for k in by_source if k.startswith("opencctv_")],
    }

    print(f"  {'TOTAL CAMERAS':<30} {total:>10,}")
    print(f"  {'Live video (HLS)':<30} {live_count:>10,}")
    print(f"  {'Static image only':<30} {total - live_count:>10,}")
    print(f"  {'Direct-displayable':<30} {direct:>10,}")
    print()
    print(f"  {'─' * 50}")
    print(f"  {'REGION / SOURCE':<35} {'CAMERAS':>10}  {'LIVE':>6}")
    print(f"  {'─' * 50}")

    for region, sources in region_groups.items():
        region_cams = sum(by_source.get(s, 0) for s in sources)
        if region_cams == 0:
            continue
        region_live = sum(by_source_live.get(s, 0) for s in sources)
        print(f"\n  {region}")
        for src in sorted(sources, key=lambda s: -by_source.get(s, 0)):
            count = by_source.get(src, 0)
            if count == 0:
                continue
            src_live = by_source_live.get(src, 0)
            live_str = f"{src_live:>6,}" if src_live else "     —"
            print(f"    {src:<33} {count:>8,}  {live_str}")
        region_live_str = f"{region_live:>6,}" if region_live else "     —"
        print(f"  {'  Subtotal':<35} {region_cams:>10,}  {region_live_str}")

    # Any uncategorised sources
    categorised = {s for sources in region_groups.values() for s in sources}
    other = {k: v for k, v in by_source.items() if k not in categorised}
    if other:
        print(f"\n  Other")
        for src, count in sorted(other.items(), key=lambda x: -x[1]):
            print(f"    {src:<33} {count:>8,}")
    print()

    print(f"\n  {'─' * 50}\n")


# ─────────────────────────────────────────────────────────
# PLUGIN RUNNER
# ─────────────────────────────────────────────────────────
def _run_plugin(alias: str, config: dict) -> tuple:
    """Import and execute a plugin's fetch(config). Returns (alias, features, error)."""
    info = PLUGIN_REGISTRY.get(alias)
    if not info:
        return alias, [], f"Unknown plugin alias '{alias}'"

    key_name = info.get("key")
    if key_name and not config.get(key_name):
        log(f"Skipping '{alias}' — {key_name} not set in .env", "WARN")
        return alias, [], f"Missing API key: {key_name}"

    try:
        module   = importlib.import_module(info["module"])
        features = module.fetch(config)
        return alias, features or [], None
    except Exception as exc:
        return alias, [], str(exc)


def _resolve_plugins(args) -> list:
    """Return the ordered list of plugin aliases to run."""
    selected = list(PLUGIN_REGISTRY.keys()) if args.all else (args.plugins or [])

    unknown = [p for p in selected if p not in PLUGIN_REGISTRY]
    if unknown:
        print(f"\n  [ERROR] Unknown plugin(s): {', '.join(unknown)}")
        print(f"  Run --list to see available plugins.\n")
        sys.exit(1)

    if args.exclude:
        bad = [p for p in args.exclude if p not in PLUGIN_REGISTRY]
        if bad:
            print(f"\n  [ERROR] Unknown --exclude plugin(s): {', '.join(bad)}\n")
            sys.exit(1)
        selected = [p for p in selected if p not in args.exclude]

    return selected


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def _resolve_ipcamlive_store(output_path, compact_path, summary_path):
    """In-place fix for cameras already stored as `ipcamlive://<alias>`: resolve
    each to a real https m3u8 and UPDATE the row, then re-export. Lets the user
    unlock these live streams without re-running the full opencctv harvester."""
    from scrapers.ipcamlive_resolver import alias_from_url, resolve as resolve_ipcamlive

    conn = store.connect()
    rows = conn.execute(
        "SELECT id, feed_url FROM cameras WHERE feed_url LIKE 'ipcamlive://%'"
    ).fetchall()
    if not rows:
        log("No ipcamlive:// cameras in the store — nothing to resolve.", "WARN")
        conn.close()
        return

    log(f"Resolving {len(rows):,} ipcamlive:// cameras to real m3u8...")
    timeout = CONFIG.get("TIMEOUT", 15)
    start = time.monotonic()

    def _one(row):
        return row["id"], resolve_ipcamlive(alias_from_url(row["feed_url"]), timeout)

    resolved = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_one, r) for r in rows]
        for i, fut in enumerate(as_completed(futures), 1):
            cid, m3u8 = fut.result()
            if m3u8:
                conn.execute(
                    "UPDATE cameras SET feed_url=?, stream_url=?, feed_type='m3u8', "
                    "direct_eligible=1 WHERE id=?", (m3u8, m3u8, cid))
                resolved += 1
            if i % 25 == 0 or i == len(rows):
                log_progress("ipcamlive", i, len(rows), start)
    conn.commit()
    log(f"Resolved {resolved:,}/{len(rows):,} ipcamlive streams "
        f"({len(rows) - resolved:,} offline/unresolvable).", "OK")

    total = store.export_geojson(conn, output_path)
    store.export_compact(conn, compact_path)
    store.export_summary(conn, summary_path)
    conn.close()
    log(f"Re-exported {total:,} cameras → {output_path}", "OK")
    log(f"Wrote compact → {compact_path}", "OK")


def _probe_image_hosts_store(output_path, compact_path, summary_path):
    """In-place fix: opencctv marks some cameras with a real image URL as
    non-direct (conservative default). Probe each origin host once — not
    every camera — and flip `direct_eligible` for every camera on a host that
    actually serves images without a referer/hotlink check."""
    from scrapers.host_prober import probe_image_host, probe_hosts

    conn = store.connect()
    rows = conn.execute(
        "SELECT id, feed_url, origin_host FROM cameras WHERE "
        "(stream_url IS NULL OR stream_url='') AND direct_eligible=0 "
        "AND feed_url LIKE 'http%' AND feed_type IN ('image','mjpeg','image/jpeg')"
    ).fetchall()
    if not rows:
        log("No gated image cameras found — nothing to probe.", "WARN")
        conn.close()
        return

    by_host = {}
    for r in rows:
        by_host.setdefault(r["origin_host"], []).append(r)

    log(f"Probing {len(by_host):,} hosts covering {len(rows):,} gated image cameras...")
    host_samples = {host: [r["feed_url"] for r in cams[:5]] for host, cams in by_host.items()}
    verdicts = probe_hosts(host_samples, probe_image_host)

    recovered = 0
    for host, cams in by_host.items():
        if verdicts.get(host):
            conn.executemany("UPDATE cameras SET direct_eligible=1 WHERE id=?",
                              [(c["id"],) for c in cams])
            recovered += len(cams)
    conn.commit()

    passed = sum(1 for v in verdicts.values() if v)
    log(f"Hosts: {passed:,}/{len(by_host):,} passed the image probe.", "OK")
    log(f"Recovered {recovered:,}/{len(rows):,} cameras (now direct-displayable).", "OK")

    total = store.export_geojson(conn, output_path)
    store.export_compact(conn, compact_path)
    store.export_summary(conn, summary_path)
    conn.close()
    log(f"Re-exported {total:,} cameras → {output_path}", "OK")
    log(f"Wrote compact → {compact_path}", "OK")


def _probe_iframe_hosts_store(output_path, compact_path, summary_path):
    """In-place fix: flip `direct_eligible` for iframe cameras whose host
    actually permits third-party framing (no X-Frame-Options/frame-ancestors
    block). Hosts that block framing are left greyed out — better than a
    silently blank iframe panel."""
    from scrapers.host_prober import probe_iframe_host, probe_hosts

    conn = store.connect()
    rows = conn.execute(
        "SELECT id, feed_url, origin_host FROM cameras WHERE "
        "(stream_url IS NULL OR stream_url='') AND direct_eligible=0 "
        "AND feed_type='iframe'"
    ).fetchall()
    if not rows:
        log("No gated iframe cameras found — nothing to probe.", "WARN")
        conn.close()
        return

    by_host = {}
    for r in rows:
        by_host.setdefault(r["origin_host"], []).append(r)

    log(f"Probing {len(by_host):,} hosts covering {len(rows):,} iframe cameras...")
    host_samples = {host: cams[0]["feed_url"] for host, cams in by_host.items()}
    verdicts = probe_hosts(host_samples, probe_iframe_host)

    recovered = 0
    for host, cams in by_host.items():
        if verdicts.get(host):
            conn.executemany("UPDATE cameras SET direct_eligible=1 WHERE id=?",
                              [(c["id"],) for c in cams])
            recovered += len(cams)
    conn.commit()

    passed = sum(1 for v in verdicts.values() if v)
    log(f"Hosts: {passed:,}/{len(by_host):,} permit third-party framing.", "OK")
    log(f"Recovered {recovered:,}/{len(rows):,} cameras (now embeddable).", "OK")

    total = store.export_geojson(conn, output_path)
    store.export_compact(conn, compact_path)
    store.export_summary(conn, summary_path)
    conn.close()
    log(f"Re-exported {total:,} cameras → {output_path}", "OK")
    log(f"Wrote compact → {compact_path}", "OK")


def _resolve_txdot_store(output_path, compact_path, summary_path):
    """In-place fix: rewrite txdot:// placeholders to the real snapshot API URL
    (a pure string transform, no network calls — see txdot_resolver.py) and
    mark them direct-eligible. The frontend's TxdotSnapshot component does the
    actual fetch+decode (direct, falling back to the local dev proxy — the API
    sends no CORS header at all)."""
    from urllib.parse import urlparse
    from scrapers.txdot_resolver import resolve_txdot_url, _API_URL

    conn = store.connect()
    rows = conn.execute(
        "SELECT id, feed_url FROM cameras WHERE feed_url LIKE 'txdot://%'"
    ).fetchall()
    if not rows:
        log("No txdot:// cameras in the store — nothing to resolve.", "WARN")
        conn.close()
        return

    host = urlparse(_API_URL).hostname or ""
    resolved = 0
    for row in rows:
        api_url = resolve_txdot_url(row["feed_url"])
        if api_url:
            conn.execute(
                "UPDATE cameras SET feed_url=?, feed_type='txdot-json', "
                "direct_eligible=1, origin_host=? WHERE id=?",
                (api_url, host, row["id"]))
            resolved += 1
    conn.commit()
    log(f"Resolved {resolved:,}/{len(rows):,} txdot:// cameras to the snapshot API.", "OK")

    total = store.export_geojson(conn, output_path)
    store.export_compact(conn, compact_path)
    store.export_summary(conn, summary_path)
    conn.close()
    log(f"Re-exported {total:,} cameras → {output_path}", "OK")
    log(f"Wrote compact → {compact_path}", "OK")


def _audit_eligible_store(output_path, compact_path, summary_path):
    """Re-probe hosts of cameras already marked direct_eligible=1 — catches hosts
    that have since gone dead, started hotlink-blocking, or started blocking
    framing, so the frontend's hide-non-working filter (filteredIndices) actually
    excludes them instead of leaving a permanently-spinning/blank panel that the
    map still lists as visible. Mirrors probe_image_host/probe_iframe_host from
    the Phase 1/2 gating passes, just applied to the already-eligible side.

    Scoped to `opencctv_*` sources only: those are the ones whose eligibility was
    granted dynamically by host-probing in the first place. Native scrapers
    (nyc_dot, drivebc, etc.) get eligibility from the long-vetted
    `_LEGACY_WORKING_DOMAINS` allowlist in store.py — auditing them with a crude
    few-sample-per-host probe is the wrong tool: a host can have thousands of
    cameras with a handful of individually-retired IDs sprinkled in, and an
    unlucky (or non-random) sample can make a mostly-healthy host look fully
    dead. That happened here once already (nyc_dot briefly hidden in full,
    restored). Per-camera dead links on native hosts are already handled at
    view time by the frontend's own onError fallback — no need for bulk hiding.

    Samples are drawn randomly (not `LIMIT N`, which is systematically biased
    toward however the rows happen to be ordered — e.g. a whole batch of
    camera IDs retired together and inserted contiguously), and any host that
    fails is re-probed once more with a fresh random sample before being hidden,
    to filter out transient network blips rather than trusting a single pass.

    `source='windy'` cameras are excluded: the frontend re-fetches their image via
    a JIT call to the Windy webcams API (App.tsx, VITE_WINDY_API_KEY) rather than
    rendering the stored feed_url directly, so probing that stored URL doesn't
    reflect what's actually displayed."""
    import random
    from scrapers.host_prober import probe_image_host, probe_iframe_host, probe_hosts
    from scrapers.utils import HEADERS
    import requests

    conn = store.connect()
    rows = conn.execute(
        "SELECT id, feed_url, origin_host, feed_type FROM cameras WHERE "
        "direct_eligible=1 AND (stream_url IS NULL OR stream_url='') "
        "AND feed_url LIKE 'http%' AND source LIKE 'opencctv_%'"
    ).fetchall()
    if not rows:
        log("No eligible non-stream cameras found — nothing to audit.", "WARN")
        conn.close()
        return

    image_types = {"image", "image/jpeg", "mjpeg"}
    by_host_image, by_host_iframe, by_host_txdot = {}, {}, {}
    for r in rows:
        ft = r["feed_type"] or ""
        if ft == "iframe":
            by_host_iframe.setdefault(r["origin_host"], []).append(r)
        elif ft == "txdot-json":
            by_host_txdot.setdefault(r["origin_host"], []).append(r)
        elif ft in image_types:
            by_host_image.setdefault(r["origin_host"], []).append(r)
        # any other feed_type (e.g. m3u8 resolved separately) is left alone

    total_hosts = len(by_host_image) + len(by_host_iframe) + len(by_host_txdot)
    total_checked = sum(len(v) for v in by_host_image.values()) \
        + sum(len(v) for v in by_host_iframe.values()) \
        + sum(len(v) for v in by_host_txdot.values())
    log(f"Auditing {total_hosts:,} hosts covering {total_checked:,} already-eligible "
        f"opencctv cameras...")

    def _sample(cams, n):
        return random.sample(cams, n) if len(cams) > n else list(cams)

    image_samples = {h: [r["feed_url"] for r in _sample(cams, 8)] for h, cams in by_host_image.items()}
    iframe_samples = {h: random.choice(cams)["feed_url"] for h, cams in by_host_iframe.items()}

    def probe_txdot_host(host, sample_urls):
        passes = 0
        for url in sample_urls:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=10)
                if resp.status_code == 200 and resp.json().get("snippet"):
                    passes += 1
            except Exception:
                continue
        return passes >= max(1, (len(sample_urls) + 1) // 2)

    txdot_samples = {h: [r["feed_url"] for r in cams[:8]] for h, cams in by_host_txdot.items()}

    verdicts = {}
    verdicts.update(probe_hosts(image_samples, probe_image_host))
    verdicts.update(probe_hosts(iframe_samples, probe_iframe_host))
    verdicts.update(probe_hosts(txdot_samples, probe_txdot_host))

    by_host_all = {**by_host_image, **by_host_iframe, **by_host_txdot}

    # A host failing once could be a transient blip, not a dead host — and the
    # cost of wrongly hiding a working host is much higher than the cost of
    # leaving a dead one visible a bit longer. Re-probe failures once more with
    # a fresh random sample; only hide on a second consecutive failure.
    first_failures = [h for h in by_host_all if not verdicts.get(h)]
    if first_failures:
        log(f"{len(first_failures):,} host(s) failed their first probe — "
            f"confirming with a second pass before hiding anything...")
        recheck_image = {h: [r["feed_url"] for r in _sample(by_host_image[h], 8)]
                          for h in first_failures if h in by_host_image}
        recheck_iframe = {h: random.choice(by_host_iframe[h])["feed_url"]
                           for h in first_failures if h in by_host_iframe}
        recheck_txdot = {h: [r["feed_url"] for r in by_host_txdot[h][:8]]
                          for h in first_failures if h in by_host_txdot}
        confirm = {}
        confirm.update(probe_hosts(recheck_image, probe_image_host))
        confirm.update(probe_hosts(recheck_iframe, probe_iframe_host))
        confirm.update(probe_hosts(recheck_txdot, probe_txdot_host))
        verdicts.update(confirm)

    hidden = 0
    hidden_hosts = []
    for host, cams in by_host_all.items():
        if not verdicts.get(host):
            conn.executemany("UPDATE cameras SET direct_eligible=0 WHERE id=?",
                              [(c["id"],) for c in cams])
            hidden += len(cams)
            hidden_hosts.append((host, len(cams)))
    conn.commit()

    failed = len(hidden_hosts)
    log(f"Hosts: {total_hosts - failed:,}/{total_hosts:,} still pass their probe.", "OK")
    if hidden_hosts:
        log(f"Hid {hidden:,} cameras across {failed:,} host(s) that no longer load "
            f"(now excluded from the map):", "WARN")
        for host, n in sorted(hidden_hosts, key=lambda x: -x[1])[:20]:
            log(f"    {host:<40} {n:>6,} cameras", "WARN")
    else:
        log("No previously-eligible host has gone dead — nothing hidden.", "OK")

    total = store.export_geojson(conn, output_path)
    store.export_compact(conn, compact_path)
    store.export_summary(conn, summary_path)
    conn.close()
    log(f"Re-exported {total:,} cameras → {output_path}", "OK")
    log(f"Wrote compact → {compact_path}", "OK")


def main():
    args = _parse_args()

    # Resolve output path early (needed for --stats too)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = args.output or os.path.join(scripts_dir, "..", "public", "cameras.geojson")
    output_path = os.path.normpath(output_path)
    summary_path = os.path.join(os.path.dirname(output_path), "summary.json")
    compact_path = os.path.join(os.path.dirname(output_path), "cameras.min.json")

    if args.list:
        _list_plugins()
        return

    if args.stats:
        _show_stats()
        return

    if args.import_path:
        _banner()
        conn = store.connect()
        log(f"Importing {args.import_path} into the store...")
        n = store.import_geojson(conn, args.import_path)
        log(f"Store now holds {n:,} cameras.", "OK")
        conn.close()
        return

    if args.resolve_ipcamlive:
        _banner()
        _resolve_ipcamlive_store(output_path, compact_path, summary_path)
        return

    if args.probe_image_hosts:
        _banner()
        _probe_image_hosts_store(output_path, compact_path, summary_path)
        return

    if args.probe_iframe_hosts:
        _banner()
        _probe_iframe_hosts_store(output_path, compact_path, summary_path)
        return

    if args.resolve_txdot:
        _banner()
        _resolve_txdot_store(output_path, compact_path, summary_path)
        return

    if args.audit_eligible:
        _banner()
        _audit_eligible_store(output_path, compact_path, summary_path)
        return

    if args.export:
        _banner()
        conn = store.connect()
        n = store.export_geojson(conn, output_path)
        store.export_compact(conn, compact_path)
        store.export_summary(conn, summary_path)
        conn.close()
        log(f"Exported {n:,} cameras → {output_path}", "OK")
        log(f"Wrote compact → {compact_path}", "OK")
        log(f"Wrote summary → {summary_path}", "OK")
        return

    _banner()

    plugins = _resolve_plugins(args)
    if not plugins:
        log("No plugins selected. Use --all or --plugins <alias>.", "WARN")
        return

    # Inject --states override for road511_usa
    if args.states:
        CONFIG["ROAD511_TARGET_STATES"] = [s.upper() for s in args.states]
        log(f"road511_usa will be limited to: {', '.join(CONFIG['ROAD511_TARGET_STATES'])}")

    # Determine update mode
    if args.fresh:
        mode = "fresh"
    elif args.replace_source:
        mode = "replace-source"
    else:
        mode = "upsert"

    # Build source map for replace-source
    source_map = _build_source_map(args.states)
    sources_being_run = []
    for p in plugins:
        val = source_map.get(p, p)
        if isinstance(val, list):
            sources_being_run.extend(val)
        else:
            sources_being_run.append(val)

    print(f"  Plugins  : {', '.join(plugins)}")
    print(f"  Mode     : {mode}")
    print(f"  Output   : {output_path}")
    print(f"  Parallel : {'yes' if args.parallel else 'no'}")
    print()

    # ── Open store ─────────────────────────────────────────
    conn = store.connect()
    store_stats    = store.stats(conn)
    existing_count = store_stats["total"]
    log(f"Store holds {existing_count:,} cameras before this run.", "OK")

    # Prior per-source counts let plugins self-calibrate their progress-bar ETA
    # target to what the store already holds (a re-run yields ~the same count).
    CONFIG["_PRIOR_SOURCE_COUNTS"] = store_stats.get("by_source", {})

    # ── Run plugins ────────────────────────────────────────
    plugin_results = {}
    plugin_errors  = {}
    start_time     = time.time()

    if args.parallel and len(plugins) > 1:
        _divider("Running plugins in parallel")
        with ThreadPoolExecutor(max_workers=min(len(plugins), 6)) as executor:
            futures = {executor.submit(_run_plugin, a, CONFIG): a for a in plugins}
            for future in as_completed(futures):
                alias, features, error = future.result()
                if error:
                    plugin_errors[alias]  = error
                else:
                    plugin_results[alias] = features
    else:
        for alias in plugins:
            _divider(f"Plugin: {alias}")
            _, features, error = _run_plugin(alias, CONFIG)
            if error:
                plugin_errors[alias]  = error
                log(f"Plugin '{alias}' failed: {error}", "ERROR")
            else:
                plugin_results[alias] = features
                log(f"Plugin '{alias}' returned {len(features):,} cameras", "OK")

    # ── Merge into store ───────────────────────────────────
    # commit=False on every step + one conn.commit() at the end makes this a
    # single atomic transaction: if the process is killed anywhere in here
    # (e.g. a cancelled sync), SQLite rolls the whole batch back and the store
    # is left exactly as it was before the run — never a partial delete/insert.
    _divider("Merging results into store")

    all_new_features = []
    for features in plugin_results.values():
        all_new_features.extend(features)

    removed = 0
    if mode == "fresh":
        removed = store.clear_all(conn, commit=False)
        log("Fresh mode — cleared existing store.", "WARN")
    elif mode == "replace-source":
        removed = store.delete_sources(conn, sources_being_run, commit=False)
        log(f"replace-source — dropped {removed:,} stale cameras from: {', '.join(sources_being_run)}", "OK")

    added, updated = store.upsert_features(conn, all_new_features, commit=False)

    deduped = store.dedupe_prefer_native(conn, commit=False)
    if deduped:
        log(f"Dedupe — dropped {deduped:,} opencctv cameras already covered by native sources.", "OK")

    conn.commit()

    # ── Export artifacts ───────────────────────────────────
    _divider("Exporting geojson + compact + summary")
    total = store.export_geojson(conn, output_path)
    store.export_compact(conn, compact_path)
    store.export_summary(conn, summary_path)
    per_source_counts = store.stats(conn)["by_source"]
    conn.close()

    # ── Summary ────────────────────────────────────────────
    elapsed    = time.time() - start_time
    mins, secs = divmod(int(elapsed), 60)

    print("\n" + "═" * 65)
    print("  ✓  Pipeline Complete")
    print("═" * 65)
    print(f"  Total cameras  : {total:,}")
    print(f"  Added          : {added:,}")
    print(f"  Updated        : {updated:,}")
    if removed:
        print(f"  Removed (stale): {removed:,}")
    print(f"  Elapsed        : {mins}m {secs}s")
    print(f"  Output         : {output_path}")
    print(f"  Summary        : {summary_path}")
    if plugin_errors:
        print(f"\n  Failed plugins : {', '.join(plugin_errors)}")
        for alias, err in plugin_errors.items():
            print(f"    ✗ {alias}: {err}")
    print()
    print("  Per-source breakdown:")
    for src, count in sorted(per_source_counts.items(), key=lambda x: -x[1]):
        print(f"    {src:<28} {count:>8,} cameras")
    print()


if __name__ == "__main__":
    main()
