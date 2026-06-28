"""mazidex-admin v0 — privacy + path safety helpers.

Every response payload passes through these helpers before being
returned to the client. The goal is to make it structurally impossible
for buyer/winner/bidder/customer keys or raw /Users paths to leak.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from config import (
    BINDING_REVIEW_LABEL,
    CAUTION_LABEL_CONSTANT,
    CAPTURE_REVIEW_LABEL,
    FORBIDDEN_PATH_PREFIXES,
    HARD_BLOCKED_LABEL,
    HIGH_VALUE_LABEL,
    HUMAN_REVIEW_REQUIRED_LABEL,
    IDENTITY_REPAIR_LABEL,
    INTERSTITIAL_CARRY_FORWARD_LABEL,
    NOT_MAZIFIED_LABEL,
    NOT_PUBLIC_READY_LABEL,
    NOT_TRUSTED_LABEL,
    NOT_VALUATION_SAFE_LABEL,
    PRIVATE_KEY_SUBSTRINGS,
    PROOF_BLOCKED_LABEL,
    PROOF_INTERNAL_ONLY_LABEL,
    PUBLIC_IMAGE_SAFE_FALSE_LABEL,
    VISUAL_CONTEXT_HOLD_LABEL,
)


def _is_private_key(key: str) -> bool:
    k = str(key).lower()
    return any(token in k for token in PRIVATE_KEY_SUBSTRINGS)


def _sanitize_string(value: str) -> str:
    """Replace forbidden filesystem prefixes with the basename."""
    if not isinstance(value, str):
        return value
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if prefix in value:
            return os.path.basename(value)
    return value


def deep_scrub(obj: Any) -> Any:
    """Recursively remove private keys and strip forbidden paths.

    Returns a NEW object; never mutates the input. Defense in depth: even
    if some upstream layer leaks a private field via raw->>'buyer', this
    scrub removes it before serialization.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _is_private_key(k):
                continue
            out[k] = deep_scrub(v)
        return out
    if isinstance(obj, list):
        return [deep_scrub(x) for x in obj]
    if isinstance(obj, str):
        return _sanitize_string(obj)
    return obj


def extract_basename(*candidates: Any) -> str:
    """Pull the first usable safe image reference from candidates.

    Accepts any of: full path, R2 URL, http URL, plain basename, or
    whatnot_cards-relative path. Returns a safe path below whatnot_cards
    when present (e.g. "pokemon/card_...jpg"), otherwise just the filename.
    Never returns a /Users/ path or a URL.
    """
    for c in candidates:
        if not c:
            continue
        s = str(c).strip()
        if not s:
            continue
        # If it's a URL with path, take the last segment
        if "://" in s:
            path = s.split("?", 1)[0].rstrip("/")
            if "/whatnot_cards/" in path:
                tail = path.split("/whatnot_cards/", 1)[1].lstrip("/")
                if tail and ".." not in tail.split("/"):
                    return tail
            tail = path.rsplit("/", 1)[-1]
            if tail:
                return tail
            continue
        if "whatnot_cards/" in s:
            tail = s.split("whatnot_cards/", 1)[1].lstrip("/")
            if tail and ".." not in tail.split("/"):
                return tail
            continue
        if "/" in s and not s.startswith("/"):
            tail = s.lstrip("/")
            if tail and ".." not in tail.split("/"):
                return tail
            continue
        # Else treat as path
        bn = os.path.basename(s)
        if bn:
            return bn
    return ""


def best_image_url(*candidates: Any) -> str:
    """Return a browser-safe 8504 proxy URL for an internal image candidate.

    The R2 bucket may be private and 9008 may only be LAN/Tailscale-visible,
    so browsers should not receive direct R2/9008 URLs. They receive a
    relative 8504 proxy URL and the server performs the storage fallback.
    """
    ref = extract_basename(*candidates)
    if not ref:
        return ""
    return f"/proxy/image/whatnot_cards/{quote(ref, safe='/._-')}"


