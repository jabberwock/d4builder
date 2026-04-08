import { createSignal, createResource, Show, For, onCleanup } from 'solid-js';
import type { Component } from 'solid-js';
import type { ParagonBoards, ParagonBoardEntry, ParagonGlyphData } from './types';
import './ParagonBoardWidget.css';

/* ── D4Planner board data types ─────────────────────────────────── */
interface D4PlannerBoardData {
  [className: string]:
    | { Board: Record<string, { grid: (string | null)[][] }> }
    | Record<string, { name: string; icon: number; attributes?: string[] }>;
}

interface NodeInfo {
  name: string;
  icon: number;
  attributes?: string[];
}

/* ── Image paths ────────────────────────────────────────────────── */
const MEDIA = '/media/paragon-nodes';
const TILES = '/media/paragon-tiles';

const NODE_BG: Record<string, string> = {
  Normal:    `${MEDIA}/3540327626.webp`,
  Magic:     `${MEDIA}/1925354498.webp`,
  Rare:      `${MEDIA}/65907627.webp`,
  Legendary: `${MEDIA}/3173514108.webp`,
  Gate:      `${MEDIA}/a82c31f8.webp`,
  Socket:    `${MEDIA}/4131664009.webp`,
};
const TILE_UNDERLAY = `${TILES}/tile_underlay.webp`;
const GATE_FRAME    = `${TILES}/frame.webp`;
const FALLBACK_ICON = `${MEDIA}/3162959593.webp`;

function nodeIconUrl(iconId: number | undefined): string {
  return `${MEDIA}/${iconId ?? 3162959593}.webp`;
}

function nodeRarity(key: string): string {
  return key.split('_')[1] ?? 'Normal';
}

/* ── Grid rotation (same algorithm as d4planner) ───────────────── */
function rotateGrid(grid: (string | null)[][], steps: number): (string | null)[][] {
  let g = grid;
  for (let i = 0; i < (steps % 4); i++) {
    g = g[0].map((_, col) => g.map(row => row[col]).reverse());
  }
  return g;
}

/* ── Fetch board data ───────────────────────────────────────────── */
async function fetchBoardData(): Promise<D4PlannerBoardData> {
  const r = await fetch('/data/paragon_boards.json');
  return r.json();
}

/* ── Board name resolver ────────────────────────────────────────── */
function lookupGrid(
  boardName: string,
  boardType: string,
  buildClass: string,
  rotation: number,
  data: D4PlannerBoardData,
): (string | null)[][] | null {
  const cls = buildClass.charAt(0).toUpperCase() + buildClass.slice(1).toLowerCase();
  const classData = data[cls] as { Board: Record<string, { grid: (string | null)[][] }> } | undefined;
  if (!classData?.Board) return null;

  let raw: (string | null)[][] | null = null;
  if (boardType === 'starting' || boardName.toLowerCase().includes('basic board')) {
    raw = classData.Board['Start']?.grid ?? null;
  } else if (classData.Board[boardName]) {
    raw = classData.Board[boardName].grid;
  } else {
    const lower = boardName.toLowerCase();
    const found = Object.keys(classData.Board).find(k => k.toLowerCase() === lower);
    raw = found ? classData.Board[found].grid : null;
  }

  if (!raw) return null;
  const steps = Math.round(rotation / 90) % 4;
  return steps > 0 ? rotateGrid(raw, steps) : raw;
}

function getNodeInfo(key: string, data: D4PlannerBoardData): NodeInfo | null {
  return (data['Nodes'] as Record<string, NodeInfo>)?.[key] ?? null;
}

/* ── Hex grid renderer ──────────────────────────────────────────── */
const MIN_SCALE = 0.6;
const MAX_SCALE = 4.0;
const GRID_PX = 420;

interface GridRendererProps {
  grid: (string | null)[][];
  data: D4PlannerBoardData;
}

