/**
 * Screen-pixel binning for the 2D map at low zoom.
 *
 * Zoomed out, ~229k cameras land on only ~26k distinct pixels (up to 1620 per
 * pixel), costing ~17ms/frame to draw an image the screen can't resolve. Keeps
 * one representative camera per occupied pixel plus a count, so App.tsx can
 * reproduce the same density via alpha accumulation (1-(1-a)^N).
 *
 * Runs in a worker and is cached per integer zoom (binning costs ~65-110ms) —
 * bins are in world space, so panning never triggers a rebin.
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
