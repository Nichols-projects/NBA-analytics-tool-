"""Daily NBA.com stats pull for DK Analytics Terminal.

Downloads play types (Synergy), player tracking, shot zones and usage from
stats.nba.com and writes a compact nba-com.json that the web app reads.
Standard library only, so it runs anywhere Python 3.9+ is installed.
"""
import gzip, json, os, re, shutil, subprocess, sys, time, unicodedata, urllib.parse, urllib.request, zlib
from datetime import datetime, timezone

# Same headers a browser sends when nba.com loads its own stats pages.
HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}
METHOD = None  # "python" or "curl", whichever NBA.com accepts first
BASE = "https://stats.nba.com/stats/"
NBA_TO_ESPN = {"GSW": "GS", "NYK": "NY", "SAS": "SA", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH"}
TEAM_IDS = {1610612737: "ATL", 1610612738: "BOS", 1610612751: "BKN", 1610612766: "CHA", 1610612741: "CHI",
            1610612739: "CLE", 1610612742: "DAL", 1610612743: "DEN", 1610612765: "DET", 1610612744: "GSW",
            1610612745: "HOU", 1610612754: "IND", 1610612746: "LAC", 1610612747: "LAL", 1610612763: "MEM",
            1610612748: "MIA", 1610612749: "MIL", 1610612750: "MIN", 1610612740: "NOP", 1610612752: "NYK",
            1610612760: "OKC", 1610612753: "ORL", 1610612755: "PHI", 1610612756: "PHX", 1610612757: "POR",
            1610612758: "SAC", 1610612759: "SAS", 1610612761: "TOR", 1610612762: "UTA", 1610612764: "WAS"}
PLAY_TYPES = ["Isolation", "Transition", "PRBallHandler", "PRRollman", "Postup",
              "Spotup", "Handoff", "Cut", "OffScreen", "OffRebound"]
ZONE_KEYS = {"Restricted Area": "RA", "In The Paint (Non-RA)": "PAINT", "Mid-Range": "MID",
             "Left Corner 3": "LC3", "Right Corner 3": "RC3", "Corner 3": "C3", "Above the Break 3": "AB3"}
ERRORS = []


def season_str(now=None):
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 9 else now.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def norm_name(n):
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z ]", "", n.replace("-", " "))
    return " ".join(w for w in n.split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"})


def team(abbr):
    return NBA_TO_ESPN.get(abbr, abbr)


def _python_get(url, timeout):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body, enc = r.read(), (r.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip":
        body = gzip.decompress(body)
    elif enc == "deflate":
        body = zlib.decompress(body)
    return json.loads(body.decode("utf-8"))


def _curl_get(url, timeout):
    # Windows 10/11 ships curl.exe, which uses Windows' own TLS like a browser does.
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not available")
    cmd = [curl, "-sS", "--compressed", "-m", str(timeout)]
    for k, v in HEADERS.items():
        if k not in ("Host", "Accept-Encoding", "Connection"):
            cmd += ["-H", f"{k}: {v}"]
    out = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 10)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or b"").decode(errors="ignore").strip() or f"curl exit {out.returncode}")
    return json.loads(out.stdout.decode("utf-8"))


def fetch(url, timeout=45):
    """Try plain Python first, then Windows curl; remember whichever works."""
    global METHOD
    order = [METHOD] if METHOD else ["python", "curl"]
    last = None
    for m in order:
        try:
            data = (_python_get if m == "python" else _curl_get)(url, timeout)
            if METHOD != m:
                print(f"  connected to NBA.com using {m}", flush=True)
            METHOD = m
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  {m} request failed: {e}", flush=True)
    raise last


def get(endpoint, params, tries=3):
    url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            data = fetch(url)
            time.sleep(1.2)  # be polite
            return data
        except Exception as e:  # noqa: BLE001
            print(f"  {endpoint} attempt {attempt + 1} failed: {e}", flush=True)
            time.sleep(4 * (attempt + 1))
    ERRORS.append(f"{endpoint} {params.get('PlayType') or params.get('PtMeasureType') or params.get('MeasureType') or ''}".strip())
    return None