const GridRenderer: Component<GridRendererProps> = (props) => {
  const [tip, setTip] = createSignal<{
    name: string; attrs: string[]; vx: number; vy: number;
  } | null>(null);
  const [scale, setScale] = createSignal(1.0);
  const [pan, setPan] = createSignal({ x: 0, y: 0 });
  let dragging = false;
  let dragStart = { mx: 0, my: 0, px: 0, py: 0 };
  let viewportEl!: HTMLDivElement;

  function clampPan(x: number, y: number, sc: number) {
    const overflow = GRID_PX * sc - GRID_PX;
    return {
      x: Math.max(-overflow, Math.min(0, x)),
      y: Math.max(-overflow, Math.min(0, y)),
    };
  }

  function onWheel(e: WheelEvent) {
    e.preventDefault();
    const rect = viewportEl.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const prev = scale();
    const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, prev - e.deltaY * 0.002));
    if (next === prev) return;
    const { x: px, y: py } = pan();
    const ratio = next / prev;
    const nx = mx - ratio * (mx - px);
    const ny = my - ratio * (my - py);
    setScale(next);
    setPan(clampPan(nx, ny, next));
  }

  function onMouseDown(e: MouseEvent) {
    if (e.button !== 0) return;
    dragging = true;
    dragStart = { mx: e.clientX, my: e.clientY, px: pan().x, py: pan().y };
    e.preventDefault();
  }

  function onMouseMove(e: MouseEvent) {
    if (!dragging) return;
    const dx = e.clientX - dragStart.mx;
    const dy = e.clientY - dragStart.my;
    setPan(clampPan(dragStart.px + dx, dragStart.py + dy, scale()));
  }

  function onMouseUp() { dragging = false; }

  function resetView() {
    setScale(1.0);
    setPan({ x: 0, y: 0 });
  }

  // Global mouse up in case cursor leaves window
  window.addEventListener('mouseup', onMouseUp);
  onCleanup(() => window.removeEventListener('mouseup', onMouseUp));

  const cells = () => {
    const result: Array<{
      col: number; row: number; key: string;
      rarity: string; iconUrl: string; isGate: boolean;
    }> = [];
    const grid = props.grid;
    for (let row = 0; row < grid.length; row++) {
      for (let col = 0; col < (grid[row]?.length ?? 0); col++) {
        const key = grid[row][col];
        if (!key) continue;
        const rarity = nodeRarity(key);
        const info = getNodeInfo(key, props.data);
        result.push({
          col, row, key, rarity,
          iconUrl: info ? nodeIconUrl(info.icon) : FALLBACK_ICON,
          isGate: rarity === 'Gate',
        });
      }
    }
    return result;
  };

  function onCellEnter(e: MouseEvent, key: string) {
    const info = getNodeInfo(key, props.data);
    const rect = viewportEl.getBoundingClientRect();
    const el = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // Tip position in viewport coords, adjusted for scale
    setTip({
      name: info?.name ?? key,
      attrs: info?.attributes ?? [],
      vx: (el.left + el.right) / 2 - rect.left,
      vy: el.top - rect.top,
    });
  }

  return (
    <div class="pgw-vp-wrap">
      <div
        ref={viewportEl!}
        class="pgw-viewport"
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseLeave={() => { dragging = false; setTip(null); }}
      >
        <div
          class="pgw-grid"
          style={{
            transform: `translate(${pan().x}px, ${pan().y}px) scale(${scale()})`,
            'transform-origin': '0 0',
          }}
        >
          <For each={cells()}>
            {(cell) => {
              const bg = NODE_BG[cell.rarity] ?? NODE_BG.Normal;
              const bgImg = cell.isGate
                ? `url(${cell.iconUrl}), url(${GATE_FRAME}), url(${bg}), url(${TILE_UNDERLAY})`
                : `url(${cell.iconUrl}), url(${bg}), url(${TILE_UNDERLAY})`;
              const bgSize = cell.isGate ? '14px, 22px, 20px, 20px' : '14px, 20px, 20px';
              const bgPos  = cell.isGate ? 'center, center, 0 0, 0 0' : 'center, 0 0, 0 0';
              return (
                <div
                  class={`pgw-cell pgw-${cell.rarity.toLowerCase()}`}
                  style={{
                    left: `${cell.col * 20}px`,
                    top:  `${cell.row * 20}px`,
                    'background-image': bgImg,
                    'background-size': bgSize,
                    'background-position': bgPos,
                  }}
                  onMouseEnter={(e) => onCellEnter(e, cell.key)}
                  onMouseLeave={() => setTip(null)}
                />
              );
            }}
          </For>
        </div>

        {/* tooltip pinned to viewport coords */}
        <Show when={tip()}>
          {(t) => (
            <div class="pgw-tip" style={{ left: `${t().vx}px`, top: `${t().vy}px` }}>
              <div class="pgw-tip-name">{t().name}</div>
              <Show when={t().attrs.length > 0}>
                <div class="pgw-tip-attrs">
                  <For each={t().attrs}>
                    {(a) => <div class="pgw-tip-attr">{a}</div>}
                  </For>
                </div>
              </Show>
            </div>
          )}
        </Show>
      </div>

      {/* zoom controls */}
      <div class="pgw-zoom-bar">
        <button class="pgw-zoom-btn" onClick={() => {
          const next = Math.min(MAX_SCALE, scale() * 1.3);
          setScale(next);
          setPan(clampPan(pan().x, pan().y, next));
        }} title="Zoom in">+</button>
        <button class="pgw-zoom-btn" onClick={resetView} title="Reset view">⌂</button>
        <button class="pgw-zoom-btn" onClick={() => {
          const next = Math.max(MIN_SCALE, scale() / 1.3);
          setScale(next);
          setPan(clampPan(pan().x, pan().y, next));
        }} title="Zoom out">−</button>
      </div>
    </div>
  );
};

