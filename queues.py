"""mazidex-admin v0 — queue SQL templates.

Each function returns a (sql, params) pair for psycopg.execute.
All reads are pure SELECT against Neon views; no mutations.
"""
from __future__ import annotations

import re
from typing import Any

from config import DEFAULT_LIMIT, MAX_LIMIT


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


# Columns we return on every row. Excludes the raw jsonb (caller may
# extract specific keys via safe accessors).
_BASE_FIELDS = (
    "feed_observation_id",
    "source_key",
    "observed_in",
    "comp_id",
    "review_id",
    "trust_bucket",
    "trust_score",
    "risk_level",
    "source_file",
    "auction_number",
    "seller",
    "sold_price",
    "feed_generated_at",
    "first_seen_at",
    "last_seen_at",
    "feed_count",
    "feed_card_id",
    "title",
    "card_name",
    "player",
    "brand",
    "set_name",
    "variant",
    "year",
    "grade",
    "grade_chip",
    "condition",
    "category",
    "sport",
    "image_front_neon_url",
    "image_back_neon_url",
    "image_front",
    "image_back",
    "last_sale_date",
    "pending_reasons",
    "warnings",
    "review_decision",
    "review_notes",
    "reviewed_by",
    "reviewed_at",
)

# JSON extracts off the `raw` column. (suffix expression, alias)
_RAW_EXTRACTS = (
    # Descriptor scalar extracts. Keep these scalar, not raw nested JSON, so
    # broad queues stay fast while app.py can still build canonical_identity.
    ("#>> '{api_scan,player}'",             "api_scan_player"),
    ("#>> '{api_scan,name}'",               "api_scan_name"),
    ("#>> '{api_scan,year}'",               "api_scan_year"),
    ("#>> '{api_scan,brand}'",              "api_scan_brand"),
    ("#>> '{api_scan,card_set}'",           "api_scan_card_set"),
    ("#>> '{api_scan,set_name}'",           "api_scan_set_name"),
    ("#>> '{api_scan,parallel}'",           "api_scan_parallel"),
    ("#>> '{api_scan,variant}'",            "api_scan_variant"),
    ("#>> '{api_scan,insert_type}'",        "api_scan_insert_type"),
    ("#>> '{api_scan,card_number}'",        "api_scan_card_number"),
    ("#>> '{api_scan,grade}'",              "api_scan_grade"),
    ("#>> '{api_scan,grading_company}'",    "api_scan_grading_company"),
    ("#>> '{api_scan,cert_number}'",        "api_scan_cert_number"),
    ("#>> '{api_scan,graded}'",             "api_scan_graded"),
    ("#>> '{api_scan,auto}'",               "api_scan_auto"),
    ("#>> '{api_scan,rookie}'",             "api_scan_rookie"),
    ("#>> '{api_scan,patch}'",              "api_scan_patch"),
    ("#>> '{api_scan,rpa}'",                "api_scan_rpa"),
    ("#>> '{api_scan,serial_numbered}'",    "api_scan_serial_numbered"),
    ("#>> '{api_scan,serial_current}'",     "api_scan_serial_current"),
    ("#>> '{api_scan,serial_total}'",       "api_scan_serial_total"),
    ("#>> '{api_scan,team}'",               "api_scan_team"),
    ("#>> '{api_scan,sport}'",              "api_scan_sport"),
    ("#>> '{api_scan,auction_type}'",       "api_scan_auction_type"),
    ("#>> '{api_scan,card_count}'",         "api_scan_card_count"),
    ("#>> '{card_ocr,player}'",             "card_ocr_player"),
    ("#>> '{card_ocr,year}'",               "card_ocr_year"),
    ("#>> '{card_ocr,brand}'",              "card_ocr_brand"),
    ("#>> '{card_ocr,card_set}'",           "card_ocr_card_set"),
    ("#>> '{card_ocr,set_name}'",           "card_ocr_set_name"),
    ("#>> '{card_ocr,serial_numbered}'",    "card_ocr_serial_numbered"),
    ("#>> '{card_ocr,auto}'",               "card_ocr_auto"),
    ("#>> '{card_ocr,rookie}'",             "card_ocr_rookie"),
    ("#>> '{card_ocr,patch}'",              "card_ocr_patch"),
    ("#>> '{card_ocr,team}'",               "card_ocr_team"),
    ("-> 'proof_binding_status'",  "proof_binding_status"),
    ("-> 'proof_image_status'",    "proof_image_status"),
    ("-> 'front_image_status'",    "front_image_status"),
    ("-> 'back_image_status'",     "back_image_status"),
    ("-> 'mazi_cert_status'",      "mazi_cert_status"),
    ("-> 'publishReady'",          "publish_ready"),
    ("-> 'risk_flags'",            "risk_flags"),
    ("-> 'review_proof'",          "review_proof_basename"),
    ("-> 'originalProofImage'",    "original_proof_basename"),
    # ---- Row-defect safety signals -------------------------------------
    # All NULL upstream today; the selectors are wired so the API surface
    # is stable when the upstream pipeline starts writing these keys.
    # Frontend MUST treat NULL as "unknown" (absent), never "safe" / False.
    ("-> 'front_capture_class'",                "front_capture_class"),
    ("-> 'proof_match_to_front'",               "proof_match_to_front"),
    ("-> 'proof_overlay_text'",                 "proof_overlay_text"),
    ("-> 'proof_overlay_risk'",                 "proof_overlay_risk"),
    ("-> 'non_card_object_detected'",           "non_card_object_detected"),
    ("-> 'price_context_outlier'",              "price_context_outlier"),
    ("-> 'identity_enrichment_disagreement'",   "identity_enrichment_disagreement"),
    ("-> 'public_image_safe_reason'",           "public_image_safe_reason"),
    ("#>> '{ops_display_image}'",                "ops_display_image"),
    ("#>> '{ops_display_image_source}'",         "ops_display_image_source"),
    ("#>> '{ops_display_image_status}'",         "ops_display_image_status"),
    ("#>> '{ops_display_back_image}'",           "ops_display_back_image"),
    ("#>> '{ops_display_back_image_status}'",    "ops_display_back_image_status"),
    ("#>> '{review_context_image}'",             "review_context_image"),
    ("#>> '{review_context_images,0}'",          "review_context_image_first"),
    ("#>> '{proof_internal_images,0}'",          "proof_internal_image_first"),
    ("#>> '{proof_images,0}'",                   "proof_image_first"),
    ("#>> '{cdn_image}'",                        "cdn_image"),
)


