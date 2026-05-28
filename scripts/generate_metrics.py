#!/usr/bin/env python3
"""Generate metrics.svg with last-1-year stats: commits, PRs, PR reviews, lines changed."""

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

USER = os.environ.get("METRICS_USER", "garvit14")
TOKEN = os.environ["GH_TOKEN"]
NOW = datetime.now(timezone.utc)
ONE_YEAR_AGO = NOW - timedelta(days=365)


def gh_request(url, method="GET", data=None, retries=1):
    """Single request — caller handles retry semantics. Returns (status, body)."""
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-metrics-script",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        parsed = json.loads(raw) if raw else None
        return r.status, parsed


GQL_ID = "query($login: String!) { user(login: $login) { id } }"

_, id_resp = gh_request(
    "https://api.github.com/graphql",
    method="POST",
    data={"query": GQL_ID, "variables": {"login": USER}},
)
USER_ID = id_resp["data"]["user"]["id"]

GQL = """
query($login: String!, $from: DateTime!, $to: DateTime!,
      $gitFrom: GitTimestamp!, $gitTo: GitTimestamp!, $userId: ID!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository {
          nameWithOwner
          defaultBranchRef {
            target {
              ... on Commit {
                history(since: $gitFrom, until: $gitTo, author: {id: $userId}, first: 100) {
                  nodes { additions deletions }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

iso_from = ONE_YEAR_AGO.isoformat()
iso_to = NOW.isoformat()
_, resp = gh_request(
    "https://api.github.com/graphql",
    method="POST",
    data={
        "query": GQL,
        "variables": {
            "login": USER,
            "from": iso_from,
            "to": iso_to,
            "gitFrom": iso_from,
            "gitTo": iso_to,
            "userId": USER_ID,
        },
    },
)

if "errors" in resp:
    raise SystemExit(f"GraphQL errors: {json.dumps(resp['errors'], indent=2)}")

cc = resp["data"]["user"]["contributionsCollection"]
commits = cc["totalCommitContributions"]
prs = cc["totalPullRequestContributions"]
reviews = cc["totalPullRequestReviewContributions"]

lines = 0
for repo_entry in cc["commitContributionsByRepository"]:
    branch = repo_entry["repository"].get("defaultBranchRef")
    if not branch or not branch.get("target"):
        continue
    history = branch["target"].get("history") or {}
    for node in history.get("nodes", []) or []:
        lines += node["additions"] + node["deletions"]


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


stats = [
    ("Commits", fmt(commits)),
    ("Pull Requests", fmt(prs)),
    ("PRs Reviewed", fmt(reviews)),
    ("Lines Changed", fmt(lines)),
]

W, H = 760, 170
card_w = W // 4
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
svg.append(
    "<style>"
    'text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;}'
    ".label{fill:#8b949e;font-size:15px;font-weight:500;}"
    ".value{fill:#58a6ff;font-size:42px;font-weight:700;}"
    ".title{fill:#c9d1d9;font-size:14px;font-weight:600;letter-spacing:0.5px;}"
    ".footer{fill:#6e7681;font-size:11px;}"
    "</style>"
)
svg.append(
    f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" '
    'rx="10" ry="10" fill="#0d1117" stroke="#30363d" stroke-width="1"/>'
)
svg.append(f'<text x="20" y="28" class="title">GITHUB · LAST 12 MONTHS</text>')

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

print(f"commits={commits} prs={prs} reviews={reviews} lines={lines}")
