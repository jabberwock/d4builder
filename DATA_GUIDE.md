# D4Builder Data Guide

Comprehensive reference for all data sources, schemas, relationships, and architecture in the d4builder project. This document is designed so any future session can understand where data lives and how to consume it without rediscovery.

---

## Local Data Files

All data files live under `/Users/michael/code/d4builder/data/`.

### d4_stats.db (SQLite, ~2.8 MB)

The canonical source-of-truth database for all Diablo 4 game data. Read by `optimizer.py` and `server.js`.

#### Table: `skills` (2,131 rows)

| Column | Type | Description |
|--------|------|-------------|
| `power_name` | TEXT PK | Internal identifier, e.g. `Sorcerer_Fireball`, `Barbarian_Whirlwind` |
| `display_name` | TEXT | Human-readable name, e.g. `Fireball`, `Whirlwind` |
| `class` | TEXT | One of: `Barbarian`, `Druid`, `Necromancer`, `Paladin`, `Rogue`, `Sorcerer`, `Spiritborn`, `Generic` |
| `is_passive` | INTEGER | 0 = active skill, 1 = passive |
| `skill_tags` | TEXT | Comma-separated tags: `Skill_Fire`, `Keyword_Vulnerable`, `Skill_Cutthroat`, etc. |
| `primary_tag` | TEXT | Tier identifier: `Skill_Primary_Basic`, `Skill_Primary_Core`, `Skill_Primary_Ultimate`, etc. |
| `description` | TEXT | Raw Blizzard-formatted description with `{payload:...}` placeholders |

Active vs passive counts per class (excluding 1,315 Generic rows):
- Barbarian: 36 active, 63 passive
- Druid: 53 active, 74 passive
- Necromancer: 66 active, 75 passive
- Paladin: 41 active, 74 passive
- Rogue: 37 active, 57 passive
- Sorcerer: 41 active, 71 passive
- Spiritborn: 49 active, 79 passive

#### Table: `skill_damage` (917 rows)

| Column | Type | Description |
|--------|------|-------------|
| `power_name` | TEXT | FK to `skills.power_name` |
| `rank` | INTEGER | Skill rank (1-7 typical) |
| `damage_pct` | REAL | Damage percentage at that rank |

PK is `(power_name, rank)`. The optimizer uses rank 7 if available, else rank 5.

Skills with damage data by class: Barbarian 15, Druid 18, Necromancer 10, Paladin 21, Rogue 16, Sorcerer 17, Spiritborn 31.

**Known gap:** Warlock has zero entries (class launches Apr 28, 2026). Some skills like Barbarian Upheaval and Death Blow have `tags=None` in the Maxroll source, leading to missing tag associations.

#### Table: `skill_cooldowns` (206 rows)

| Column | Type | Description |
|--------|------|-------------|
| `power_name` | TEXT PK | FK to `skills.power_name` |
| `cooldown_table_id` | INTEGER | Reference to `skill_ranks` table |
| `cooldown_base` | REAL | Base cooldown in seconds (NULL/0 means no cooldown) |
| `cooldown_formula` | TEXT | Formula string for dynamic cooldowns |

#### Table: `skill_rank_tables` (229 rows)

| Column | Type | Description |
|--------|------|-------------|
| `power_name` | TEXT | FK to `skills.power_name` |
| `table_id` | TEXT | Identifies which rank table this maps to |
| `formula_context` | TEXT | Context for how the table is used |

#### Table: `skill_ranks` (2,812 rows)

| Column | Type | Description |
|--------|------|-------------|
| `table_id` | INTEGER | Groups ranks into tables |
| `rank` | INTEGER | Rank level |
| `value` | REAL | Scaled value at that rank |

#### Table: `skill_payloads` (632 rows)

| Column | Type | Description |
|--------|------|-------------|
| `power_name` | TEXT | FK to `skills.power_name` |
| `payload_index` | INTEGER | Position in the description's `{payload:N}` placeholder |
| `scalar_formula` | TEXT | Formula for computing the actual value |

#### Table: `affixes` (4,347 rows)

| Column | Type | Description |
|--------|------|-------------|
| `internal_name` | TEXT PK | e.g. `legendary_barb_026`, `Resource_MaxMana`, `CooldownReductionCDR` |
| `display_name` | TEXT | Human-readable name |
| `class` | TEXT | Class restriction (NULL = all classes) |
| `max_value` | REAL | Maximum possible roll value |
| `linked_skill` | TEXT | Skill this affix is linked to (if any) |
| `affix_category` | TEXT | Category grouping |

Legendary aspects use the prefix pattern `legendary_<class>_<number>`, e.g.:
- `legendary_barb_001` through `legendary_barb_NNN`
- `legendary_sorc_115`, `legendary_druid_071`, `legendary_necro_114`
- `legendary_generic_109` for class-agnostic aspects