# Names of the row-defect safety fields. Exported for tests + UI.
# Acceptance gate asserts every row carries each key (value can be None
# = "unknown" / absent), and NEVER literal False / "safe" / 0 unless
# explicitly written upstream as such.
SAFETY_FIELD_NAMES = (
    "front_capture_class",
    "proof_match_to_front",
    "proof_overlay_text",
    "proof_overlay_risk",
    "non_card_object_detected",
    "price_context_outlier",
    "identity_enrichment_disagreement",
    "public_image_safe_reason",
)


def _row_fields(prefix: str = "") -> str:
    """Build the SELECT list. Pass `ofs.` (or any alias.) for joins.

    For unjoined queries pass "" — columns stay unqualified, matching
    the legacy behavior. For queries that JOIN another table that also
    has `source_key` / `created_at` / `raw`, pass `ofs.` so Postgres
    doesn't raise AmbiguousColumn.
    """
    p = prefix.rstrip(".")
    p = (p + ".") if p else ""
    cols = [f"{p}{c}" for c in _BASE_FIELDS]
    cols += [f"{p}raw {expr} AS {alias}" for expr, alias in _RAW_EXTRACTS]
    return ",\n    ".join(cols)


ROW_FIELDS = _row_fields("")
ROW_FIELDS_OFS = _row_fields("ofs")


# ---------------------------------------------------------------------------
# queue_reason CASE expressions (2026-05-30, senior-pass)
#
# Each queue SQL below SELECTs a per-row `queue_reason` column whose value
# names WHICH branch of the WHERE predicate qualified the row. The value is
# a stable snake_case code (no trailing punctuation, no whitespace) so that
# the SPA can both DISPLAY it and DIFFERENTIATE filter chips inside a queue.
#
# Policy: queue_reason is METADATA. It is NEVER consumed by the write gate
# or by row_allowed_decisions. Removing the CASE column would degrade the UX
# but break no decisions logic. Adding new branches is safe and additive.
# ---------------------------------------------------------------------------
_QR_WORKING = """
    CASE
      WHEN trust_bucket = 'FLAG_REVIEW'        THEN 'flagged_for_review'
      WHEN trust_bucket = 'RAW_ONLY'           THEN 'raw_capture_only'
      WHEN trust_bucket = 'TRUSTED_CANDIDATE'  THEN 'trusted_candidate'
      WHEN trust_bucket = 'REVIEW_HIGH_VALUE'  THEN 'high_value_review'
      ELSE 'working_other'
    END AS queue_reason
"""

_QR_HIGH_VALUE = """
    CASE
      WHEN COALESCE(sold_price, 0) >= 1000 THEN 'price_tier_1k_plus'
      WHEN COALESCE(sold_price, 0) >= 500  THEN 'price_tier_500_999'
      WHEN COALESCE(sold_price, 0) >= 100  THEN 'price_tier_100_499'
      ELSE 'price_below_100'
    END AS queue_reason
"""

_QR_PROOF_REVIEW = """
    CASE
      WHEN (raw ->> 'proof_binding_status') = 'unknown_unreadable'
          THEN 'proof_binding_unknown_unreadable'
      WHEN (raw ->> 'proof_binding_status') = 'mismatch_neighboring_auction'
          THEN 'proof_binding_neighbor_mismatch'
      WHEN (raw ->> 'proof_binding_status') = 'multiple_auction_numbers'
          THEN 'proof_binding_multi_auction'
      WHEN pending_reasons ? 'INTERSTITIAL_CARRY_FORWARD'
          THEN 'pending_interstitial_carry_forward'
      WHEN pending_reasons ? 'CDN_ONLY'        THEN 'pending_cdn_only'
      WHEN pending_reasons ? 'BACK_ONLY'       THEN 'pending_back_only'
      WHEN pending_reasons ? 'ROLE_WARNING'    THEN 'pending_role_warning'
      WHEN pending_reasons ? 'SELLER_SUSPECT'  THEN 'pending_seller_suspect'
      WHEN COALESCE(image_front, '') = ''      THEN 'image_front_missing'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%sale_frame_rescue%%'
          THEN 'review_reason_sale_frame_rescue'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%interstitial%%'
          THEN 'review_reason_interstitial'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%auction-mismatch%%'
          THEN 'review_reason_auction_mismatch'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%binding-mismatch%%'
          THEN 'review_reason_binding_mismatch'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%missing-front%%'
          THEN 'review_reason_missing_front'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%multi-card%%'
          THEN 'review_reason_multi_card'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%popup%%'
          THEN 'review_reason_popup'
      WHEN lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%not-card%%'
          THEN 'review_reason_not_card'
      ELSE 'proof_review_other'
    END AS queue_reason
"""

