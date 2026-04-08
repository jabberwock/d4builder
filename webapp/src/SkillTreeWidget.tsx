import { createSignal, Show, For } from 'solid-js';
import type { Component } from 'solid-js';
import type { Skills, Skill } from './types';
import './SkillTreeWidget.css';

interface SkillTreeWidgetProps {
  skills: Skills;
  buildClass: string;
}

const TIER_ORDER = [
  'basic', 'core', 'defensive', 'conjuration', 'mastery', 'brawling', 'weapon_mastery',
  'macabre', 'corpse_macabre', 'curse', 'corruption', 'summoning',
  'companion', 'imbuement', 'trap', 'agility', 'wrath',
  'incarnate', 'ferocity', 'resolve', 'spirit', 'ultimate',
] as const;

const TIER_LABELS: Record<string, string> = {
  basic: 'Basic', core: 'Core', defensive: 'Defensive',
  conjuration: 'Conjuration', mastery: 'Mastery',
  brawling: 'Brawling', weapon_mastery: 'Weapon Mastery',
  macabre: 'Macabre', corpse_macabre: 'Corpse & Macabre',
  curse: 'Curse', corruption: 'Corruption', summoning: 'Summoning',
  companion: 'Companion', imbuement: 'Imbuement', trap: 'Trap',
  agility: 'Agility', wrath: 'Wrath', incarnate: 'Incarnate',
  ferocity: 'Ferocity', resolve: 'Resolve', spirit: 'Spirit',
  ultimate: 'Ultimate',
};

const TIER_COLORS: Record<string, string> = {
  basic: '#9e9e9e', core: '#c9730a', defensive: '#3b6dbf',
  conjuration: '#2e8bbf', mastery: '#9b59b6',
  brawling: '#bf3b3b', weapon_mastery: '#9e8b2e',
  macabre: '#7b3bbf', corpse_macabre: '#7b3bbf',
  curse: '#8b1010', corruption: '#4a6060', summoning: '#5b3bbf',
  companion: '#7b5b2e', imbuement: '#c46080', trap: '#4e8b4e',
  agility: '#4e9b6b', wrath: '#c05020', incarnate: '#70bf4e',
  ferocity: '#c07020', resolve: '#4e7b2e', spirit: '#a070c0',
  ultimate: '#daa520',
};

function isPassive(skill: Skill): boolean {
  const note = (skill.note ?? '').toLowerCase();
  return note.startsWith('passive') || note.includes(' — passive') || note.includes('(passive)');
}

function skillIconUrl(buildClass: string, skillName: string): string {
  const slug = skillName.toLowerCase()
    .replace(/'/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return `/media/skills/${buildClass.toLowerCase()}/skill_${slug}`;
}

const SkillNode: Component<{ skill: Skill; buildClass: string; tierColor: string }> = (props) => {
  const [showTip, setShowTip] = createSignal(false);
  const [tried, setTried] = createSignal<'png' | 'jpg' | 'fail'>('png');
  const passive = () => isPassive(props.skill);
  const base = () => skillIconUrl(props.buildClass, props.skill.name);

  function imgSrc() {
    const t = tried();
    if (t === 'png') return `${base()}.png`;
    if (t === 'jpg') return `${base()}.jpg`;
    return '';
  }

  function onImgError() {
    const t = tried();
    if (t === 'png') setTried('jpg');
    else setTried('fail');
  }

  return (
    <div
      class={`stw-node${passive() ? ' stw-passive' : ''}`}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      <div class="stw-frame" style={`--tier-clr: ${props.tierColor}`}>
        <Show when={tried() !== 'fail'} fallback={
          <div class="stw-fallback">{props.skill.name.slice(0, 2).toUpperCase()}</div>
        }>
          <img
            src={imgSrc()}
            alt={props.skill.name}
            class="stw-icon"
            onError={onImgError}
          />
        </Show>
        <span class="stw-rank" style={`background: ${props.tierColor}`}>{props.skill.rank}</span>
      </div>
      <div class="stw-name">{props.skill.name}</div>
      <Show when={showTip() && props.skill.note}>
        <div class="stw-tip">{props.skill.note}</div>
      </Show>
    </div>
  );
};

const SkillTreeWidget: Component<SkillTreeWidgetProps> = (props) => {
  const activeTiers = () => TIER_ORDER.filter(t => {
    const v = props.skills[t as keyof Skills];
    return Array.isArray(v) && (v as Skill[]).length > 0;
  });

  return (
    <div class="stw-wrap">
      <div class="stw-header">
        <span class="stw-title">Skill Tree</span>
        <span class="stw-class">{props.buildClass}</span>
      </div>

      <div class="stw-tree">
        <For each={activeTiers()}>
          {(tier, idx) => {
            const skills = props.skills[tier as keyof Skills] as Skill[];
            const color = TIER_COLORS[tier] ?? '#888';
            return (
              <>
                {idx() > 0 && <div class="stw-vline" />}
                <div class="stw-tier">
                  <div class="stw-tier-label" style={`color: ${color}; border-color: ${color}55`}>
                    {TIER_LABELS[tier] ?? tier}
                  </div>
                  <div class="stw-tier-nodes">
                    <For each={skills}>
                      {(skill) => (
                        <SkillNode skill={skill} buildClass={props.buildClass} tierColor={color} />
                      )}
                    </For>
                  </div>
                </div>
              </>
            );
          }}
        </For>

        <Show when={props.skills.key_passive}>
          <div class="stw-vline" />
          <div class="stw-key-passive">
            <span class="stw-kp-label">Key Passive</span>
            <span class="stw-kp-name">{props.skills.key_passive}</span>
          </div>
        </Show>
      </div>
    </div>
  );
};

export default SkillTreeWidget;
