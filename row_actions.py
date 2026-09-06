"""mazidex-admin row actions — Phase 3a DELETE soft-hide helpers.

Pure, unit-testable building blocks for the 8504 DELETE row action. NOTHING
here mutates the database or the image store: the DB write goes exclusively
through ``decisions.write_decision`` (the single append-only INSERT path), and
the only filesystem write is the append-only JSONL audit ledger (an audit
artifact, not evidence).

Contract (plan §3.3, Phase 3a):

  * ``resolve_keep_one_image(row)`` — keep exactly ONE image with a soft-hidden
    row: the bound/displayed front 8504 last showed. The ``score_front_candidate``
    "best frame" ranking lives in ``whatnot-sniper-m4/frame_selector.py`` and is
    NOT importable in this process, so scored refinement is deferred to the
    Phase 3b archiver; here we keep the last front 8504 actually displayed.
  * ``pick_deletable_row(rows)`` — choose the identified/pending surface a
    DELETE may act on (trusted/feed/mazified are out of scope).
  * ``soft_delete_payload(row, ...)`` — the ``review_decision_events`` payload
    the dedicated DELETE route hands to ``decisions.write_decision``. ``decision``
    is ``deleted_from_8504`` — DB-supported (CHECK after migration 019) yet
    row-action-only (never reachable through the confirm-only generic endpoint).
  * ``build_delete_record(...)`` / ``append_delete_record(...)`` — the JSONL
    audit line (Phase 3a shape: ``archived_to=null``, ``archived_files=[]``,
    ``already_on_6tb=null`` — the physical 6TB archive is Phase 3b).

Reversibility: the ``deleted_from_8504`` event is un-hidden by a later ``clear``
event (latest-decision exclusion semantics in queues.py), and the JSONL line is
auditable. No bytes are deleted in Phase 3a.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional


# The soft-hide decision string. Must be in BOTH config.VALID_DECISIONS (payload
# shape) AND decisions.DB_VALID_DECISIONS (DB CHECK) — and is row-action-only
# (decisions._ROW_ACTION_ONLY_DECISIONS), so it is never offered in the generic
# per-row decision menu nor writable through the confirm-only generic endpoint.
DELETE_DECISION = "deleted_from_8504"

# The 8504 surfaces a DELETE may originate from. Trusted/feed/mazified rows are
# out of scope (operator: DELETE shown on identified + pending only).
DELETABLE_SOURCE_VIEWS = ("identified", "pending")

# Allowed structured delete reasons (plan §3.3d). Anything else collapses to the
# free-form "operator" default.
VALID_DELETE_REASONS = frozenset({
    "no_single_card_image",
    "no_verifiable_auction_number",
    "operator",
})
DEFAULT_DELETE_REASON = "operator"
DEFAULT_OPERATOR = "andy"

# Append-only JSONL audit ledger. The reports/ dir does not exist yet; the
# writer mkdir -p's the parent on first write (plan §3.3d).
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DELETE_LEDGER_PATH = os.path.join(
    _REPO_ROOT, "reports", "deleted_from_8504", "deleted_rows.jsonl"
)

# Ordered keep-one priority (Phase 3a). ``score_front_candidate`` (the scored
# "best frame" picker in whatnot-sniper-m4) is the Phase 3b refinement; in 3a we
# keep the front 8504 actually displayed:
#   image_front          — bound LOCAL front path (canonical retained image;
#                          matches the plan's JSONL retained_image example)
#   image_front_neon_url — R2 HTTPS front (when no local path)
#   ops_display_image    — operationally-displayed front (deepest fallback,
#                          used when the bound front is proof-only/blocked)
_KEEP_ONE_IMAGE_KEYS = (
    "image_front",
    "image_front_neon_url",
    "ops_display_image",
)

# Sentinel so build_delete_record can distinguish "resolve from row" from an
# explicit retained_image=None.
_UNSET: Any = object()


def _clean_str(value: Any) -> str:
    """Trim to a real string; treat None and quoted-empty JSON extracts as ''."""
    if value is None:
        return ""
    s = str(value).strip()
    if s in ('""', "''"):
        return ""
    return s


def resolve_source_view(row: dict[str, Any]) -> str:
    """Which 8504 surface this row sits on (lowercased), or '' when unknown.

    Prefers the explicit ``source_view`` literal (set by ROW_BY_SOURCE_KEY_SQL's
    UNION branches) over the row's own ``observed_in`` column.
    """
    if not isinstance(row, dict):
        return ""
    for key in ("source_view", "observed_in"):
        val = _clean_str(row.get(key)).lower()
        if val:
            return val
    return ""


def is_deletable_row(row: dict[str, Any]) -> bool:
    """True only for identified/pending rows (DELETE scope per spec)."""
    return resolve_source_view(row) in DELETABLE_SOURCE_VIEWS


def pick_deletable_row(rows: Any) -> Optional[dict[str, Any]]:
    """From ROW_BY_SOURCE_KEY_SQL results, pick the surface a DELETE may act on.

    Preference order is identified, then pending (deterministic if a key somehow
    appears on both). Returns None when no identified/pending surface exists —
    i.e. a trusted/feed/mazified-only row is NOT deletable through this action.
    """
    if not rows:
        return None
    by_view: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        view = resolve_source_view(row)
        if view in DELETABLE_SOURCE_VIEWS and view not in by_view:
            by_view[view] = row
    for view in DELETABLE_SOURCE_VIEWS:
        if view in by_view:
            return by_view[view]
    return None


def resolve_keep_one_image(row: dict[str, Any]) -> Optional[str]:
    """Return the single image to keep with a soft-hidden row, or None.

    Priority (Phase 3a): the bound/displayed front 8504 last showed —
    ``image_front`` → ``image_front_neon_url`` → ``ops_display_image``. Returns
    None when the row carries no resolvable front image (the caller may still
    soft-hide; there is simply nothing to retain/archive).

    Scored "best frame" selection (``frame_selector.score_front_candidate``) is
    a Phase 3b refinement — that module lives in whatnot-sniper-m4 and is not
    importable here.
    """
    if not isinstance(row, dict):
        return None
    for key in _KEEP_ONE_IMAGE_KEYS:
        val = _clean_str(row.get(key))
        if val:
            return val
    return None


def normalize_delete_reason(reason: Any) -> str:
    """Collapse to one of VALID_DELETE_REASONS; unknown/empty -> 'operator'."""
    r = _clean_str(reason)
    return r if r in VALID_DELETE_REASONS else DEFAULT_DELETE_REASON


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def soft_delete_payload(
    row: dict[str, Any],
    *,
    reason: Any = DEFAULT_DELETE_REASON,
    operator: str = DEFAULT_OPERATOR,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Build the ``review_decision_events`` payload for the soft-hide write.

    Handed to ``decisions.write_decision`` (the single INSERT path). Carries
    non-empty ``comp_id`` + ``source_key`` so ``validate_decision_payload``
    passes; a row missing either yields None there and the validator rejects it
    (the route surfaces a 400), which is the correct fail-closed behavior.
    """
    src_view = resolve_source_view(row)
    norm_reason = normalize_delete_reason(reason)
    op = _clean_str(operator) or DEFAULT_OPERATOR
    return {
        "source_key": _clean_str(row.get("source_key")) or None,
        "observed_in": src_view or "pending",
        "review_id": row.get("review_id"),
        "comp_id": _clean_str(row.get("comp_id")) or None,
        "source_file": _clean_str(row.get("source_file")) or None,
        "auction_number": row.get("auction_number"),
        "decision": DELETE_DECISION,
        "notes": notes or f"DELETE soft-hide from 8504 ({norm_reason})",
        "reviewer": op,
        "row_meta": {
            "row_action": "delete",
            "reason": norm_reason,
            "retained_image": resolve_keep_one_image(row),
            "source_view": src_view or None,
        },
    }


