import { useState, useEffect, useRef, useMemo } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer } from '@deck.gl/layers';
import MapGL, { Source, Layer } from 'react-map-gl/maplibre';
import type { MapMouseEvent } from 'maplibre-gl';
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
      // Detect CORS blocks or dead streams and switch to static image fallback
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (data.fatal) {
          console.log(`[Argus] Stream ${data.details} - falling back to static image.`);
          hls?.destroy();
          onFallback?.();
        }
      });
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      videoRef.current.src = url;
      videoRef.current.addEventListener('loadedmetadata', () => {
        videoRef.current?.play().catch(e => console.log('Autoplay prevented', e));
      });
      videoRef.current.addEventListener('error', () => onFallback?.());
    } else {
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

// Domains known to support CORS headers for image fingerprinting
const CORS_ENABLED_DOMAINS = [
  'imgproxy.windy.com',
  'amazonaws.com',
  'cloudfront.net',
  'images.data.gov.sg',
  'nzta.govt.nz'
];

function isFeedWorking(feedUrl: string, streamUrl?: string): boolean {
  // A feed works if EITHER the image URL is on a known-good domain,
  // OR there is a valid stream URL (HLS etc.) available.
  if (streamUrl && streamUrl.trim()) return true;
  if (!feedUrl) return false;
  return WORKING_IMAGE_DOMAINS.some(d => feedUrl.includes(d));
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
  const [viewState, setViewState] = useState(INITIAL_VIEW_STATE);
  const [mouseCoords, setMouseCoords] = useState<[number, number] | null>(null);
  const [liveWindyUrl, setLiveWindyUrl] = useState<string | null>(null);
  const [hlsFailed, setHlsFailed] = useState(false);
  const [imgLastLoaded, setImgLastLoaded] = useState<Date | null>(null);
  const [lastImageHash, setLastImageHash] = useState<string | null>(null);
  const [use24Hour, setUse24Hour] = useState(false);
  const [is3D, setIs3D] = useState(false);
  const [nodeOpacity, setNodeOpacity] = useState(0.8);
  const [showBorders, setShowBorders] = useState(false);
  const [filterCountries, setFilterCountries] = useState<string[]>([]);
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [filterSearch, setFilterSearch] = useState('');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: !use24Hour
    });
  };

  // Sanitize viewState on mode switch to prevent matrix crashes
  useEffect(() => {
    setViewState(prev => ({
      ...prev,
      latitude: Number.isFinite(prev.latitude) ? prev.latitude : 38,
      longitude: Number.isFinite(prev.longitude) ? prev.longitude : -95,
      zoom: Number.isFinite(prev.zoom) ? prev.zoom : 1.5,
      pitch: Number.isFinite(prev.pitch) ? prev.pitch : 0,
      bearing: Number.isFinite(prev.bearing) ? prev.bearing : 0,
    }));
  }, [is3D]);

  const openRandomCamera = () => {
    const pool = filteredCameras.length > 0 ? filteredCameras : cameras;
    if (pool.length === 0) return;
    
    const randomIndex = Math.floor(Math.random() * pool.length);
    const cam = pool[randomIndex];
    setSelectedCamera(cam);

    if (cam.geometry.coordinates[0] && cam.geometry.coordinates[1]) {
      setViewState(prev => ({
        ...prev,
        longitude: cam.geometry.coordinates[0],
        latitude: cam.geometry.coordinates[1],
        zoom: Math.max(prev.zoom, 10)
      }));
    }
  };

  useEffect(() => {
    fetch('/cameras.geojson')
      .then(r => r.json())
      .then(data => { setCameras(data.features || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const mapStyle = useMemo(() => "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json", []);

  const getCountryName = (props: any) => {
    const rawCountry = (props.country || '').toUpperCase();
    const source = (props.source || '').toLowerCase();
    const region = (props.region || '').toUpperCase();

    // Consolidated US Sources
    const isUS = rawCountry === 'US' || 
                 rawCountry === 'USA' || 
                 source.includes('caltrans') || 
                 source.includes('road511') || 
                 source.includes('nyc_dot') || 
                 source.includes('iowa_dot');

    if (isUS) return 'United States';
    
    const key = (props.country || props.region || 'unknown').toUpperCase();
    
    // Check manual overrides first
    if (MANUAL_OVERRIDES[key]) return MANUAL_OVERRIDES[key];
    
    // Use dynamic ISO lookup
    const resolved = countries.getName(key, 'en');
    if (resolved) return resolved;

    return key.length <= 3 ? key : 'Global Sector';
  };

  const countryStats = useMemo(() => {
    const stats: Record<string, number> = {};
    cameras.forEach(c => {
      const name = getCountryName(c.properties);
      stats[name] = (stats[name] || 0) + 1;
    });
    return Object.entries(stats).sort((a, b) => b[1] - a[1]);
  }, [cameras]);

  const filteredCameras = useMemo(() => {
    if (filterCountries.length === 0) return cameras;
    return cameras.filter(c => filterCountries.includes(getCountryName(c.properties)));
  }, [cameras, filterCountries]);

  const cameraMap = useMemo(() => {
    const m = new window.Map<string, CameraFeature>();
    filteredCameras.forEach(c => m.set(c.properties.id, c));
    return m;
  }, [filteredCameras]);

  const camerasGeoJson = useMemo(() => ({
    type: 'FeatureCollection',
    features: filteredCameras
  }), [filteredCameras]);

  const hoveredGeoJson = useMemo(() => ({
    type: 'FeatureCollection',
    features: hovered ? [hovered] : []
  }), [hovered]);

  const selectedGeoJson = useMemo(() => ({
    type: 'FeatureCollection',
    features: selectedCamera ? [selectedCamera] : []
  }), [selectedCamera]);

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


  const onMapClick = (e: MapMouseEvent) => {
    const feature = e.features?.[0];
    if (feature) {
      const camId = feature.properties?.id;
      const original = cameraMap.get(camId);
      if (original) {
        setSelectedCamera(original);
        setHlsFailed(false);
      }
    }
  };

  const onMapMouseMove = (e: MapMouseEvent) => {
    const feature = e.features?.[0];
    if (feature) {
      const camId = feature.properties?.id;
      const original = cameraMap.get(camId);
      setHovered(original || null);
      if (Number.isFinite(e.lngLat.lng) && Number.isFinite(e.lngLat.lat)) {
        setMouseCoords([e.lngLat.lng, e.lngLat.lat]);
      }
      e.target.getCanvas().style.cursor = 'crosshair';
    } else {
      setHovered(null);
      if (e.lngLat && Number.isFinite(e.lngLat.lng) && Number.isFinite(e.lngLat.lat)) {
        setMouseCoords([e.lngLat.lng, e.lngLat.lat]);
      }
      e.target.getCanvas().style.cursor = '';
    }
  };

  const deckLayers = [
    new ScatterplotLayer<CameraFeature>({
      id: 'deck-points',
      data: filteredCameras,
      getPosition: d => d.geometry.coordinates,
      getFillColor: d => {
        const base = d.properties.streamUrl ? [0, 255, 136] : [0, 229, 255];
        return [...base, nodeOpacity * 255];
      },
      getRadius: d => (hovered?.properties.id === d.properties.id ? 6000 : 3000),
      radiusMinPixels: 0.4,
      radiusMaxPixels: 4,
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 255],
      transitions: {
        getRadius: 150
      },
      updateTriggers: {
        getFillColor: [nodeOpacity],
        getRadius: [hovered?.properties.id]
      }
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

  const counts = filteredCameras.reduce((acc, c) => {
    if (c.properties.streamUrl) {
      acc.live += 1;
    } else {
      acc.still += 1;
    }
    return acc;
  }, { live: 0, still: 0 });

  const feedUrl = selectedCamera?.properties.feedUrl ?? '';
  const streamUrl = selectedCamera?.properties.streamUrl ?? '';
  const feedWorks = selectedCamera ? isFeedWorking(feedUrl, streamUrl) : false;
  // hasStream is true only when a stream URL exists AND it hasn't failed CORS/Network checks
  const hasStream = !!(streamUrl) && !hlsFailed;

  return (
    <div className="w-full h-screen relative bg-[#111419] overflow-hidden font-sans">
      <style>{scrollbarStyles}</style>
      {/* Map */}
      {is3D ? (
        <MapGL
          key="globe-map"
          longitude={viewState.longitude}
          latitude={viewState.latitude}
          zoom={viewState.zoom}
          pitch={viewState.pitch}
          bearing={viewState.bearing}
          onMove={e => {
            if (e.viewState && Number.isFinite(e.viewState.latitude) && Number.isFinite(e.viewState.longitude)) {
              setViewState(e.viewState);
            }
          }}
          mapStyle={mapStyle}
          projection={{ type: 'globe' }}
          onClick={onMapClick}
          onMouseMove={onMapMouseMove}
          onMouseLeave={() => {
            setHovered(null);
            setMouseCoords(null);
          }}
          interactiveLayerIds={['camera-points']}
        >
          <Source id="cameras" type="geojson" data={camerasGeoJson}>
            <Layer
              id="camera-points"
              type="circle"
              paint={{
                'circle-radius': [
                  'interpolate', ['linear'], ['zoom'],
                  2, 1.2,
                  6, 2.5,
                  10, 4.5,
                  14, 6
                ],
                'circle-color': [
                  'case',
                  ['all', ['has', 'streamUrl'], ['!=', ['get', 'streamUrl'], '']],
                  '#00ff88',
                  '#00e5ff'
                ],
                'circle-opacity': nodeOpacity,
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
          viewState={viewState}
          onViewStateChange={e => {
            if (e.viewState && Number.isFinite(e.viewState.latitude) && Number.isFinite(e.viewState.longitude)) {
              setViewState(e.viewState);
            }
          }}
          controller={true}
          layers={deckLayers}
          onHover={({ object, coordinate }) => {
            setHovered(object || null);
            if (coordinate) setMouseCoords(coordinate as [number, number]);
          }}
          onClick={({ object }) => {
            if (object) {
              setSelectedCamera(object);
              setHlsFailed(false);
            }
          }}
          getCursor={({ isDragging }) => isDragging ? 'grabbing' : hovered ? 'crosshair' : 'grab'}
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
      {mouseCoords && (
        <div className="absolute bottom-8 left-8 z-30 pointer-events-none">
          <div className="bg-[#05090C]/70 backdrop-blur-xl rounded-xl border border-white/10 px-6 py-4 flex flex-col gap-2 shadow-2xl">
            <div className="flex items-center gap-4">
              <Scan className="w-5 h-5 text-[#00e5ff]" />
              <span className="text-[#00e5ff] text-base font-mono tracking-widest font-semibold">
                {mouseCoords[1].toFixed(4)}, {mouseCoords[0].toFixed(4)}
              </span>
            </div>
            <div className="flex items-center gap-4 border-t border-white/5 pt-2">
              <Activity className="w-4 h-4 text-gray-500" />
              <span className="text-gray-500 text-xs font-mono uppercase tracking-[0.2em]">
                Zoom: <span className="text-white">{(viewState.zoom * 10).toFixed(1)}%</span>
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
                        crossOrigin={CORS_ENABLED_DOMAINS.some(d => selectedCamera.properties.feedUrl.includes(d)) ? "anonymous" : undefined}
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
              className="absolute bottom-0 right-20 bg-[#05090C]/90 backdrop-blur-2xl rounded-3xl border border-white/10 p-6 shadow-2xl min-w-[320px] flex flex-col z-50"
            >
              <div className="flex items-center justify-between mb-8 flex-shrink-0">
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

              <div className="mt-6 pt-6 border-t border-white/5">
                <p className="text-[10px] text-gray-600 font-mono text-center uppercase tracking-widest">Argus v1.4.2 · Secure</p>
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
    </div>
  );
}

export default App;
