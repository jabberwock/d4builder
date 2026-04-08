#!/usr/bin/env python3
"""
Fetch real D4 skill coefficients from DiabloTools/d4data (Season 12 game data)
and populate the skill_coefficients table in d4_stats.db.

Source: https://github.com/DiabloTools/d4data
Data format: .pow.json files with ptScriptFormulas containing damage expressions
Coefficient formula: SF_N = <coeff> * Table(34, sLevel)  →  % weapon damage = coeff * 100
"""

import json
import re
import sqlite3
import urllib.request
import urllib.error
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "d4_stats.db"
GITHUB_RAW = "https://raw.githubusercontent.com/DiabloTools/d4data/master"

# Map our display class names → D4 internal SkillKit file names
CLASS_SKILLKIT_MAP = {
    "barbarian":   "Barbarian",
    "druid":       "Druid",
    "necromancer": "Necromancer",
    "rogue":       "Rogue",
    "sorcerer":    "Sorcerer",
    "spiritborn":  "Spiritborn",
    "paladin":     "Paladin_NEW",  # Added Season 11; the file is named Paladin_NEW
}

# Map display skill name → D4 internal power name.
# Source: DiabloTools/d4data SkillKit files + d4planner skill data.
POWER_NAME_OVERRIDES = {
    # Barbarian (verified from Barbarian.skl.json)
    "Frenzy":                  "Barbarian_Frenzy",
    "Double Swing":            "Barbarian_DoubleSwing",
    "Hammer of the Ancients":  "Barbarian_HammeroftheAncients",   # lowercase 'o','t'
    "Whirlwind":               "Barbarian_Whirlwind",
    "Rend":                    "Barbarian_Rend",
    "Upheaval":                "Barbarian_Upheaval",
    "Lunging Strike":          "Barbarian_LungingStrike",
    "Steel Grasp":             "Barbarian_SteelGrasp",
    "Ground Stomp":            "Barbarian_GroundStomp",
    "Leap":                    "Barbarian_Leap",
    "Rallying Cry":            "Barbarian_RallyingCry",
    "Iron Skin":               "Barbarian_IronSkin",
    "War Cry":                 "Barbarian_WarCry",
    "Challenging Shout":       "Barbarian_ChallengingShout",
    "Wrath of the Berserker":  "Barbarian_WrathoftheBerserker",
    "Call of the Ancients":    "Barbarian_CalloftheAncients",
    "Iron Maelstrom":          "Barbarian_IronMaelstrom",
    # Druid (verified from Druid.skl.json)
    "Storm Strike":            "Druid_StormStrike",
    "Wind Shear":              "Druid_WindShear",
    "Maul":                    "Druid_Maul",
    "Shred":                   "Druid_Shred_NEW",           # renamed in patch
    "Pulverize":               "Druid_Pulverize",
    "Landslide":               "Druid_landslide",           # lowercase 'l'
    "Boulder":                 "Druid_Boulder",
    "Tornado":                 "Druid_Tornado",
    "Hurricane":               "Druid_Hurricane",
    "Lightning Storm":         "Druid_LightningStorm",
    "Cataclysm":               "Druid_Cataclysm",
    "Lacerate":                "Druid_Lacerate",
    "Ravens":                  "Druid_Ravens",
    "Wolves":                  "Druid_WolfPack",            # renamed
    "Blood Howl":              "Druid_BloodHowl",
    "Cyclone Armor":           "Druid_CycloneArmor",
    "Earthen Bulwark":         "Druid_EarthenBulwark",
    "Debilitating Roar":       "Druid_DebilitatingRoar",
    "Grizzly Rage":            "Druid_GrizzlyRage",
    "Petrify":                 "Druid_Petrify",
    # Necromancer (verified from Necromancer.skl.json)
    "Bone Splinters":          "Necromancer_BoneSplinters",
    "Bone Spear":              "Necromancer_BoneSpear",
    "Bone Storm":              "Necromancer_BoneStorm",
    "Blood Surge":             "Necromancer_BloodSurge",
    "Blood Lance":             "Necromancer_BloodLance",
    "Hemorrhage":              "Necromancer_Hemorrhage",
    "Decompose":               "Necromancer_Decompose",
    "Blight":                  "Necromancer_Blight",
    "Corpse Explosion":        "Necromancer_CorpseExplosion",
    "Corpse Tendrils":         "Necromancer_CorpseTendrils",
    "Bone Prison":             "Necromancer_BonePrison",
    "Blood Mist":              "Necromancer_BloodMist",
    "Decrepify":               "Necromancer_Decrepify",
    "Iron Maiden":             "Necromancer_IronMaiden",
    "Army of the Dead":        "Necromancer_ArmyoftheDead",
    "Golem":                   "Necromancer_Golem",
    "Skeletons":               "Necromancer_RaiseSkeleton",  # renamed
    # Rogue (verified from Rogue.skl.json)
    "Puncture":                "Rogue_Puncture",
    "Twisting Blades":         "Rogue_TwistingBlades",
    "Barrage":                 "Rogue_Barrage",
    "Flurry":                  "Rogue_Flurry",
    "Rain of Arrows":          "Rogue_RainofArrows",
    "Rapid Fire":              "Rogue_RapidFire",
    "Shadow Step":             "Rogue_ShadowStep",
    "Dash":                    "Rogue_Dash",
    "Poison Trap":             "Rogue_PoisonTrap",
    "Death Trap":              "Rogue_DeathTrap",
    "Smoke Grenade":           "Rogue_SmokeBomb",           # renamed
    "Shadow Clone":            "Rogue_ShadowClone",
    "Dark Shroud":             "Rogue_DarkShroud",
    "Concealment":             "Rogue_Stealth",             # renamed
    "Cold Imbuement":          "Rogue_ColdImbue",           # renamed
    "Shadow Imbuement":        "Rogue_ShadowImbue",         # renamed
    "Poison Imbuement":        "Rogue_PoisonImbue",         # renamed
    "Invigorating Strike":     "Rogue_InvigoratingStrike",
    "Blade Shift":             "Rogue_BladeShift",
    # Sorcerer (verified from Sorcerer.skl.json)
    "Fire Bolt":               "Sorcerer_FireBolt",
    "Fireball":                "Sorcerer_Fireball",
    "Firewall":                "Sorcerer_Firewall",
    "Incinerate":              "Sorcerer_Incinerate",
    "Meteor":                  "Sorcerer_Meteor",
    "Frost Bolt":              "Sorcerer_FrostBolt",
    "Ice Shards":              "Sorcerer_IceShards",
    "Frozen Orb":              "Sorcerer_FrozenOrb",
    "Blizzard":                "Sorcerer_Blizzard",
    "Arc Lash":                "Sorcerer_ArcLash",
    "Chain Lightning":         "Sorcerer_ChainLightning",
    "Ball Lightning":          "Sorcerer_BallLightning",
    "Lightning Spear":         "Sorcerer_LightningSpear",
    "Unstable Currents":       "Sorcerer_UnstableCurrents",
    "Deep Freeze":             "Sorcerer_DeepFreeze",
    "Inferno":                 "Sorcerer_Inferno",
    "Frost Nova":              "Sorcerer_FrostNova",
    "Ice Armor":               "Sorcerer_IceArmor",
    "Flame Shield":            "Sorcerer_FlameShield",
    "Teleport":                "Sorcerer_Teleport",
    "Hydra":                   "Sorcerer_Hydra",
    "Ice Blades":              "Sorcerer_IceBlades",
    # Spiritborn (verified from Spiritborn.skl.json + d4planner)
    "Rake":                    "Spiritborn_Jaguar_Core",
    "Thrash":                  "Spiritborn_Jaguar_Basic",
    "Stinger":                 "Spiritborn_Centipede_Core",
    "Thunderspike":            "Spiritborn_Eagle_Basic",
    "Soar":                    "Spiritborn_Eagle_Focus2",
    "Crushing Hand":           "Spiritborn_Gorilla_Core",
    "Scourge":                 "Spiritborn_Centipede_Defensive",
    "Stampede":                "Spiritborn_Gorilla_Ultimate",
    "Ravager":                 "Spiritborn_Jaguar_Focus",
    "The Devourer":            "Spiritborn_Centipede_Ultimate",
    "The Hunter":              "Spiritborn_Jaguar_Ultimate",
    "The Seeker":              "Spiritborn_Eagle_Ultimate",
    # Paladin (Season 11 class — Paladin_NEW.skl.json)
    "Brandish":                "Paladin_Brandish",
    "Blessed Hammer":          "Paladin_BlessedHammer",
    "Consecration":            "Paladin_Consecration",
    "Heaven's Fury":           "Paladin_HeavensFury",
    "Condemn":                 "Paladin_Condemn",
    "Spear of the Heavens":    "Paladin_SpearOfTheHeavens",   # capital O
    "Shield Charge":           "Paladin_ShieldCharge_Channel_Short",
    "Advance":                 "Paladin_Advance_lunge",
    "Purify":                  "Paladin_Purify",
    "Rally":                   "Paladin_Trinity",             # Trinity = rally-type buff
    "Holy Shield":             "Paladin_Fortress",            # Fortress = shield defensive
    "Blessed Shield":          "Paladin_BlessedShield",
    "Zeal":                    "Paladin_PreTrailZeal",
}

