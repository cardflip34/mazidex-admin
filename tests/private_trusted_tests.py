"""PART B — private-Trusted (watermark + visual) row action. Pure; no DB, no network.

Proves:
  * the flag is fail-closed and OFF by default;
  * the decision is DB-supported but row-action-ONLY (never in the generic menu);
  * the gate requires BOTH a matches_expected watermark read-back bound to the
    row's own auction AND an explicit human visual confirmation;
  * the audit payload carries reviewer identity + timestamp and NO private values;
  * the state is explicitly marked not-public / not-Mazified / not-a-cert.

Run:  python3 tests/private_trusted_tests.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import row_actions  # noqa: E402
from decisions import (  # noqa: E402
    DB_VALID_DECISIONS, _ROW_ACTION_ONLY_DECISIONS, row_allowed_decisions,
)
from row_actions import (  # noqa: E402
    PRIVATE_TRUSTED_DECISION, private_trusted_blockers, private_trusted_payload,
)

FLAG = "MAZI_PRIVATE_TRUSTED_REVIEW_PROMOTE"
_fails = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        _fails.append(label)


def rescued_row(**over):
    row = {
        "source_view": "identified",
        "source_key": "identified_sweep::MC-20260904120000-x1",
        "comp_id": "MC-20260904120000-x1",
        "review_id": "identified:MC-20260904120000-x1",
        "source_file": "live_import:whatnot_auctions.json",
        "auction_number": 799,
        "seller": "debutsports",
        "sold_price": 8900.0,
        "player": "Kobe Bryant",
        "image_front": None,
        "image_front_neon_url": None,
        "image_review_front": "whatnot_cards/card_b123_799_20260904_mazi.jpg",
        "review_decision": None,
        "raw": {
            "front_image_status": "missing_or_context_only",
            "binding_status": "proof_review",
            "official_front_rejected": True,
            "trusted_eligible": False,
            "review_front_admitted": True,
            "evidence_stamp": {"enabled": True, "expected_auction_number": "799"},
            "api_scan": {
                "mazi_watermark_verification": "matches_expected",
                "mazi_watermark_auction_number": "799",
            },
        },
    }
    row.update(over)
    return row


print("\n=== A. flag is fail-closed ===")
os.environ.pop(FLAG, None)
check("A1 default OFF", config.private_trusted_review_promote_enabled() is False)
for val, want in [("1", True), ("true", True), ("YES", True), ("on", True),
                  ("0", False), ("", False), ("off", False), ("maybe", False)]:
    os.environ[FLAG] = val
    check(f"A2 '{val}' -> {want}", config.private_trusted_review_promote_enabled() is want)
os.environ.pop(FLAG, None)

print("\n=== B. decision wiring ===")
check("B1 DB-supported", PRIVATE_TRUSTED_DECISION in DB_VALID_DECISIONS)
check("B2 row-action-only", PRIVATE_TRUSTED_DECISION in _ROW_ACTION_ONLY_DECISIONS)
check("B3 in config.VALID_DECISIONS", PRIVATE_TRUSTED_DECISION in config.VALID_DECISIONS)
check("B4 not 'confirm' (so trusted_sales_current cannot see it)",
      PRIVATE_TRUSTED_DECISION != "confirm")
for view in ("identified", "pending", "trusted", "feed", "mazified"):
    allowed = row_allowed_decisions({"source_view": view, "raw": {}})
    check(f"B5.{view} never offered in the generic menu",
          PRIVATE_TRUSTED_DECISION not in allowed, str(allowed))

print("\n=== C. gate: happy path ===")
check("C1 eligible with read-back + visual confirm",
      private_trusted_blockers(rescued_row(), visual_confirmed=True) == [],
      str(private_trusted_blockers(rescued_row(), visual_confirmed=True)))

print("\n=== D. gate: fail-closed cases ===")


def blocked(label, row, *, visual_confirmed=True, expect=None):
    bl = private_trusted_blockers(row, visual_confirmed=visual_confirmed)
    ok = bool(bl) and (expect is None or expect in bl)
    check(label, ok, str(bl))


blocked("D1 no human visual confirm", rescued_row(), visual_confirmed=False,
        expect="visual_confirmation_required")

r = rescued_row(); r["raw"]["api_scan"]["mazi_watermark_verification"] = "not_present"
blocked("D2 no read-back", r, expect="watermark_not_read_back:not_present")

r = rescued_row(); r["raw"]["api_scan"]["mazi_watermark_verification"] = "mismatch"
blocked("D3 mismatch read-back", r)

r = rescued_row(); r["raw"]["api_scan"]["mazi_watermark_verification"] = "verified"
blocked("D4 'verified' is NOT a read-back", r)

r = rescued_row(); r["raw"]["api_scan"]["mazi_watermark_auction_number"] = "800"
blocked("D5 read-back names another auction", r, expect="watermark_auction_ne_row")

r = rescued_row(); r["raw"]["evidence_stamp"]["expected_auction_number"] = "801"
blocked("D6 stamp targets another auction", r, expect="expected_auction_ne_row")

blocked("D7 no auction number", rescued_row(auction_number=None), expect="no_auction_number")
blocked("D8 no review front", rescued_row(image_review_front=None), expect="no_review_front")
blocked("D9 row has an official front",
        rescued_row(image_front="whatnot_cards/card_b1_799.jpg"),
        expect="row_has_official_front")
blocked("D10 row has an official neon url",
        rescued_row(image_front_neon_url="https://r2/x.jpg"),
        expect="row_has_official_front")
blocked("D11 pending row is out of scope", rescued_row(source_view="pending"),
        expect="not_an_identified_row")
blocked("D12 trusted row is out of scope", rescued_row(source_view="trusted"),
        expect="not_an_identified_row")
blocked("D13 banned seller", rescued_row(seller="tripp_cards"), expect="banned_seller")

r = rescued_row(); r["raw"]["front_image_status"] = "valid_card_front"
blocked("D14 unexpected front class", r, expect="unexpected_front_image_status")

# An empty dict is still a dict, so it fails on every dimension rather than the
# single "row_missing" sentinel. Blocked is blocked -- that is the fail-closed shape.
blocked("D15 empty row", {})
blocked("D16 non-dict row", None, expect="row_missing")

r = rescued_row(); r["raw"] = None
blocked("D17 null raw is not a crash", r)

print("\n=== E. audit payload ===")
p = private_trusted_payload(rescued_row(), reviewer="andy", notes="looks right",
                            visual_confirmed=True)
check("E1 decision", p["decision"] == PRIVATE_TRUSTED_DECISION)
check("E2 reviewer recorded", p["reviewer"] == "andy")
check("E3 timestamp recorded", bool(p["row_meta"]["visual_confirmed_at"]))
check("E4 visual confirm recorded", p["row_meta"]["visual_confirmed"] is True)
check("E5 binding evidence recorded",
      p["row_meta"]["watermark_verification"] == "matches_expected"
      and p["row_meta"]["row_auction_number"] == "799")
for key in ("public_ready", "mazified", "is_cert", "public_mazidex_eligible",
            "auto_promotable"):
    check(f"E6.{key} explicitly False", p["row_meta"][key] is False)
check("E7 review frame recorded as a BASENAME only",
      p["row_meta"]["review_front_basename"] == "card_b123_799_20260904_mazi.jpg")

blob = repr(p).lower()
for private in ("buyer", "winner", "bidder", "chat", "handle", "cookie", "token",
                "sticky", "secret"):
    check(f"E8 no private value '{private}'", private not in blob)

print("\n=== F. auction-number normalisation ===")
for variant in ("799", "#799", 799, 799.0, " 799 "):
    r = rescued_row(auction_number=variant)
    check(f"F1 {variant!r} still eligible",
          private_trusted_blockers(r, visual_confirmed=True) == [],
          str(private_trusted_blockers(r, visual_confirmed=True)))

print("\n=== G. purity ===")
r = rescued_row()
before = copy.deepcopy(r)
private_trusted_blockers(r, visual_confirmed=True)
private_trusted_payload(r, reviewer="andy", notes=None, visual_confirmed=True)
check("G1 the row is not mutated", r == before)

print("\n" + ("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}"))
sys.exit(1 if _fails else 0)
