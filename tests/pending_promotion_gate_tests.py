"""Phase 2 gate tests: Pending->Trusted promotion (2026-06-30).

Q1 trust model: private Trusted = watermark (auction-number binding) + single
foremost card + Gemini/image identity + human visual click. Cert/proof are the
PUBLIC/Mazified path and intentionally do NOT block here.

Covers four layers, each with positive + negative cases and per-case PASS/FAIL:
  A. promotion._pending_trusted_gate_reasons  (authoritative server-side gate)
  B. decisions._row_is_watermarked / _row_single_card_ok (advisory helpers)
  C. decisions.row_allowed_decisions          (advisory drawer gate; pending
     branch added, Identified path must stay byte-identical)
  D. app.review_decision route fallback        (Identified attempt ->
     not_in_identified_state -> Pending gate; other errors re-raise)

No production DB is touched: Parts A-C are pure functions; Part D monkeypatches
neon_conn + both promote fns to fakes.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import promotion
import decisions

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# ---------------------------------------------------------------------------
# Row builders (raw DB-row shape for Part A; API-enriched shape for Parts B/C).
# ---------------------------------------------------------------------------
def db_row(**over):
    """Minimal raw operational_pending_sales-shaped row that PASSES the gate."""
    row = {
        "source_key": "pending_sweep::TEST-0001",
        "comp_id": "TEST-0001",
        "image_front": "/x/whatnot_cards/seller/MC-1_mazi.jpg",
        "image_front_neon_url": None,
        "seller": "goodseller",
        "card_name": "Victor Wembanyama",
        "player": "Victor Wembanyama",
        "brand": "Panini",
        "set_name": "Prizm",
        "title": "2023 Prizm Victor Wembanyama Fuchsia",
        "pending_reasons": [],
        "raw": {"api_scan": {"card_count": "1"}},
    }
    row.update(over)
    return row


def api_row(**over):
    """API-enriched row that PASSES the advisory pending gate."""
    row = {
        "source_key": "pending_sweep::TEST-0001",
        "observed_in": "pending",
        "image_front_basename": "MC-1_mazi.jpg",
        "seller": "goodseller",
        "card_name": "Victor Wembanyama",
        "player": "Victor Wembanyama",
        "brand": "Panini",
        "set_name": "Prizm",
        "title": "2023 Prizm Victor Wembanyama Fuchsia",
        "pending_reasons": [],
        "proof_binding_status": "watermark_bound",
        "raw": {"api_scan": {"card_count": "1"}},
    }
    row.update(over)
    return row


# ===========================================================================
# Part A - promotion._pending_trusted_gate_reasons (authoritative gate)
# ===========================================================================
g = promotion._pending_trusted_gate_reasons

check("A1 accept: watermark+single+identity -> [] (eligible)",
      g(db_row()) == [], f"reasons={g(db_row())}")

check("A2 reject: not watermarked",
      "pending_not_watermarked" in g(db_row(image_front="/x/MC-1.jpg", raw={"api_scan": {"card_count": "1"}})),
      f"reasons={g(db_row(image_front='/x/MC-1.jpg', raw={'api_scan': {'card_count': '1'}}))}")

check("A3 reject: api_scan.card_count>=2 -> multi_card",
      "pending_multi_card" in g(db_row(raw={"api_scan": {"card_count": "2"}})),
      f"reasons={g(db_row(raw={'api_scan': {'card_count': '2'}}))}")

check("A4 reject: pending label multi-card -> multi_card",
      "pending_multi_card" in g(db_row(pending_reasons=["MULTI_CARD"])),
      f"reasons={g(db_row(pending_reasons=['MULTI_CARD']))}")

_no_id = db_row(card_name="", player="", brand="", set_name="", title="whatnot live capture")
check("A5 reject: no identity at all -> missing_identity",
      "pending_missing_identity" in g(_no_id), f"reasons={g(_no_id)}")

check("A6 reject: UNIDENTIFIED_WITH_IMAGE label -> missing_identity",
      "pending_missing_identity" in g(db_row(pending_reasons=["UNIDENTIFIED_WITH_IMAGE"])),
      f"reasons={g(db_row(pending_reasons=['UNIDENTIFIED_WITH_IMAGE']))}")

_coll = g(db_row(pending_reasons=["FILENAME_COLLISION_RISK"]))
check("A7 reject: FILENAME_COLLISION_RISK -> capture_hard_blocker + duplicate",
      "pending_capture_hard_blocker" in _coll and "pending_duplicate_or_collision" in _coll,
      f"reasons={_coll}")

check("A8 reject: banned seller tripp_cards",
      "pending_banned_seller" in g(db_row(seller="Tripp_Cards")),
      f"reasons={g(db_row(seller='Tripp_Cards'))}")

_pds = db_row(raw={"api_scan": {"card_count": "1"}, "proof_binding_status": "mismatch_neighboring_auction"})
check("A9 reject: proof bound to different sale",
      "pending_proof_different_sale" in g(_pds), f"reasons={g(_pds)}")

check("A10 accept: identity via title only (card fields blank)",
      g(db_row(card_name="", player="", brand="", set_name="",
               title="2023 Prizm Wembanyama")) == [],
      f"reasons={g(db_row(card_name='', player='', brand='', set_name='', title='2023 Prizm Wembanyama'))}")

_wm_raw = db_row(image_front="/x/MC-1.jpg",
                 raw={"api_scan": {"card_count": "1"}, "front_image_stamped": "/x/MC-1_mazi.jpg"})
check("A11 accept: watermark via raw.front_image_stamped provenance",
      g(_wm_raw) == [], f"reasons={g(_wm_raw)}")

check("A12 accept: unknown card_count (None) defers to human -> []",
      g(db_row(raw={"api_scan": {}})) == [], f"reasons={g(db_row(raw={'api_scan': {}}))}")

_multi = g(db_row(image_front="/x/MC-1.jpg", seller="tripp_cards",
                  card_name="", player="", brand="", set_name="", title="whatnot live capture",
                  raw={"api_scan": {"card_count": "3"}}))
check("A13 multi-reason: no dupes + all four present (caller sorts for the msg)",
      len(_multi) == len(set(_multi)) and set(_multi) == {"pending_not_watermarked", "pending_multi_card",
                                                          "pending_missing_identity", "pending_banned_seller"},
      f"reasons={_multi}")

# --- A14-A16: the placeholder-identity defect (Whatnot ingest writes the generic
# 'Whatnot Live Capture' into card_name AND title, so a placeholder must fail the
# identity gate in EVERY column -- not just title). Empirically verified: 7,333
# generic-only watermarked pending rows wrongly passed before this fix.
_ph_cn = db_row(card_name="Whatnot Live Capture", player="", brand="", set_name="", title="")
check("A14 reject: placeholder card_name only (no real identity) -> missing_identity",
      "pending_missing_identity" in g(_ph_cn), f"reasons={g(_ph_cn)}")

_ph_both = db_row(card_name="Whatnot Live Capture", player="", brand="", set_name="",
                  title="Whatnot Live Capture")
check("A15 reject: placeholder in card_name AND title -> missing_identity",
      "pending_missing_identity" in g(_ph_both), f"reasons={g(_ph_both)}")

# A real title rescues a placeholder card_name (title IS real identity here).
_ph_cn_real_title = db_row(card_name="Whatnot Live Capture", player="", brand="", set_name="",
                           title="2023 Prizm Victor Wembanyama Fuchsia /99")
check("A16 accept: placeholder card_name BUT real title -> [] (identity via title)",
      g(_ph_cn_real_title) == [], f"reasons={g(_ph_cn_real_title)}")


# ===========================================================================
# Part B - decisions advisory helpers (_row_is_watermarked / _row_single_card_ok)
# ===========================================================================
check("B1 watermarked via image_front_basename _mazi -> True",
      decisions._row_is_watermarked(api_row()) is True)
check("B2 not watermarked -> False",
      decisions._row_is_watermarked(api_row(image_front_basename="MC-1.jpg", raw={"api_scan": {"card_count": "1"}})) is False)
check("B3 watermarked via raw.capture_quality.front_stamped -> True",
      decisions._row_is_watermarked(api_row(image_front_basename="MC-1.jpg",
                                             raw={"api_scan": {"card_count": "1"}, "capture_quality": {"front_stamped": "true"}})) is True)
check("B4 single ok: unknown count -> True",
      decisions._row_single_card_ok(api_row(raw={"api_scan": {}})) is True)
check("B5 multi via card_count 3 -> False",
      decisions._row_single_card_ok(api_row(raw={"api_scan": {"card_count": "3"}})) is False)
check("B6 multi via pending label -> False",
      decisions._row_single_card_ok(api_row(pending_reasons=["MULTI_CARD_DETECTED"])) is False)


# ===========================================================================
# Part C - decisions.row_allowed_decisions (advisory drawer gate)
# ===========================================================================
def allowed(row):
    return set(decisions.row_allowed_decisions(row))


check("C1 pending eligible -> confirm allowed",
      "confirm" in allowed(api_row()), f"allowed={sorted(allowed(api_row()))}")
check("C2 pending NOT watermarked -> confirm NOT allowed",
      "confirm" not in allowed(api_row(image_front_basename="MC-1.jpg", raw={"api_scan": {"card_count": "1"}})),
      f"allowed={sorted(allowed(api_row(image_front_basename='MC-1.jpg', raw={'api_scan': {'card_count': '1'}})))}")
check("C3 pending banned seller -> confirm NOT allowed",
      "confirm" not in allowed(api_row(seller="kksportscards")),
      f"allowed={sorted(allowed(api_row(seller='kksportscards')))}")
check("C4 pending identity_ambiguous (UNIDENTIFIED_WITH_IMAGE) -> confirm NOT allowed",
      "confirm" not in allowed(api_row(pending_reasons=["UNIDENTIFIED_WITH_IMAGE"])),
      f"allowed={sorted(allowed(api_row(pending_reasons=['UNIDENTIFIED_WITH_IMAGE'])))}")
check("C5 pending multi-card -> confirm NOT allowed",
      "confirm" not in allowed(api_row(raw={"api_scan": {"card_count": "4"}})),
      f"allowed={sorted(allowed(api_row(raw={'api_scan': {'card_count': '4'}})))}")
# C5b: placeholder card_name only (no real identity) -> advisory must NOT offer
# confirm (mirrors the authoritative A14 gate; the two must agree).
_c5b = api_row(card_name="Whatnot Live Capture", player="", brand="", set_name="", title="")
check("C5b pending placeholder card_name only -> confirm NOT allowed",
      "confirm" not in allowed(_c5b), f"allowed={sorted(allowed(_c5b))}")

# Identified-path regression: observed_in='identified' must still get confirm
# the SAME way as before (pending branch is observed_in=='pending'-gated, so it
# can never fire here).
_ident = api_row(observed_in="identified")
check("C6 identified row still gets confirm (byte-identical path)",
      "confirm" in allowed(_ident), f"allowed={sorted(allowed(_ident))}")
# An identified row that is NOT watermarked must STILL get confirm (the
# Identified gate never checked watermark) - proves pending logic didn't leak in.
_ident_nowm = api_row(observed_in="identified", image_front_basename="MC-1.jpg",
                      raw={"api_scan": {"card_count": "1"}})
check("C7 identified NOT-watermarked still gets confirm (pending logic did not leak)",
      "confirm" in allowed(_ident_nowm), f"allowed={sorted(allowed(_ident_nowm))}")


# ===========================================================================
# Part D - route fallback (Identified -> not_in_identified_state -> Pending)
# ===========================================================================
try:
    import app
    _app_ok = True
except ModuleNotFoundError as exc:
    print(f"NOTE: Part D skipped, app dependency unavailable ({exc.name})")
    _app_ok = False

if _app_ok:
    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_neon_conn():
        return _FakeConn()

    CALLS: list[str] = []

    def _ident_raises_not_in_identified(conn, body):
        CALLS.append("identified")
        raise ValueError("not_in_identified_state")

    def _ident_raises_other(conn, body):
        CALLS.append("identified")
        raise ValueError("cannot_promote_missing_front_image")

    def _ident_ok(conn, body):
        CALLS.append("identified")
        return {"status": "ident_ok", "promotion": "identified_to_trusted"}

    def _pending_fake(conn, body):
        CALLS.append("pending")
        return {"status": "pending_ok", "promotion": "pending_to_trusted",
                "source_key": body.get("source_key")}

    class FakeRequest:
        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    def post(payload):
        resp = asyncio.run(app.review_decision(FakeRequest(payload)))
        return resp.status_code, json.loads(resp.body.decode("utf-8"))

    # Open the write gate (all-identified scope) so Step 4 is reached.
    os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED"] = "1"
    os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_SOURCE_KEYS"] = ""
    os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_TEST_RUN_ID"] = ""
    os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL"] = "1"

    confirm_payload = {"comp_id": "TEST-PEND", "source_key": "TEST-PEND::1", "decision": "confirm"}

    _orig_neon = app.neon_conn
    _orig_ident = app.promote_identified_to_trusted
    _orig_pend = app.promote_pending_to_trusted
    app.neon_conn = _fake_neon_conn
    try:
        # D1: identified misses -> pending fallback fires -> 201
        CALLS.clear()
        app.promote_identified_to_trusted = _ident_raises_not_in_identified
        app.promote_pending_to_trusted = _pending_fake
        status, body = post(confirm_payload)
        check("D1 identified-miss -> pending fallback -> 201",
              status == 201 and body.get("promotion") == "pending_to_trusted" and CALLS == ["identified", "pending"],
              f"http={status} calls={CALLS} body={body}")

        # D2: identified raises a DIFFERENT error -> re-raise, pending NOT called
        CALLS.clear()
        app.promote_identified_to_trusted = _ident_raises_other
        app.promote_pending_to_trusted = _pending_fake
        status, body = post(confirm_payload)
        check("D2 identified other-error -> NO pending fallback (400)",
              status == 400 and "pending" not in CALLS and body.get("error") == "cannot_promote_missing_front_image",
              f"http={status} calls={CALLS} body={body}")

        # D3: identified succeeds -> pending NOT called
        CALLS.clear()
        app.promote_identified_to_trusted = _ident_ok
        app.promote_pending_to_trusted = _pending_fake
        status, body = post(confirm_payload)
        check("D3 identified success -> pending NOT called (201)",
              status == 201 and "pending" not in CALLS and body.get("promotion") == "identified_to_trusted",
              f"http={status} calls={CALLS} body={body}")
    finally:
        app.neon_conn = _orig_neon
        app.promote_identified_to_trusted = _orig_ident
        app.promote_pending_to_trusted = _orig_pend


# ---------------------------------------------------------------------------
print("=" * 84)
all_pass = True
for name, ok, detail in results:
    tag = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{tag}] {name:<70}  {detail}")
print("=" * 84)
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
