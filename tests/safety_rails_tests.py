"""mazidex-admin v0 — safety-rail focused tests.

Scope: TIER-B safety patch drafted 2026-05-29 for /api/v1/row source-key
       resolution + drift, /api/v1/review-decision strict 422, row-state-
       aware MAZIFIED button gating.

Mode:  Pure-function tests. Imports decisions.py directly. NO Neon hit,
       NO 8504 HTTP hit, NO Chrome bridge hit. Safe to run while Chrome 1
       (bridge 8767) and Chrome 2 (bridge 8768) are doing live QA against
       the deployed 8504.

Usage: cd ~/mazidex-admin && ./venv/bin/python3 tests/safety_rails_tests.py
"""
from __future__ import annotations

import os
import sys

# Make `decisions` importable when run from project root.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from decisions import (
    DB_VALID_DECISIONS,
    _ROW_ACTION_ONLY_DECISIONS,
    UNMAPPED_SEMANTIC_DECISIONS,
    is_db_supported_decision,
    mazified_blockers,
    mazified_button_state,
    resolve_preferred_row,
    row_allowed_decisions,
    row_hard_block_labels,
    source_drift_meta,
    unmapped_decision_reason,
    validate_decision_payload,
)


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# --------------------------------------------------------------------------
# T1 — Unsupported semantic decision must surface as 422-flavored reason.
#
# Maps to acceptance criterion: "unsupported decision returns 422".
# We assert this at the pure-function layer (route handler will translate
# the helper return into HTTP 422 — that integration assertion is in the
# tomorrow-deploy step list, not here).
# --------------------------------------------------------------------------
check(
    "T1a needs_better_image is NOT db-supported",
    not is_db_supported_decision("needs_better_image"),
    f"is_db_supported_decision('needs_better_image')={is_db_supported_decision('needs_better_image')}",
)
check(
    "T1b unmapped_decision_reason gives semantic tag",
    unmapped_decision_reason("needs_better_image") == "unmapped_semantic_decision: needs_better_image",
    f"reason={unmapped_decision_reason('needs_better_image')!r}",
)
check(
    "T1c unknown gibberish gives unknown_decision tag",
    unmapped_decision_reason("totally_made_up_xyz") == "unknown_decision: totally_made_up_xyz",
    f"reason={unmapped_decision_reason('totally_made_up_xyz')!r}",
)
check(
    "T1d mazified IS db-supported",
    is_db_supported_decision("mazified"),
    f"is_db_supported_decision('mazified')={is_db_supported_decision('mazified')}",
)
check(
    "T1e DB_VALID_DECISIONS is the 7 generic + deleted_from_8504 row-action",
    DB_VALID_DECISIONS == frozenset({"confirm","flag","reject","workable","clear","mazified","deny","deleted_from_8504"}),
    f"DB_VALID_DECISIONS={sorted(DB_VALID_DECISIONS)}",
)


# --------------------------------------------------------------------------
# T2 — Already-mazified row cannot render or click MAZIFIED.
# --------------------------------------------------------------------------
mazified_row = {
    "comp_id": "MC-test-mazified",
    "review_decision": "mazified",
    "mazi_cert_status": "verified",
    "mazi_cert_id": "MAZI-1234",
    "image_front_basename": "card_b1234_5_front.jpg",
    "chips": ["MAZIFIED REVIEW", "INTERNAL REVIEW ONLY"],
}
allowed_after = row_allowed_decisions(mazified_row)
check(
    "T2a mazified NOT in row_allowed_decisions for mazified row",
    "mazified" not in allowed_after,
    f"allowed_decisions={allowed_after}",
)
mb = mazified_button_state(mazified_row)
check(
    "T2b mazified_button_state returns {enabled: False, reason: already_mazified}",
    mb.get("enabled") is False and mb.get("reason") == "already_mazified",
    f"mazified_button={mb}",
)


