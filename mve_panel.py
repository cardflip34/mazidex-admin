"""MVE panel — internal MAZI Value Engine surface inside the 8504 workbench (Phase 3, approved 2026-08-19).

Purely ADDITIVE and read-only: reads the engine's LOCAL SQLite stores + canary HTML under
~/MAZI-VALUE-ESTIMATE (never Neon, never the review DB, no write paths). If the stores are absent the
routes degrade to a friendly 404. Registered from app.py via `app.include_router(mve_router)`.

Routes:
  GET /mve                                     index (sports, runs, links)
  GET /mve/{sport}                             latest canary page (full HTML, served as-is)
  GET /mve/api/{sport}/latest                  latest run summary (JSON)
  GET /mve/api/{sport}/card/{card_print_id}    all grade-keys for one print, latest run (JSON incl. comps)
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

MVE_ROOT = Path(os.environ.get("MVE_ROOT", os.path.expanduser("~/MAZI-VALUE-ESTIMATE")))
DATA = MVE_ROOT / "data"
PRIVATE = MVE_ROOT / "docs" / "private"
SPORTS = ("football", "basketball")

mve_router = APIRouter(prefix="/mve", tags=["mve"])


def _store(sport: str) -> sqlite3.Connection:
    p = DATA / f"mve_{sport}.sqlite"
    if sport not in SPORTS or not p.exists():
        raise HTTPException(404, f"no MVE store for sport {sport!r}")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_run(conn: sqlite3.Connection) -> dict:
    r = conn.execute(
        "SELECT run_id, model_version, input_snapshot, started_at, finished_at, metrics FROM model_runs "
        "WHERE run_id LIKE 'run_%' AND json_extract(params, '$.cohort') = 0 ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not r:
        r = conn.execute("SELECT run_id, model_version, input_snapshot, started_at, finished_at, metrics "
                         "FROM model_runs WHERE run_id LIKE 'run_%' ORDER BY started_at DESC LIMIT 1").fetchone()
    if not r:
        raise HTTPException(404, "no valuation runs in the store")
    d = dict(r)
    d["metrics"] = json.loads(d.get("metrics") or "{}")
    return d


@mve_router.get("", response_class=HTMLResponse)
@mve_router.get("/", response_class=HTMLResponse)
def mve_index():
    rows = []
    for sport in SPORTS:
        try:
            conn = _store(sport)
        except HTTPException:
            rows.append(f"<li>{sport}: <i>store not built</i></li>")
            continue
        try:
            run = _latest_run(conn)
            m = run["metrics"]
            sc = m.get("status_counts", {})
            rows.append(
                f"<li><b><a href='/mve/{sport}'>{sport}</a></b> — run {run['run_id']} ({run['model_version']}) · "
                f"keys {m.get('keys', '?')} · ok {sc.get('ok', '?')} · regime {m.get('regime', '?')} · "
                f"<a href='/mve/api/{sport}/latest'>json</a></li>")
        finally:
            conn.close()
    body = ("<html><head><title>MVE — internal</title><style>body{font-family:-apple-system,Helvetica,sans-serif;"
            "background:#0e1116;color:#e6e8eb;padding:24px}a{color:#8ab4f8}</style></head><body>"
            "<h1>MAZI Value Engine — internal panel</h1>"
            "<p>Model-based market intelligence from historical sale evidence. Not investment advice. "
            "MIS is provisional (market-only weights; letter grades withheld).</p>"
            f"<ul>{''.join(rows)}</ul>"
            "<p><a href='http://100.111.48.86:8011/_mve_review/docs/private/index.html'>full review artifacts (8011)</a></p>"
            "</body></html>")
    return HTMLResponse(body)


@mve_router.get("/api/{sport}/latest")
def mve_latest(sport: str):
    conn = _store(sport)
    try:
        run = _latest_run(conn)
        bands = conn.execute(
            "SELECT confidence_band, count(*) n FROM value_estimates WHERE run_id=? GROUP BY 1", (run["run_id"],)
        ).fetchall()
        top = conn.execute(
            "SELECT card_print_id, grade_key, mve, low, high, confidence, confidence_band, exact_comp_count "
            "FROM value_estimates WHERE run_id=? AND status='ok' AND confidence_band IN ('tight','moderate') "
            "ORDER BY mve DESC LIMIT 25", (run["run_id"],)).fetchall()
        return JSONResponse({"run": run, "bands": {r["confidence_band"]: r["n"] for r in bands},
                             "top_reliable": [dict(r) for r in top]})
    finally:
        conn.close()


@mve_router.get("/api/{sport}/card/{card_print_id}")
def mve_card(sport: str, card_print_id: str):
    conn = _store(sport)
    try:
        run = _latest_run(conn)
        # latest estimate PER (print, grade) across runs — incremental runs only re-value changed keys,
        # so run-scoped lookups would 404 every untouched card
        rows = conn.execute(
            "SELECT grade_key, payload FROM value_estimates WHERE card_print_id=? AND id IN "
            "(SELECT max(id) FROM value_estimates WHERE card_print_id=? GROUP BY grade_key)",
            (card_print_id, card_print_id)).fetchall()
        if not rows:
            raise HTTPException(404, "card_print_id has no stored estimate")
        scores = {r["grade_key"]: json.loads(r["payload"]) for r in conn.execute(
            "SELECT grade_key, payload FROM investment_scores WHERE card_print_id=? AND id IN "
            "(SELECT max(id) FROM investment_scores WHERE card_print_id=? GROUP BY grade_key)",
            (card_print_id, card_print_id)).fetchall()}
        # orphan guard: identity re-keying between runs strands the OLD card_print_id, and latest-per-key
        # would serve its final estimate forever. Flag rather than hide — this is an internal review panel,
        # and a visibly stale row is more useful than a missing one.
        has_evidence = conn.execute(
            "SELECT 1 FROM sale_observations WHERE card_print_id=? LIMIT 1", (card_print_id,)).fetchone()
        out = []
        for r in rows:
            est = json.loads(r["payload"])
            est["mis"] = scores.get(r["grade_key"])
            if not has_evidence:
                est["orphan"] = True
                est["orphan_note"] = ("No observations remain under this card_print_id — the identity was "
                                      "re-keyed after this estimate was made. Do not serve this value.")
            out.append(est)
        return JSONResponse({"run_id": run["run_id"], "card_print_id": card_print_id,
                             "orphan": not has_evidence, "estimates": out})
    finally:
        conn.close()


@mve_router.get("/{sport}", response_class=HTMLResponse)
def mve_canary(sport: str):
    if sport not in SPORTS:
        raise HTTPException(404, "unknown sport")
    pages = sorted(glob.glob(str(PRIVATE / f"canary_{sport}_*.html")))
    pages = [p for p in pages if "cohort" not in p] or pages
    if not pages:
        raise HTTPException(404, f"no canary page for {sport}")
    return FileResponse(pages[-1], media_type="text/html")
