#!/usr/bin/env node
/**
 * fetch-paragon-nodes.js
 *
 * Fetches real paragon board node layout data from DiabloTools/d4data
 * (https://github.com/DiabloTools/d4data — MIT license, game assets © Blizzard)
 *
 * Outputs: src/ParagonBoardData.ts with hex-grid node positions for all boards.
 *
 * Board grid: 21×21 flat array. Position: col = index%21, row = index/21
 * Hex render: x = col * HEX_W + (row%2 * HEX_W/2), y = row * HEX_H * 0.75
 */

const https = require('https');
const fs = require('fs');
const path = require('path');

const BASE_URL = 'https://raw.githubusercontent.com/DiabloTools/d4data/master';
const API_URL = 'https://api.github.com/repos/DiabloTools/d4data/contents/json/base/meta/ParagonBoard';

const HEX_SIZE = 28; // px per hex node radius
const HEX_W = HEX_SIZE * 2;
const HEX_H = HEX_SIZE * Math.sqrt(3);

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const opts = new URL(url);
    const options = {
      hostname: opts.hostname,
      path: opts.pathname + opts.search,
      headers: {
        'User-Agent': 'd4builder-paragon-fetcher',
        'Accept': 'application/json',
      },
    };
    https.get(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error(`JSON parse error for ${url}: ${e.message}`)); }
      });
    }).on('error', reject);
  });
}

/** Determine node rarity/type from node name */
function classifyNode(name) {
  if (!name) return { type: 'normal', rarity: 'normal' };
  const n = name.toLowerCase();
  if (n.includes('gate')) return { type: 'gate', rarity: 'normal' };
  if (n.includes('socket')) return { type: 'glyph', rarity: 'rare' };
  if (n.includes('start')) return { type: 'normal', rarity: 'normal' }; // start node
  if (n.includes('legendary')) return { type: 'legendary', rarity: 'legendary' };
  if (n.includes('_rare_') || n.endsWith('_rare')) return { type: 'normal', rarity: 'rare' };
  if (n.includes('_magic_') || n.endsWith('_magic')) return { type: 'normal', rarity: 'magic' };
  return { type: 'normal', rarity: 'normal' };
}

/** Convert board name like "Paragon_Barb_01" to metadata */
function parseBoardFileName(filename) {
  // e.g. "Paragon_Barb_01.pbd.json" -> { class: 'Barbarian', index: 1, id: 'paragon_barb_01' }
  const m = filename.match(/^Paragon_(Barb|Druid|Necro|Rogue|Sorc|Pal|Spirit)_(\d+)\.pbd\.json$/i);
  if (!m) return null;
  const classMap = {
    Barb: 'Barbarian', Druid: 'Druid', Necro: 'Necromancer',
    Rogue: 'Rogue', Sorc: 'Sorcerer', Pal: 'Paladin', Spirit: 'Spiritborn',
  };
  return {
    cls: classMap[m[1]] || m[1],
    index: parseInt(m[2], 10),
    id: `paragon_${m[1].toLowerCase()}_${m[2]}`,
    filename,
  };
}

/** Convert flat 21×21 grid to hex-grid pixel coordinates */
function gridToHex(col, row) {
  const x = col * HEX_W + (row % 2 === 1 ? HEX_W / 2 : 0);
  const y = row * HEX_H * 0.75;
  return { x: Math.round(x), y: Math.round(y) };
}

/** Find adjacent occupied grid positions (4-directional) */
function getAdjacent(row, col, occupied) {
  const neighbors = [
    [row - 1, col], [row + 1, col],
    [row, col - 1], [row, col + 1],
  ];
  return neighbors.filter(([r, c]) => occupied.has(`${r},${c}`));
}