# --------------------------------------------------------------------------
# T3 — Source drift between clicked tile and resolved row.
# --------------------------------------------------------------------------
clicked = {
    "comp_id":             "MC-test-drift",
    "source_key":          "bot_55555.jsonl::MC-test-drift::42",
    "feed_observation_id": "10000",
}
resolved_same = {
    "comp_id":             "MC-test-drift",
    "source_key":          "bot_55555.jsonl::MC-test-drift::42",
    "feed_observation_id": "10000",
}
resolved_different_source = {
    "comp_id":             "MC-test-drift",
    "source_key":          "whatnot_auctions.json::MC-test-drift::42",
    "feed_observation_id": "20000",
}
check(
    "T3a no drift when clicked == resolved",
    source_drift_meta(clicked, resolved_same) is None,
    "expected None",
)
d = source_drift_meta(clicked, resolved_different_source)
check(
    "T3b drift detected when source_key differs",
    bool(d and d.get("drift") is True),
    f"drift={d}",
)
check(
    "T3c drift policy disables actions",
    bool(d and "disable" in (d.get("policy") or "").lower()),
    f"policy={(d or {}).get('policy')}",
)
check(
    "T3d drift diffs name the bad fields",
    bool(d and any(f["field"] == "source_key" for f in (d or {}).get("diffs", []))),
    f"diffs={(d or {}).get('diffs')}",
)


# --------------------------------------------------------------------------
# T4 — source_key + feed_observation_id resolves the exact drawer row
#      (here we simulate the server-side picker: it should pick the
#      candidate whose source_key matches exactly).
# --------------------------------------------------------------------------
candidates = [
    {"comp_id": "MC-x", "source_key": "A", "feed_observation_id": 1},
    {"comp_id": "MC-x", "source_key": "B", "feed_observation_id": 2},
    {"comp_id": "MC-x", "source_key": "C", "feed_observation_id": 3},
]
# Resolution now exercises the SHARED helper from decisions.py -- the same
# function app.py /api/v1/row calls -- not a local replica.
picked, exact_match = resolve_preferred_row(candidates, "B")
check(
    "T4a exact source_key match wins over first-row fallback",
    picked["source_key"] == "B" and picked["feed_observation_id"] == 2 and exact_match is True,
    f"picked={picked} exact={exact_match}",
)
picked_no_match, exact_no = resolve_preferred_row(candidates, "Z")
check(
    "T4b unknown source_key falls back to first candidate (drift will be raised)",
    picked_no_match["source_key"] == "A" and exact_no is False,
    f"picked_fallback={picked_no_match} exact={exact_no}",
)
none_pick, none_exact = resolve_preferred_row([], "B")
check(
    "T4b2 empty candidate list -> (None, False)",
    none_pick is None and none_exact is False,
    f"none_pick={none_pick} none_exact={none_exact}",
)
no_sk_pick, no_sk_exact = resolve_preferred_row(candidates, None, 3)
check(
    "T4b3 no source_key requested -> exact feed_observation_id wins",
    no_sk_pick["source_key"] == "C" and no_sk_exact is True,
    f"no_sk_pick={no_sk_pick} exact={no_sk_exact}",
)
# Drift should be raised by source_drift_meta on the fallback.
fallback_drift = source_drift_meta(
    {"comp_id":"MC-x","source_key":"Z","feed_observation_id":"99"},
    picked_no_match,
)
check(
    "T4c drift raised when comp_id-only fallback used",
    bool(fallback_drift and fallback_drift.get("drift") is True),
    f"fallback_drift={fallback_drift}",
)


