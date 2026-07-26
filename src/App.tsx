import { useState, useEffect, useRef, useMemo } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer } from '@deck.gl/layers';
import MapGL, { Source, Layer } from 'react-map-gl/maplibre';
import type { MapMouseEvent, ExpressionSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Scan, Eye, Activity, X, MapPin, RefreshCw, Clock, Video, ChevronUp, ChevronDown, Settings, Shuffle, Filter, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Rnd } from 'react-rnd';
import Hls from 'hls.js';
import * as countries from 'i18n-iso-countries';
import enLocale from 'i18n-iso-countries/langs/en.json';

// Initialize ISO countries library
countries.registerLocale(enLocale);

// Global styles for custom scrollbar
const scrollbarStyles = `
  .custom-scrollbar::-webkit-scrollbar {
    width: 4px;
  }
  .custom-scrollbar::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.02);
    border-radius: 10px;
  }
  .custom-scrollbar::-webkit-scrollbar-thumb {
    background: #00e5ff;
    border-radius: 10px;
    border: 1px solid rgba(0, 229, 255, 0.4);
    box-shadow: 0 0 10px rgba(0, 229, 255, 0.5);
  }
  .custom-scrollbar::-webkit-scrollbar-thumb:hover {
    background: #33ebff;
  }
`;

const CROSSHAIR_COLOR = '#ff2d2d';

// "Small" preset (r 22->14, 13->8), matched from neonbladeui's live demo — only ring
// size shrinks; thickness, arm length/gap, speed, and arc gap match the default.
// Counter-rotating arcs, transform-origin at the SVG center (20,20).
const crosshairStyles = `
  @keyframes crosshair-spin-cw { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  @keyframes crosshair-spin-ccw { from { transform: rotate(360deg); } to { transform: rotate(0deg); } }
  .crosshair-ring-outer { transform-origin: 20px 20px; animation: crosshair-spin-cw 3s linear infinite; }
  .crosshair-ring-inner { transform-origin: 20px 20px; animation: crosshair-spin-ccw 2s linear infinite; }
`;

function CrosshairCursor() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const move = (e: MouseEvent) => {
      const el = ref.current;
      if (!el) return;
      el.style.transform = `translate(${e.clientX - 20}px, ${e.clientY - 20}px)`;
      if (el.style.opacity !== '1') el.style.opacity = '1';
    };
    // Hide on window exit, else it freezes at the edge and reads as a stuck real cursor.
    const hide = () => {
      const el = ref.current;
      if (el) el.style.opacity = '0';
    };
    window.addEventListener('mousemove', move);
    document.documentElement.addEventListener('mouseleave', hide);
    return () => {
      window.removeEventListener('mousemove', move);
      document.documentElement.removeEventListener('mouseleave', hide);
    };
  }, []);

  return (
    <div ref={ref} className="fixed top-0 left-0 z-[9999] pointer-events-none opacity-0" style={{ width: 40, height: 40, willChange: 'transform' }}>
      <style>{crosshairStyles}</style>
      {/* Dark drop-shadow first so the cursor also reads against bright camera-feed images. */}
      <svg width="40" height="40" viewBox="0 0 40 40" style={{ overflow: 'visible', filter: `drop-shadow(0 0 1.5px rgba(0,0,0,0.95)) drop-shadow(0 0 4px ${CROSSHAIR_COLOR}) drop-shadow(0 0 9px ${CROSSHAIR_COLOR}99)` }}>
        <circle className="crosshair-ring-outer" cx="20" cy="20" r="14" fill="none" stroke={CROSSHAIR_COLOR} strokeWidth="2" strokeLinecap="round" strokeDasharray="61.58 26.39" />
        <circle className="crosshair-ring-inner" cx="20" cy="20" r="8" fill="none" stroke={CROSSHAIR_COLOR} strokeWidth="1.5" strokeLinecap="round" strokeDasharray="35.19 15.08" />
        <line x1="20" y1="10" x2="20" y2="17" stroke={CROSSHAIR_COLOR} strokeWidth="1.5" />
        <line x1="20" y1="23" x2="20" y2="30" stroke={CROSSHAIR_COLOR} strokeWidth="1.5" />
        <line x1="10" y1="20" x2="17" y2="20" stroke={CROSSHAIR_COLOR} strokeWidth="1.5" />
        <line x1="23" y1="20" x2="30" y2="20" stroke={CROSSHAIR_COLOR} strokeWidth="1.5" />
      </svg>
    </div>
  );
}

function HlsPlayer({ url, cacheBust, onFallback, proxyBase }: { url: string; cacheBust?: number; onFallback?: () => void; proxyBase?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  // Escalation: direct -> proxy (if available) -> static fallback.
  const [useProxy, setUseProxy] = useState(false);
  const canProxy = !!proxyBase;

  // A brand-new camera (url change) always starts a fresh direct attempt.
  useEffect(() => { setUseProxy(false); }, [url]);

  useEffect(() => {
    if (!videoRef.current) return;

    const via = (u: string) => (useProxy && proxyBase ? `${proxyBase}${encodeURIComponent(u)}` : u);
    // First failure escalates to the proxy; failing again (or no proxy) means static.
    const escalate = () => {
      if (!useProxy && canProxy) {
        console.log('[Argus] Stream failed direct — retrying via local proxy.');
        setUseProxy(true);
      } else {
        console.log('[Argus] Stream unavailable — falling back to static image.');
        onFallback?.();
      }
    };

    // Direct MP4 — native video src (native <video> handles progressive CORS).
    if (url.toLowerCase().includes('.mp4')) {
      const base = via(url);
      const sep = base.includes('?') ? '&' : '?';
      videoRef.current.src = cacheBust ? `${base}${sep}_t=${cacheBust}` : base;
      videoRef.current.onerror = () => escalate();
      videoRef.current.play().catch(e => console.log('Autoplay prevented', e));
      return () => { if (videoRef.current) videoRef.current.onerror = null; };
    }

    let hls: Hls | null = null;

    if (Hls.isSupported()) {
      hls = new Hls({ enableWorker: false });
      // On retry, the proxy rewrites every segment/variant URI, so hls.js's
      // follow-up fetches through it are CORS-enabled too.
      hls.loadSource(via(url));
      hls.attachMedia(videoRef.current);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        videoRef.current?.play().catch(e => console.log('Autoplay prevented', e));
      });
      // Detect CORS blocks or dead streams and escalate (proxy → static).
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (data.fatal) {
          console.log(`[Argus] Stream ${data.details}${useProxy ? ' (via proxy)' : ''}`);
          hls?.destroy();
          escalate();
        }
      });
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari fetches segments itself, so the proxy can't help — direct-only.
      videoRef.current.src = url;
      videoRef.current.addEventListener('loadedmetadata', () => {
        videoRef.current?.play().catch(e => console.log('Autoplay prevented', e));
      });
      videoRef.current.addEventListener('error', () => onFallback?.());
    } else {
      onFallback?.();
    }

    return () => { if (hls) hls.destroy(); };
  }, [url, useProxy, proxyBase]);

  useEffect(() => {
    if (!videoRef.current || !url.toLowerCase().includes('.mp4')) return;
    const base = (useProxy && proxyBase) ? `${proxyBase}${encodeURIComponent(url)}` : url;
    const sep = base.includes('?') ? '&' : '?';
    videoRef.current.src = cacheBust ? `${base}${sep}_t=${cacheBust}` : base;
    videoRef.current.play().catch(e => console.log('Autoplay prevented', e));
  }, [cacheBust]);

  return (
    <video
      ref={videoRef}
      className="w-full h-full object-contain bg-black"
      controls
      muted
      autoPlay
      playsInline
      loop
    />
  );
}

// TxDOT's snapshot API returns base64 JSON with no CORS header, so fetch() needs
// the proxy. Tries direct first, then proxy, then falls back like any other feed.
function TxdotSnapshot({ url, cacheBust, onFallback, proxyBase }: { url: string; cacheBust?: number; onFallback?: () => void; proxyBase?: string }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSrc(null);

    const tryFetch = async (fetchUrl: string) => {
      const resp = await fetch(fetchUrl);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.snippet) throw new Error('no snippet in response');
      return `data:image/jpeg;base64,${data.snippet}`;
    };

    (async () => {
      try {
        const dataUrl = await tryFetch(url);
        if (!cancelled) setSrc(dataUrl);
      } catch {
        if (!proxyBase) {
          if (!cancelled) onFallback?.();
          return;
        }
        try {
          const dataUrl = await tryFetch(`${proxyBase}${encodeURIComponent(url)}`);
          if (!cancelled) setSrc(dataUrl);
        } catch {
          if (!cancelled) onFallback?.();
        }
      }
    })();

    return () => { cancelled = true; };
  }, [url, cacheBust, proxyBase]);

  if (!src) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-black/50 backdrop-blur-sm z-10">
        <div className="w-8 h-8 border-2 border-white/10 border-t-[#00e5ff] rounded-full animate-spin" />
      </div>
    );
  }
  return <img src={src} alt="" className="w-full h-full object-contain" />;
}

// How far from the cursor (in screen pixels) a node still counts as clicked/hovered.
const PICK_RADIUS_PX = 8;

// At or above this zoom, draw every point: most are clipped offscreen by then, so the
// GPU cost is already low (~4ms vs 17ms at world zoom) and binning would barely thin.
const THIN_MAX_ZOOM = 8;