_QR_NEEDS_IDENTITY = """
    CASE
      WHEN lower(COALESCE(title, '')) = 'whatnot live capture'
          THEN 'title_is_generic_live_capture'
      WHEN COALESCE(player, '') = '' AND COALESCE(title, '') = ''
          THEN 'player_and_title_blank'
      WHEN COALESCE(player, '') = ''                     THEN 'player_blank'
      WHEN COALESCE(title, '') = ''                      THEN 'title_blank'
      WHEN pending_reasons ? 'LOW_CONFIDENCE'            THEN 'low_confidence_identification'
      WHEN pending_reasons ? 'UNIDENTIFIED_WITH_IMAGE'   THEN 'unidentified_with_image'
      ELSE 'needs_identity_other'
    END AS queue_reason
"""

_QR_NEEDS_BETTER_IMAGE = """
    CASE
      WHEN COALESCE(raw ->> 'front_image_status', '') = 'missing'        THEN 'front_image_missing'
      WHEN COALESCE(raw ->> 'front_image_status', '') = 'stream_still'   THEN 'front_image_stream_still'
      WHEN COALESCE(raw ->> 'front_image_status', '') = 'interstitial'   THEN 'front_image_interstitial'
      WHEN COALESCE(raw ->> 'front_image_status', '') = 'unknown'        THEN 'front_image_status_unknown'
      WHEN (raw ->> 'back_image_status') = 'missing'                     THEN 'back_image_missing'
      WHEN pending_reasons ? 'FRONT_IMAGE_MISSING'                       THEN 'pending_front_image_missing'
      WHEN pending_reasons ? 'FRONT_IMAGE_STREAM_STILL'                  THEN 'pending_front_image_stream_still'
      WHEN pending_reasons ? 'CDN_ONLY'                                  THEN 'pending_cdn_only'
      ELSE 'needs_better_image_other'
    END AS queue_reason
"""




WORKING_QUEUE_SQL = f"""
    -- 2026-05-30 senior-pass:
    --   * admit REVIEW_HIGH_VALUE so price-tier rows are NOT gated out
    --     (HIGH VALUE is metadata, not a workflow bucket).
    --   * add queue_reason CASE so the SPA can show "why this row is here"
    --     without inferring from trust_bucket.
    SELECT {ROW_FIELDS},
           {{_QR_WORKING}}
    FROM operational_pending_sales
    WHERE trust_bucket IN (
              'FLAG_REVIEW', 'RAW_ONLY', 'TRUSTED_CANDIDATE',
              'REVIEW_HIGH_VALUE'
          )
      AND source_key NOT IN (
          SELECT DISTINCT source_key FROM review_decision_events
          WHERE decision IN ('rejected_from_public_path', 'hidden_from_work_queue')
      )
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""
WORKING_QUEUE_SQL = WORKING_QUEUE_SQL.replace("{_QR_WORKING}", _QR_WORKING.strip())




HIGH_VALUE_SQL = f"""
    SELECT {ROW_FIELDS},
           {{_QR_HIGH_VALUE}}
    FROM operational_pending_sales
    -- Price view only. HIGH VALUE is metadata/sort/filter, not a review
    -- workflow bucket and not a Mazified eligibility gate. queue_reason
    -- surfaces the price tier so operators can sub-filter inside the view.
    WHERE COALESCE(sold_price, 0) >= 100
    ORDER BY sold_price DESC NULLS LAST, last_seen_at DESC NULLS LAST
    LIMIT %s
"""
HIGH_VALUE_SQL = HIGH_VALUE_SQL.replace("{_QR_HIGH_VALUE}", _QR_HIGH_VALUE.strip())


PROOF_REVIEW_SQL = f"""
    SELECT {ROW_FIELDS},
           {_QR_PROOF_REVIEW}
    FROM operational_pending_sales
    -- Union 8504 proof-binding conflicts with Streamlit's proof/capture
    -- quality review predicate so the workbench does not hide either class.
    WHERE (
        (raw ->> 'proof_binding_status') IN (
            'unknown_unreadable',
            'mismatch_neighboring_auction',
            'multiple_auction_numbers'
        )
        OR pending_reasons ?| ARRAY[
            'INTERSTITIAL_CARRY_FORWARD',
            'CDN_ONLY',
            'BACK_ONLY',
            'ROLE_WARNING',
            'SELLER_SUSPECT'
        ]
        OR COALESCE(image_front, '') = ''
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%sale_frame_rescue%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%interstitial%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%auction-mismatch%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%binding-mismatch%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%missing-front%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%multi-card%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%popup%%'
        OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%%not-card%%'
    )
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""
PROOF_REVIEW_SQL = PROOF_REVIEW_SQL.replace("{_QR_PROOF_REVIEW}", _QR_PROOF_REVIEW.strip())