# --------------------------------------------------------------------------
# T5 — Image-missing + identity-ambiguous + proof-mismatch each block MAZIFIED.
# --------------------------------------------------------------------------
safe_base = {
    "image_front_basename": "ok.jpg",
    "image_proof_basename": "proof.jpg",
    "proof_image_status": "present",
    "proof_binding_status": "verified",
    "mazi_cert_status": "verified",
    "publish_ready": True,
    "public_image_safe_reason": "ok",
}
no_image_row = {
    **safe_base,
    "comp_id": "MC-no-img",
    "image_front_basename": "",
}
amb_id_row = {
    **safe_base,
    "comp_id": "MC-amb",
    "identity_enrichment_disagreement": "disagree",
}
proof_diff_row = {
    **safe_base,
    "comp_id": "MC-proof-diff",
    "proof_binding_status": "mismatch_neighboring_auction",
}
hard_block_row = {
    **safe_base,
    "comp_id": "MC-hardblock",
    "chips": ["HARD BLOCKED", "PROOF_BLOCKED"],
}
check(
    "T5a image_missing -> mazified disabled",
    mazified_button_state(no_image_row)["reason"] == "image_missing_or_broken",
    f"state={mazified_button_state(no_image_row)}",
)
check(
    "T5b identity_ambiguous -> mazified disabled",
    mazified_button_state(amb_id_row)["reason"] == "identity_ambiguous",
    f"state={mazified_button_state(amb_id_row)}",
)
check(
    "T5c proof_different_sale -> mazified disabled",
    mazified_button_state(proof_diff_row)["reason"] == "proof_different_sale",
    f"state={mazified_button_state(proof_diff_row)}",
)
check(
    "T5d hard_blocker -> mazified disabled + only negative-path decisions",
    mazified_button_state(hard_block_row)["reason"] == "hard_blocker_present"
    and set(row_allowed_decisions(hard_block_row)).issubset({"reject","deny","flag","clear"}),
    f"button={mazified_button_state(hard_block_row)} allowed={row_allowed_decisions(hard_block_row)}",
)
proof_missing_row = {**safe_base, "comp_id": "MC-proof-missing", "image_proof_basename": "", "proof_image_status": "missing"}
cert_pending_row = {**safe_base, "comp_id": "MC-cert-pending", "mazi_cert_status": "pending"}
unsafe_image_row = {**safe_base, "comp_id": "MC-image-unsafe", "public_image_safe_reason": "unsafe_internal_only"}
binding_review_row = {**safe_base, "comp_id": "MC-binding", "pending_reasons": ["BINDING_REVIEW"]}
collision_row = {**safe_base, "comp_id": "MC-collision", "pending_reasons": ["FILENAME_COLLISION_RISK"]}
banned_seller_row = {**safe_base, "comp_id": "MC-banned", "seller": "kksportscards"}
check(
    "T5e shared blocker predicate disables proof/cert/image/policy blockers",
    mazified_button_state(proof_missing_row)["reason"] == "proof_missing"
    and mazified_button_state(cert_pending_row)["reason"] == "cert_or_evidence_pending"
    and mazified_button_state(unsafe_image_row)["reason"] == "public_or_private_image_unsafe"
    and mazified_button_state(binding_review_row)["reason"] == "binding_review"
    and mazified_button_state(collision_row)["reason"] == "capture_hard_blocker"
    and mazified_button_state(banned_seller_row)["reason"] == "banned_seller",
    f"proof={mazified_button_state(proof_missing_row)} cert={mazified_button_state(cert_pending_row)} image={mazified_button_state(unsafe_image_row)} binding={mazified_button_state(binding_review_row)} collision={mazified_button_state(collision_row)} banned={mazified_button_state(banned_seller_row)}",
)
check(
    "T5f blocker labels are visible tile/drawer strip labels",
    "PROOF MISSING" in row_hard_block_labels(proof_missing_row)
    and "CERT PENDING" in row_hard_block_labels(cert_pending_row)
    and "IMAGE UNKNOWN" in row_hard_block_labels(unsafe_image_row),
    f"labels={row_hard_block_labels(proof_missing_row)} {row_hard_block_labels(cert_pending_row)} {row_hard_block_labels(unsafe_image_row)}",
)


