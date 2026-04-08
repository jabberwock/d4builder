#!/usr/bin/env node
/**
 * fetch-paragon-lothrik.cjs
 *
 * Fetches real paragon board node layout data from Lothrik/diablo4-build-calc
 * (https://github.com/Lothrik/diablo4-build-calc — MIT license, game assets © Blizzard)
 *
 * Source data has exact board grids (2D arrays), real node names, and descriptions
 * for Barbarian, Druid, Necromancer, Rogue, Sorcerer (5 classes, 9 boards each = 45 boards).
 *
 * Outputs: src/ParagonBoardData.ts
 */

const https = require('https');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DATA_URL = 'https://raw.githubusercontent.com/Lothrik/diablo4-build-calc/master/data/paragon.js';

const HEX_SIZE = 14; // px radius per hex node
const HEX_W = HEX_SIZE * 2;      // horizontal spacing
const HEX_H = HEX_SIZE * Math.sqrt(3); // vertical spacing

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const opts = new URL(url);
    https.get({
      hostname: opts.hostname,
      path: opts.pathname + opts.search,
      headers: { 'User-Agent': 'd4builder-paragon-fetcher' },
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

/** Determine node rarity/type from node ID string */
function classifyNode(nodeId) {
  if (!nodeId) return { type: 'normal', rarity: 'normal' };
  const n = nodeId.toLowerCase();
  if (n.includes('_gate')) return { type: 'gate', rarity: 'normal' };
  if (n.includes('_socket')) return { type: 'glyph', rarity: 'rare' };
  if (n.startsWith('startnode')) return { type: 'normal', rarity: 'normal' };
  if (n.includes('_legendary_')) return { type: 'legendary', rarity: 'legendary' };
  if (n.includes('_rare_') || n.endsWith('_rare')) return { type: 'normal', rarity: 'rare' };
  if (n.includes('_magic_') || n.endsWith('_magic')) return { type: 'normal', rarity: 'magic' };
  return { type: 'normal', rarity: 'normal' };
}

/** Convert grid col/row to SVG pixel coordinates (offset hex grid) */
function gridToPixel(col, row) {
  const x = col * HEX_W + (row % 2 === 1 ? HEX_W / 2 : 0);
  const y = row * HEX_H * 0.75;
  return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 };
}

/** Resolve human-readable name and description for a node ID */
function resolveNodeInfo(nodeId, classNodes, genericNodes) {
  const lookup = (id) => classNodes[id] || genericNodes[id];
  const info = lookup(nodeId);
  if (info) {
    return { name: info.name || nodeId, description: info.description || '' };
  }
  // Fallback: prettify the ID
  const pretty = nodeId.replace(/^Generic_|^[A-Z][a-z]+_/, '')
    .replace(/_/g, ' ').trim();
  return { name: pretty, description: '' };
}

/** Build board layout from Lothrik 2D grid */
function transformBoard(boardId, boardName, cls, grid2D, classNodes, genericNodes) {
  const nodes = [];
  const occupied = new Set();

  const numRows = grid2D.length;

  // First pass: collect occupied cells
  for (let row = 0; row < numRows; row++) {
    const rowArr = grid2D[row];
    if (!rowArr) continue;
    for (let col = 0; col < rowArr.length; col++) {
      if (rowArr[col]) occupied.add(`${row},${col}`);
    }
  }

  // Second pass: build nodes
  for (let row = 0; row < numRows; row++) {
    const rowArr = grid2D[row];
    if (!rowArr) continue;
    for (let col = 0; col < rowArr.length; col++) {
      const nodeId = rowArr[col];
      if (!nodeId) continue;

      const { x, y } = gridToPixel(col, row);
      const { type, rarity } = classifyNode(nodeId);
      const { name, description } = resolveNodeInfo(nodeId, classNodes, genericNodes);

      nodes.push({
        id: `${boardId}_${row}_${col}`,
        type,
        rarity,
        x,
        y,
        gridRow: row,
        gridCol: col,
        nodeKey: nodeId,
        name,
        description,
      });
    }
  }

  // Build connections (4-directional adjacency)
  const connections = [];
  const addedConns = new Set();

  for (let row = 0; row < numRows; row++) {
    const rowArr = grid2D[row];
    if (!rowArr) continue;
    for (let col = 0; col < rowArr.length; col++) {
      if (!rowArr[col]) continue;
      const fromId = `${boardId}_${row}_${col}`;

      const neighbors = [
        [row - 1, col], [row + 1, col],
        [row, col - 1], [row, col + 1],
      ];

      for (const [r2, c2] of neighbors) {
        if (!occupied.has(`${r2},${c2}`)) continue;
        const toId = `${boardId}_${r2}_${c2}`;
        const key = [fromId, toId].sort().join('->');
        if (addedConns.has(key)) continue;
        addedConns.add(key);

        const p1 = gridToPixel(col, row);
        const p2 = gridToPixel(c2, r2);
        connections.push({
          from: fromId,
          to: toId,
          pathData: `M${p1.x} ${p1.y} L${p2.x} ${p2.y}`,
          state: 'default',
        });
      }
    }
  }

  // Compute viewBox
  const xs = nodes.map(n => n.x);
  const ys = nodes.map(n => n.y);
  const pad = HEX_W * 1.5;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  const maxX = Math.max(...xs) + pad;
  const maxY = Math.max(...ys) + pad;
  const vbW = Math.round(maxX - minX);
  const vbH = Math.round(maxY - minY);

  // Offset all positions
  nodes.forEach(n => { n.x = Math.round((n.x - minX) * 10) / 10; n.y = Math.round((n.y - minY) * 10) / 10; });
  connections.forEach(c => {
    c.pathData = c.pathData.replace(/M([\d.-]+) ([\d.-]+) L([\d.-]+) ([\d.-]+)/, (_, x1, y1, x2, y2) =>
      `M${Math.round((+x1 - minX)*10)/10} ${Math.round((+y1-minY)*10)/10} L${Math.round((+x2-minX)*10)/10} ${Math.round((+y2-minY)*10)/10}`
    );
  });

  const gatePositions = nodes.filter(n => n.type === 'gate').map(n => ({ x: n.x, y: n.y }));
  const legendaryNode = nodes.find(n => n.type === 'legendary');

  return {
    boardId,
    name: boardName,
    class: cls,
    description: legendaryNode ? legendaryNode.name : '',
    nodes,
    connections,
    gatePositions,
    width: vbW,
    height: vbH,
    viewBox: `0 0 ${vbW} ${vbH}`,
  };
}

// Maps class name to short prefix used in board IDs
const CLASS_PREFIX = {
  Barbarian: 'barb',
  Druid: 'druid',
  Necromancer: 'necro',
  Rogue: 'rogue',
  Sorcerer: 'sorc',
};

async function main() {
  console.log('Fetching paragon data from Lothrik/diablo4-build-calc...');
  const raw = await fetchText(DATA_URL);
  console.log(`Downloaded ${(raw.length / 1024).toFixed(0)} KB`);

  // Strip ES module syntax, then evaluate via vm to get the data object
  const cleaned = raw
    .replace(/^export\s*\{[^}]*\};\s*$/m, '')   // remove: export { paragonData };
    .replace(/^let\s+paragonData\s*=\s*/, '')    // remove: let paragonData =
    .replace(/^var\s+paragonData\s*=\s*/, '')    // remove: var paragonData =
    .trimStart();

  // Now `cleaned` is just the raw JSON-like object literal, wrap to return it
  const ctx = {};
  const script = new vm.Script(`__result__ = (${cleaned.replace(/;\s*$/, '')})`);
  vm.createContext(ctx);
  script.runInContext(ctx);

  const paragonData = ctx.__result__;
  if (!paragonData) {
    throw new Error('Could not extract paragonData from file');
  }

  const genericNodes = paragonData['Generic']?.Node || {};
  const allBoards = {};

  const classes = Object.keys(CLASS_PREFIX);

  for (const cls of classes) {
    const clsData = paragonData[cls];
    if (!clsData) { console.warn(`No data for ${cls}`); continue; }

    const boards = clsData.Board || {};
    const classNodes = clsData.Node || {};
    const prefix = CLASS_PREFIX[cls];
    const boardNames = Object.keys(boards);

    console.log(`  ${cls}: ${boardNames.length} boards`);

    boardNames.forEach((boardName, index) => {
      const boardId = `paragon_${prefix}_${String(index).padStart(2, '0')}`;
      const displayName = boardName === 'Start'
        ? `${cls} Starting Board`
        : boardName;

      const grid2D = boards[boardName];
      const layout = transformBoard(boardId, displayName, cls, grid2D, classNodes, genericNodes);
      allBoards[boardId] = layout;
    });
  }

  const totalBoards = Object.keys(allBoards).length;
  const totalNodes = Object.values(allBoards).reduce((s, b) => s + b.nodes.length, 0);
  console.log(`\nTransformed ${totalBoards} boards, ${totalNodes} total nodes`);

  // Build TypeScript output
  const outPath = path.join(__dirname, '../src/ParagonBoardData.ts');

  const byClass = {};
  for (const [id, board] of Object.entries(allBoards)) {
    if (!byClass[board.class]) byClass[board.class] = 0;
    byClass[board.class]++;
  }

  // Deduplicate node info into a lookup map (nodeKey → { name, description })
  // This avoids repeating the same name/description for every Generic node instance
  const nodeInfoMap = {};
  for (const board of Object.values(allBoards)) {
    for (const node of board.nodes) {
      if (!nodeInfoMap[node.nodeKey]) {
        nodeInfoMap[node.nodeKey] = { name: node.name, description: node.description };
      }
      // Remove from node — will be looked up at render time
      delete node.name;
      delete node.description;
    }
  }

  let ts = `/**
 * Paragon Board Layout Data — AUTO-GENERATED
 * Source: https://github.com/Lothrik/diablo4-build-calc (MIT license)
 * Game assets © Blizzard Entertainment
 *
 * ${totalBoards} boards across ${Object.keys(byClass).length} classes
 * ${totalNodes} total nodes with real names and descriptions
 */

export interface ParagonNode {
  id: string;
  type: 'normal' | 'glyph' | 'legendary' | 'gate';
  rarity: 'normal' | 'magic' | 'rare' | 'legendary';
  x: number;
  y: number;
  gridRow: number;
  gridCol: number;
  nodeKey: string;
}

export interface ParagonNodeInfo {
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

/** Lookup: nodeKey → { name, description } — shared across all boards */
export const NODE_INFO: Record<string, ParagonNodeInfo> = ${JSON.stringify(nodeInfoMap)};

export function getNodeInfo(nodeKey: string): ParagonNodeInfo {
  return NODE_INFO[nodeKey] ?? { name: nodeKey.replace(/_/g, ' '), description: '' };
}

export const ALL_PARAGON_BOARDS: Record<string, ParagonBoardLayout> = ${JSON.stringify(allBoards)};

export function getParagonBoard(boardId: string): ParagonBoardLayout | null {
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

/** Look up a board by display name and class */
export function getBoardByName(name: string, className: string): ParagonBoardLayout | null {
  const cls = className.toLowerCase();
  const nameLower = name.toLowerCase().trim();
  for (const board of Object.values(ALL_PARAGON_BOARDS)) {
    if (board.class.toLowerCase() === cls && board.name.toLowerCase() === nameLower) {
      return board;
    }
  }
  // Partial match fallback
  for (const board of Object.values(ALL_PARAGON_BOARDS)) {
    if (board.class.toLowerCase() === cls && board.name.toLowerCase().includes(nameLower)) {
      return board;
    }
  }
  return null;
}
`;

  fs.writeFileSync(outPath, ts, 'utf8');
  console.log(`\nWrote ${outPath} (${(ts.length/1024).toFixed(0)} KB)`);
  console.log(`Node info map: ${Object.keys(nodeInfoMap).length} unique node types`);
  for (const [cls, count] of Object.entries(byClass)) {
    console.log(`  ${cls}: ${count} boards`);
  }
}

main().catch(e => { console.error(e); process.exit(1); });