// Collapsing a stack loses the antialiased spill neighboring dots contributed (~16%
// dimmer, measured); drawing binned dots at 1.2x radius restores brightness to within 1%.
const BIN_RADIUS_BOOST = 1.2;

// One representative camera per occupied screen pixel, plus how many collapsed into it.
type BinGroup = { idx: number[]; count: number[] };
type BinResult = { still: BinGroup; live: BinGroup };

const INITIAL_VIEW_STATE = {
  longitude: -95,
  latitude: 38,
  zoom: 1.5,
  pitch: 0,
  bearing: 0,
};

// Custom overrides for non-standard codes or specific project needs
const MANUAL_OVERRIDES: Record<string, string> = {
  'XK': 'Kosovo',
  'XKX': 'Kosovo',
  'Global Sector': 'Global Sector'
};

interface CameraProperties {
  id: string;
  name: string;
  type: string;
  city: string;
  country: string;
  feedUrl: string;
  streamUrl?: string;
  playerUrl?: string;
  feedType: string;
  highway?: string;
  route?: string;
  source?: string;
  directEligible?: boolean;
  updateRate?: number;
  // Known from core alone, so hover can style live/static before the detail chunk arrives.
  live?: boolean;
}

interface CameraFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: CameraProperties;
}

// Three-tier payload from store.py:export_compact. Dict-encoded parallel arrays let
// deck.gl render without one JS object per camera. Split because per-camera strings
// (~80% of a combined payload) only matter for whichever camera is open:
//   core (blocks first paint) / labels (name, streams in behind) / detail (per-camera, fetched on open)
interface CoreData {
  count: number;
  chunk: number;
  generated: string;
  srcDict: string[];
  ccDict: string[];
  ftDict?: string[];
  lon: number[]; lat: number[]; de: number[]; live: number[];
  src: number[]; cc: number[]; ft?: number[];
}
interface LabelData { cityDict: string[]; name: string[]; city: number[]; }
interface DetailChunk {
  from: number;
  id: string[]; feed: string[]; stream: string[]; route: string[]; ur: number[];
}

// Rebuilds one CameraFeature at index i (never the full set). `labels`/`detail` may be
// null — hover passes no detail and gets no URLs, which is all the tooltip needs.
function camAt(
  d: CoreData, i: number,
  labels: LabelData | null,
  detail: DetailChunk | null,
): CameraFeature {
  const j = detail ? i - detail.from : -1;
  const stream = j >= 0 ? detail!.stream[j] : '';
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [d.lon[i], d.lat[i]] },
    properties: {
      id: j >= 0 ? detail!.id[j] : '',
      name: labels ? labels.name[i] : '',
      type: '',
      city: labels ? labels.cityDict[labels.city[i]] : '',
      country: d.ccDict[d.cc[i]],
      feedUrl: j >= 0 ? detail!.feed[j] : '',
      streamUrl: stream || undefined,
      feedType: (d.ftDict && d.ft) ? (d.ftDict[d.ft[i]] || '') : '',
      source: d.srcDict[d.src[i]],
      route: (j >= 0 ? detail!.route[j] : '') || undefined,
      directEligible: d.de[i] === 1,
      updateRate: (j >= 0 ? detail!.ur[j] : 0) || undefined,
      live: d.live[i] === 1,
    },
  };
}

// Local dev-only control server (scripts/server.py). Absent on the deployed static site.
const SYNC_SERVER = 'http://localhost:8787';
// CORS pass-through proxy on that same server, for streams whose origin sends no
// Access-Control-Allow-Origin. Only used when reachable; falls back to static otherwise.
const PROXY_BASE = `${SYNC_SERVER}/api/proxy?url=`;

// Satellite/weather-sat sources refresh every ~10min (their ToS); everything else
// honors its harvested updateRate under a 60s floor.
const SATELLITE_SOURCES = ['goes-satellite', 'faa-weathercams', 'goes-satellite', 'satellite'];
function refreshIntervalMs(cam: CameraFeature): number {
  const src = (cam.properties.source || '').toLowerCase();
  if (SATELLITE_SOURCES.some(s => src.includes(s))) return 600_000;
  return Math.max(cam.properties.updateRate || 0, 60_000);
}

const COUNTRY_NAMES: Record<string, string> = {
  US: 'United States', CA: 'Canada', GB: 'United Kingdom', FR: 'France',
  DE: 'Germany', IT: 'Italy', ES: 'Spain', JP: 'Japan', AU: 'Australia',
  NZ: 'New Zealand', SG: 'Singapore', HK: 'Hong Kong', AE: 'UAE',
  IN: 'India', BR: 'Brazil', AR: 'Argentina', MX: 'Mexico',
  GR: 'Greece', PT: 'Portugal', AT: 'Austria', NL: 'Netherlands',
  CH: 'Switzerland', ZA: 'South Africa', EG: 'Egypt', ID: 'Indonesia', TH: 'Thailand',
};

// Domains known to support CORS headers for image fingerprinting (used to
// decide crossOrigin="anonymous" so we can hash pixels to detect real updates).
const CORS_ENABLED_DOMAINS = [
  'imgproxy.windy.com',
  'amazonaws.com',
  'cloudfront.net',
  'images.data.gov.sg',
  'nzta.govt.nz'
];

// Whether a camera's feed can display: a live stream, or the scraper's per-camera
// `directEligible` flag (opencctv's force_direct / the legacy native-source allowlist).
function isFeedWorking(cam: CameraFeature): boolean {
  if (cam.properties.streamUrl && cam.properties.streamUrl.trim()) return true;
  return !!cam.properties.directEligible;
}


// Reused across the 3D layer's paint properties. Explicitly typed so it stays a valid
// ExpressionSpecification tuple rather than widening to string[][].
const GLOBE_IS_LIVE: ExpressionSpecification = ['==', ['get', 'live'], 1];

function formatLocation(cam: CameraFeature): string {
  const { city, country } = cam.properties;
  const countryName = COUNTRY_NAMES[country] || country;
  if (!city || city === 'British Columbia') return countryName;
  return `${city} · ${countryName}`;
}

// Parses a scraper "[PROGRESS] <plugin> [bar] NN% · a/b · eta X" line (emitted by
// scrapers/utils.py:log_progress); null for any non-progress line.
function parseProgress(line: string): { plugin: string; pct: number; eta: string } | null {
  if (!line || !line.includes('[PROGRESS]')) return null;
  const plugin = line.match(/\[PROGRESS\]\s+(\S+)/)?.[1] ?? '';
  const pct = line.match(/(\d+)%/)?.[1];
  const eta = line.match(/eta\s+(\S+)/)?.[1] ?? '—';
  if (pct === undefined) return null;
  return { plugin, pct: Math.min(100, Math.max(0, parseInt(pct, 10))), eta };
}

