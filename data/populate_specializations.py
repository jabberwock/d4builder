#!/usr/bin/env python3
"""
Populate the specializations table in d4_stats.db with verified game data.
Clears existing rows and inserts canonical specialization data for all classes.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "d4_stats.db")

# (class, specialization_name, description, mechanic_type,
#  required_skill_tags, generator_tags, spender_tags)
ROWS = [
    # ------------------------------------------------------------------
    # Barbarian — Arsenal (weapon_expertise)
    # One row per weapon type. No skill bar constraints.
    # ------------------------------------------------------------------
    (
        "Barbarian",
        "One-Handed Axe",
        "+10% Crit Chance vs Injured enemies; Lucky Hit: Chance to gain increased Attack Speed",
        "weapon_expertise",
        None, None, None,
    ),
    (
        "Barbarian",
        "One-Handed Mace",
        "+10%[x] damage to Stunned enemies; Lucky Hit: Chance to gain Berserking",
        "weapon_expertise",
        None, None, None,
    ),
    (
        "Barbarian",
        "One-Handed Sword",
        "Lucky Hit: 10% Fury generated on CC hit; +15% Attack Speed on CC kill",
        "weapon_expertise",
        None, None, None,
    ),
    (
        "Barbarian",
        "Two-Handed Axe",
        "+15%[x] Vulnerable damage; +10% Crit Chance vs Vulnerable enemies",
        "weapon_expertise",
        None, None, None,
    ),
    (
        "Barbarian",
        "Two-Handed Mace",
        "Lucky Hit: 10% Fury generated; +15%[x] Crit damage while Berserking",
        "weapon_expertise",
        None, None, None,
    ),
    (
        "Barbarian",
        "Two-Handed Sword",
        "20% of direct damage dealt as Bleed over 5s; +30%[x] Bleed damage after kill",
        "weapon_expertise",
        None, None, None,
    ),
    (
        "Barbarian",
        "Polearm",
        "+15% Lucky Hit Chance; +15% damage while Healthy",
        "weapon_expertise",
        None, None, None,
    ),

    # ------------------------------------------------------------------
    # Druid — Spirit Boons (spirit_boon)
    # One row per boon, 16 total. No skill bar constraints.
    # ------------------------------------------------------------------
    # Deer
    (
        "Druid", "Prickleskin",
        "Gain Thorns equal to a portion of your Armor",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Gift of the Stag",
        "+10 Maximum Spirit",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Wariness",
        "-10% damage taken from Elite monsters",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Advantageous Beast",
        "-15% duration of Crowd Control effects applied to you",
        "spirit_boon", None, None, None,
    ),
    # Eagle
    (
        "Druid", "Scythe Talons",
        "+15% Critical Strike Chance",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Iron Feather",
        "+30% Maximum Life",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Swooping Attacks",
        "+20% Attack Speed",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Avian Wrath",
        "+40% Critical Strike Damage",
        "spirit_boon", None, None, None,
    ),
    # Wolf
    (
        "Druid", "Pack Leader",
        "Lucky Hit: Chance to reset all Companion skill cooldowns",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Energize",
        "Lucky Hit: Chance to restore 10 Spirit",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Bolster",
        "Casting a Defensive skill Fortifies you for 10% of your Maximum Life",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Calamity",
        "+35% duration of Ultimate skills",
        "spirit_boon", None, None, None,
    ),
    # Snake
    (
        "Druid", "Obsidian Slam",
        "Every Nth kill causes your next Earth skill to Overpower",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Overload",
        "Lucky Hit: Nature Magic skills have a chance to discharge a lightning bolt dealing additional damage",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Masochistic",
        "Critical Strikes with Shapeshifting skills heal you for a portion of your Maximum Life",
        "spirit_boon", None, None, None,
    ),
    (
        "Druid", "Calm Before the Storm",
        "Nature Magic skills reduce the cooldown of your Ultimate skill by 2 seconds",
        "spirit_boon", None, None, None,
    ),

    # ------------------------------------------------------------------
    # Sorcerer — Enchantment Slots (enchantment_slots)
    # Single row: two enchantment slots for passive skill procs.
    # ------------------------------------------------------------------
    (
        "Sorcerer",
        "Enchantment Slots",
        "Slot any skill (with ≥1 point invested) into 2 enchantment slots for passive proc effects instead of active casting",
        "enchantment_slots",
        "any",
        None, None,
    ),

    # ------------------------------------------------------------------
    # Rogue — 3 specializations
    # ------------------------------------------------------------------
    (
        "Rogue",
        "Combo Points",
        "Basic Skills generate 1 Combo Point (max 3). Core Skills auto-consume all points for scaled bonus damage/effects. "
        "Barrage +20/40/60% dmg; Rapid Fire +13/26/39%; Penetrating Shot +30/60/90%; Flurry +25/50/75% dmg; Twisting Blades +30/60/90%",
        "combo_points",
        "Skill_Basic,Skill_Core",
        "Skill_Basic",
        "Skill_Core",
    ),
    (
        "Rogue",
        "Inner Sight",
        "Hit a marked enemy to fill gauge. Full gauge = unlimited Energy for 4 seconds (roughly every 10-20s)",
        "inner_sight",
        None, None, None,
    ),
    (
        "Rogue",
        "Preparation",
        "Spending 75 Energy reduces Ultimate cooldown by 5s. Casting Ultimate resets all other cooldowns + grants 15% Damage Reduction for 10s",
        "preparation",
        "Skill_Primary_Ultimate",
        None,
        "Skill_Primary_Ultimate",
    ),

    # ------------------------------------------------------------------
    # Necromancer — Book of the Dead (book_of_the_dead)
    # 9 variant rows + 3 sacrifice rows, no skill bar constraints.
    # ------------------------------------------------------------------
    # Warriors
    (
        "Necromancer", "Skeletal Warriors: Skirmishers",
        "Skeletal Warriors deal +30% damage but have -15% Life",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Skeletal Warriors: Defenders",
        "Skeletal Warriors gain +15% Life and periodically taunt nearby enemies",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Skeletal Warriors: Reapers",
        "Skeletal Warriors wield a heavy scythe that deals massive damage every 10s",
        "book_of_the_dead", None, None, None,
    ),
    # Mages
    (
        "Necromancer", "Skeletal Mages: Shadow",
        "Shadow Mages deal Shadow damage and can Stun enemies",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Skeletal Mages: Cold",
        "Cold Mages Chill and Freeze enemies; they also generate Essence for you",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Skeletal Mages: Bone",
        "Bone Mages sacrifice Life to deal massive Bone damage",
        "book_of_the_dead", None, None, None,
    ),
    # Golems
    (
        "Necromancer", "Golem: Bone",
        "Bone Golem taunts enemies and periodically sheds corpses",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Golem: Blood",
        "Blood Golem drains Life from enemies and shares a portion of damage absorbed with you",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Golem: Iron",
        "Iron Golem slams enemies dealing massive damage and Stunning them",
        "book_of_the_dead", None, None, None,
    ),
    # Sacrifices
    (
        "Necromancer", "Sacrifice: Warriors (Skirmishers)",
        "+5% Critical Strike Chance; sacrifice Skeletal Warriors for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Warriors (Defenders)",
        "+15% Physical Resistance; sacrifice Skeletal Warriors for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Warriors (Reapers)",
        "+10% Shadow Damage; sacrifice Skeletal Warriors for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Mages (Shadow)",
        "+15% Maximum Essence; sacrifice Skeletal Mages for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Mages (Cold)",
        "+15% Vulnerable Damage; sacrifice Skeletal Mages for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Mages (Bone)",
        "+40%[x] Overpower Damage; sacrifice Skeletal Mages for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Golem (Bone)",
        "+10% Attack Speed; sacrifice Golem for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Golem (Blood)",
        "+10%[x] Maximum Life; sacrifice Golem for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),
    (
        "Necromancer", "Sacrifice: Golem (Iron)",
        "+30%[x] Critical Strike Damage; sacrifice Golem for permanent passive bonus",
        "book_of_the_dead", None, None, None,
    ),

    # ------------------------------------------------------------------
    # Spiritborn — Spirit Hall (guardian_spirit)
    # Primary (lvl 15): tags all skills as that spirit; Secondary (lvl 30): passive bonus only.
    # ------------------------------------------------------------------
    (
        "Spiritborn", "Jaguar (Primary)",
        "Every 15th direct damage hit unleashes an extra strike dealing 15% of damage dealt in the last 0.5s. All skills become Jaguar Skills.",
        "guardian_spirit",
        "Skill_Spirit_Plains",
        None, None,
    ),
    (
        "Spiritborn", "Jaguar (Secondary)",
        "Maximum Ferocity +1. Gain Ferocity on kill or Boss hit.",
        "guardian_spirit",
        "Skill_Spirit_Plains",
        None, None,
    ),
    (
        "Spiritborn", "Eagle (Primary)",
        "Eagle Skill grants 4s Storm Feathers movement speed. On Evade fling up to 8 Storm Feathers (125% Lightning each, Vulnerable 5s). All skills become Eagle Skills.",
        "guardian_spirit",
        "Skill_Spirit_Sky",
        None, None,
    ),
    (
        "Spiritborn", "Eagle (Secondary)",
        "Per 4m moved gain +4% Critical Strike Chance; resets 4s after a Critical Strike.",
        "guardian_spirit",
        "Skill_Spirit_Sky",
        None, None,
    ),
    (
        "Spiritborn", "Gorilla (Primary)",
        "Gorilla Skill deals 100% Thorns and creates a Barrier equal to 5% Maximum Life (up to 30%) for 3s. All skills become Gorilla Skills.",
        "guardian_spirit",
        "Skill_Spirit_Forest",
        None, None,
    ),
    (
        "Spiritborn", "Gorilla (Secondary)",
        "Maximum Resolve +2. At 5 or more Resolve stacks you become Unstoppable.",
        "guardian_spirit",
        "Skill_Spirit_Forest",
        None, None,
    ),
    (
        "Spiritborn", "Centipede (Primary)",
        "Centipede Skill reduces enemy damage by 2%, Slows 10%, and deals 70% Poison damage for 6s (stacks up to 8x). All skills become Centipede Skills.",
        "guardian_spirit",
        "Skill_Spirit_Soil",
        None, None,
    ),
    (
        "Spiritborn", "Centipede (Secondary)",
        "Heal 1% Maximum Life per nearby Poisoned enemy (up to 5% per second).",
        "guardian_spirit",
        "Skill_Spirit_Soil",
        None, None,
    ),

    # ------------------------------------------------------------------
    # Paladin — Oaths (oaths)
    # 4 oaths, one active at a time.
    # ------------------------------------------------------------------
    (
        "Paladin", "Juggernaut",
        "Casting a Juggernaut Skill consumes 8 Resolve stacks for increased damage and +20% size for 5s. Minimum Resolve +1%.",
        "oaths",
        "Juggernaut_Skill",
        "Juggernaut_Skill",
        "Juggernaut_Skill",
    ),
    (
        "Paladin", "Zealot",
        "Zealot Skills grant Fervor for 4s. Critical Strikes echo the attack once per Fervor stack. At max Fervor Fortify 1% Maximum Life.",
        "oaths",
        "Zealot_Skill",
        "Zealot_Skill",
        "Zealot_Skill",
    ),
    (
        "Paladin", "Judicator",
        "Basic Skills apply Judgement stacks. Core Judicator Skills detonate stacks for AoE damage. Each Judgement stack increases damage taken by 8% (up to 80%).",
        "oaths",
        "Skill_Basic,Judicator_Skill",
        "Skill_Basic",
        "Judicator_Skill",
    ),
    (
        "Paladin", "Disciple",
        "Casting a Cooldown Disciple Skill grants Arbiter for 4.5s. Wing Strikes gain Disciple benefits. +50% Disciple Skill damage while in Arbiter.",
        "oaths",
        "Disciple_Skill",
        None,
        "Disciple_Skill",
    ),

    # ------------------------------------------------------------------
    # Warlock — Soul Shards (soul_shards) — new class, incomplete wiki data
    # ------------------------------------------------------------------
    (
        "Warlock", "Legion (Ae'grom)",
        "Command lesser demons and AoE swarm abilities through the Ae'grom shard. Soul Shard mechanic: incomplete (wiki data pending).",
        "soul_shards",
        None, None, None,
    ),
    (
        "Warlock", "Vanguard (Abodian)",
        "Archfiend mobility and offensive charge abilities through the Abodian shard. Soul Shard mechanic: incomplete (wiki data pending).",
        "soul_shards",
        None, None, None,
    ),
    (
        "Warlock", "Mastermind (Laalish)",
        "Stealth, Shadow damage, and deception abilities through the Laalish shard. Soul Shard mechanic: incomplete (wiki data pending).",
        "soul_shards",
        None, None, None,
    ),
    (
        "Warlock", "Ritualist (Vollach)",
        "Blood, Hex, and Overpower abilities through the Vollach shard. Soul Shard mechanic: incomplete (wiki data pending).",
        "soul_shards",
        None, None, None,
    ),
]


def main():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("DELETE FROM specializations")
    deleted = cur.rowcount
    print(f"Cleared {deleted} existing rows from specializations")

    cur.executemany(
        "INSERT INTO specializations "
        "(class, specialization_name, description, mechanic_type, "
        "required_skill_tags, generator_tags, spender_tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ROWS,
    )
    conn.commit()

    count = cur.execute("SELECT COUNT(*) FROM specializations").fetchone()[0]
    conn.close()

    print(f"Inserted {len(ROWS)} rows -> total in table: {count}")

    # Summary by class
    conn2 = sqlite3.connect(DB_PATH)
    for row in conn2.execute(
        "SELECT class, COUNT(*) FROM specializations GROUP BY class ORDER BY class"
    ):
        print(f"  {row[0]:20s}: {row[1]} rows")
    conn2.close()


if __name__ == "__main__":
    main()
