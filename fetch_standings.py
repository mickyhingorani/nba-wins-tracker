"""
Pull current NBA standings from ESPN's public feed, total wins per player
using picks.json, and write data/standings.json for the website.

Standard library only, so the GitHub Action needs no installs.
Run locally with:  python fetch_standings.py
Test offline with: python fetch_standings.py --from-file some_espn_standings.json
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ESPN_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
ROOT = Path(__file__).parent
PICKS_FILE = ROOT / "picks.json"
OUT_FILE = ROOT / "data" / "standings.json"


def load_espn(from_file=None):
    if from_file:
        return json.loads(Path(from_file).read_text())
    req = urllib.request.Request(ESPN_URL, headers={"User-Agent": "nba-wins-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


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


def main():
    from_file = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--from-file" else None
    picks = json.loads(PICKS_FILE.read_text())
    teams, espn_season = parse_teams(load_espn(from_file))

    # Fail loudly if a pick doesn't match ESPN, or a team is missing/duplicated.
    assigned = [a for lst in picks["players"].values() for a in lst] + picks["unpicked"]
    unknown = [a for a in assigned if a not in teams]
    dupes = sorted({a for a in assigned if assigned.count(a) > 1})
    if unknown or dupes:
        sys.exit(f"Bad picks.json: unknown={unknown} duplicates={dupes}")
    if len(teams) != 30:
        sys.exit(f"Expected 30 teams from ESPN, got {len(teams)}")

    players = [{"name": name, **build_group(abbrs, teams)} for name, abbrs in picks["players"].items()]
    players.sort(key=lambda p: (-p["wins"], p["losses"]))

    out = {
        "season": espn_season or picks.get("season", ""),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "players": players,
        "unpicked": build_group(picks["unpicked"], teams),
    }
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2) + "\n")

    for p in players:
        print(f"{p['name']:<6} {p['wins']:>3}-{p['losses']}")
    print(f"Unpicked {out['unpicked']['wins']}-{out['unpicked']['losses']}")


if __name__ == "__main__":
    main()
