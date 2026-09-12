"""
FE Bot Run Monitor — Streamlit dashboard.

Deploy this file to your Streamlit Cloud GitHub repo as `streamlit.py`
(or set Main file path to this file).

Reads event JSON files written by the bot via GitHub Contents API:
  data/events/{run_id}__{ts}__{event}__{uid}.json

Secrets (Streamlit Cloud → App settings → Secrets):
  GITHUB_TOKEN = "ghp_..."
  GITHUB_REPO  = "youruser/your-monitor-repo"
  GITHUB_BRANCH = "main"          # optional
  WEBHOOK_SECRET = "fe-monitor-secret"  # optional filter
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import requests
import streamlit as st

st.set_page_config(
    page_title="FE Run Monitor",
    page_icon="📡",
    layout="wide",
)

EVENTS_DIR = "data/events"
GITHUB_API = "https://api.github.com"
REFRESH_SECONDS = 8


def _secrets() -> dict[str, str]:
    """Prefer Streamlit secrets; fall back to empty (local demo)."""
    try:
        s = st.secrets
        return {
            "token": str(s.get("GITHUB_TOKEN", "") or "").strip(),
            "repo": str(s.get("GITHUB_REPO", "") or "").strip(),
            "branch": str(s.get("GITHUB_BRANCH", "main") or "main").strip(),
            "secret": str(s.get("WEBHOOK_SECRET", "") or "").strip(),
        }
    except Exception:
        return {"token": "", "repo": "", "branch": "main", "secret": ""}


def _headers(token: str) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def fetch_event_files(repo: str, branch: str, token: str) -> list[dict[str, Any]]:
    """List + download event JSON files from the monitor repo."""
    if not repo:
        return []

    list_url = f"{GITHUB_API}/repos/{repo}/contents/{EVENTS_DIR}"
    params = {"ref": branch}
    try:
        resp = requests.get(
            list_url, headers=_headers(token), params=params, timeout=20
        )
    except Exception as e:
        st.warning(f"GitHub list failed: {e}")
        return []

    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        st.warning(f"GitHub list {resp.status_code}: {resp.text[:240]}")
        return []

    entries = resp.json()
    if not isinstance(entries, list):
        return []

    events: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or ""
        if not name.endswith(".json"):
            continue
        download = entry.get("download_url")
        if not download:
            continue
        try:
            r = requests.get(download, headers=_headers(token), timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data, dict):
                data["_file"] = name
                events.append(data)
        except Exception:
            continue
    return events


def aggregate_runs(events: list[dict[str, Any]], secret_filter: str) -> dict[str, dict]:
    runs: dict[str, dict] = {}
    by_run: dict[str, list] = defaultdict(list)

    for ev in events:
        if secret_filter:
            if str(ev.get("secret") or "") != secret_filter:
                continue
        rid = str(ev.get("run_id") or "").strip()
        if not rid:
            continue
        by_run[rid].append(ev)

    for rid, items in by_run.items():
        items.sort(key=lambda e: e.get("ts") or "")
        session: dict[str, Any] = {}
        status = "running"
        tag = ""
        current_page = ""
        current_url = ""
        started = items[0].get("ts") or ""
        updated = items[-1].get("ts") or ""

        for ev in items:
            sess = ev.get("session")
            if isinstance(sess, dict):
                session.update({k: v for k, v in sess.items() if v not in (None, "")})
            et = (ev.get("event") or "").lower()
            if et == "page":
                current_page = ev.get("page") or current_page
                current_url = ev.get("url") or current_url
                status = "running"
            elif et == "success":
                status = "success"
                tag = ev.get("tag") or tag
                current_url = ev.get("url") or current_url
            elif et == "fail":
                status = "failed"
                tag = ev.get("tag") or tag
                current_url = ev.get("url") or current_url
            elif et == "run_start":
                status = status if status in ("success", "failed") else "running"
            if ev.get("tag"):
                tag = ev.get("tag") or tag

        email = (
            session.get("email")
            or session.get("_lead_email")
            or ""
        )
        name = " ".join(
            x for x in (session.get("first_name"), session.get("last_name")) if x
        ).strip()

        runs[rid] = {
            "run_id": rid,
            "status": status,
            "tag": tag,
            "email": email,
            "name": name,
            "sessionId": session.get("sessionId") or "",
            "zipcode": session.get("zipcode") or "",
            "city": session.get("city") or "",
            "state": session.get("state") or "",
            "current_page": current_page,
            "current_url": current_url,
            "started": started,
            "updated": updated,
            "events": items,
            "session": session,
        }
    return runs


def status_badge(status: str) -> str:
    s = (status or "").lower()
    if s == "success":
        return "🟢 success"
    if s == "failed":
        return "🔴 failed"
    return "🟡 running"


def main() -> None:
    st.title("FE Run Monitor")
    st.caption("Live bot runs — pages, failures, enrollments. Auto-refreshes.")

    cfg = _secrets()
    if not cfg["repo"]:
        st.error(
            "Set Streamlit secrets: `GITHUB_TOKEN`, `GITHUB_REPO` "
            "(and optional `GITHUB_BRANCH`, `WEBHOOK_SECRET`)."
        )
        st.code(
            'GITHUB_TOKEN = "ghp_..."\n'
            'GITHUB_REPO = "youruser/your-monitor-repo"\n'
            'GITHUB_BRANCH = "main"\n'
            'WEBHOOK_SECRET = "fe-monitor-secret"',
            language="toml",
        )
        st.stop()

    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        auto = st.toggle("Auto-refresh", value=True)
    with col_b:
        if st.button("Refresh now"):
            fetch_event_files.clear()
            st.rerun()
    with col_c:
        st.write(f"Repo: `{cfg['repo']}` · branch `{cfg['branch']}`")

    raw_events = fetch_event_files(cfg["repo"], cfg["branch"], cfg["token"])
    runs = aggregate_runs(raw_events, cfg["secret"])

    if not runs:
        st.info(
            "No events yet. Start a bot run after setting `GITHUB_TOKEN` + "
            "`GITHUB_REPO` in the bot’s `webhook.py`."
        )
        if auto:
            import time

            time.sleep(REFRESH_SECONDS)
            fetch_event_files.clear()
            st.rerun()
        st.stop()

    # ── Summary cards ─────────────────────────────────────────────────────
    statuses = [r["status"] for r in runs.values()]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total runs", len(runs))
    c2.metric("Running", sum(1 for s in statuses if s == "running"))
    c3.metric("Success", sum(1 for s in statuses if s == "success"))
    c4.metric("Failed", sum(1 for s in statuses if s == "failed"))

    # ── Filters ───────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns(3)
    with f1:
        status_filter = st.multiselect(
            "Status",
            options=["running", "success", "failed"],
            default=["running", "success", "failed"],
        )
    with f2:
        q = st.text_input("Search email / name / sessionId", "").strip().lower()
    with f3:
        sort_newest = st.toggle("Newest first", value=True)

    rows = list(runs.values())
    if status_filter:
        rows = [r for r in rows if r["status"] in status_filter]
    if q:
        rows = [
            r
            for r in rows
            if q in (r.get("email") or "").lower()
            or q in (r.get("name") or "").lower()
            or q in (r.get("sessionId") or "").lower()
            or q in (r.get("run_id") or "").lower()
            or q in (r.get("tag") or "").lower()
        ]
    rows.sort(key=lambda r: r.get("updated") or "", reverse=sort_newest)

    st.subheader(f"Runs ({len(rows)})")

    for r in rows:
        title = (
            f"{status_badge(r['status'])} · "
            f"{r.get('email') or r.get('name') or 'unknown'} · "
            f"{r.get('tag') or r.get('current_page') or '—'} · "
            f"`{r['run_id'][:8]}`"
        )
        with st.expander(title, expanded=(r["status"] == "running")):
            m1, m2, m3 = st.columns(3)
            m1.write(f"**Session:** `{r.get('sessionId') or '—'}`")
            m2.write(
                f"**Location:** "
                f"{r.get('city') or '—'}, {r.get('state') or '—'} "
                f"{r.get('zipcode') or ''}"
            )
            m3.write(f"**Page:** `{r.get('current_page') or '—'}`")
            st.write(f"**URL:** {r.get('current_url') or '—'}")
            st.write(f"**Started:** {r.get('started')} · **Updated:** {r.get('updated')}")

            if r.get("session"):
                with st.popover("Session details"):
                    st.json(r["session"])

            timeline = []
            for ev in r.get("events") or []:
                timeline.append(
                    {
                        "ts": ev.get("ts"),
                        "event": ev.get("event"),
                        "page": ev.get("page"),
                        "tag": ev.get("tag"),
                        "status": ev.get("status"),
                        "detail": (ev.get("detail") or "")[:120],
                        "url": ev.get("url"),
                    }
                )
            st.dataframe(timeline, use_container_width=True, hide_index=True)

    if auto:
        import time

        st.caption(f"Auto-refresh in {REFRESH_SECONDS}s…")
        time.sleep(REFRESH_SECONDS)
        fetch_event_files.clear()
        st.rerun()


if __name__ == "__main__":
    main()
