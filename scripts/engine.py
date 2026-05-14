#!/usr/bin/env python3
"""
Argus — Unified Camera Data Pipeline Engine
============================================
Single entry point for all scraper plugins. Supports plugin selection,
upsert/replace-source/fresh merge modes, parallel execution, and scalable
plugin registration.

Usage:
  python engine.py --list
  python engine.py --all
  python engine.py --plugins windy drivebc
  python engine.py --all --exclude windy
  python engine.py --plugins caltrans drivebc --replace-source
  python engine.py --all --fresh
  python engine.py --all --output public/cameras.geojson
  python engine.py --all --parallel
"""

import os
import sys
import importlib
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure scrapers package is importable when running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.utils import log, load_geojson, save_geojson, merge_features


# ─────────────────────────────────────────────────────────
# PLUGIN REGISTRY
# Add new scrapers here. alias → module info.
# ─────────────────────────────────────────────────────────
PLUGIN_REGISTRY = {
    "windy": {
        "module":      "scrapers.global.windy",
        "name":        "Windy Webcams (Global)",
        "key":         "WINDY_API_KEY",
        "description": "100k+ global cameras via recursive 20°×20° grid + auto-dense subdivision",
    },
    "caltrans": {
        "module":      "scrapers.usa.california.caltrans",
        "name":        "Caltrans California",
        "key":         None,
        "description": "~2,000 California highway cameras with HLS live streams",
    },
    "nyc_dot": {
        "module":      "scrapers.usa.new_york.nyc_dot",
        "name":        "NYC DOT",
        "key":         None,
        "description": "~900 New York City traffic cameras",
    },
    "drivebc": {
        "module":      "scrapers.canada.bc.drivebc",
        "name":        "DriveBC BC",
        "key":         None,
        "description": "~1,040 British Columbia traffic cameras",
    },
    "singapore_lta": {
        "module":      "scrapers.asia.singapore.lta",
        "name":        "Singapore LTA",
        "key":         None,
        "description": "~90 Singapore traffic cameras",
    },
    "tfl_london": {
        "module":      "scrapers.europe.uk.tfl_london",
        "name":        "TfL London JamCam",
        "key":         None,
        "description": "~950 London traffic cameras with video feeds",
    },
    "nzta": {
        "module":      "scrapers.oceania.nz.nzta",
        "name":        "NZTA New Zealand",
        "key":         None,
        "description": "New Zealand highway webcams",
    },
    "iowa_dot": {
        "module":      "scrapers.usa.iowa.iowa511",
        "name":        "Iowa DOT",
        "key":         None,
        "description": "~1,244 Iowa highway cameras with JPEG snapshots and HLS streams (ArcGIS)",
    },
}