def rows(data, idx=0):
    """Turn a stats.nba.com result set into a list of dicts."""
    if not data:
        return []
    rs = data.get("resultSets") or data.get("resultSet")
    if isinstance(rs, list):
        rs = rs[idx] if len(rs) > idx else None
    if not rs:
        return []
    heads = rs["headers"]
    if heads and isinstance(heads[0], dict):  # multi-level headers (shot locations)
        return None
    return [dict(zip(heads, r)) for r in rs.get("rowSet", [])]


def rank(values, ascending=True):
    """values: {key: number}; rank 1 = smallest when ascending."""
    order = sorted((k for k, v in values.items() if v is not None), key=lambda k: values[k], reverse=not ascending)
    return {k: i + 1 for i, k in enumerate(order)}


def r3(x):
    return None if x is None else round(float(x), 3)


def main():
    season = season_str()
    print(f"Season {season}", flush=True)
    # quick check first, so a blocked connection fails in about a minute instead of 20
    ok = False
    for ep, q in (("commonteamyears", {"LeagueID": "00"}), ("leaguestandingsv3", {"LeagueID": "00", "Season": season, "SeasonType": "Regular Season"})):
        try:
            fetch(BASE + ep + "?" + urllib.parse.urlencode(q), timeout=40)
            ok = True
            break
        except Exception as e:  # noqa: BLE001
            print(f"  connection check {ep} failed: {e}", flush=True)
    if not ok:
        print("NBA.com is not reachable from this machine. It probably blocks this network (VPN, cloud server).", flush=True)
        sys.exit(1)
    players, teams = {}, {}

    def P(name, abbr):
        key = norm_name(name)
        p = players.setdefault(key, {"name": name})
        if abbr:
            p["team"] = team(abbr)
        return p

    def T(abbr):
        return teams.setdefault(team(abbr), {})

    common = {"LeagueID": "00", "SeasonType": "Regular Season", "PerMode": "PerGame"}

    # 1) Play types (Synergy): player offense + team defense
    for pt in PLAY_TYPES:
        q = dict(common, PlayType=pt, SeasonYear=season)
        off = rows(get("synergyplaytypes", dict(q, PlayerOrTeam="P", TypeGrouping="offensive")))
        for r in off or []:
            if (r.get("GP") or 0) < 2:
                continue
            P(r["PLAYER_NAME"], r.get("TEAM_ABBREVIATION")).setdefault("pt", {})[pt] = [
                r3(r.get("POSS_PCT")), r3(r.get("PPP")), r3(r.get("PTS")), r3(r.get("POSS"))]
        dfn = rows(get("synergyplaytypes", dict(q, PlayerOrTeam="T", TypeGrouping="defensive")))
        ppp = {team(r["TEAM_ABBREVIATION"]): r.get("PPP") for r in dfn or []}
        rk = rank(ppp, ascending=True)  # 1 = allows the lowest PPP (toughest)
        for r in dfn or []:
            ab = team(r["TEAM_ABBREVIATION"])
            T(r["TEAM_ABBREVIATION"]).setdefault("ptDef", {})[pt] = [r3(r.get("PPP")), rk.get(ab), r3(r.get("POSS_PCT"))]

    # 2) Player tracking
    track = {
        "Rebounding": {"REB_CHANCES": "rebChances", "REB_CONTEST": "rebContested", "REB_CHANCE_PCT": "rebChancePct"},
        "Passing": {"POTENTIAL_AST": "potAst", "AST_PTS_CREATED": "astPtsCreated", "PASSES_MADE": "passes"},
        "Drives": {"DRIVES": "drives", "DRIVE_PTS": "drivePts", "DRIVE_FGA": "driveFga"},
        "Possessions": {"TOUCHES": "touches", "TIME_OF_POSS": "timeOfPoss"},
        "CatchShoot": {"CATCH_SHOOT_FGA": "csFga", "CATCH_SHOOT_FG3A": "cs3a"},
        "PullUpShot": {"PULL_UP_FGA": "puFga", "PULL_UP_FG3A": "pu3a"},
    }
    for measure, cols in track.items():
        q = dict(common, Season=season, PlayerOrTeam="Player", PtMeasureType=measure,
                 LastNGames=0, Month=0, OpponentTeamID=0, PORound=0, TeamID=0)
        for r in rows(get("leaguedashptstats", q)) or []:
            t = P(r["PLAYER_NAME"], r.get("TEAM_ABBREVIATION")).setdefault("trk", {})
            for src, dst in cols.items():
                if src in r and r[src] is not None:
                    t[dst] = r3(r[src])

    # 3) Shot zones: player FGA/FG% by zone, team opponent FG% by zone
    def zone_rows(data, id_name):
        if not data:
            return []
        rs = data.get("resultSets")
        rs = rs[0] if isinstance(rs, list) else rs
        heads = rs["headers"]
        zones, cols = heads[0]["columnNames"], heads[1]["columnNames"]
        skip, span = heads[0].get("columnsToSkip", 0), heads[0].get("columnSpan", 3)
        out = []
        for r in rs["rowSet"]:
            rec = {c: r[i] for i, c in enumerate(cols[:skip])}
            rec["zones"] = {}
            for zi, z in enumerate(zones):
                base = skip + zi * span
                vals = dict(zip(cols[base:base + span], r[base:base + span]))
                fga = next((v for k, v in vals.items() if k.endswith("FGA")), None)
                pct = next((v for k, v in vals.items() if k.endswith("FG_PCT")), None)
                if z in ZONE_KEYS:
                    rec["zones"][ZONE_KEYS[z]] = [r3(fga), r3(pct)]
            out.append(rec)
        return out

    zq = dict(common, Season=season, DistanceRange="By Zone", LastNGames=0, Month=0,
              OpponentTeamID=0, PORound=0, TeamID=0, Period=0)
    for r in zone_rows(get("leaguedashplayershotlocations", dict(zq, MeasureType="Base")), "PLAYER_NAME"):
        if r.get("PLAYER_NAME"):
            P(r["PLAYER_NAME"], r.get("TEAM_ABBREVIATION"))["zones"] = r["zones"]
    tz = zone_rows(get("leaguedashteamshotlocations", dict(zq, MeasureType="Opponent")), "TEAM_NAME")
    for z in ZONE_KEYS.values():
        ab_of = lambda r: r.get("TEAM_ABBREVIATION") or TEAM_IDS.get(r.get("TEAM_ID"))
        vals = {team(ab_of(r)): (r["zones"].get(z) or [None, None])[1] for r in tz if ab_of(r)}
        rk = rank(vals, ascending=True)  # 1 = lowest opp FG% (toughest)
        for r in tz:
            ab = ab_of(r)
            if ab and z in r["zones"]:
                T(ab).setdefault("zoneDef", {})[z] = r["zones"][z] + [rk.get(team(ab))]

    # 4) Usage: season and last 5
    for label, n in (("usg", 0), ("usgL5", 5)):
        q = dict(common, Season=season, MeasureType="Advanced", LastNGames=n, Month=0,
                 OpponentTeamID=0, PORound=0, TeamID=0, PaceAdjust="N", PlusMinus="N", Rank="N", Period=0)
        for r in rows(get("leaguedashplayerstats", q)) or []:
            if r.get("USG_PCT") is not None:
                P(r["PLAYER_NAME"], r.get("TEAM_ABBREVIATION"))[label] = r3(r["USG_PCT"])

    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "season": season,
           "errors": ERRORS, "players": players, "teams": teams}
    with open("nba-com.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {len(players)} players, {len(teams)} teams, {len(ERRORS)} failed calls", flush=True)
    # fail the job only if NBA.com gave us nothing at all (likely blocked)
    if not players:
        print("NBA.com returned no data. It is probably blocking this server.", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
