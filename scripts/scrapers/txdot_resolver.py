"""
txdot:// resolver
=================
opencctv stores TxDOT cameras as a placeholder `txdot://<district>/<icdId>`
instead of a playable URL. its.txdot.gov's own web app (its.js) calls a plain,
keyless JSON API to fetch a camera's live snapshot:

    GET /its/DistrictIts/GetCctvSnapshotByIcdId?icdId=<icdId>&districtCode=<district>
    -> {"icd_Id": "...", "snippet": "<base64 JPEG bytes, no data: prefix>"}

`<icdId>` in that query string is exactly the (already percent-encoded)
`<icdId>` segment stored in our placeholder — verified against the live API
across three districts (ABL/DAL/SAT), so this is a pure URL rewrite with no
network calls needed at resolve time.

Caveat: this API sends no Access-Control-Allow-Origin header at all, so a
browser can't `fetch()` it cross-origin directly — see TxdotSnapshot in
src/App.tsx, which tries direct then falls back to the local dev proxy.
"""

_API_URL = "https://its.txdot.gov/its/DistrictIts/GetCctvSnapshotByIcdId"
_SCHEME = "txdot://"


def resolve_txdot_url(feed_url: str) -> str | None:
    """Rewrite `txdot://<district>/<icdId>` into the real snapshot API URL.
    Returns None if `feed_url` isn't a txdot:// placeholder."""
    if not feed_url or not feed_url.startswith(_SCHEME):
        return None
    rest = feed_url[len(_SCHEME):]
    if "/" not in rest:
        return None
    district, icd_id = rest.split("/", 1)
    if not district or not icd_id:
        return None
    return f"{_API_URL}?icdId={icd_id}&districtCode={district}"