#### Table: `items` (8,902 rows)

| Column | Type | Description |
|--------|------|-------------|
| `item_name` | TEXT PK | Internal item identifier |
| `display_name` | TEXT | Human-readable name |
| `item_type` | TEXT | `Helm`, `ChestArmor`, `Gloves`, `Boots`, `Legs`, `Ring`, `Amulet`, `Axe`, `Sword2H`, `Dagger`, `Focus`, `Shield`, etc. |
| `magic_type` | TEXT | `unique`, `legendary`, `rare`, etc. |
| `usable_by_class` | TEXT | Comma-separated class names, or empty for all-class items |
| `affixes` | TEXT | JSON array of affix `internal_name` strings |

The optimizer filters to `magic_type = 'unique'` items only, excluding placeholders (`[PH]`, `(PH)`, `test`, `MeganS`).

#### Table: `paragon_boards` (69 rows)

| Column | Type | Description |
|--------|------|-------------|
| `board_name` | TEXT PK | Internal board identifier |
| `class` | TEXT | Owning class |
| `display_name` | TEXT | Human-readable name |
| `node_count` | INTEGER | Number of nodes on this board |
| `socket_positions` | TEXT | JSON positions for glyph sockets |

Boards per class: Barbarian 10, Druid 10, Necromancer 10, Paladin 10, Rogue 10, Sorcerer 10, Spiritborn 9. **CRITICAL:** D4 hardcodes max 5 equipped boards per class = 35 max total in any build.

#### Table: `paragon_nodes` (493 rows)

| Column | Type | Description |
|--------|------|-------------|
| `node_name` | TEXT PK | Internal node identifier |
| `node_type` | TEXT | `normal`, `magic`, `rare`, `legendary`, `glyph_socket` |
| `display_name` | TEXT | Human-readable name |
| `bonus_value` | REAL | Stat bonus amount |
| `bonus_type` | TEXT | What stat it boosts |
| `skill_tags` | TEXT | Tags linking the node to skill archetypes |

#### Table: `paragon_glyphs` (137 rows)

| Column | Type | Description |
|--------|------|-------------|
| `glyph_name` | TEXT PK | Internal glyph identifier |
| `display_name` | TEXT | Human-readable name |
| `rarity` | TEXT | Glyph rarity tier |
| `usable_by_class` | TEXT | Class restriction |
| `bonus_per_point` | REAL | Scaling bonus per attribute point in radius |
| `radius_bonus` | REAL | Bonus at radius threshold |
| `max_rank` | INTEGER | Maximum glyph rank |

#### Table: `runes` (62 rows)

| Column | Type | Description |
|--------|------|-------------|
| `rune_name` | TEXT PK | Internal rune identifier |
| `display_name` | TEXT | e.g. `Tec`, `Kry`, `Ohm`, `Jah` |
| `rune_type` | TEXT | `ritual` or `invocation` |
| `effect_description` | TEXT | What the rune does |
| `runic_amount` | REAL | Base runic power |
| `rarity` | TEXT | Rune rarity |
| `offering_gain` | REAL | How much offering a ritual generates |
| `offering_cost` | REAL | How much offering an invocation consumes |

#### Table: `specializations` (61 rows)

| Column | Type | Description |
|--------|------|-------------|
| `class` | TEXT | Owning class |
| `specialization_name` | TEXT | e.g. `weapon_expertise`, `combo_points`, `enchantment_slots` |
| `description` | TEXT | What the specialization does |
| `mechanic_type` | TEXT | Mechanic category |
| `required_skill_tags` | TEXT | Tags a skill must have to qualify |
| `generator_tags` | TEXT | Tags for generator skills |
| `spender_tags` | TEXT | Tags for spender skills |

#### Table: `tempering_recipes` (180 rows)

| Column | Type | Description |
|--------|------|-------------|
| `recipe_name` | TEXT PK | Internal recipe identifier |
| `display_name` | TEXT | Human-readable name |
| `class` | TEXT | Class restriction (NULL = generic) |
| `item_type` | TEXT | Which gear slot(s) this recipe applies to |
| `affix_key` | TEXT | JSON array of affix key strings, e.g. `["Tempered_Size_Skill_Barb_Whirlwind_Tier3"]` |
| `max_value` | REAL | Maximum temper roll value |
| `category` | TEXT | `Offensive`, `Defensive`, `Weapons`, `Mobility` |

#### Table: `gems` (15 rows)