NEEDS_IDENTITY_SQL = f"""
    SELECT {ROW_FIELDS},
           {{_QR_NEEDS_IDENTITY}}
    FROM operational_pending_sales
    WHERE (
        COALESCE(player, '') = ''
        OR COALESCE(title, '') = ''
        OR lower(COALESCE(title,'')) = 'whatnot live capture'
        OR (pending_reasons ? 'LOW_CONFIDENCE')
        OR (pending_reasons ? 'UNIDENTIFIED_WITH_IMAGE')
    )
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""
NEEDS_IDENTITY_SQL = NEEDS_IDENTITY_SQL.replace("{_QR_NEEDS_IDENTITY}", _QR_NEEDS_IDENTITY.strip())


NEEDS_BETTER_IMAGE_SQL = f"""
    SELECT {ROW_FIELDS},
           {{_QR_NEEDS_BETTER_IMAGE}}
    FROM operational_pending_sales
    WHERE (
        COALESCE(raw ->> 'front_image_status', '') IN ('stream_still', 'missing', 'unknown', 'interstitial')
        OR (raw ->> 'back_image_status') = 'missing'
        OR (pending_reasons ? 'FRONT_IMAGE_MISSING')
        OR (pending_reasons ? 'FRONT_IMAGE_STREAM_STILL')
        OR (pending_reasons ? 'CDN_ONLY')
    )
    ORDER BY COALESCE(sold_price, 0) DESC, last_seen_at DESC NULLS LAST
    LIMIT %s
"""
NEEDS_BETTER_IMAGE_SQL = NEEDS_BETTER_IMAGE_SQL.replace("{_QR_NEEDS_BETTER_IMAGE}", _QR_NEEDS_BETTER_IMAGE.strip())


CAPTURE_REVIEW_SQL = f"""
    SELECT {ROW_FIELDS}, 'capture_review_pending' AS queue_reason
    FROM operational_pending_sales
    WHERE pending_reasons ? 'CAPTURE_REVIEW'
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""


INTERSTITIAL_CARRY_FORWARD_SQL = f"""
    SELECT {ROW_FIELDS}, 'interstitial_carry_forward_unsubstantiated' AS queue_reason
    FROM operational_pending_sales
    WHERE pending_reasons ? 'INTERSTITIAL_CARRY_FORWARD'
      AND (
        COALESCE(raw ->> 'capture_class', '') = 'unsubstantiated'
        OR COALESCE(raw ->> 'auction_number_match', '') = 'no_overlay_number'
      )
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""


CHROME_ADVANCED_SQL = f"""
    WITH latest AS (
        SELECT DISTINCT ON (source_key) source_key, decision, created_at
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    )
    SELECT {ROW_FIELDS_OFS}, latest.decision AS queue_reason
    FROM operational_pending_sales ofs
    JOIN latest ON latest.source_key = ofs.source_key
    WHERE latest.decision = 'chrome_advanced_to_human_review'
    ORDER BY latest.created_at DESC
    LIMIT %s
"""


HUMAN_REVIEW_AI_APPROVED_SQL = f"""
    WITH latest AS (
        SELECT DISTINCT ON (source_key) source_key, decision, created_at
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    )
    SELECT {ROW_FIELDS_OFS}, latest.decision AS queue_reason
    FROM operational_pending_sales ofs
    JOIN latest ON latest.source_key = ofs.source_key
    WHERE latest.decision = 'human_confirmed_for_final_gate'
    ORDER BY latest.created_at DESC
    LIMIT %s
"""


MAZIFIED_SQL = f"""
    SELECT {ROW_FIELDS}
    FROM operational_feed_sales
    WHERE review_decision = 'mazified'
    ORDER BY reviewed_at DESC NULLS LAST, last_seen_at DESC NULLS LAST
    LIMIT %s
"""


FLAGGED_REVIEW_SQL = f"""
    SELECT {ROW_FIELDS}
    FROM operational_pending_sales
    WHERE jsonb_typeof(raw -> 'risk_flags') = 'array'
      AND jsonb_array_length(raw -> 'risk_flags') > 0
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""


REJECTED_HIDDEN_SQL = f"""
    WITH latest AS (
        SELECT DISTINCT ON (source_key) source_key, decision, created_at
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    )
    SELECT {ROW_FIELDS_OFS}, latest.decision AS queue_reason
    FROM operational_pending_sales ofs
    JOIN latest ON latest.source_key = ofs.source_key
    WHERE latest.decision IN ('rejected_from_public_path', 'hidden_from_work_queue')
    ORDER BY latest.created_at DESC
    LIMIT %s
"""


TRUSTED_VIEW_SQL = f"""
    SELECT {ROW_FIELDS}
    FROM stage1_trusted_sales_current
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""

IDENTIFIED_VIEW_SQL = f"""
    SELECT {ROW_FIELDS}, 'identified_front_gemini_sweep' AS queue_reason
    FROM identified_sales_current
    ORDER BY last_seen_at DESC NULLS LAST
    LIMIT %s