function App() {
  const [data, setData] = useState<CoreData | null>(null);
  const [labels, setLabels] = useState<LabelData | null>(null);
  // Detail chunks already fetched, keyed by chunk number. A ref, not state: it's a
  // cache read inside async handlers, and filling it must not re-render the map.
  const chunksRef = useRef(new window.Map<number, DetailChunk>());
  const [selectedCamera, setSelectedCamera] = useState<CameraFeature | null>(null);
  const [hovered, setHovered] = useState<CameraFeature | null>(null);
  // Last hovered camera index, tracked in a ref so per-pixel hover events only touch
  // React state when the hovered camera actually changes.
  const hoveredIdxRef = useRef(-1);
  const didLoadRef = useRef(false);
  // Coordinate HUD is updated by writing textContent directly (no state -> no re-render).
  const coordRef = useRef<HTMLSpanElement | null>(null);
  const [loading, setLoading] = useState(true);
  const [imgCacheBust, setImgCacheBust] = useState(Date.now());
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [imgLoaded, setImgLoaded] = useState(false);
  const [isHudMinimized, setIsHudMinimized] = useState(false);
  // Live camera lives in a ref, not state — feeding every pan frame through setState
  // costs a frame of latency (pointer -> setState -> commit -> redraw). `initialView`
  // only changes for programmatic jumps (mode switch, random camera).
  const [initialView, setInitialView] = useState(INITIAL_VIEW_STATE);
  const viewRef = useRef(INITIAL_VIEW_STATE);
  const zoomRef = useRef<HTMLSpanElement | null>(null);
  const globeRef = useRef<{ getMap?: () => { jumpTo: (o: object) => void } } | null>(null);
  const [liveWindyUrl, setLiveWindyUrl] = useState<string | null>(null);
  const [hlsFailed, setHlsFailed] = useState(false);
  // Set when TxdotSnapshot fails both direct and proxy — no separate static-image
  // URL to fall back to, so this gates the whole panel to "Feed Unavailable".
  const [staticFeedFailed, setStaticFeedFailed] = useState(false);
  const [imgLastLoaded, setImgLastLoaded] = useState<Date | null>(null);
  const [lastImageHash, setLastImageHash] = useState<string | null>(null);
  const [use24Hour, setUse24Hour] = useState(false);
  const [is3D, setIs3D] = useState(false);
  const [nodeOpacity, setNodeOpacity] = useState(0.8);
  // Pixel-binned draw set for the 2D map, or null to draw every point (see binWorker).
  const [bins, setBins] = useState<BinResult | null>(null);
  const [zoomLevel, setZoomLevel] = useState(Math.min(THIN_MAX_ZOOM, Math.floor(INITIAL_VIEW_STATE.zoom)));
  const zoomLevelRef = useRef(zoomLevel);
  const binWorkerRef = useRef<Worker | null>(null);
  const binReqRef = useRef(0);
  const [showBorders, setShowBorders] = useState(false);
  const [filterCountries, setFilterCountries] = useState<string[]>([]);
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [filterSearch, setFilterSearch] = useState('');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Data Sync (local control server, dev-only) ──
  const [syncServerUp, setSyncServerUp] = useState<boolean | null>(null);
  const [syncTarget, setSyncTarget] = useState<string | null>(null); // running target, or null
  const [syncLog, setSyncLog] = useState<string[]>([]);
  const [syncStatus, setSyncStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [syncSummary, setSyncSummary] = useState<string | null>(null);
  // Latest progress parsed from a scraper [PROGRESS] line, or null before the first one.
  const [syncProgress, setSyncProgress] = useState<{ plugin: string; pct: number; eta: string } | null>(null);
  const syncLogRef = useRef<HTMLDivElement | null>(null);
  const syncAbortRef = useRef<AbortController | null>(null);

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: !use24Hour
    });
  };

  // Pan/zoom writes to a ref, bypassing React — except crossing an integer zoom level,
  // which invalidates the pixel bins (see binWorker) and triggers one cheap re-render.
  const trackView = (v: typeof INITIAL_VIEW_STATE) => {
    viewRef.current = v;
    if (zoomRef.current) zoomRef.current.textContent = `${(v.zoom * 10).toFixed(1)}%`;
    const z = Math.min(THIN_MAX_ZOOM, Math.floor(v.zoom));
    if (z !== zoomLevelRef.current) {
      zoomLevelRef.current = z;
      setZoomLevel(z);
    }
  };

  // The HUD's zoom readout is written imperatively, so restate it after any render
  // that may have remounted it (e.g. un-minimizing the HUD).
  useEffect(() => { trackView(viewRef.current); });

  // Hand the current camera to the other renderer on mode switch, sanitizing it
  // first — a non-finite value crashes the projection matrix.
  useEffect(() => {
    const p = viewRef.current;
    const v = {
      latitude: Number.isFinite(p.latitude) ? p.latitude : 38,
      longitude: Number.isFinite(p.longitude) ? p.longitude : -95,
      zoom: Number.isFinite(p.zoom) ? p.zoom : 1.5,
      pitch: Number.isFinite(p.pitch) ? p.pitch : 0,
      bearing: Number.isFinite(p.bearing) ? p.bearing : 0,
    };
    trackView(v);
    setInitialView(v);
  }, [is3D]);

  // Move the camera programmatically. Deck picks up a changed initialViewState;
  // react-map-gl reads it only on mount, so the globe is moved through its map.
  const jumpTo = (v: typeof INITIAL_VIEW_STATE) => {
    trackView(v);
    setInitialView(v);
    globeRef.current?.getMap?.()?.jumpTo({ center: [v.longitude, v.latitude], zoom: v.zoom });
  };

  // Fetches (once, cached) the detail chunk holding camera `i`. Versioned by the core
  // payload's timestamp — a sync shifts indices, so a stale cached chunk would return
  // the wrong camera's URLs; stable and cacheable between syncs.
  const chunkFor = async (d: CoreData, i: number): Promise<DetailChunk | null> => {
    const n = Math.floor(i / d.chunk);
    const cached = chunksRef.current.get(n);
    if (cached) return cached;
    try {
      const c: DetailChunk = await fetch(
        `/cameras.detail/${n}.json?v=${encodeURIComponent(d.generated)}`
      ).then(r => r.json());
      chunksRef.current.set(n, c);
      return c;
    } catch {
      return null;
    }
  };

  const selectCamera = async (i: number) => {
    if (!data) return;
    const cam = camAt(data, i, labels, await chunkFor(data, i));
    setSelectedCamera(cam);
    setHlsFailed(false);
    return cam;
  };

  const openRandomCamera = async () => {
    if (!data || filteredIndices.length === 0) return;
    const i = filteredIndices[Math.floor(Math.random() * filteredIndices.length)];
    const cam = await selectCamera(i);

    if (cam && cam.geometry.coordinates[0] && cam.geometry.coordinates[1]) {
      jumpTo({
        ...viewRef.current,
        longitude: cam.geometry.coordinates[0],
        latitude: cam.geometry.coordinates[1],
        zoom: Math.max(viewRef.current.zoom, 10)
      });
    }
  };

  // Loads (or reloads) the dataset. Core blocks first paint; labels follow in the
  // background. `bust` re-fetches past the cache after a sync.
  const loadData = (bust = false) => {
    setLoading(true);
    const q = bust ? `?_t=${Date.now()}` : '';
    if (bust) chunksRef.current.clear();
    fetch(`/cameras.core.json${q}`)
      .then(r => r.json())
      .then((d: CoreData) => {
        // Typed arrays: smaller footprint and a zero-copy upload into deck.gl's buffer.
        d.lon = Float32Array.from(d.lon) as unknown as number[];
        d.lat = Float32Array.from(d.lat) as unknown as number[];
        setData(d);
        setLoading(false);
        fetch(`/cameras.labels.json${q}`)
          .then(r => r.json())
          .then(setLabels)
          .catch(() => { /* names stay blank; the map is already usable */ });
      })
      .catch(() => setLoading(false));
  };

  // Ref-guarded: StrictMode double-invokes mount effects in dev, and fetching this
  // payload twice measurably slows every reload.
  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    loadData();
  }, []);

  // Probe the local control server once on mount so the stream proxy is known to
  // be available while viewing a camera (Settings closed). Re-probed on open below.
  useEffect(() => {
    let cancelled = false;
    fetch(`${SYNC_SERVER}/api/health`)
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then(() => { if (!cancelled) setSyncServerUp(true); })
      .catch(() => { if (!cancelled) setSyncServerUp(false); });
    return () => { cancelled = true; };
  }, []);

  // Probe the local control server whenever Settings opens — the Data Sync
  // controls only work when `python scripts/server.py` is running locally.
  useEffect(() => {
    if (!isSettingsOpen) return;
    let cancelled = false;
    fetch(`${SYNC_SERVER}/api/health`)
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then(() => { if (!cancelled) setSyncServerUp(true); })
      .catch(() => { if (!cancelled) setSyncServerUp(false); });
    return () => { cancelled = true; };
  }, [isSettingsOpen]);

  // Reset the sync UI back to the three idle buttons.
  const resetSync = () => {
    setSyncStatus('idle'); setSyncLog([]); setSyncSummary(null); setSyncTarget(null);
    setSyncProgress(null);
  };

  // Abort a run in progress — the fetch aborts, which closes the stream and the
  // server terminates the scraper subprocess on the broken pipe.
  const cancelScrape = () => { syncAbortRef.current?.abort(); };

  // Kick off a scrape and consume the server's newline-delimited JSON progress stream.
  const runScrape = async (target: string) => {
    const ctrl = new AbortController();
    syncAbortRef.current = ctrl;
    setSyncTarget(target);
    setSyncStatus('running');
    setSyncLog([]);
    setSyncSummary(null);
    setSyncProgress(null);
    try {
      const res = await fetch(`${SYNC_SERVER}/api/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
        signal: ctrl.signal,
      });
      if (res.status === 409) {
        setSyncStatus('error'); setSyncSummary('A sync is already running.'); setSyncTarget(null); return;
      }
      if (!res.ok || !res.body) throw new Error('server error');
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const l of lines) {
          if (!l.trim()) continue;
          let ev: { type: string; line?: string; message?: string; code?: number; before?: number; after?: number; delta?: number };
          try { ev = JSON.parse(l); } catch { continue; }
          if (ev.type === 'log') {
            const prog = parseProgress(ev.line as string);
            if (prog) {
              // Progress lines drive the bar instead of cluttering the raw log.
              setSyncProgress(prog);
            } else {
              setSyncLog(prev => [...prev.slice(-400), ev.line as string]);
            }
          } else if (ev.type === 'done') {
            const d = ev.delta;
            const sign = d != null && d >= 0 ? '+' : '';
            setSyncSummary(
              `${(ev.before ?? 0).toLocaleString()} → ${(ev.after ?? 0).toLocaleString()}` +
              (d != null ? ` (${sign}${d.toLocaleString()})` : '')
            );
            if (ev.code === 0) setSyncProgress(p => (p ? { ...p, pct: 100, eta: '0s' } : p));
            setSyncStatus(ev.code === 0 ? 'done' : 'error');
          } else if (ev.type === 'error') {
            setSyncSummary(ev.message ?? 'Scraper error'); setSyncStatus('error');
          }
        }
      }
      setSyncTarget(null);
    } catch {
      if (ctrl.signal.aborted) {
        resetSync();  // user cancelled — return cleanly to the idle buttons
      } else {
        setSyncStatus('error');
        setSyncSummary('Lost connection to the local server.');
        setSyncTarget(null);
      }
    }
  };

  // Keep the sync log pinned to the newest line.
  useEffect(() => {
    if (syncLogRef.current) syncLogRef.current.scrollTop = syncLogRef.current.scrollHeight;
  }, [syncLog]);

  const mapStyle = useMemo(() => "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json", []);

  // Display country name from a raw code + source (US sources consolidate).
  const countryNameFor = (code: string, source: string) => {
    const rawCountry = (code || '').toUpperCase();
    const src = (source || '').toLowerCase();
    const isUS = rawCountry === 'US' || rawCountry === 'USA' ||
                 src.includes('caltrans') || src.includes('road511') ||
                 src.includes('nyc_dot') || src.includes('iowa_dot');
    if (isUS) return 'United States';
    const key = (code || 'unknown').toUpperCase();
    if (MANUAL_OVERRIDES[key]) return MANUAL_OVERRIDES[key];
    const resolved = countries.getName(key, 'en');
    if (resolved) return resolved;
    return key.length <= 3 ? key : 'Global Sector';
  };

  // Country -> display name, computed once over the compact arrays (no per-camera objects).
  const countryStats = useMemo(() => {
    if (!data) return [] as [string, number][];
    const stats: Record<string, number> = {};
    for (let i = 0; i < data.count; i++) {
      const name = countryNameFor(data.ccDict[data.cc[i]], data.srcDict[data.src[i]]);
      stats[name] = (stats[name] || 0) + 1;
    }
    return Object.entries(stats).sort((a, b) => b[1] - a[1]);
  }, [data]);

  // Render set for both map paths: passes the country filter and is actually
  // displayable (mirrors isFeedWorking()) — hides dead points instead of cluttering the map.
  const filteredIndices = useMemo(() => {
    if (!data) return [] as number[];
    const idx: number[] = [];
    const active = new Set(filterCountries);
    for (let i = 0; i < data.count; i++) {
      if (!data.live[i] && data.de[i] !== 1) continue;
      if (active.size === 0 ||
          active.has(countryNameFor(data.ccDict[data.cc[i]], data.srcDict[data.src[i]]))) {
        idx.push(i);
      }
    }
    return idx;
  }, [data, filterCountries]);

  // GeoJSON source for the 3D globe path — built only when 3D is active so the
  // 2D default never materializes 100k+ features.
  const camerasGeoJson = useMemo(() => {
    if (!data || !is3D) return { type: 'FeatureCollection', features: [] as any[] };
    return {
      type: 'FeatureCollection',
      // Carry the array index, not id/streamUrl — MapLibre serializes every property
      // to its tiler worker, saving ~10MB and removing the need for an id->index lookup.
      features: filteredIndices.map(i => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [data.lon[i], data.lat[i]] },
        properties: { i, live: data.live[i] },
      })),
    };
  }, [data, is3D, filteredIndices]);

  const hoveredGeoJson = useMemo(() => ({
    type: 'FeatureCollection',
    features: hovered ? [hovered] : []
  }), [hovered]);

  const selectedGeoJson = useMemo(() => ({
    type: 'FeatureCollection',
    features: selectedCamera ? [selectedCamera] : []
  }), [selectedCamera]);

  // Auto-refreshes the selected camera's image per refreshIntervalMs; only the
  // selected feed polls.
  useEffect(() => {
    if (!selectedCamera || selectedCamera.properties.streamUrl) return; // streams self-refresh
    const ms = refreshIntervalMs(selectedCamera);
    refreshTimer.current = setInterval(() => {
      setImgCacheBust(Date.now());
      setLastRefresh(new Date());
      setImgLoaded(false);
    }, ms);
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current); };
  }, [selectedCamera?.properties.id]);

  // Resets all per-camera feed state when the selected camera changes.
  useEffect(() => {
    setImgLoaded(false);
    setImgCacheBust(Date.now());
    setLiveWindyUrl(null);
    setHlsFailed(false);
    setStaticFeedFailed(false);
    setImgLastLoaded(null);
    setLastImageHash(null);

    if (selectedCamera?.properties.source === 'windy') {
      const camId = selectedCamera.properties.id.replace('windy_', '');
      const apiKey = import.meta.env.VITE_WINDY_API_KEY;

      if (apiKey) {
        fetch(`https://api.windy.com/webcams/api/v3/webcams/${camId}?include=images`, {
          headers: { 'x-windy-api-key': apiKey }
        })
          .then(r => r.json())
          .then(json => {
            const images = json.images || {};
            const liveUrl = (images.current && images.current.preview) || (images.daylight && images.daylight.preview);
            if (liveUrl) setLiveWindyUrl(liveUrl);
          })
          .catch(e => console.error("Failed to fetch live windy token:", e));
      }
    }
  }, [selectedCamera?.properties.id]);

  const manualRefresh = () => {
    setImgCacheBust(Date.now());
    setLastRefresh(new Date());
    setImgLoaded(false);

    // If it's a windy camera, force a re-fetch of the token
    if (selectedCamera?.properties.source === 'windy') {
      const camId = selectedCamera.properties.id.replace('windy_', '');
      const apiKey = import.meta.env.VITE_WINDY_API_KEY;
      if (apiKey) {
        fetch(`https://api.windy.com/webcams/api/v3/webcams/${camId}?include=images`, {
          headers: { 'x-windy-api-key': apiKey }
        })
          .then(r => r.json())
          .then(json => {
            const images = json.images || {};
            const liveUrl = (images.current && images.current.preview) || (images.daylight && images.daylight.preview);
            if (liveUrl) setLiveWindyUrl(liveUrl);
          })
          .catch(e => console.error("Failed to fetch live windy token:", e));
      }
    }
  };

  const getLiveUrl = (url: string) => {
    if (selectedCamera?.properties.source === 'windy') {
      if (liveWindyUrl) return liveWindyUrl;
    }

    if (!url) return '';
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}_t=${imgCacheBust}`;
  };

  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement, Event>) => {
    setImgLoaded(true);
    const img = e.target as HTMLImageElement;

    try {
      // Create a small fingerprint of the image to detect actual content changes
      const canvas = document.createElement('canvas');
      canvas.width = 16;
      canvas.height = 16;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        // This will throw a security error if the image doesn't have CORS headers
        ctx.drawImage(img, 0, 0, 16, 16);
        const fingerprint = canvas.toDataURL('image/jpeg', 0.1);

        if (fingerprint !== lastImageHash) {
          setLastImageHash(fingerprint);
          setImgLastLoaded(new Date());
        }
      }
    } catch {
      // Fallback for non-CORS images: update timestamp on every successful load
      // because we can't inspect the pixels to know if it's the same.
      setImgLastLoaded(new Date());
    }
  };


  // Writes the coordinate HUD directly to the DOM, bypassing React.
  const writeCoord = (lng: number, lat: number) => {
    if (coordRef.current) coordRef.current.textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  };

  // Widens an exact-miss to a small box query so near-1px isolated nodes are still
  // clickable. Click-only — MapLibre's box query costs ~100ms, too slow for hover;
  // the 2D path gets the same forgiveness for free via deck's `pickingRadius`.
  const pickNear = (e: MapMouseEvent) => {
    const exact = e.features?.[0];
    if (exact) return exact;
    const { x, y } = e.point;
    const near = e.target.queryRenderedFeatures(
      [[x - PICK_RADIUS_PX, y - PICK_RADIUS_PX], [x + PICK_RADIUS_PX, y + PICK_RADIUS_PX]],
      { layers: ['camera-points'] }
    );
    let best = undefined, bestDist = PICK_RADIUS_PX ** 2;
    for (const f of near) {
      const p = e.target.project((f.geometry as GeoJSON.Point).coordinates as [number, number]);
      const dist = (p.x - x) ** 2 + (p.y - y) ** 2;
      if (dist < bestDist) { bestDist = dist; best = f; }
    }
    return best;
  };

  const onMapClick = (e: MapMouseEvent) => {
    const feature = pickNear(e);
    if (feature && data) {
      const i = feature.properties?.i;
      if (typeof i === 'number') selectCamera(i);
    }
  };

  const onMapMouseMove = (e: MapMouseEvent) => {
    const feature = e.features?.[0];
    const idx = feature && data ? (feature.properties?.i ?? -1) : -1;
    // Only update hover state when the hovered camera changes.
    if (idx !== hoveredIdxRef.current) {
      hoveredIdxRef.current = idx;
      setHovered(idx >= 0 && data ? camAt(data, idx, labels, null) : null);
    }
    if (e.lngLat && Number.isFinite(e.lngLat.lng) && Number.isFinite(e.lngLat.lat)) {
      writeCoord(e.lngLat.lng, e.lngLat.lat);
    }
    e.target.getCanvas().style.cursor = 'none';
  };

  // Partitioned once — the deck layers and HUD counts both read it.
  const { liveIdx, stillIdx } = useMemo(() => {
    const live: number[] = [], still: number[] = [];
    if (data) for (const i of filteredIndices) (data.live[i] ? live : still).push(i);
    return { liveIdx: live, stillIdx: still };
  }, [data, filteredIndices]);

  // Spin up the binning worker once and hand it the coordinates when they land.
  useEffect(() => {
    const w = new Worker(new URL('./binWorker.ts', import.meta.url), { type: 'module' });
    w.onmessage = (e: MessageEvent<{ reqId: number } & BinResult>) => {
      // Ignore results for a zoom level or filter set we've already moved past.
      if (e.data.reqId === binReqRef.current) setBins({ still: e.data.still, live: e.data.live });
    };
    binWorkerRef.current = w;
    return () => { w.terminate(); binWorkerRef.current = null; };
  }, []);

  useEffect(() => {
    if (data && binWorkerRef.current) {
      binWorkerRef.current.postMessage({ type: 'init', lon: data.lon, lat: data.lat });
    }
  }, [data]);

  // Rebin whenever the zoom level or the visible set changes. Never during a pan —
  // bins are computed in world space, so panning reuses them untouched.
  useEffect(() => {
    if (!data || !binWorkerRef.current) return;
    if (zoomLevel >= THIN_MAX_ZOOM) { setBins(null); return; }
    const reqId = ++binReqRef.current;
    binWorkerRef.current.postMessage({
      // One level finer: this set covers [zoomLevel, zoomLevel+1), and binning at the
      // low end would merge points still distinguishable near the top of that range.
      type: 'bin', reqId, zoom: zoomLevel + 1, dpr: window.devicePixelRatio || 1,
      still: stillIdx, live: liveIdx,
    });
  }, [data, zoomLevel, stillIdx, liveIdx]);

  const stillAlpha = nodeOpacity * 0.85;
  const liveAlpha = Math.min(1, nodeOpacity * 1.15);

  // N stacked dots at alpha a blend to 1-(1-a)^N; baking that into the one surviving
  // dot keeps density visually intact (n=1 still renders at base opacity).
  const stackedAlpha = (a: number, n: number) => Math.round(255 * (1 - Math.pow(1 - a, n)));

  const deckLayers = [
    // Static feeds: dimmer/smaller cyan, drawn underneath. Unbinned, color/opacity are
    // constants, so neither the opacity slider nor hover rebuilds the point buffers.
    new ScatterplotLayer<number>({
      id: 'deck-still',
      data: bins ? bins.still.idx : stillIdx,
      getPosition: (i: number) => (data ? [data.lon[i], data.lat[i]] : [0, 0]),
      getFillColor: bins
        ? (_: number, info: { index: number }) =>
          [0, 229, 255, stackedAlpha(stillAlpha, bins.still.count[info.index])]
        : [0, 229, 255],
      getRadius: 3000,
      radiusMinPixels: bins ? 0.7 * BIN_RADIUS_BOOST : 0.7,
      radiusMaxPixels: 5,
      opacity: bins ? 1 : stillAlpha,
      updateTriggers: { getFillColor: [bins, stillAlpha] },
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 255]
    }),
    // Live video feeds: larger, brighter green, drawn on top so they pop out of the field.
    new ScatterplotLayer<number>({
      id: 'deck-live',
      data: bins ? bins.live.idx : liveIdx,
      getPosition: (i: number) => (data ? [data.lon[i], data.lat[i]] : [0, 0]),
      getFillColor: bins
        ? (_: number, info: { index: number }) =>
          [0, 255, 136, stackedAlpha(liveAlpha, bins.live.count[info.index])]
        : [0, 255, 136],
      getRadius: 4500,
      radiusMinPixels: bins ? 1.3 * BIN_RADIUS_BOOST : 1.3,
      radiusMaxPixels: 8,
      opacity: bins ? 1 : liveAlpha,
      updateTriggers: { getFillColor: [bins, liveAlpha] },
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 255]
    }),
    ...(selectedCamera ? [
      new ScatterplotLayer<CameraFeature>({
        id: 'camera-highlight',
        data: [selectedCamera],
        getPosition: d => d.geometry.coordinates,
        getFillColor: [255, 60, 60, 0],
        getLineColor: [255, 60, 60, 255],
        lineWidthMinPixels: 1.5,
        getRadius: 8000,
        radiusMinPixels: 10,
        radiusMaxPixels: 16,
        stroked: true,
        filled: true,
        updateTriggers: { getPosition: selectedCamera }
      })
    ] : [])
  ];

  const counts = { live: liveIdx.length, still: stillIdx.length };

  const feedUrl = selectedCamera?.properties.feedUrl ?? '';
  const streamUrl = selectedCamera?.properties.streamUrl ?? '';
  // hasStream is true only when a stream URL exists AND it hasn't failed CORS/Network checks
  const hasStream = !!(streamUrl) && !hlsFailed;
  // Embeddable third-party player (e.g. YouTube live) — only ones whose host
  // passed the framing probe are ever marked directEligible, see host_prober.py
  const isIframe = selectedCamera?.properties.feedType === 'iframe';
  // TxDOT snapshots need a JSON fetch+decode (see TxdotSnapshot); staticFeedFailed
  // gates the whole panel once that's tried direct and via-proxy and both failed.
  const isTxdotJson = selectedCamera?.properties.feedType === 'txdot-json';
  const feedWorks = (selectedCamera ? isFeedWorking(selectedCamera) : false) && !staticFeedFailed;

  return (
    <div className="w-full h-screen relative bg-[#111419] overflow-hidden font-sans cursor-none">
      <style>{scrollbarStyles}</style>
      {/* Map */}
      {is3D ? (
        <MapGL
          key="globe-map"
          ref={globeRef as never}
          initialViewState={initialView}
          onMove={e => {
            if (e.viewState && Number.isFinite(e.viewState.latitude) && Number.isFinite(e.viewState.longitude)) {
              trackView(e.viewState);
            }
          }}
          mapStyle={mapStyle}
          projection={{ type: 'globe' }}
          onClick={onMapClick}
          onMouseMove={onMapMouseMove}
          onMouseLeave={() => {
            hoveredIdxRef.current = -1;
            setHovered(null);
            if (coordRef.current) coordRef.current.textContent = '—';
          }}
          interactiveLayerIds={['camera-points']}
        >
          <Source id="cameras" type="geojson" data={camerasGeoJson}>
            <Layer
              id="camera-points"
              type="circle"
              paint={{
                // Zoom interpolate must be outermost with the live/static `case` nested
                // inside each stop — MapLibre allows only one zoom interpolate per
                // expression; the reverse nesting fails style validation silently.
                'circle-radius': [
                  'interpolate', ['linear'], ['zoom'],
                  2, ['case', GLOBE_IS_LIVE, 1.8, 1.0],
                  6, ['case', GLOBE_IS_LIVE, 3.4, 2.2],
                  10, ['case', GLOBE_IS_LIVE, 6, 4],
                  14, ['case', GLOBE_IS_LIVE, 8, 5.5]
                ],
                'circle-color': [
                  'case',
                  GLOBE_IS_LIVE,
                  '#00ff88',
                  '#00e5ff'
                ],
                'circle-opacity': [
                  'case',
                  GLOBE_IS_LIVE,
                  Math.min(1, nodeOpacity * 1.15),
                  nodeOpacity * 0.85
                ],
                'circle-pitch-alignment': 'map',
                'circle-pitch-scale': 'map'
              }}
            />
          </Source>
          {selectedCamera && (
            <Source id="selected-source" type="geojson" data={selectedGeoJson}>
              <Layer
                id="selected-highlight"
                type="circle"
                paint={{
                  'circle-radius': [
                    'interpolate', ['linear'], ['zoom'],
                    2, 12,
                    10, 20
                  ],
                  'circle-color': 'rgba(255, 60, 60, 0)',
                  'circle-stroke-width': 2,
                  'circle-stroke-color': '#ff3c3c',
                  'circle-opacity': 1,
                  'circle-pitch-alignment': 'map'
                }}
              />
            </Source>
          )}
          {hovered && (
            <Source id="hovered-source" type="geojson" data={hoveredGeoJson}>
              <Layer
                id="hover-highlight"
                type="circle"
                paint={{
                  'circle-radius': [
                    'interpolate', ['linear'], ['zoom'],
                    2, 2,
                    10, 6,
                    14, 8
                  ],
                  'circle-color': '#ffffff',
                  'circle-stroke-width': 1,
                  'circle-stroke-color': '#ffffff',
                  'circle-opacity': 1,
                  'circle-pitch-alignment': 'map'
                }}
              />
            </Source>
          )}
          {showBorders && (
            <Source id="borders" type="geojson" data="https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson">
              <Layer
                id="country-borders"
                type="line"
                paint={{
                  'line-color': '#ffab00',
                  'line-width': 2.5,
                  'line-opacity': 0.8
                }}
              />
            </Source>
          )}
        </MapGL>
      ) : (
        <DeckGL
          key="tactical-deck"
          initialViewState={initialView}
          onViewStateChange={e => {
            if (e.viewState && Number.isFinite(e.viewState.latitude) && Number.isFinite(e.viewState.longitude)) {
              trackView(e.viewState);
            }
          }}
          controller={true}
          layers={deckLayers}
          pickingRadius={PICK_RADIUS_PX}
          onHover={({ object, coordinate }) => {
            // object is a camera index into filteredIndices (0 is valid) or null.
            const idx = (object as number | null) ?? -1;
            // Only touch React state when the hovered camera actually changes.
            if (idx !== hoveredIdxRef.current) {
              hoveredIdxRef.current = idx;
              setHovered(idx >= 0 && data ? camAt(data, idx, labels, null) : null);
            }
            if (coordinate) writeCoord((coordinate as number[])[0], (coordinate as number[])[1]);
          }}
          onClick={({ object }) => {
            const i = object as number | null;
            if (i != null) selectCamera(i);
          }}
          getCursor={() => 'none'}
        >
          <MapGL
            key="mercator-map"
            mapStyle={mapStyle}
            projection={{ type: 'mercator' }}
          >
            {showBorders && (
              <Source id="borders-2d" type="geojson" data="https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson">
                <Layer
                  id="country-borders-2d"
                  type="line"
                  paint={{
                    'line-color': '#ffab00',
                    'line-width': 2.0,
                    'line-opacity': 0.7
                  }}
                />
              </Source>
            )}
          </MapGL>
        </DeckGL>
      )}

      {/* Vignette */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.7) 100%)' }} />

      {/* ── MOUSE COORDINATES ── */}
      {data && (
        <div className="absolute bottom-8 left-8 z-30 pointer-events-none">
          <div className="bg-[#05090C]/70 backdrop-blur-xl rounded-xl border border-white/10 px-6 py-4 flex flex-col gap-2 shadow-2xl">
            <div className="flex items-center gap-4">
              <Scan className="w-5 h-5 text-[#00e5ff]" />
              <span ref={coordRef} className="text-[#00e5ff] text-base font-mono tracking-widest font-semibold">
                —
              </span>
            </div>
            <div className="flex items-center gap-4 border-t border-white/5 pt-2">
              <Activity className="w-4 h-4 text-gray-500" />
              <span className="text-gray-500 text-xs font-mono uppercase tracking-[0.2em]">
                Zoom: <span ref={zoomRef} className="text-white" />
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── ARGUS HUD — TOP RIGHT ── */}
      <div style={{ position: 'absolute', top: 32, right: 32, zIndex: 30, width: 340 }} className="pointer-events-auto">
        <motion.div
          animate={{ height: isHudMinimized ? 120 : 'auto' }}
          className="bg-[#05090C]/70 backdrop-blur-2xl rounded-3xl border border-white/10 p-8 shadow-2xl relative overflow-hidden"
        >
          {/* Logo Section */}
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h1 className="text-4xl font-extrabold tracking-tighter text-white">ARGUS</h1>
              <div className="flex items-center gap-2 mt-2">
                <Scan className="w-4 h-4 text-[#00e5ff]" />
                <p className="text-xs text-[#00e5ff] font-mono tracking-widest uppercase">Global Network</p>
              </div>
            </div>
            <button
              onClick={() => setIsHudMinimized(!isHudMinimized)}
              className="p-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-gray-400 hover:text-white transition-all outline-none"
            >
              {isHudMinimized ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
            </button>
          </div>

          <AnimatePresence>
            {!isHudMinimized && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                {/* Stats Grid */}
                <div className="grid grid-cols-2 gap-4 mb-8">
                  <div className="bg-[#0A1015]/40 rounded-2xl p-5 border border-white/5 relative overflow-hidden group">
                    <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                    <p className="text-gray-500 text-[10px] uppercase tracking-widest font-semibold mb-2">Total Nodes</p>
                    <p className="text-white font-mono text-2xl font-semibold">
                      {loading ? '—' : (counts.live + counts.still).toLocaleString()}
                    </p>
                  </div>
                  <div className="bg-[#0A1015]/40 rounded-2xl p-5 border border-[#00ff88]/20 relative overflow-hidden group">
                    <div className="absolute inset-0 bg-gradient-to-br from-[#00ff88]/10 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                    <p className="text-[#00ff88]/70 text-[10px] uppercase tracking-widest font-semibold mb-2">System Status</p>
                    <p className="text-[#00ff88] font-mono text-lg font-semibold flex items-center gap-2">
                      <Activity className="w-4 h-4 animate-pulse" /> Active
                    </p>
                  </div>
                </div>

                {/* Legend Details */}
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-3 rounded-xl bg-white/5 border border-white/5">
                    <div className="flex items-center gap-3">
                      <div className="w-2.5 h-2.5 rounded-full bg-[#00ff88] shadow-[0_0_12px_#00ff88]" />
                      <span className="text-sm font-medium text-gray-200">Live Video</span>
                    </div>
                    <span className="text-sm font-mono font-semibold text-white">
                      {counts.live.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex items-center justify-between p-3 rounded-xl bg-white/5 border border-white/5">
                    <div className="flex items-center gap-3">
                      <div className="w-2.5 h-2.5 rounded-full bg-[#00e5ff] shadow-[0_0_12px_#00e5ff]" />
                      <span className="text-sm font-medium text-gray-200">Static Feed</span>
                    </div>
                    <span className="text-sm font-mono font-semibold text-white">
                      {counts.still.toLocaleString()}
                    </span>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>

      {/* ── Hover Tooltip ── */}
      <AnimatePresence>
        {hovered && (
          <motion.div
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 5, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="absolute bottom-12 left-1/2 -translate-x-1/2 z-50 pointer-events-none"
          >
            <div className="bg-[#05090C]/90 backdrop-blur-xl rounded-2xl border border-white/10 p-4 shadow-2xl flex items-center gap-4 min-w-[300px]">
              <div className="flex-shrink-0 w-10 h-10 rounded-full bg-white/5 flex items-center justify-center border border-white/10">
                <Scan className="w-5 h-5 text-[#00e5ff]" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-white text-sm font-bold tracking-tight truncate mb-1">
                  {hovered.properties.name}
                </p>
                <div className="flex items-center gap-2">
                  <div className={`w-1.5 h-1.5 rounded-full ${hovered.properties.live ? 'bg-[#00ff88]' : 'bg-[#00e5ff]'}`} />
                  <p className="text-gray-400 text-xs font-mono truncate">
                    {formatLocation(hovered)}
                  </p>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Camera Feed Panel — DRAGGABLE RND ── */}
      {selectedCamera ? (
        <Rnd
          default={{ x: 40, y: 40, width: 400, height: 560 }}
          minWidth={320}
          minHeight={400}
          bounds="window"
          dragHandleClassName="drag-handle"
          className="z-40"
        >
          {/* Main Container - Strict Flex Column */}
          <div className="bg-[#05090C]/75 backdrop-blur-3xl rounded-3xl border border-white/10 flex flex-col h-full shadow-[0_0_50px_rgba(0,0,0,0.8)] overflow-hidden">

            {/* 1. Header Area (Fixed height) */}
            <div className="drag-handle cursor-move flex items-center justify-between p-6 bg-white/5 border-b border-white/10 flex-shrink-0">
              <div className="flex-1 min-w-0 pr-4">
                <div className="flex items-center gap-2 mb-1.5">
                  {selectedCamera.properties.source === 'windy' ? (
                    <span className="text-[10px] font-semibold tracking-wide text-gray-500">
                      Webcams provided by <a href="https://www.windy.com/" target="_blank" rel="noopener noreferrer" className="text-[#00e5ff] hover:underline hover:text-white transition-colors">windy.com</a> &mdash; <a href="https://www.windy.com/webcams/add" target="_blank" rel="noopener noreferrer" className="text-[#00e5ff] hover:underline hover:text-white transition-colors">add a webcam</a>
                    </span>
                  ) : (
                    <span className="text-[10px] font-bold tracking-widest uppercase text-gray-500">
                      {selectedCamera.properties.source ?? selectedCamera.properties.type}
                    </span>
                  )}
                  {(hasStream || isIframe) && (
                    <span className="text-[#00ff88] text-[9px] font-bold tracking-widest bg-[#00ff88]/10 px-1.5 py-0.5 rounded flex items-center border border-[#00ff88]/20">
                      <Video className="w-3 h-3 mr-1" /> LIVE
                    </span>
                  )}
                </div>
                <h2 className="text-white text-lg font-bold tracking-tight truncate">
                  {selectedCamera.properties.name}
                </h2>
                <div className="flex items-center gap-1.5 mt-1">
                  <MapPin className="w-3.5 h-3.5 text-[#00e5ff]" />
                  <p className="text-gray-400 text-xs truncate">{formatLocation(selectedCamera)}</p>
                </div>
              </div>

              {/* Controls */}
              <div className="flex items-center gap-2 flex-shrink-0">
                {!hasStream && !isIframe && feedWorks && (
                  <button onClick={manualRefresh} title="Refresh Feed"
                    className="w-10 h-10 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 flex items-center justify-center text-white transition-all group outline-none">
                    <RefreshCw className="w-4 h-4 group-hover:rotate-180 transition-transform duration-500" />
                  </button>
                )}
                <button onClick={() => setSelectedCamera(null)} title="Close"
                  className="w-10 h-10 rounded-full bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 flex items-center justify-center text-red-400 hover:text-red-300 transition-all outline-none">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* 2. Media Area (Flex Grow) */}
            <div className="flex-1 relative bg-black flex flex-col min-h-0 border-b border-white/10">
              {feedWorks ? (
                <>
                  {!hasStream && !isIframe && (
                    <div className="absolute bottom-4 left-4 z-10 bg-black/80 backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2 border border-[#00e5ff]/30">
                      <div className="w-2 h-2 rounded-full bg-[#00e5ff] shadow-[0_0_8px_#00e5ff]" />
                      <span className="text-[#00e5ff] text-[10px] font-bold tracking-widest">STATIC</span>
                    </div>
                  )}
                  {hasStream ? (
                    <HlsPlayer url={streamUrl || feedUrl} cacheBust={imgCacheBust} onFallback={() => setHlsFailed(true)} proxyBase={syncServerUp === true ? PROXY_BASE : ''} />
                  ) : isIframe ? (
                    <iframe
                      key={selectedCamera.properties.id}
                      src={feedUrl}
                      title={selectedCamera.properties.name}
                      className="w-full h-full border-0"
                      sandbox="allow-scripts allow-same-origin allow-presentation"
                      allow="autoplay; encrypted-media; picture-in-picture"
                    />
                  ) : isTxdotJson ? (
                    <TxdotSnapshot url={feedUrl} cacheBust={imgCacheBust} onFallback={() => setStaticFeedFailed(true)} proxyBase={syncServerUp === true ? PROXY_BASE : ''} />
                  ) : (
                    <>
                      {!imgLoaded && (
                        <div className="absolute inset-0 flex items-center justify-center bg-black/50 backdrop-blur-sm z-10">
                          <div className="w-8 h-8 border-2 border-white/10 border-t-[#00e5ff] rounded-full animate-spin" />
                        </div>
                      )}
                      <img
                        key={`${selectedCamera.properties.id}-${imgCacheBust}`}
                        src={getLiveUrl(selectedCamera.properties.feedUrl)}
                        referrerPolicy={selectedCamera.properties.source?.startsWith('opencctv_') ? "no-referrer" : "strict-origin-when-cross-origin"}
                        crossOrigin={selectedCamera.properties.source?.startsWith('opencctv_') ? undefined : (CORS_ENABLED_DOMAINS.some(d => selectedCamera.properties.feedUrl.includes(d)) ? "anonymous" : undefined)}
                        alt={selectedCamera.properties.name}
                        className="w-full h-full object-contain"
                        style={{ opacity: imgLoaded ? 1 : 0 }}
                        onLoad={handleImageLoad}
                        onError={(e) => {
                          const img = e.target as HTMLImageElement;
                          img.style.display = 'none';
                          const fb = img.nextElementSibling as HTMLElement;
                          if (fb) fb.style.display = 'flex';
                          setImgLoaded(true);
                        }}
                      />
                    </>
                  )}
                  <div className="hidden absolute inset-0 flex-col items-center justify-center text-center p-6 bg-[#05090C]">
                    <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                      <Eye className="w-8 h-8 text-red-500" />
                    </div>
                    <p className="text-white text-base font-bold mb-1">Feed Offline</p>
                    <p className="text-gray-500 text-xs">Connection to the camera was lost.</p>
                  </div>
                </>
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center text-center p-8 bg-[#05090C]">
                  <div className="w-16 h-16 rounded-full bg-white/5 flex items-center justify-center mb-4 border border-white/10">
                    <Eye className="w-8 h-8 text-gray-500" />
                  </div>
                  <p className="text-white text-base font-bold mb-2">Feed Unavailable</p>
                  <p className="text-gray-500 text-xs max-w-[250px] leading-relaxed">
                    This camera host prevents external embedding or the feed requires authentication.
                  </p>
                </div>
              )}
            </div>

            {/* 3. Footer Area (Fixed height) */}
            <div className="p-6 bg-[#05090C] flex-shrink-0">
              <div className="grid grid-cols-1 gap-4">
                {(selectedCamera.properties.route || selectedCamera.properties.highway) && (
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500 text-xs font-semibold tracking-wide uppercase">Route</span>
                    <span className="text-white text-sm font-medium truncate ml-4">
                      {selectedCamera.properties.route || `HWY ${selectedCamera.properties.highway}`}
                    </span>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-gray-500 text-xs font-semibold tracking-wide uppercase">Location</span>
                  <span className="text-gray-300 text-sm font-mono truncate ml-4">
                    {selectedCamera.geometry.coordinates[1].toFixed(4)}, {selectedCamera.geometry.coordinates[0].toFixed(4)}
                  </span>
                </div>
                {feedWorks && (
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500 text-xs font-semibold tracking-wide uppercase flex items-center gap-1.5">
                      <Clock className="w-4 h-4" /> Last Sync
                    </span>
                    <div className="flex flex-col items-end">
                      <span className="text-[#00e5ff] text-sm font-mono truncate ml-4">
                        {formatTime(lastRefresh)}
                      </span>
                      {selectedCamera.properties.source === 'tfl_london' && (
                        <span className="text-gray-500 text-[9px] mt-0.5 tracking-wider uppercase">Source updates every 5m</span>
                      )}
                    </div>
                  </div>
                )}
                {feedWorks && !hasStream && imgLastLoaded && (
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500 text-xs font-semibold tracking-wide uppercase flex items-center gap-1.5">
                      <Clock className="w-4 h-4" /> Image Updated
                    </span>
                    <span className="text-emerald-400 text-sm font-mono truncate ml-4">
                      {formatTime(imgLastLoaded)}
                    </span>
                  </div>
                )}
              </div>
            </div>

          </div>
        </Rnd>
      ) : null}

      {/* ── SETTINGS MENU ── */}
      <div className="absolute bottom-8 right-8 z-50 flex flex-col items-end gap-4">
        <AnimatePresence>
          {isSettingsOpen && (
            <motion.div
              initial={{ opacity: 0, x: 20, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 20, scale: 0.95 }}
              className="absolute bottom-0 right-20 bg-[#05090C]/90 backdrop-blur-2xl rounded-3xl border border-white/10 p-6 shadow-2xl min-w-[320px] max-h-[440px] flex flex-col z-50"
            >
              <div className="flex items-center justify-between mb-6 flex-shrink-0">
                <div>
                  <h3 className="text-white text-lg font-bold tracking-tight flex items-center gap-2">
                    <Settings className="w-5 h-5 text-[#00e5ff]" /> System Config
                  </h3>
                  <p className="text-gray-500 text-[10px] uppercase tracking-widest mt-1">Configure Dashboard HUD</p>
                </div>
                <button
                  onClick={() => setIsSettingsOpen(false)}
                  className="w-8 h-8 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-gray-400 hover:text-white hover:border-white/20 transition-all"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Scrollable body — capped panel height so it never overlaps the top-right HUD */}
              <div className="flex-1 min-h-0 overflow-y-auto pr-2 -mr-3 custom-scrollbar">
              <div className="space-y-6">
                <div className="flex items-center justify-between gap-8">
                  <div>
                    <p className="text-gray-200 text-sm font-semibold">Military Time</p>
                    <p className="text-gray-500 text-[10px] uppercase tracking-wider mt-1">24-hour format</p>
                  </div>
                  <button
                    onClick={() => setUse24Hour(!use24Hour)}
                    className={`w-12 h-6 rounded-full transition-all duration-300 relative border ${use24Hour ? 'bg-[#00e5ff]/20 border-[#00e5ff]/50' : 'bg-white/5 border-white/10'
                      }`}
                  >
                    <motion.div
                      animate={{ x: use24Hour ? 26 : 4 }}
                      className={`absolute top-1 w-3.5 h-3.5 rounded-full shadow-lg ${use24Hour ? 'bg-[#00e5ff]' : 'bg-gray-500'
                        }`}
                    />
                  </button>
                </div>

                <div className="flex flex-col gap-4 mb-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-gray-200 text-sm font-semibold">Node Opacity</p>
                      <p className="text-gray-500 text-[10px] uppercase tracking-wider mt-1">Adjust point visibility</p>
                    </div>
                    <span className="text-[#00e5ff] font-mono text-xs">{Math.round(nodeOpacity * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={nodeOpacity}
                    onChange={(e) => setNodeOpacity(parseFloat(e.target.value))}
                    className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer accent-[#00e5ff]"
                  />
                </div>

                <div className="flex items-center justify-between gap-8 mb-4">
                  <div>
                    <p className="text-gray-200 text-sm font-semibold">Country Borders</p>
                    <p className="text-gray-500 text-[10px] uppercase tracking-wider mt-1">Show political boundaries</p>
                  </div>
                  <button
                    onClick={() => setShowBorders(!showBorders)}
                    className={`w-12 h-6 rounded-full transition-all duration-300 relative border ${showBorders ? 'bg-[#ffab00]/20 border-[#ffab00]/50' : 'bg-white/5 border-white/10'
                      }`}
                  >
                    <motion.div
                      animate={{ x: showBorders ? 26 : 4 }}
                      className={`absolute top-1 w-3.5 h-3.5 rounded-full shadow-lg ${showBorders ? 'bg-[#ffab00]' : 'bg-gray-500'
                        }`}
                    />
                  </button>
                </div>

                <div className="flex items-center justify-between gap-8">
                  <div>
                    <p className="text-gray-200 text-sm font-semibold">3D Globe Mode</p>
                    <p className="text-gray-500 text-[10px] uppercase tracking-wider mt-1">Render world sphere</p>
                  </div>
                  <button
                    onClick={() => setIs3D(!is3D)}
                    className={`w-12 h-6 rounded-full transition-all duration-300 relative border ${is3D ? 'bg-emerald-500/20 border-emerald-500/50' : 'bg-white/5 border-white/10'
                      }`}
                  >
                    <motion.div
                      animate={{ x: is3D ? 26 : 4 }}
                      className={`absolute top-1 w-3.5 h-3.5 rounded-full shadow-lg ${is3D ? 'bg-emerald-500' : 'bg-gray-500'
                        }`}
                    />
                  </button>
                </div>
              </div>

              {/* ── DATA SYNC (local dev server) ── */}
              <div className="mt-6 pt-6 border-t border-white/5">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-gray-200 text-sm font-semibold flex items-center gap-2">
                    <RefreshCw className={`w-4 h-4 text-[#00e5ff] ${syncStatus === 'running' ? 'animate-spin' : ''}`} />
                    Data Sync
                  </p>
                  <span className={`text-[9px] font-mono uppercase tracking-widest px-2 py-0.5 rounded ${
                    syncServerUp === true ? 'text-[#00ff88] bg-[#00ff88]/10'
                    : syncServerUp === false ? 'text-gray-500 bg-white/5'
                    : 'text-gray-500'}`}>
                    {syncServerUp === true ? 'Online' : syncServerUp === false ? 'Offline' : 'Checking…'}
                  </span>
                </div>
                <p className="text-gray-500 text-[10px] uppercase tracking-wider mb-3">Refresh cameras from source</p>

                {syncServerUp === false ? (
                  <div className="text-[11px] text-gray-500 bg-white/5 border border-white/10 rounded-xl px-3 py-3 leading-relaxed">
                    Local only. Start the control server, then reopen:
                    <code className="block mt-1 text-[#00e5ff] font-mono text-[10px]">python scripts/server.py</code>
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-3 gap-2">
                      {(['native', 'opencctv', 'both'] as const).map(t => (
                        <button
                          key={t}
                          disabled={syncStatus === 'running' || syncServerUp !== true}
                          onClick={() => runScrape(t)}
                          className={`px-2 py-2 rounded-xl border text-[11px] font-semibold capitalize transition-all ${
                            syncTarget === t
                              ? 'bg-[#00e5ff]/20 border-[#00e5ff]/50 text-[#00e5ff]'
                              : 'bg-white/5 border-white/10 text-gray-300 hover:border-white/25 disabled:opacity-40 disabled:hover:border-white/10'
                          }`}
                        >
                          {t === 'opencctv' ? 'OpenCCTV' : t}
                        </button>
                      ))}
                    </div>

                    {syncStatus !== 'idle' && (
                      <div className="mt-3">
                        {/* Progress bar + ETA, driven by the scraper's [PROGRESS] lines. */}
                        {syncProgress && (
                          <div className="mb-2">
                            <div className="flex items-center justify-between text-[10px] font-mono mb-1">
                              <span className="text-[#00e5ff] uppercase tracking-wider">{syncProgress.plugin}</span>
                              <span className="text-gray-400">
                                {syncProgress.pct}%
                                {syncStatus === 'running' && syncProgress.eta !== '—' && (
                                  <span className="text-gray-500"> · eta {syncProgress.eta}</span>
                                )}
                              </span>
                            </div>
                            <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                              <div
                                className="h-full bg-gradient-to-r from-[#00e5ff] to-[#00ff88] rounded-full transition-all duration-500 ease-out"
                                style={{ width: `${syncProgress.pct}%` }}
                              />
                            </div>
                          </div>
                        )}
                        <div
                          ref={syncLogRef}
                          className="h-28 overflow-y-auto bg-black/40 border border-white/10 rounded-xl px-3 py-2 font-mono text-[10px] leading-relaxed text-gray-400 whitespace-pre-wrap custom-scrollbar"
                        >
                          {syncLog.length === 0
                            ? <span className="text-gray-600">Starting {syncTarget}…</span>
                            : syncLog.map((l, i) => <div key={i}>{l}</div>)}
                        </div>
                        <div className="flex items-center justify-between mt-2 gap-2">
                          <span className={`text-[11px] font-mono truncate ${
                            syncStatus === 'done' ? 'text-[#00ff88]'
                            : syncStatus === 'error' ? 'text-red-400'
                            : 'text-[#00e5ff]'}`}>
                            {syncStatus === 'running' ? `Syncing ${syncTarget}…`
                              : syncStatus === 'done' ? `Done · ${syncSummary}`
                              : syncSummary}
                          </span>
                          <div className="flex items-center gap-2 flex-shrink-0">
                            {syncStatus === 'running' && (
                              <button
                                onClick={cancelScrape}
                                className="px-3 py-1.5 rounded-lg bg-red-500/15 border border-red-500/40 text-red-400 text-[11px] font-semibold hover:bg-red-500/25 transition-all"
                              >
                                Cancel
                              </button>
                            )}
                            {syncStatus === 'done' && (
                              <button
                                onClick={() => loadData(true)}
                                className="px-3 py-1.5 rounded-lg bg-[#00ff88]/15 border border-[#00ff88]/40 text-[#00ff88] text-[11px] font-semibold hover:bg-[#00ff88]/25 transition-all whitespace-nowrap"
                              >
                                Reload map
                              </button>
                            )}
                            {(syncStatus === 'done' || syncStatus === 'error') && (
                              <button
                                onClick={resetSync}
                                className="px-3 py-1.5 rounded-lg bg-white/5 border border-white/15 text-gray-300 text-[11px] font-semibold hover:border-white/30 transition-all"
                              >
                                Dismiss
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>

              <div className="mt-6 pt-6 border-t border-white/5">
                <p className="text-[10px] text-gray-600 font-mono text-center uppercase tracking-widest">Argus v1.4.2 · Secure</p>
              </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── SECTOR FILTER PANEL ── */}
        <AnimatePresence>
          {isFilterOpen && (
            <motion.div
              initial={{ opacity: 0, x: 20, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 20, scale: 0.95 }}
              className="absolute bottom-0 right-20 w-80 h-[440px] bg-[#05090C]/90 backdrop-blur-2xl rounded-3xl border border-white/10 p-6 shadow-2xl z-50 overflow-hidden flex flex-col"
            >
              <div className="flex items-center justify-between mb-6 flex-shrink-0">
                <div>
                  <h3 className="text-white text-lg font-bold tracking-tight">Sector Filter</h3>
                  <div className="flex items-center gap-2 mt-1">
                    <p className="text-gray-500 text-[10px] uppercase tracking-widest">
                      {filterCountries.length === 0 ? 'Showing All Sectors' : `${filterCountries.length} Sectors Active`}
                    </p>
                    {filterCountries.length > 0 && (
                      <button
                        onClick={() => setFilterCountries([])}
                        className="text-[10px] text-[#00e5ff] font-mono uppercase tracking-widest hover:text-white transition-colors"
                      >
                        (Reset)
                      </button>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => setIsFilterOpen(false)}
                  className="w-8 h-8 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-gray-400 hover:text-white hover:border-white/20 transition-all"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="mb-4 relative">
                <input 
                  type="text"
                  placeholder="SEARCH SECTORS..."
                  value={filterSearch}
                  onChange={(e) => setFilterSearch(e.target.value)}
                  className="w-full bg-white/5 border border-white/10 rounded-xl py-2 px-4 text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-[#00e5ff]/50 focus:bg-white/10 transition-all font-mono tracking-widest"
                />
                {filterSearch && (
                  <button 
                    onClick={() => setFilterSearch('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white transition-colors"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>

              <div className="flex-1 overflow-y-auto pr-2 space-y-2 custom-scrollbar">
                {countryStats
                  .filter(([name]) => name.toLowerCase().includes(filterSearch.toLowerCase()))
                  .map(([name, count]) => {
                  const isActive = filterCountries.includes(name);
                  return (
                    <button
                      key={name}
                      onClick={() => {
                        if (isActive) {
                          setFilterCountries(filterCountries.filter(c => c !== name));
                        } else {
                          setFilterCountries([...filterCountries, name]);
                        }
                      }}
                      className={`w-full flex items-center justify-between p-3 rounded-xl border transition-all ${isActive
                        ? 'bg-[#00e5ff]/10 border-[#00e5ff]/30 text-[#00e5ff]'
                        : 'bg-white/5 border-white/5 text-gray-400 hover:bg-white/10 hover:border-white/10'
                        }`}
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className={`w-4 h-4 rounded border flex items-center justify-center transition-colors ${isActive ? 'bg-[#00e5ff] border-[#00e5ff]' : 'border-white/20'
                          }`}>
                          {isActive && <Check className="w-3 h-3 text-[#05090C]" />}
                        </div>
                        <span className="text-xs font-medium truncate">{name}</span>
                      </div>
                      <span className={`text-[10px] font-mono font-bold ${isActive ? 'text-[#00e5ff]' : 'text-gray-500'}`}>
                        {count.toLocaleString()}
                      </span>
                    </button>
                  );
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <button
          onClick={openRandomCamera}
          title="Open Random Camera"
          className="w-14 h-14 rounded-full bg-[#05090C]/70 backdrop-blur-xl border border-white/10 text-gray-400 hover:text-[#00ff88] hover:border-[#00ff88]/50 flex items-center justify-center transition-all duration-300 shadow-2xl group"
        >
          <Shuffle className="w-6 h-6 group-hover:scale-110 transition-transform" />
        </button>

        <button
          onClick={() => {
            setIsFilterOpen(!isFilterOpen);
            if (isSettingsOpen) setIsSettingsOpen(false);
          }}
          className={`w-14 h-14 rounded-full flex items-center justify-center transition-all duration-500 shadow-2xl border ${isFilterOpen
            ? 'bg-[#00e5ff] border-[#00e5ff] text-[#05090C] scale-110'
            : 'bg-[#05090C]/70 backdrop-blur-xl border-white/10 text-gray-400 hover:text-white hover:border-white/20'
            }`}
        >
          <Filter className="w-6 h-6" />
        </button>

        <button
          onClick={() => {
            setIsSettingsOpen(!isSettingsOpen);
            if (isFilterOpen) setIsFilterOpen(false);
          }}
          className={`w-14 h-14 rounded-full flex items-center justify-center transition-all duration-500 shadow-2xl border ${isSettingsOpen
            ? 'bg-[#00e5ff] border-[#00e5ff] text-[#05090C] rotate-90 scale-110'
            : 'bg-[#05090C]/70 backdrop-blur-xl border-white/10 text-gray-400 hover:text-white hover:border-white/20'
            }`}
        >
          <Settings className={`w-6 h-6 ${isSettingsOpen ? 'animate-none' : 'group-hover:rotate-45 transition-transform'}`} />
        </button>
      </div>

      <CrosshairCursor />
    </div>
  );
}

export default App;
