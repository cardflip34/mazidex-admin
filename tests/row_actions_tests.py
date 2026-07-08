"""mazidex-admin — Phase 3a DELETE row-action focused tests.

Scope: row_actions.py pure helpers (keep-one-image, source-view resolution,
       deletable-row picking, soft-hide payload shape, JSONL audit record),
       PLUS the cross-module invariants the DELETE route depends on:
         * decisions.py  — deleted_from_8504 is DB-supported, row-action-only,
                           never in the generic per-row menu; front_swapped is
                           an unmapped semantic decision (friendly 422).
         * config.py     — deleted_from_8504 in the wide VALID_DECISIONS set so
                           validate_decision_payload accepts the payload shape;
                           DB_VALID_DECISIONS subset of VALID_DECISIONS.
         * queues.py     — every user-facing tab + counts excludes the soft-hid
                           decision; HIDDEN_EXCLUSION uses latest-decision
                           semantics; the 3 self-excluding tabs are left alone.

Mode:  Pure-function / string-introspection. Imports row_actions, decisions,
       config, queues directly. NO Neon hit, NO 8504 HTTP hit, NO image/byte
       write (the only filesystem write is one JSONL append into a tempdir).
       Safe to run while Chrome is doing live QA against the deployed 8504.

Usage: cd ~/mazidex-admin && ./venv/bin/python3 tests/row_actions_tests.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

# Make project modules importable when run from project root.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402
import queues  # noqa: E402
from decisions import (  # noqa: E402
    DB_VALID_DECISIONS,
    UNMAPPED_SEMANTIC_DECISIONS,
    _ROW_ACTION_ONLY_DECISIONS,
    is_db_supported_decision,
    row_allowed_decisions,
    validate_decision_payload,
)
import row_actions as ra  # noqa: E402


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# A representative fully-populated identified row (proof path on purpose so the
# JSONL keeps a /Users/ path; the HTTP layer scrubs to basename, the ledger does
# not).
ROW_IDENTIFIED = {
    "comp_id": "CMP-0001",
    "source_key": "identified_sweep::MC-20260623083759-p56171-0242-a503",
    "source_view": "identified",
    "observed_in": "identified",
    "review_id": 7,
    "source_file": "proof_a503.jpg",
    "auction_number": 4503,
    "image_front": "/Users/stavrosaim4/cards/card_front_a503.jpg",
    "image_front_neon_url": "https://r2.example/front_a503.jpg",
    "ops_display_image": "ops_a503.jpg",
}


# --------------------------------------------------------------------------
# A — _clean_str
# --------------------------------------------------------------------------
check("A1 _clean_str(None) -> ''", ra._clean_str(None) == "", repr(ra._clean_str(None)))
check("A2 _clean_str('  x ') trims", ra._clean_str("  x ") == "x", "")
check('A3 _clean_str(\'""\') -> \'\'', ra._clean_str('""') == "", "quoted-empty JSON extract")
check("A4 _clean_str(\"''\") -> ''", ra._clean_str("''") == "", "single-quoted-empty")
check("A5 _clean_str(4503) -> '4503'", ra._clean_str(4503) == "4503", "numeric coerced")


# --------------------------------------------------------------------------
# B — resolve_source_view / is_deletable_row
# --------------------------------------------------------------------------
check(
    "B1 source_view literal preferred over observed_in",
    ra.resolve_source_view({"source_view": "Pending", "observed_in": "identified"}) == "pending",
    "lowercased",
)
check(
    "B2 falls back to observed_in when no source_view",
    ra.resolve_source_view({"observed_in": "Identified"}) == "identified",
    "",
)
check("B3 unknown row -> ''", ra.resolve_source_view({}) == "", "")
check("B4 non-dict -> ''", ra.resolve_source_view(None) == "", "")
check("B5 identified is deletable", ra.is_deletable_row({"source_view": "identified"}) is True, "")
check("B6 pending is deletable", ra.is_deletable_row({"source_view": "pending"}) is True, "")
check("B7 trusted NOT deletable", ra.is_deletable_row({"source_view": "trusted"}) is False, "")
check("B8 mazified NOT deletable", ra.is_deletable_row({"source_view": "mazified"}) is False, "")
check("B9 feed NOT deletable", ra.is_deletable_row({"source_view": "feed"}) is False, "")


# --------------------------------------------------------------------------
# C — pick_deletable_row (identified-first; None when only trusted/empty)
# --------------------------------------------------------------------------
rows_both = [
    {"source_view": "trusted", "comp_id": "T"},
    {"source_view": "pending", "comp_id": "P"},
    {"source_view": "identified", "comp_id": "I"},
]
picked = ra.pick_deletable_row(rows_both)
check("C1 identified preferred over pending", picked is not None and picked.get("comp_id") == "I", str(picked))
picked2 = ra.pick_deletable_row(
    [{"source_view": "pending", "comp_id": "P"}, {"source_view": "feed", "comp_id": "F"}]
)
check("C2 pending chosen when no identified", picked2 is not None and picked2.get("comp_id") == "P", str(picked2))
check(
    "C3 None when only trusted/mazified/feed",
    ra.pick_deletable_row(
        [{"source_view": "trusted"}, {"source_view": "mazified"}, {"source_view": "feed"}]
    )
    is None,
    "trusted-only row not deletable through this action",
)
check("C4 None on empty list", ra.pick_deletable_row([]) is None, "")
check("C5 None on None", ra.pick_deletable_row(None) is None, "")
check(
    "C6 non-dict items skipped",
    ra.pick_deletable_row([None, "x", {"source_view": "pending", "comp_id": "P"}]) is not None,
    "",
)


# --------------------------------------------------------------------------
# D — resolve_keep_one_image (priority + fallback + None + empty-skip)
# --------------------------------------------------------------------------
check(
    "D1 image_front wins",
    ra.resolve_keep_one_image(ROW_IDENTIFIED) == "/Users/stavrosaim4/cards/card_front_a503.jpg",
    "",
)
check(
    "D2 falls to neon_url when no local front",
    ra.resolve_keep_one_image(
        {"image_front": "", "image_front_neon_url": "https://r2/x.jpg", "ops_display_image": "o.jpg"}
    )
    == "https://r2/x.jpg",
    "empty-string front skipped",
)
check(
    "D3 falls to ops_display_image deepest",
    ra.resolve_keep_one_image({"ops_display_image": "ops_x.jpg"}) == "ops_x.jpg",
    "",
)
check("D4 None when no front image at all", ra.resolve_keep_one_image({"comp_id": "X"}) is None, "")
check(
    "D5 quoted-empty extract treated as missing",
    ra.resolve_keep_one_image({"image_front": '""', "image_front_neon_url": "u.jpg"}) == "u.jpg",
    "",
)


# --------------------------------------------------------------------------
# E — normalize_delete_reason
# --------------------------------------------------------------------------
check("E1 valid reason passes", ra.normalize_delete_reason("no_single_card_image") == "no_single_card_image", "")
check("E2 valid reason 2 passes", ra.normalize_delete_reason("no_verifiable_auction_number") == "no_verifiable_auction_number", "")
check("E3 unknown -> operator", ra.normalize_delete_reason("garbage_value") == "operator", "")
check("E4 empty -> operator", ra.normalize_delete_reason("") == "operator", "")
check("E5 None -> operator", ra.normalize_delete_reason(None) == "operator", "")


# --------------------------------------------------------------------------
# F — soft_delete_payload: passes the SAME validators write_decision runs, and
#     fail-closes when comp_id / source_key are missing.
# --------------------------------------------------------------------------
payload = ra.soft_delete_payload(ROW_IDENTIFIED, reason="no_single_card_image", operator="andy")
ok, reason = validate_decision_payload(payload)
check("F1 payload passes validate_decision_payload", ok, f"reason={reason!r}")
check("F2 decision == deleted_from_8504", payload.get("decision") == "deleted_from_8504", str(payload.get("decision")))
check("F3 decision is DB-supported", is_db_supported_decision(payload.get("decision")), "")
check("F4 comp_id carried through", payload.get("comp_id") == "CMP-0001", "")
check("F5 source_key carried through", payload.get("source_key") == ROW_IDENTIFIED["source_key"], "")
check(
    "F6 row_meta carries row_action+reason+retained_image+source_view",
    payload.get("row_meta", {}).get("row_action") == "delete"
    and payload["row_meta"].get("reason") == "no_single_card_image"
    and payload["row_meta"].get("retained_image") == ROW_IDENTIFIED["image_front"]
    and payload["row_meta"].get("source_view") == "identified",
    str(payload.get("row_meta")),
)
check("F7 reviewer defaults to operator arg", payload.get("reviewer") == "andy", "")
check(
    "F8 unknown reason collapses to operator in payload",
    ra.soft_delete_payload(ROW_IDENTIFIED, reason="bogus")["row_meta"]["reason"] == "operator",
    "",
)
# Fail-closed: validator must REJECT a payload built from a row missing identity.
p_no_comp = ra.soft_delete_payload({"source_key": "sk-only", "source_view": "pending"})
ok_nc, reason_nc = validate_decision_payload(p_no_comp)
check("F9 missing comp_id -> validator rejects", (not ok_nc) and reason_nc == "missing_comp_id", f"reason={reason_nc!r}")
p_no_sk = ra.soft_delete_payload({"comp_id": "CMP-only", "source_view": "pending"})
ok_ns, reason_ns = validate_decision_payload(p_no_sk)
check("F10 missing source_key -> validator rejects", (not ok_ns) and reason_ns == "missing_source_key", f"reason={reason_ns!r}")
check(
    "F11 observed_in falls back to 'pending' when source_view unknown",
    ra.soft_delete_payload({"comp_id": "c", "source_key": "s"})["observed_in"] == "pending",
    "",
)


# --------------------------------------------------------------------------
# G — build_delete_record: Phase-3a shape + threading + overrides.
# --------------------------------------------------------------------------
rec = ra.build_delete_record(ROW_IDENTIFIED, reason="operator", operator="andy", event_id=12345)
check("G1 archived_to is None (Phase 3a)", rec.get("archived_to") is None, "")
check("G2 archived_files is [] (Phase 3a)", rec.get("archived_files") == [], str(rec.get("archived_files")))
check("G3 already_on_6tb is None (Phase 3a)", rec.get("already_on_6tb") is None, "")
check("G4 event_id threaded", rec.get("event_id") == 12345, "")
check("G5 retained_image resolved from row by default", rec.get("retained_image") == ROW_IDENTIFIED["image_front"], "")
check("G6 source_key + comp_id carried", rec.get("source_key") == ROW_IDENTIFIED["source_key"] and rec.get("comp_id") == "CMP-0001", "")
check("G7 observed_in resolved", rec.get("observed_in") == "identified", "")
check("G8 deleted_at present (ISO-ish)", isinstance(rec.get("deleted_at"), str) and rec["deleted_at"].endswith("Z"), str(rec.get("deleted_at")))
check("G9 operator default applied on empty", ra.build_delete_record(ROW_IDENTIFIED, operator="")["operator"] == ra.DEFAULT_OPERATOR, "")
# Explicit retained_image override (incl. explicit None, distinct from _UNSET).
rec_override = ra.build_delete_record(ROW_IDENTIFIED, retained_image="custom_keep.jpg", event_id=1)
check("G10 retained_image override honored", rec_override.get("retained_image") == "custom_keep.jpg", "")
rec_none = ra.build_delete_record(ROW_IDENTIFIED, retained_image=None, event_id=1)
check("G11 explicit retained_image=None honored (not re-resolved)", rec_none.get("retained_image") is None, "")
rec_at = ra.build_delete_record(ROW_IDENTIFIED, deleted_at="2026-06-30T00:00:00Z", event_id=1)
check("G12 deleted_at override honored", rec_at.get("deleted_at") == "2026-06-30T00:00:00Z", "")
check("G13 unknown reason normalized in record", ra.build_delete_record(ROW_IDENTIFIED, reason="bogus")["reason"] == "operator", "")


# --------------------------------------------------------------------------
# H — append_delete_record: JSONL roundtrip + mkdir -p + append (not overwrite).
# --------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    # Nested path whose parent does NOT exist yet -> writer must mkdir -p.
    ledger = os.path.join(td, "reports", "deleted_from_8504", "deleted_rows.jsonl")
    rec1 = ra.build_delete_record(ROW_IDENTIFIED, event_id=1)
    returned = ra.append_delete_record(rec1, path=ledger)
    check("H1 append returns the ledger path", returned == ledger, returned)
    check("H2 parent dir was created (mkdir -p)", os.path.isdir(os.path.dirname(ledger)), "")
    rec2 = ra.build_delete_record(ROW_IDENTIFIED, event_id=2)
    ra.append_delete_record(rec2, path=ledger)
    with open(ledger, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    check("H3 two appends -> two JSONL lines (append, not overwrite)", len(lines) == 2, f"lines={len(lines)}")
    parsed0 = json.loads(lines[0])
    check("H4 line 0 roundtrips to event_id 1", parsed0.get("event_id") == 1, str(parsed0.get("event_id")))
    check("H5 line 1 roundtrips to event_id 2", json.loads(lines[1]).get("event_id") == 2, "")
    check(
        "H6 ledger keeps the FULL /Users/ path (audit, pre-scrub)",
        parsed0.get("retained_image") == ROW_IDENTIFIED["image_front"],
        "the HTTP layer scrubs to basename; the on-disk audit does not",
    )


# --------------------------------------------------------------------------
# I — decision-wiring invariants the DELETE route depends on.
# --------------------------------------------------------------------------
check("I1 deleted_from_8504 in DB_VALID_DECISIONS", "deleted_from_8504" in DB_VALID_DECISIONS, "")
check("I2 deleted_from_8504 in config.VALID_DECISIONS", "deleted_from_8504" in config.VALID_DECISIONS, "")
check("I3 deleted_from_8504 in _ROW_ACTION_ONLY_DECISIONS", "deleted_from_8504" in _ROW_ACTION_ONLY_DECISIONS, "")
check(
    "I4 DB_VALID_DECISIONS subset of config.VALID_DECISIONS",
    set(DB_VALID_DECISIONS).issubset(set(config.VALID_DECISIONS)),
    f"extra={sorted(set(DB_VALID_DECISIONS) - set(config.VALID_DECISIONS))}",
)
# Never offered in the generic per-row menu (row-action-only) regardless of row.
for label, sample in [
    ("identified", ROW_IDENTIFIED),
    ("pending", {"comp_id": "c", "source_key": "s", "source_view": "pending"}),
    ("bare", {}),
]:
    allowed = row_allowed_decisions(sample)
    check(
        f"I5.{label} deleted_from_8504 NOT in row_allowed_decisions",
        "deleted_from_8504" not in allowed,
        f"allowed={allowed}",
    )
check("I6 front_swapped is an unmapped semantic decision (friendly 422)", "front_swapped" in UNMAPPED_SEMANTIC_DECISIONS, "")
check("I7 front_swapped NOT yet DB-supported", not is_db_supported_decision("front_swapped"), "")
check(
    "I8 row_actions.DELETE_DECISION matches the wired string",
    ra.DELETE_DECISION == "deleted_from_8504",
    ra.DELETE_DECISION,
)


# --------------------------------------------------------------------------
# J — queue-exclusion coverage (the soft-hide must vanish from every tab/count).
# --------------------------------------------------------------------------
USER_FACING_TABS = [
    "WORKING_QUEUE_SQL",
    "HIGH_VALUE_SQL",
    "PROOF_REVIEW_SQL",
    "NEEDS_IDENTITY_SQL",
    "NEEDS_BETTER_IMAGE_SQL",
    "CAPTURE_REVIEW_SQL",
    "INTERSTITIAL_CARRY_FORWARD_SQL",
    "MAZIFIED_SQL",
    "FLAGGED_REVIEW_SQL",
    "TRUSTED_VIEW_SQL",
    "IDENTIFIED_VIEW_SQL",
    "QUEUE_COUNTS_SQL",
]
for tab in USER_FACING_TABS:
    sql = getattr(queues, tab)
    check(f"J1.{tab} excludes deleted_from_8504", "deleted_from_8504" in sql, f"len={len(sql)}")

check("J2 HIDDEN_DECISIONS contains deleted_from_8504", "deleted_from_8504" in queues.HIDDEN_DECISIONS, str(queues.HIDDEN_DECISIONS))
check("J3 HIDDEN_DECISIONS contains hidden_from_work_queue", "hidden_from_work_queue" in queues.HIDDEN_DECISIONS, "")
check("J4 HIDDEN_DECISIONS contains rejected_from_public_path", "rejected_from_public_path" in queues.HIDDEN_DECISIONS, "")
check("J5 HIDDEN_EXCLUSION is defined", hasattr(queues, "HIDDEN_EXCLUSION") and bool(queues.HIDDEN_EXCLUSION), "")
check(
    "J6 HIDDEN_EXCLUSION uses latest-decision (DISTINCT ON ... ORDER BY ... created_at DESC)",
    "DISTINCT ON (source_key)" in queues.HIDDEN_EXCLUSION
    and "created_at DESC" in queues.HIDDEN_EXCLUSION,
    "later clear un-hides (reversible soft-hide)",
)
check(
    "J7 HIDDEN_EXCLUSION renders the literal deleted_from_8504",
    "deleted_from_8504" in queues.HIDDEN_EXCLUSION,
    "",
)
# The 3 self-excluding / resurfacing tabs are intentionally left alone.
for tab in ["CHROME_ADVANCED_SQL", "HUMAN_REVIEW_AI_APPROVED_SQL", "REJECTED_HIDDEN_SQL"]:
    sql = getattr(queues, tab, None)
    check(
        f"J8.{tab} intentionally NOT carrying the exclusion",
        sql is not None and "deleted_from_8504" not in sql,
        "latest-decision self-exclude / resurfacing path",
    )
# Row inspection endpoint stays reachable for reversal (clear) — must NOT filter.
check(
    "J9 ROW_BY_SOURCE_KEY_SQL takes 5 source_key params (UNION of 5 surfaces)",
    queues.ROW_BY_SOURCE_KEY_SQL.count("%s") == 5,
    "single-row inspection stays reachable so a deleted row can be cleared",
)


# --------------------------------------------------------------------------
# Print results, exit non-zero on any failure so CI catches it.
# --------------------------------------------------------------------------
print("=" * 90)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {name:<66}  {detail}")
print("=" * 90)
print(f"  {sum(1 for _, ok, _ in results if ok)}/{len(results)} checks passed")
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
