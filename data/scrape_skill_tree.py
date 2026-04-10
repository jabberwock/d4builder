#!/usr/bin/env python3
"""
Scrape d4planner.io build guides for all classes.
Each page shows a full skill tree with CategoryIcon row dividers
and MajorSkillIcon/MinorSkillIcon skill nodes.

Extracts icon IDs from background-image URLs, maps to skill names
via Maxroll JSON, and assigns rows using CategoryIcon Y positions
as dividers + known thresholds (0, 1, 2, 11, 16, 23, 33).
"""

import json
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT = Path(__file__).parent / "skill_tree_positions.json"
MAXROLL_JSON = (lambda: __import__("_maxroll").MAXROLL_PATH)()

# One build guide per class on d4planner
CLASS_URLS = {
    "Barbarian":   "https://d4planner.io/builds/barbarian-hammer-of-the-ancients-endgame-build-guide",
    "Druid":       "https://d4planner.io/builds/lightning-storm-druid-guide",
    "Necromancer": "https://d4planner.io/builds/bone-spear-necromancer-guide",
    "Rogue":       "https://d4planner.io/builds/rapid-fire-rogue-guide",
    "Sorcerer":    "https://d4planner.io/builds/ball-lightning-sorcerer-guide",
    "Spiritborn":  "https://d4planner.io/builds/lightning-evade-spiritborn-guide",
    # No Paladin guide on d4planner yet
}

# Confirmed in-game thresholds per row
ROW_THRESHOLDS = [0, 1, 2, 11, 16, 23, 33]


def build_icon_to_skill():
    with open(MAXROLL_JSON) as f:
        mdata = json.load(f)
    skills = mdata.get("skills") or {}
    if isinstance(skills, list):
        skills = {str(i): s for i, s in enumerate(skills)}
    mapping = {}
    for sid, s in skills.items():
        name = s.get("name") or ""
        if not name:
            continue
        for icon_id in (s.get("icons") or []):
            if icon_id and icon_id != 0:
                mapping[str(icon_id)] = name
    return mapping


def scrape_class(page, cls_name, url, icon_to_skill):
    print(f"  [{cls_name}] Loading {url}...")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)

    # Dismiss cookie
    try:
        btn = page.query_selector("#onetrust-accept-btn-handler")
        if btn and btn.is_visible():
            btn.click()
            time.sleep(1)
    except Exception:
        pass

    # Extract all nodes with icon IDs and positions
    raw_nodes = page.evaluate("""() => {
        const tree = document.querySelector("[class^='Builds_miniSkillTreeContent']");
        if (!tree) return [];
        const nodes = tree.querySelectorAll("[class*='SkillNode_BaseNode']");
        const results = [];
        for (const node of nodes) {
            const top = parseFloat(node.style.top) || 0;
            const left = parseFloat(node.style.left) || 0;
            const classes = node.className || '';

            // Extract icon ID from background-image (computed style)
            let iconId = '';
            for (const child of [node, ...node.querySelectorAll('*')]) {
                const bg = window.getComputedStyle(child).backgroundImage || '';
                const m = bg.match(/(\\d{5,})\\.webp/);
                if (m) { iconId = m[1]; break; }
            }

            const isCategory = classes.includes('CategoryIcon');
            const isMajor = classes.includes('MajorSkillIcon');
            const isMinor = classes.includes('MinorSkillIcon');
            const isPassive = classes.includes('PassiveSkillIcon');
            const isKey = classes.includes('KeyPassive') || classes.includes('KeystoneIcon');

            results.push({
                top, left, iconId, isCategory, isMajor, isMinor, isPassive, isKey,
                classes: classes.substring(0, 100),
            });
        }
        return results;
    }""")

    print(f"  [{cls_name}] {len(raw_nodes)} nodes extracted")

    # Separate category icons (row headers) from skill nodes
    categories = sorted([n for n in raw_nodes if n["isCategory"]], key=lambda n: n["top"])
    skills = [n for n in raw_nodes if not n["isCategory"]]

    print(f"  [{cls_name}] {len(categories)} category dividers, {len(skills)} skill nodes")

    # Category icons define row boundaries
    # There should be 5-7 of them (one per row: Basic, Core, Row3, Row4, Row5, Ultimate, Key)
    # Map each to a threshold
    if len(categories) >= 5:
        # Sort by Y, assign thresholds in order
        for i, cat in enumerate(categories):
            threshold = ROW_THRESHOLDS[i] if i < len(ROW_THRESHOLDS) else 33
            cat["threshold"] = threshold
            name = icon_to_skill.get(cat["iconId"], f"Row{i+1}")
            print(f"    Category {i+1}: Y={cat['top']:.0f} pts={threshold} icon={cat['iconId']} name={name}")
    else:
        print(f"    WARNING: expected 5+ categories, got {len(categories)}")

    # Assign each skill to a row based on Y position
    results = {}
    for n in skills:
        icon_id = n.get("iconId", "")
        name = icon_to_skill.get(icon_id)
        if not name:
            continue

        y = n["top"]

        # Find which category this node falls under
        pts = 0
        for cat in categories:
            if y >= cat["top"] - 30:  # tolerance for nodes slightly above header
                pts = cat.get("threshold", 0)

        # Key passives override
        if n.get("isKey"):
            pts = 33

        key = name.lower()
        # Don't overwrite if already found at a different position
        # (some skills appear multiple times as enhanced/morph variants)
        if key not in results:
            results[key] = {
                "name": name,
                "y": round(y),
                "points_required": pts,
                "icon_id": icon_id,
            }

    print(f"  [{cls_name}] {len(results)} unique skills mapped")
    return results


def main():
    icon_to_skill = build_icon_to_skill()
    print(f"Icon mapping: {len(icon_to_skill)} icons\n")

    all_results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        for cls_name, url in CLASS_URLS.items():
            try:
                results = scrape_class(page, cls_name, url, icon_to_skill)
                all_results[cls_name] = results
                print()
            except Exception as e:
                print(f"  [{cls_name}] ERROR: {e}\n")
                all_results[cls_name] = {}

        browser.close()

    with open(OUTPUT, "w") as f:
        json.dump(all_results, f, indent=2, sort_keys=True)

    print(f"\nWrote {OUTPUT}")
    for cls, skills in all_results.items():
        print(f"  {cls}: {len(skills)} skills")


if __name__ == "__main__":
    main()