# ─────────────────────────────────────────────────────────
# ENVIRONMENT / CONFIG
# ─────────────────────────────────────────────────────────
def _load_env():
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
    # API keys
    "WINDY_API_KEY":              os.getenv("WINDY_API_KEY"),

    # Networking
    "TIMEOUT":                    15,
    "REQUEST_DELAY":              0.5,

    # Windy-specific
    "WINDY_GRID_SIZE":            20,    # Degrees per grid cell
    "WINDY_SATURATION_THRESHOLD": 999,   # Triggers recursive subdivision
    "WINDY_MAX_DEPTH":            5,     # Max recursion depth (~0.6° boxes at depth 5)
    "WINDY_BATCH_SIZE":           50,    # Windy free-tier max per request
}


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────
def _parse_args():
    parser = argparse.ArgumentParser(
        prog="engine.py",
        description="Argus Camera Data Pipeline — unified scraper engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Update Modes:
  (default)        Upsert — refresh known cameras by ID, add new ones.
                   Cameras from sources NOT run this time are kept as-is.
  --replace-source Drop all old cameras from the sources being run, then
                   insert fresh results. Removes stale cameras per-source.
  --fresh          Ignore the existing file entirely. Write only what was
                   just fetched. Use to nuke and rebuild from scratch.

Examples:
  python engine.py --all
  python engine.py --plugins windy drivebc
  python engine.py --all --exclude windy
  python engine.py --plugins caltrans drivebc --replace-source
  python engine.py --all --fresh
  python engine.py --all --output public/cameras.geojson --parallel
  python engine.py --list
""",
    )

    # Plugin selection — mutually exclusive
    sel = parser.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all",     action="store_true",
                     help="Run all registered plugins")
    sel.add_argument("--plugins", nargs="+", metavar="ALIAS",
                     help="Run specific plugins (see --list for aliases)")
    sel.add_argument("--list",    action="store_true",
                     help="List all available plugins and exit")

    # Optional exclusion (only meaningful with --all)
    parser.add_argument("--exclude", nargs="+", metavar="ALIAS",
                        help="Skip specific plugins (use with --all)")

    # Update mode — mutually exclusive
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replace-source", action="store_true",
                      help="Replace cameras from the sources being run (removes stale)")
    mode.add_argument("--fresh", action="store_true",
                      help="Ignore existing file; rebuild from scratch")

    # Output path
    parser.add_argument(
        "--output", default=None, metavar="PATH",
        help="Output GeoJSON path (default: ../public/cameras.geojson)"
    )

    # Parallel execution
    parser.add_argument("--parallel", action="store_true",
                        help="Run plugins concurrently using threads (faster, mixed logs)")

    return parser.parse_args()


# ─────────────────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────────────────
def _banner():
    print("\n" + "═" * 65)
    print("  Argus — Camera Data Pipeline Engine")
    print("═" * 65 + "\n")


def _divider(label=""):
    if label:
        print(f"\n  ── {label} {'─' * max(0, 50 - len(label))}")
    else:
        print(f"\n  {'─' * 55}")


def _list_plugins():
    _banner()
    print("  Registered Plugins:\n")
    header = f"  {'ALIAS':<18} {'API KEY':<20} DESCRIPTION"
    print(header)
    print("  " + "─" * (len(header) - 2))
    for alias, info in PLUGIN_REGISTRY.items():
        key_col = f"✅ {info['key']}" if info["key"] else "❌ Free (no key)"
        print(f"  {alias:<18} {key_col:<20} {info['description']}")
    print()


# ─────────────────────────────────────────────────────────
# PLUGIN RUNNER
# ─────────────────────────────────────────────────────────
def _run_plugin(alias: str, config: dict) -> tuple:
    """
    Import and execute a single plugin's fetch(config) function.
    Returns: (alias, features: list, error: str | None)
    """
    info = PLUGIN_REGISTRY.get(alias)
    if not info:
        return alias, [], f"Unknown plugin alias '{alias}'"

    # Warn if this plugin needs a key that isn't set
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
    if args.all:
        selected = list(PLUGIN_REGISTRY.keys())
    else:
        selected = args.plugins or []

    # Validate aliases
    unknown = [p for p in selected if p not in PLUGIN_REGISTRY]
    if unknown:
        print(f"\n  [ERROR] Unknown plugin(s): {', '.join(unknown)}")
        print(f"  Run --list to see available plugins.\n")
        sys.exit(1)

    # Apply exclusions
    if args.exclude:
        bad_excl = [p for p in args.exclude if p not in PLUGIN_REGISTRY]
        if bad_excl:
            print(f"\n  [ERROR] Unknown --exclude plugin(s): {', '.join(bad_excl)}\n")
            sys.exit(1)
        selected = [p for p in selected if p not in args.exclude]

    return selected


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    args = _parse_args()

    if args.list:
        _list_plugins()
        return

    _banner()

    # Resolve which plugins to run
    plugins = _resolve_plugins(args)
    if not plugins:
        log("No plugins selected. Use --all or --plugins <alias>.", "WARN")
        return

    # Determine output path
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = args.output or os.path.join(scripts_dir, "..", "public", "cameras.geojson")
    output_path = os.path.normpath(output_path)

    # Determine update mode
    if args.fresh:
        mode = "fresh"
    elif args.replace_source:
        mode = "replace-source"
    else:
        mode = "upsert"

    # Identify which Windy "source" values map to which plugins
    # (needed for replace-source to know which IDs to drop)
    SOURCE_MAP = {
        "windy":         "windy",
        "caltrans":      "caltrans",
        "nyc_dot":       "nyc_dot",
        "drivebc":       "drivebc",
        "singapore_lta": "singapore",
        "tfl_london":    "tfl_london",
        "nzta":          "nzta",
    }
    sources_being_run = [SOURCE_MAP.get(p, p) for p in plugins]

    print(f"  Plugins  : {', '.join(plugins)}")
    print(f"  Mode     : {mode}")
    print(f"  Output   : {output_path}")
    print(f"  Parallel : {'yes' if args.parallel else 'no'}")
    print()

    # ── Load existing GeoJSON ──────────────────────────────
    if mode != "fresh":
        log(f"Loading existing data from {output_path}...")
        existing = load_geojson(output_path)
        existing_features = existing.get("features", [])
        log(f"Found {len(existing_features):,} existing cameras.", "OK")
    else:
        existing_features = []
        log("Fresh mode — ignoring existing data.", "WARN")

    # ── Run plugins ────────────────────────────────────────
    plugin_results = {}   # alias → list[feature]
    plugin_errors  = {}   # alias → error string
    start_time     = time.time()

    if args.parallel and len(plugins) > 1:
        _divider("Running plugins in parallel")
        with ThreadPoolExecutor(max_workers=min(len(plugins), 6)) as executor:
            futures = {executor.submit(_run_plugin, alias, CONFIG): alias for alias in plugins}
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
    per_source_raw   = {}
    for alias, features in plugin_results.items():
        all_new_features.extend(features)
        per_source_raw[alias] = len(features)

    merged, added, updated, removed = merge_features(
        existing_features, all_new_features, mode, sources_being_run
    )

    # Per-source counts in final output
    per_source_counts = {}
    for feat in merged:
        src = feat["properties"].get("source", "unknown")
        per_source_counts[src] = per_source_counts.get(src, 0) + 1

    # ── Save ───────────────────────────────────────────────
    sources_list = sorted(per_source_counts.keys())
    save_geojson(output_path, merged, sources_list, per_source_counts)

    # ── Summary ────────────────────────────────────────────
    elapsed = time.time() - start_time
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
        print(f"\n  Failed plugins : {', '.join(plugin_errors.keys())}")
        for alias, err in plugin_errors.items():
            print(f"    ✗ {alias}: {err}")
    print()

    print("  Per-source breakdown:")
    for src, count in sorted(per_source_counts.items(), key=lambda x: -x[1]):
        print(f"    {src:<20} {count:>7,} cameras")
    print()


if __name__ == "__main__":
    main()
