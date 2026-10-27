"""
Pull NBA standings, team colors and daily results from ESPN's public feed,
total wins per player using picks.json, and write:
  data/standings.json   season totals per player and team
  data/last_night.json  yesterday's games, tagged with owners
  data/history.json     wins per player for every day of the season (for the race chart)
  data/tonight.json     today's schedule, tagged with owners (the page updates it live from ESPN)

Standard library only, so the GitHub Action needs no installs.
Run:           python3 fetch_standings.py
Test offline:  python3 fetch_standings.py --fixtures some_dir --today 2026-10-26
               (some_dir holds standings.json, teams.json, scoreboard_YYYYMMDD.json)
"""

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ESPN = "https://site.api.espn.com/apis"
STANDINGS_URL = f"{ESPN}/v2/sports/basketball/nba/standings"
TEAMS_URL = f"{ESPN}/site/v2/sports/basketball/nba/teams"
SCOREBOARD_URL = f"{ESPN}/site/v2/sports/basketball/nba/scoreboard?dates={{d}}"

ROOT = Path(__file__).parent
PICKS_FILE = ROOT / "picks.json"
DATA_DIR = ROOT / "data"
UNPICKED = "Unpicked"


class Source:
    """Fetches from ESPN, or from saved files when testing."""

    def __init__(self, fixtures=None):
        self.fixtures = Path(fixtures) if fixtures else None

    def _get(self, url, fixture_name):
        if self.fixtures:
            path = self.fixtures / fixture_name
            return json.loads(path.read_text()) if path.exists() else {"events": []}
        req = urllib.request.Request(url, headers={"User-Agent": "nba-wins-tracker/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def standings(self):
        return self._get(STANDINGS_URL, "standings.json")

    def teams(self):
        return self._get(TEAMS_URL, "teams.json")

    def scoreboard(self, day):
        d = day.strftime("%Y%m%d")
        return self._get(SCOREBOARD_URL.format(d=d), f"scoreboard_{d}.json")


def eastern_today():
    """Today's date in US Eastern time. ESPN files games under the US date."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write(name, obj):
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / name).write_text(json.dumps(obj, indent=2) + "\n")


# ---------- standings ----------

def parse_colors(espn_teams):
    colors = {}
    try:
        for t in espn_teams["sports"][0]["leagues"][0]["teams"]:
            team = t["team"]
            colors[team["abbreviation"]] = {
                "color": "#" + team.get("color", "888888"),
                "alt": "#" + team.get("alternateColor", "cccccc"),
            }
    except (KeyError, IndexError):
        pass  # colors are nice-to-have; the page falls back to grey
    return colors


def parse_teams(espn, colors):
    """Return {abbr: {...}} for every team in the standings feed."""
    teams = {}
    for conference in espn["children"]:
        for entry in conference["standings"]["entries"]:
            team = entry["team"]
            stats = {s.get("name"): s for s in entry["stats"]}
            logos = team.get("logos") or [{}]
            abbr = team["abbreviation"]
            teams[abbr] = {
                "abbr": abbr,
                "name": team["displayName"],
                "short": team.get("shortDisplayName", team["displayName"]),
                "logo": logos[0].get("href", ""),
                "wins": int(stats["wins"]["value"]),
                "losses": int(stats["losses"]["value"]),
                **colors.get(abbr, {"color": "#888888", "alt": "#cccccc"}),
            }
    return teams, espn["children"][0]["standings"].get("seasonDisplayName", "")


def build_group(abbrs, teams):
    rows = sorted((teams[a] for a in abbrs), key=lambda t: (-t["wins"], t["losses"]))
    return {
        "wins": sum(t["wins"] for t in rows),
        "losses": sum(t["losses"] for t in rows),
        "teams": rows,
    }


# ---------- games ----------

def parse_scoreboard(espn, owners, teams):
    games = []
    for ev in espn.get("events", []):
        # Regular season only: ESPN tags each game with a season type (2 = regular season;
        # preseason, play-in and playoff games carry other types).
        season_type = (ev.get("season") or {}).get("type")
        if season_type is not None and season_type != 2:
            continue
        status = ev["status"]["type"]
        state = status.get("state", "")  # "pre", "in" or "post"
        side = {}
        for c in ev["competitions"][0]["competitors"]:
            abbr = c["team"]["abbreviation"]
            score = c.get("score")
            side[c["homeAway"]] = {
                "abbr": abbr,
                "name": c["team"].get("displayName", abbr),
                "short": c["team"].get("shortDisplayName", abbr),
                "logo": c["team"].get("logo", ""),
                "score": int(score) if str(score).isdigit() and state != "pre" else None,
                "winner": bool(c.get("winner")),
                "record": next((r["summary"] for r in c.get("records", []) if r.get("type") == "total"), ""),
                "owner": owners.get(abbr, UNPICKED),
                "color": teams.get(abbr, {}).get("color", "#888888"),
                "alt": teams.get(abbr, {}).get("alt", "#cccccc"),
            }
        final = bool(status.get("completed"))
        a, h = side["away"], side["home"]
        # Fallback: if ESPN ever omits the winner flag on a final, use the score.
        if final and not (a["winner"] or h["winner"]) and a["score"] is not None and h["score"] is not None:
            a["winner"], h["winner"] = a["score"] > h["score"], h["score"] > a["score"]
        games.append({"start": ev["date"], "final": final, "state": state,
                      "status": status.get("shortDetail", ""), "away": a, "home": h})
    games.sort(key=lambda g: g["start"])
    return games


def head_to_head(games, players):
    """Games where two different people's teams met: {"A|B": [A's wins, B's wins]}, names in picks order."""
    out = {}
    for g in games:
        a, h = g["away"], g["home"]
        if not g["final"] or a["owner"] == h["owner"] or a["owner"] not in players or h["owner"] not in players:
            continue
        x, y = sorted((a, h), key=lambda t: players.index(t["owner"]))
        rec = out.setdefault(f'{x["owner"]}|{y["owner"]}', [0, 0])
        rec[0 if x["winner"] else 1] += 1
    return out


def tally(games, names):
    out = {n: {"wins": 0, "losses": 0} for n in names}
    for g in games:
        if g["final"]:
            for t in (g["away"], g["home"]):
                out[t["owner"]]["wins" if t["winner"] else "losses"] += 1
    return out


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", help="read ESPN data from this folder instead of the web")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD (testing)")
    args = ap.parse_args()

    src = Source(args.fixtures)
    picks = json.loads(PICKS_FILE.read_text())
    names = list(picks["players"]) + [UNPICKED]
    owners = {abbr: name for name, abbrs in picks["players"].items() for abbr in abbrs}

    teams, espn_season = parse_teams(src.standings(), parse_colors(src.teams()))

    # Fail loudly if a pick doesn't match ESPN, or a team is missing/duplicated.
    assigned = [a for lst in picks["players"].values() for a in lst] + picks["unpicked"]
    unknown = [a for a in assigned if a not in teams]
    dupes = sorted({a for a in assigned if assigned.count(a) > 1})
    if unknown or dupes:
        sys.exit(f"Bad picks.json: unknown={unknown} duplicates={dupes}")
    if len(teams) != 30:
        sys.exit(f"Expected 30 teams from ESPN, got {len(teams)}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = date.fromisoformat(args.today) if args.today else eastern_today()
    yesterday = today - timedelta(days=1)
    season_end = date.fromisoformat(picks["season_end"]) if picks.get("season_end") else None

    history = read_json(DATA_DIR / "history.json", {"days": {}})
    days = history.setdefault("days", {})
    h2h_days = history.setdefault("h2h", {})
    player_names = list(picks["players"])

    # Season over: once the last regular-season night is recorded and standings are marked
    # final, leave everything exactly as it is.
    if season_end and yesterday > season_end and season_end.isoformat() in days \
            and read_json(DATA_DIR / "standings.json", {}).get("final"):
        print(f"Regular season ended {season_end}; standings are final. Nothing to do.")
        return

    # The most recent night to record: last night, but never past the end of the regular season.
    night = min(yesterday, season_end) if season_end else yesterday

    # --- Daily history: fetch any finished day we don't have yet ---
    start = date.fromisoformat(picks["season_start"])
    last_night_games = None
    d = start
    while d <= night:
        key = d.isoformat()
        # Re-fetch a day if it's new, if it's last night, or if it predates head-to-head tracking.
        if key not in days or key not in h2h_days or d == night:
            games = parse_scoreboard(src.scoreboard(d), owners, teams)
            if d == night:
                last_night_games = games
            if all(g["final"] for g in games):  # only store a day once it's complete
                days[key] = {n: t["wins"] for n, t in tally(games, names).items()}
                h2h_days[key] = head_to_head(games, player_names)
        d += timedelta(days=1)
    history["days"] = dict(sorted(days.items()))
    history["h2h"] = dict(sorted(h2h_days.items()))
    history["players"] = names

    # --- Season standings ---
    players = [{"name": name, **build_group(abbrs, teams)} for name, abbrs in picks["players"].items()]
    players.sort(key=lambda p: (-p["wins"], p["losses"]))
    unpicked = build_group(picks["unpicked"], teams)

    # Sanity check: daily history should add up to the standings.
    totals = {n: sum(day.get(n, 0) for day in history["days"].values()) for n in names}
    expected = {p["name"]: p["wins"] for p in players} | {UNPICKED: unpicked["wins"]}
    if totals != expected:
        print(f"Note: history totals {totals} differ from standings {expected} "
              "(normal if a game was still in progress).")

    final = bool(season_end and season_end.isoformat() in history["days"] and yesterday >= season_end)
    write("standings.json", {"season": espn_season or picks.get("season", ""), "updated": now,
                             "final": final, "players": players, "unpicked": unpicked})
    write("history.json", history)
    write("last_night.json", {"date": night.isoformat(), "updated": now,
                              "games": last_night_games or [],
                              "haul": tally(last_night_games or [], names)})

    # Tonight's schedule: a morning snapshot the page falls back on if it can't reach ESPN live.
    tonight_games = [] if (season_end and today > season_end) else parse_scoreboard(src.scoreboard(today), owners, teams)
    write("tonight.json", {"date": today.isoformat(), "updated": now, "games": tonight_games})

    for p in players:
        print(f"{p['name']:<8} {p['wins']:>3}-{p['losses']}")
    print(f"History: {len(history['days'])} days; last night ({night}): {len(last_night_games or [])} games"
          + ("; regular season complete, standings final" if final else ""))


if __name__ == "__main__":
    main()
