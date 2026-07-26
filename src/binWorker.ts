/**
 * Screen-pixel binning for the 2D map.
 *
 * Zoomed out, ~229k cameras land on only ~26k distinct device pixels — up to 1620
 * of them stack on a single pixel. Drawing all of them is pure waste: the GPU pays
 * per instance (measured 17ms/frame at world zoom on integrated graphics) to produce
 * an image the screen can't resolve.
 *
 * So we keep one representative camera per occupied pixel and record how many
 * collapsed into it. The density impression isn't lost — it's currently produced by
 * alpha accumulation of overlapping translucent dots, which App.tsx reproduces
 * exactly from `count` via 1-(1-a)^N. Isolated cameras (count 1) are always kept.
 *
 * Binning costs ~65-110ms for 229k points, which is why it lives in a worker and is
 * cached per integer zoom rather than run per frame. Panning never rebins (bins are
 * in world space); only a zoom-level or filter change does.
 */

export interface BinGroup {
  idx: number[];
  count: number[];
}

// The core payload stores coordinates as plain arrays; structured clone hands them
// over as-is, so accept anything indexable rather than assuming a typed array.
let lon: ArrayLike<number> | null = null;
let lat: ArrayLike<number> | null = null;

function bin(indices: number[], worldPx: number): BinGroup {
  const seen = new Map<number, number>(); // pixel key -> position in idx[]
  const idx: number[] = [];
  const count: number[] = [];
  if (!lon || !lat) return { idx, count };

  for (const i of indices) {
    const la = lat[i];
    // Web Mercator is undefined past ±85°; those points are off-map anyway.
    if (!(Math.abs(la) <= 85)) continue;
    const rad = (la * Math.PI) / 180;
    const x = ((lon[i] + 180) / 360) * worldPx | 0;
    const y = ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * worldPx | 0;
    const key = x * 1e6 + y;
    const at = seen.get(key);
    if (at === undefined) {
      seen.set(key, idx.length);
      idx.push(i);
      count.push(1);
    } else {
      count[at]++;
    }
  }
  return { idx, count };
}

type InitMsg = { type: 'init'; lon: ArrayLike<number>; lat: ArrayLike<number> };
type BinMsg = { type: 'bin'; reqId: number; zoom: number; dpr: number; still: number[]; live: number[] };

self.onmessage = (e: MessageEvent<InitMsg | BinMsg>) => {
  const msg = e.data;
  if (msg.type === 'init') {
    lon = msg.lon;
    lat = msg.lat;
    return;
  }
  const worldPx = 512 * Math.pow(2, msg.zoom) * msg.dpr;
  (self as unknown as Worker).postMessage({
    reqId: msg.reqId,
    still: bin(msg.still, worldPx),
    live: bin(msg.live, worldPx),
  });
};
