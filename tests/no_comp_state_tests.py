#!/usr/bin/env python3
"""Pos/neg unit tests for app.py zero-comp deliberate states (2026-07-16).

Under test:
  - _no_comp_rarity: 1/1 / print-run regex over title/card_name/variant.
    Dates ("11/12/2024") and PSA-style grade fractions ("PSA 10/10") must
    NOT read as print runs; /150 is not rare.
  - _no_comp_state bucket routing: rare_by_design short-circuits with ZERO
    queries; otherwise the newest deep-scrub job status maps
    pending/running -> pending_fill and completed/failed/absent ->
    coverage_gap. Lookup errors fail-open to coverage_gap (never 500 the
    drawer, never fabricate a "comps are coming" promise).

Run: venv/bin/python tests/no_comp_state_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (  # noqa: E402
    _no_comp_rarity,
    _no_comp_state,
)


# ---- fakes ------------------------------------------------------------------

class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return self._conn.row


class FakeConn:
    """Fake read-only conn: returns one canned trusted_enrichment_jobs row."""

    def __init__(self, row):
        self.row = row          # e.g. ("pending",) or None
        self.executed = []      # (sql, params) log

    def cursor(self):
        return FakeCursor(self)


class BoomConn:
    """Conn whose cursor() raises -- exercises the fail-open path."""

    def cursor(self):
        raise RuntimeError("db down")


def row(title="", card_name="", variant=""):
    return {"title": title, "card_name": card_name, "variant": variant}


# ---- rarity regex: pos/neg ----------------------------------------------------

RARITY_CASES = [
    # (label, row, expected (tier, print_run) or None)
    ("slash 1/1 unique", row(title="2024 Topps Chrome Superfractor 1/1"), ("unique", 1)),
    ("one of one words", row(title="Downtown ONE OF ONE eagle"), ("unique", 1)),
    ("1 of 1 words", row(title="Kaboom 1 of 1 case hit"), ("unique", 1)),
    ("print run /5 ultra_rare", row(title="Gold Vinyl /5 auto"), ("ultra_rare", 5)),
    ("serial 3/10 ultra_rare", row(title="numbered 3/10 refractor"), ("ultra_rare", 10)),
    ("print run /25 rare", row(title="Orange Wave /25"), ("rare", 25)),
    ("print run /150 NOT rare", row(title="Red Prizm /150"), None),
    ("serial 23/99 NOT rare", row(title="Blue Shimmer 23/99"), None),
    ("date 11/12/2024 NOT a print run", row(title="Sold 11/12/2024 auction"), None),
    ("date 1/1/2026 NOT unique", row(title="stream 1/1/2026 pull"), None),
    ("PSA 10/10 grade NOT a print run", row(title="Jordan PSA 10/10 gem"), None),
    ("BGS 9.5/10 grade NOT a print run", row(title="Luka BGS 9.5/10"), None),
    ("date stripped but /5 still found", row(title="Sold 11/12/2024 Gold /5"), ("ultra_rare", 5)),
    ("plain title no rarity", row(title="2023 Prizm Silver RC"), None),
    ("empty row", row(), None),
    ("card_name field scanned", row(card_name="Wemby Flawless 1/1"), ("unique", 1)),
    ("variant field scanned", row(variant="Gold Shimmer /10"), ("ultra_rare", 10)),
    ("identity_json dict scanned", {**row(), "identity_json": {"parallel": "Black Finite 1/1"}}, ("unique", 1)),
]


def rarity_case(rrow, want):
    got = _no_comp_rarity(rrow)
    if want is None:
        return got is None
    return (
        got is not None
        and got.get("rarity_tier") == want[0]
        and got.get("print_run") == want[1]
        and bool(got.get("matched_token"))
        and bool(got.get("matched_field"))
    )


# ---- bucket routing -----------------------------------------------------------

def route_comps_present_returns_none():
    """Non-empty comps list -> helper is a no-op (never overwrites anything)."""
    conn = FakeConn(("pending",))
    out = _no_comp_state(conn, "WN-1", row(title="whatever 1/1"), [{"sale_price": 5}])
    return out is None and conn.executed == []


def route_rare_by_design_skips_query():
    """Rarity hit -> rare_by_design with ZERO queries (the cheap path)."""
    conn = FakeConn(("pending",))
    out = _no_comp_state(conn, "WN-1", row(title="Superfractor 1/1"), [])
    return (
        out == {
            "bucket": "rare_by_design",
            "detail": {
                "rarity_tier": "unique",
                "print_run": 1,
                "matched_token": "1/1",
                "matched_field": "title",
            },
        }
        and conn.executed == []
    )


def route_pending():
    conn = FakeConn(("pending",))
    out = _no_comp_state(conn, "WN-2", row(title="2023 Prizm Silver RC"), [])
    return out == {"bucket": "pending_fill", "detail": {"job_status": "pending"}}


def route_running():
    conn = FakeConn(("running",))
    out = _no_comp_state(conn, "WN-2", row(title="2023 Prizm Silver RC"), [])
    return out == {"bucket": "pending_fill", "detail": {"job_status": "running"}}


def route_completed():
    conn = FakeConn(("completed",))
    out = _no_comp_state(conn, "WN-2", row(title="2023 Prizm Silver RC"), [])
    return out == {"bucket": "coverage_gap", "detail": {"job_status": "completed"}}


def route_failed():
    conn = FakeConn(("failed",))
    out = _no_comp_state(conn, "WN-2", row(title="2023 Prizm Silver RC"), [])
    return out == {"bucket": "coverage_gap", "detail": {"job_status": "failed"}}


def route_no_job_on_file():
    conn = FakeConn(None)
    out = _no_comp_state(conn, "WN-2", row(title="2023 Prizm Silver RC"), [])
    return out == {"bucket": "coverage_gap", "detail": {"job_status": "absent"}}


def route_blank_source_key_skips_query():
    conn = FakeConn(("pending",))
    out = _no_comp_state(conn, "  ", row(title="2023 Prizm Silver RC"), [])
    return (
        out == {"bucket": "coverage_gap", "detail": {"job_status": "absent"}}
        and conn.executed == []
    )


def route_lookup_error_fails_open():
    out = _no_comp_state(BoomConn(), "WN-2", row(title="2023 Prizm Silver RC"), [])
    return (
        out is not None
        and out["bucket"] == "coverage_gap"
        and out["detail"]["job_status"] == "lookup_error"
        and out["detail"]["error"] == "RuntimeError"
    )


def route_query_shape():
    """Exactly ONE query, filtered to deep-scrub kinds, newest-first LIMIT 1."""
    conn = FakeConn(("completed",))
    _no_comp_state(conn, "WN-3", row(title="2023 Prizm Silver RC"), [])
    if len(conn.executed) != 1:
        return False
    sql, params = conn.executed[0]
    return (
        "trusted_enrichment_jobs" in sql
        and "deep_scrub_initial" in sql
        and "deep_scrub_refresh" in sql
        and "ORDER BY created_at DESC" in sql
        and "LIMIT 1" in sql
        and params == ("WN-3",)
    )


ROUTING_CASES = [
    ("comps_present_returns_none", route_comps_present_returns_none),
    ("rare_by_design_skips_query", route_rare_by_design_skips_query),
    ("pending_maps_to_pending_fill", route_pending),
    ("running_maps_to_pending_fill", route_running),
    ("completed_maps_to_coverage_gap", route_completed),
    ("failed_maps_to_coverage_gap", route_failed),
    ("no_job_maps_to_coverage_gap_absent", route_no_job_on_file),
    ("blank_source_key_skips_query", route_blank_source_key_skips_query),
    ("lookup_error_fails_open_to_coverage_gap", route_lookup_error_fails_open),
    ("query_shape_one_read_only_lookup", route_query_shape),
]


def _main() -> int:
    total = passed = 0
    print("== _no_comp_rarity ==")
    for label, rrow, want in RARITY_CASES:
        total += 1
        try:
            ok = rarity_case(rrow, want)
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] {label}: EXC {exc}")
        else:
            got = _no_comp_rarity(rrow)
            print(f"  [{'PASS' if ok else 'FAIL'}] {label:<40} want={want} got={got and (got['rarity_tier'], got['print_run'])}")
        passed += ok
    print("== _no_comp_state bucket routing ==")
    for name, fn in ROUTING_CASES:
        total += 1
        try:
            ok = fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] {name}: EXC {exc}")
        else:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        passed += ok
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(_main())
