import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01'
}

urls = [
    'https://www.nvroads.com/api/v2/cameras',
    'https://511.idaho.gov/api/v2/cameras',
    'https://511.alaska.gov/api/v2/cameras',
    'https://udottraffic.utah.gov/api/v2/cameras',
    'https://www.511virginia.org/data/geojson/cameras.geojson',
    'https://data.texas.gov/resource/rq2v-7bxy.json',
    'https://traffic.maryland.gov/api/cameras'
]

for u in urls:
    try:
        r = requests.get(u, headers=HEADERS, timeout=5)
        print(f'{u}: {r.status_code} ({len(r.text)} bytes)')
        if r.status_code == 200 and 'json' in r.headers.get('content-type', '').lower():
            try:
                data = r.json()
                if isinstance(data, list): print(f"  -> List of {len(data)} items")
                elif isinstance(data, dict): print(f"  -> Dict with keys: {list(data.keys())[:5]}")
            except Exception as e:
                print(f"  -> JSON parse failed: {e}")
    except Exception as e:
        print(f'{u} failed: {e}')
