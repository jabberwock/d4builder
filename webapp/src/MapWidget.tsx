import { createResource, createSignal, For, Show, createMemo, onMount, onCleanup } from 'solid-js';
import type { Component } from 'solid-js';
import type { Gear } from './types';
import './MapWidget.css';

/* ── Types ──────────────────────────────────────────────────────────────── */

interface AspectEntry {
  name: string;
  class: string;
  category: string;
  source: 'dungeon' | 'world_drop';
  dungeon?: string;
  region?: string;
  x?: number;
  y?: number;
}

interface MapData {
  aspects: Record<string, AspectEntry>;
}

/* ── Stronghold gates ───────────────────────────────────────────────────── */

const STRONGHOLD_GATES: Record<string, string> = {
  'Kor Dragan Barracks': 'Kor Dragan',
  'Forbidden City':      'Malnok',
  'Immortal Emanation':  'Nostrava',
};

/* ── Coordinate transform (from d4planner eB() function) ───────────────── */
// Game coords → MercatorCoordinate [0,1] range, centered near (0.5, 0.5)

function gameToMercator(gx: number, gy: number): [number, number] {
  // Rotate by -45°
  const cos = Math.SQRT1_2; // cos(45°)
  const sin = -Math.SQRT1_2; // sin(-45°)
  const rx = gx * cos - gy * sin;
  const ry = gx * sin + gy * cos;

  // Shift
  const o = rx + 3755;
  const n = ry - 135;

  // Scale to Mercator [0,1]
  const scale = 4.8828125e-4; // 1/2048
  const mx = (1 - (o - 1356) / 4795 - 0.5) * scale + 0.5;
  const my = ((n + 2724) / 4795 - 0.5) * scale + 0.5;
  return [mx, my];
}

// Mercator [0,1] to longitude/latitude
function mercatorToLngLat(mx: number, my: number): [number, number] {
  const lng = mx * 360 - 180;
  const lat = (2 * Math.atan(Math.exp((1 - 2 * my) * Math.PI)) - Math.PI / 2) * (180 / Math.PI);
  return [lng, lat];
}

export function gameToLngLat(gx: number, gy: number): [number, number] {
  const [mx, my] = gameToMercator(gx, gy);
  return mercatorToLngLat(mx, my);
}

/* ── Data fetching ──────────────────────────────────────────────────────── */

async function fetchMapData(): Promise<MapData> {
  const res = await fetch('/data/map_data.json');
  if (!res.ok) throw new Error('Failed to load map data');
  return res.json() as Promise<MapData>;
}

/* ── Aspect extraction ──────────────────────────────────────────────────── */

function extractAspects(gear: Gear): string[] {
  return Object.values(gear)
    .map(slot => slot?.aspect)
    .filter((a): a is string => typeof a === 'string' && a.trim().length > 0);
}

/* ── Resolved marker ────────────────────────────────────────────────────── */

interface Marker {
  aspectName: string;
  dungeon: string;
  region: string;
  gx: number;
  gy: number;
  gatedBy?: string;
}

/* ── SVG zone definitions (game coordinates) ───────────────────────────── */

const ZONES = [
  { name: 'Scosglen',        abbr: 'SCOS', color: '#1a2e1a', stroke: '#2a4a2a', x: -2440, y: -1380, w: 1160, h: 1570 },
  { name: 'Fractured Peaks', abbr: 'FRAC', color: '#16203a', stroke: '#26304a', x: -2100, y: -430,  w: 1280, h: 1180 },
  { name: 'Dry Steppes',     abbr: 'STEP', color: '#2e1a06', stroke: '#4a2a0a', x: -1310, y: -1180, w: 1240, h: 1200 },
  { name: 'Kehjistan',       abbr: 'KEHJ', color: '#2a0e0e', stroke: '#4a1a1a', x: -820,  y: -960,  w: 1580, h: 1160 },
  { name: 'Hawezar',         abbr: 'HAWE', color: '#082a1a', stroke: '#104422', x: -1360, y:  100,  w: 1380, h: 1220 },
  { name: 'Nahantu',         abbr: 'NAHA', color: '#1a0a2e', stroke: '#2a1244', x: -120,  y: -420,  w: 1730, h: 1260 },
] as const;

const VB_X = -2500, VB_Y = -1450, VB_W = 4300, VB_H = 3000;

/* ── Props ──────────────────────────────────────────────────────────────── */

interface MapWidgetProps {
  gear: Gear;
}

/* ── MapLibre implementation ────────────────────────────────────────────── */

interface MapLibreModule {
  Map: new (opts: object) => MapLibreInstance;
  Marker: new (opts?: object) => MarkerInstance;
  Popup: new (opts?: object) => PopupInstance;
  NavigationControl: new (opts?: object) => object;
}

