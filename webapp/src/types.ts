export interface BuildSummary {
  id: string;
  uuid: string;
  build_name: string;
  class: string;
  available: string;
  season: string;
  difficulty: string;
  playstyle_summary: string;
  stat_priority: string[];
  file: string;
  guide: string;
  tier: 'S' | 'A' | 'B' | 'C';
  efficiency_score: number;
  season_rank: number;
}

export interface BuildsIndex {
  version: string;
  season: string;
  total_builds: number;
  classes: string[];
  builds: BuildSummary[];
}

export interface Skill {
  name: string;
  rank: number;
  note?: string;
}

export interface Skills {
  basic?: Skill[];
  core?: Skill[];
  defensive?: Skill[];
  brawling?: Skill[];
  macabre?: Skill[];
  corpse_macabre?: Skill[];
  curse?: Skill[];
  corruption?: Skill[];
  summoning?: Skill[];
  ultimate?: Skill[];
  companion?: Skill[];
  imbuement?: Skill[];
  trap?: Skill[];
  agility?: Skill[];
  wrath?: Skill[];
  incarnate?: Skill[];
  ferocity?: Skill[];
  resolve?: Skill[];
  spirit?: Skill[];
  key_passive: string;
  skill_bar?: string[];
  [key: string]: Skill[] | string | string[] | undefined;
}

export interface GearSlot {
  item: string;
  type: string;
  aspect?: string;
  temper_1?: string;
  temper_2?: string;
  gems?: string[];
  note?: string;
  bloodied?: string;
  stats?: string[];
}

export interface Gear {
  [slot: string]: GearSlot;
}

export interface Runeword {
  ritual: string;
  invocation: string;
  slot: string;
  synergy: string;
}

export interface BuildVariant {
  stage: string;
  stage_name: string;
  description: string;
  gear: Gear;
  gems_strategy?: string;
}

export interface ParagonGlyphData {
  name: string;
  radius: number;
  primary_bonus: string;
  radius_bonus: string;
}

export interface ParagonBoardEntry {
  name: string;
  type: string;
  paragon_points: number;
  socket: string;
  glyph: ParagonGlyphData | 'None' | null;
  stat_increases: Record<string, number>;
  rotation?: 0 | 90 | 180 | 270;
}

export interface ParagonBoards {
  starting: ParagonBoardEntry;
  board_1: ParagonBoardEntry;
  board_2: ParagonBoardEntry;
  board_3: ParagonBoardEntry;
  board_4?: ParagonBoardEntry;
  total_paragon_points: number;
  paragon_point_allocation: Record<string, number>;
}

export interface BuildDetail {
  build_name: string;
  class: string;
  available: string;
  season: string;
  difficulty: string;
  playstyle_summary: string;
  math_justification?: string;
  skills: Skills;
  gear: Gear;
  runewords: Runeword[];
  stat_priority: string[];
  gems_strategy?: string;
  seasonal_synergy?: {
    killstreak_strategy?: string;
    bloodied_items?: string[];
  };
  class_mechanic?: {
    technique: string;
    note: string;
  };
  variants?: BuildVariant[];
  paragon_boards?: ParagonBoards;
  skill_order?: string[];
}