def derive_chips(row: dict[str, Any]) -> list[str]:
    """Compute the chip cluster that should render on the tile.

    Reads from the row's trust_bucket / pending_reasons / raw fields.
    Always returns INTERNAL REVIEW ONLY + the standard NOT_* labels for
    rows that are not in the trusted feed (which v0 never promotes).
    """
    chips: list[str] = [CAUTION_LABEL_CONSTANT]
    bucket = (row.get("trust_bucket") or "").upper()
    pending_reasons = row.get("pending_reasons") or []
    if not isinstance(pending_reasons, list):
        pending_reasons = []

    # Standard internal-only labels on every non-trusted row
    if bucket != "TRUSTED_CANDIDATE":
        chips.append(NOT_TRUSTED_LABEL)
        chips.append(NOT_MAZIFIED_LABEL)
        chips.append(NOT_PUBLIC_READY_LABEL)
        chips.append(NOT_VALUATION_SAFE_LABEL)

    # Proof / image safety
    raw = row.get("raw") or {}
    if isinstance(raw, dict):
        pbs = raw.get("proof_binding_status") or row.get("proof_binding_status")
        if pbs in ("unknown_unreadable",):
            chips.append(PROOF_INTERNAL_ONLY_LABEL)
            chips.append(PROOF_BLOCKED_LABEL)
        elif pbs in ("mismatch_neighboring_auction", "multiple_auction_numbers"):
            chips.append(HARD_BLOCKED_LABEL)
            chips.append(PROOF_BLOCKED_LABEL)
    if row.get("public_image_safety") == "unsafe_internal_only" or bucket != "TRUSTED_CANDIDATE":
        chips.append(PUBLIC_IMAGE_SAFE_FALSE_LABEL)

    # Pending-reasons-driven chips
    pr_set = {str(x) for x in pending_reasons}
    if "VISUAL_CONTEXT_PUBLISH_HOLD" in pr_set:
        chips.append(VISUAL_CONTEXT_HOLD_LABEL)
    if "BINDING_REVIEW" in pr_set:
        chips.append(BINDING_REVIEW_LABEL)
    if "CAPTURE_REVIEW" in pr_set:
        chips.append(CAPTURE_REVIEW_LABEL)
    if "INTERSTITIAL_CARRY_FORWARD" in pr_set:
        chips.append(INTERSTITIAL_CARRY_FORWARD_LABEL)
    if "LOW_CONFIDENCE" in pr_set or "UNIDENTIFIED_WITH_IMAGE" in pr_set:
        chips.append(IDENTITY_REPAIR_LABEL)
    if "FILENAME_COLLISION_RISK" in pr_set or "NO_IMAGE" in pr_set:
        chips.append(HARD_BLOCKED_LABEL)

    # High-value badge
    price = row.get("sold_price") or 0
    try:
        if float(price) >= 1000.0:
            chips.append(HIGH_VALUE_LABEL)
    except (TypeError, ValueError):
        pass

    # Human review marker. REVIEW_HIGH_VALUE/PRICE_HIGH_REVIEW are price
    # metadata only and must not imply workflow blocking.
    if bucket in ("FLAG_REVIEW", "RAW_ONLY"):
        chips.append(HUMAN_REVIEW_REQUIRED_LABEL)

    # De-dup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for c in chips:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Row-defect safety badges (distinct from posture chips).
#
# Posture chips (derive_chips above) describe LANE state: INTERNAL REVIEW
# ONLY, NOT TRUSTED, PROOF INTERNAL ONLY, etc. -- they answer "what is
# this row's review posture today?"
#
# Safety badges (derive_safety_badges below) describe ROW DEFECTS: front
# capture class, proof-to-front match, overlay text/risk, non-card object
# detection, price outlier, identity drift, public-image-safe reason --
# they answer "what specific defect, if any, blocks this row?"
#
# Critical fail-closed rule:
#   - Field value None / missing  -> emit a single "<NAME> unknown" badge
#                                    (NOT a "safe" / OK badge)
#   - Field value present         -> emit "<NAME>: <value>" badge with
#                                    severity color tied to the value
# We NEVER coerce missing -> false. Operators must see absence as absence.
# ---------------------------------------------------------------------------

SAFETY_BADGE_FIELDS = (
    ("front_capture_class",              "FRONT CLASS"),
    ("proof_match_to_front",             "PROOF MATCH"),
    ("proof_overlay_risk",               "OVERLAY CONTEXT"),
    ("non_card_object_detected",         "NON-CARD"),
    ("price_context_outlier",            "PRICE OUTLIER"),
    ("identity_enrichment_disagreement", "IDENTITY DRIFT"),
    ("public_image_safe_reason",         "PUBLIC IMAGE REASON"),
)


