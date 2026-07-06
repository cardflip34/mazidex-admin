#!/usr/bin/env python3
"""Pos/neg tests for promotion._trusted_gate_block_reasons (2026-07-05).

The hard-block gate applied to EVERY ->Trusted promotion (identified + pending,
manual APPROVE + auto-promote canary). Mirrors whatnot-sniper-m4 trusted_gate.py
BLOCK dimensions: definitional non-singles + auction-binding mismatches.

Run: python3 tests/trusted_gate_block_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import promotion  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def row(**over):
    base = {
        "source_key": "sweep::MC-1",
        "auction_number": "95",
        "raw": {
            "auction_type": "single",
            "api_scan": {"card_count": "1",
                         "mazi_watermark_verification": "matches_expected"},
            "evidence_stamp": {"status": "verified",
                               "expected_auction_number": "95"},
        },
    }
    base.update(over)
    return base


g = promotion._trusted_gate_block_reasons

# --- accepts ---------------------------------------------------------------
check("clean single, matching binding -> eligible", g(row()) == [], f"got {g(row())}")
check("card_count 1 / no api_scan -> eligible",
      g(row(raw={"auction_type": "single"})) == [],
      f"got {g(row(raw={'auction_type': 'single'}))}")
check("HOLD-level signals do NOT block (front class / unreadable wmv / no stamp)",
      g(row(raw={"auction_type": "single", "front_image_status": "stream_still",
                 "api_scan": {"mazi_watermark_verification": "unreadable"}})) == [],
      "front class + unreadable read-back must stay human-review, not server-block")
check("blank auction_type -> eligible (unknown is not a block)",
      g(row(raw={})) == [], f"got {g(row(raw={}))}")
check("expected==row auction (string vs int spelling) -> eligible",
      g(row(auction_number=95,
            raw={"evidence_stamp": {"expected_auction_number": "95"}})) == [],
      "95 vs '95' must not false-block")

# --- blocks ----------------------------------------------------------------
for at in ("bundle", "pack", "nuke", "break", "lot", "mystery"):
    check(f"auction_type={at} -> blocked",
          any(r.startswith("gate_non_single_auction_type") for r in g(row(raw={"auction_type": at}))),
          f"got {g(row(raw={'auction_type': at}))}")

check("card_count=2 -> blocked",
      "gate_multi_card_count_2" in g(row(raw={"auction_type": "single",
                                              "api_scan": {"card_count": "2"}})))
check("card_count=12 -> blocked",
      "gate_multi_card_count_12" in g(row(raw={"auction_type": "single",
                                               "api_scan": {"card_count": 12}})))
check("watermark read-back mismatch -> blocked",
      "gate_watermark_mismatch" in g(row(raw={"auction_type": "single",
                                              "api_scan": {"mazi_watermark_verification": "mismatch"}})))
check("stamp expected_auction != row auction -> blocked",
      "gate_binding_auction_mismatch" in g(row(
          auction_number="95",
          raw={"auction_type": "single",
               "evidence_stamp": {"expected_auction_number": "104"}})))
check("multiple defects -> all reasons reported",
      len(g(row(raw={"auction_type": "bundle",
                     "api_scan": {"card_count": "3",
                                  "mazi_watermark_verification": "mismatch"}}))) == 3)

# --- malformed raw never crashes --------------------------------------------
check("raw=None -> eligible, no crash", g({"raw": None, "auction_number": "1"}) == [])
check("raw not a dict -> eligible, no crash", g({"raw": "junk"}) == [])
check("card_count garbage -> ignored",
      g(row(raw={"auction_type": "single", "api_scan": {"card_count": "abc"}})) == [])

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(1 if FAIL else 0)