| Column | Type | Description |
|--------|------|-------------|
| `gem_name` | TEXT PK | Internal gem identifier |
| `display_name` | TEXT | e.g. `Royal Emerald`, `Royal Ruby` |
| `gem_type` | TEXT | `ruby`, `emerald`, `skull`, `diamond`, etc. |
| `quality` | TEXT | `royal`, `grand`, etc. |
| `weapon_bonus` | TEXT | Bonus when socketed in weapon |
| `armor_bonus` | TEXT | Bonus when socketed in armor |
| `jewelry_bonus` | TEXT | Bonus when socketed in jewelry |

---

### optimizer_results.db (SQLite, ~180 KB)

Output database written by `optimizer.py`, read by `server.js`. Currently contains **36 rows** across 7 classes (no Warlock).

#### Table: `optimizer_results`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `class` | TEXT | Class name |
| `specialization` | TEXT | Spec name |
| `rank` | INTEGER | Rank within spec (1 = best) |
| `build_score` | REAL | Final composite score |
| `skill_bar` | TEXT | JSON array of 6 display names: `["Bash", "Whirlwind", ...]` |
| `skill_upgrades` | TEXT | JSON array: `[{"name": "Bash", "enhanced": "Enhanced Bash", "morph": "Combat Bash"}, ...]` |
| `passives` | TEXT | JSON array of passive display names |
| `key_passive` | TEXT | Single key passive display name |
| `aspects` | TEXT | JSON array (legacy, usually empty) |
| `tempers` | TEXT | JSON array (legacy, usually empty) |
| `rune_pair_1` | TEXT | JSON: `{"ritual": "Cem", "invocation": "Tec", "score": 1.234}` |
| `rune_pair_2` | TEXT | JSON: same format or null |
| `score_breakdown` | TEXT | JSON: `{"Whirlwind": 45.123, "Bash": 12.456, ...}` per-skill scores |
| `aspects_recommended` | TEXT | JSON array: `[{"slot": "Helm", "aspect_name": "...", "max_value": 50.0}, ...]` |
| `tempers_recommended` | TEXT | JSON: `{"offensive": "...", "defensive": "...", "weapon": "...", "mobility": "..."}` |
| `gems_recommended` | TEXT | JSON: `{"weapon": "Royal Ruby", "armor": "Royal Ruby", "jewelry": "Royal Skull"}` |
| `gear_recommended` | TEXT | JSON: `{"Helm": "Crown of Lucion", "Weapon": "...", ...}` |
| `mercenary` | TEXT | JSON: `{"primary": "Raheir", "reason": "..."}` |
| `specialization_detail` | TEXT | JSON: `{"name": "...", "mechanic_type": "..."}` |
| `nightmare_dungeons` | TEXT | JSON array: `[{"name": "Mournfield", "zone": "Dry Steppes", "region": "Khargai Crags", "aspect": "...", "x": -954.4, "y": -669.1}, ...]` |

---

### skill_morphs.json (~21 KB)

Maps base skill names to their Enhanced + Morph upgrade options. Sourced from Fextralife wiki scraping.

```json
{
  "Fireball": {
    "enhanced": "Enhanced Fireball",
    "morph_1": "Destructive Fireball",
    "morph_2": "Greater Fireball"
  },
  "Blessed Hammer": {
    "enhanced": "Enhanced Blessed Hammer",
    "morph_1": "Shattering Blow",
    "morph_2": "Disciple's Halo",
    "morph_3": "Mortar Combat"
  }
}
```

- Most skills have 2 morphs (`morph_1`, `morph_2`)
- Paladin and Spiritborn skills often have 3 morphs (`morph_1`, `morph_2`, `morph_3`)
- ~155 base skills covered
- The optimizer picks the morph with the highest combined aspect_bonus + temper_bonus

---

### nightmare_dungeons.json (~45 KB)

135 nightmare dungeons with d4planner.io map coordinates.

```json
{
  "total": 135,
  "dungeons": [
    {
      "id": "Dungeon|DGN_Step_Mournfield|500380",
      "name": "Mournfield",
      "aspect": "legendary_barb_001",
      "description": "What walks here is living and dead...",
      "region": "Khargai Crags",
      "zone": "Dry Steppes",
      "x": -954.4340209960938,
      "y": -669.0989990234375
    }
  ]
}
```

- `aspect` field uses the same `legendary_<class>_<number>` internal_name format as `affixes.internal_name` in d4_stats.db
- `x`/`y` are d4planner.io world coordinates for map rendering
- The optimizer uses this to recommend dungeons where build-relevant aspects drop

---

### /tmp/maxroll_data.json (~9 MB, external download)

Full Diablo 4 game data from Maxroll, patch 2.6.0.70982. Downloaded from:
`https://assets-ng.maxroll.gg/d4-tools/game/data.min.json?e7989b2b`