def _severity_for(field: str, value: Any) -> str:
    """Map a (field, value) pair to a severity bucket.

    Returns one of: "unknown" | "info" | "ok" | "warn" | "block".
    Conservative: anything we don't explicitly recognize -> "warn".
    """
    if value is None or value == "":
        return "unknown"
    s = str(value).strip().lower()
    if field == "front_capture_class":
        if s in ("valid_card_front", "card_front", "card"):
            return "ok"
        if s in ("stream_still", "interstitial", "unknown"):
            return "warn"
        if s in ("non_card", "phone_screen", "logo", "popup"):
            return "block"
        return "info"
    if field == "proof_match_to_front":
        if s in ("match", "valid", "true", "yes"):
            return "ok"
        if s in ("mismatch", "different_auction", "neighboring_auction"):
            return "block"
        if s in ("unknown", "low_confidence"):
            return "warn"
        return "warn"
    if field == "proof_overlay_risk":
        if s in ("low", "none", "safe"):
            return "ok"
        if s in ("medium", "moderate"):
            return "warn"
        # Buyer/winner handle in frame = internal evidence detail, NOT a hard
        # blocker. The handle is stripped before any public publish. Blue info
        # badge only. Real proof-context blockers (post_sale, timer_zero,
        # wrong_auction, giveaway, confetti, unsubstantiated proof) are
        # Tier B and require DB-value verification before being added here.
        if s in ("winner_visible", "buyer_visible"):
            return "info"
        if s in ("high",):
            return "block"
        return "warn"
    if field == "non_card_object_detected":
        if s in ("true", "yes", "detected"):
            return "block"
        if s in ("false", "no", "none"):
            return "ok"
        return "warn"
    if field == "price_context_outlier":
        if s in ("none", "false", "no", "in_band"):
            return "ok"
        if s in ("high", "low", "outlier", "true", "yes"):
            return "warn"
        return "warn"
    if field == "identity_enrichment_disagreement":
        if s in ("none", "false", "agree", "match"):
            return "ok"
        if s in ("disagree", "true", "yes", "conflict"):
            return "block"
        return "warn"
    if field == "public_image_safe_reason":
        if s in ("safe", "ok", "publishable"):
            return "ok"
        return "block"
    return "warn"


def derive_safety_badges(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the 7 row-defect safety badges as structured dicts.

    Each badge is:
        { "field":   "<snake_case field name>",
          "label":   "<UI label, e.g. 'FRONT CLASS'>",
          "value":   <raw value or None>,
          "severity":"unknown"|"info"|"ok"|"warn"|"block",
          "display": "<label>: <value-or-unknown>",
          "absent":  bool   # True iff value is None or empty
        }

    Always returns exactly len(SAFETY_BADGE_FIELDS) badges. Absent fields
    produce a badge with severity="unknown" so the UI surfaces the gap.
    """
    out: list[dict[str, Any]] = []
    for field, label in SAFETY_BADGE_FIELDS:
        v = row.get(field)
        # Treat the json-encoded "null"/JSON-quoted empty strings as missing
        if isinstance(v, str):
            stripped = v.strip().strip('"')
            if stripped.lower() in ("", "null", "none"):
                v = None
            else:
                v = stripped
        sev = _severity_for(field, v)
        absent = (v is None) or (v == "")
        display = f"{label}: unknown" if absent else f"{label}: {v}"
        out.append({
            "field":    field,
            "label":    label,
            "value":    v,
            "severity": sev,
            "display":  display,
            "absent":   absent,
        })
    return out


def privacy_scan_payload(payload: Any) -> dict[str, Any]:
    """Return a self-report of any private-key or path leakage in a payload.

    Used by tests and by /api/v1/health to confirm no leaks.
    """
    import json as _json
    import re as _re

    blob = _json.dumps(payload, default=str)
    hits = {}
    for term in ("buyer", "winner", "bidder", "customer"):
        hits[term] = bool(_re.search(rf"\b{term}\b", blob, _re.I))
    user_paths = _re.findall(r"/Users/[^\"\\s,]+", blob)
    return {
        "private_text_hits": hits,
        "any_private_hit": any(hits.values()),
        "raw_user_path_count": len(user_paths),
        "raw_user_path_sample": user_paths[:3],
    }