def build_delete_record(
    row: dict[str, Any],
    *,
    reason: Any = DEFAULT_DELETE_REASON,
    operator: str = DEFAULT_OPERATOR,
    retained_image: Any = _UNSET,
    deleted_at: Optional[str] = None,
    event_id: Optional[int] = None,
) -> dict[str, Any]:
    """Build the append-only JSONL audit dict for one DELETE (Phase 3a shape).

    ``archived_to`` / ``archived_files`` / ``already_on_6tb`` are the Phase 3b
    physical-archive fields and are emitted null/empty here (no bytes moved in
    3a). Pass ``retained_image`` to override the resolved keep-one image.
    """
    if retained_image is _UNSET:
        retained_image = resolve_keep_one_image(row)
    return {
        "source_key": _clean_str(row.get("source_key")) or None,
        "comp_id": _clean_str(row.get("comp_id")) or None,
        "auction_number": row.get("auction_number"),
        "observed_in": resolve_source_view(row) or None,
        "deleted_at": deleted_at or _utc_now_iso(),
        "reason": normalize_delete_reason(reason),
        "retained_image": retained_image,
        "event_id": event_id,
        # ---- Phase 3b physical-archive fields (not populated in Phase 3a) ----
        "archived_to": None,
        "archived_files": [],
        "already_on_6tb": None,
        "operator": _clean_str(operator) or DEFAULT_OPERATOR,
    }


