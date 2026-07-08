"""mazidex-admin v0 — review decision write helper.

DISABLED at v0 startup. Operator flips MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED=1
after acceptance tests pass, then the POST /api/v1/review-decision endpoint
becomes effective. Until that flag is on, the endpoint returns 503 with
a clear "dry-run; write gate closed" message.

----------------------------------------------------------------------------
SAFETY-RAIL ADDITIONS (Tier B, drafted 2026-05-29):
  - DB_VALID_DECISIONS:        the strict server-side allowlist actually
                               supported by review_decision_events today
                               (7 values). Wider VALID_DECISIONS lives in
                               config.py for backward compatibility with
                               existing UI strings; this stricter set is
                               what /api/v1/review-decision enforces at
                               422.
  - row_allowed_decisions(row):decide which decisions are legal for a
                               specific row state. Hides MAZIFIED on
                               already-mazified rows, blocks all writes
                               on missing-image rows, etc.
  - mazified_button_state(row):drawer-button-level helper. Returns
                               {enabled, reason} so the UI can render the
                               disabled button with a tooltip.
  - source_drift_meta(...):     compares clicked-tile identity (source_key,
                               feed_observation_id) against the resolved
                               drawer row and returns a structured
                               "drift" object or None.

All helpers are pure functions, no DB access, safe to import + unit-test.
"""
from __future__ import annotations

from typing import Any, Optional

from config import VALID_DECISIONS


# --------------------------------------------------------------------------
# Strict DB-valid decision allowlist.
#
# CONTEXT (2026-05-29 orchestrator memo): although config.VALID_DECISIONS
# accepts a wider set of semantic UI strings (needs_better_image,
# visual_context_hold, ...), the only decisions that map to a backend
# path today are these 7. Anything outside this set must return HTTP 422
# from /api/v1/review-decision *before* any DB call, so we do not leak
# psycopg.errors.CheckViolation as 500s.
#
# This list MUST stay a subset of config.VALID_DECISIONS — never add a
# value here that the outer payload validator rejects.
# --------------------------------------------------------------------------
DB_VALID_DECISIONS = frozenset({
    "confirm",
    "flag",
    "reject",
    "workable",
    "clear",
    "mazified",
    "deny",
    # 8504 row-action decision (Phase 3a). DB-supported so the dedicated
    # DELETE soft-hide route can write it through the single write_decision
    # INSERT path. It can NEVER leak through the generic /api/v1/review-decision
    # endpoint: that route's write stage accepts only decision=='confirm'
    # (every other decision -> 403 write_scope_closed). Reversible via 'clear'.
    "deleted_from_8504",
})


# DB-supported decisions that are written ONLY by a dedicated row-action route
# (never the generic per-row decision menu). row_allowed_decisions() subtracts
# these so the drawer's generic decision buttons never offer them -- DELETE is
# its own button with its own keep-one-image / JSONL side effects.
_ROW_ACTION_ONLY_DECISIONS = frozenset({
    "deleted_from_8504",
})


# Semantic UI decisions that today have NO DB-side handler. Listed here so
# we can give a friendly 422 with `unmapped_semantic_decision` instead of
# letting them through.
UNMAPPED_SEMANTIC_DECISIONS = frozenset({
    "needs_better_image",
    "needs_identity_enrichment",
    "visual_context_hold",
    "proof_binding_unsubstantiated",
    "chrome_advanced_to_human_review",
    "human_confirmed_for_final_gate",
    "rejected_from_public_path",
    "hidden_from_work_queue",
    # 8504 SWAP FRONT decision (Phase 4): written ONLY by its dedicated route,
    # never the generic endpoint. (deleted_from_8504 graduated to
    # DB_VALID_DECISIONS in Phase 3a now that the DELETE route exists.)
    "front_swapped",
})


