import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9"
}

def log(msg: str, status: str = "INFO"):
    color = ""
    reset = "\033[0m"
    if status == "INFO": color = "\033[94m"  # Blue
    elif status == "OK": color = "\033[92m"  # Green
    elif status == "WARN": color = "\033[93m"  # Yellow
    elif status == "ERROR": color = "\033[91m"  # Red
    
    icon = "ℹ"
    if status == "OK": icon = "✓"
    elif status == "WARN": icon = "⚠"
    elif status == "ERROR": icon = "✗"
    
    print(f"  {color}{icon} [{status}] {reset}{msg}")

def build_feature(cam_id: str, name: str, lat: float, lon: float, 
                  feed_url: str, cam_type: str, city: str, country: str, 
                  source: str, player_url: str = "", stream_url: str = "", 
                  feed_type: str = "image/jpeg", **kwargs) -> dict:
    """Helper to consistently build a GeoJSON feature for a camera."""
    props = {
        "id": f"{source}_{cam_id}",
        "name": name,
        "type": cam_type,
        "city": city,
        "country": country,
        "feedUrl": feed_url,
        "playerUrl": player_url,
        "streamUrl": stream_url,
        "feedType": feed_type,
        "source": source
    }
    props.update(kwargs)
    
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props
    }
