#!/usr/bin/env python3
"""
FIFA Team Builder & Formation Recommender (Model 2.0)

What's new:
- Players can have MULTIPLE roles (e.g., FB can be 'balanced, fast').
- Added formation: '3-4-2-1 Bayern' with your fit rules.
- After finishing, prompts to run again for a new team.

Positions: CB, FB, CM, CAM, Winger, ST
Roles by position:
  CB: normal, rugged, ball playing
  FB: balanced, fast, technical
  CM: defensive, balanced, technical, road runner
  CAM: btl, balanced, roaming playmaker, muller type
  Winger: wide, interior, balanced, wide playmaker
  ST: balanced, target man, false 9, FAST   (FAST remains uppercase by design)

Formations considered:
  - 3-4-2-1 assymetric
  - 4-2-3-1
  - 4-3-3(invert)
  - 3-4-2-1 Bayern
"""

import sqlite3
import os
import sys
from collections import Counter, defaultdict

DB_PATH = "fifa_team.db"

POSITIONS = ["CB", "FB", "CM", "CAM", "Winger", "ST"]

ROLE_OPTIONS = {
    "CB": ["normal", "rugged", "ball playing"],
    "FB": ["balanced", "fast", "technical"],
    "CM": ["defensive", "balanced", "technical", "road runner"],
    "CAM": ["btl", "balanced", "roaming playmaker", "muller type"],
    "Winger": ["wide", "interior", "balanced", "wide playmaker"],
    "ST": ["balanced", "target man", "false 9", "FAST"],  # keep FAST uppercase as specified
}

