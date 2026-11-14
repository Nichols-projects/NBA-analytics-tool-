"""Daily referee + blowout data for DK Analytics Terminal.

Downloads every regular-season box score from ESPN for this season and last season (cached in
data/games-<season>.json so later runs only fetch new games) and writes refs.json with:
  - league averages and per-referee tendencies (fouls, free throws, points, pace, home/road foul
    split, how often games went over DraftKings' closing total), shrunk toward league average
  - tonight's referee crews and a crew grade
  - blowout data measured the way games actually get away: by the margin entering the 4th quarter
    (team tendencies, coaches' starter-rest habits, each player's minutes in lopsided games)
No API keys needed. Standard library only.
"""
import json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
AZ = timezone(timedelta(hours=-7))
LOPSIDED = 15      # margin entering the 4th that counts as "out of hand"
COMPETITIVE = 10   # within this margin entering the 4th counts as competitive


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 dk-analytics-game-data"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                print(f"  failed {url[-60:]}: {e}")
                return None
            time.sleep(2 * (i + 1))


def season_of(d):
    return d.year + 1 if d.month >= 8 else d.year


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_game(sm, ev_id, date):
    box = sm.get("boxscore") or {}
    comp = ((sm.get("header") or {}).get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    if "home" not in sides or "away" not in sides:
        return None
    h, a = sides["home"], sides["away"]
    hid, aid = (h.get("team") or {}).get("id"), (a.get("team") or {}).get("id")
    if not hid or not aid:
        return None
    q = [[int(num(l.get("displayValue"))) for l in (c.get("linescores") or [])] for c in (h, a)]
    tstats = {}
    for t in box.get("teams", []):
        if not (t.get("team") or {}).get("id"):
            continue
        st = {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}
        sp = lambda k: [num(x) for x in str(st.get(k, "0-0")).split("-")] + [0, 0]
        fga, fta = sp("fieldGoalsMade-fieldGoalsAttempted")[1], sp("freeThrowsMade-freeThrowsAttempted")[1]
        poss = fga + 0.44 * fta - num(st.get("offensiveRebounds")) + num(st.get("totalTurnovers") or st.get("turnovers"))
        tstats[t["team"]["id"]] = {"f": int(num(st.get("fouls"))), "fta": int(fta), "poss": round(poss, 1)}
    if hid not in tstats or aid not in tstats:
        return None
    pl = []
    for tp in box.get("players", []):
        tid = (tp.get("team") or {}).get("id")
        if tid not in (hid, aid):
            continue
        side = 0 if tid == hid else 1
        stt = (tp.get("statistics") or [{}])[0]
        keys = stt.get("keys") or []
        ix = {k: i for i, k in enumerate(keys)}
        for at in stt.get("athletes", []):
            pid = (at.get("athlete") or {}).get("id")
            if not pid:
                continue
            s = at.get("stats") or []
            mins = num(s[ix["minutes"]]) if s and "minutes" in ix else 0
            fouls = int(num(s[ix["fouls"]])) if s and "fouls" in ix else 0
            pl.append([pid, side, mins, 1 if at.get("starter") else 0, fouls])
    pc = (sm.get("pickcenter") or [{}])[0] if sm.get("pickcenter") else {}
    exp_home = None  # expected home margin from DraftKings' closing spread
    det = str(pc.get("details") or "")
    m = re.match(r"([A-Z]+)\s*([+-]?\d+(\.\d+)?)", det)
    if m:
        fav, pts = m.group(1), abs(float(m.group(2)))
        exp_home = pts if fav == h["team"].get("abbreviation") else -pts
    elif det.upper().startswith("EVEN"):
        exp_home = 0.0
    refs = [o.get("displayName") or o.get("fullName") for o in ((sm.get("gameInfo") or {}).get("officials") or [])]
    return {"id": ev_id, "d": date, "h": hid, "a": aid, "ha": h["team"].get("abbreviation"), "aa": a["team"].get("abbreviation"),
            "hs": int(num(h.get("score"))), "as": int(num(a.get("score"))), "q": q, "ref": [r for r in refs if r],
            "exp": exp_home, "ou": pc.get("overUnder"), "t": [tstats[hid], tstats[aid]], "pl": pl}


def load_season(season, today_az):
    path = f"data/games-{season}.json"
    try:
        cache = json.load(open(path))
    except (OSError, ValueError):
        cache = {"games": {}, "daysDone": []}
    done = set(cache.get("daysDone", []))
    start, end = datetime(season - 1, 10, 1), min(datetime(season, 6, 30), today_az - timedelta(days=1))
    days = []
    d = start
    while d <= end:
        k = d.strftime("%Y%m%d")
        if k not in done:
            days.append(k)
        d += timedelta(days=1)
    print(f"Season {season}: {len(cache['games'])} cached games, checking {len(days)} new days")

    def scan(day):
        sb = get(f"{ESPN}/scoreboard?dates={day}")
        if sb is None:
            return day, None
        evs = [(e["id"], e["date"]) for e in sb.get("events", [])
               if (e.get("season") or {}).get("type") == 2 and ((e.get("status") or {}).get("type") or {}).get("completed")]
        return day, evs

    todo = []
    with ThreadPoolExecutor(8) as pool:
        for day, evs in pool.map(scan, days):
            if evs is None:
                continue
            todo += [(i, dt) for i, dt in evs if i not in cache["games"]]
            done.add(day)

    def fetch(item):
        ev_id, dt = item
        sm = get(f"{ESPN}/summary?event={ev_id}")
        if not sm:
            return None
        try:
            local = datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(AZ).strftime("%Y-%m-%d")
            return parse_game(sm, ev_id, local)
        except Exception as e:  # noqa: BLE001  one odd game shouldn't stop the whole build
            print(f"  skipped game {ev_id}: {e}")
            return None

    with ThreadPoolExecutor(8) as pool:
        for g in pool.map(fetch, todo):
            if g:
                cache["games"][g["id"]] = g
    cache["daysDone"] = sorted(done)
    os.makedirs("data", exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, separators=(",", ":"))
    print(f"  fetched {len(todo)} new games, {len(cache['games'])} total")
    return list(cache["games"].values())


def q3_margin(g):
    hq, aq = g["q"]
    if len(hq) < 3 or len(aq) < 3:
        return None
    return sum(hq[:3]) - sum(aq[:3])


def shrink(total, n, prior, k):
    return (total + k * prior) / (n + k)


def todays_crews(today_az):
    sb = get(f"{ESPN}/scoreboard?dates={today_az.strftime('%Y%m%d')}") or {}
    out = {}
    for e in sb.get("events", []):
        sm = get(f"{ESPN}/summary?event={e['id']}") or {}
        names = [o.get("displayName") for o in ((sm.get("gameInfo") or {}).get("officials") or []) if o.get("displayName")]
        out[e["id"]] = {"game": e.get("shortName"), "officials": names}
    missing = [g for g, v in out.items() if not v["officials"]]
    if missing:
        try:  # fallback: the NBA's official assignments page (posted 9 AM Eastern)
            req = urllib.request.Request("https://official.nba.com/referee-assignments/", headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
            for row in rows:
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                if len(cells) < 4 or "@" not in cells[0]:
                    continue
                away, home = [x.strip().lower() for x in cells[0].split("@", 1)]
                refs = [re.sub(r"\s*\(#?\d+\)", "", c).strip() for c in cells[1:4] if c.strip()]
                for gid in missing:
                    short = (out[gid]["game"] or "").lower()
                    if any(w and w in short for w in (away[:3], home[:3])):
                        out[gid]["officials"] = refs
            print("  used official.nba.com for missing crews")
        except Exception as e:  # noqa: BLE001
            print(f"  official.nba.com unavailable: {e}")
    return out


def main():
    now = datetime.now(timezone.utc)
    today = now.astimezone(AZ).replace(tzinfo=None)
    cur = season_of(today)
    games = {cur - 1: load_season(cur - 1, today), cur: load_season(cur, today)}
    allg = games[cur - 1] + games[cur]
    print(f"{len(allg)} games across two seasons")

    # ---------- league + referees ----------
    def gstats(g):
        return {"f": g["t"][0]["f"] + g["t"][1]["f"], "fta": g["t"][0]["fta"] + g["t"][1]["fta"],
                "pts": g["hs"] + g["as"], "pace": (g["t"][0]["poss"] + g["t"][1]["poss"]) / 2,
                "hfd": g["t"][0]["f"] - g["t"][1]["f"],
                "over": None if not g.get("ou") else (1 if g["hs"] + g["as"] > g["ou"] else 0 if g["hs"] + g["as"] < g["ou"] else None)}
    st = [(g, gstats(g)) for g in allg if g.get("ref")]
    L = {k: sum(s[k] for _, s in st) / max(1, len(st)) for k in ("f", "fta", "pts", "pace", "hfd")}
    ov = [s["over"] for _, s in st if s["over"] is not None]
    L["overPct"] = sum(ov) / max(1, len(ov))
    # points/pace drift between seasons: compare refs to their own season's average
    seasonAvg = {}
    for sz, gl in games.items():
        ss = [gstats(g) for g in gl if g.get("ref")]
        seasonAvg[sz] = {k: sum(s[k] for s in ss) / max(1, len(ss)) for k in ("f", "fta", "pts", "pace")}
    refs = {}
    for sz, gl in games.items():
        for g in gl:
            if not g.get("ref"):
                continue
            s = gstats(g)
            for name in g["ref"]:
                r = refs.setdefault(name, {"n": 0, "f": 0, "fta": 0, "pts": 0, "pace": 0, "hfd": 0, "ovN": 0, "ovH": 0, "cur": 0})
                r["n"] += 1
                r["cur"] += 1 if sz == cur else 0
                for k in ("f", "fta", "pts", "pace"):
                    r[k] += s[k] - seasonAvg[sz][k]   # deviation from that season's average
                r["hfd"] += s["hfd"]
                if s["over"] is not None:
                    r["ovN"] += 1
                    r["ovH"] += s["over"]
    K = 15
    ref_out = {}
    for name, r in refs.items():
        ref_out[name] = {"n": r["n"], "nCur": r["cur"],
                         "fouls": round(shrink(r["f"], r["n"], 0, K), 2), "fta": round(shrink(r["fta"], r["n"], 0, K), 2),
                         "pts": round(shrink(r["pts"], r["n"], 0, K), 2), "pace": round(shrink(r["pace"], r["n"], 0, K), 2),
                         "homeFoulDiff": round(shrink(r["hfd"], r["n"], L["hfd"], K), 2),
                         "overPct": round(shrink(r["ovH"], r["ovN"], L["overPct"], 25), 3), "overN": r["ovN"]}

    def crew_grade(names):
        known = [ref_out[n] for n in names if n in ref_out]
        if not known:
            return None
        avgk = lambda k: sum(x[k] for x in known) / len(known)
        c = {"fouls": round(avgk("fouls"), 2), "fta": round(avgk("fta"), 2), "pts": round(avgk("pts"), 2),
             "pace": round(avgk("pace"), 2), "overPct": round(avgk("overPct"), 3), "known": len(known), "games": sum(x["n"] for x in known)}
        c["label"] = "Whistle-heavy" if c["fouls"] >= 1.5 else "Lets them play" if c["fouls"] <= -1.5 else "Average whistle"
        return c

    today_crews = todays_crews(today)
    for gid, v in today_crews.items():
        v["crew"] = crew_grade(v["officials"]) if v["officials"] else None

    # ---------- blowouts: margin entering the 4th ----------
    pairs = [(g["exp"], q3_margin(g)) for g in allg if g.get("exp") is not None and q3_margin(g) is not None]
    sxx = sum(e * e for e, _ in pairs) or 1
    slope = sum(e * m for e, m in pairs) / sxx
    resid = [m - slope * e for e, m in pairs]
    sd = (sum(r * r for r in resid) / max(1, len(resid))) ** 0.5
    print(f"Q3 margin model: {slope:.2f} x expected margin, sd {sd:.1f} ({len(pairs)} games)")

    teams = {}
    players = {}
    for sz, gl in games.items():
        w = 1.0 if sz == cur else 0.5
        for g in gl:
            m3 = q3_margin(g)
            if m3 is None:
                continue
            for side, tid, sign in ((0, g["h"], 1), (1, g["a"], -1)):
                tm = sign * m3
                state = "ahead" if tm >= LOPSIDED else "behind" if tm <= -LOPSIDED else "close" if abs(tm) < COMPETITIVE else "mid"
                t = teams.setdefault(tid, {"n": 0, "ahead": 0, "behind": 0, "stA": [0, 0], "stB": [0, 0], "stC": [0, 0]})
                t["n"] += w
                if state in ("ahead", "behind"):
                    t[state] += w
                for pid, s, mins, starter, fouls in g["pl"]:
                    if s != side:
                        continue
                    p = players.setdefault(pid, {"close": [0, 0], "ahead": [0, 0], "behind": [0, 0], "fouls": 0, "min": 0, "g": 0, "starter": 0})
                    if mins > 0:
                        p["g"] += 1
                        p["min"] += mins
                        p["fouls"] += fouls
                        p["starter"] += starter
                    if state in ("close", "ahead", "behind"):
                        p[state][0] += 1
                        p[state][1] += mins
                    if starter and state in ("close", "ahead", "behind"):
                        key = {"close": "stC", "ahead": "stA", "behind": "stB"}[state]
                        t[key][0] += 1
                        t[key][1] += mins
    team_out = {}
    for tid, t in teams.items():
        avg = lambda a: a[1] / a[0] if a[0] else None
        c, a_, b_ = avg(t["stC"]), avg(t["stA"]), avg(t["stB"])
        team_out[tid] = {"games": round(t["n"], 1), "aheadRate": round(t["ahead"] / t["n"], 3) if t["n"] else None,
                         "behindRate": round(t["behind"] / t["n"], 3) if t["n"] else None,
                         "starterRatioAhead": round(a_ / c, 3) if c and a_ else None,
                         "starterRatioBehind": round(b_ / c, 3) if c and b_ else None}
    pl_out = {}
    for pid, p in players.items():
        if p["g"] < 5 or p["close"][0] < 3:
            continue
        base = p["close"][1] / p["close"][0]
        if base < 8:
            continue
        rec = {"base": round(base, 1), "starterShare": round(p["starter"] / p["g"], 2),
               "foulsPer36": round(p["fouls"] / max(1, p["min"]) * 36, 2)}
        for k in ("ahead", "behind"):
            if p[k][0]:
                rec[k] = [p[k][0], round((p[k][1] / p[k][0]) / base, 3)]
        pl_out[pid] = rec

    out = {"updated": now.isoformat(timespec="minutes"), "seasons": [cur - 1, cur], "games": len(allg),
           "league": {k: round(v, 3) for k, v in L.items()}, "refs": ref_out, "today": today_crews,
           "blowout": {"lopsided": LOPSIDED, "slope": round(slope, 3), "sd": round(sd, 2), "teams": team_out, "players": pl_out}}
    with open("refs.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    crews = sum(1 for v in today_crews.values() if v["officials"])
    print(f"Wrote refs.json: {len(ref_out)} refs, {len(team_out)} teams, {len(pl_out)} players, crews for {crews}/{len(today_crews)} of today's games")


if __name__ == "__main__":
    main()