# --------------------------------------------------------------------------
# T6 — Clean row should permit MAZIFIED + the full DB-supported decision set.
# --------------------------------------------------------------------------
clean_row = {
    **safe_base,
    "comp_id": "MC-clean",
    "image_front_basename": "card_b9999_1_front.jpg",
    "chips": ["INTERNAL REVIEW ONLY","NOT TRUSTED","NOT MAZIFIED",
              "NOT PUBLIC READY","NOT VALUATION SAFE"],
    "pending_reasons": [],
    "identity_enrichment_disagreement": None,
}
check(
    "T6a clean row -> mazified ENABLED",
    mazified_button_state(clean_row)["enabled"] is True and mazified_blockers(clean_row) == [],
    f"state={mazified_button_state(clean_row)}",
)
check(
    "T6b clean row -> all generic DB decisions allowed (row-action-only excluded)",
    set(row_allowed_decisions(clean_row)) == (DB_VALID_DECISIONS - _ROW_ACTION_ONLY_DECISIONS),
    f"allowed={row_allowed_decisions(clean_row)}",
)
check(
    "T6c deleted_from_8504 is row-action-only, never in the generic per-row menu",
    "deleted_from_8504" not in set(row_allowed_decisions(clean_row)),
    f"allowed={row_allowed_decisions(clean_row)}",
)

high_value_clean_row = dict(clean_row)
high_value_clean_row.update({
    "comp_id": "MC-high-value-clean",
    "sold_price": 5000,
    "trust_bucket": "REVIEW_HIGH_VALUE",
    "pending_reasons": ["PRICE_HIGH_REVIEW"],
})
check(
    "T6c high value is metadata only; clean high-price row can be mazified",
    mazified_button_state(high_value_clean_row).get("enabled") is True
    and "mazified" in row_allowed_decisions(high_value_clean_row),
    f"state={mazified_button_state(high_value_clean_row)} allowed={row_allowed_decisions(high_value_clean_row)}",
)

audio_only_row = dict(clean_row)
audio_only_row.update({
    "comp_id": "MC-audio-advisory",
    "title": "2023 Test Player Silver Prizm",
    "pending_reasons": ["LOW_CONFIDENCE"],
    "warnings": ["audio-image-conflict", "audio-non-single", "audio-card-count"],
    "identity_enrichment_disagreement": "conflict",
    "raw": {"_review_reason": "low-verification-confidence audio-image-conflict"},
})
check(
    "T6d audio-only identity flags are advisory and do not disable MAZIFIED",
    mazified_button_state(audio_only_row).get("enabled") is True,
    f"state={mazified_button_state(audio_only_row)} allowed={row_allowed_decisions(audio_only_row)}",
)

image_supported_identity_row = dict(clean_row)
image_supported_identity_row.update({
    "comp_id": "MC-image-supported-identity",
    "pending_reasons": ["LOW_CONFIDENCE"],
    "raw": {"_review_reason": "gemini low confidence image_identity"},
})
check(
    "T6e image-supported low confidence still disables MAZIFIED",
    mazified_button_state(image_supported_identity_row)["reason"] == "identity_ambiguous",
    f"state={mazified_button_state(image_supported_identity_row)}",
)


# --------------------------------------------------------------------------
# T7 — Payload validator still rejects missing comp_id/source_key.
# --------------------------------------------------------------------------
ok, reason = validate_decision_payload({})
check(
    "T7a empty payload -> 400 missing_comp_id",
    not ok and reason == "missing_comp_id",
    f"reason={reason}",
)
ok, reason = validate_decision_payload({"comp_id": "X"})
check(
    "T7b comp_id only -> 400 missing_source_key",
    not ok and reason == "missing_source_key",
    f"reason={reason}",
)
ok, reason = validate_decision_payload({"comp_id": "X","source_key":"Y","decision":"NONSENSE"})
check(
    "T7c invalid decision string -> 400 invalid_decision (caught at outer validator)",
    not ok and reason.startswith("invalid_decision"),
    f"reason={reason}",
)
ok, reason = validate_decision_payload({"comp_id": "X","source_key":"Y","decision":"needs_better_image"})
check(
    "T7d known-but-semantic decision passes shape check (DB-strict check is separate)",
    ok,
    f"ok={ok} reason={reason}",
)
# But the DB-strict layer rejects it:
check(
    "T7e ... and is_db_supported_decision still rejects it (route returns 422)",
    not is_db_supported_decision("needs_better_image"),
    "",
)


