"""Laptop job: pull NBA.com stats and upload nba-com.json to the DK Analytics Terminal repo.

NBA.com blocks cloud servers but allows home internet, so this runs on your laptop
each morning (Windows Task Scheduler, set up by setup.bat).
It always downloads the latest fetch_nba_com.py from the repo, so updates arrive by themselves.
"""
import base64, importlib.util, json, os, sys, urllib.error, urllib.request
from datetime import datetime

REPO = "Nichols-projects/NBA-analytics-tool-"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}", flush=True)


def latest_fetch_script():
    url = f"https://raw.githubusercontent.com/{REPO}/main/scripts/fetch_nba_com.py"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            open("fetch_nba_com.py", "wb").write(r.read())
    except Exception as e:  # noqa: BLE001
        log(f"Could not download the latest fetch script ({e}); using the saved copy.")
    spec = importlib.util.spec_from_file_location("fetch_nba_com", os.path.join(HERE, "fetch_nba_com.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def github(method, path, token, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "dk-analytics-laptop", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def main():
    token_file = os.path.join(HERE, "github_token.txt")
    if not os.path.exists(token_file):
        log("github_token.txt is missing. Run setup.bat first.")
        return 1
    token = open(token_file).read().strip()

    log("Downloading NBA.com stats...")
    mod = latest_fetch_script()
    try:
        mod.main()
    except SystemExit as e:
        if e.code:
            log("NBA.com download failed. Check your internet connection and try again.")
            return 1
    data = open("nba-com.json", "rb").read()
    log(f"Got {len(json.loads(data)['players'])} players. Uploading to GitHub...")

    sha = None
    try:
        sha = github("GET", "contents/nba-com.json?ref=main", token).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log(f"GitHub said {e.code}. Is the token still valid with Contents: Read and write?")
            return 1
    body = {"message": f"NBA.com data {datetime.now():%Y-%m-%d}", "content": base64.b64encode(data).decode(), "branch": "main"}
    if sha:
        body["sha"] = sha
    try:
        github("PUT", "contents/nba-com.json", token, body)
    except urllib.error.HTTPError as e:
        log(f"Upload failed ({e.code}). Is the token still valid with Contents: Read and write?")
        return 1
    log("Done. The site will show the new NBA.com data within a few minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
