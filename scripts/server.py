#!/usr/bin/env python3
"""
Argus — Local Control Server
============================
A tiny stdlib-only HTTP server that lets the Argus web UI trigger the scraper
pipeline and stream its progress live. It exists ONLY for local development —
Argus itself stays a static, $0-hosted site. Run it alongside `npm run dev`:

    python scripts/server.py            # listens on http://localhost:8787

The frontend Settings panel pings /api/health; if this server is up, the
"Data Sync" buttons light up. If it isn't (e.g. the deployed static site),
they stay disabled with a "local only" hint. Nothing here is ever deployed.

Endpoints
  GET  /api/health   -> {"status":"ok","running":bool}
  POST /api/scrape   -> body {"target":"native"|"opencctv"|"both"}
                        streams newline-delimited JSON progress events:
                          {"type":"start", "target", "before"}
                          {"type":"log",   "line"}
                          {"type":"done",  "code", "before", "after", "delta"}
                          {"type":"error", "message"}
  GET  /api/proxy?url=<upstream> -> streams the upstream body back with
                        Access-Control-Allow-Origin:* so the browser can play
                        HLS/mp4 streams (and images) whose origin sends no CORS
                        header. Dev-only; the deployed static site has no proxy
                        and simply falls back to a static image.

Every run uses --replace-source so stale/offline cameras from the sources being
refreshed are dropped, and re-exports the public/cameras.core+labels+detail
payload when it finishes.
"""

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

PORT = int(os.getenv("ARGUS_SERVER_PORT", "8787"))

# target -> extra scraper.py args. Native = everything except the opencctv bridge.
TARGETS = {
    "native":   ["--all", "--exclude", "opencctv_bridge"],
    "opencctv": ["--plugins", "opencctv_bridge"],
    "both":     ["--all"],
}

# Only one scrape may run at a time across all clients.
_run_lock = threading.Lock()

# Bytes per chunk when streaming a proxied response back to the browser.
_PROXY_CHUNK = 64 * 1024
# Upstream request headers the browser normally sends that we forward as-is.
_PROXY_FORWARD_HEADERS = ("Range", "Accept", "Accept-Language")


_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')


def _rewrite_playlist(text: str, playlist_url: str, proxy_prefix: str) -> str:
    """Rewrite every URI in an HLS playlist to an absolute, proxied URL.

    - segment / variant-playlist lines (non-comment) are replaced wholesale
    - URI="..." attributes inside tags (#EXT-X-KEY, #EXT-X-MEDIA, #EXT-X-MAP)
      are rewritten in place
    Relative URIs are first resolved against the playlist's own URL, so the
    browser only ever fetches fully-qualified proxied URLs and hls.js never has
    to resolve anything against the proxy address itself.
    """
    def proxied(uri: str) -> str:
        absolute = urllib.parse.urljoin(playlist_url, uri.strip())
        return proxy_prefix + urllib.parse.quote(absolute, safe="")

    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(line)
        elif stripped.startswith("#"):
            m = _URI_ATTR_RE.search(stripped)
            out.append(stripped.replace(m.group(1), proxied(m.group(1))) if m else line)
        else:
            out.append(proxied(stripped))
    return "\n".join(out)