# ---------------------------
# SQLite helpers
# ---------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
      CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
      );
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        position TEXT NOT NULL,
        role TEXT NOT NULL,  -- comma-joined summary of roles for convenience
        FOREIGN KEY(team_id) REFERENCES teams(id)
      );
    """)
    # New in v2.0: normalized roles
    c.execute("""
      CREATE TABLE IF NOT EXISTS player_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        FOREIGN KEY(player_id) REFERENCES players(id)
      );
    """)
    conn.commit()
    return conn

def save_team(conn, team_name, players):
    c = conn.cursor()
    c.execute("INSERT INTO teams (name) VALUES (?);", (team_name,))
    team_id = c.lastrowid
    for p in players:
        role_summary = ", ".join(p["roles"])
        c.execute(
            "INSERT INTO players (team_id, name, position, role) VALUES (?, ?, ?, ?);",
            (team_id, p["name"], p["position"], role_summary),
        )
        player_id = c.lastrowid
        c.executemany(
            "INSERT INTO player_roles (player_id, role) VALUES (?, ?);",
            [(player_id, r) for r in p["roles"]]
        )
    conn.commit()
    return team_id

# ---------------------------
# Input helpers
# ---------------------------
def canonicalize_role(candidate, position):
    """
    Match candidate text to a canonical role in ROLE_OPTIONS[position],
    case-insensitive, ignoring extra spaces and hyphen variations.
    Handles ST 'FAST' special casing.
    """
    cand = candidate.strip().lower().replace("-", " ").replace("_", " ")
    # Special aliasing
    aliases = {
        "ballplaying": "ball playing",
        "ball playing": "ball playing",
        "bp": "ball playing",
        "roaming": "roaming playmaker",
        "rp": "roaming playmaker",
        "false9": "false 9",
        "false-9": "false 9",
        "muller": "muller type",
        "roadrunner": "road runner",
        "wideplaymaker": "wide playmaker",
        "wide-playmaker": "wide playmaker",
    }
    if cand in aliases:
        cand = aliases[cand]

    for ch in ROLE_OPTIONS[position]:
        if cand == ch.lower():
            # enforce ST FAST uppercase
            if position == "ST" and ch.lower() == "fast":
                return "FAST"
            return ch
    return None

def prompt_choice(prompt, choices):
    choices_disp = ", ".join(choices)
    while True:
        ans = input(f"{prompt} [{choices_disp}]: ").strip()
        normalized = ans.lower()
        for ch in choices:
            if normalized == ch.lower():
                return ch
        print(f"  -> Invalid choice. Please choose one of: {choices_disp}")

def prompt_roles(position):
    """
    Allow one or multiple roles for a given position.
    Example input: "balanced, fast" or "fast" or "technical/fast"
    """
    options = ROLE_OPTIONS[position]
    print(f"Available roles for {position}: {', '.join(options)}")
    while True:
        raw = input("Enter role(s) (one or more, separated by commas): ").strip()
        raw = raw.replace("/", ",")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        selected = []
        for part in parts:
            canon = canonicalize_role(part, position)
            if canon and canon not in selected:
                selected.append(canon)
        if selected:
            return selected
        print("  -> Please enter at least one valid role from the list above.")

def prompt_player(i):
    name = input(f"Enter player {i} name: ").strip()
    if not name:
        name = f"Player {i}"
    position = prompt_choice(f"Enter {name}'s position", POSITIONS)
    roles = prompt_roles(position)
    return {"name": name, "position": position, "roles": roles}

# ---------------------------
# Counting utilities
# ---------------------------
def roster_counters(players):
    """
    counts[pos][role] = number of players who have that role at that position.
    Multi-role players increment multiple buckets (no double-count per same role).
    """
    counts = defaultdict(Counter)
    for p in players:
        pos = p["position"]
        # de-dup in case user typed same role twice
        for role in sorted(set(p["roles"]), key=str.lower):
            counts[pos][role] += 1
    return counts

def count_pos_role(counts, pos, role):
    return counts[pos][role] if pos in counts else 0

def sum_pos(counts, pos):
    return sum(counts[pos].values()) if pos in counts else 0

def any_cm_not(counts, excluded_role):
    for role, n in counts.get("CM", {}).items():
        if role.lower() != excluded_role.lower() and n > 0:
            return True
    return False

def any_winger_not(counts, excluded_role):
    for role, n in counts.get("Winger", {}).items():
        if role.lower() != excluded_role.lower() and n > 0:
            return True
    return False

def other_fb_exists(counts):
    return sum_pos(counts, "FB") >= 2

# ---------------------------
# Formation scoring
# ---------------------------
def score_3412_asym(counts):
    """
    3-4-2-1 assymetric:
      - 1 fast FB
      - 1 wide winger
      - 1 defensive CM, 1 technical CM
      - (2 btl CAM and 1 roaming playmaker) or 3 btl CAM
      - 2 ball playing CB
    """
    score = 0
    reasons = []

    # fast FB
    if count_pos_role(counts, "FB", "fast") >= 1:
        score += 2
        reasons.append("Has a fast FB (+2)")

    # wide winger
    if count_pos_role(counts, "Winger", "wide") >= 1:
        score += 2
        reasons.append("Has a wide winger (+2)")

    # CM defensive + technical
    has_def = count_pos_role(counts, "CM", "defensive") >= 1
    has_tech = count_pos_role(counts, "CM", "technical") >= 1
    if has_def and has_tech:
        score += 3
        reasons.append("CM mix: defensive + technical (+3)")
    elif has_def or has_tech:
        score += 1
        reasons.append("Partial CM mix (have one of defensive/technical) (+1)")

    # CAM combos
    btl = count_pos_role(counts, "CAM", "btl")
    roam = count_pos_role(counts, "CAM", "roaming playmaker")
    if btl >= 2 and roam >= 1:
        score += 3
        reasons.append("CAM combo: 2×BTL + 1×Roaming Playmaker (+3)")
    elif btl >= 3:
        score += 3
        reasons.append("CAM trio: 3×BTL (+3)")
    else:
        partial = 0
        if btl >= 1: partial += 1
        if roam >= 1: partial += 1
        if partial:
            score += partial
            reasons.append(f"Partial CAM fit (BTL={btl}, RP={roam}) (+{partial})")

    # CB ball playing
    bp_cb = count_pos_role(counts, "CB", "ball playing")
    if bp_cb >= 2:
        score += 3
        reasons.append("2× ball playing CB (+3)")
    elif bp_cb == 1:
        score += 1
        reasons.append("1× ball playing CB (+1)")

    return score, reasons

def score_4231(counts):
    """
    4-2-3-1:
      - 1 roaming playmaker CAM
      - 2 interior wingers or (1 interior, 1 balanced)
      - (1 technical CM, 1 defensive CM) or 2 technical CM
      - 1 fast FB
      - 1 ball playing CB
    """
    score = 0
    reasons = []

    # roaming CAM
    if count_pos_role(counts, "CAM", "roaming playmaker") >= 1:
        score += 3
        reasons.append("Has a roaming playmaker CAM (+3)")

    # wingers
    interior = count_pos_role(counts, "Winger", "interior")
    balanced_w = count_pos_role(counts, "Winger", "balanced")
    if interior >= 2 or (interior >= 1 and balanced_w >= 1):
        score += 3
        reasons.append("Wingers fit (interior/balanced) (+3)")
    else:
        part = 0
        if interior >= 1: part += 2
        if balanced_w >= 1: part += 1
        if part:
            score += part
            reasons.append(f"Partial winger fit (interior={interior}, balanced={balanced_w}) (+{part})")

    # CMs
    tech = count_pos_role(counts, "CM", "technical")
    defc = count_pos_role(counts, "CM", "defensive")
    if (tech >= 1 and defc >= 1) or tech >= 2:
        score += 3
        reasons.append("CM setup fits (tech/def) (+3)")
    else:
        part = 0
        if tech >= 1: part += 2
        if defc >= 1: part += 1
        if part:
            score += part
            reasons.append(f"Partial CM fit (tech={tech}, def={defc}) (+{part})")

    # fast FB
    if count_pos_role(counts, "FB", "fast") >= 1:
        score += 2
        reasons.append("Has a fast FB (+2)")

    # ball playing CB
    if count_pos_role(counts, "CB", "ball playing") >= 1:
        score += 2
        reasons.append("Has a ball playing CB (+2)")

    return score, reasons

def score_433_invert(counts):
    """
    4-3-3 (invert):
      - 2 wide wingers or (1 wide, 1 winger that isn't 'wide playmaker')
      - 2 road runner CM or (1 road runner + any other CM that's not 'defensive')
      - CAM allowed if role is btl or balanced (nice-to-have)
      - 2 technical FB, or (1 technical FB and FB with any other role)
    """
    score = 0
    reasons = []

    # Wingers condition
    wide = count_pos_role(counts, "Winger", "wide")
    not_wide_playmaker_exists = any_winger_not(counts, "wide playmaker")
    if wide >= 2 or (wide >= 1 and not_wide_playmaker_exists):
        score += 3
        reasons.append("Winger profiles fit inverted 4-3-3 (+3)")
    else:
        part = 0
        if wide >= 1: part += 2
        if not_wide_playmaker_exists: part += 1
        if part:
            score += part
            reasons.append("Partial winger fit for 4-3-3(invert) (+{})".format(part))

    # CM runners
    rr = count_pos_role(counts, "CM", "road runner")
    cm_non_def = any_cm_not(counts, "defensive")
    if rr >= 2 or (rr >= 1 and cm_non_def):
        score += 3
        reasons.append("Midfield suits high-tempo (road runners) (+3)")
    else:
        part = 0
        if rr >= 1: part += 2
        if cm_non_def: part += 1
        if part:
            score += part
            reasons.append("Partial CM tempo fit (+{})".format(part))

    # CAM permitted
    cam_btl = count_pos_role(counts, "CAM", "btl")
    cam_bal = count_pos_role(counts, "CAM", "balanced")
    if cam_btl + cam_bal >= 1:
        score += 1
        reasons.append("CAM acceptable type present (+1)")

    # FB technical
    tech_fb = count_pos_role(counts, "FB", "technical")
    if tech_fb >= 2 or (tech_fb >= 1 and other_fb_exists(counts)):
        score += 3
        reasons.append("Fullbacks support inverted play (+3)")
    else:
        part = 0
        if tech_fb >= 1: part += 2
        if other_fb_exists(counts): part += 1
        if part:
            score += part
            reasons.append("Partial FB fit for inversion (+{})".format(part))

    return score, reasons

def score_3412_bayern(counts):
    """
    3-4-2-1 Bayern:
      - 1 fast FB
      - 2 interior wingers or (1 interior + 1 balanced)
      - (1 technical CM, 1 defensive CM) or 2 technical CM
      - 1 btl CAM or 1 muller type CAM
      - 1 ball playing CB, 1 rugged CB
    """
    score = 0
    reasons = []

    # fast FB
    if count_pos_role(counts, "FB", "fast") >= 1:
        score += 2
        reasons.append("Has a fast FB (+2)")

    # wingers
    interior = count_pos_role(counts, "Winger", "interior")
    balanced_w = count_pos_role(counts, "Winger", "balanced")
    if interior >= 2 or (interior >= 1 and balanced_w >= 1):
        score += 3
        reasons.append("Wingers fit Bayern (interior/balanced) (+3)")
    else:
        part = 0
        if interior >= 1: part += 2
        if balanced_w >= 1: part += 1
        if part:
            score += part
            reasons.append(f"Partial winger fit (interior={interior}, balanced={balanced_w}) (+{part})")

    # CMs
    tech = count_pos_role(counts, "CM", "technical")
    defc = count_pos_role(counts, "CM", "defensive")
    if (tech >= 1 and defc >= 1) or tech >= 2:
        score += 3
        reasons.append("CM setup fits (tech/def) (+3)")
    else:
        part = 0
        if tech >= 1: part += 2
        if defc >= 1: part += 1
        if part:
            score += part
            reasons.append(f"Partial CM fit (tech={tech}, def={defc}) (+{part})")

    # CAM: btl OR muller type
    cam_btl = count_pos_role(counts, "CAM", "btl")
    cam_muller = count_pos_role(counts, "CAM", "muller type")
    if cam_btl >= 1 or cam_muller >= 1:
        score += 2
        reasons.append("Has CAM (BTL or Müller type) (+2)")
    else:
        # tiny partial credit if CAM exists at all
        cam_any = sum_pos(counts, "CAM")
        if cam_any >= 1:
            score += 1
            reasons.append("Some CAM presence (+1)")

    # CBs: 1 ball playing AND 1 rugged
    bp = count_pos_role(counts, "CB", "ball playing")
    rg = count_pos_role(counts, "CB", "rugged")
    if bp >= 1 and rg >= 1:
        score += 3
        reasons.append("CB mix: ball playing + rugged (+3)")
    else:
        part = 0
        if bp >= 1: part += 1
        if rg >= 1: part += 1
        if part:
            score += part
            reasons.append(f"Partial CB mix (bp={bp}, rugged={rg}) (+{part})")

    return score, reasons

# ---------------------------
# Reporting
# ---------------------------
def suggest_formation(players):
    counts = roster_counters(players)
    s1, r1 = score_3412_asym(counts)
    s2, r2 = score_4231(counts)
    s3, r3 = score_433_invert(counts)
    s4, r4 = score_3412_bayern(counts)

    results = [
        ("3-4-2-1 assymetric", s1, r1),
        ("4-2-3-1", s2, r2),
        ("4-3-3(invert)", s3, r3),
        ("3-4-2-1 Bayern", s4, r4),
    ]
    results.sort(key=lambda x: x[1], reverse=True)

    best = results[0]
    return best, results

def print_summary(team_name, players, best, results):
    print("\n" + "="*72)
    print(f"Team: {team_name}")
    print("-"*72)
    for p in players:
        print(f"{p['name']:<18}  {p['position']:<7}  roles: {', '.join(p['roles'])}")
    print("-"*72)
    print("Formation fit scores:")
    for name, score, reasons in results:
        print(f"  {name:<20} -> {score} pts")
        for rr in reasons:
            print(f"     - {rr}")
    print("-"*72)
    print(f"Recommended formation: \033[1m{best[0]}\033[0m (score: {best[1]})")
    top_reasons = best[2][:3] if best[2] else []
    if top_reasons:
        print("Why this fits:")
        for rr in top_reasons:
            print(f"  • {rr}")
    print("="*72 + "\n")

# ---------------------------
# One run (collect->save->suggest)
# ---------------------------
def one_run():
    print("=== FIFA Team Builder & Formation Recommender — Model 2.0 ===\n")
    print("Enter your team details. You'll add 10 outfield players (no goalkeeper).")
    team_name = input("Team name: ").strip() or "My FIFA Team"  
    
#Silas added this new part
    team_name_capitalized = team_name.capitalize()
    


#Silas added this new part 
    if team_name_capitalized == "Bayern":                       
        print("Best formation fit is: 3-4-2-1 bayern")

   
    else:

#Silas stopped here

        players = []
        for i in range(1, 11):
            print(f"\n--- Player {i} of 10 ---")
            p = prompt_player(i)
            players.append(p)

        # Save to DB
        conn = init_db()
        team_id = save_team(conn, team_name, players)
        conn.close()

        best, results = suggest_formation(players)
        print_summary(team_name, players, best, results)
        print(f"Saved to database: {os.path.abspath(DB_PATH)} (team id: {team_id})")

# ---------------------------
# Main loop with "try a new team?"
# ---------------------------
def main():
    while True:
        try:
            one_run()
        except KeyboardInterrupt:
            print("\nCanceled by user.")
            sys.exit(1)

        again = input("Do you want to try this for a new team? (y/n): ").strip().lower()
        if again not in ("y", "yes"):
            print("Alright—good luck with your squad! 👊")
            break
        print("\nRestarting...\n")

if __name__ == "__main__":
    main()