# --------------------------------------------------------------------------
# T8 — Footer copy matches health write state.
# This test stays at the pure-function layer because the actual footer
# update happens in static/app.js. We assert the contract: when
# review_write_enabled is False, footer reads "READ-ONLY MODE";
# when True, footer reads "decision writes ACCEPTED" (UTF-8 middot variant
# is allowed). The JS uses unicode escape · which renders as ·.
# --------------------------------------------------------------------------
expected_closed = "READ-ONLY MODE"
expected_open   = "decision writes ACCEPTED"
expected_policy_open = "actions restricted by operator policy"

with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as f:
    js = f.read()
check(
    "T8a footer copy: write closed branch contains 'READ-ONLY MODE'",
    expected_closed in js,
    f"present={expected_closed in js}",
)
check(
    "T8b footer copy: write open branch contains 'decision writes ACCEPTED'",
    expected_open in js,
    f"present={expected_open in js}",
)
check(
    "T8c footer copy: operator-policy variant present",
    expected_policy_open in js,
    f"present={expected_policy_open in js}",
)


# --------------------------------------------------------------------------
# T9 — Read-only page load causes no decision_events change.
# This is enforced by the architecture: GET endpoints touched by SPA
# render (/api/v1/health, /queue, /queue/counts, /sources/counts, /row)
# do NOT write to review_decision_events. We assert this by source-scan
# of app.py: no INSERT into review_decision_events outside review-decision
# POST + write_decision().
# --------------------------------------------------------------------------
with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
    app_src = f.read()
# Count INSERT INTO review_decision_events occurrences (case-insensitive).
inserts = app_src.lower().count("insert into review_decision_events")
check(
    "T9a app.py contains zero INSERT into review_decision_events",
    inserts == 0,
    f"insert_count={inserts}  (writes are encapsulated in decisions.write_decision)",
)
with open(os.path.join(ROOT, "decisions.py"), encoding="utf-8") as f:
    dec_src = f.read()
check(
    "T9b decisions.py write_decision is the only INSERT path",
    dec_src.lower().count("insert into review_decision_events") == 1,
    f"insert_count={dec_src.lower().count('insert into review_decision_events')}",
)


# --------------------------------------------------------------------------
# T10 — High Value no longer acts as a workflow/trust bucket and
#       audio-only low-verification does not drive proof review.
# --------------------------------------------------------------------------
with open(os.path.join(ROOT, "queues.py"), encoding="utf-8") as f:
    queue_src = f.read()
check(
    "T10a high-value view is price-only, not REVIEW_HIGH_VALUE-gated",
    "OR trust_bucket = 'REVIEW_HIGH_VALUE'" not in queue_src
    and "OR (pending_reasons ? 'PRICE_HIGH_REVIEW')" not in queue_src,
    "high_value query has no trust_bucket/pending_reason OR gate",
)
check(
    "T10b active queue SQL does not use PRICE_HIGH_REVIEW as high-value gate",
    "PRICE_HIGH_REVIEW" not in queue_src,
    "PRICE_HIGH_REVIEW absent from queues.py active source",
)
check(
    "T10c proof review SQL does not include audio-only low-verification-confidence",
    "low-verification-confidence" not in queue_src,
    "audio-only low-verification-confidence absent from queues.py active source",
)


# --------------------------------------------------------------------------
# Print results, exit non-zero on any failure so CI catches it.
# --------------------------------------------------------------------------
print("=" * 80)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] {name:<70}  {detail}")
print("=" * 80)
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