/* ── Data normalisation ─────────────────────────────────────────── */
interface OldGlyph { name: string; board: string; note?: string }
interface OldParagonBoards {
  primary?: { name: string };
  secondary?: { name: string };
  tertiary?: { name: string };
  glyphs?: OldGlyph[];
}

function findGlyph(name: string, glyphs: OldGlyph[]): ParagonGlyphData | 'None' {
  const g = glyphs.find(gl => gl.board === name);
  if (!g) return 'None';
  return { name: g.name, radius: 15, primary_bonus: g.note ?? '', radius_bonus: '' };
}

function mkEntry(name: string, type: string, glyphs: OldGlyph[]): ParagonBoardEntry {
  return { name, type, paragon_points: type === 'starting' ? 25 : 200,
           socket: '', glyph: findGlyph(name, glyphs), stat_increases: {} };
}

function normalise(raw: unknown): ParagonBoards {
  const pb = raw as Record<string, unknown>;
  if (pb?.starting != null) return raw as ParagonBoards;
  const old = raw as OldParagonBoards;
  const gl = old.glyphs ?? [];
  return {
    starting: mkEntry('Starting Board', 'starting', gl),
    board_1: old.primary   ? mkEntry(old.primary.name, 'legendary', gl)   : mkEntry('Board 1', 'legendary', gl),
    board_2: old.secondary ? mkEntry(old.secondary.name, 'legendary', gl) : mkEntry('Board 2', 'legendary', gl),
    board_3: old.tertiary  ? mkEntry(old.tertiary.name, 'legendary', gl)  : mkEntry('Board 3', 'legendary', gl),
    total_paragon_points: 625,
    paragon_point_allocation: {},
  };
}

