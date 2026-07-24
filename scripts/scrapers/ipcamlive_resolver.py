"""
ipcamlive:// resolver
=====================
opencctv's catalog stores many cameras as a placeholder `ipcamlive://<alias>`
instead of a playable URL. ipcamlive doesn't publish a JSON stream API, but its
embed player page (`player.php?alias=<alias>`) declares the live stream server
and id as plain JS vars:

    var address  = 'http://s5.ipcamlive.com/';
    var streamid = '057lqbpzfshvnxgdm';

The HLS playlist is `<address>streams/<streamid>/stream.m3u8`. ipcamlive's
segment CDN serves that over https with `Access-Control-Allow-Origin: *`, so a
resolved URL plays directly in the browser (no proxy needed).

The per-camera stream server (s5, s173, …) is dynamic, so a resolved URL can go
stale; a re-sync simply re-resolves it — same freshness model as every other
source.
"""

import re

import requests

from scrapers.utils import HEADERS

_PLAYER_URL = "https://www.ipcamlive.com/player/player.php"
# `\baddress` intentionally does NOT match `groupaddress`/`timelapseaddress`
# (no word boundary before "address" there) — it picks the real stream server.
_ADDR_RE = re.compile(r"\baddress\s*=\s*['\"]([^'\"]+)['\"]")
_SID_RE = re.compile(r"\bstreamid\s*=\s*['\"]([^'\"]+)['\"]")

_SCHEME = "ipcamlive://"


def alias_from_url(url: str) -> str | None:
    """Extract '<alias>' from 'ipcamlive://<alias>', else None."""
    if url and url.startswith(_SCHEME):
        return url[len(_SCHEME):]
    return None


def resolve(alias: str, timeout: int = 15) -> str | None:
    """Resolve an ipcamlive alias to a real https HLS m3u8 URL.

    Returns None if the camera is offline/unresolvable (missing address or
    streamid, or the player page can't be fetched).
    """
    if not alias:
        return None
    try:
        resp = requests.get(_PLAYER_URL, params={"alias": alias},
                            headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return None

    addr = _ADDR_RE.search(resp.text)
    sid = _SID_RE.search(resp.text)
    if not addr or not sid:
        return None

    base = addr.group(1).strip()
    stream_id = sid.group(1).strip()
    if not base or not stream_id:
        return None

    # Prefer https — the app is served over https and the CDN supports it.
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    if not base.endswith("/"):
        base += "/"
    return f"{base}streams/{stream_id}/stream.m3u8"
