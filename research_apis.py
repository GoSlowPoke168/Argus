import requests, json

HEADERS = {"User-Agent": "Mozilla/5.0"}
timeout = 8

tests = {
    "Iowa DOT": "https://www.511ia.org/api/v2/cameras",
    "Wisconsin DOT": "https://511wi.gov/api/v2/cameras",
    "Nevada DOT": "https://www.nvroads.com/api/v2/cameras",
    "New Jersey DOT": "https://511nj.org/api/v2/cameras",
    "Maine DOT": "https://www.newengland511.org/api/v2/cameras",
    "Maryland CHART": "https://chart.maryland.gov/DataAndMapVideo/GetCameraList",
    "Houston TranStar": "https://traffic.houstontranstar.org/cctv/cctv_json.aspx",
    "TravelMidwest (IL/IN/WI)": "https://www.travelmidwest.com/lmiga/cameras.json",
    "Virginia 511": "https://www.511virginia.org/data/geojson/cameras.geojson",
    "New Zealand Transport": "https://www.journeys.nzta.govt.nz/api/webcams",
    "TfL London (UK)": "https://api.tfl.gov.uk/Place/Type/JamCam",
    "Traffic Scotland": "https://trafficscotland.org/api/cameras",
}

for name, url in tests.items():
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    if 'features' in data: count = len(data['features'])
                    elif 'items' in data: count = len(data['items'])
                    elif 'Cameras' in data: count = len(data['Cameras'])
                    else: count = "dict"
                print(f"[OK] {name}: {count} cameras")
            except:
                print(f"[OK-Text] {name}: {len(r.text)} bytes")
        else:
            print(f"[FAIL] {name}: HTTP {r.status_code}")
    except Exception as e:
        print(f"[ERR] {name}: {str(e)[:50]}")
