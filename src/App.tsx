import { useState, useEffect, useRef } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer } from '@deck.gl/layers';
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Scan, Eye, Activity, X, MapPin, RefreshCw, Clock, Video, ChevronUp, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Rnd } from 'react-rnd';
import Hls from 'hls.js';

function HlsPlayer({ url, cacheBust, onFallback }: { url: string; cacheBust?: number; onFallback?: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!videoRef.current) return;

    // Direct MP4 — use native video src
    if (url.toLowerCase().includes('.mp4')) {
      const sep = url.includes('?') ? '&' : '?';
      videoRef.current.src = cacheBust ? `${url}${sep}_t=${cacheBust}` : url;
      videoRef.current.play().catch(e => console.log('Autoplay prevented', e));
      return;
    }

    let hls: Hls | null = null;

    if (Hls.isSupported()) {
      hls = new Hls({ enableWorker: false });
      hls.loadSource(url);
      hls.attachMedia(videoRef.current);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        videoRef.current?.play().catch(e => console.log('Autoplay prevented', e));
      });
      // Fatal error (403, network, parse failure) → signal parent to fall back to static image
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (data.fatal) {
          console.warn(`HLS fatal error on ${url}:`, data.type, data.details);
          hls?.destroy();
          onFallback?.();
        }
      });
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      videoRef.current.src = url;
      videoRef.current.addEventListener('loadedmetadata', () => {
        videoRef.current?.play().catch(e => console.log('Autoplay prevented', e));
      });
      // Native HLS error fallback
      videoRef.current.addEventListener('error', () => onFallback?.());
    } else {
      // HLS not supported at all
      onFallback?.();
    }

    return () => { if (hls) hls.destroy(); };
  }, [url]);

  useEffect(() => {
    if (!videoRef.current || !url.toLowerCase().includes('.mp4')) return;
    const sep = url.includes('?') ? '&' : '?';
    videoRef.current.src = cacheBust ? `${url}${sep}_t=${cacheBust}` : url;
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

const INITIAL_VIEW_STATE = {
  longitude: 10,
  latitude: 25,
  zoom: 2,
  pitch: 30,
  bearing: 0,
  minZoom: 1.5
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
}

interface CameraFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: CameraProperties;
}

const COUNTRY_NAMES: Record<string, string> = {
  US: 'United States', CA: 'Canada', GB: 'United Kingdom', FR: 'France',
  DE: 'Germany', IT: 'Italy', ES: 'Spain', JP: 'Japan', AU: 'Australia',
  NZ: 'New Zealand', SG: 'Singapore', HK: 'Hong Kong', AE: 'UAE',
  IN: 'India', BR: 'Brazil', AR: 'Argentina', MX: 'Mexico',
  GR: 'Greece', PT: 'Portugal', AT: 'Austria', NL: 'Netherlands',
  CH: 'Switzerland', ZA: 'South Africa', EG: 'Egypt', ID: 'Indonesia', TH: 'Thailand',
};

// Domains confirmed to serve embeddable images without hotlink protection
const WORKING_IMAGE_DOMAINS = [
  // ── Existing dedicated plugins ──────────────────────────────────────────
  'drivebc.ca',           // DriveBC — British Columbia
  'cwwp2.dot.ca.gov',    // Caltrans — California
  'images.data.gov.sg',  // Singapore LTA
  'imgproxy.windy.com',  // Windy Webcams
  'webcams.nyctmc.org',  // NYC DOT
  'nzta.govt.nz',        // New Zealand NZTA
  'tfl.gov.uk',          // London TfL
  'amazonaws.com',       // S3-hosted feeds (TfL, Iowa, SC, etc.)
  'cloudfront.net',      // CloudFront CDN

  // ── Road511 USA state DOT domains ───────────────────────────────────────
  'fl511.com',           // Florida (4,136 cameras)
  'udottraffic.utah.gov',// Utah (2,035 cameras)
  '511ny.org',           // New York state (1,702 cameras)
  'wsdot.wa.gov',        // Washington (1,452 cameras)
  'carsprogram.org',     // Indiana / Colorado / Kansas (CARS camera network)
  'tripcheck.com',       // Oregon TripCheck
  'skyvdn.com',          // Iowa / South Carolina DOT CDN
  'tnsnapshots.com',     // Tennessee (668 cameras)
  'az511.com',           // Arizona (643 cameras)
  'idrivearkansas.com',  // Arkansas (545 cameras)
  'iowadot.gov',         // Iowa DOT (atmsqf.iowadot.gov RWIS snapshots)
  'dot.state.oh.us',     // Ohio (itscameras.dot.state.oh.us)
  'nebraska.gov',        // Nebraska (dot511.nebraska.gov)
  'deldot.gov',          // Delaware (video.deldot.gov)
  'kcscout.net',         // Kansas City Scout cameras
  'trimarc.org',         // Kentucky (Louisville TRIMARC)
  'wyoroad.info',        // Wyoming (www.wyoroad.info)
  'dot.nd.gov',          // North Dakota
  'streamlock.net',      // Massachusetts (Wowza streaming)
  'iteris-atis.com',     // South Dakota
  'trafficnz.info',      // New Zealand (trafficnz.info highway cameras)
];

function isFeedWorking(feedUrl: string, streamUrl?: string): boolean {
  // A feed works if EITHER the image URL is on a known-good domain,
  // OR there is a valid stream URL (HLS etc.) available.
  if (streamUrl && streamUrl.trim()) return true;
  if (!feedUrl) return false;
  return WORKING_IMAGE_DOMAINS.some(d => feedUrl.includes(d));
}

// Returns true if the feedUrl is a direct image (not a webpage/player link)
function isDirectImageUrl(url: string): boolean {
  if (!url) return false;
  const lower = url.toLowerCase();
  // 511ny.org/map/Cctv/... are webpages serving images via redirect
  // but the browser <img> tag can't always render them without CORS issues
  const knownWebpagePatterns = ['511ny.org/map', '511.org/map', '/map/Cctv'];
  return !knownWebpagePatterns.some(p => url.includes(p));
}

function shouldRetryWithProxy(url: string): boolean {
  // Caltrans cameras may need retry with proxy if direct load fails
  return url.includes('cwwp2.dot.ca.gov');
}

function getStreamColor(cam: CameraFeature): [number, number, number, number] {
  if (cam.properties.streamUrl) {
    return [0, 255, 136, 255]; // Neon Green for Live Video
  }
  return [0, 229, 255, 200]; // Cyan for Still Image
}
function formatLocation(cam: CameraFeature): string {
  const { city, country } = cam.properties;
  const countryName = COUNTRY_NAMES[country] || country;
  if (!city || city === 'British Columbia') return countryName;
  return `${city} · ${countryName}`;
}

function App() {
  const [cameras, setCameras] = useState<CameraFeature[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<CameraFeature | null>(null);
  const [hovered, setHovered] = useState<CameraFeature | null>(null);
  const [loading, setLoading] = useState(true);
  const [imgCacheBust, setImgCacheBust] = useState(Date.now());
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [imgLoaded, setImgLoaded] = useState(false);
  const [isHudMinimized, setIsHudMinimized] = useState(false);
  const [mouseCoords, setMouseCoords] = useState<[number, number] | null>(null);
  const [liveWindyUrl, setLiveWindyUrl] = useState<string | null>(null);
  const [hlsFailed, setHlsFailed] = useState(false);
  const [imgLastLoaded, setImgLastLoaded] = useState<Date | null>(null);
  const [lastImageHash, setLastImageHash] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetch('/cameras.geojson')
      .then(r => r.json())
      .then(data => { setCameras(data.features || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  // Auto-refresh every 15s
  useEffect(() => {
    refreshTimer.current = setInterval(() => {
      setImgCacheBust(Date.now());
      setLastRefresh(new Date());
      setImgLoaded(false);
    }, 15000);
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current); };
  }, []);

  useEffect(() => {
    setImgLoaded(false);
    setImgCacheBust(Date.now());
    setLiveWindyUrl(null);
    setHlsFailed(false); // reset on every camera change
    setImgLastLoaded(null); // reset image timestamp on camera change
    setLastImageHash(null); // reset hash on camera change

    if (selectedCamera?.properties.source === 'windy') {
      const camId = selectedCamera.properties.id.replace('windy_', '');
      const apiKey = import.meta.env.VITE_WINDY_API_KEY;
      
      if (apiKey) {
        fetch(`https://api.windy.com/webcams/api/v3/webcams/${camId}?include=images`, {
          headers: { 'x-windy-api-key': apiKey }
        })
          .then(r => r.json())
          .then(data => {
            const images = data.images || {};
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
          .then(data => {
            const images = data.images || {};
            const liveUrl = (images.current && images.current.preview) || (images.daylight && images.daylight.preview);
            if (liveUrl) setLiveWindyUrl(liveUrl);
          })
          .catch(e => console.error("Failed to fetch live windy token:", e));
      }
    }
  };

  const getCorsProxiedUrl = (url: string): string => {
    // For Caltrans, try direct first; if it fails, the error handler will retry with proxy
    return url;
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
    } catch (err) {
      // Fallback for non-CORS images: update timestamp on every successful load
      // because we can't inspect the pixels to know if it's the same.
      setImgLastLoaded(new Date());
    }
  };

  const layers = [
    new ScatterplotLayer<CameraFeature>({
      id: 'camera-points',
      data: cameras,
      getPosition: d => d.geometry.coordinates,
      getFillColor: d => getStreamColor(d),
      getRadius: 8000,
      radiusMinPixels: 3,
      radiusMaxPixels: 10,
      pickable: true,
      onClick: ({ object }) => object && setSelectedCamera(object),
      onHover: ({ object }) => setHovered(object ?? null),
      updateTriggers: { getFillColor: cameras }
    }),
    ...(selectedCamera ? [
      new ScatterplotLayer<CameraFeature>({
        id: 'camera-highlight',
        data: [selectedCamera],
        getPosition: d => d.geometry.coordinates,
        getFillColor: [255, 60, 60, 0], // transparent fill
        getLineColor: [255, 60, 60, 255], // red border
        lineWidthMinPixels: 2,
        getRadius: 15000,
        radiusMinPixels: 15,
        radiusMaxPixels: 25,
        stroked: true,
        filled: true,
        updateTriggers: { getPosition: selectedCamera }
      })
    ] : [])
  ];

  const counts = cameras.reduce((acc, c) => {
    if (c.properties.streamUrl) {
      acc.live += 1;
    } else {
      acc.still += 1;
    }
    return acc;
  }, { live: 0, still: 0 });

  const feedUrl   = selectedCamera?.properties.feedUrl ?? '';
  const streamUrl  = selectedCamera?.properties.streamUrl ?? '';
  const feedWorks  = selectedCamera ? isFeedWorking(feedUrl, streamUrl) : false;
  // hasStream is true only when a stream URL exists AND HLS hasn't already failed
  const hasStream  = (!hlsFailed && !!(streamUrl)) || (!isDirectImageUrl(feedUrl) && feedWorks && !hlsFailed);

  return (
    <div className="w-full h-screen relative bg-[#111419] overflow-hidden font-sans">
      {/* Map */}
      <DeckGL
        initialViewState={INITIAL_VIEW_STATE}
        controller={true}
        layers={layers}
        getCursor={({ isDragging }) => isDragging ? 'grabbing' : hovered ? 'crosshair' : 'grab'}
        onHover={(info) => {
          if (info.coordinate) setMouseCoords(info.coordinate as [number, number]);
          else setMouseCoords(null);
        }}
        onMouseLeave={() => setMouseCoords(null)}
      >
        <Map mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json" />
      </DeckGL>

      {/* Vignette */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.7) 100%)' }} />

      {/* ── MOUSE COORDINATES ── */}
      {mouseCoords && (
        <div className="absolute bottom-8 left-8 z-30 pointer-events-none">
          <div className="bg-[#05090C]/70 backdrop-blur-xl rounded-xl border border-white/10 px-6 py-4 flex items-center gap-4 shadow-2xl">
            <Scan className="w-5 h-5 text-[#00e5ff]" />
            <span className="text-[#00e5ff] text-base font-mono tracking-widest font-semibold">
              {mouseCoords[1].toFixed(4)}, {mouseCoords[0].toFixed(4)}
            </span>
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
                      {loading ? '—' : cameras.length.toLocaleString()}
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
                  <div className={`w-1.5 h-1.5 rounded-full ${hovered.properties.streamUrl ? 'bg-[#00ff88]' : 'bg-[#00e5ff]'}`} />
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
                  {hasStream && (
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
                {!hasStream && feedWorks && (
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
                  {!hasStream && (
                    <div className="absolute bottom-4 left-4 z-10 bg-black/80 backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2 border border-[#00e5ff]/30">
                      <div className="w-2 h-2 rounded-full bg-[#00e5ff] shadow-[0_0_8px_#00e5ff]" />
                      <span className="text-[#00e5ff] text-[10px] font-bold tracking-widest">STATIC</span>
                    </div>
                  )}
                  {hasStream ? (
                    <HlsPlayer url={streamUrl || feedUrl} cacheBust={imgCacheBust} onFallback={() => setHlsFailed(true)} />
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
                        crossOrigin="anonymous"
                        alt={selectedCamera.properties.name}
                        className="w-full h-full object-contain"
                        style={{ opacity: imgLoaded ? 1 : 0 }}
                        onLoad={handleImageLoad}
                        onError={(e) => {
                          const img = e.target as HTMLImageElement;
                          const url = img.src;
                          
                          // Retry with CORS proxy if applicable
                          if (shouldRetryWithProxy(url) && !url.includes('cors-anywhere')) {
                            const proxiedUrl = `https://cors-anywhere.herokuapp.com/${url.split('?')[0]}`;
                            img.src = proxiedUrl;
                            img.onload = () => {
                              setImgLoaded(true);
                              img.style.opacity = '1';
                            };
                            return;
                          }
                          
                          // If all retries fail, show error fallback
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
                        {lastRefresh.toLocaleTimeString()}
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
                      {imgLastLoaded.toLocaleTimeString()}
                    </span>
                  </div>
                )}
              </div>
            </div>
            
          </div>
        </Rnd>
      ) : null}
    </div>
  );
}

export default App;
