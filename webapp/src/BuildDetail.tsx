import { createResource, createSignal, createMemo, For, Show } from 'solid-js';
import type { Component, JSX } from 'solid-js';
import type { BuildSummary, BuildDetail as BuildDetailData, GearSlot, Skill } from './types';
import ParagonBoardWidget from './ParagonBoardWidget';
import SkillTreeWidget from './SkillTreeWidget';
import MapWidget from './MapWidget';
import { getSkillIconPath } from './iconFormats';

/* ─── Helpers ─────────────────────────────────────────────────────────── */

function classSlug(cls: string): string {
  return cls.toLowerCase().replace(/\s+/g, '-');
}

function classCssClass(cls: string): string {
  return `cls-${cls.toLowerCase()}`;
}

const SKILL_PREFIXES = /^(enhanced|prime|supreme|primary|advanced|improved|blended|countering|subverting|disciplined|methodical|fundamental)\s+/i;

function nameToSlug(name: string): string {
  const base = name.replace(SKILL_PREFIXES, '');
  return base.toLowerCase().replace(/'/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
}

function onImgError(e: Event): void {
  const img = e.currentTarget as HTMLImageElement;
  if (img.src.endsWith('.jpg')) {
    img.src = img.src.replace(/\.jpg$/, '.png');
  } else {
    img.removeAttribute('src');
    img.classList.add('img-missing');
  }
}

const GEAR_SLOT_LABELS: Record<string, string> = {
  helm:           'Helm',
  chest:          'Chest',
  gloves:         'Gloves',
  pants:          'Pants',
  boots:          'Boots',
  weapon_main:    'Main Hand',
  weapon_bludgeon:'Bludgeon',
  weapon_slash:   'Slash',
  weapon_2h:      '2H Weapon',
  weapon_offhand: 'Off-Hand',
  offhand:        'Off-Hand',
  amulet:         'Amulet',
  ring_1:         'Ring 1',
  ring_2:         'Ring 2',
};

const SKILL_SECTION_LABELS: Record<string, string> = {
  basic:              'Basic',
  core:               'Core',
  defensive:          'Defensive',
  conjuration:        'Conjuration',
  mastery:            'Mastery',
  brawling:           'Brawling',
  macabre:            'Macabre',
  corpse_macabre:     'Corpse & Macabre',
  corruption:         'Corruption',
  curse:              'Curse',
  summoning:          'Summoning',
  summon:             'Summoning',
  ultimate:           'Ultimate',
  companion:          'Companion',
  imbuement:          'Imbuement',
  trap:               'Trap',
  agility:            'Agility',
  wrath:              'Wrath',
  incarnate:          'Incarnate',
  ferocity:           'Ferocity',
  resolve:            'Resolve',
  spirit:             'Spirit',
  weapon_mastery:     'Weapon Mastery',
  subterfuge:         'Subterfuge',
  focus:              'Focus',
  potency:            'Potency',
  aura:               'Aura',
  valor:              'Valor',
  justice:            'Justice',
  enchantment_slots:  'Enchantment Slots',
};


/* ─── Data fetching ───────────────────────────────────────────────────── */

async function fetchBuild(uuid: string): Promise<BuildDetailData> {
  const res = await fetch(`/api/builds/${uuid}`);
  if (!res.ok) throw new Error(`Failed to load build ${uuid}`);
  return res.json() as Promise<BuildDetailData>;
}

/* ─── Section header ──────────────────────────────────────────────────── */

const SectionHeader: Component<{ title: string }> = (props) => (
  <div class="d-section-header">
    <span class="d-section-title">{props.title}</span>
    <span class="d-section-rule" />
  </div>
);

/* ─── Skill section ───────────────────────────────────────────────────── */

interface SkillSectionProps {
  label: string;
  key: string;
  skills: Skill[];
  cls: string;
  onSkillEnter: (e: MouseEvent, skill: Skill, category: string) => void;
  onSkillLeave: () => void;
  onSkillMove: (e: MouseEvent) => void;
}

function getGearBoost(rank: number, category: string): { baseMax: number; boost: number } | undefined {
  const baseMaxRanks: Record<string, number> = {
    basic: 5,
    core: 5,
    defensive: 5,
    brawling: 5,
    macabre: 5,
    corruption: 5,
    summoning: 5,
    ultimate: 5,
    companion: 5,
    imbuement: 5,
    trap: 5,
    agility: 5,
    wrath: 5,
    incarnate: 5,
    ferocity: 5,
    resolve: 5,
    spirit: 5,
    weapon_mastery: 5,
  };

  const baseMax = baseMaxRanks[category] ?? 5;

  if (rank > baseMax) {
    return { baseMax, boost: rank - baseMax };
  }

  return undefined;
}

const SkillSection: Component<SkillSectionProps> = (props) => (
  <div class="skill-tree">
    <div class="skill-tree-label">{props.label}</div>
    <div class="skill-list">
      <For each={props.skills}>
        {skill => {
          const gearBoost = getGearBoost(skill.rank, props.key);
          return (
            <div
              class="skill-row"
              onMouseEnter={(e) => props.onSkillEnter(e, skill, props.key)}
              onMouseLeave={props.onSkillLeave}
              onMouseMove={props.onSkillMove}
            >
              <img
                src={getSkillIconPath(props.cls, nameToSlug(skill.name))}
                alt=""
                class="skill-icon"
                loading="lazy"
                onError={onImgError}
              />
              <div class="skill-info">
                <div class="skill-header">
                  <span class="skill-name">{skill.name}</span>
                  <span class="skill-rank">
                    <Show when={gearBoost} fallback={<>Rank {skill.rank}</>}>
                      {boost => <>Rank {boost().baseMax} <span class="skill-rank-gear-badge" title={`Skill is maxed at rank ${boost().baseMax}; gear augmentation adds +${boost().boost} bonus rank`}>(+{boost().boost} from gear)</span></>}
                    </Show>
                  </span>
                </div>
                <Show when={skill.note?.trim()}>
                  <p class="skill-note">{skill.note}</p>
                </Show>
              </div>
            </div>
          );
        }}
      </For>
    </div>
  </div>
);

/* ─── Copy link button ────────────────────────────────────────────────── */

const CopyLinkButton: Component<{ buildId: string }> = (props) => {
  const [copied, setCopied] = createSignal(false);

  function copyLink(): void {
    const url = `${window.location.origin}${window.location.pathname}#${props.buildId}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <button class="copy-link-btn" onClick={copyLink}>
      {copied() ? '✓ Copied' : '⎘ Copy Link'}
    </button>
  );
};

/* ─── Action Bar ──────────────────────────────────────────────────────── */

const SLOT_LABELS = ['LMB', 'RMB', '1', '2', '3', '4'];

function getActiveSkills(skills: import('./types').Skills, barOverride?: string[]): import('./types').Skill[] {
  const TIER_ORDER = [
    'basic', 'core', 'defensive', 'brawling', 'subterfuge', 'aura', 'valor', 'justice',
    'macabre', 'corpse_macabre', 'corruption', 'curse', 'summoning', 'summon',
    'companion', 'imbuement', 'trap', 'agility', 'wrath', 'focus', 'potency',
    'conjuration', 'mastery', 'incarnate', 'ferocity', 'resolve', 'spirit',
    'weapon_mastery', 'enchantment_slots', 'ultimate',
  ];

  // Build a flat lookup of all skills by name
  const skillByName = new Map<string, import('./types').Skill>();
  for (const tier of TIER_ORDER) {
    const list = skills[tier];
    if (Array.isArray(list)) {
      for (const s of list as import('./types').Skill[]) {
        if (s.name) skillByName.set(s.name, s);
      }
    }
  }

  // If an explicit skill_bar order is provided, use it
  const barOrder = barOverride ?? (skills as Record<string, unknown>).skill_bar;
  if (Array.isArray(barOrder)) {
    return (barOrder as string[])
      .map(name => skillByName.get(name))
      .filter((s): s is import('./types').Skill => s != null);
  }

  // Fallback: auto-detect active skills
  const allNames = new Set(skillByName.keys());
  const active: import('./types').Skill[] = [];
  for (const tier of TIER_ORDER) {
    const list = skills[tier];
    if (!Array.isArray(list)) continue;
    for (const s of list) {
      const note = s.note ?? '';
      if (note.startsWith('PASSIVE')) continue;
      const isUpgrade = [...allNames].some(other => other !== s.name && s.name.endsWith(' ' + other));
      if (isUpgrade) continue;
      if (active.length < 6) active.push(s);
    }
  }
  return active;
}

const ActionBar: Component<{ skills: import('./types').Skills; cls: string; skillBar?: string[] }> = (props) => {
  const slots = () => getActiveSkills(props.skills, props.skillBar);
  return (
    <div class="action-bar">
      <For each={slots()}>
        {(skill, i) => (
          <div class="ab-slot">
            <div class="ab-keybind">{SLOT_LABELS[i()] ?? String(i() + 1)}</div>
            <div class="ab-icon-wrap">
              <img
                src={getSkillIconPath(props.cls, nameToSlug(skill.name))}
                alt={skill.name}
                class="ab-icon"
                onError={onImgError}
              />
            </div>
            <div class="ab-name">{skill.name}</div>
          </div>
        )}
      </For>
      {/* Fill empty slots up to 6 */}
      <For each={Array(Math.max(0, 6 - slots().length)).fill(null)}>
        {(_, i) => (
          <div class="ab-slot ab-slot-empty">
            <div class="ab-keybind">{SLOT_LABELS[slots().length + i()] ?? ''}</div>
            <div class="ab-icon-wrap" />
            <div class="ab-name">—</div>
          </div>
        )}
      </For>
    </div>
  );
};

/* ─── Leveling Guide ──────────────────────────────────────────────────── */

const RENOWN_CHECKPOINTS: Array<{ label: string; pts: number }> = [
  { label: '0 zones', pts: 0 },
  { label: '1 zone',  pts: 1 },
  { label: '2 zones', pts: 2 },
  { label: '3 zones', pts: 3 },
  { label: '4 zones', pts: 4 },
  { label: '5 zones', pts: 5 },
  { label: '6 zones', pts: 6 },
];

const LVL_TIER_ORDER = [
  'basic', 'core', 'defensive', 'brawling', 'subterfuge', 'aura', 'valor', 'justice',
  'macabre', 'corpse_macabre', 'corruption', 'curse', 'summoning', 'summon',
  'companion', 'imbuement', 'trap', 'agility', 'wrath', 'focus', 'potency',
  'conjuration', 'mastery', 'incarnate', 'ferocity', 'resolve', 'spirit',
  'weapon_mastery', 'enchantment_slots', 'ultimate',
];

const LevelingGuide: Component<{
  skills: import('./types').Skills;
  skillOrder: string[];
  cls: string;
}> = (props) => {
  const [level, setLevel] = createSignal(1);
  const [renownPts, setRenownPts] = createSignal(0);

  // D4: skill points from levels 2–50 only (max 49), then renown on top
  const availablePoints = createMemo(() =>
    Math.min(Math.max(0, level() - 1), 49) + renownPts()
  );

  const currentRanks = createMemo(() => {
    const ranks: Record<string, number> = {};
    for (const name of props.skillOrder.slice(0, availablePoints()))
      ranks[name] = (ranks[name] ?? 0) + 1;
    return ranks;
  });

  // Static — sections don't change with level/renown, only rank display does
  const sections = LVL_TIER_ORDER
    .map(tier => {
      const list = props.skills[tier];
      if (!Array.isArray(list) || list.length === 0) return null;
      return { tier, label: SKILL_SECTION_LABELS[tier] ?? tier, skills: list as import('./types').Skill[] };
    })
    .filter(Boolean) as Array<{ tier: string; label: string; skills: import('./types').Skill[] }>;

  return (
    <div class="lvl-guide">
      <div class="lvl-controls">
        <div class="lvl-slider-wrap">
          <label class="lvl-label">
            Level <span class="lvl-value">{level()}</span>
            <span class="lvl-pts">· {availablePoints()} skill points</span>
          </label>
          <input
            type="range"
            min={1}
            max={60}
            value={level()}
            class="lvl-slider"
            style={`--pct: ${((level() - 1) / 59) * 100}%`}
            onInput={(e) => setLevel(parseInt(e.currentTarget.value))}
          />
          <div class="lvl-tick-row">
            <span>1</span><span>15</span><span>30</span><span>45</span><span>60</span>
          </div>
        </div>
        <div class="lvl-renown-wrap">
          <span class="lvl-renown-label">Renown</span>
          <div class="lvl-renown-pills">
            <For each={RENOWN_CHECKPOINTS}>
              {(cp) => (
                <button
                  class={`lvl-renown-pill${renownPts() === cp.pts ? ' active' : ''}`}
                  onClick={() => setRenownPts(cp.pts)}
                >
                  +{cp.pts} ({cp.label})
                </button>
              )}
            </For>
          </div>
        </div>
      </div>

      <div class="lvl-skills">
        <For each={sections}>
          {({ label, skills }) => (
            <div class="lvl-tier">
              <div class="lvl-tier-label">{label}</div>
              <For each={skills}>
                {(skill) => {
                  const maxRank = Math.min(skill.rank, 5);
                  const cur = () => Math.min(currentRanks()[skill.name] ?? 0, maxRank);
                  const unlocked = () => cur() > 0;
                  const isPassiveSkill = skill.note?.startsWith('PASSIVE') ||
                    [...Object.values(props.skills).flat()].some(
                      (other): other is import('./types').Skill =>
                        typeof other === 'object' && other !== null && 'name' in other &&
                        (other as import('./types').Skill).name !== skill.name &&
                        skill.name.endsWith(' ' + (other as import('./types').Skill).name)
                    );
                  return (
                    <div class={`lvl-skill${unlocked() ? ' unlocked' : ' locked'}`}>
                      <img
                        src={getSkillIconPath(props.cls, nameToSlug(skill.name))}
                        alt=""
                        class="lvl-skill-icon"
                        onError={onImgError}
                      />
                      <div class="lvl-skill-info">
                        <div class="lvl-skill-name">
                          {skill.name}
                          {isPassiveSkill && <span class="lvl-passive-badge">passive</span>}
                        </div>
                        <div class="lvl-pips">
                          <For each={Array(maxRank).fill(null)}>
                            {(_, pi) => (
                              <span class={`lvl-pip${pi() < cur() ? ' filled' : ''}`} />
                            )}
                          </For>
                          <span class="lvl-rank-text">{cur()}/{maxRank}</span>
                        </div>
                      </div>
                    </div>
                  );
                }}
              </For>
            </div>
          )}
        </For>
        <Show when={props.skills.key_passive}>
          <div class="lvl-tier">
            <div class="lvl-tier-label">Key Passive</div>
            <div class={`lvl-skill${availablePoints() >= 33 ? ' unlocked' : ' locked'}`}>
              <div class="lvl-skill-info">
                <div class="lvl-skill-name">{props.skills.key_passive}</div>
                <div class="lvl-rank-text">
                  {availablePoints() >= 33 ? 'Unlocked (33+ pts)' : `Requires 33 pts (have ${availablePoints()})`}
                </div>
              </div>
            </div>
          </div>
        </Show>
      </div>
    </div>
  );
};

/* ─── Tooltip helpers ─────────────────────────────────────────────────── */

const GEAR_TYPE_LABEL: Record<string, string> = {
  legendary: 'Legendary',
  unique:    'Unique',
  mythic:    'Mythic Unique',
  rare:      'Rare',
};

const GEAR_TYPE_CLASS: Record<string, string> = {
  legendary: 'tt-type-legendary',
  unique:    'tt-type-unique',
  mythic:    'tt-type-mythic',
  rare:      'tt-type-rare',
};

/* ─── Props ───────────────────────────────────────────────────────────── */

interface Props {
  summary: BuildSummary;
  onBack: () => void;
}

/* ─── Main component ──────────────────────────────────────────────────── */

const BuildDetail: Component<Props> = (props) => {
  const [detail] = createResource(() => props.summary.uuid, fetchBuild);
  const cls = () => classSlug(props.summary.class);

  /* ── Tooltip state ─────────────────────────────────── */
  const [tooltipVisible, setTooltipVisible] = createSignal(false);
  const [tooltipX, setTooltipX] = createSignal(0);
  const [tooltipY, setTooltipY] = createSignal(0);
  const [tooltipContent, setTooltipContent] = createSignal<JSX.Element>(null);

  function showTooltip(e: MouseEvent, content: JSX.Element): void {
    setTooltipX(e.clientX);
    setTooltipY(e.clientY);
    setTooltipContent(content);
    setTooltipVisible(true);
  }

  function moveTooltip(e: MouseEvent): void {
    setTooltipX(e.clientX);
    setTooltipY(e.clientY);
  }

  function hideTooltip(): void {
    setTooltipVisible(false);
  }

  function skillTooltipContent(skill: Skill, category: string): JSX.Element {
    const gearBoost = getGearBoost(skill.rank, category);
    return (
      <div class="tt-skill">
        <div class="tt-skill-header">
          <span class="tt-skill-name">{skill.name}</span>
          <span class="tt-skill-rank">
            {gearBoost
              ? <>Rank {gearBoost.baseMax} <span class="tt-gear-boost">+{gearBoost.boost} from gear</span></>
              : <>Rank {skill.rank}</>
            }
          </span>
        </div>
        <Show when={skill.note?.trim()}>
          <p class="tt-skill-desc">{skill.note}</p>
        </Show>
      </div>
    );
  }

  function gearTooltipContent(label: string, data: GearSlot): JSX.Element {
    return (
      <div class="tt-gear">
        <div class="tt-gear-header">
          <span class="tt-gear-slot">{label}</span>
          <span class={`tt-gear-type ${GEAR_TYPE_CLASS[data.type] ?? ''}`}>
            {GEAR_TYPE_LABEL[data.type] ?? data.type}
          </span>
        </div>
        <div class="tt-gear-name">{data.item}</div>
        <Show when={data.stats && data.stats.length > 0}>
          <div class="tt-divider" />
          <ul class="tt-stats">
            <For each={data.stats}>
              {stat => <li class="tt-stat">{stat}</li>}
            </For>
          </ul>
        </Show>
        <Show when={data.note?.trim()}>
          <div class="tt-divider" />
          <p class="tt-gear-note">{data.note}</p>
        </Show>
      </div>
    );
  }

  const skillSections = () => {
    const d = detail();
    if (!d) return [];
    return Object.entries(d.skills)
      .filter(([key, val]) => key in SKILL_SECTION_LABELS && Array.isArray(val) && (val as Skill[]).length > 0)
      .map(([key, val]) => ({
        key,
        label: SKILL_SECTION_LABELS[key] ?? key,
        skills: val as Skill[],
      }));
  };

  const gearSlots = () => {
    const d = detail();
    if (!d) return [];
    return Object.entries(d.gear)
      .filter(([, data]) => data != null)
      .map(([slot, data]) => ({
        slot,
        label: GEAR_SLOT_LABELS[slot] ?? slot.replace(/_/g, ' '),
        data,
      }));
  };

  return (
    <div class="detail">
      {/* ── Back nav ──────────────────────────────────────── */}
      <div class="detail-nav">
        <button class="back-btn" onClick={props.onBack}>
          ← All Builds
        </button>
        <CopyLinkButton buildId={props.summary.uuid} />
      </div>

      {/* ── Hero banner ───────────────────────────────────── */}
      <div class="detail-hero">
        <img
          src={`/media/classes/${cls()}.webp`}
          alt=""
          class="detail-hero-img"
          onError={onImgError}
        />
        <div class="detail-hero-overlay" />
        <div class="detail-hero-content">
          <div class="detail-meta-row">
            <span class={`detail-class-label ${classCssClass(props.summary.class)}`}>
              {props.summary.class}
            </span>
          </div>
          <h1 class="detail-title">{props.summary.build_name}</h1>
          <p class="detail-season-line">
            {props.summary.season}
          </p>
        </div>
      </div>

      {/* ── Loading / error ───────────────────────────────── */}
      <Show when={detail.loading}>
        <div class="detail-loading">Consulting the Codex…</div>
      </Show>

      <Show when={detail.error}>
        <div class="error-banner" role="alert">
          Failed to load build details.
        </div>
      </Show>

      {/* ── Body ──────────────────────────────────────────── */}
      <Show when={detail()}>
        {(d) => (
          <div class="detail-body">

            {/* Build Identity */}
            <section class="d-section">
              <SectionHeader title="Build Identity" />
              <div class="identity-grid">
                <div class="identity-block">
                  <p>{d().playstyle_summary}</p>
                </div>
                <Show when={d().math_justification}>
                  <div class="identity-block math-block">
                    <p>{d().math_justification}</p>
                  </div>
                </Show>
              </div>
            </section>

            {/* Action Bar */}
            <section class="d-section">
              <SectionHeader title="Action Bar" />
              <ActionBar skills={d().skills} cls={cls()} skillBar={(d() as Record<string, unknown>).skill_bar as string[] | undefined} />
            </section>

            {/* Skills */}
            <section class="d-section">
              <SectionHeader title="Skills" />
              <div class="skills-body">
                <For each={skillSections()}>
                  {sec => (
                    <SkillSection
                      label={sec.label}
                      key={sec.key}
                      skills={sec.skills}
                      cls={cls()}
                      onSkillEnter={(e, skill, cat) => showTooltip(e, skillTooltipContent(skill, cat))}
                      onSkillLeave={hideTooltip}
                      onSkillMove={moveTooltip}
                    />
                  )}
                </For>
                <div class="key-passive-row">
                  <span class="key-passive-label">Key Passive</span>
                  <span class="key-passive-value">{d().skills.key_passive}</span>
                </div>
              </div>
            </section>

            {/* Skill Tree Widget */}
            <Show when={d().skills}>
              <section class="d-section">
                <SkillTreeWidget skills={d().skills} buildClass={cls()} />
              </section>
            </Show>

            {/* Leveling Guide */}
            <Show when={d().skill_order && d().skill_order!.length > 0}>
              <section class="d-section">
                <SectionHeader title="Leveling Guide" />
                <LevelingGuide
                  skills={d().skills}
                  skillOrder={d().skill_order!}
                  cls={cls()}
                />
              </section>
            </Show>

            {/* Class Mechanic */}
            <Show when={d().class_mechanic}>
              <section class="d-section">
                <SectionHeader title="Class Mechanic" />
                <div class="mechanic-card">
                  <div class="mechanic-name">{d().class_mechanic!.technique}</div>
                  <p class="mechanic-note">{d().class_mechanic!.note}</p>
                </div>
              </section>
            </Show>

            {/* Gear */}
            <section class="d-section">
              <SectionHeader title="Gear" />
              <div class="gear-grid">
                <For each={gearSlots()}>
                  {({ label, data }) => (
                    <div
                      class="gear-card"
                      onMouseEnter={(e) => showTooltip(e, gearTooltipContent(label, data))}
                      onMouseLeave={hideTooltip}
                      onMouseMove={moveTooltip}
                    >
                      <div class="gear-slot-label">{label}</div>
                      <div
                        class={`gear-item-name${
                          data.type === 'mythic'  ? ' is-mythic'  :
                          data.type === 'unique'  ? ' is-unique'  : ''
                        }`}
                      >
                        {data.item}
                      </div>
                      <Show when={data.aspect}>
                        <div class="gear-aspect">{data.aspect}</div>
                      </Show>
                      <Show when={data.temper_1 || data.temper_2}>
                        <div class="gear-tempers">
                          <Show when={data.temper_1}>
                            <span class="gear-temper">{data.temper_1}</span>
                          </Show>
                          <Show when={data.temper_2}>
                            <span class="gear-temper">{data.temper_2}</span>
                          </Show>
                        </div>
                      </Show>
                      <Show when={data.gems && data.gems!.length > 0}>
                        <div class="gear-gems">
                          <For each={data.gems}>
                            {gem => <span class="gear-gem">{gem}</span>}
                          </For>
                        </div>
                      </Show>
                      <Show when={data.note}>
                        <p class="gear-note">{data.note}</p>
                      </Show>
                    </div>
                  )}
                </For>
              </div>
            </section>

            {/* Aspect Map */}
            <section class="d-section">
              <MapWidget gear={d().gear} />
            </section>

            {/* Runewords */}
            <Show when={d().runewords && d().runewords.length > 0}>
              <section class="d-section">
                <SectionHeader title="Runewords" />
                <div class="runeword-list">
                  <For each={d().runewords}>
                    {rw => (
                      <div class="runeword-card">
                        <div class="runeword-runes">
                          <span class="rune-tag rune-ritual">{rw.ritual}</span>
                          <span class="rune-plus">+</span>
                          <span class="rune-tag rune-invocation">{rw.invocation}</span>
                          <span class="rune-slot">{rw.slot}</span>
                        </div>
                        <p class="runeword-synergy">{rw.synergy}</p>
                      </div>
                    )}
                  </For>
                </div>
              </section>
            </Show>

            {/* Paragon Boards */}
            <Show when={d().paragon_boards}>
              <section class="d-section">
                <ParagonBoardWidget paragon_boards={d().paragon_boards!} buildClass={d().class} />
              </section>
            </Show>

            {/* Stat Priority */}
            <section class="d-section">
              <SectionHeader title="Stat Priority" />
              <ol class="stat-list">
                <For each={d().stat_priority}>
                  {(stat, i) => (
                    <li style={`animation-delay: ${i() * 0.05}s`}>{stat}</li>
                  )}
                </For>
              </ol>
            </section>

            {/* Gem Strategy */}
            <Show when={d().gems_strategy}>
              <section class="d-section">
                <SectionHeader title="Gem Strategy" />
                <div class="gems-block">
                  <p>{d().gems_strategy}</p>
                </div>
              </section>
            </Show>

            {/* Seasonal Synergy */}
            <Show when={d().seasonal_synergy}>
              <section class="d-section">
                <SectionHeader title="Seasonal Synergy" />
                <div class="seasonal-block">
                  <Show when={d().seasonal_synergy!.killstreak_strategy}>
                    <div class="seasonal-strategy">
                      <p>{d().seasonal_synergy!.killstreak_strategy}</p>
                    </div>
                  </Show>
                  <Show when={d().seasonal_synergy!.bloodied_items && d().seasonal_synergy!.bloodied_items!.length > 0}>
                    <div>
                      <div class="bloodied-label">Bloodied Items</div>
                      <ul class="bloodied-list">
                        <For each={d().seasonal_synergy!.bloodied_items}>
                          {item => <li>{item}</li>}
                        </For>
                      </ul>
                    </div>
                  </Show>
                </div>
              </section>
            </Show>

          </div>
        )}
      </Show>

      {/* ── Floating tooltip ─────────────────────────────── */}
      <Show when={tooltipVisible()}>
        <div
          class="tooltip-floating"
          style={{
            left: `${Math.min(tooltipX() + 16, window.innerWidth - 316)}px`,
            top: `${Math.min(tooltipY() - 8, window.innerHeight - 320)}px`,
          }}
        >
          {tooltipContent()}
        </div>
      </Show>
    </div>
  );
};

export default BuildDetail;