async function transformBoard(meta, boardData) {
  const entries = boardData.arEntries;
  const width = boardData.nWidth || 21;

  const nodes = [];
  const occupied = new Set();

  // First pass: collect occupied positions
  entries.forEach((entry, i) => {
    if (!entry) return;
    const col = i % width;
    const row = Math.floor(i / width);
    occupied.add(`${row},${col}`);
  });

  // Second pass: build nodes
  let legendaryNode = null;
  let socketNode = null;
  entries.forEach((entry, i) => {
    if (!entry) return;
    const col = i % width;
    const row = Math.floor(i / width);
    const { x, y } = gridToHex(col, row);
    const nodeName = entry.name || '';
    const { type, rarity } = classifyNode(nodeName);

    const node = {
      id: `${meta.id}_${row}_${col}`,
      type,
      rarity,
      x,
      y,
      gridRow: row,
      gridCol: col,
      name: nodeName,
      description: nodeName.replace(/_/g, ' '),
    };

    if (type === 'legendary') legendaryNode = node;
    if (type === 'glyph') socketNode = node;
    nodes.push(node);
  });

  // Build connections (adjacency in grid)
  const connections = [];
  const addedConnections = new Set();
  entries.forEach((entry, i) => {
    if (!entry) return;
    const col = i % width;
    const row = Math.floor(i / width);
    const fromId = `${meta.id}_${row}_${col}`;

    getAdjacent(row, col, occupied).forEach(([r2, c2]) => {
      const toId = `${meta.id}_${r2}_${c2}`;
      const key = [fromId, toId].sort().join('->');
      if (!addedConnections.has(key)) {
        addedConnections.add(key);
        const p1 = gridToHex(col, row);
        const p2 = gridToHex(c2, r2);
        connections.push({
          from: fromId,
          to: toId,
          pathData: `M${p1.x} ${p1.y} L${p2.x} ${p2.y}`,
          state: 'default',
        });
      }
    });
  });

  // Compute viewBox from node positions
  const xs = nodes.map(n => n.x);
  const ys = nodes.map(n => n.y);
  const minX = Math.min(...xs) - HEX_W;
  const minY = Math.min(...ys) - HEX_H;
  const maxX = Math.max(...xs) + HEX_W;
  const maxY = Math.max(...ys) + HEX_H;
  const vbW = maxX - minX;
  const vbH = maxY - minY;

  // Offset all nodes by -minX/-minY so viewBox starts at 0
  nodes.forEach(n => { n.x -= minX; n.y -= minY; });
  connections.forEach(c => {
    c.pathData = c.pathData.replace(/M(-?\d+) (-?\d+) L(-?\d+) (-?\d+)/, (_, x1, y1, x2, y2) =>
      `M${+x1 - minX} ${+y1 - minY} L${+x2 - minX} ${+y2 - minY}`
    );
  });

  const gatePositions = nodes.filter(n => n.type === 'gate').map(n => ({ x: n.x, y: n.y }));
  const displayName = BOARD_DISPLAY_NAMES[meta.id] || null;
  const boardName = meta.index === 0
    ? `${meta.cls} Starting Board`
    : (displayName || `${meta.cls} Board ${meta.index}`);

  return {
    boardId: meta.id,
    name: boardName,
    class: meta.cls,
    description: legendaryNode ? legendaryNode.name.replace(/_/g, ' ') : '',
    nodes,
    connections,
    gatePositions,
    width: Math.round(vbW),
    height: Math.round(vbH),
    viewBox: `0 0 ${Math.round(vbW)} ${Math.round(vbH)}`,
  };
}

// Mapping from d4data board_id -> human-readable board name (szInternalName from Power files)
const BOARD_DISPLAY_NAMES = {
  "paragon_barb_01": "Hemorrhage",
  "paragon_barb_02": "Blood Rage",
  "paragon_barb_03": "Carnage",
  "paragon_barb_04": "Decimator",
  "paragon_barb_05": "Bone Breaker",
  "paragon_barb_06": "Flawless Technique",
  "paragon_barb_07": "Warbringer",
  "paragon_barb_08": "Weapons Master",
  "paragon_barb_10": "Force of Nature",
  "paragon_druid_01": "Thunderstruck",
  "paragon_druid_02": "Earthen Devastation",
  "paragon_druid_03": "Survival Instincts",
  "paragon_druid_04": "Lust for Carnage",
  "paragon_druid_05": "Heightened Malice",
  "paragon_druid_06": "Inner Beast",
  "paragon_druid_07": "Sinister Tendrils",
  "paragon_druid_08": "Ancestral Guidance",
  "paragon_druid_10": "Untamed",
  "paragon_necro_01": "Cult Leader",
  "paragon_necro_02": "Hulking Monstrosity",
  "paragon_necro_03": "Flesh-eater",
  "paragon_necro_04": "Scent of Death",
  "paragon_necro_05": "Bone Graft",
  "paragon_necro_06": "Blood Begets Blood",
  "paragon_necro_07": "Bloodbath",
  "paragon_necro_08": "Wither",
  "paragon_rogue_01": "Eldritch Bounty",
  "paragon_rogue_02": "Tricks of the Trade",
  "paragon_rogue_03": "Cheap Shot",
  "paragon_rogue_04": "Deadly Ambush",
  "paragon_rogue_05": "Leyrana's Instinct",
  "paragon_rogue_06": "No Witnesses",
  "paragon_rogue_07": "Exploit Weakness",
  "paragon_rogue_08": "Cunning Stratagem",
  "paragon_rogue_10": "Danse Macabre",
  "paragon_sorc_01": "Searing Heat",
  "paragon_sorc_02": "Frigid Fate",
  "paragon_sorc_03": "Static Surge",
  "paragon_sorc_04": "Elemental Summoner",
  "paragon_sorc_05": "Burning Instinct",
  "paragon_sorc_06": "Icefall",
  "paragon_sorc_07": "Ceaseless Conduit",
  "paragon_sorc_08": "Enchantment Master",
  "paragon_sorc_10": "Component Release",
  "paragon_spirit_01": "Gorilla Jaguar Legendary",
  "paragon_spirit_02": "Gorilla Centipede Legendary",
  "paragon_spirit_03": "Gorilla Eagle Legendary",
  "paragon_spirit_04": "Centi Jaguar Legendary",
  "paragon_spirit_05": "Centi Eagle Legendary",
  "paragon_spirit_06": "Jaguar Eagle Legendary",
  "paragon_spirit_07": "Generic Omni Legendary",
  "paragon_spirit_08": "Generic Omni Legendary",
};

