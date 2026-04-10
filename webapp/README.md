# D4Builder Webapp

Fast, honest Diablo 4 build guides for returning seasonal players.

## Current Status

✅ **Project scaffolded** — Astro static site with mobile-first design  
✅ **Data validation** — 49/49 checks pass  
✅ **Build system** — Compiles to static HTML (36 pages generated)  
⏳ **Next**: Data integration from `/data/builds*.json` and skill tree rendering

## Stack

- **Framework**: Astro (static site generator)
- **Styling**: Vanilla CSS with design tokens (8px baseline, mobile-first)
- **JavaScript**: Minimal (Astro islands for interactive picker/compare drawer)
- **Hosting**: Cloudflare Pages

## Project Structure

```
webdev/
├── src/
│   ├── pages/           # Route pages (auto-generated from file structure)
│   │   ├── index.astro  # Home: 7 class tiles
│   │   ├── class/       # [class].astro: 4 builds per class
│   │   └── builds/      # [...id].astro: build detail pages
│   ├── components/      # Reusable Astro/React components
│   ├── layouts/         # Layout wrapper (Layout.astro)
│   └── styles/          # Global CSS + design tokens
├── public/              # Static assets
├── dist/                # Build output (gitignored)
├── astro.config.mjs     # Astro configuration
└── package.json
```

## Design Brief

- **Mobile-first**: 375px baseline; desktop is progressive enhancement
- **Picker model**: 7 class tiles → 4 builds → detail page
- **Compare drawer**: Secondary mode for side-by-side builds (not on home)
- **Data**: Static JSON files from `/data/builds_index.json`, `/data/builds/<id>.json`, `/data/skill_trees.json`
- **Accessibility**: WCAG AA

## Data Spec

See `../docs/BUILD_DATA_SPEC.md` for the full contract. Key files:

- `/data/builds_index.json` — Master index of all 28 builds
- `/data/builds/<id>.json` — Full build data (skills, gear, paragon, etc.)
- `/data/skill_trees.json` — Skill tree metadata per build

## Running Locally

```bash
npm install
npm run dev        # Start dev server (http://localhost:3000)
npm run build      # Build static site to dist/
npm run preview    # Preview production build locally
npm run lint       # Run ESLint (no warnings allowed)
```

## Next Steps

1. **Build data integration** — Load `/data/builds_index.json` and populate build cards with real data
2. **Skill tree component** — Render skills, upgrades, passives, key passive per build detail page
3. **Gear rendering** — Display unique and legendary items with aspect lookups
4. **Compare drawer** — Modal for side-by-side build comparison
5. **Lighthouse 95+** — Performance optimization (lazy load, optimize images)
6. **Sourcing badges** — Tappable stats to reveal provenance from `sources` field

## Known Data Notes

- 28 builds total: 7 classes × 4 purposes (pit, speed, leveling, mythic)
- Gear slots: Helm, Chest, Gloves, Boots, Legs, Ring, Amulet, Offhand, Weapon
- Legendary items use `(base_item_type, aspect_id)` pairs; uniques use `unique_id`
- Display names computed at render time, never stored in JSON
- Max 5 paragon boards per class (D4 hard limit)
- All numeric values sourced from verified data; no placeholders