# Skills that are passives/buffs (no damage coefficient expected)
PASSIVE_SKILLS = {
    "Aggressive Resistance", "Booming Voice", "Furious Impulse", "Guttural Yell",
    "Heavy Handed", "Imposing Presence", "Invigorating Fury", "Martial Vigor",
    "No Mercy", "Pit Fighter", "Pressure Point", "Raid Leader",
    "Slaying Strike", "Tempered Fury", "Thick Skin",
    # Druid passives
    "Abundance", "Charged Atmosphere", "Clarity", "Crushing Earth", "Defiance",
    "Elemental Exposure", "Endless Tempest", "Ferocity", "Heart of the Wild",
    "Natural Disaster", "Nature's Reach", "Predatory Instinct", "Safeguard",
    "Survival Instincts", "Wild Impulses", "Wolves Passive — Heightened Senses",
    # Necromancer passives
    "Amplify Damage", "Compound Fracture", "Death's Embrace", "Death's Reach",
    "Fueled by Death", "Golem Mastery", "Grim Harvest", "Gruesome Mending",
    "Hellbent Commander", "Hewed Flesh", "Imperfect Resonance", "Imperfectly Balanced",
    "Inspiring Leader", "Memento Mori", "Serration", "Stand Alone", "Transfusion",
    "Unliving Energy",
    # Rogue passives
    "Adrenaline Rush", "Aftermath", "Agile", "Alchemical Advantage", "Consuming Shadows",
    "Deadly Venom", "Evasion", "Exploit", "Frigid Finesse", "Innervation",
    "Second Wind", "Shadow Crash", "Siphoning Strikes", "Sturdy", "Stutter Step",
    "Subverting Dark Shroud", "Trap Mastery", "Trick Attacks", "Unstable Elixirs",
    "Weapon Mastery",
    # Sorcerer passives
    "Align the Elements", "Conjuration Mastery", "Coursing Currents", "Devastation",
    "Frigid Breeze", "Inner Flames", "Icy Touch", "Snap Freeze", "Static Discharge",
    # Spiritborn passives
    "Acceleration", "Adaptive", "Apex", "Armored Hide", "Counterattack",
    "Crushing Advance", "Endurance", "Flourish", "Indomitable", "Ironclad",
    "Nourishment", "Payback", "Potent", "Resolute", "Toxicity", "Unrelenting",
    # Paladin passives
    "Aegis", "Arbiter of Justice", "Blessed Life", "Conviction", "Fanaticism",
    "Holy Fervor", "Might of the Faithful", "Zenith",
    # Paladin skills not in game data (may be renamed/missing from Season 11 dump)
    "Smite", "Holy Bolt", "Judgment", "Falling Star", "Divine Lance", "Divine Wrath",
    "Holy Light", "Phalanx",
    # Spiritborn passives/talents not in SkillKit as active skills
    "Crushing Advance", "Indomitable", "Viper Strike",
    # Rogue skills not directly available
    "Death from Above",
}