#### Top-level keys (34 total):

`version`, `affixes`, `items`, `classes`, `skills`, `skillTrees`, `uiStrings`, `heroDetails`, `stones`, `vampiricPowers`, `remap`, `mercenaries`, `paragonBoards`, `paragonGlyphAffixes`, `paragonGlyphs`, `paragonNodes`, `paragonThresholds`, `skillTags`, `skillCategories`, `powerTables`, `itemRequirements`, `attributeDescriptions`, `attributeFormulas`, `itemTypes`, `levelScaling`, `rareNames`, `attributes`, `worldTiers`, `oldAffixes`, `temperingRecipes`, `temperingGroups`, `buffItems`, `vampiricPowerMap`, `icons`

#### Affix structure

Each affix is keyed by its `internal_name` string:

```json
{
  "Resource_MaxMana": {
    "id": 577013,
    "magicType": 0,
    "category": 0,
    "affixType": 2,
    "tags": [],
    "classFilter": [true, false, false, false, false, false, false, false],
    "itemLabels": [16],
    "maximumRank": [8, 12, 16, 16, 0],
    "attributes": [
      {"id": 159, "param": 0, "formula": "AffixFlatResource1x"}
    ],
    "qualityMask": 63,
    "flags": 1155,
    "weight": 100,
    "prefix": "Originating",
    "suffix": "of Origination"
  }
}
```

- `tags`: Array of strings. All tags are strings (zero integer tags found). Includes `FILTER_Flex_*` and `FILTER_Build_*` tags on legendary affixes.
- `classFilter`: Boolean array indexed by class: `[Sorcerer, Barbarian, Druid, Necromancer, Rogue, Spiritborn, Paladin, Warlock]`
- `maximumRank`: Array of 5 values corresponding to world tier scaling
- `attributes[].formula`: References `attributeFormulas` for computing actual values

#### FILTER_Flex_ tags (aspect-to-skill linking)

228 total FILTER_Flex_ tags across legendary affixes. These link legendary aspects to the specific skills they enhance.

Format: `FILTER_Flex_<ClassAbbrev>_<SkillNameCamelCase>`

Examples:
- `FILTER_Flex_Barb_Leap` on `legendary_barb_026`
- `FILTER_Flex_Barb_Kick` on `legendary_barb_018`
- `FILTER_Flex_Rogue_ShadowStep` on a Rogue legendary

Distribution by class:
- Barb: 41 tags
- Rogue: 78 tags
- Druid: 44 tags
- Sorc: 29 tags
- Necro: 36 tags
- **Paladin: 0 tags** (data gap)
- **Spiritborn: 0 tags** (data gap)
- **Warlock: 0 tags** (class not released)

#### FILTER_Build_ tags

359 total tags. Link aspects to broader build archetypes rather than specific skills.

Format: `FILTER_Build_<ClassAbbrev>_<Archetype>`

Examples:
- `FILTER_Build_Barb_RequiresBerserk`
- `FILTER_Build_All_Generic`
- `FILTER_Build_Druid_RequiresCompanion`

Distribution: Barb 45, All/Generic 72, Druid 91, Sorc 47, Rogue 37, Necro 67.

#### skillTags (441 entries)

Keyword and tag definitions with descriptions. Format:

```json
{
  "Keyword_Vulnerable": {
    "name": "Vulnerable",
    "desc": "Vulnerable enemies take 20% increased damage.",
    "types": 6
  }
}
```

---

## Key Data Relationships

### skill_tags -> Archetypes

The `skill_tags` column in `skills` links every skill to its damage school and mechanic keywords. The optimizer uses these for synergy scoring.

