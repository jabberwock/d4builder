# d4-builder Data Spec — for @d4-web

All data lives in `workers/d4-builder/`. Paths below are relative to that directory.

## Webapp Static Files (served from `/data/`)

| Path | Purpose |
|------|---------|
| `/data/builds_index.json` | Master index — start here. Lists all 28 builds with summary fields. |
| `/data/builds/<id>.json` | Full build data (skills, gear, runewords, stat priority, math) |
| `/data/guides/<id>_guide.md` | Leveling guide (milestones 1/10/25/50/70/100, tempers, paragon path) |
| `/data/skill_trees.json` | Skill tree data by build — tiers, ranks, leveling order, upgrades |

## Source Files (workers/d4-builder/)

| File | Purpose |
|------|---------|
| `builds_index.json` | Source master index |
| `builds/<id>.json` | Source build data |
| `guides/<id>_guide.md` | Source leveling guides |
| `skill_trees.json` | Source skill tree data |

## builds_index.json

```json
{
  "version": "1.0",
  "season": "Season 12 - Season of Slaughter",
  "total_builds": 28,
  "classes": ["Barbarian", "Druid", "Necromancer", "Paladin", "Rogue", "Sorcerer", "Spiritborn"],
  "builds": [
    {
      "id": "rogue_phantom_rain",
      "build_name": "Phantom Rain",
      "class": "Rogue",
      "available": "live",       // "live" | "ptr"
      "season": "Season 12 - Season of Slaughter",
      "difficulty": "Torment",
      "playstyle_summary": "...",
      "stat_priority": [...],
      "file": "builds/rogue_phantom_rain.json",
      "guide": "guides/rogue_phantom_rain_guide.md"
    }
  ]
}
```

## builds/<id>.json top-level keys

```
build_name, class, available, season, difficulty,
playstyle_summary, math_justification,
skills: { basic, core, defensive, subterfuge/other, weapon_mastery, ultimate, key_passive },
gear: { weapon/mainhand/offhand, helm, chest, gloves, pants, boots, rings, amulet },
variants: [
  {
    stage: "leveling|early_legendary|full_legendary|legendary_uniques|mythic_uniques",
    stage_name: "human readable name",
    description: "progression description",
    gear: { ... },  // same structure as top-level gear
    gems_strategy: "optional gems advice for this stage"
  }
],
runewords,
seasonal_synergy,
gems_strategy,
stat_priority
```

## Build Variants

Each build now includes a `variants` array with 5 progression stages:

| Stage | Difficulty | Description |
|-------|-----------|-------------|
| `leveling` | Act 1-3 | Early game with rare/uncommon gear, basic aspects, focus on survivability |
| `early_legendary` | Act 4-5 | Mixed legendary + rare with accessible aspects, starting to scale |
| `full_legendary` | Torment 1-3 | All legendary slots, good tempers, optimized non-unique gear |
| `legendary_uniques` | Torment 4-5 | Key unique items (1-2) with legendary fill-ins, optimal aspects |
| `mythic_uniques` | Torment 6+ | Mythic-rarity uniques, perfect tempers, maximum endgame power |

Each variant includes its own `gear` object (replacing top-level gear for that stage) and optional `gems_strategy`.

## skill_trees.json structure

```json
{
  "metadata": { "version", "season", "generated_from", "description" },
  "builds": [
    {
      "id": "...",
      "build_name": "...",
      "class": "...",
      "available": "live",
      "key_passive": "...",
      "tiers": {
        "basic": [{ "name", "final_rank", "leveling_order", "upgrades": [] }],
        "core": [...],
        "defensive": [...],
        ...
      }
    }
  ]
}
```

## Classes (7 total)

Barbarian, Druid, Necromancer, Paladin, Rogue, Sorcerer, Spiritborn

## Builds per class: 4

28 builds total, all `available: "live"`, Season 12.