function statLabel(k: string) {
  return k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/* ── Node type legend ───────────────────────────────────────────── */
const LEGEND = [
  { rarity: 'legendary', label: 'Legendary', hint: 'Primary board bonus — always path toward this' },
  { rarity: 'rare',      label: 'Rare',      hint: 'Strong secondary stat — take if on path to Legendary/Socket' },
  { rarity: 'socket',    label: 'Socket',    hint: 'Glyph slot — the main routing target' },
  { rarity: 'magic',     label: 'Magic',     hint: 'Minor bonus — take to reach higher nodes' },
  { rarity: 'normal',    label: 'Normal',    hint: 'Basic stat (Str/Dex/Int/Will) — take as filler' },
  { rarity: 'gate',      label: 'Gate',      hint: 'Board connection point — edges of the board' },
];

/* ── Main widget ─────────────────────────────────────────────────── */
interface ParagonBoardWidgetProps {
  paragon_boards: ParagonBoards;
  buildClass: string;
}

const ParagonBoardWidget: Component<ParagonBoardWidgetProps> = (props) => {
  const [activeIdx, setActiveIdx] = createSignal(0);
  const [boardData] = createResource(fetchBoardData);

  const pb = () => normalise(props.paragon_boards);

  const boards = () => {
    const d = pb();
    const list: Array<{ key: string; entry: ParagonBoardEntry }> = [
      { key: 'starting', entry: d.starting },
      { key: 'board_1',  entry: d.board_1  },
      { key: 'board_2',  entry: d.board_2  },
      { key: 'board_3',  entry: d.board_3  },
    ];
    if (d.board_4) list.push({ key: 'board_4', entry: d.board_4 });
    return list;
  };

  const active = () => boards()[activeIdx()];

  const activeEntry = () => active()?.entry;

  const rotation = () => activeEntry()?.rotation ?? 0;

  const grid = () => {
    const data = boardData();
    const entry = activeEntry();
    if (!data || !entry) return null;
    return lookupGrid(entry.name, entry.type, props.buildClass, rotation(), data);
  };

  const glyph = () => {
    const g = activeEntry()?.glyph;
    return (g && g !== 'None' && typeof g === 'object') ? g as ParagonGlyphData : null;
  };

  const stats = () => Object.entries(activeEntry()?.stat_increases ?? {});
  const allocation = () => Object.entries(pb().paragon_point_allocation ?? {});

  const BOARD_SHORT = (idx: number) => idx === 0 ? 'Start' : `Board ${idx}`;

  return (
    <div class="pgw-wrap">
      {/* header */}
      <div class="pgw-header">
        <span class="pgw-title">Paragon</span>
        <span class="pgw-pts">{pb().total_paragon_points} pts</span>
      </div>

      {/* board tabs */}
      <div class="pgw-tabs" role="tablist">
        <For each={boards()}>
          {({ entry }, i) => (
            <button
              class={`pgw-tab${i() === activeIdx() ? ' active' : ''}`}
              role="tab"
              aria-selected={i() === activeIdx()}
              onClick={() => setActiveIdx(i())}
            >
              <span class="pgw-tab-num">{i() + 1}</span>
              <span class="pgw-tab-label">{entry.name || BOARD_SHORT(i())}</span>
            </button>
          )}
        </For>
      </div>

      {/* board content */}
      <div class="pgw-content">
        {/* grid column */}
        <div class="pgw-grid-col">
          <Show when={boardData.loading}>
            <div class="pgw-loading">Loading…</div>
          </Show>
          <Show when={!boardData.loading && grid()}>
            {(g) => <GridRenderer grid={g()} data={boardData()!} />}
          </Show>
          <Show when={!boardData.loading && !grid()}>
            <div class="pgw-no-layout">No layout data for "{activeEntry()?.name}"</div>
          </Show>

          {/* node legend */}
          <div class="pgw-legend">
            <For each={LEGEND}>
              {(item) => (
                <div class="pgw-legend-row" title={item.hint}>
                  <span class={`pgw-legend-dot pgw-${item.rarity}`} />
                  <span class="pgw-legend-label">{item.label}</span>
                </div>
              )}
            </For>
          </div>
        </div>

        {/* side panel */}
        <div class="pgw-side">
          <div class="pgw-board-name">{activeEntry()?.name}</div>
          <div class="pgw-board-pts">{activeEntry()?.paragon_points} points</div>

          {/* rotation note */}
          <Show when={rotation() !== 0}>
            <div class="pgw-rotation-note">↺ Rotated {rotation()}°</div>
          </Show>

          {/* routing note */}
          <div class="pgw-routing-note">
            Path toward the <strong>Socket</strong> (glyph slot), collecting <strong>Rare</strong>
            and <strong>Legendary</strong> nodes along the way.
          </div>

          {/* glyph */}
          <Show when={glyph()}>
            {(g) => (
              <div class="pgw-glyph">
                <div class="pgw-glyph-header">
                  <span class="pgw-glyph-icon">◈</span>
                  <span class="pgw-glyph-name">{g().name}</span>
                  <span class="pgw-glyph-r">r{g().radius}</span>
                </div>
                <Show when={g().primary_bonus}>
                  <div class="pgw-glyph-bonus">{g().primary_bonus}</div>
                </Show>
                <Show when={g().radius_bonus}>
                  <div class="pgw-glyph-threshold">{g().radius_bonus}</div>
                </Show>
              </div>
            )}
          </Show>

          {/* stat increases */}
          <Show when={stats().length > 0}>
            <div class="pgw-stats">
              <For each={stats()}>
                {([k, v]) => (
                  <div class="pgw-stat-row">
                    <span class="pgw-stat-k">{statLabel(k)}</span>
                    <span class="pgw-stat-v">+{v}</span>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>
      </div>

      {/* point allocation footer */}
      <Show when={allocation().length > 0}>
        <div class="pgw-alloc">
          <div class="pgw-alloc-title">Point Allocation</div>
          <div class="pgw-alloc-grid">
            <For each={allocation()}>
              {([cat, pts]) => (
                <div class="pgw-alloc-row">
                  <span>{statLabel(cat)}</span>
                  <span class="pgw-alloc-pts">{pts}</span>
                </div>
              )}
            </For>
          </div>
        </div>
      </Show>
    </div>
  );
};

export default ParagonBoardWidget;
