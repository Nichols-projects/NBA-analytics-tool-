"""Save DraftKings player-prop lines shortly before each NBA tip-off (closing lines).

Runs every 15 minutes in the evening via .github/workflows/closing-lines.yml. For each game that
tips within the next 45 minutes it records the current DraftKings Over/Under line for every player
prop (via ESPN, DraftKings is ESPN's odds provider) into closing-lines.json. The last snapshot
before tip is the closing line, which the site's tracker compares against your bets.
No API keys needed. Standard library only.
"""
import json, os, urllib.request
from datetime import datetime, timedelta, timezone

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
MARKETS = {
    "Total Points": "pts", "Total Rebounds": "reb", "Total Assists": "ast", "Total 3-Point Field Goals": "fg3",
    "Total Points, Rebounds, and Assists": "pra", "Total Points and Rebounds": "pr",
    "Total Points and Assists": "pa", "Total Assists and Rebounds": "ra",
}
WINDOW_MIN = 45
KEEP_DAYS = 45
AZ = timezone(timedelta(hours=-7))  # Arizona, no daylight saving


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 dk-analytics-closing-lines"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def prop_lines(game_id):
    base = f"{CORE}/events/{game_id}/competitions/{game_id}/odds/100/propBets?lang=en&region=us&limit=1000"
    first = get(base)
    items = first.get("items", [])
    for page in range(2, (first.get("pageCount") or 1) + 1):
        items += get(base + f"&page={page}").get("items", [])
    groups = {}
    for it in items:
        mkey = MARKETS.get((it.get("type") or {}).get("name"))
        ref = (it.get("athlete") or {}).get("$ref", "")
        if not mkey or "/athletes/" not in ref:
            continue
        pid = ref.split("/athletes/")[1].split("?")[0].split("/")[0]
        groups.setdefault(f"{pid}|{mkey}", []).append(it)
    lines = {}
    for key, arr in groups.items():
        if len(arr) < 2:
            continue
        over = arr[0]  # ESPN lists DraftKings pairs Over first, Under second
        line = ((over.get("current") or {}).get("target") or {}).get("value")
        under = next((x for x in arr[1:] if ((x.get("current") or {}).get("target") or {}).get("value") == line), arr[1])
        try:
            lines[key] = [line, int(over["odds"]["american"]["value"]), int(under["odds"]["american"]["value"])]
        except (KeyError, TypeError, ValueError):
            continue
    return lines


def main():
    now = datetime.now(timezone.utc)
    today = now.astimezone(AZ).strftime("%Y%m%d")
    try:
        data = json.load(open("closing-lines.json"))
    except (OSError, ValueError):
        data = {"games": {}}
    games = data.setdefault("games", {})

    events = get(f"{ESPN}/scoreboard?dates={today}").get("events", [])
    captured = 0
    for e in events:
        state = ((e.get("status") or {}).get("type") or {}).get("state")
        tip = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        mins = (tip - now).total_seconds() / 60
        if state != "pre" or mins > WINDOW_MIN or mins < -10:
            continue
        try:
            lines = prop_lines(e["id"])
        except Exception as err:  # noqa: BLE001
            print(f"{e.get('shortName')}: props unavailable ({err})")
            continue
        if lines:
            games[e["id"]] = {"date": tip.astimezone(AZ).strftime("%Y-%m-%d"), "game": e.get("shortName"),
                              "tip": e["date"], "at": now.isoformat(timespec="minutes"), "lines": lines}
            captured += 1
            print(f"{e.get('shortName')}: saved {len(lines)} lines, {mins:.0f} min before tip")

    cutoff = (now - timedelta(days=KEEP_DAYS)).astimezone(AZ).strftime("%Y-%m-%d")
    for gid in [g for g, v in games.items() if v.get("date", "9999") < cutoff]:
        del games[gid]
    if captured:
        data["updated"] = now.isoformat(timespec="minutes")
        with open("closing-lines.json", "w") as f:
            json.dump(data, f, separators=(",", ":"))
    print(f"{len(events)} games today, {captured} snapshotted this run")


if __name__ == "__main__":
    main()
