"""
Host prober
===========
Reusable host-level probes for the maintenance passes in scraper.py
(`--probe-image-hosts`, `--probe-iframe-hosts`). Cameras sharing an origin
host share the same CORS/framing behavior, so rather than probing every
individual camera we probe a small sample per host and apply the verdict to
every camera on that host — much cheaper than a per-camera check.

Two independent verdicts:
  - probe_image_host()  — does this host actually serve real images (used to
    flip `direct_eligible` for opencctv cameras that have a real image URL but
    were conservatively marked non-direct)?
  - probe_iframe_host()  — does this host permit itself to be embedded in a
    third-party <iframe> (checks X-Frame-Options / CSP frame-ancestors)?
"""

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from scrapers.utils import HEADERS

# YouTube's embed endpoint (youtube.com/embed/<id>) is designed to be framed
# by any third-party site — no need to probe it.
_ALWAYS_FRAMABLE_HOSTS = {
    "www.youtube.com", "youtube.com",
    "www.youtube-nocookie.com", "youtube-nocookie.com",
}


def probe_image_host(host: str, sample_urls: list, timeout: int = 10) -> bool:
    """A host passes if a majority of sampled URLs return 200 with an
    image/* Content-Type."""
    if not sample_urls:
        return False
    passes = 0
    for url in sample_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
            ct = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and ct.lower().startswith("image/"):
                passes += 1
            resp.close()
        except Exception:
            continue
    return passes >= max(1, (len(sample_urls) + 1) // 2)


def probe_iframe_host(host: str, sample_url: str, timeout: int = 10) -> bool:
    """A host passes if it does not send a header that blocks third-party
    framing. Fails closed on any error, non-2xx/3xx status, or ambiguous CSP
    — better to leave a camera greyed out than show a blank iframe."""
    if host in _ALWAYS_FRAMABLE_HOSTS:
        return True
    if not sample_url:
        return False
    try:
        resp = requests.get(sample_url, headers=HEADERS, timeout=timeout, stream=True)
        resp.close()
    except Exception:
        return False
    if resp.status_code >= 400:
        return False

    xfo = (resp.headers.get("X-Frame-Options") or "").strip().upper()
    if xfo in ("DENY", "SAMEORIGIN"):
        return False

    csp = resp.headers.get("Content-Security-Policy") or ""
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.lower().startswith("frame-ancestors"):
            sources = directive.split()[1:]
            # 'none', or any allowlist that doesn't include '*', blocks a
            # third-party origin like Argus's.
            if not sources or "'none'" in sources or "*" not in sources:
                return False
    return True


def probe_hosts(host_samples: dict, probe_fn, max_workers: int = 8) -> dict:
    """Run `probe_fn(host, samples)` concurrently across many hosts.

    host_samples: {host: samples} — samples is whatever `probe_fn` expects as
    its second positional argument (a list of URLs for probe_image_host, a
    single URL string for probe_iframe_host).
    Returns {host: bool}.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(probe_fn, host, samples): host
                   for host, samples in host_samples.items()}
        for fut in as_completed(futures):
            host = futures[fut]
            try:
                results[host] = fut.result()
            except Exception:
                results[host] = False
    return results
