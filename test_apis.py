import requests, json, time

HEADERS = {"User-Agent": "Argus/1.0 Camera Research"}

def test(name, url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        print(f"  {r.status_code}  {name}")
        if r.status_code == 200:
            try:
                return r.json()
            except:
                return r.text[:200]
        return None
    except Exception as e:
        print(f"  ERR  {name} — {str(e)[:60]}")
        return None

print("=== EUROPE ===")

# Finland - Digitraffic (confirmed free, no key)
print("\n[Finland - Digitraffic]")
d = test("Camera stations list", "https://tie.digitraffic.fi/api/v3/metadata/camera-stations")
if d and isinstance(d, dict):
    stations = d.get("features", [])
    print(f"  → {len(stations)} camera stations")
    if stations: print(f"  → Sample: {json.dumps(stations[0].get('properties',{}))[:200]}")

d2 = test("Camera data (images)", "https://tie.digitraffic.fi/api/v3/data/camera-data")
if d2 and isinstance(d2, dict):
    cams = d2.get("cameraStations", [])
    print(f"  → {len(cams)} cameras with data")
    if cams: print(f"  → Sample: {json.dumps(cams[0])[:300]}")

# Norway Vegvesen
print("\n[Norway Vegvesen]")
d = test("Cameras", "https://nvdbapiles-v3.atlas.vegvesen.no/vegobjekter/95?inkluder=alle&antall=50")
if d and isinstance(d, dict):
    objs = d.get("objekter", [])
    print(f"  → {len(objs)} objects")
    if objs: print(f"  → Sample: {str(objs[0])[:300]}")

# Sweden Trafikverket
print("\n[Sweden Trafikverket]")
d = test("Cameras", "https://api.trafikinfo.trafikverket.se/v2/data.json")
if d: print(f"  → Got response: {str(d)[:200]}")

# Denmark Vejdirektoratet  
print("\n[Denmark]")
d = test("Cameras", "https://dc.vd.dk/api/cameras")
d2 = test("Alternative", "https://api.vejdirektoratet.dk/v1/cameras")

print("\n=== NORTH AMERICA (no key) ===")

# Oregon DOT (tripcheck)
print("\n[Oregon DOT]")
d = test("Cameras JSON", "https://tripcheck.com/Scripts/rss.asp?rsstype=Cams&format=json")
d2 = test("Alt endpoint", "https://www.tripcheck.com/api/cameras")

# Wyoming DOT
print("\n[Wyoming DOT]")
d = test("CCTV", "https://wyoroad.info/Highway/CCTV/GetCCTVData.aspx?format=json")
d2 = test("Alt", "https://www.wyoroad.info/api/cameras")

# Michigan DOT
print("\n[Michigan DOT - mi511]")
d = test("Cameras", "https://mi511.com/api/v1/cameras")

# Utah DOT
print("\n[Utah DOT]")
d = test("Cameras", "https://www.udottraffic.utah.gov/api/v1/cameras")
d2 = test("Alt", "https://udottraffic.utah.gov/api/cameras")

# Idaho DOT
print("\n[Idaho DOT]")
d = test("Cameras", "https://511.idaho.gov/api/v1/cameras")
d2 = test("Alt", "https://idaho511.com/api/cameras")

print("\n=== ASIA-PACIFIC ===")

# Singapore (confirmed working)
print("\n[Singapore LTA - CONFIRMED]")
d = test("Traffic Images", "https://api.data.gov.sg/v1/transport/traffic-images")
if d and isinstance(d, dict):
    cams = d.get("items", [{}])[0].get("cameras", [])
    print(f"  → {len(cams)} cameras")

# Taiwan government open data
print("\n[Taiwan MOTC]")
d = test("Cameras", "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/CCTV?%24top=50&%24format=JSON")

# South Korea
print("\n[Korea ITS]")
d = test("Cameras", "https://its.go.kr/opendata/api/camera")

print("\n=== OCEANIA ===")

# Queensland Australia (TMR)
print("\n[Queensland TMR Australia]")
d = test("Cameras", "https://www.tmr.qld.gov.au/api/cameras")
d2 = test("Traffic cameras", "https://trafficqld.com.au/api/cameras")

# VicRoads Victoria Australia
print("\n[VicRoads Victoria]")
d = test("Cameras", "https://traffic.vicroads.vic.gov.au/api/v2/cameras")

print("\nDone.")
