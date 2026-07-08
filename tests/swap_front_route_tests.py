"""mazidex-admin — Phase 4 SWAP FRONT route + helper tests.

Scope: the FastAPI route `/api/v1/row-action/swap-front` (app.row_action_swap_front)
       PLUS its three new pure helpers:
         * _swap_status_code(status)      — engine status  -> HTTP code map.
         * _swap_entry_from_row(row)      — DB row -> minimal engine `entry`
                                            (raw_full merged; DB cols authoritative).
         * _parse_engine_json(stdout)     — LAST top-level JSON object printed by
                                            the engine (merge output may precede it).

Mode:  Imports `app` (clean, no side effects), then exercises the async route by
       calling it directly with a fake Request and MONKEYPATCHED dependencies:
         * app.review_write_enabled / app.row_actions_write_enabled (the gate),
         * app.neon_conn (fake conn/cursor yielding canned rows),
         * subprocess.run (fake CompletedProcess with a canned engine stdout).
       The real pick_deletable_row + deep_scrub run. NO Neon hit, NO real
       subprocess, NO image/byte write. tempfile writes+unlinks one small JSON.

Usage: cd ~/mazidex-admin && ./venv/bin/python tests/swap_front_route_tests.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app  # noqa: E402


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class _FakeReq:
    """Minimal FastAPI Request: only `await request.json()` is used."""

    def __init__(self, body, *, raise_on_json: bool = False):
        self._body = body
        self._raise = raise_on_json

    async def json(self):
        if self._raise:
            raise ValueError("malformed")
        return self._body


class _FakeCur:
    def __init__(self, rows_dicts):
        self._rows = rows_dicts
        self._cols = list(rows_dicts[0].keys()) if rows_dicts else ["source_view"]
        self.executed = None

    @property
    def description(self):
        return [type("D", (), {"name": c})() for c in self._cols]

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchall(self):
        return [tuple(r.get(c) for c in self._cols) for r in self._rows]


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeProc:
    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _run_route(body, *, gate_open=True, rows=None, engine_stdout="{}",
               engine_stderr="", engine_rc=0, raise_json=False):
    """Call the async route with all deps faked. Returns (status_code, payload, calls).

    `calls` collects the subprocess.run invocations so tests can assert the exact
    engine command + env (dry-run vs --merge, MAZI_IMAGE_BACKFILL_V1).
    """
    calls: list[dict] = []

    orig_rwe = app.review_write_enabled
    orig_rawe = app.row_actions_write_enabled
    orig_conn = app.neon_conn
    orig_run = subprocess.run

    app.review_write_enabled = (lambda: True) if gate_open else (lambda: False)
    app.row_actions_write_enabled = (lambda: True) if gate_open else (lambda: False)
    app.neon_conn = lambda: _FakeConn(_FakeCur(rows if rows is not None else []))

    def _fake_run(cmd, **kw):
        calls.append({"cmd": list(cmd), "env": kw.get("env") or {}, "cwd": kw.get("cwd")})
        return _FakeProc(engine_stdout, engine_stderr, engine_rc)

    subprocess.run = _fake_run
    try:
        resp = asyncio.run(
            app.row_action_swap_front(_FakeReq(body, raise_on_json=raise_json))
        )
    finally:
        app.review_write_enabled = orig_rwe
        app.row_actions_write_enabled = orig_rawe
        app.neon_conn = orig_conn
        subprocess.run = orig_run

    payload = json.loads(bytes(resp.body).decode("utf-8"))
    return resp.status_code, payload, calls


IDENT_ROW = {
    "source_view": "identified",
    "observed_in": "identified",
    "comp_id": "MC-20260623083759-p56171-0242-a503",
    "source_key": "identified_sweep::MC-20260623083759-p56171-0242-a503",
    "front_image": "/Users/stavrosaim4/m4_live/whatnot_cards/old_front.jpg",
    "raw_full": {
        "comp_id": "MC-20260623083759-p56171-0242-a503",
        "observed_in": "identified",
        "capture_quality": {"card_image_count": 1},
        "api_scan": {"player": "Ja Morant"},
        "evidence_stamp": {"recovery_detector": {"frame_file": "frame_003.jpg"}},
    },
}
TRUSTED_ONLY_ROWS = [
    {"source_view": "trusted", "comp_id": "T", "source_key": "sk", "raw_full": {}},
    {"source_view": "feed", "comp_id": "F", "source_key": "sk", "raw_full": {}},
]
SK = IDENT_ROW["source_key"]


# ==========================================================================
# A — _swap_status_code (pos + neg)
# ==========================================================================
_SC = app._swap_status_code
check("A1 swapped -> 201", _SC("swapped") == 201, str(_SC("swapped")))
check("A2 swap_prepared -> 200", _SC("swap_prepared") == 200, "")
check("A3 no_alternate_front_available -> 200", _SC("no_alternate_front_available") == 200, "")
check("A4 no_lot_found -> 409", _SC("no_lot_found") == 409, "")
check("A5 refused_trusted_row -> 409", _SC("refused_trusted_row") == 409, "")
check("A6 row_not_found -> 404", _SC("row_not_found") == 404, "")
check("A7 unparseable_source_key -> 400", _SC("unparseable_source_key") == 400, "")
check("A8 integrity_refused_verified -> 422", _SC("integrity_refused_verified") == 422, "")
check("A9 build_failed:<any> -> 422 (prefix)", _SC("build_failed:frame_file_missing") == 422, "")
check("A10 unknown status -> 500", _SC("some_new_status") == 500, "")
check("A11 empty status -> 500", _SC("") == 500, "")


# ==========================================================================
# B — _swap_entry_from_row (raw_full merge + DB-column authority)
# ==========================================================================
e = app._swap_entry_from_row(IDENT_ROW)
check("B1 comp_id carried from DB column", e.get("comp_id") == IDENT_ROW["comp_id"], "")
check("B2 evidence_stamp carried from raw_full",
      e.get("evidence_stamp", {}).get("recovery_detector", {}).get("frame_file") == "frame_003.jpg", "")
check("B3 capture_quality carried from raw_full",
      e.get("capture_quality", {}).get("card_image_count") == 1, "")
check("B4 api_scan carried from raw_full", e.get("api_scan", {}).get("player") == "Ja Morant", "")
check("B5 front_image overlaid from DB column", e.get("front_image") == IDENT_ROW["front_image"], "")
check("B6 source_view overlaid from DB column", e.get("source_view") == "identified", "")
# Stale raw.observed_in must be overridden by the authoritative DB column.
stale = {
    "source_view": "pending",
    "observed_in": "pending",
    "comp_id": "C-p1-0-a2",
    "front_image": "db_front.jpg",
    "raw_full": {"observed_in": "trusted", "front_image": "stale.jpg", "comp_id": "STALE"},
}
es = app._swap_entry_from_row(stale)
check("B7 stale raw.observed_in overridden by DB source_view/observed_in",
      es.get("source_view") == "pending" and es.get("observed_in") == "pending", str(es.get("observed_in")))
check("B8 stale raw.front_image overridden by DB front_image", es.get("front_image") == "db_front.jpg", "")
check("B9 stale raw.comp_id overridden by DB comp_id", es.get("comp_id") == "C-p1-0-a2", "")
# raw_full absent/non-dict -> still a usable minimal entry.
en = app._swap_entry_from_row({"comp_id": "X-p1-0-a2", "source_view": "pending", "raw_full": None})
check("B10 raw_full None -> minimal entry with comp_id", en.get("comp_id") == "X-p1-0-a2" and "evidence_stamp" not in en, str(en))


# ==========================================================================
# C — _parse_engine_json (LAST object; merge output tolerated; neg cases)
# ==========================================================================
_PJ = app._parse_engine_json
check("C1 single object parsed", _PJ('{"status":"swapped"}').get("status") == "swapped", "")
check("C2 pretty-printed object parsed",
      _PJ('{\n  "status": "swap_prepared",\n  "mutated": false\n}').get("status") == "swap_prepared", "")
# merge_captures may print its own line/object BEFORE the result dict -> take LAST.
merged_stdout = (
    'merged 1 update into master\n'
    '{"merge":"ok","n":1}\n'
    '{"status":"swapped","mutated":true,"new_front":"/x/new.jpg"}\n'
)
pj = _PJ(merged_stdout)
check("C3 returns the LAST object when merge output precedes it",
      pj.get("status") == "swapped" and pj.get("new_front") == "/x/new.jpg", str(pj))
check("C4 non-JSON text -> None", _PJ("Traceback (most recent call last): boom") is None, "")
check("C5 empty stdout -> None", _PJ("") is None, "")
check("C6 whitespace-only -> None", _PJ("   \n  ") is None, "")
check("C7 a JSON array (not object) at end -> None (dicts only)", _PJ('[1,2,3]') is None, "")


# ==========================================================================
# D — route: gate CLOSED -> 503 echo (fail-closed, before DB/subprocess)
# ==========================================================================
sc, pl, calls = _run_route({"source_key": SK, "confirm": True}, gate_open=False)
check("D1 gate closed -> 503", sc == 503, str(sc))
check("D2 gate closed error code", pl.get("error") == "row_actions_write_disabled", str(pl.get("error")))
check("D3 gate closed echoes validated_request", pl.get("validated_request", {}).get("source_key") == SK, "")
check("D4 gate closed ran NO subprocess", calls == [], str(calls))


# ==========================================================================
# E — route: input validation (malformed json / missing source_key)
# ==========================================================================
sc, pl, _ = _run_route({}, raise_json=True)
check("E1 malformed json -> 400", sc == 400 and pl.get("error") == "malformed_json", str((sc, pl)))
sc, pl, _ = _run_route({"confirm": True})
check("E2 missing source_key -> 400", sc == 400 and pl.get("error") == "missing_source_key", str((sc, pl)))


# ==========================================================================
# F — route: row resolution (not found / not swappable)
# ==========================================================================
sc, pl, calls = _run_route({"source_key": SK, "confirm": True}, rows=[])
check("F1 no rows -> 404 source_key_not_found", sc == 404 and pl.get("error") == "source_key_not_found", str((sc, pl)))
check("F2 not found ran NO subprocess", calls == [], str(calls))
sc, pl, calls = _run_route({"source_key": SK, "confirm": True}, rows=TRUSTED_ONLY_ROWS)
check("F3 trusted/feed only -> 409 row_not_swappable", sc == 409 and pl.get("error") == "row_not_swappable", str((sc, pl)))
check("F4 not-swappable surfaces found views", set(pl.get("found_source_views", [])) == {"trusted", "feed"}, str(pl.get("found_source_views")))
check("F5 not swappable ran NO subprocess", calls == [], str(calls))


# ==========================================================================
# G — route: REAL swap (confirm:true, gate open) -> --merge + env flag + 201
# ==========================================================================
swap_stdout = json.dumps({
    "source_key": SK, "status": "swapped", "mutated": True,
    "bound_frame_file": "frame_003.jpg", "selected_frame_file": "frame_000.jpg",
    "previous_front": IDENT_ROW["front_image"], "new_front": "/x/new_front_stamped.jpg",
    "record": {"evidence_stamp": {"status": "front_stamped"}},
})
sc, pl, calls = _run_route({"source_key": SK, "confirm": True}, rows=[IDENT_ROW], engine_stdout=swap_stdout)
check("G1 real swap -> 201", sc == 201, str(sc))
check("G2 status swapped threaded", pl.get("status") == "swapped", "")
check("G3 write_committed True", pl.get("write_committed") is True, str(pl.get("write_committed")))
check("G4 dry_run False in response", pl.get("dry_run") is False, "")
cmd = calls[0]["cmd"] if calls else []
check("G5 engine got --entry-json + --json", "--entry-json" in cmd and "--json" in cmd, str(cmd))
check("G6 real swap passes --merge (not --dry-run)", "--merge" in cmd and "--dry-run" not in cmd, str(cmd))
check("G7 real swap sets MAZI_IMAGE_BACKFILL_V1=1", calls[0]["env"].get("MAZI_IMAGE_BACKFILL_V1") == "1", str(calls[0]["env"].get("MAZI_IMAGE_BACKFILL_V1")))
check("G8 engine invoked with the whatnot-sniper-m4 venv python",
      cmd and cmd[0].endswith("/whatnot-sniper-m4/venv/bin/python"), str(cmd[:1]))
check("G9 source_key passed through to engine", "--source-key" in cmd and SK in cmd, str(cmd))
check("G10 new_front surfaced in response", pl.get("new_front") == "/x/new_front_stamped.jpg", "")


# ==========================================================================
# H — route: PREVIEW paths (no confirm, or explicit dry_run) -> --dry-run, no env
# ==========================================================================
prep_stdout = json.dumps({"source_key": SK, "status": "swap_prepared", "mutated": False,
                          "selected_frame_file": "frame_000.jpg"})
# H-a: bare request (no confirm) is fail-safe preview even with gate open.
sc, pl, calls = _run_route({"source_key": SK}, rows=[IDENT_ROW], engine_stdout=prep_stdout)
check("H1 no-confirm -> 200 (fail-safe preview)", sc == 200 and pl.get("status") == "swap_prepared", str((sc, pl.get("status"))))
check("H2 no-confirm uses --dry-run", "--dry-run" in calls[0]["cmd"] and "--merge" not in calls[0]["cmd"], str(calls[0]["cmd"]))
check("H3 no-confirm does NOT set MAZI_IMAGE_BACKFILL_V1", "MAZI_IMAGE_BACKFILL_V1" not in calls[0]["env"], str(calls[0]["env"].get("MAZI_IMAGE_BACKFILL_V1")))
check("H4 no-confirm write_committed False", pl.get("write_committed") is False, "")
check("H5 no-confirm dry_run True", pl.get("dry_run") is True, "")
# H-b: explicit dry_run overrides confirm.
sc, pl, calls = _run_route({"source_key": SK, "confirm": True, "dry_run": True}, rows=[IDENT_ROW], engine_stdout=prep_stdout)
check("H6 confirm+dry_run -> preview (dry_run wins)", "--dry-run" in calls[0]["cmd"] and pl.get("write_committed") is False, str(calls[0]["cmd"]))


# ==========================================================================
# I — route: no-op + engine failure surfaces
# ==========================================================================
noop_stdout = json.dumps({"source_key": SK, "status": "no_alternate_front_available", "mutated": False})
sc, pl, calls = _run_route({"source_key": SK, "confirm": True}, rows=[IDENT_ROW], engine_stdout=noop_stdout)
check("I1 no_alternate_front_available -> 200 (valid no-op)", sc == 200 and pl.get("status") == "no_alternate_front_available", str((sc, pl.get("status"))))
check("I2 no-op write_committed False", pl.get("write_committed") is False, "")
# engine prints non-JSON garbage (crash) -> 502 engine_bad_output with stderr tail.
sc, pl, calls = _run_route({"source_key": SK, "confirm": True}, rows=[IDENT_ROW],
                           engine_stdout="Traceback ...\n", engine_stderr="ImportError: ijson", engine_rc=1)
check("I3 non-JSON engine stdout -> 502 engine_bad_output", sc == 502 and pl.get("error") == "engine_bad_output", str((sc, pl.get("error"))))
check("I4 502 surfaces stderr tail", "ijson" in (pl.get("stderr") or ""), str(pl.get("stderr")))
# integrity guard status from engine -> 422.
integ_stdout = json.dumps({"source_key": SK, "status": "integrity_refused_verified", "mutated": False})
sc, pl, _ = _run_route({"source_key": SK, "confirm": True}, rows=[IDENT_ROW], engine_stdout=integ_stdout)
check("I5 integrity_refused_verified -> 422", sc == 422 and pl.get("status") == "integrity_refused_verified", str((sc, pl.get("status"))))
# no lot on disk -> 409.
nolot_stdout = json.dumps({"source_key": SK, "status": "no_lot_found", "mutated": False})
sc, pl, _ = _run_route({"source_key": SK, "confirm": True}, rows=[IDENT_ROW], engine_stdout=nolot_stdout)
check("I6 no_lot_found -> 409", sc == 409 and pl.get("status") == "no_lot_found", str((sc, pl.get("status"))))


# ==========================================================================
# J — route registration smoke (the button's endpoint exists + is POST)
# ==========================================================================
_paths = {getattr(r, "path", None) for r in app.app.routes}
check("J1 /api/v1/row-action/swap-front registered", "/api/v1/row-action/swap-front" in _paths, "")
_swap_route = next((r for r in app.app.routes if getattr(r, "path", None) == "/api/v1/row-action/swap-front"), None)
check("J2 swap-front route is POST", _swap_route is not None and "POST" in getattr(_swap_route, "methods", set()), "")
check("J3 DELETE route still registered (no regression)", "/api/v1/row-action/delete" in _paths, "")


# --------------------------------------------------------------------------
# Print results, exit non-zero on any failure so CI catches it.
# --------------------------------------------------------------------------
print("=" * 94)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {name:<70}  {detail}")
print("=" * 94)
print(f"  {sum(1 for _, ok, _ in results if ok)}/{len(results)} checks passed")
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