**School/Archetype tags** (used by the optimizer's `SCHOOL_TAGS`):
- Sorcerer elements: `Skill_Fire`, `Skill_Lightning`, `Skill_Cold`
- Necromancer schools: `Skill_Blood`, `Skill_Bone`, `Skill_Corruption`
- Rogue archetypes: `Skill_Marksman`, `Skill_Cutthroat`, `Skill_Trap`, `Skill_Shadow`
- Barbarian physical: `Skill_Bludgeoning`, `Skill_Channeled`, `Skill_Bleeding`, `Skill_Slam`
- General: `Skill_Physical`, `Skill_Summon`

Example: `Fireball` has `skill_tags = "Skill_Fire"`, `Flame Shield` has `"Skill_Fire,Keyword_Immune"`.

### Maxroll FILTER_Flex_ -> Skill-Aspect Links

The optimizer builds an aspect index from Maxroll JSON at startup (`_build_maxroll_aspect_index()`). It:
1. Iterates all `legendary_*` keyed affixes
2. Extracts `FILTER_Flex_<Class>_<SkillName>` from each affix's `tags` array
3. Converts CamelCase skill name to lowercase words
4. Maps `internal_name` -> list of skill name words

This lets the optimizer match aspects to skills without relying on substring matching of display names.

### nightmare_dungeons.json -> d4_stats.db affixes

The `aspect` field in each dungeon entry (e.g. `"legendary_barb_001"`) maps directly to `affixes.internal_name` in d4_stats.db. The optimizer:
1. Builds a lookup: `aspect_internal_name` -> dungeon entry
2. When recommending aspects for a build, looks up each recommended aspect's `internal_name` in the dungeon index
3. Returns the dungeon name, zone, region, and x/y coordinates

### primary_tag -> Skill Tree Tiers

The `primary_tag` column determines which row of the skill tree a skill belongs to. The full mapping used in `server.js`:

| primary_tag | Tier Name | Classes |
|-------------|-----------|---------|
| `Skill_Primary_Basic` | basic | All |
| `Skill_Primary_Core` | core | All |
| `Skill_Primary_Defensive` | defensive | Barb, Sorc |
| `Skill_Primary_Brawling` | brawling | Barb |
| `Skill_Primary_Weapon_Mastery` | weapon_mastery | Barb |
| `Skill_Primary_Macabre` | macabre | Necro |
| `Skill_Primary_Corpse` | corpse | Necro |
| `Skill_Primary_Curse` | curse | Necro |
| `Skill_Primary_Summoning` | summoning | Necro |
| `Skill_Primary_Companion` | companion | Druid |
| `Skill_Primary_Wrath` | wrath | Druid |
| `Skill_Primary_Conjuration` | conjuration | Sorc |
| `Skill_Primary_Mastery` | mastery | Sorc |
| `Skill_Primary_Agility` | agility | Rogue |
| `Skill_Primary_Subterfuge` | trap | Rogue |
| `Skill_Primary_Imbuements` | imbuement | Rogue |
| `Skill_Primary_Justice` | justice | Paladin |
| `Skill_Primary_Valor` | valor | Paladin |
| `Skill_Primary_Aura` | aura | Paladin |
| `Skill_Primary_Archfiend` | archfiend | Warlock |
| `Skill_Primary_Soul_Shard` | soul_shard | Warlock |
| `Skill_Primary_Potency` | potency | Warlock |
| `Skill_Primary_Sigil` | sigil | Warlock |
| `Skill_Primary_Focus` | focus | Warlock |
| `Skill_Primary_Offense` | offense | Generic |
| `Skill_Primary_Minion` | minion | Generic |
| `Skill_Primary_Ultimate` | ultimate | All |

---

## D4 Skill Tree Structure

### Tier Unlock Thresholds

The number of total skill points that must be spent before unlocking each tier:

| Row | Tier | Points Required | Examples |
|-----|------|-----------------|----------|
| 1 | Basic (generators) | 0 | Bash, Fire Bolt, Bone Splinters |
| 2 | Core (main damage) | 1 | Whirlwind, Fireball, Bone Spear |
| 3 | Utility / secondary | 2 | Defensive, Brawling, Macabre, Corpse, Companion, Agility, Valor, Justice, Focus, Potency |
| 4 | Advanced | 11 | Conjuration, Weapon Mastery, Imbuement, Summoning, Wrath, Aura |
| 5 | Mastery | 16 | Mastery tier skills |
| 6 | Ultimate | 23 | Class ultimates (1 point max) |
| 7 | Key Passive | 33 | One per build, no rank |

### Rank Limits

- **Active skills** (Basic, Core, Row 3-5): max rank **5** (can be boosted to 7+ by gear)
- **Ultimate skills**: max rank **1**
- **Passives**: max rank **3**
- **Total available skill points**: ~58 (49 from leveling + up to 10 from renown)
- The optimizer's `buildSkillOrder()` allocates in phases: 1 point each -> fill cores -> fill mid-tier -> fill passives

### Key Passive Detection

Key passives are identified by:
1. `"Key Passive"` substring in `skill_tags`
2. `_T5_` in `power_name` (most classes)
3. `_T3_` in `power_name` for Sorcerer specifically

---

## Optimizer Architecture

**File:** `/Users/michael/code/d4builder/data/optimizer.py`

### Input

| Source | Purpose |
|--------|---------|
| `d4_stats.db` | Skills, damage values, cooldowns, affixes, items, tempers, gems, runes, specializations |
| `skill_morphs.json` | Enhanced/Morph upgrade names for each base skill |
| `/tmp/maxroll_data.json` | FILTER_Flex_ tag index for aspect-to-skill linking |
| `nightmare_dungeons.json` | Dungeon-to-aspect mapping with map coordinates |

### Output

Writes to `optimizer_results.db`, dropping and recreating the `optimizer_results` table each run. Currently produces 36 rows (builds) across 7 classes.

### Scoring Model

```
base_skill_score = damage_pct@rank7 / max(cooldown_base, 1.0)
aspect_multiplier = 1.0 + sum(max_value/100 for linked affixes)
temper_multiplier = 1.0 + sum(max_value/100 for matching tempers)
skill_score = base_skill_score * aspect_multiplier * temper_multiplier
```

For the full build:
```
synergy_multiplier = 1.0 + (SYNERGY_PER_TAG * overlapping_school_tags_with_primary)
  where SYNERGY_PER_TAG = 2.0
final_skill_score = skill_score * synergy_multiplier * spec_multiplier
build_score = sum(final_skill_scores) + rune_bonus + enchant_bonus
```

Rune scoring: `proc_rate = offering_gain / offering_cost`, then `score = proc_rate * invocation_value`. Top 2 non-overlapping runeword pairs selected.

### Build Generation Strategy

The optimizer is **archetype-driven**: one build per Core skill per specialization.

For each Core skill (the "anchor"):
1. Extract its school tags (`Skill_Fire`, `Skill_Blood`, etc.)
2. Pick 1 Basic that matches the school (or best generic)
3. Pick 1 Ultimate that matches
4. Fill remaining 3 slots from other tiers, preferring school matches
5. Optionally add a 2nd Core skill from the same school
6. Score the 6-skill combo with synergy bonuses

Per-spec: top 5 builds kept (`TOP_N_PER_SPEC = 5`).

### Specialization Handling

One representative spec per `mechanic_type` per class:

| Class | Specializations Run |
|-------|-------------------|
| Barbarian | `weapon_expertise` |
| Druid | `spirit_boon` |
| Sorcerer | `enchantment_slots` (gets +15% enchant bonus) |
| Rogue | `combo_points` (Core skills get 1.39x), `inner_sight`, `preparation` |
| Necromancer | `book_of_the_dead` |
| Spiritborn | `guardian_spirit` |
| Paladin | `oaths` |
| Warlock | `soul_shards` |

### Recommendation Features

The optimizer generates 7 recommendation types per build:

1. **Aspects** (`select_aspects`): Uses Maxroll FILTER_Flex_ tag index, falls back to display_name substring match. Up to 6 aspects, assigned to slots: Helm, Chest, Gloves, Boots, Legs, Ring1, Ring2, Amulet, Weapon.

2. **Nightmare Dungeons** (`select_nightmare_dungeons`): Reverse-lookup from recommended aspect display names -> internal_names -> dungeon index. Up to 5 dungeons with coordinates.

3. **Tempers** (`select_tempers`): One per category (Offensive, Defensive, Weapons, Mobility). Matches affix_key JSON fragments against skill names.

4. **Gems** (`select_gems`): Deterministic by class. Barbarian gets Ruby in weapon (Overpower), others get Emerald (Vulnerable). Ruby in armor, Skull in jewelry.

5. **Gear** (`select_gear`): Unique items scored by how many of their affixes mention active skill names. Best per slot.

6. **Mercenary** (`select_mercenary`): Tag-based scoring. Aldkin for Vulnerable builds, Varyana for Barbarian, Subo for Rogue, Raheir as universal default.

7. **Passives** (`select_passives`): Scored by tag overlap with active skills. Top 10 regular passives + 1 key passive.

---

## API Architecture

**File:** `/Users/michael/code/d4builder/webapp/api/server.js`

### Databases

Both opened as **readonly** at startup:
- `optimizer_results.db` -> `optDb`
- `d4_stats.db` -> `statsDb`

### Startup Processing

At import time, `server.js` builds three in-memory lookup maps from `d4_stats.db`:

1. **`skillTierLookup`**: `lowercase_display_name` -> tier string (e.g. `"core"`, `"basic"`, `"ultimate"`)
2. **`skillTagsLookup`**: `lowercase_display_name` -> array of clean tag names (e.g. `["Fire"]`, `["Cutthroat", "Shadow"]`)
3. **`skillDescLookup`**: `lowercase_display_name` -> cleaned description (Blizzard markup stripped)

Description cleaning (`cleanDesc`) strips: `{if:}`, `{icon:}`, `{c_*}`, `{payload:*}`, `{buffduration:*}`, `{resource cost}`, `{combat effect chance}`, and bracket markup.

### Routes

#### `GET /api/builds`

Returns all builds as summaries, sorted by `rank ASC, build_score DESC`.

Response:
```json
{
  "version": "2.0",
  "season": "Season 12",
  "total_builds": 36,
  "classes": ["Barbarian", "Druid", ...],
  "builds": [
    {
      "id": "barbarian_weapon_expertise_1",
      "uuid": "barbarian_weapon_expertise_1",
      "build_name": "Barbarian: Whirlwind + Bash (weapon_expertise)",
      "class": "Barbarian",
      "specialization": "weapon_expertise",
      "season_rank": 5,
      "efficiency_score": 123.456,
      "tier": "A",
      "available": "Endgame",
      "season": "Season 12",
      "difficulty": "Torment IV",
      "playstyle_summary": "Barbarian build using Whirlwind + Bash (weapon_expertise).",
      "stat_priority": [],
      "file": null,
      "guide": null
    }
  ]
}
```

Tier assignment (global rank percentile): S = top 10%, A = top 30%, B = top 60%, C = rest.

Build slug format: `{class}_{specialization}_{rank}`, lowercased, non-alphanumeric replaced with `_`.

Build name: `{class}: {top2_skills_by_score} ({specialization})`.

#### `GET /api/builds/:id`

Accepts numeric `id` or slug string. Returns full build detail including:
- `skills` object grouped by tier, with `rank`, `enhanced`, `morph`, `tags`, `description` per skill
- `skill_order` array (58-entry leveling allocation sequence)
- `gear`, `runewords`, `score_breakdown`, `aspects_recommended`, `tempers_recommended`, `gems_recommended`, `mercenary`, `specialization_detail`, `nightmare_dungeons`

The `skill_order` is computed by `buildSkillOrder()`: allocates 1 point to each skill in unlock-threshold order, then greedily fills highest-scoring non-maxed skills.

### Port

The API listens on port **3001** (`const PORT = 3001`).

---

## External Data Sources

### Already Downloaded Locally

| Source | Local Path | Notes |
|--------|-----------|-------|
| Maxroll game data JSON | `/tmp/maxroll_data.json` | 9 MB, patch 2.6.0.70982 |
| Skill morphs (scraped from Fextralife) | `data/skill_morphs.json` | 155 base skills |

### Reference URLs

**Maxroll:**
- Game data: `https://assets-ng.maxroll.gg/d4-tools/game/data.min.json?e7989b2b`
- D4 Planner: `https://maxroll.gg/d4/planner/`

**Fextralife Wiki (class skills):**
- Barbarian: `https://diablo4.wiki.fextralife.com/Barbarian+Skills`
- Druid: `https://diablo4.wiki.fextralife.com/Druid+Skills`
- Necromancer: `https://diablo4.wiki.fextralife.com/Necromancer+Skills`
- Paladin: `https://diablo4.wiki.fextralife.com/Paladin+Skills`
- Rogue: `https://diablo4.wiki.fextralife.com/Rogue+Skills`
- Sorcerer: `https://diablo4.wiki.fextralife.com/Sorceress+Skills`
- Spiritborn: `https://diablo4.wiki.fextralife.com/Spiritborn+Skills`
- Warlock: `https://diablo4.wiki.fextralife.com/Warlock+Skills` (not yet published, launches Apr 28 2026)

**Wowhead (legendary aspects per class):**
- `https://www.wowhead.com/diablo-4/guide/classes/{class}/legendary-aspects` for each of: barbarian, druid, necromancer, paladin, rogue, sorcerer, spiritborn

---

## Known Data Gaps

### Missing FILTER_Flex_ Tags
- **Paladin** and **Spiritborn** have zero `FILTER_Flex_` tags in the Maxroll JSON. The optimizer falls back to display_name substring matching for these classes, which is less accurate.

### Warlock (Unreleased)
- Warlock class launches April 28, 2026 (Lord of Hatred expansion)
- `d4_stats.db` has Warlock skill entries with `primary_tag` values (`Skill_Primary_Archfiend`, `Skill_Primary_Soul_Shard`, `Skill_Primary_Potency`, `Skill_Primary_Sigil`, `Skill_Primary_Focus`) but **zero `skill_damage` rows**
- Maxroll JSON has no Warlock `FILTER_Flex_` or `FILTER_Build_` tags
- `skill_morphs.json` has no Warlock entries (Fextralife page not published)

### Skill Data Issues
- Some skills have `skill_tags = None` (e.g. Barbarian Upheaval, Death Blow), breaking tag-based synergy scoring
- Skill descriptions contain Blizzard markup with `{payload:...}` placeholders instead of actual numeric values. The `cleanDesc()` function replaces these with `#` symbols.
- The `skill_payloads` table has the formulas to compute actual values, but no runtime evaluation is implemented

### Other Gaps
- `class_mechanic` field is empty across all specialization rows
- Aspect dungeon sources in `nightmare_dungeons.json` only cover 135 of the total legendary aspects
- Mercenary data is hardcoded in `optimizer.py` (`MERCENARY_SYNERGIES` dict), not sourced from the database
- Paladin legendary aspects are unverified against official sources
- Paragon board data has 10 boards per class in the DB but only 5 can be equipped per build (hardcoded game limit)

---

## Known Non-Gaps (Investigated, Not Closeable)

These look like data gaps in audit queries but are confirmed not real issues.
**Don't re-open these.** Each was traced to its source and ruled out.

### 166 unique items missing affix data (`items` table, `magic_type='unique'`, empty `affixes`)

Audit query that surfaces them:
```sql
SELECT COUNT(*) FROM items WHERE magic_type='unique'
AND (affixes IS NULL OR affixes='' OR affixes='[]');
-- 166
```

Breakdown of all 166:
- **~140 are `_TestLook` art fixtures** (e.g. `Boots_053_TestLook`, `Helm_054_TestLook`,
  `Chest_054_TestLook`, etc.). These exist in `temp/d4data/json/base/meta/Item/*.itm.json`
  as game art tests, never shipped as wearable items, never had affixes anywhere.
- **19 are S07 Witchcraft socketables** (`S07_Socketable_*` — Wicked Pact, Heart of Anima,
  Toadling's Wish, Killing Wind, etc.). These ARE in `maxroll_data.json["items"]` but every
  single one carries the same single sentinel attribute `{id: 750, value: 6}` — not real
  affix data, just a marker. Season 7 vampiric powers were socketables, not stats-bearing
  uniques.
- **~7 are `[PH]` dev placeholders** (e.g. `[PH] Unique Helm 95`, `[PH]Godslayer Crown`,
  `[PH] Vampiric Feast`). Internal stub records.

**Why this isn't closeable:**
- d4data: art fixtures never had affixes
- maxroll_data.json: same items present but with no usable attribute data
- temp/d4data Item JSONs: just art metadata
- No other source exists

**Verification:** all 166 confirmed in `maxroll_data.json["items"]` (Apr 9 2026 fetch,
version 2.6.0.70982). Of those, 0 have meaningful affix data, 19 carry only the sentinel
S07 socket marker, 147 have no affix-like attributes at all.

### 23 active class skills with no damage coefficient

Audit query:
```sql
SELECT power_name, display_name, primary_tag FROM skills s
LEFT JOIN skill_damage sd ON s.power_name = sd.power_name
WHERE s.class IN ('Barbarian','Druid','Necromancer','Paladin','Rogue','Sorcerer','Spiritborn')
AND s.is_passive = 0 AND sd.power_name IS NULL;
-- 23
```

Most are utility/defensive skills that legitimately deal no direct damage:
Challenging Shout, Berserking, Rallying Cry, War Cry, Blood Howl, Debilitating Roar,
Petrify, Bone Prison, Decrepify, Raise Skeleton, Concealment, Dark Shroud, Stealth,
Fanaticism Aura, Aegis, Fortress, Iron Skin, Iron Maelstrom variants, Soar, etc.

**Why this isn't closeable:** the d4data `arPayloads` lists are legitimately empty for
these — the game has no damage payload because they're buffs/CC/auras. The optimizer
handles them via the utility scoring branch (`UTILITY_TAG_VALUES` + extracted
description effects), not via damage coefficients.

### Charge-based skills with `tCooldownTime = 0`

Hydra, Familiar (X1_Sorcerer_Familiar), Spiritborn Eagle Ultimate. These have
`tCooldownTime = 0` because they're not cooldown-gated; they're charge-gated. The
real time-between-casts is in `tRechargeTime`. `extract_cooldowns_d4data.py` falls
back to `tRechargeTime` for these. Hydra has BOTH fields = 0 because it's
mana+max-active gated only — no time gate at all. Confirmed: not extractable as
"cooldown" because no cooldown exists in the game data.

### "Current Bonus" tooltip displays in passive descriptions

Passive descriptions like Combustion, Precision, Ossified Essence, Shadowblight,
Affliction, Cunning Strategem etc. include a "Current Bonus: [Min(...)]" string that
shows the player's LIVE computed bonus from runtime state (e.g. their actual current
crit damage). The extractor used to mistake these for new effects.

`build_passive_table.py` filters them via `_is_runtime_display_expr()` which detects
expressions wrapped in `Min(...)`/`Max(...)` AND containing `_Bonus`, `_Percent`, or
`Resource_Cur` patterns. **Don't expand the filter to include `PlayerHealthMax()` or
`ToPlayerDmgNum()`** — those wrap real numeric coefficients (e.g. `0.05` in
`PlayerHealthMax()*0.05` is the actual heal % for Vital Strikes' "5% of max life").
