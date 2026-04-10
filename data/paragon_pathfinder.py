#!/usr/bin/env python3
"""
Paragon board pathfinder.
For each recommended board, find the optimal path from entry gate
through glyph socket and legendary node, preferring main stat nodes.
"""

import json
import re
from collections import deque
from pathlib import Path

BOARD_DATA_PATH = Path(__file__).parent / "../webapp/src/ParagonBoardData.ts"

MAX_PARAGON_POINTS = 300
MAX_BOARDS = 5
STAT_PER_NODE = 5  # normal nodes give +5 per stat point

CLASS_MAIN_STAT = {
    "Barbarian": "Str", "Druid": "Will", "Necromancer": "Int",
    "Rogue": "Dex", "Sorcerer": "Int", "Spiritborn": "Dex",
    "Paladin": "Str", "Warlock": "Int",
}


def load_boards() -> dict:
    """Parse ALL_PARAGON_BOARDS from TypeScript file or return empty if unavailable."""
    if not BOARD_DATA_PATH.exists():
        return {}
    try:
        with open(BOARD_DATA_PATH) as f:
            content = f.read()
        start = content.index("ALL_PARAGON_BOARDS")
        eq = content.index("{", start)
        depth = 0
        end = eq
        for i in range(eq, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        return json.loads(content[eq:end])
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {}


def build_adjacency(board: dict) -> dict[str, list[str]]:
    """Build adjacency list from connections."""
    adj: dict[str, list[str]] = {}
    for conn in board.get("connections", []):
        a, b = conn["from"], conn["to"]
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    return adj


def weighted_path(
    adj: dict,
    start: str,
    target: str,
    nodes_by_id: dict,
    main_stat: str,
) -> list[str]:
    """
    Dijkstra shortest path from start to target.
    Nodes with the class's main stat are cheaper to traverse,
    so the path naturally prefers main stat nodes.
    """
    import heapq

    if start == target:
        return [start]

    # Cost per node: main stat = 0.5, other stat = 1.0,
    # magic/rare nodes = 0.3 (always valuable), legendary = 0, gate = 0
    def node_cost(node_id: str) -> float:
        node = nodes_by_id.get(node_id, {})
        nk = node.get("nodeKey", "")
        rarity = node.get("rarity", "normal")
        ntype = node.get("type", "normal")

        if ntype in ("gate", "glyph", "legendary"):
            return 0.1
        if rarity in ("magic", "rare"):
            return 0.3  # always worth grabbing
        if rarity == "legendary":
            return 0.1
        # Normal stat nodes
        if nk == f"Generic_Normal_{main_stat}":
            return 0.5  # prefer main stat
        return 1.0  # off-stat costs more

    # Dijkstra
    dist = {start: 0.0}
    prev: dict[str, str | None] = {start: None}
    heap = [(0.0, start)]

    while heap:
        d, node = heapq.heappop(heap)
        if node == target:
            # Reconstruct path
            path = []
            cur: str | None = target
            while cur is not None:
                path.append(cur)
                cur = prev.get(cur)
            return list(reversed(path))

        if d > dist.get(node, float("inf")):
            continue

        for neighbor in adj.get(node, []):
            cost = d + node_cost(neighbor)
            if cost < dist.get(neighbor, float("inf")):
                dist[neighbor] = cost
                prev[neighbor] = node
                heapq.heappush(heap, (cost, neighbor))

    return []  # no path found


def find_path_for_board(
    board: dict,
    entry_gate_idx: int,
    exit_gate_idx: int | None,
    main_stat: str,
) -> dict:
    """
    Find the path through a board:
    1. Enter from entry gate
    2. Path to glyph socket
    3. Path to legendary node
    4. Path to exit gate (if not last board)

    Returns {activated_nodes: [ids], glyph_node: id, legendary_node: id,
             exit_gate: id, points_spent: int}
    """
    adj = build_adjacency(board)
    nodes_by_id = {n["id"]: n for n in board["nodes"]}

    # Find key nodes
    gates = [n for n in board["nodes"] if n["type"] == "gate"]
    glyph = next((n for n in board["nodes"] if n["type"] == "glyph"), None)
    legendary = next((n for n in board["nodes"] if n["rarity"] == "legendary"), None)

    if not gates or not glyph:
        return {"activated_nodes": [], "points_spent": 0}

    # Entry point: starting board enters from bottom center, others from a gate
    if entry_gate_idx == -1:
        # Starting board: find bottom center node
        max_row = max(n["gridRow"] for n in board["nodes"])
        center_col = max(n["gridCol"] for n in board["nodes"]) // 2
        entry = None
        for n in board["nodes"]:
            if n["gridRow"] == max_row and n["gridCol"] == center_col:
                entry = n
                break
        if not entry:
            # Fallback: any node at max row
            entry = next((n for n in board["nodes"] if n["gridRow"] == max_row), gates[0])
    else:
        entry = gates[entry_gate_idx % len(gates)]

    # Find glyph socket — weighted path from entry (prefers main stat nodes)
    path_to_glyph = weighted_path(adj, entry["id"], glyph["id"], nodes_by_id, main_stat)

    # Find legendary node — weighted path from glyph
    from_node = glyph["id"] if path_to_glyph else entry["id"]
    path_to_legend = []
    if legendary:
        path_to_legend = weighted_path(adj, from_node, legendary["id"], nodes_by_id, main_stat)

    # Find exit gate — weighted path from legendary (or glyph, or entry)
    path_to_exit = []
    exit_gate_node = None
    if exit_gate_idx is not None and len(gates) > 1:
        exit_gate_node = gates[exit_gate_idx % len(gates)]
        from_node2 = legendary["id"] if path_to_legend else (glyph["id"] if path_to_glyph else entry["id"])
        path_to_exit = weighted_path(adj, from_node2, exit_gate_node["id"], nodes_by_id, main_stat)

    # Combine all paths, deduplicate while preserving order
    all_nodes = []
    seen = set()
    for path in [path_to_glyph, path_to_legend, path_to_exit]:
        for node_id in path:
            if node_id not in seen:
                all_nodes.append(node_id)
                seen.add(node_id)

    # Count points spent (each non-gate node costs 1 point)
    points = sum(1 for nid in all_nodes if nodes_by_id.get(nid, {}).get("type") != "gate")

    # Stat breakdown along path
    stats = {"Str": 0, "Dex": 0, "Int": 0, "Will": 0}
    for nid in all_nodes:
        node = nodes_by_id.get(nid, {})
        nk = node.get("nodeKey", "")
        if nk.startswith("Generic_Normal_"):
            stat = nk.replace("Generic_Normal_", "")
            if stat in stats:
                stats[stat] += STAT_PER_NODE

    return {
        "activated_nodes": all_nodes,
        "glyph_node": glyph["id"] if glyph else None,
        "legendary_node": legendary["id"] if legendary else None,
        "exit_gate": exit_gate_node["id"] if exit_gate_node else None,
        "points_spent": points,
        "stats_gained": stats,
        "main_stat_gained": stats.get(main_stat, 0),
    }


def plan_paragon(
    cls: str,
    board_names: list[str],
    glyph_names: list[str],
) -> list[dict]:
    """
    Plan paragon paths for a build's 5 boards.
    Returns list of board path results.
    """
    all_boards = load_boards()
    main_stat = CLASS_MAIN_STAT.get(cls, "Int")

    # Map board display names to board IDs
    name_to_id = {}
    for bid, board in all_boards.items():
        name_to_id[board.get("name", "").lower()] = bid
        # Also map by the legendary node name
        for n in board["nodes"]:
            if n["rarity"] == "legendary":
                name_to_id[n["nodeKey"].split("_")[-1].lower()] = bid

    # Enforce max boards
    board_names = board_names[:MAX_BOARDS]
    glyph_names = glyph_names[:MAX_BOARDS]

    results = []
    total_points = 0
    for i, (board_name, glyph_name) in enumerate(zip(board_names, glyph_names)):
        # Find the board
        board_id = name_to_id.get(board_name.lower())
        if not board_id:
            # Try partial match
            for key, bid in name_to_id.items():
                if board_name.lower() in key or key in board_name.lower():
                    board_id = bid
                    break

        if not board_id or board_id not in all_boards:
            results.append({
                "board_name": board_name,
                "glyph": glyph_name,
                "activated_nodes": [],
                "points_spent": 0,
            })
            continue

        board = all_boards[board_id]

        # Entry: bottom center for starting board, top gate for subsequent
        entry_idx = -1 if i == 0 else 0  # -1 = bottom center (starting board)
        exit_idx = 0 if i < len(board_names) - 1 else None  # top gate to next board, None for last

        path = find_path_for_board(board, entry_idx, exit_idx, main_stat)

        # Enforce 300-point cap: trim activated nodes if total would exceed
        board_pts = path["points_spent"]
        if total_points + board_pts > MAX_PARAGON_POINTS:
            remaining = MAX_PARAGON_POINTS - total_points
            if remaining <= 0:
                # No points left, skip this board entirely
                results.append({
                    "board_id": board_id,
                    "board_name": board_name,
                    "glyph": glyph_name,
                    "activated_nodes": [],
                    "points_spent": 0,
                    "stats_gained": {"Str": 0, "Dex": 0, "Int": 0, "Will": 0},
                    "main_stat_gained": 0,
                    "capped": True,
                })
                continue
            # Trim: keep only the first `remaining` non-gate nodes
            nodes_by_id = {n["id"]: n for n in board["nodes"]}
            trimmed = []
            pts_used = 0
            for nid in path["activated_nodes"]:
                is_gate = nodes_by_id.get(nid, {}).get("type") == "gate"
                if is_gate:
                    trimmed.append(nid)
                elif pts_used < remaining:
                    trimmed.append(nid)
                    pts_used += 1
            path["activated_nodes"] = trimmed
            path["points_spent"] = pts_used
            # Recalculate stats for trimmed path
            stats = {"Str": 0, "Dex": 0, "Int": 0, "Will": 0}
            for nid in trimmed:
                node = nodes_by_id.get(nid, {})
                nk = node.get("nodeKey", "")
                if nk.startswith("Generic_Normal_"):
                    stat = nk.replace("Generic_Normal_", "")
                    if stat in stats:
                        stats[stat] += STAT_PER_NODE
            path["stats_gained"] = stats
            path["main_stat_gained"] = stats.get(main_stat, 0)
            path["capped"] = True

        total_points += path["points_spent"]

        results.append({
            "board_id": board_id,
            "board_name": board_name,
            "glyph": glyph_name,
            **path,
        })

    return results


if __name__ == "__main__":
    # Test with Necro Pit Push build's paragon
    boards = ["Blood Begets Blood", "Bone Graft", "Cult Leader", "Hulking Monstrosity", "Scent of Death"]
    glyphs = ["Revenge", "Sacrificial", "Corporeal", "Blood-drinker", "Control"]

    results = plan_paragon("Necromancer", boards, glyphs)
    total_pts = 0
    for r in results:
        pts = r.get("points_spent", 0)
        total_pts += pts
        nodes = len(r.get("activated_nodes", []))
        stats = r.get("stats_gained", {})
        main = r.get("main_stat_gained", 0)
        print(f"  {r['board_name']:25s} + {r['glyph']:15s}: {nodes:3d} nodes, {pts:2d} pts, Int={main} stats={stats}")

    print(f"\n  Total points: {total_pts}/300")