def validate_decision_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason). Pure-function; no DB access.

    Validates payload shape only. Whether the decision string is allowed
    against the strict DB set is enforced separately by the route handler
    using `is_db_supported_decision()`.
    """
    if not isinstance(payload, dict):
        return False, "payload_not_dict"
    comp_id = payload.get("comp_id")
    if not comp_id or not isinstance(comp_id, str):
        return False, "missing_comp_id"
    source_key = payload.get("source_key")
    if not source_key or not isinstance(source_key, str):
        return False, "missing_source_key"
    decision = payload.get("decision") or payload.get("decision_type")
    if decision not in VALID_DECISIONS:
        return False, f"invalid_decision: {decision}"
    return True, "ok"


def is_db_supported_decision(decision: str) -> bool:
    """Stricter check: True iff the decision has a DB-side handler today."""
    return isinstance(decision, str) and decision in DB_VALID_DECISIONS


def unmapped_decision_reason(decision: str) -> str:
    """Friendly 422 reason. Distinguishes never heard of it from
    we know it but it has no backend wiring yet."""
    if not isinstance(decision, str) or decision == "":
        return "missing_decision"
    if decision in DB_VALID_DECISIONS:
        return "ok"
    if decision in UNMAPPED_SEMANTIC_DECISIONS:
        return f"unmapped_semantic_decision: {decision}"
    if decision in VALID_DECISIONS:
        return f"recognized_but_not_db_supported: {decision}"
    return f"unknown_decision: {decision}"


# --------------------------------------------------------------------------
# Row-state-aware allowed decisions.
# --------------------------------------------------------------------------

# Coarse "hard blocker" check: row should not accept any write decisions
# while these signals are present. We keep this conservative — a False
# return must mean "this row is safe enough to ACCEPT a decision write".
_HARD_BLOCKER_CHIPS = {
    "HARD BLOCKED",
    "PROOF_BLOCKED",
    "PROOF UNVERIFIED HOLD",
    "PROOF_UNVERIFIED_HOLD",
}

_BANNED_SELLERS = {"kksportscards", "collectiblescloset", "tripp_cards"}

# Generic capture placeholders the Whatnot ingest writes into identity columns
# (card_name='Whatnot Live Capture' lives on 53.6k pending rows, plus title).
# These are NOT real identity -- must be rejected in EVERY identity column.
_GENERIC_IDENTITY_VALUES = frozenset({
    "whatnot live capture", "whatnot", "live capture", "unidentified", "unknown",
})

_AUDIO_ADVISORY_TOKENS = (
    "audio-image-conflict",
    "audio_image_conflict",
    "low-verification-confidence",
    "low_verification_confidence",
    "audio-non-single",
    "audio_non_single",
    "audio-card-count",
    "audio_card_count",
    "whisper",
    "audio",
)

_NON_AUDIO_IDENTITY_EVIDENCE_TOKENS = (
    "unidentified_with_image",
    "unidentified-with-image",
    "gemini",
    "vision",
    "image_identity",
    "image-identity",
    "dom_identity",
    "dom-identity",
    "capture_identity",
    "capture-identity",
)


def _row_already_mazified(row: dict[str, Any]) -> bool:
    """True iff the row has already been mazified."""
    dec = str(row.get("review_decision") or "").strip().lower()
    if dec == "mazified":
        return True
    cert = str(row.get("mazi_cert_status") or "").strip().lower()
    if cert == "verified" and row.get("mazi_cert_id"):
        return True
    chips = row.get("chips") or []
    if isinstance(chips, list):
        upper = {str(c).upper() for c in chips}
        if "MAZIFIED REVIEW" in upper or "MAZIFIED" in upper:
            return True
    return False


def _row_has_hard_blocker(row: dict[str, Any]) -> bool:
    """True iff the row carries a chip-level hard blocker."""
    chips = row.get("chips") or []
    if not isinstance(chips, list):
        return False
    return any(str(c).upper() in _HARD_BLOCKER_CHIPS for c in chips)


def _pending_set(row: dict[str, Any]) -> set[str]:
    pending = row.get("pending_reasons") or []
    if not isinstance(pending, list):
        return set()
    return {str(p).strip().upper() for p in pending if str(p).strip()}


def _chip_set(row: dict[str, Any]) -> set[str]:
    chips = row.get("chips") or []
    if not isinstance(chips, list):
        return set()
    return {str(c).strip().upper() for c in chips if str(c).strip()}


def _row_text_blob(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "pending_reasons",
        "warnings",
        "risk_flags",
        "review_notes",
        "identity_enrichment_disagreement",
    ):
        value = row.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value)
        elif value is not None:
            parts.append(str(value))
    raw = row.get("raw") or row.get("raw_full") or {}
    if isinstance(raw, dict):
        for key in (
            "_review_reason",
            "review_reason",
            "audio_flags",
            "whisper_flags",
            "identity_enrichment_disagreement",
        ):
            value = raw.get(key)
            if isinstance(value, list):
                parts.extend(str(x) for x in value)
            elif value is not None:
                parts.append(str(value))
    return " ".join(parts).lower()


def _row_has_gemini_or_image_identity(row: dict[str, Any]) -> bool:
    # A generic capture placeholder (e.g. card_name='Whatnot Live Capture') is
    # NOT real identity. The ingest writes it into card_name AND title, so reject
    # it in every identity column -- not just title.
    for key in ("card_name", "player", "brand", "set_name", "title"):
        v = str(row.get(key) or "").strip().lower()
        if v and v not in _GENERIC_IDENTITY_VALUES:
            return True
    return False


def _row_has_audio_advisory_signal(row: dict[str, Any]) -> bool:
    text = _row_text_blob(row)
    return any(token in text for token in _AUDIO_ADVISORY_TOKENS)


def _low_confidence_is_audio_only(row: dict[str, Any]) -> bool:
    text = _row_text_blob(row)
    if not any(token in text for token in _AUDIO_ADVISORY_TOKENS):
        return False
    return not any(token in text for token in _NON_AUDIO_IDENTITY_EVIDENCE_TOKENS)


def _row_image_missing_or_broken(row: dict[str, Any]) -> bool:
    """True iff the row has no usable front image basename."""
    bn = str(row.get("image_front_basename") or "").strip()
    if not bn:
        return True
    # Tiny / SVG / placeholder-shaped basenames mean the image is unusable
    # for a Mazify decision. Same conservative cutoff used by the Chrome
    # mazify selector.
    bn_lower = bn.lower()
    if bn_lower.endswith(".svg"):
        return True
    if "placeholder" in bn_lower:
        return True
    return False


def _row_front_trust_candidate(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    if raw.get("front_trust_candidate") is True:
        return True
    cert = str(row.get("mazi_cert_status") or raw.get("mazi_cert_status") or "").strip().lower()
    proof = str(row.get("proof_binding_status") or raw.get("proof_binding_status") or "").strip().lower()
    return cert == "front_trust_candidate" and proof == "front_overlay_verified"


def _row_proof_missing(row: dict[str, Any]) -> bool:
    if _row_front_trust_candidate(row):
        return False
    proof_bn = str(row.get("image_proof_basename") or "").strip()
    proof_status = str(row.get("proof_image_status") or "").strip().lower()
    proof_binding = str(row.get("proof_binding_status") or "").strip().lower()
    if proof_bn:
        return False
    return proof_status in {"", "missing", "unknown", "unknown_unreadable"} or proof_binding == ""


def _row_cert_or_evidence_pending(row: dict[str, Any]) -> bool:
    if _row_front_trust_candidate(row):
        return False
    cert = str(row.get("mazi_cert_status") or "").strip().lower()
    if cert not in {"verified", "human_approved"}:
        return True
    proof = str(row.get("proof_binding_status") or "").strip().lower()
    return proof in {"", "pending_review", "unknown", "unknown_unreadable"}


def _row_public_or_private_image_unsafe(row: dict[str, Any]) -> bool:
    if _row_front_trust_candidate(row):
        return False
    text = " ".join(str(x).lower() for x in (
        row.get("public_image_safe_reason"),
        row.get("public_image_safety"),
        row.get("image_safety"),
    ) if x is not None)
    if any(token in text for token in ("unsafe", "private", "internal_only", "not_public")):
        return True
    return "PUBLIC IMAGE SAFE FALSE" in _chip_set(row)


def _row_binding_review(row: dict[str, Any]) -> bool:
    return "BINDING_REVIEW" in _pending_set(row) or "BINDING_REVIEW" in _chip_set(row)


def _row_capture_hard_blocker(row: dict[str, Any]) -> bool:
    pending = _pending_set(row)
    hard_pending = {
        "FILENAME_COLLISION_RISK",
        "NO_IMAGE",
        "FRONT_IMAGE_MISSING",
        "CDN_ONLY",
        "INTERSTITIAL_CARRY_FORWARD",
    }
    if pending & hard_pending:
        return True
    for key in ("front_capture_class", "proof_overlay_risk", "non_card_object_detected"):
        value = str(row.get(key) or "").strip().lower()
        if value in {"non_card", "phone_screen", "logo", "popup", "high", "true", "detected"}:
            return True
    return False


def _row_duplicate_or_collision(row: dict[str, Any]) -> bool:
    pending = _pending_set(row)
    if pending & {"FILENAME_COLLISION_RISK", "DUPLICATE", "DUPLICATE_SOURCE"}:
        return True
    duplicate = str(row.get("duplicate_status") or row.get("duplicate_reason") or "").strip().lower()
    return duplicate not in {"", "none", "unique", "false"}


def _row_banned_seller(row: dict[str, Any]) -> bool:
    seller = str(row.get("seller") or "").strip().lower()
    return seller in _BANNED_SELLERS


def _row_identity_ambiguous(row: dict[str, Any]) -> bool:
    """True iff identity enrichment disagrees or row is unidentified."""
    sig = str(row.get("identity_enrichment_disagreement") or "").strip().lower()
    if sig in ("disagree", "true", "yes", "conflict"):
        # Whisper/audio-only disagreement is advisory when Gemini/image
        # identity is present. Image/DOM/capture disagreements still block.
        if _low_confidence_is_audio_only(row) and _row_has_gemini_or_image_identity(row):
            return False
        return True
    pending = row.get("pending_reasons") or []
    if isinstance(pending, list):
        pset = {str(p) for p in pending}
        if "UNIDENTIFIED_WITH_IMAGE" in pset:
            return True
        if "LOW_CONFIDENCE" in pset and not _low_confidence_is_audio_only(row):
            return True
    return False


def _row_proof_different_sale(row: dict[str, Any]) -> bool:
    """True iff the proof image is bound to a different auction/sale."""
    pbs = str(row.get("proof_binding_status") or "").strip().lower()
    if pbs in ("mismatch_neighboring_auction", "multiple_auction_numbers"):
        return True
    pmf = str(row.get("proof_match_to_front") or "").strip().lower()
    if pmf in ("mismatch", "different_auction", "neighboring_auction"):
        return True
    return False


def _row_is_watermarked(row: dict[str, Any]) -> bool:
    """Advisory drawer check: does the front carry an `_mazi` watermark?

    Mirrors promotion._already_watermarked on an API-enriched row shape (which
    additionally carries image_front_basename). The authoritative Pending->Trusted
    guard is promotion._pending_trusted_gate_reasons on the raw DB row; this is
    the drawer-rendering / 422-advisory twin.
    """
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    evidence = raw.get("evidence_stamp") if isinstance(raw.get("evidence_stamp"), dict) else {}
    capture = raw.get("capture_quality") if isinstance(raw.get("capture_quality"), dict) else {}
    for value in (
        row.get("image_front"),
        row.get("image_front_neon_url"),
        row.get("image_front_basename"),
        raw.get("front_image_stamped"),
        evidence.get("front_stamped_image"),
    ):
        if "_mazi." in str(value or "").lower():
            return True
    return str(capture.get("front_stamped", "")).strip().lower() == "true"


def _row_single_card_ok(row: dict[str, Any]) -> bool:
    """Advisory: one foremost card. Blocks on an explicit multi-card label or
    api_scan.card_count >= 2; unknown count -> True (defer to human eyeballs).
    Mirrors promotion._pending_single_card_ok."""
    pending = row.get("pending_reasons") or []
    if isinstance(pending, list):
        for p in pending:
            s = str(p).lower()
            if "multi-card" in s or "multi_card" in s:
                return False
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    api = raw.get("api_scan") if isinstance(raw.get("api_scan"), dict) else {}
    cc = api.get("card_count")
    if cc is None or str(cc).strip() == "":
        return True
    try:
        return int(str(cc).strip()) <= 1
    except (TypeError, ValueError):
        return True


_BLOCKER_LABELS = {
    "already_mazified": "ALREADY MAZIFIED",
    "proof_missing": "PROOF MISSING",
    "cert_or_evidence_pending": "CERT PENDING",
    "public_or_private_image_unsafe": "IMAGE UNKNOWN",
    "image_missing_or_broken": "IMAGE UNKNOWN",
    "identity_ambiguous": "IDENTITY REPAIR",
    "binding_review": "BINDING REVIEW",
    "proof_different_sale": "PROOF DIFFERENT SALE",
    "hard_blocker_present": "HARD BLOCKER",
    "capture_hard_blocker": "CAPTURE HARD BLOCK",
    "duplicate_or_collision": "DUPLICATE/COLLISION",
    "banned_seller": "BANNED SELLER",
    "not_in_allowed_decisions": "NOT ALLOWED",
}


def mazified_blockers(row: dict[str, Any]) -> list[str]:
    """Single shared truth predicate for MAZIFIED eligibility."""
    if not isinstance(row, dict):
        return ["row_missing"]
    checks = (
        ("already_mazified", _row_already_mazified),
        ("proof_missing", _row_proof_missing),
        ("cert_or_evidence_pending", _row_cert_or_evidence_pending),
        ("public_or_private_image_unsafe", _row_public_or_private_image_unsafe),
        ("image_missing_or_broken", _row_image_missing_or_broken),
        ("identity_ambiguous", _row_identity_ambiguous),
        ("binding_review", _row_binding_review),
        ("proof_different_sale", _row_proof_different_sale),
        ("hard_blocker_present", _row_has_hard_blocker),
        ("capture_hard_blocker", _row_capture_hard_blocker),
        ("duplicate_or_collision", _row_duplicate_or_collision),
        ("banned_seller", _row_banned_seller),
    )
    return [name for name, predicate in checks if predicate(row)]


def row_hard_block_labels(row: dict[str, Any]) -> list[str]:
    labels = [_BLOCKER_LABELS.get(reason, reason.replace("_", " ").upper()) for reason in mazified_blockers(row)]
    reason_text = _row_text_blob(row)
    if "sale_frame_rescue" in reason_text or "rescue_valid" in reason_text or "rescue valid" in reason_text:
        labels.append("RESCUE VALID")
    out: list[str] = []
    for label in labels:
        if label not in out:
            out.append(label)
    return out


def row_allowed_decisions(row: dict[str, Any]) -> list[str]:
    """Return the sorted list of DB-supported decisions legal for this row.

    Strict subset of DB_VALID_DECISIONS. Used by the API to drive both
    the drawer button rendering AND the server-side 422 short-circuit on
    illegal-for-this-row writes.
    """
    if not isinstance(row, dict):
        return []
    # Row-action-only decisions (e.g. deleted_from_8504) are written by their
    # own dedicated routes, never offered in the generic per-row decision menu.
    allowed = set(DB_VALID_DECISIONS) - _ROW_ACTION_ONLY_DECISIONS
    blockers = set(mazified_blockers(row))
    observed_in = str(row.get("observed_in") or row.get("source_view") or "").strip().lower()
    stage1_identified_confirm = (
        observed_in == "identified"
        and not _row_image_missing_or_broken(row)
        and "proof_different_sale" not in blockers
        and "capture_hard_blocker" not in blockers
        and "duplicate_or_collision" not in blockers
        and "banned_seller" not in blockers
        and "hard_blocker_present" not in blockers
    )
    # Pending->Trusted (Q1 trust model): watermark + single foremost card +
    # Gemini/image identity + (downstream) human visual click. Stricter than the
    # Identified path on identity_ambiguous because a pending row has no prior
    # Identified confirmation behind it. Authoritative guard lives in
    # promotion._pending_trusted_gate_reasons; this only drives drawer rendering.
    pending_watermark_confirm = (
        observed_in == "pending"
        and not _row_image_missing_or_broken(row)
        and _row_is_watermarked(row)
        and _row_single_card_ok(row)
        and _row_has_gemini_or_image_identity(row)
        and not (blockers & {
            "capture_hard_blocker",
            "duplicate_or_collision",
            "banned_seller",
            "proof_different_sale",
            "hard_blocker_present",
            "identity_ambiguous",
        })
    )
    if blockers:
        allowed.discard("mazified")
    if blockers & {"image_missing_or_broken", "proof_missing", "proof_different_sale", "capture_hard_blocker", "duplicate_or_collision", "banned_seller"}:
        # No image = no positive promotion path. Keep negative-path
        # decisions available (reject/deny/flag/clear) so operators can
        # still triage these.
        allowed.discard("confirm")
        allowed.discard("workable")
    if blockers & {"identity_ambiguous", "cert_or_evidence_pending", "public_or_private_image_unsafe", "binding_review"}:
        allowed.discard("confirm")
    if "hard_blocker_present" in blockers:
        # Hard-blocker rows admit only negative-path decisions.
        allowed &= {"reject", "deny", "flag", "clear"}
    if stage1_identified_confirm:
        allowed.add("confirm")
    if pending_watermark_confirm:
        allowed.add("confirm")
    cert = str(row.get("mazi_cert_status") or "").strip().lower()
    if cert not in {"verified", "human_approved"}:
        # Front-trust candidates can be privately Confirmed, but they are not
        # MAZIFIED/public-cert-ready until the explicit cert gate says so.
        allowed.discard("mazified")
    return sorted(allowed)


def mazified_button_state(row: dict[str, Any]) -> dict[str, Any]:
    """Drawer-side {enabled, reason} helper for the MAZIFIED button.

    Reason is a short snake_case token the UI can map to a human label;
    the UI may also display it verbatim as a tooltip.
    """
    if not isinstance(row, dict):
        return {"enabled": False, "reason": "row_missing"}
    blockers = mazified_blockers(row)
    if blockers:
        return {"enabled": False, "reason": blockers[0], "blockers": blockers}
    if "mazified" not in row_allowed_decisions(row):
        return {"enabled": False, "reason": "not_in_allowed_decisions", "blockers": ["not_in_allowed_decisions"]}
    return {"enabled": True, "reason": None, "blockers": []}


# --------------------------------------------------------------------------
# Source drift detection.
#
# When the operator clicks a tile, the SPA passes the tile's identity
# (comp_id + source_key + feed_observation_id) as drawer-fetch query
# parameters. The drawer endpoint resolves a row by best-effort and
# returns *what it actually resolved*. This helper compares the two and
# emits a structured `source_drift` object iff they differ.
#
# `clicked` and `resolved` are arbitrary dicts; we read the four identity
# fields only. Either may be None / empty.
# --------------------------------------------------------------------------

_IDENTITY_FIELDS = (
    "source_key",
    "feed_observation_id",
    "review_id",
    "comp_id",
    "seller",
    "auction_number",
    "image_front_basename",
    "image_proof_basename",
)


def _norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def resolve_preferred_row(
    candidates: list[dict[str, Any]],
    source_key: Optional[str],
    feed_observation_id: Optional[Any] = None,
) -> tuple[Optional[dict[str, Any]], bool]:
    """Pick the candidate row whose source_key exactly matches the request.

    Returns (chosen, exact_match):
      * non-empty source_key with an exact candidate match -> (that, True)
      * otherwise -> (first candidate, False)  [historical fallback]
      * empty candidate list -> (None, False)

    Single shared implementation used by the /api/v1/row route so the
    resolution behavior is unit-testable instead of inlined. Behavior is
    identical to the previous inline loop in app.py.
    """
    if not candidates:
        return None, False
    if source_key:
        for cand in candidates:
            if str(cand.get("source_key") or "") == str(source_key):
                return cand, True
    if feed_observation_id not in (None, ""):
        for cand in candidates:
            if str(cand.get("feed_observation_id") or "") == str(feed_observation_id):
                return cand, True
    return candidates[0], False


def source_drift_meta(
    clicked: Optional[dict[str, Any]],
    resolved: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Return a `{drift: True, ...}` dict if identity differs, else None.

    Drift is declared if ANY of (source_key, feed_observation_id,
    review_id) was provided by the click AND differs from the resolved
    row. comp_id alone disagreeing is fatal (would never happen — that
    is the lookup key) so we treat any comp_id mismatch as drift too.
    """
    if not clicked or not resolved:
        return None
    diffs: list[dict[str, Any]] = []
    for f in _IDENTITY_FIELDS:
        c = _norm(clicked.get(f))
        r = _norm(resolved.get(f))
        if c and r and c != r:
            diffs.append({"field": f, "clicked": c, "resolved": r})
        elif c and not r:
            diffs.append({"field": f, "clicked": c, "resolved": None,
                          "note": "resolved_row_missing_field"})
    if not diffs:
        return None
    return {
        "drift": True,
        "message": "SOURCE ROW DRIFT: drawer row differs from clicked tile.",
        "diffs": diffs,
        "policy": "all_action_buttons_disabled_until_drift_resolved",
    }


