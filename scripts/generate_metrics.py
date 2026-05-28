#!/usr/bin/env python3
"""Generate metrics.svg with last-1-year stats: PRs, PR reviews, lines changed.

Uses the GitHub search API instead of contributionsCollection because some orgs
(e.g. nirvanatech) configure member-contribution privacy in a way that hides
private repo contributions from contributionsCollection. The search API returns
whatever the token can directly access, which is what we want.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

USER = os.environ.get("METRICS_USER", "garvit14")
TOKEN = os.environ["GH_TOKEN"]
NOW = datetime.now(timezone.utc)
ONE_YEAR_AGO = NOW - timedelta(days=365)
SINCE = ONE_YEAR_AGO.strftime("%Y-%m-%d")


def gh(url, *, method="GET", data=None, accept="application/vnd.github+json"):
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": accept,
        "User-Agent": f"{USER}-metrics-script",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code in (403, 502, 503) and attempt < 4:
                reset = e.headers.get("X-RateLimit-Reset")
                if e.code == 403 and reset:
                    wait = max(1, int(reset) - int(time.time()) + 1)
                else:
                    wait = 2 ** attempt
                print(f"warn: {e.code} on {url} — retrying in {wait}s", file=sys.stderr)
                time.sleep(min(wait, 60))
                continue
            raise


def search_total(q):
    url = f"https://api.github.com/search/issues?q={quote(q)}&per_page=1"
    return gh(url)["total_count"]


def paginate_commits(q, max_pages=10):
    items = []
    for page in range(1, max_pages + 1):
        url = f"https://api.github.com/search/commits?q={quote(q)}&per_page=100&page={page}"
        resp = gh(url)
        page_items = resp.get("items", [])
        items.extend(page_items)
        if len(page_items) < 100:
            break
    return items


prs = search_total(f"type:pr author:{USER} created:>={SINCE}")
prs_merged = search_total(f"type:pr author:{USER} created:>={SINCE} is:merged")
reviews = search_total(f"type:pr -author:{USER} reviewed-by:{USER} updated:>={SINCE}")
merge_rate_pct = int(round(prs_merged / prs * 100)) if prs else 0

all_commits = paginate_commits(f"author:{USER} author-date:>={SINCE}")
by_repo = defaultdict(list)
for c in all_commits:
    by_repo[c["repository"]["full_name"]].append(c["sha"])

# Active days via contributionCalendar — counts all activity types (commits, PRs,
# reviews, issues, including the org-restricted ones search/commits would miss).
calendar_query = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { contributionCount } }
      }
    }
  }
}
"""
calendar_resp = gh(
    "https://api.github.com/graphql",
    method="POST",
    data={
        "query": calendar_query,
        "variables": {
            "login": USER,
            "from": ONE_YEAR_AGO.isoformat(),
            "to": NOW.isoformat(),
        },
    },
)
active_days = sum(
    1
    for week in calendar_resp["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    for day in week["contributionDays"]
    if day["contributionCount"] > 0
)


def graphql_lines(owner, name, shas):
    chunks = [shas[i : i + 50] for i in range(0, len(shas), 50)]
    total = 0
    for chunk in chunks:
        aliases = "\n".join(
            f'c{i}: object(oid: "{sha}") {{ ... on Commit {{ additions deletions }} }}'
            for i, sha in enumerate(chunk)
        )
        query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {aliases} }} }}'
        resp = gh(
            "https://api.github.com/graphql",
            method="POST",
            data={"query": query},
        )
        if "errors" in resp:
            print(f"warn graphql {owner}/{name}: {resp['errors']}", file=sys.stderr)
            continue
        repo_node = (resp.get("data") or {}).get("repository") or {}
        for i in range(len(chunk)):
            node = repo_node.get(f"c{i}")
            if node:
                total += node.get("additions", 0) + node.get("deletions", 0)
    return total


lines = 0
for repo, shas in by_repo.items():
    owner, name = repo.split("/", 1)
    try:
        lines += graphql_lines(owner, name, shas)
    except Exception as e:
        print(f"warn lines {repo}: {e}", file=sys.stderr)


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


stats = [
    ("Pull Requests", fmt(prs)),
    ("PRs Reviewed", fmt(reviews)),
    ("Lines Changed", fmt(lines)),
    ("Active Days", f"{active_days}/365"),
    ("PR Merge Rate", f"{merge_rate_pct}%"),
]

W, H = 920, 170
card_w = W // len(stats)
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
svg.append(
    "<style>"
    'text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;}'
    ".label{fill:#8b949e;font-size:14px;font-weight:500;}"
    ".value{fill:#58a6ff;font-size:38px;font-weight:700;}"
    ".title{fill:#c9d1d9;font-size:14px;font-weight:600;letter-spacing:0.5px;}"
    ".footer{fill:#6e7681;font-size:11px;}"
    "</style>"
)
svg.append(
    f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" '
    'rx="10" ry="10" fill="#0d1117" stroke="#30363d" stroke-width="1"/>'
)
svg.append('<text x="20" y="28" class="title">GITHUB · LAST 12 MONTHS</text>')

for i, (label, value) in enumerate(stats):
    cx = i * card_w + card_w // 2
    svg.append(f'<text x="{cx}" y="100" text-anchor="middle" class="value">{value}</text>')
    svg.append(f'<text x="{cx}" y="128" text-anchor="middle" class="label">{label}</text>')
    if i > 0:
        x = i * card_w
        svg.append(f'<line x1="{x}" y1="60" x2="{x}" y2="140" stroke="#30363d" stroke-width="1"/>')

svg.append(
    f'<text x="{W - 20}" y="160" text-anchor="end" class="footer">'
    f'Updated {NOW.strftime("%Y-%m-%d")} · @{USER}</text>'
)
svg.append("</svg>")

with open("metrics.svg", "w") as f:
    f.write("".join(svg))

print(
    f"prs={prs} merged={prs_merged} merge_rate={merge_rate_pct}% "
    f"reviews={reviews} lines={lines} active_days={active_days} "
    f"repos={len(by_repo)} commits_inspected={len(all_commits)}"
)
