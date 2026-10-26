"""
Pull current NBA standings and last night's games from ESPN's public feed,
total wins per player using picks.json, and write:
  data/standings.json   season totals
  data/last_night.json  yesterday's games, tagged with owners

Standard library only, so the GitHub Action needs no installs.
Run locally with:  python3 fetch_standings.py
Test offline with: python3 fetch_standings.py --standings-file a.json --scoreboard-file b.json
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date}"
ROOT = Path(__file__).parent
PICKS_FILE = ROOT / "picks.json"
DATA_DIR = ROOT / "data"
UNPICKED = "Unpicked"


def get_json(url, from_file=None):
    if from_file:
        return json.loads(Path(from_file).read_text())
    req = urllib.request.Request(url, headers={"User-Agent": "nba-wins-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def eastern_yesterday():
    """Yesterday's date in US Eastern time. ESPN files games under the US date."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.now(timezone.utc) - timedelta(hours=5)
    return (now - timedelta(days=1)).date()


# ---------- standings ----------

def parse_teams(espn):
    """Return {abbr: {name, logo, wins, losses}} for every team in the feed."""
    teams = {}
    for conference in espn["children"]:
        for entry in conference["standings"]["entries"]:
            team = entry["team"]
            stats = {s.get("name"): s for s in entry["stats"]}
            logos = team.get("logos") or [{}]
            teams[team["abbreviation"]] = {
                "abbr": team["abbreviation"],
                "name": team["displayName"],
                "logo": logos[0].get("href", ""),
                "wins": int(stats["wins"]["value"]),
                "losses": int(stats["losses"]["value"]),
            }
    return teams, espn["children"][0]["standings"].get("seasonDisplayName", "")


def build_group(abbrs, teams):
    rows = sorted((teams[a] for a in abbrs), key=lambda t: (-t["wins"], t["losses"]))
    return {
        "wins": sum(t["wins"] for t in rows),
        "losses": sum(t["losses"] for t in rows),
        "teams": rows,
    }


# ---------- last night ----------

def parse_scoreboard(espn, owners):
    games = []
    for ev in espn.get("events", []):
        status = ev["status"]["type"]
        side = {}
        for c in ev["competitions"][0]["competitors"]:
            abbr = c["team"]["abbreviation"]
            record = next((r["summary"] for r in c.get("records", []) if r.get("type") == "total"), "")
            side[c["homeAway"]] = {
                "abbr": abbr,
                "name": c["team"]["displayName"],
                "logo": c["team"].get("logo", ""),
                "score": int(c["score"]) if str(c.get("score", "")).isdigit() else None,
                "winner": bool(c.get("winner")),
                "record": record,
                "owner": owners.get(abbr, UNPICKED),
            }
        games.append({
            "start": ev["date"],
            "final": bool(status.get("completed")),
            "status": status.get("shortDetail", ""),
            "away": side["away"],
            "home": side["home"],
        })
    games.sort(key=lambda g: g["start"])
    return games


def build_haul(games, player_names):
    haul = {name: {"wins": 0, "losses": 0} for name in player_names + [UNPICKED]}
    for g in games:
        if not g["final"]:
            continue
        for t in (g["away"], g["home"]):
            haul[t["owner"]]["wins" if t["winner"] else "losses"] += 1
    return haul


def write(name, obj):
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / name).write_text(json.dumps(obj, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--standings-file")
    ap.add_argument("--scoreboard-file")
    ap.add_argument("--date", help="YYYY-MM-DD for 'last night' (default: yesterday, Eastern)")
    args = ap.parse_args()

    picks = json.loads(PICKS_FILE.read_text())
    teams, espn_season = parse_teams(get_json(STANDINGS_URL, args.standings_file))

    # Fail loudly if a pick doesn't match ESPN, or a team is missing/duplicated.
    assigned = [a for lst in picks["players"].values() for a in lst] + picks["unpicked"]
    unknown = [a for a in assigned if a not in teams]
    dupes = sorted({a for a in assigned if assigned.count(a) > 1})
    if unknown or dupes:
        sys.exit(f"Bad picks.json: unknown={unknown} duplicates={dupes}")
    if len(teams) != 30:
        sys.exit(f"Expected 30 teams from ESPN, got {len(teams)}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Season standings
    players = [{"name": name, **build_group(abbrs, teams)} for name, abbrs in picks["players"].items()]
    players.sort(key=lambda p: (-p["wins"], p["losses"]))
    write("standings.json", {
        "season": espn_season or picks.get("season", ""),
        "updated": now,
        "players": players,
        "unpicked": build_group(picks["unpicked"], teams),
    })

    # Last night's games
    owners = {abbr: name for name, abbrs in picks["players"].items() for abbr in abbrs}
    night = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else eastern_yesterday()
    scoreboard = get_json(SCOREBOARD_URL.format(date=night.strftime("%Y%m%d")), args.scoreboard_file)
    games = parse_scoreboard(scoreboard, owners)
    write("last_night.json", {
        "date": night.isoformat(),
        "updated": now,
        "games": games,
        "haul": build_haul(games, list(picks["players"])),
    })

    for p in players:
        print(f"{p['name']:<8} {p['wins']:>3}-{p['losses']}")
    print(f"Last night ({night}): {len(games)} games")


if __name__ == "__main__":
    main()
