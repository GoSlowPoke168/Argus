import { useState, useEffect, useRef } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer } from '@deck.gl/layers';
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Scan, Eye, Activity, X, MapPin, RefreshCw, Clock, Video } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Rnd } from 'react-rnd';
import Hls from 'hls.js';

function HlsPlayer({ url }: { url: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!videoRef.current) return;
    
    // If it's a direct MP4 video, use native video src
    if (url.toLowerCase().endsWith('.mp4')) {
      videoRef.current.src = url;
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
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      videoRef.current.src = url;
      videoRef.current.addEventListener('loadedmetadata', () => {
        videoRef.current?.play().catch(e => console.log('Autoplay prevented', e));
      });
    }

    return () => {
      if (hls) hls.destroy();
    };
  }, [url]);

  return (
    <video
      ref={videoRef}
      className="w-full h-full object-contain bg-black"
      controls
      muted
      autoPlay
      playsInline
    />
  );
}

const INITIAL_VIEW_STATE = {
  longitude: 10,
  latitude: 25,
  zoom: 2,
  pitch: 30,
  bearing: 0
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
  'drivebc.ca',
  'cwwp2.dot.ca.gov',    // Caltrans California static JPEGs
  'images.data.gov.sg',  // Singapore LTA
  'imgproxy.windy.com',  // Windy Webcams
  'tripcheck.com',       // Oregon TripCheck
  '511ia.org',           // Iowa 511
  'nzta.govt.nz',        // New Zealand NZTA
  'tfl.gov.uk',          // London TfL
  'amazonaws.com',       // TfL / NZTA S3 buckets
  'cloudfront.net',      // Iowa 511 CDN
  'webcams.nyctmc.org',  // NYC DOT
];