interface MapLibreInstance {
  on(event: string, cb: () => void): void;
  addLayer(layer: object): void;
  addSource(id: string, source: object): void;
  fitBounds(bounds: [[number,number],[number,number]], opts: object): void;
  addControl(ctrl: object, pos?: string): void;
  remove(): void;
}

interface MarkerInstance {
  setLngLat(coords: [number, number]): MarkerInstance;
  setPopup(popup: PopupInstance): MarkerInstance;
  addTo(map: MapLibreInstance): MarkerInstance;
  getElement(): HTMLElement;
}

interface PopupInstance {
  setHTML(html: string): PopupInstance;
}

/* ── Component ──────────────────────────────────────────────────────────── */

const MapWidget: Component<MapWidgetProps> = (props) => {
  const [mapData] = createResource(fetchMapData);
  const [hovered, setHovered] = createSignal<Marker | null>(null);
  const [svgRef, setSvgRef] = createSignal<SVGSVGElement | null>(null);
  const [tipPos, setTipPos] = createSignal({ x: 0, y: 0 });
  const [mapMode, setMapMode] = createSignal<'loading' | 'gl' | 'svg'>('loading');
  let mapContainer: HTMLDivElement | undefined;
  let glMap: MapLibreInstance | null = null;

  const markers = createMemo<Marker[]>(() => {
    const data = mapData();
    if (!data) return [];

    const result: Marker[] = [];
    const seen = new Set<string>();

    for (const aspectName of extractAspects(props.gear)) {
      const entry = data.aspects[aspectName];
      if (!entry || entry.source !== 'dungeon') continue;
      if (!entry.dungeon || entry.x == null || entry.y == null) continue;
      if (seen.has(entry.dungeon)) continue;
      seen.add(entry.dungeon);

      result.push({
        aspectName,
        dungeon: entry.dungeon,
        region: entry.region ?? 'Unknown',
        gx: entry.x,
        gy: entry.y,
        gatedBy: STRONGHOLD_GATES[entry.dungeon],
      });
    }
    return result;
  });

  const worldDropAspects = createMemo(() => {
    const data = mapData();
    if (!data) return [];
    return extractAspects(props.gear).filter(a => {
      const entry = data.aspects[a];
      return entry?.source === 'world_drop' || !entry;
    });
  });

  // Try to initialize MapLibre once map data is ready
  onMount(async () => {
    // Wait for mapData to be available (it may load after mount)
    let waited = 0;
    while (!mapData() && waited < 5000) {
      await new Promise(r => setTimeout(r, 100));
      waited += 100;
    }
    if (!mapContainer) { setMapMode('svg'); return; }

    try {
      const ml = await import('maplibre-gl') as unknown as MapLibreModule;

      // Use d4planner's default center if no markers, otherwise center on average marker position
      const ms = markers();
      let centerLng = 0.003, centerLat = 0.01; // d4planner defaults
      if (ms.length > 0) {
        const avgGx = ms.reduce((s, m) => s + m.gx, 0) / ms.length;
        const avgGy = ms.reduce((s, m) => s + m.gy, 0) / ms.length;
        [centerLng, centerLat] = gameToLngLat(avgGx, avgGy);
      }

      glMap = new ml.Map({
        container: mapContainer,
        style: {
          version: 8,
          sources: {
            'd4map': {
              type: 'raster',
              tiles: ['/media/map-tiles/{z}/{x}/{y}.webp'],
              tileSize: 1024,
              minzoom: 10,
              maxzoom: 14,
            },
          },
          layers: [
            { id: 'background', type: 'background', paint: { 'background-color': '#030308' } },
            { id: 'd4map-layer', type: 'raster', source: 'd4map', paint: { 'raster-opacity': 1 } },
          ],
        },
        center: [centerLng, centerLat],
        zoom: 12,
        minZoom: 10,
        maxZoom: 14,
        renderWorldCopies: false,
        attributionControl: false,
      });

      // Wait for map load
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('timeout')), 8000);
        glMap!.on('load', () => { clearTimeout(timeout); resolve(); });
        glMap!.on('error', () => { clearTimeout(timeout); reject(new Error('map load error')); });
      });

      // Add zoom/pan controls
      glMap!.addControl(new ml.NavigationControl({ showCompass: false }), 'bottom-right');

      // Add markers and collect bounds
      const lngLats: [number, number][] = [];
      for (const m of markers()) {
        const [lng, lat] = gameToLngLat(m.gx, m.gy);
        lngLats.push([lng, lat]);

        const el = document.createElement('div');
        el.className = `mw-gl-marker${m.gatedBy ? ' mw-gl-marker-gated' : ''}`;

        const popup = new ml.Popup({ offset: 20, closeButton: false, maxWidth: '260px' }).setHTML(`
          <div class="mw-tip-aspect">${m.aspectName}</div>
          <div class="mw-tip-dungeon">${m.dungeon}</div>
          <div class="mw-tip-region">${m.region}</div>
          ${m.gatedBy ? `<div class="mw-tip-gate">🔒 Clear ${m.gatedBy} stronghold first</div>` : ''}
        `);

        new ml.Marker({ element: el })
          .setLngLat([lng, lat])
          .setPopup(popup)
          .addTo(glMap!);
      }

      // Fit camera to show all markers
      if (lngLats.length > 0) {
        const minLng = Math.min(...lngLats.map(c => c[0]));
        const maxLng = Math.max(...lngLats.map(c => c[0]));
        const minLat = Math.min(...lngLats.map(c => c[1]));
        const maxLat = Math.max(...lngLats.map(c => c[1]));

        glMap!.fitBounds([[minLng, minLat], [maxLng, maxLat]], {
            padding: 60,
            maxZoom: 13,
            duration: 0,
          });
      }

      setMapMode('gl');
    } catch {
      setMapMode('svg');
    }
  });

  onCleanup(() => {
    if (glMap) { glMap.remove(); glMap = null; }
  });

  function onSvgMouseMove(e: MouseEvent) {
    const svg = svgRef();
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    setTipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  }

  return (
    <div class="mw-wrap">
      {/* Header */}
      <div class="mw-header">
        <span class="mw-title">Aspect Locations</span>
        <div class="mw-legend">
          <span class="mw-legend-item"><span class="mw-dot mw-dot-free" />Dungeon</span>
          <span class="mw-legend-item"><span class="mw-dot mw-dot-gated" />Stronghold Required</span>
          <span class="mw-legend-item"><span class="mw-dot mw-dot-drop" />World Drop</span>
        </div>
      </div>

      <Show when={mapData.loading}>
        <div class="mw-loading">Loading map…</div>
      </Show>

      <Show when={mapData()}>
        <div class="mw-body">
          {/* Map area */}
          <div class="mw-map-col">
            {/* MapLibre GL container — always in DOM so MapLibre can measure it.
                Hidden visually until tiles confirm loaded. */}
            <div
              ref={mapContainer}
              class="mw-gl-map"
              style={{ visibility: mapMode() === 'gl' ? 'visible' : 'hidden', position: 'absolute', inset: '0' }}
            />

            {/* SVG fallback — shown while loading or when GL fails */}
            <Show when={mapMode() !== 'gl'}>
              <svg
                ref={setSvgRef}
                class="mw-svg"
                viewBox={`${VB_X} ${VB_Y} ${VB_W} ${VB_H}`}
                preserveAspectRatio="xMidYMid meet"
                onMouseMove={onSvgMouseMove}
                onMouseLeave={() => setHovered(null)}
              >
                <defs>
                  {/* Crosshatch terrain pattern */}
                  <pattern id="hatch-scos" patternUnits="userSpaceOnUse" width="40" height="40" patternTransform="rotate(45)">
                    <line x1="0" y1="0" x2="0" y2="40" stroke="#2a4a2a" stroke-width="1.5" />
                  </pattern>
                  <pattern id="hatch-step" patternUnits="userSpaceOnUse" width="30" height="30" patternTransform="rotate(0)">
                    <line x1="0" y1="15" x2="30" y2="15" stroke="#4a2a0a" stroke-width="1" />
                    <line x1="15" y1="0" x2="15" y2="30" stroke="#4a2a0a" stroke-width="0.5" />
                  </pattern>
                  <pattern id="hatch-frac" patternUnits="userSpaceOnUse" width="20" height="20" patternTransform="rotate(30)">
                    <line x1="0" y1="10" x2="20" y2="10" stroke="#26304a" stroke-width="1.5" />
                  </pattern>
                  <pattern id="dots-hawe" patternUnits="userSpaceOnUse" width="24" height="24">
                    <circle cx="12" cy="12" r="2" fill="#104422" />
                  </pattern>
                  <filter id="glow-gold">
                    <feGaussianBlur in="SourceGraphic" stdDeviation="20" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                  <filter id="glow-red">
                    <feGaussianBlur in="SourceGraphic" stdDeviation="20" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                </defs>

                {/* Sea/void background */}
                <rect x={VB_X} y={VB_Y} width={VB_W} height={VB_H} fill="#010108" />

                {/* Zone fills */}
                <For each={ZONES}>
                  {zone => (
                    <g>
                      <rect x={zone.x} y={zone.y} width={zone.w} height={zone.h} fill={zone.color} />
                      <rect
                        x={zone.x} y={zone.y} width={zone.w} height={zone.h}
                        fill={`url(#hatch-${zone.abbr.toLowerCase()})`}
                        opacity="0.5"
                      />
                      <rect
                        x={zone.x} y={zone.y} width={zone.w} height={zone.h}
                        fill="none"
                        stroke={zone.stroke}
                        stroke-width="6"
                      />
                      <text
                        x={zone.x + zone.w / 2}
                        y={zone.y + zone.h / 2}
                        text-anchor="middle"
                        dominant-baseline="middle"
                        fill="#ffffff10"
                        font-size="100"
                        font-weight="900"
                        letter-spacing="8"
                        style="pointer-events: none; text-transform: uppercase;"
                      >
                        {zone.name.toUpperCase()}
                      </text>
                    </g>
                  )}
                </For>

                {/* Dungeon markers */}
                <For each={markers()}>
                  {(m) => {
                    const isHov = () => hovered()?.dungeon === m.dungeon;
                    return (
                      <g
                        onMouseEnter={() => setHovered(m)}
                        onMouseLeave={() => setHovered(null)}
                        style="cursor: pointer;"
                      >
                        <Show when={isHov()}>
                          <circle
                            cx={m.gx} cy={m.gy} r="80"
                            fill={m.gatedBy ? 'rgba(220,80,40,0.12)' : 'rgba(218,165,32,0.12)'}
                            stroke={m.gatedBy ? '#dc5028' : '#daa520'}
                            stroke-width="3"
                            stroke-dasharray="12 6"
                          />
                        </Show>
                        {/* Shadow */}
                        <circle cx={m.gx + 5} cy={m.gy + 5} r="26" fill="rgba(0,0,0,0.5)" />
                        {/* Main circle */}
                        <circle
                          cx={m.gx} cy={m.gy} r="26"
                          fill={m.gatedBy ? '#5a1008' : '#3a2400'}
                          stroke={m.gatedBy ? '#e04020' : '#daa520'}
                          stroke-width="5"
                          filter={isHov() ? `url(#glow-${m.gatedBy ? 'red' : 'gold'})` : undefined}
                        />
                        {/* Icon using image element */}
                        <image
                          href={m.gatedBy ? '/media/icons/Stronghold.webp' : '/media/icons/Dungeon.webp'}
                          x={m.gx - 18} y={m.gy - 18}
                          width="36" height="36"
                          style="pointer-events: none;"
                        />
                      </g>
                    );
                  }}
                </For>
              </svg>

              {/* SVG tooltip */}
              <Show when={hovered()}>
                {(m) => (
                  <div
                    class="mw-tip"
                    style={{
                      left: `${Math.min(tipPos().x + 14, 380)}px`,
                      top: `${Math.max(tipPos().y - 70, 4)}px`,
                    }}
                  >
                    <div class="mw-tip-aspect">{m().aspectName}</div>
                    <div class="mw-tip-dungeon">{m().dungeon}</div>
                    <div class="mw-tip-region">{m().region}</div>
                    <Show when={m().gatedBy}>
                      <div class="mw-tip-gate">🔒 Clear {m().gatedBy} stronghold first</div>
                    </Show>
                  </div>
                )}
              </Show>
            </Show>
          </div>

          {/* Aspect list */}
          <div class="mw-side">
            <Show when={markers().length > 0}>
              <div class="mw-side-section">
                <div class="mw-side-label">Dungeon Aspects</div>
                <For each={markers()}>
                  {(m) => (
                    <div
                      class={`mw-aspect-row${hovered()?.dungeon === m.dungeon ? ' mw-aspect-row-active' : ''}`}
                      onMouseEnter={() => setHovered(m)}
                      onMouseLeave={() => setHovered(null)}
                    >
                      <div class="mw-aspect-top">
                        <span class={`mw-aspect-dot ${m.gatedBy ? 'mw-dot-gated' : 'mw-dot-free'}`} />
                        <span class="mw-aspect-name">{m.aspectName}</span>
                      </div>
                      <div class="mw-aspect-loc">
                        {m.dungeon} · {m.region}
                        <Show when={m.gatedBy}>
                          <span class="mw-gate-tag">🔒 {m.gatedBy}</span>
                        </Show>
                      </div>
                    </div>
                  )}
                </For>
              </div>
            </Show>

            <Show when={worldDropAspects().length > 0}>
              <div class="mw-side-section">
                <div class="mw-side-label">World Drops</div>
                <For each={worldDropAspects()}>
                  {(name) => (
                    <div class="mw-aspect-row mw-aspect-row-drop">
                      <div class="mw-aspect-top">
                        <span class="mw-aspect-dot mw-dot-drop" />
                        <span class="mw-aspect-name">{name}</span>
                      </div>
                      <div class="mw-aspect-loc">Any Legendary drop</div>
                    </div>
                  )}
                </For>
              </div>
            </Show>

            <Show when={markers().length === 0 && worldDropAspects().length === 0}>
              <div class="mw-empty">No aspect location data found for this build.</div>
            </Show>
          </div>
        </div>
      </Show>
    </div>
  );
};

export default MapWidget;