def _proxy_host_allowed(host: str) -> bool:
    """Minimal SSRF guard: refuse loopback/link-local/private targets so a
    malicious page can't use our dev proxy to reach the user's internal network.
    Only public http(s) camera hosts should ever be proxied."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # unresolvable — let it fail closed
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _store_total():
    """Current camera count in the SQLite store, or None if it can't be read."""
    try:
        import store
        conn = store.connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 so the streaming response ends cleanly on connection close
    # (the browser's fetch reader reads until EOF — no Content-Length needed).
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # quieter console; the scrape logs are what matter
        pass

    # ── CORS (the Vite dev server on :5173 calls us cross-origin) ──
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            self._json(200, {"status": "ok", "running": _run_lock.locked()})
        elif self.path.startswith("/api/proxy"):
            self._proxy()
        else:
            self._json(404, {"error": "not found"})

    # ── CORS pass-through proxy: fetch an upstream URL and stream it back with
    # permissive CORS so hls.js can read cross-origin playlists+segments. ──
    def _proxy(self):
        query = urllib.parse.urlparse(self.path).query
        target = urllib.parse.parse_qs(query).get("url", [""])[0]
        if not target:
            self._json(400, {"error": "missing url param"})
            return

        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https"):
            self._json(400, {"error": "only http/https urls may be proxied"})
            return
        if not _proxy_host_allowed(parsed.hostname or ""):
            self._json(403, {"error": "host not allowed"})
            return

        req_headers = {"User-Agent": "Mozilla/5.0 (Argus proxy)"}
        for h in _PROXY_FORWARD_HEADERS:
            val = self.headers.get(h)
            if val:
                req_headers[h] = val

        try:
            upstream = urllib.request.urlopen(
                urllib.request.Request(target, headers=req_headers), timeout=20
            )
        except urllib.error.HTTPError as exc:
            # Pass the upstream status through (403/404/etc.) so the client's
            # error handling behaves the same as a direct fetch.
            self._json(exc.code, {"error": f"upstream {exc.code}"})
            return
        except Exception as exc:
            self._json(502, {"error": f"proxy fetch failed: {exc}"})
            return

        content_type = upstream.headers.get("Content-Type", "")
        try:
            status = getattr(upstream, "status", 200) or 200

            # HLS playlists must be rewritten, not streamed: hls.js resolves each
            # relative segment/variant URI against the playlist's URL, so if the
            # playlist came from the proxy its segments would resolve to bad proxy
            # paths. Rewrite every URI to an absolute proxied URL up front.
            if "mpegurl" in content_type.lower():
                body = upstream.read()
                prefix = f"http://{self.headers.get('Host', f'127.0.0.1:{PORT}')}/api/proxy?url="
                rewritten = _rewrite_playlist(body.decode("utf-8", "replace"),
                                              target, prefix).encode("utf-8")
                self.send_response(status)
                self._cors()
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(rewritten)))
                self.end_headers()
                self.wfile.write(rewritten)
                return

            self.send_response(status)
            self._cors()
            self.send_header("Access-Control-Expose-Headers", "*")
            for h in ("Content-Type", "Content-Length", "Content-Range",
                      "Accept-Ranges", "Cache-Control"):
                val = upstream.headers.get(h)
                if val:
                    self.send_header(h, val)
            self.end_headers()

            while True:
                chunk = upstream.read(_PROXY_CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client closed the tab / switched cameras mid-stream
        except Exception:
            pass  # headers may already be sent; nothing more we can do
        finally:
            upstream.close()

    def do_POST(self):
        if self.path != "/api/scrape":
            self._json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid JSON body"})
            return

        target = payload.get("target")
        if target not in TARGETS:
            self._json(400, {"error": f"target must be one of {list(TARGETS)}"})
            return

        if not _run_lock.acquire(blocking=False):
            self._json(409, {"error": "a scrape is already running"})
            return

        try:
            self._stream_scrape(target)
        finally:
            _run_lock.release()

    # ── stream the scraper subprocess as newline-delimited JSON ──
    def _emit(self, obj):
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def _stream_scrape(self, target):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        before = _store_total()
        self._emit({"type": "start", "target": target, "before": before})

        cmd = [sys.executable, "-u", "scraper.py", *TARGETS[target], "--replace-source"]
        try:
            proc = subprocess.Popen(
                cmd, cwd=SCRIPTS_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except Exception as exc:
            self._emit({"type": "error", "message": f"failed to start scraper: {exc}"})
            return

        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.strip():
                    self._emit({"type": "log", "line": line})
            proc.wait()
        except (BrokenPipeError, ConnectionResetError):
            # Client navigated away / closed the panel — stop the run.
            proc.terminate()
            return

        after = _store_total()
        delta = (after - before) if (after is not None and before is not None) else None
        self._emit({"type": "done", "code": proc.returncode,
                    "before": before, "after": after, "delta": delta})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("\n" + "=" * 58)
    print("  Argus — Local Control Server")
    print("=" * 58)
    print(f"  Listening on   http://localhost:{PORT}")
    print(f"  Health check   http://localhost:{PORT}/api/health")
    print("  The Argus Settings panel will now enable Data Sync.")
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.\n")
        server.shutdown()


if __name__ == "__main__":
    main()
