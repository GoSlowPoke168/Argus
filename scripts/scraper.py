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
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.utils import log, load_geojson, save_geojson, merge_features


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
    "TIMEOUT":                    15,
    "REQUEST_DELAY":              0.5,
    "WINDY_GRID_SIZE":            20,
    "WINDY_SATURATION_THRESHOLD": 999,
    "WINDY_MAX_DEPTH":            5,
    "WINDY_BATCH_SIZE":           50,
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
                     help="Show camera counts by source and region from the current geojson, then exit")

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


def _show_stats(geojson_path: str):
    """Load cameras.geojson and print a breakdown by source and region."""
    _banner()

    if not os.path.exists(geojson_path):
        print(f"  [ERROR] File not found: {geojson_path}\n")
        return

    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    total = len(features)

    # Count by source
    by_source = {}
    live_count = 0
    for feat in features:
        src = feat["properties"].get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        if feat["properties"].get("streamUrl"):
            live_count += 1

    # Group road511_* sources under a USA subtotal
    road511_total = sum(v for k, v in by_source.items() if k.startswith("road511_"))
    usa_total = (
        road511_total
        + by_source.get("caltrans", 0)
        + by_source.get("nyc_dot", 0)
        + by_source.get("iowa_dot", 0)
    )

    # Country / region groups
    region_groups = {
        "🌍  Global":       ["windy"],
        "🇺🇸  USA":          [k for k in by_source if k.startswith("road511_")]
                           + ["caltrans", "nyc_dot", "iowa_dot"],
        "🇨🇦  Canada":       ["drivebc"],
        "🇬🇧  United Kingdom": ["tfl_london"],
        "🇸🇬  Singapore":    ["singapore_lta"],
        "🇳🇿  New Zealand":  ["nzta"],
    }

    print(f"  Dataset: {geojson_path}")
    meta = data.get("metadata", {})
    if meta.get("last_updated"):
        print(f"  Last run: {meta['last_updated']}")
    print()
    print(f"  {'TOTAL CAMERAS':<30} {total:>10,}")
    print(f"  {'Live video (HLS)':<30} {live_count:>10,}")
    print(f"  {'Static image only':<30} {total - live_count:>10,}")
    print()
    print(f"  {'─' * 50}")
    print(f"  {'REGION / SOURCE':<35} {'CAMERAS':>10}  {'LIVE':>6}")
    print(f"  {'─' * 50}")

    for region, sources in region_groups.items():
        region_cams = sum(by_source.get(s, 0) for s in sources)
        if region_cams == 0:
            continue
        region_live = sum(
            1 for f in features
            if f["properties"].get("source") in sources
            and f["properties"].get("streamUrl")
        )
        print(f"\n  {region}")
        for src in sorted(sources, key=lambda s: -by_source.get(s, 0)):
            count = by_source.get(src, 0)
            if count == 0:
                continue
            src_live = sum(
                1 for f in features
                if f["properties"].get("source") == src
                and f["properties"].get("streamUrl")
            )
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
def main():
    args = _parse_args()

    # Resolve output path early (needed for --stats too)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = args.output or os.path.join(scripts_dir, "..", "public", "cameras.geojson")
    output_path = os.path.normpath(output_path)

    if args.list:
        _list_plugins()
        return

    if args.stats:
        _show_stats(output_path)
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

    # ── Load existing ──────────────────────────────────────
    if mode != "fresh":
        log(f"Loading existing data from {output_path}...")
        existing = load_geojson(output_path)
        existing_features = existing.get("features", [])
        log(f"Found {len(existing_features):,} existing cameras.", "OK")
    else:
        existing_features = []
        log("Fresh mode — ignoring existing data.", "WARN")

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

    # ── Merge ──────────────────────────────────────────────
    _divider("Merging results")

    all_new_features = []
    for features in plugin_results.values():
        all_new_features.extend(features)

    merged, added, updated, removed = merge_features(
        existing_features, all_new_features, mode, sources_being_run
    )

    per_source_counts = {}
    for feat in merged:
        src = feat["properties"].get("source", "unknown")
        per_source_counts[src] = per_source_counts.get(src, 0) + 1

    # ── Save ───────────────────────────────────────────────
    save_geojson(output_path, merged, sorted(per_source_counts), per_source_counts)

    # ── Summary ────────────────────────────────────────────
    elapsed    = time.time() - start_time
    mins, secs = divmod(int(elapsed), 60)

    print("\n" + "═" * 65)
    print("  ✓  Pipeline Complete")
    print("═" * 65)
    print(f"  Total cameras  : {len(merged):,}")
    print(f"  Added          : {added:,}")
    print(f"  Updated        : {updated:,}")
    if removed:
        print(f"  Removed (stale): {removed:,}")
    print(f"  Elapsed        : {mins}m {secs}s")
    print(f"  Output         : {output_path}")
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
