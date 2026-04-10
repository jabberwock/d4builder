# d4builder

[![License](https://img.shields.io/badge/License-AGPL--3.0%20%2B%20Commons%20Clause-30363D?style=flat&labelColor=1e3a5f)](LICENSE)

A Diablo 4 build optimizer and webapp. Every number on the site is sourced from verified game data — no editorial opinion, no hand-authored content.

Season 12 (Season of Slaughter), patch 2.6.0.70982. 7 classes, 28 builds.

## Quick start

```bash
git clone https://github.com/jabberwock/d4builder.git
cd d4builder/webapp
pnpm install
pnpm run dev
```

Open http://localhost:4321.

## Project structure

```
d4builder/
  data/           # Frozen data pipeline + optimizer (read-only in normal use)
  webapp/         # Astro static site (the frontend)
  docs/           # Specs and design docs
  workers.yml     # Collab worker definitions (architect + webdev)
```

## Data pipeline

The `data/` directory contains the extraction scripts, verified databases, and the build optimizer. Data is frozen and verified — 49/49 regression tests pass. Do not modify unless you are deliberately updating the pipeline.

### Key files

| File | Contents |
|------|----------|
| `data/d4_stats.db` | 26-table SQLite: skills, damage, cooldowns, affixes, items, paragon, glyphs |
| `data/maxroll_data.json` | Maxroll game data dump (patch 2.6.0.70982) |
| `data/passive_effects_d4data.json` | 322 tagged passive effects |
| `data/optimizer_results.db` | Build optimizer output — 28 builds, 4 per class |

### Running the optimizer

```bash
chmod -R u+w data/
python data/optimizer_v2.py
python data/verify_data.py   # must be 49/49
chmod -R u-w data/
```

Tiers are per-class (ranked relative to that class's top score), not global.

### Refreshing maxroll data

```bash
data/fetch_maxroll.sh
```

### Verification

```bash
python data/verify_data.py
```

49 hand-curated fixtures. Also wired into pre-commit hook, GitHub Actions, and `make verify`.

## Webapp

Static Astro site consuming pre-baked JSON from the optimizer. Zero client JS by default. See `docs/BUILD_DATA_SPEC.md` for the data contract.

Astro + TypeScript (strict) + Tailwind. Cloudflare Pages. WCAG AA. Mobile-first, 375px baseline.

## License

See [LICENSE](LICENSE). Diablo IV assets are the property of Blizzard Entertainment. This is a fan project, not affiliated with or endorsed by Blizzard.