def fetch_json(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "d4builder/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt < retries - 1:
                time.sleep(1)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
    return None


def _get_formula_strings(pow_json: dict) -> list[str]:
    """Extract all formula strings from ptScriptFormulas array."""
    out = []
    for entry in pow_json.get("ptScriptFormulas", []):
        if isinstance(entry, dict):
            tf = entry.get("tFormula", {})
            if isinstance(tf, dict):
                v = tf.get("value", "")
            elif isinstance(tf, str):
                v = tf
            else:
                v = entry.get("value", "")
        elif isinstance(entry, str):
            v = entry
        else:
            v = ""
        out.append(v)
    return out


def extract_coefficient(pow_json: dict) -> float | None:
    """
    Extract the primary damage coefficient from a .pow.json file.

    D4 formula conventions (0-indexed):
      - SF_N in a formula string → ptScriptFormulas[N]
      - Raw damage:  "<coeff> * Table(34,sLevel)"  or  "SF_N * Table(34,sLevel)"
      - Tooltip:     "SF_N * 100 * Table(34,3)"    (hardcoded level 3, skip these)
    The in-game % weapon damage = coeff * 100.
    """
    formulas = _get_formula_strings(pow_json)
    if not formulas:
        return None

    # Build lookup: index → numeric constant for plain-number formulas
    sf_map: dict[int, float] = {}
    for i, f in enumerate(formulas):
        stripped = f.strip()
        try:
            sf_map[i] = float(stripped)
        except (ValueError, TypeError):
            pass

    # Only care about formulas referencing Table(34,sLevel) (raw damage)
    # Skip Table(34,3) / Table(34, 3) which are tooltip display variants
    raw_damage_re = re.compile(r'Table\s*\(\s*34\s*,\s*sLevel\s*\)', re.IGNORECASE)
    # Tooltip variant to skip
    tooltip_re = re.compile(r'Table\s*\(\s*34\s*,\s*\d+\s*\)', re.IGNORECASE)

    coefficients = []

    for formula_str in formulas:
        if not raw_damage_re.search(formula_str):
            continue
        # Skip tooltip formulas that also have hardcoded level like Table(34,3)
        if tooltip_re.search(formula_str.replace('sLevel', '')):
            continue

        # Try to extract multiplier before Table(34,sLevel)
        # Pattern A: <float> * Table(34,sLevel)
        direct = re.findall(r'([\d]+\.[\d]*|[\d]*\.[\d]+)\s*\*\s*Table\s*\(\s*34', formula_str)
        for v in direct:
            try:
                coefficients.append(float(v))
            except ValueError:
                pass

        # Pattern B: SF_N * Table(34,sLevel)  — look up SF_N
        sf_refs = re.findall(r'SF_(\d+)\s*\*\s*Table\s*\(\s*34', formula_str)
        for idx_str in sf_refs:
            idx = int(idx_str)
            if idx in sf_map:
                coefficients.append(sf_map[idx])

        # Pattern C: conditional with embedded coefficients
        # e.g. "? (4.5 * Table(34, sLevel))"
        cond_vals = re.findall(r'\(\s*([\d]+\.[\d]*|[\d]*\.[\d]+)\s*\*\s*Table\s*\(\s*34', formula_str)
        for v in cond_vals:
            try:
                coefficients.append(float(v))
            except ValueError:
                pass

    if not coefficients:
        return None

    # Primary damage = largest single-hit coefficient
    return max(coefficients)


def get_damage_bucket(pow_json: dict, class_name: str) -> str:
    """Determine the primary damage type from power tags."""
    tags = []
    # Check arPowerTagRelationship or similar fields
    for key in ("arPowerTagRelationship", "arSkillTagRelationship", "eSkillType"):
        val = pow_json.get(key)
        if val:
            tags.append(str(val).lower())

    # Check name-based hints
    name = pow_json.get("__fileName__", "").lower()

    if "fire" in name or "fireball" in name or "burn" in name:
        return "fire"
    if "ice" in name or "frost" in name or "cold" in name or "frozen" in name or "blizzard" in name:
        return "cold"
    if "lightning" in name or "arc" in name or "chain" in name or "ball_lightning" in name:
        return "lightning"
    if "shadow" in name or "bone" in name or "dark" in name:
        return "shadow"
    if "poison" in name or "blight" in name or "venom" in name:
        return "poison"
    if "blood" in name or "hemorrhage" in name:
        return "physical"
    if "holy" in name or "divine" in name or "consecrate" in name or "heaven" in name:
        return "holy"

    # Class defaults
    class_defaults = {
        "barbarian": "physical",
        "druid": "physical",
        "necromancer": "shadow",
        "rogue": "physical",
        "sorcerer": "fire",
        "spiritborn": "physical",
        "paladin": "holy",
    }
    return class_defaults.get(class_name, "physical")


def build_skill_list() -> list[dict]:
    """Build list of all skills from build JSON files."""
    import glob

    builds_dir = Path(__file__).parent.parent / "webapp" / "public" / "data" / "builds"
    skills_seen = {}  # (class, display_name) -> True

    for fpath in sorted(builds_dir.glob("*.json")):
        data = json.loads(fpath.read_text())
        cls = data.get("class", "").lower()
        for tier, items in data.get("skills", {}).items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and "name" in item:
                    key = (cls, item["name"])
                    skills_seen[key] = True

    result = []
    for (cls, name) in sorted(skills_seen.keys()):
        result.append({"class": cls, "display_name": name})
    return result


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Clear existing (fabricated) data
    cur.execute("DELETE FROM skill_coefficients")
    conn.commit()
    print("Cleared existing skill_coefficients rows")

    skills = build_skill_list()
    print(f"Found {len(skills)} unique skills across all builds\n")

    inserted = 0
    passive_skipped = 0
    not_found = 0
    no_coeff = 0

    for skill in skills:
        display_name = skill["display_name"]
        cls = skill["class"]

        # Skip pure passives — they don't have damage coefficients
        if display_name in PASSIVE_SKILLS:
            passive_skipped += 1
            continue

        # Get internal power name
        power_name = POWER_NAME_OVERRIDES.get(display_name)
        if not power_name:
            # Auto-generate: ClassName_SkillName (remove spaces, camelCase words)
            class_prefix = cls.capitalize()
            words = display_name.replace("'", "").replace("-", " ").split()
            internal = "".join(w.capitalize() for w in words)
            power_name = f"{class_prefix}_{internal}"

        url = f"{GITHUB_RAW}/json/base/meta/Power/{power_name}.pow.json"
        pow_json = fetch_json(url)

        if pow_json is None:
            print(f"  NOT FOUND: {display_name} ({power_name})")
            not_found += 1
            # Still insert with null coefficient so we know it was attempted
            cur.execute(
                "INSERT INTO skill_coefficients (skill_name, damage_bucket, coefficient, scaling_stats) VALUES (?,?,?,?)",
                (display_name, get_damage_bucket({}, cls), None, json.dumps({"class": cls, "power_name": power_name, "source": "not_found"}))
            )
            continue

        coeff = extract_coefficient(pow_json)
        bucket = get_damage_bucket(pow_json, cls)

        scaling = {
            "class": cls,
            "power_name": power_name,
            "source": "DiabloTools/d4data",
            "pct_weapon_damage": round(coeff * 100, 1) if coeff else None,
        }

        cur.execute(
            "INSERT INTO skill_coefficients (skill_name, damage_bucket, coefficient, scaling_stats) VALUES (?,?,?,?)",
            (display_name, bucket, coeff, json.dumps(scaling))
        )
        inserted += 1

        status = f"{coeff:.3f} ({coeff*100:.0f}%)" if coeff else "found, no coeff"
        print(f"  {cls:12} {display_name:30} {status}")

        time.sleep(0.05)  # be polite to GitHub

    conn.commit()
    conn.close()

    print(f"\n{'='*60}")
    print(f"Inserted:         {inserted}")
    print(f"Passive (skipped):{passive_skipped}")
    print(f"Not found:        {not_found}")
    print(f"Found, no coeff:  {no_coeff}")
    print(f"\nDB: {DB_PATH}")


if __name__ == "__main__":
    main()
