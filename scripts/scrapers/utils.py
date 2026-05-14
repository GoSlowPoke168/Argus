import os
import json
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9"
}


def log(msg: str, status: str = "INFO"):
    color = ""
    reset = "\033[0m"
    if status == "INFO":   color = "\033[94m"
    elif status == "OK":   color = "\033[92m"
    elif status == "WARN": color = "\033[93m"
    elif status == "ERROR":color = "\033[91m"

    icon = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗"}.get(status, "·")
    print(f"  {color}{icon} [{status}]{reset} {msg}")


def build_feature(cam_id: str, name: str, lat: float, lon: float,
                  feed_url: str, cam_type: str, city: str, country: str,
                  source: str, player_url: str = "", stream_url: str = "",
                  feed_type: str = "image/jpeg", **kwargs) -> dict:
    """Consistently build a GeoJSON Feature for a camera."""
    props = {
        "id":        f"{source}_{cam_id}",
        "name":      name,
        "type":      cam_type,
        "city":      city,
        "country":   country,
        "feedUrl":   feed_url,
        "playerUrl": player_url,
        "streamUrl": stream_url,
        "feedType":  feed_type,
        "source":    source,
    }
    props.update(kwargs)
    return {
        "type":     "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


# ─────────────────────────────────────────────────────────
# GeoJSON I/O
# ─────────────────────────────────────────────────────────

def load_geojson(path: str) -> dict:
    """Load an existing GeoJSON file, or return an empty FeatureCollection."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"Could not read {path}: {e} — starting fresh.", "WARN")
    return {"type": "FeatureCollection", "metadata": {}, "features": []}


def save_geojson(path: str, features: list, sources: list, per_source_counts: dict):
    """Write features to a GeoJSON file with full metadata."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "generated":        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_cameras":    len(features),
            "sources":          sources,
            "per_source_counts": per_source_counts,
        },
        "features": features,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)


def merge_features(existing_features: list, new_features: list,
                   mode: str, sources_being_run: list) -> tuple:
    """
    Merge new_features into existing_features according to mode.

    Modes:
      'upsert'         — update by ID, keep everything not touched
      'replace-source' — drop old cameras from the sources being run, insert fresh
      'fresh'          — discard existing entirely, use only new_features

    Returns: (merged_list, added, updated, removed)
    """
    added = updated = removed = 0

    if mode == "fresh":
        seen = set()
        result = []
        for f in new_features:
            fid = f["properties"]["id"]
            if fid not in seen:
                seen.add(fid)
                result.append(f)
                added += 1
        return result, added, 0, len(existing_features)

    # Build id → feature map from existing
    existing_map = {f["properties"]["id"]: f for f in existing_features}

    if mode == "replace-source":
        # Remove cameras belonging to the sources we're about to replace
        to_remove = [
            fid for fid, f in existing_map.items()
            if f["properties"].get("source") in sources_being_run
        ]
        for fid in to_remove:
            del existing_map[fid]
            removed += 1

    # Upsert new features
    for f in new_features:
        fid = f["properties"]["id"]
        if fid in existing_map:
            existing_map[fid] = f
            updated += 1
        else:
            existing_map[fid] = f
            added += 1

    return list(existing_map.values()), added, updated, removed
