import os, importlib, time, json
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    "WINDY_API_KEY": os.getenv("WINDY_API_KEY"),
    "WINDY_LIMIT_PER_REGION": 1000,  # Max 1000 per region
    "API_511_KEY": os.getenv("API_511_KEY"),
    "TIMEOUT": 10,
    "REQUEST_DELAY": 0.1
}

# The list of active scraper plugins
# Format: module_path
PLUGINS = [
    "scrapers.global.windy",
    "scrapers.usa.caltrans",
    "scrapers.canada.drivebc",
    "scrapers.asia.singapore_lta",
    "scrapers.europe.tfl_london",
    "scrapers.usa.nyc_dot",
]

def deduplicate(features: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for f in features:
        cam_id = f["properties"]["id"]
        if cam_id not in seen:
            seen.add(cam_id)
            unique.append(f)
    return unique

def main():
    print("\n" + "═" * 60)
    print("  Argus — Camera Data Pipeline Engine")
    print("═" * 60 + "\n")

    all_features = []
    sources_used = []

    for plugin_path in PLUGINS:
        try:
            module = importlib.import_module(plugin_path)
            features = module.fetch(CONFIG)
            if features:
                all_features.extend(features)
                sources_used.append(plugin_path.split('.')[-1])
        except Exception as e:
            print(f"  \033[91m✗ [ERROR]\033[0m Plugin {plugin_path} failed: {e}")

    # Deduplicate
    before = len(all_features)
    all_features = deduplicate(all_features)
    if before - len(all_features) > 0:
        print(f"  \033[93m⚠ [WARN]\033[0m Removed {before - len(all_features)} duplicates")

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_cameras": len(all_features),
            "sources": sources_used
        },
        "features": all_features
    }

    output_path = "cameras.geojson"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print("\n" + "─" * 60)
    print(f"  ✓ Engine complete!")
    print(f"  ✓ Total cameras: {len(all_features)}")
    print(f"  ✓ Sources: {', '.join(sources_used)}")
    print(f"  ✓ Output: {output_path}")
    print("─" * 60 + "\n")

if __name__ == "__main__":
    main()
