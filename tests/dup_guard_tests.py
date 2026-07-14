#!/usr/bin/env python3
"""Pos/neg unit tests for promotion.py _duplicate_sale_reasons (no DB — FakeCursor).

Mirror-side of whatnot-sniper-m4/tests/test_trusted_gate.py dup_guard cases.
Run: python tests/dup_guard_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promotion import _duplicate_sale_reasons, _dup_seller_key  # noqa: E402

# What "Trusted" already holds for these tests. Shapes seen live 2026-07-13:
# a cross-bot dup pair, a legacy-key same-capture pair, a glue-dirt seller,
# a near-midnight sale, and a mini-format key.
TRUSTED = [
    {"source_key": "identified_sweep::MC-20260605094234-p99475-0204-a27",
     "seller": "legacypulls_", "auction_number": "27", "sold_price": "32.00",
     "sale_ts": None},
    {"source_key": "identified_sweep::MC-20260603170025-p4168-0132-a244",
     "seller": "wolff_cards", "auction_number": "244", "sold_price": "82.00",
     "sale_ts": None},
    {"source_key": "identified_sweep::MC-20260607110000-p55555-0068-a68",
     "seller": "popsplosionFollow68Card", "auction_number": "68",
     "sold_price": "12.00", "sale_ts": None},
    {"source_key": "identified_sweep::MC-20260605234000-p11111-0009-a55",
     "seller": "nightowlcards", "auction_number": "55", "sold_price": "25.00",
     "sale_ts": "2026-06-05T23:58:59.000000"},
    {"source_key": "identified_sweep::mini_billykozkardz_122_99475_2026-07-03",
     "seller": "billykozkardz", "auction_number": "122", "sold_price": "14.00",
     "sale_ts": None},
]


class FakeCursor:
    """Answers the two queries _duplicate_sale_reasons issues, from TRUSTED."""

    def __init__(self):
        self._result = []

    def execute(self, sql, params):
        if "LIKE" in sql:  # core-id probe
            pat, self_key = params
            core = pat.strip("%")
            self._result = [(t["source_key"],) for t in TRUSTED
                            if core in t["source_key"] and t["source_key"] != self_key]
        else:  # seller+auction probe (normalized like the SQL does)
            seller, auction, self_key = params
            self._result = [
                (t["source_key"], t["sold_price"], t["sale_ts"]) for t in TRUSTED
                if _dup_seller_key(t["seller"]) == seller
                and str(t["auction_number"]).strip().lower() == auction
                and t["source_key"] != self_key]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


def probe(sk="identified_sweep::MC-20260605093736-p96953-0228-a27",
          seller="legacypulls_", auction="27", price="32.00", ts=None):
    reasons = _duplicate_sale_reasons(FakeCursor(), {
        "source_key": sk, "seller": seller, "auction_number": auction,
        "sold_price": price, "raw": {"timestamp": ts} if ts else {}})
    return reasons[0].split(":")[0] if reasons else "none"


CASES = [
    # NEG (blocks)
    ("crossbot_same_sale_dup", lambda: probe(), "gate_duplicate_sale_of"),
    ("follow_suffix_seller_dup", lambda: probe(seller="legacypulls_Follow"),
     "gate_duplicate_sale_of"),
    ("follow_glue_both_sides_dup",
     lambda: probe(sk="identified_sweep::MC-20260607110500-p66666-0068-a68",
                   seller="popsplosion", auction="68", price="12.00"),
     "gate_duplicate_sale_of"),
    ("price_float_vs_str_dup", lambda: probe(price=32.0), "gate_duplicate_sale_of"),
    ("legacy_key_same_capture_dup",
     lambda: probe(sk="whatnot.jsonl::MC-20260603170025-p4168-0132-a244::244",
                   seller="wolff_cards", auction="244", price="82.00"),
     "gate_duplicate_capture_of"),
    ("midnight_straddle_dup",
     lambda: probe(sk="identified_sweep::MC-20260606000500-p33333-0001-a55",
                   seller="nightowlcards", auction="55", price="25.00",
                   ts="2026-06-05T23:59:29.000000"), "gate_duplicate_sale_of"),
    ("mini_key_same_day_dup",
     lambda: probe(sk="identified_sweep::mini_billykozkardz_122_11111_2026-07-03",
                   seller="billykozkardz", auction="122", price="14.00"),
     "gate_duplicate_sale_of"),
    # POS (passes)
    ("same_row_not_self_dup",
     lambda: probe(sk="identified_sweep::MC-20260605094234-p99475-0204-a27"), "none"),
    ("different_price_ok", lambda: probe(price="45.00"), "none"),
    ("different_auction_ok", lambda: probe(auction="99"), "none"),
    ("different_seller_ok", lambda: probe(seller="someoneelse"), "none"),
    ("auction_reused_other_day_ok",
     lambda: probe(sk="identified_sweep::MC-20260701101010-p11111-0002-a27"), "none"),
    ("blank_auction_skips_guard", lambda: probe(auction=""), "none"),
    ("timestamps_hours_apart_ok",
     lambda: probe(sk="identified_sweep::MC-20260605234000-p11111-0140-a55",
                   seller="nightowlcards", auction="55", price="25.00",
                   ts="2026-06-06T05:30:00.000000"), "none"),
    ("mini_key_other_day_ok",
     lambda: probe(sk="identified_sweep::mini_billykozkardz_122_11111_2026-07-09",
                   seller="billykozkardz", auction="122", price="14.00"), "none"),
]


def _main() -> int:
    total = passed = 0
    for name, fn, exp in CASES:
        total += 1
        try:
            got = fn()
            ok = got == exp
        except Exception as exc:  # noqa: BLE001
            got, ok = f"EXC:{exc}", False
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<32} expected={exp!s:<26} got={got}")
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(_main())