def append_delete_record(
    record: dict[str, Any],
    *,
    path: str = DEFAULT_DELETE_LEDGER_PATH,
) -> str:
    """Append one JSON line to the deleted_rows ledger; mkdir -p the parent.

    Returns the ledger path written. This is the only filesystem write in
    Phase 3a (an append-only audit artifact, never evidence).
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


# ===========================================================================
# PRIVATE-TRUSTED (watermark + visual) row action — PART B, 2026-09-05.
#
# A reviewer promotes a RESCUED review-front row to the INTERNAL state
# `private_trusted_watermark_visual` after BOTH:
#   (i)  the watermark read-back == matches_expected against the row's own
#        auction_number (machine-checkable — enforced here), and
#   (ii) an explicit human visual confirmation (the client must post
#        visual_confirmed=true; the button cannot be scripted past this).
#
# WHAT THIS STATE IS NOT — restated in code as required by the lane command:
#   * NOT public Trusted. trusted_sales_current admits a pending row only on
#     review_decision='confirm'; this is not 'confirm'.
#   * NOT a MAZIFIED RECORD and NOT a cert. It is watermark + visual evidence.
#   * NEVER reachable by the auto-promote daemon. The daemon selects candidates
#     under --require-front-allow (front_image_status IN trusted_gate.FRONT_ALLOW);
#     these rows are 'missing_or_context_only' -> FRONT_HOLD. It reads
#     review_decision only to HOLD an already-decided row, so this decision makes
#     the daemon strictly more conservative.
#   * NEVER eligible for public MaziDex.
#
# This action does NOT call promote_identified_to_trusted and writes nothing to
# stage1_trusted_promotion_rows — the only relation stage1_trusted_sales_current
# reads. It appends ONE append-only review_decision_events row. Reversible by a
# later 'clear'.
# ===========================================================================

PRIVATE_TRUSTED_DECISION = "private_trusted_watermark_visual"

# Only rescued review-front rows are in scope. A row with a real official front
# is NOT this action's business — it has the ordinary Trusted path available.
PRIVATE_TRUSTED_SOURCE_VIEWS = ("identified",)

# The read-back verdicts that count as PROVEN. Mirrors trusted_gate.check_binding's
# `proven` arm exactly: a self-consistent "we stamped it" status is NOT enough.
PRIVATE_TRUSTED_READBACK_OK = frozenset({"matches_expected"})


def _auction_text(value: Any) -> str:
    """Normalize an auction number for comparison ('#799', '799.0' -> '799')."""
    text = _clean_str(value).lstrip("#").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _raw_of(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw")
    return raw if isinstance(raw, dict) else {}


def private_trusted_blockers(row: dict[str, Any], *, visual_confirmed: bool) -> list[str]:
    """Return the reasons this row may NOT be privately promoted. Empty == eligible.

    Pure function over a DB row. Fail-closed: every unknown or absent value is a
    blocker, never a pass.
    """
    blockers: list[str] = []
    if not isinstance(row, dict):
        return ["row_missing"]
    raw = _raw_of(row)
    api = raw.get("api_scan") if isinstance(raw.get("api_scan"), dict) else {}
    stamp = raw.get("evidence_stamp") if isinstance(raw.get("evidence_stamp"), dict) else {}

    # (ii) explicit human visual confirmation — never inferable from data.
    if not visual_confirmed:
        blockers.append("visual_confirmation_required")

    # Scope: a rescued review-front row only.
    if resolve_source_view(row) not in PRIVATE_TRUSTED_SOURCE_VIEWS:
        blockers.append("not_an_identified_row")
    if not _clean_str(row.get("image_review_front")):
        blockers.append("no_review_front")
    if _clean_str(row.get("image_front")) or _clean_str(row.get("image_front_neon_url")):
        # Has a real front -> the ordinary Trusted path applies, not this one.
        blockers.append("row_has_official_front")
    if _clean_str(raw.get("front_image_status")).lower() != "missing_or_context_only":
        blockers.append("unexpected_front_image_status")

    # (i) watermark read-back == matches_expected, bound to THIS row's auction.
    row_auction = _auction_text(row.get("auction_number"))
    if not row_auction:
        blockers.append("no_auction_number")
    verdict = _clean_str(api.get("mazi_watermark_verification")).lower()
    if verdict not in PRIVATE_TRUSTED_READBACK_OK:
        blockers.append(f"watermark_not_read_back:{verdict or 'unread'}")
    scan_auction = _auction_text(api.get("mazi_watermark_auction_number"))
    if row_auction and scan_auction and scan_auction != row_auction:
        blockers.append("watermark_auction_ne_row")
    expected = _auction_text(stamp.get("expected_auction_number"))
    if row_auction and expected and expected != row_auction:
        blockers.append("expected_auction_ne_row")

    # A banned seller hard-blocks every ->Trusted path; keep this one aligned.
    if _clean_str(row.get("seller")).lower() in _PRIVATE_TRUSTED_BANNED_SELLERS:
        blockers.append("banned_seller")
    return sorted(set(blockers))


# Mirror of trusted_gate.BANNED_SELLERS / promotion._PENDING_BANNED_SELLERS.
_PRIVATE_TRUSTED_BANNED_SELLERS = frozenset({
    "kksportscards", "collectiblescloset", "tripp_cards",
})


def private_trusted_payload(
    row: dict[str, Any],
    *,
    reviewer: str,
    notes: Any = None,
    visual_confirmed: bool,
) -> dict[str, Any]:
    """The review_decision_events payload for the private-Trusted action.

    Audit-logs reviewer identity and the exact evidence the decision rested on.
    Contains NO private values (no buyer/winner/bidder/chat/handles) — only the
    row's own auction binding and the review-frame basename.
    """
    raw = _raw_of(row)
    api = raw.get("api_scan") if isinstance(raw.get("api_scan"), dict) else {}
    reviewer = _clean_str(reviewer) or DEFAULT_OPERATOR
    return {
        "source_key": _clean_str(row.get("source_key")),
        "comp_id": _clean_str(row.get("comp_id")) or None,
        "observed_in": resolve_source_view(row) or None,
        "review_id": _clean_str(row.get("review_id")) or None,
        "source_file": _clean_str(row.get("source_file")) or None,
        "auction_number": row.get("auction_number"),
        "decision": PRIVATE_TRUSTED_DECISION,
        "reviewer": reviewer,
        "notes": _clean_str(notes) or None,
        "row_meta": {
            "private_state": PRIVATE_TRUSTED_DECISION,
            "basis": "watermark_readback_plus_human_visual",
            # Restated on every event so the audit row is self-describing.
            "public_ready": False,
            "mazified": False,
            "is_cert": False,
            "public_mazidex_eligible": False,
            "auto_promotable": False,
            "watermark_verification": _clean_str(api.get("mazi_watermark_verification")) or None,
            "watermark_auction_number": _auction_text(api.get("mazi_watermark_auction_number")) or None,
            "row_auction_number": _auction_text(row.get("auction_number")) or None,
            "front_image_status": _clean_str(raw.get("front_image_status")) or None,
            "review_front_basename": os.path.basename(
                _clean_str(row.get("image_review_front"))
            ) or None,
            "visual_confirmed": bool(visual_confirmed),
            "visual_confirmed_by": reviewer,
            "visual_confirmed_at": _utc_now_iso(),
        },
    }
