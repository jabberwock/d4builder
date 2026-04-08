// Auto-generated: skill icon slugs that exist as .png only (no .jpg)
// Used by BuildDetail to avoid 404s from trying .jpg first
export const PNG_ONLY_SLUGS: Record<string, Set<string>> = {
  barbarian: new Set(['aggressive_resistance', 'booming_voice', 'furious_impulse', 'guttural_yell', 'heavy_handed', 'imposing_presence', 'invigorating_fury', 'iron_skin', 'martial_vigor', 'mighty_throw_skills', 'no_mercy', 'pit_fighter', 'pressure_point', 'raid_leader', 'slaying_strike', 'tempered_fury', 'thick_skin']),
  druid: new Set(['abundance', 'charged_atmosphere', 'clarity', 'crushing_earth', 'defiance', 'elemental_exposure', 'endless_tempest', 'ferocity', 'heart_of_the_wild', 'natural_disaster', 'nature_s_reach', 'natures_reach', 'predatory_instinct', 'safeguard', 'survival_instincts', 'wild_impulses', 'wolves_passive_heightened_senses']),
  necromancer: new Set(['amplify_damage', 'bone_storm', 'compound_fracture', 'death_s_embrace', 'death_s_reach', 'deaths_embrace', 'deaths_reach', 'fueled_by_death', 'golem', 'golem_mastery', 'grim_harvest', 'gruesome_mending', 'hellbent_commander', 'hewed_flesh', 'imperfect_resonance', 'imperfectly_balanced', 'inspiring_leader', 'memento_mori', 'serration', 'skeletons', 'stand_alone', 'transfusion', 'unliving_energy']),
  paladin: new Set(['advance', 'aegis', 'anointing_passive_skill', 'arbiter_of_justice', 'blessed_hammer', 'blessed_life', 'blessed_shield', 'brandish', 'break_the_line_passive_skill', 'clash', 'condemn', 'consecration', 'conviction', 'defiance', 'defiance_aura', 'divine_lance', 'divine_wrath', 'dizzying_blow_passive_skill', 'falling_star', 'fanaticism', 'fanaticism_aura', 'fortress', 'giant_slayer_passive_skill', 'heaven_s_fury', 'heavens_fury', 'heavyweight_passive_skill', 'holy_bolt', 'holy_fervor', 'holy_light', 'holy_light_aura', 'holy_shield', 'judgment', 'might_of_the_faithful', 'phalanx', 'pressure_point', 'purify', 'rally', 'righteousness_passive_skill', 'shield_bash', 'shield_charge', 'shining_armor_passive_skill', 'smite', 'spear_of_the_heavens', 'thorns_and_thistles_passive_skill', 'zeal', 'zenith']),
  rogue: new Set(['adrenaline_rush', 'aftermath', 'agile', 'alchemical_advantage', 'consuming_shadows', 'dance_of_knives_diablo_4_wiki_guide_126px', 'deadly_venom', 'death_from_above', 'evasion', 'exploit', 'frigid_finesse', 'innervation', 'second_wind', 'shadow_crash', 'siphoning_strikes', 'sturdy', 'stutter_step', 'subverting_dark_shroud', 'trap_mastery', 'trick_attacks', 'unstable_elixirs', 'weapon_mastery']),
  sorcerer: new Set(['align_the_elements', 'conjuration_mastery', 'coursing_currents', 'devastation', 'frigid_breeze', 'icy_touch', 'inner_flames', 'snap_freeze', 'static_discharge']),
  spiritborn: new Set(['acceleration', 'adaptive', 'apex', 'crushing_advance', 'endurance', 'flourish', 'indomitable', 'ironclad', 'nourishment', 'potent', 'resolute', 'stampede', 'toxicity', 'unrelenting', 'viper_strike']),
};

export function getSkillIconPath(cls: string, slug: string): string {
  const isPng = PNG_ONLY_SLUGS[cls]?.has(slug) ?? false;
  return `/media/skills/${cls}/skill_${slug}.${isPng ? 'png' : 'jpg'}`;
}