# --------------------------------------------------------------------------
# DB write (kept identical in semantics; only validation tightened).
# --------------------------------------------------------------------------

def write_decision(
    conn_obj,
    payload: dict[str, Any],
    *,
    write_enabled: bool,
) -> dict[str, Any]:
    """Insert one event into review_decision_events. Append-only.

    Gated by the caller-supplied write_enabled value. The route passes
    the same runtime value reported by /api/v1/health so health and POST
    behavior cannot drift.

    Also enforces DB_VALID_DECISIONS (the strict 7-value set) to keep
    the CHECK constraint from raising at the DB level.
    """
    if not write_enabled:
        raise PermissionError("review_write_disabled")

    ok, reason = validate_decision_payload(payload)
    if not ok:
        raise ValueError(reason)

    decision = payload.get("decision") or payload.get("decision_type")
    if not is_db_supported_decision(decision):
        # Map this to a 422-flavored ValueError so the route handler
        # can distinguish from the generic 400.
        raise ValueError(unmapped_decision_reason(decision))

    sql = """
        INSERT INTO review_decision_events
            (source_key, observed_in, review_id, comp_id, source_file,
             auction_number, decision, notes, reviewer, row_meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
    """
    from psycopg.types.json import Jsonb

    cur = conn_obj.cursor()
    cur.execute(
        sql,
        (
            payload.get("source_key"),
            payload.get("observed_in") or "pending",
            payload.get("review_id"),
            payload.get("comp_id"),
            payload.get("source_file"),
            payload.get("auction_number"),
            decision,
            payload.get("notes"),
            payload.get("reviewer") or "mazidex-admin-v0",
            Jsonb(payload.get("row_meta") or {}),
        ),
    )
    rid, ts = cur.fetchone()
    conn_obj.commit()
    return {"event_id": int(rid), "created_at": str(ts), "decision": decision}