async function main() {
  console.log('Fetching paragon board list from DiabloTools/d4data...');

  const listing = await fetchJson(API_URL);
  const boardFiles = listing
    .map(f => f.name)
    .filter(n => /^Paragon_(Barb|Druid|Necro|Rogue|Sorc|Pal|Spirit)_\d+\.pbd\.json$/.test(n))
    .sort();

  console.log(`Found ${boardFiles.length} board files`);

  const allBoards = {};
  let count = 0;

  for (const filename of boardFiles) {
    const meta = parseBoardFileName(filename);
    if (!meta) continue;

    const url = `${BASE_URL}/json/base/meta/ParagonBoard/${encodeURIComponent(filename)}`;
    console.log(`  Fetching ${filename}...`);
    try {
      const boardData = await fetchJson(url);
      const layout = await transformBoard(meta, boardData);
      allBoards[meta.id] = layout;
      count++;
    } catch (e) {
      console.error(`  ERROR fetching ${filename}: ${e.message}`);
    }

    // Small delay to avoid rate limiting
    await new Promise(r => setTimeout(r, 100));
  }

  console.log(`\nTransformed ${count} boards. Generating TypeScript...`);

  // Group by class
  const byClass = {};
  for (const [id, board] of Object.entries(allBoards)) {
    const cls = board.class;
    if (!byClass[cls]) byClass[cls] = {};
    byClass[cls][id] = board;
  }

  // Generate TypeScript output
  const outPath = path.join(__dirname, '../src/ParagonBoardData.ts');

  let ts = `/**
 * Paragon Board Layout Data — AUTO-GENERATED
 * Source: https://github.com/DiabloTools/d4data (MIT license)
 * Game assets © Blizzard Entertainment
 *
 * Generated by scripts/fetch-paragon-nodes.js
 * ${count} boards across ${Object.keys(byClass).length} classes
 *
 * Grid: 21×21 flat array → hex offset coordinates
 * HEX_SIZE=${HEX_SIZE}px, HEX_W=${HEX_W}px, HEX_H=${Math.round(HEX_H)}px
 */

export interface ParagonNode {
  id: string;
  type: 'normal' | 'glyph' | 'legendary' | 'gate';
  rarity: 'normal' | 'magic' | 'rare' | 'legendary';
  x: number;
  y: number;
  gridRow: number;
  gridCol: number;
  name: string;
  description: string;
}

export interface ParagonConnection {
  from: string;
  to: string;
  pathData: string;
  state: 'active' | 'locked' | 'default';
}

export interface ParagonBoardLayout {
  boardId: string;
  name: string;
  class: string;
  description: string;
  nodes: ParagonNode[];
  connections: ParagonConnection[];
  gatePositions: { x: number; y: number }[];
  width: number;
  height: number;
  viewBox: string;
}

`;

  // Emit all boards as a single lookup map
  ts += `export const ALL_PARAGON_BOARDS: Record<string, ParagonBoardLayout> = ${JSON.stringify(allBoards, null, 2)};\n\n`;

  ts += `export function getParagonBoard(boardId: string): ParagonBoardLayout | null {
  return ALL_PARAGON_BOARDS[boardId] ?? null;
}

export function getBoardsByClass(className: string): Record<string, ParagonBoardLayout> {
  const cls = className.toLowerCase();
  const result: Record<string, ParagonBoardLayout> = {};
  for (const [id, board] of Object.entries(ALL_PARAGON_BOARDS)) {
    if (board.class.toLowerCase() === cls) result[id] = board;
  }
  return result;
}

/** Look up a board by its display name (e.g. "Carnage") and class (e.g. "Barbarian") */
export function getBoardByName(name: string, className: string): ParagonBoardLayout | null {
  const cls = className.toLowerCase();
  const nameLower = name.toLowerCase().trim();
  for (const board of Object.values(ALL_PARAGON_BOARDS)) {
    if (board.class.toLowerCase() === cls && board.name.toLowerCase() === nameLower) {
      return board;
    }
  }
  // Fallback: partial match
  for (const board of Object.values(ALL_PARAGON_BOARDS)) {
    if (board.class.toLowerCase() === cls && board.name.toLowerCase().includes(nameLower)) {
      return board;
    }
  }
  return null;
}
`;

  fs.writeFileSync(outPath, ts, 'utf8');
  console.log(`\nWrote ${outPath}`);
  console.log(`Boards by class:`);
  for (const [cls, boards] of Object.entries(byClass)) {
    console.log(`  ${cls}: ${Object.keys(boards).length} boards`);
  }
}

main().catch(e => { console.error(e); process.exit(1); });