function isFeedWorking(url: string): boolean {
  if (!url) return false;
  return WORKING_IMAGE_DOMAINS.some(d => url.includes(d));
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
  }, [selectedCamera?.properties.id]);

  const manualRefresh = () => {
    setImgCacheBust(Date.now());
    setLastRefresh(new Date());
    setImgLoaded(false);
  };

  const getLiveUrl = (url: string) => {
    if (!url) return '';
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}_t=${imgCacheBust}`;
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
    })
  ];

  const counts = cameras.reduce((acc, c) => {
    if (c.properties.streamUrl) {
      acc.live += 1;
    } else {
      acc.still += 1;
    }
    return acc;
  }, { live: 0, still: 0 });

  const feedWorks = selectedCamera ? isFeedWorking(selectedCamera.properties.feedUrl) : false;
  const hasStream = !!(selectedCamera?.properties.streamUrl);

  return (
    <div className="w-full h-screen relative bg-[#0a0a0f] overflow-hidden font-sans">
      {/* Map */}
      <DeckGL
        initialViewState={INITIAL_VIEW_STATE}
        controller={true}
        layers={layers}
        getCursor={({ isDragging }) => isDragging ? 'grabbing' : hovered ? 'crosshair' : 'grab'}
      >
        <Map mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json" />
      </DeckGL>

      {/* Vignette */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.7) 100%)' }} />

      {/* ── ARGUS HUD — TOP RIGHT ── */}
      <div style={{ position: 'absolute', top: 32, right: 32, zIndex: 30, width: 300 }} className="pointer-events-auto">
        <div className="bg-[#0a0f14]/85 backdrop-blur-3xl rounded-2xl border border-white/5 p-6 shadow-2xl">
          {/* Logo */}
          <div className="flex flex-col mb-6 pb-4 border-b border-white/5">
            <h1 className="text-3xl font-extrabold tracking-tight text-white leading-none">Argus</h1>
            <p className="text-xs text-gray-400 font-medium tracking-wide mt-2">Global Surveillance</p>
          </div>

          {/* Stats */}
          <div className="flex gap-3 mb-6">
            <div className="flex-1 bg-white/5 rounded-xl p-4 text-center">
              <p className="text-white font-mono text-xl font-semibold">
                {loading ? '—' : cameras.length.toLocaleString()}
              </p>
              <p className="text-gray-500 text-[10px] uppercase tracking-wider font-semibold mt-1">Nodes</p>
            </div>
            <div className="flex-1 bg-white/5 rounded-xl p-4 text-center">
              <p className="text-white font-mono text-lg font-semibold flex items-center justify-center gap-1.5">
                <Activity className="w-4 h-4 text-[#00ff88]" /> Live
              </p>
              <p className="text-gray-500 text-[10px] uppercase tracking-wider font-semibold mt-1">Status</p>
            </div>
          </div>

          {/* Legend */}
          <div className="flex flex-col gap-3">
            {[
              { type: 'live',  color: '#00ff88', label: 'Live Video Feed' },
              { type: 'still', color: '#00e5ff', label: 'Static Snapshot' },
            ].map(({ type, color, label }) => (
              <div key={label} className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color, boxShadow: `0 0 10px ${color}` }} />
                  <span className="text-xs font-medium text-gray-300">
                    {label}
                  </span>
                </div>
                <span className="text-xs font-mono font-medium text-gray-400">
                  {type === 'live' ? counts.live.toLocaleString() : counts.still.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Hover Tooltip ── */}
      {hovered && (
        <div className="absolute bottom-12 left-1/2 -translate-x-1/2 z-50 pointer-events-none">
          <div className="bg-[#0a0f14]/90 backdrop-blur-2xl rounded-full border border-white/10 px-6 py-2.5 shadow-xl max-w-lg flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-[#00e5ff]" />
            <p className="text-white text-sm font-semibold tracking-tight truncate">{hovered.properties.name}</p>
            <div className="w-px h-3 bg-white/20 mx-1" />
            <p className="text-gray-400 text-xs font-mono">{formatLocation(hovered)}</p>
          </div>
        </div>
      )}

      {/* ── Camera Feed Panel — DRAGGABLE RND ── */}
      {selectedCamera ? (
        <Rnd
          default={{ x: 32, y: 32, width: 360, height: 500 }}
          minWidth={280}
          minHeight={350}
          bounds="window"
          dragHandleClassName="drag-handle"
          className="z-40"
        >
          <div className="bg-[#0a0f14]/90 backdrop-blur-3xl rounded-2xl border border-white/10 p-6 flex flex-col h-full shadow-2xl w-full">
            {/* Elegant Header (Drag Handle) */}
            <div className="drag-handle cursor-move flex justify-between items-start mb-4 pb-4 border-b border-white/5">
              <div className="flex-1 min-w-0 pr-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] font-bold tracking-widest uppercase text-gray-400">
                    {selectedCamera.properties.source ?? selectedCamera.properties.type}
                  </span>
                  {hasStream && (
                    <span className="text-[#00ff88] text-[9px] font-bold tracking-widest bg-[#00ff88]/10 px-1.5 py-0.5 rounded flex items-center">
                      <Video className="w-3 h-3 mr-1" /> LIVE
                    </span>
                  )}
                </div>
                <h2 className="text-white text-lg font-semibold tracking-tight leading-snug truncate">
                  {selectedCamera.properties.name}
                </h2>
                <div className="flex items-center gap-1.5 mt-1.5">
                  <MapPin className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
                  <p className="text-gray-400 text-xs truncate">{formatLocation(selectedCamera)}</p>
                </div>
              </div>
              <button onClick={() => setSelectedCamera(null)}
                className="text-[#00e5ff]/40 hover:text-[#00e5ff] transition-colors p-2 rounded-full flex-shrink-0">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Feed area */}
            <div className="flex-1 relative bg-black/50 rounded-xl overflow-hidden border border-white/5 min-h-0">
              {feedWorks ? (
                <>
                  {hasStream && (
                    <div className="absolute bottom-3 left-3 z-10 bg-black/60 backdrop-blur-md rounded-md px-3 py-1.5 flex items-center gap-2 border border-[#00ff88]/20 shadow-lg">
                      <div className="w-2 h-2 rounded-full bg-[#00ff88] animate-pulse" />
                      <span className="text-[#00ff88] text-[10px] font-bold tracking-widest">LIVE STREAM</span>
                    </div>
                  )}
                  {!hasStream && (
                    <div className="absolute bottom-3 left-3 z-10 bg-black/60 backdrop-blur-md rounded-md px-3 py-1.5 flex items-center gap-2 border border-[#00e5ff]/20 shadow-lg">
                      <div className="w-2 h-2 rounded-full bg-[#00e5ff]" />
                      <span className="text-[#00e5ff] text-[10px] font-bold tracking-widest">STATIC IMAGE</span>
                    </div>
                  )}
                  {hasStream ? (
                    <HlsPlayer url={selectedCamera.properties.streamUrl!} />
                  ) : (
                    <>
                      <button onClick={manualRefresh}
                        className="absolute top-3 right-3 z-10 bg-transparent p-2 text-[#00e5ff]/50 hover:text-[#00e5ff] hover:bg-[#00e5ff]/10 rounded-full transition-all group shadow-none border-none">
                        <RefreshCw className="w-5 h-5 group-hover:rotate-180 transition-transform duration-500" />
                      </button>
                      {!imgLoaded && (
                        <div className="absolute inset-0 flex items-center justify-center">
                          <div className="w-6 h-6 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                        </div>
                      )}
                      <img
                        key={`${selectedCamera.properties.id}-${imgCacheBust}`}
                        src={getLiveUrl(selectedCamera.properties.feedUrl)}
                        alt={selectedCamera.properties.name}
                        className="w-full h-full object-contain"
                        style={{ opacity: imgLoaded ? 1 : 0 }}
                        onLoad={() => setImgLoaded(true)}
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
                  <div className="hidden absolute inset-0 flex-col items-center justify-center text-center p-4">
                    <Eye className="w-8 h-8 text-gray-600 mb-2" />
                    <p className="text-gray-500 text-sm font-medium">Camera offline</p>
                  </div>
                </>
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center text-center p-6">
                  <Eye className="w-8 h-8 text-gray-700 mb-3" />
                  <p className="text-gray-300 text-sm font-medium mb-1">Feed Unavailable</p>
                  <p className="text-gray-500 text-xs leading-relaxed">
                    This host blocks external embedding.
                    Try another node.
                  </p>
                </div>
              )}
            </div>

            {/* Footer meta */}
            <div className="mt-6 pt-6 border-t border-white/5 space-y-3">
              {(selectedCamera.properties.route || selectedCamera.properties.highway) && (
                <div className="flex justify-between items-center">
                  <span className="text-gray-500 text-xs">Route</span>
                  <span className="text-gray-300 text-xs font-medium">
                    {selectedCamera.properties.route || `HWY ${selectedCamera.properties.highway}`}
                  </span>
                </div>
              )}
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Coordinates</span>
                <span className="text-gray-400 text-xs font-mono">
                  {selectedCamera.geometry.coordinates[1].toFixed(4)}, {selectedCamera.geometry.coordinates[0].toFixed(4)}
                </span>
              </div>
              {feedWorks && (
                <div className="flex justify-between items-center">
                  <span className="text-gray-500 text-xs flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5" /> Refreshed
                  </span>
                  <span className="text-gray-400 text-xs font-mono">{lastRefresh.toLocaleTimeString()}</span>
                </div>
              )}
            </div>
          </div>
        </Rnd>
      ) : null}
    </div>
  );
}

export default App;
