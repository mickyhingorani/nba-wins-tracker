# NBA Wins Tracker

Four friends, seven teams each, most regular-season wins takes it.

- `picks.json`: who has which teams (ESPN abbreviations). Edit this to change picks.
- `fetch_standings.py`: pulls standings from ESPN's public feed and writes `data/standings.json`.
- `.github/workflows/update-standings.yml`: runs the script every morning (~8:20am ET) and commits the result.
- `index.html`: the page. Reads `data/standings.json`.

Run it by hand any time: **Actions → Update NBA standings → Run workflow**.
