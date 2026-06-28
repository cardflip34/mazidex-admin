"""mazidex-admin v0 — runtime config.

All values can be overridden via environment variables.
Defaults are safe for local M4 :8504 development.
"""
from __future__ import annotations

import os


# --- Server bind ---------------------------------------------------------
BIND_HOST = os.environ.get("MAZIDEX_ADMIN_HOST", "100.111.48.86")
BIND_PORT = int(os.environ.get("MAZIDEX_ADMIN_PORT", "8504"))

# --- Database --------------------------------------------------------------
# Read from .env.mazi (sourced before launch). We use the same MAZI_DB_URL
# that the rest of the stack uses. The app NEVER mutates sale rows.
DB_URL = os.environ.get("MAZI_DB_URL", "")

# --- Image URL prefix ------------------------------------------------------
# Order of preference for image URLs returned to the client:
#   1. image_front_neon_url / image_back_neon_url    (R2 HTTPS)
#   2. 9008 image proxy fallback (internal LAN only) if set
#   3. proof image basename rendered via /image-proxy/<basename>
LEGACY_9008_IMAGE_PROXY_BASE = os.environ.get(
    "MAZI_9008_IMAGE_PROXY_BASE", "http://100.111.48.86:9008/whatnot_cards"
).rstrip("/")

# --- Write gate -----------------------------------------------------------
# v0 ships with the decision write endpoint DISABLED. Operator flips this
# to "1"/"true"/"yes" via env when read-only acceptance tests pass.
def review_write_enabled() -> bool:
    """Single runtime source of truth for review-decision writes."""
    return os.environ.get(
        "MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def review_write_scope_source_keys() -> frozenset[str]:
    """Optional operator-set allowlist for a controlled write-gate opening."""
    raw = os.environ.get("MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_SOURCE_KEYS", "")
    return frozenset(key.strip() for key in raw.split(",") if key.strip())


def review_write_scope_test_run_id() -> str:
    """Optional marker that must be present in row_meta for scoped writes."""
    return os.environ.get("MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_TEST_RUN_ID", "").strip()


def review_write_scope_identified_all() -> bool:
    """Durable scope: allow Confirm on ANY row currently in Identified.

    When on (and review_write_enabled), the scoped gate no longer requires a
    static source_key allowlist for an Identified->Trusted Confirm. Membership
    is still enforced downstream by promote_identified_to_trusted's
    not_in_identified_state guard, and non-confirm decisions stay closed.
    Default off so the gate remains fail-closed unless explicitly opened.
    """
    return os.environ.get(
        "MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


# Backward-compatible snapshot for older imports. New code should call
# review_write_enabled() so health, UI copy, and POST behavior agree.
REVIEW_WRITE_ENABLED = review_write_enabled()

# --- Decision allowlist ---------------------------------------------------
# Mirrors the spec; review_decision_events accepts these via the `decision`
# column. NEW workbench decision types are NOT new schema; they are new
# string values.
VALID_DECISIONS = frozenset({
    # Workbench v0 decision types
    "chrome_advanced_to_human_review",
    "human_confirmed_for_final_gate",
    "mazified",
    "needs_identity_enrichment",
    "needs_better_image",
    "proof_binding_unsubstantiated",
    "visual_context_hold",
    "deny",
    "rejected_from_public_path",
    "hidden_from_work_queue",
    # Legacy 9009 decision types (preserved for parity)
    "confirm",
    "flag",
    "reject",
    "workable",
    "clear",
})

# --- Privacy / safety scrub -----------------------------------------------
# Top-level keys that must NEVER appear in API responses.
PRIVATE_KEY_SUBSTRINGS = (
    "buyer",
    "winner",
    "bidder",
    "customer",
    "private",
    "email",
    "cookie",
)

# Path prefixes that must NEVER leak in image URLs or string fields.
FORBIDDEN_PATH_PREFIXES = ("/Users/", "file://")

# Caution labels surfaced in the tile UI. Used by safety.derive_chips().
CAUTION_LABEL_CONSTANT = "INTERNAL REVIEW ONLY"
NOT_TRUSTED_LABEL = "NOT TRUSTED"
NOT_MAZIFIED_LABEL = "NOT MAZIFIED"
NOT_PUBLIC_READY_LABEL = "NOT PUBLIC READY"
NOT_VALUATION_SAFE_LABEL = "NOT VALUATION SAFE"
PROOF_INTERNAL_ONLY_LABEL = "PROOF INTERNAL ONLY"
PUBLIC_IMAGE_SAFE_FALSE_LABEL = "PUBLIC IMAGE SAFE FALSE"
VISUAL_CONTEXT_HOLD_LABEL = "VISUAL CONTEXT HOLD"
HARD_BLOCKED_LABEL = "HARD BLOCKED"
HUMAN_REVIEW_REQUIRED_LABEL = "HUMAN REVIEW REQUIRED"
HIGH_VALUE_LABEL = "HIGH VALUE"
BINDING_REVIEW_LABEL = "BINDING_REVIEW"
CAPTURE_REVIEW_LABEL = "CAPTURE_REVIEW"
INTERSTITIAL_CARRY_FORWARD_LABEL = "INTERSTITIAL_CARRY_FORWARD"
PROOF_BLOCKED_LABEL = "PROOF_BLOCKED"
IDENTITY_REPAIR_LABEL = "IDENTITY_REPAIR"

# --- Queue limits ---------------------------------------------------------
DEFAULT_LIMIT = 100
MAX_LIMIT = 2000