"""


QUEUE_MAP: dict[str, str] = {
    "identified_view": IDENTIFIED_VIEW_SQL,
    "working": WORKING_QUEUE_SQL,
    "high_value": HIGH_VALUE_SQL,
    "proof_review": PROOF_REVIEW_SQL,
    "needs_identity": NEEDS_IDENTITY_SQL,
    "needs_better_image": NEEDS_BETTER_IMAGE_SQL,
    "capture_review": CAPTURE_REVIEW_SQL,
    "interstitial_carry_forward": INTERSTITIAL_CARRY_FORWARD_SQL,
    "chrome_advanced": CHROME_ADVANCED_SQL,
    "human_review_ai_approved": HUMAN_REVIEW_AI_APPROVED_SQL,
    "mazified": MAZIFIED_SQL,
    "flagged_review": FLAGGED_REVIEW_SQL,
    "rejected_hidden": REJECTED_HIDDEN_SQL,
    "trusted_view": TRUSTED_VIEW_SQL,
}


_WATERMARKED_IMAGE_PREDICATE = """
    (
        COALESCE(image_front, '') ILIKE '%%_mazi.jpg%%'
        OR COALESCE(image_front_neon_url, '') ILIKE '%%_mazi.jpg%%'
        OR COALESCE(raw->>'front_image_stamped', '') ILIKE '%%_mazi.jpg%%'
        OR COALESCE(raw#>>'{evidence_stamp,front_stamped_image}', '') ILIKE '%%_mazi.jpg%%'
        OR COALESCE(raw#>>'{capture_quality,front_stamped}', '') = 'true'
    )
"""


def _watermark_order_sql() -> str:
    return f"""
    CASE WHEN {_WATERMARKED_IMAGE_PREDICATE} THEN 0 ELSE 1 END,
    last_seen_at DESC NULLS LAST,
    first_seen_at DESC NULLS LAST
"""


_WATERMARKED_SUBQUERY_PREDICATE = """
    (
        COALESCE(q.image_front, '') ILIKE '%%_mazi.jpg%%'
        OR COALESCE(q.image_front_neon_url, '') ILIKE '%%_mazi.jpg%%'
    )
"""


def _strip_final_order_limit(sql: str) -> str:
    """Remove a queue template's final ORDER BY/LIMIT for dynamic wrapping."""
    without_limit = sql.rsplit("LIMIT %s", 1)[0]
    without_order = without_limit.rsplit("ORDER BY", 1)[0]
    return without_order.strip()


def _dynamic_order_sql(sort: str | None) -> str:
    if sort == "watermarked_newest":
        return f"""
        CASE WHEN {_WATERMARKED_SUBQUERY_PREDICATE} THEN 0 ELSE 1 END,
        q.last_seen_at DESC NULLS LAST,
        q.first_seen_at DESC NULLS LAST
        """
    if sort == "oldest":
        return "q.first_seen_at ASC NULLS LAST, q.last_seen_at ASC NULLS LAST"
    if sort == "price_high":
        return "q.sold_price DESC NULLS LAST, q.last_seen_at DESC NULLS LAST"
    if sort == "price_low":
        return "q.sold_price ASC NULLS LAST, q.last_seen_at DESC NULLS LAST"
    return "q.last_seen_at DESC NULLS LAST, q.first_seen_at DESC NULLS LAST"


_SEARCH_COLUMNS = (
    "q.comp_id",
    "q.source_key",
    "q.source_file",
    "q.auction_number::text",
    "q.seller",
    "q.title",
    "q.card_name",
    "q.player",
    "q.brand",
    "q.set_name",
    "q.variant",
    "q.grade",
    "q.grade_chip",
    "q.condition",
    "q.category",
    "q.sport",
    "q.year::text",
    "q.api_scan_card_number",
    "q.api_scan_serial_numbered",
    "q.api_scan_grading_company",
    "q.api_scan_grade",
    "q.api_scan_team",
    "q.card_ocr_serial_numbered",
)


def _search_tokens(search: str | None) -> list[str]:
    if not search:
        return []
    tokens = re.findall(r"[A-Za-z0-9./#_-]+", str(search).lower())
    return [t for t in tokens if t]


def _search_clause(search: str | None, params: list[Any]) -> str:
    tokens = _search_tokens(search)
    if not tokens:
        return ""
    haystack = "lower(concat_ws(' ', " + ", ".join(_SEARCH_COLUMNS) + "))"
    parts = []
    for token in tokens[:12]:
        params.append(f"%{token}%")
        parts.append(f"{haystack} LIKE %s")
    return " AND ".join(parts)


def _dynamic_queue_sql(
    name: str,
    sort: str | None,
    watermarked_only: bool,
    search: str | None,
) -> tuple[str, list[Any]]:
    base = _strip_final_order_limit(QUEUE_MAP[name])
    params: list[Any] = []
    filters: list[str] = []
    if watermarked_only:
        filters.append(_WATERMARKED_SUBQUERY_PREDICATE)
    search_filter = _search_clause(search, params)
    if search_filter:
        filters.append(search_filter)
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    return f"""
    SELECT q.*, count(*) OVER() AS __total_count
    FROM (
{base}
    ) q
    {where_sql}
    ORDER BY {_dynamic_order_sql(sort)}
    LIMIT %s
    OFFSET %s
""", params


def _working_queue_sql(sort: str | None, watermarked_only: bool) -> str:
    wm_filter = f"      AND {_WATERMARKED_IMAGE_PREDICATE}\n" if watermarked_only else ""
    if sort == "watermarked_newest":
        order_by = _watermark_order_sql()
    elif sort == "oldest":
        order_by = "first_seen_at ASC NULLS LAST, last_seen_at ASC NULLS LAST"
    elif sort == "price_high":
        order_by = "sold_price DESC NULLS LAST, last_seen_at DESC NULLS LAST"
    elif sort == "price_low":
        order_by = "sold_price ASC NULLS LAST, last_seen_at DESC NULLS LAST"
    else:
        order_by = "last_seen_at DESC NULLS LAST, first_seen_at DESC NULLS LAST"
    return f"""
    SELECT {ROW_FIELDS}
    FROM operational_pending_sales
    WHERE trust_bucket IN ('FLAG_REVIEW', 'RAW_ONLY', 'TRUSTED_CANDIDATE', 'REVIEW_HIGH_VALUE')
      AND source_key NOT IN (
          SELECT DISTINCT source_key FROM review_decision_events
          WHERE decision IN ('rejected_from_public_path', 'hidden_from_work_queue')
      )
{wm_filter}    ORDER BY {order_by}
    LIMIT %s
"""


def _trusted_view_sql(sort: str | None, watermarked_only: bool) -> str:
    wm_filter = f"    WHERE {_WATERMARKED_IMAGE_PREDICATE}\n" if watermarked_only else ""
    if sort == "watermarked_newest":
        order_by = _watermark_order_sql()
    elif sort == "oldest":
        order_by = "first_seen_at ASC NULLS LAST, last_seen_at ASC NULLS LAST"
    elif sort == "price_high":
        order_by = "sold_price DESC NULLS LAST, last_seen_at DESC NULLS LAST"
    elif sort == "price_low":
        order_by = "sold_price ASC NULLS LAST, last_seen_at DESC NULLS LAST"
    else:
        order_by = "last_seen_at DESC NULLS LAST, first_seen_at DESC NULLS LAST"
    return f"""
    SELECT {ROW_FIELDS}
    FROM stage1_trusted_sales_current
{wm_filter}    ORDER BY {order_by}
    LIMIT %s
"""


def queue_sql(
    name: str,
    limit: int | None = None,
    sort: str | None = None,
    watermarked_only: bool = False,
    search: str | None = None,
    page: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    if name not in QUEUE_MAP:
        raise ValueError(f"unknown queue: {name}")
    safe_limit = _clamp_limit(limit)
    try:
        safe_page = max(1, int(page or 1))
    except (TypeError, ValueError):
        safe_page = 1
    offset = (safe_page - 1) * safe_limit
    sql, params = _dynamic_queue_sql(name, sort, watermarked_only, search)
    return sql, (*params, safe_limit, offset)


# Detail rows by comp_id / source_key. These return all matching source
# surfaces so the API can choose source-key-first and expose sibling rows.
ROW_DETAIL_SQL = f"""
    SELECT 'mazified' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM operational_feed_sales
    WHERE comp_id = %s
      AND review_decision = 'mazified'
    UNION ALL
    SELECT 'feed' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM operational_feed_sales
    WHERE comp_id = %s
      AND COALESCE(review_decision, '') <> 'mazified'
    UNION ALL
    SELECT 'pending' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM operational_pending_sales
    WHERE comp_id = %s
    UNION ALL
    SELECT 'identified' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM identified_sales_current
    WHERE comp_id = %s
    UNION ALL
    SELECT 'trusted' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM stage1_trusted_sales_current
    WHERE comp_id = %s
    LIMIT 50
"""


ROW_BY_SOURCE_KEY_SQL = f"""
    SELECT 'mazified' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM operational_feed_sales
    WHERE source_key = %s
      AND review_decision = 'mazified'
    UNION ALL
    SELECT 'feed' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM operational_feed_sales
    WHERE source_key = %s
      AND COALESCE(review_decision, '') <> 'mazified'
    UNION ALL
    SELECT 'pending' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM operational_pending_sales
    WHERE source_key = %s
    UNION ALL
    SELECT 'identified' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM identified_sales_current
    WHERE source_key = %s
    UNION ALL
    SELECT 'trusted' AS source_view,
           {ROW_FIELDS},
           raw AS raw_full
    FROM stage1_trusted_sales_current
    WHERE source_key = %s
    LIMIT 50
"""


# Decision history for a row
DECISIONS_SQL = """
    SELECT id, source_key, observed_in, review_id, comp_id, source_file,
           auction_number, decision, notes, reviewer, row_meta, created_at
    FROM review_decision_events
    WHERE comp_id = %s
    ORDER BY created_at DESC
    LIMIT 100
"""


# Per-tab counts in a single query (UNION ALL)
# ============================================================================
# External transactions (eBay + Goldin) — read-only mapping into 8504.
#
# Rules applied here (matches the orchestrator's wiring spec):
#   eBay:   source_code='ebay'   AND best_offer=FALSE
#                                AND verified_price_eligible=TRUE
#   Goldin: source_code='goldin' AND verified_price_eligible=TRUE
#                                (Goldin has no OBO in this corpus; left
#                                 unconditional on best_offer for forward-
#                                 compatibility if/when scraper changes.)
#
# These rows are EXTERNAL REFERENCE COMPS. They are NOT Whatnot captures,
# NOT proof-bound, NOT Trusted, NOT Mazified, NOT Public Ready. The SPA
# renders them in their own tiles with EXTERNAL/REFERENCE chips and never
# blends them into the Whatnot decision queues.
# ============================================================================
_EXTERNAL_FIELDS = """
    id,
    source_code,
    source_item_id,
    source_url,
    title,
    normalized_title,
    sold_price,
    sold_date,
    currency,
    best_offer,
    verified_price_eligible,
    sold_status,
    duplicate_status,
    raw,
    scraped_at,
    created_at
"""

_EXTERNAL_SEARCH_COLUMNS = (
    "title",
    "normalized_title",
    "source_item_id",
    "source_url",
    "raw->>'player_query'",
    "raw->>'player'",
    "raw->>'player_name'",
    "raw->>'grade'",
    "raw->>'grader'",
    "raw->>'year'",
    "raw->>'set_name'",
    "raw->>'card_number'",
    "raw->>'category'",
)


def _external_source_where(source: str) -> str:
    if source == "ebay":
        return """
        source_code = 'ebay'
        AND best_offer = FALSE
        AND verified_price_eligible = TRUE
        """
    if source in {"goldin", "fanatics", "heritage", "pwcc"}:
        return f"""
        source_code = '{source}'
        AND verified_price_eligible = TRUE
        """
    raise ValueError(f"unknown external source: {source}")


def _external_search_clause(search: str | None, params: list[Any]) -> str:
    tokens = _search_tokens(search)
    if not tokens:
        return ""
    parts = []
    for token in tokens[:12]:
        params.append(f"%{token}%")
        parts.append("title ILIKE %s")
    return " AND " + " AND ".join(parts)


def _external_source_sql(source: str, limit: int | None = None, search: str | None = None, page: int | None = None) -> tuple[str, tuple[Any, ...]]:
    safe_limit = _clamp_limit(limit)
    try:
        safe_page = max(1, int(page or 1))
    except (TypeError, ValueError):
        safe_page = 1
    offset = (safe_page - 1) * safe_limit
    search_params: list[Any] = []
    where_sql = _external_source_where(source)
    search_sql = _external_search_clause(search, search_params)
    return f"""
    SELECT {_EXTERNAL_FIELDS},
           (
             SELECT COUNT(*)
             FROM external_transactions
             WHERE {where_sql}
               {search_sql}
           ) AS __total_count
    FROM external_transactions
    WHERE {where_sql}
      {search_sql}
    ORDER BY sold_date DESC NULLS LAST, id DESC
    LIMIT %s
    OFFSET %s
    """, (*search_params, *search_params, safe_limit, offset)


EXTERNAL_EBAY_SQL = f"""
    SELECT {_EXTERNAL_FIELDS}
    FROM external_transactions
    WHERE source_code = 'ebay'
      AND best_offer = FALSE
      AND verified_price_eligible = TRUE
    ORDER BY sold_date DESC NULLS LAST, id DESC
    LIMIT %s
"""


EXTERNAL_GOLDIN_SQL = f"""
    SELECT {_EXTERNAL_FIELDS}
    FROM external_transactions
    WHERE source_code = 'goldin'
      AND verified_price_eligible = TRUE
    ORDER BY sold_date DESC NULLS LAST, id DESC
    LIMIT %s
"""

EXTERNAL_FANATICS_SQL = f"""
    SELECT {_EXTERNAL_FIELDS}
    FROM external_transactions
    WHERE source_code = 'fanatics'
      AND verified_price_eligible = TRUE
    ORDER BY sold_date DESC NULLS LAST, id DESC
    LIMIT %s
"""


EXTERNAL_HERITAGE_SQL = f"""
    SELECT {_EXTERNAL_FIELDS}
    FROM external_transactions
    WHERE source_code = 'heritage'
      AND verified_price_eligible = TRUE
    ORDER BY sold_date DESC NULLS LAST, id DESC
    LIMIT %s
"""


EXTERNAL_PWCC_SQL = f"""
    SELECT {_EXTERNAL_FIELDS}
    FROM external_transactions
    WHERE source_code = 'pwcc'
      AND verified_price_eligible = TRUE
    ORDER BY sold_date DESC NULLS LAST, id DESC
    LIMIT %s
"""


EXTERNAL_SQL_MAP: dict[str, str] = {
    "ebay": EXTERNAL_EBAY_SQL,
    "goldin": EXTERNAL_GOLDIN_SQL,
    "fanatics": EXTERNAL_FANATICS_SQL,
    "heritage": EXTERNAL_HERITAGE_SQL,
    "pwcc": EXTERNAL_PWCC_SQL,
}


def external_sql(source: str, limit: int | None = None, search: str | None = None, page: int | None = None) -> tuple[str, tuple[Any, ...]]:
    if source not in EXTERNAL_SQL_MAP:
        raise ValueError(f"unknown external source: {source}")
    return _external_source_sql(source, limit=limit, search=search, page=page)


# Per-source counts (UNION ALL) — matches the safe-filter predicates above
EXTERNAL_COUNTS_SQL = """
    SELECT source_code, COUNT(*) AS n
    FROM external_transactions
    WHERE source_code IN ('ebay', 'goldin', 'fanatics', 'heritage', 'pwcc')
      AND verified_price_eligible = TRUE
      AND (source_code <> 'ebay' OR best_offer = FALSE)
    GROUP BY source_code
"""


# ============================================================================
# Whatnot per-tab counts (unchanged)
# ============================================================================
QUEUE_COUNTS_SQL = """
    SELECT 'identified_view' AS name, COUNT(*) AS n
    FROM identified_sales_current
    UNION ALL
    SELECT 'working' AS name, COUNT(*) AS n
    FROM operational_pending_sales
    WHERE trust_bucket IN ('FLAG_REVIEW','RAW_ONLY','TRUSTED_CANDIDATE','REVIEW_HIGH_VALUE')
      AND source_key NOT IN (
        SELECT DISTINCT source_key FROM review_decision_events
        WHERE decision IN ('rejected_from_public_path','hidden_from_work_queue')
      )
    UNION ALL
    SELECT 'high_value', COUNT(*)
    FROM operational_pending_sales
    WHERE COALESCE(sold_price,0) >= 100
    UNION ALL
    SELECT 'proof_review', COUNT(*)
    FROM operational_pending_sales
    WHERE (raw ->> 'proof_binding_status') IN (
        'unknown_unreadable','mismatch_neighboring_auction','multiple_auction_numbers'
    )
       OR pending_reasons ?| ARRAY[
           'INTERSTITIAL_CARRY_FORWARD',
           'CDN_ONLY',
           'BACK_ONLY',
           'ROLE_WARNING',
           'SELLER_SUSPECT'
       ]
       OR COALESCE(image_front, '') = ''
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%sale_frame_rescue%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%interstitial%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%auction-mismatch%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%binding-mismatch%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%missing-front%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%multi-card%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%popup%'
       OR lower(COALESCE(raw->>'_review_reason', '')) LIKE '%not-card%'
    UNION ALL
    SELECT 'needs_identity', COUNT(*)
    FROM operational_pending_sales
    WHERE COALESCE(player,'') = ''
       OR COALESCE(title,'') = ''
       OR lower(COALESCE(title,'')) = 'whatnot live capture'
    UNION ALL
    SELECT 'needs_better_image', COUNT(*)
    FROM operational_pending_sales
    WHERE COALESCE(raw ->> 'front_image_status', '') IN ('stream_still','missing','unknown','interstitial')
       OR (raw ->> 'back_image_status') = 'missing'
       OR (pending_reasons ? 'FRONT_IMAGE_MISSING')
       OR (pending_reasons ? 'FRONT_IMAGE_STREAM_STILL')
       OR (pending_reasons ? 'CDN_ONLY')
    UNION ALL
    SELECT 'capture_review', COUNT(*)
    FROM operational_pending_sales
    WHERE pending_reasons ? 'CAPTURE_REVIEW'
    UNION ALL
    SELECT 'interstitial_carry_forward', COUNT(*)
    FROM operational_pending_sales
    WHERE pending_reasons ? 'INTERSTITIAL_CARRY_FORWARD'
      AND (
        COALESCE(raw ->> 'capture_class', '') = 'unsubstantiated'
        OR COALESCE(raw ->> 'auction_number_match', '') = 'no_overlay_number'
      )
    UNION ALL
    SELECT 'chrome_advanced', COUNT(*)
    FROM (
        SELECT DISTINCT ON (source_key) source_key, decision
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    ) latest
    WHERE latest.decision = 'chrome_advanced_to_human_review'
    UNION ALL
    SELECT 'human_review_ai_approved', COUNT(*)
    FROM (
        SELECT DISTINCT ON (source_key) source_key, decision
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    ) latest
    WHERE latest.decision = 'human_confirmed_for_final_gate'
    UNION ALL
    SELECT 'mazified', COUNT(*)
    FROM (
        SELECT DISTINCT ON (source_key) source_key, decision
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    ) latest
    WHERE latest.decision = 'mazified'
    UNION ALL
    SELECT 'flagged_review', COUNT(*)
    FROM operational_pending_sales
    WHERE jsonb_typeof(raw -> 'risk_flags') = 'array'
      AND jsonb_array_length(raw -> 'risk_flags') > 0
    UNION ALL
    SELECT 'rejected_hidden', COUNT(*)
    FROM (
        SELECT DISTINCT ON (source_key) source_key, decision
        FROM review_decision_events
        ORDER BY source_key, created_at DESC
    ) latest
    WHERE latest.decision IN ('rejected_from_public_path','hidden_from_work_queue')
    UNION ALL
    SELECT 'trusted_view', COUNT(*) FROM stage1_trusted_sales_current
"""
