"""Stage 1 Identified -> Trusted promotion plumbing.

This module is intentionally scoped: it only promotes rows already present in
identified_sales_current, and only into the private Trusted workbench state.
It does not touch public, Mazified, bot, or review-decision policy outside the
Confirm path.
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from safety import best_image_url

ROOT = Path("/Users/stavrosaim4/whatnot-sniper-m4")
SSD_ROOT = Path("/Volumes/MAZI_4TB_SSD/m4_archive/whatnot-sniper-m4")
M4_VENV_PYTHON = ROOT / "venv/bin/python"
IMAGE_ROOTS = [
    Path("/Users/stavrosaim4/m4_live/whatnot_cards"),
    ROOT / "ops/hot_runtime/whatnot_cards",
    ROOT / "whatnot_cards",
    Path("/Volumes/MAZI_4TB_SSD/m4_live/whatnot_cards"),
    Path("/Volumes/MAZI_4TB_SSD/m4_live/whatnot-sniper-m4/whatnot_cards"),
    SSD_ROOT / "whatnot_cards",
]

FIELD_COLUMNS = [
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
    "raw",
]


def _row_dict(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def _resolve_local_image(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    raw = str(path_value).strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        raw = parsed.path or ""
    if raw.startswith("/proxy/image/"):
        raw = raw.removeprefix("/proxy/image/")
    if "whatnot_cards/" in raw:
        tail = raw.split("whatnot_cards/", 1)[1].lstrip("/")
        for base in IMAGE_ROOTS:
            candidate = base / tail
            if candidate.exists() and candidate.is_file():
                return candidate
    p = Path(raw)
    candidates = [p] if p.is_absolute() else [ROOT / raw, SSD_ROOT / raw]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _logical_path(resolved: Path) -> str:
    for base in (ROOT, SSD_ROOT):
        try:
            return str(resolved.relative_to(base))
        except ValueError:
            pass
    return str(resolved)


def _looks_watermarked(value: Any) -> bool:
    return "_mazi." in str(value or "").lower()


# --------------------------------------------------------------------------
# Relation-agnostic Identified|Pending -> Trusted promotion gate.
#
# The promotion route enforces membership/eligibility *downstream* in this
# module (the Flask route only checks scope), so this gate is the
# authoritative server-side guard for a Pending->Trusted promotion. It runs
# on a RAW DB row (SELECT * from operational_pending_sales), NOT an
# API-enriched row, so every predicate reads real DB columns / raw JSON.
#
# Q1 trust model (operator, 2026-06-30): private Trusted = watermark
# (auction-number binding) + single foremost card + Gemini identity + human
# visual click. Cert/proof/independent re-read are the PUBLIC/Mazified path
# and are intentionally OUT of scope here (so BINDING_REVIEW / evidence_pending
# do NOT block — the front watermark is the binding).
# --------------------------------------------------------------------------
_PROMOTION_SOURCE_RELATIONS = frozenset({
    "identified_sales_current",
    "operational_pending_sales",
})

_PENDING_BANNED_SELLERS = frozenset({"kksportscards", "collectiblescloset", "tripp_cards"})
# §3.1 hard-blocker label sets (mirror decisions.py _row_capture_hard_blocker /
# _row_duplicate_or_collision so the advisory drawer gate and this authoritative
# gate agree). Verified present in operational_pending_sales.pending_reasons.
_PENDING_CAPTURE_HARD_BLOCKER_LABELS = frozenset({
    "FILENAME_COLLISION_RISK", "NO_IMAGE", "FRONT_IMAGE_MISSING",
    "CDN_ONLY", "INTERSTITIAL_CARRY_FORWARD",
})
_PENDING_DUPLICATE_LABELS = frozenset({"FILENAME_COLLISION_RISK", "DUPLICATE", "DUPLICATE_SOURCE"})
# proof bound to a DIFFERENT sale/auction -> capture is confused about which
# sale this is; block even though the front is watermarked.
_PENDING_PROOF_DIFFERENT_SALE_PBS = frozenset({"mismatch_neighboring_auction", "multiple_auction_numbers"})
_PENDING_PROOF_DIFFERENT_SALE_PMF = frozenset({"mismatch", "different_auction", "neighboring_auction"})
# Generic capture placeholders the Whatnot ingest writes into identity columns
# (verified live: card_name='Whatnot Live Capture' on 53.6k pending rows, plus a
# few 'WHATNOT'/'LIVE' noise values). These are NOT real identity, so a row whose
# only "identity" is one of these must fail the identity gate (pending_missing_identity).
_GENERIC_IDENTITY_VALUES = frozenset({
    "whatnot live capture", "whatnot", "live capture", "unidentified", "unknown",
})


def _already_watermarked(row: dict[str, Any], raw: dict[str, Any]) -> bool:
    """True iff the front carries an auction-number watermark (any provenance).

    Single shared definition used by BOTH the promotion stamp block and the
    pending gate so they can never disagree.
    """
    evidence = (raw.get("evidence_stamp") or {}) if isinstance(raw.get("evidence_stamp"), dict) else {}
    capture = (raw.get("capture_quality") or {}) if isinstance(raw.get("capture_quality"), dict) else {}
    return (
        _looks_watermarked(row.get("image_front"))
        or _looks_watermarked(row.get("image_front_neon_url"))
        or _looks_watermarked(raw.get("front_image_stamped"))
        or _looks_watermarked(evidence.get("front_stamped_image"))
        or str(capture.get("front_stamped", "")).lower() == "true"
    )


def _pending_reason_set(row: dict[str, Any]) -> set[str]:
    pend = row.get("pending_reasons") or []
    if not isinstance(pend, list):
        return set()
    return {str(p).strip().upper() for p in pend if str(p).strip()}


def _row_has_identity(row: dict[str, Any]) -> bool:
    """Mirror decisions._row_has_gemini_or_image_identity on a raw DB row.

    A generic capture placeholder (card_name='Whatnot Live Capture', etc.) is
    NOT real identity. The Whatnot ingest writes that placeholder into card_name
    AND title, so we must reject it in EVERY identity column, not just title --
    otherwise an identity-less watermarked pending row wrongly passes the gate.
    """
    for key in ("card_name", "player", "brand", "set_name", "title"):
        v = str(row.get(key) or "").strip().lower()
        if v and v not in _GENERIC_IDENTITY_VALUES:
            return True
    return False


def _pending_single_card_ok(row: dict[str, Any], raw: dict[str, Any]) -> bool:
    """One foremost card. Blocks on an explicit vision multi-card label or
    api_scan.card_count >= 2. Unknown count (the majority) -> human eyeballs."""
    for p in (row.get("pending_reasons") or []):
        s = str(p).lower()
        if "multi-card" in s or "multi_card" in s:
            return False
    api = raw.get("api_scan") if isinstance(raw.get("api_scan"), dict) else {}
    cc = api.get("card_count")
    if cc is None or str(cc).strip() == "":
        return True
    try:
        return int(str(cc).strip()) <= 1
    except (TypeError, ValueError):
        return True


def _pending_trusted_gate_reasons(row: dict[str, Any]) -> list[str]:
    """Authoritative Pending->Trusted gate. Empty list == eligible.

    Pure function over a raw DB row; unit-tested with pos+neg cases.
    """
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    reasons: list[str] = []
    if not _already_watermarked(row, raw):
        reasons.append("pending_not_watermarked")
    if not _pending_single_card_ok(row, raw):
        reasons.append("pending_multi_card")
    pset = _pending_reason_set(row)
    if not _row_has_identity(row) or "UNIDENTIFIED_WITH_IMAGE" in pset:
        reasons.append("pending_missing_identity")
    if pset & _PENDING_CAPTURE_HARD_BLOCKER_LABELS:
        reasons.append("pending_capture_hard_blocker")
    if pset & _PENDING_DUPLICATE_LABELS:
        reasons.append("pending_duplicate_or_collision")
    if str(row.get("seller") or "").strip().lower() in _PENDING_BANNED_SELLERS:
        reasons.append("pending_banned_seller")
    pbs = str(raw.get("proof_binding_status") or "").strip().lower()
    pmf = str(raw.get("proof_match_to_front") or "").strip().lower()
    if pbs in _PENDING_PROOF_DIFFERENT_SALE_PBS or pmf in _PENDING_PROOF_DIFFERENT_SALE_PMF:
        reasons.append("pending_proof_different_sale")
    return reasons


_TRUSTED_GATE_NON_SINGLE_TYPES = {
    "bundle", "pack", "nuke", "break", "supplies", "giveaway",
    "multi-card", "multi_card", "lot", "mystery",
}


def _trusted_gate_block_reasons(row: dict[str, Any]) -> list[str]:
    """Hard-BLOCK conditions for ANY ->Trusted promotion (added 2026-07-05).

    Mirrors the BLOCK dimensions of whatnot-sniper-m4/mazi_db/scripts/trusted_gate.py
    (single-card + auction binding): definitional non-singles and binding mismatches
    must never enter Trusted from any path, manual APPROVE included. HOLD-level
    signals (front class, missing watermark read-back, blank year) are intentionally
    NOT enforced here — the reviewing human, or the auto-promote evaluator's full
    gate, owns those. Pure function; empty list == eligible.
    """
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    api = raw.get("api_scan") if isinstance(raw.get("api_scan"), dict) else {}
    stamp = raw.get("evidence_stamp") if isinstance(raw.get("evidence_stamp"), dict) else {}
    reasons: list[str] = []
    auction_type = str(raw.get("auction_type") or "").strip().lower()
    if auction_type in _TRUSTED_GATE_NON_SINGLE_TYPES:
        reasons.append(f"gate_non_single_auction_type_{auction_type}")
    card_count = api.get("card_count")
    try:
        cci = int(card_count) if card_count not in (None, "") else None
    except (TypeError, ValueError):
        cci = None
    if cci is not None and cci >= 2:
        reasons.append(f"gate_multi_card_count_{cci}")
    wmv = str(api.get("mazi_watermark_verification") or "").strip().lower()
    if wmv == "mismatch":
        reasons.append("gate_watermark_mismatch")
    expected = stamp.get("expected_auction_number")
    row_auction = str(row.get("auction_number") or "").strip().lower()
    if (expected is not None and str(expected).strip() != "" and row_auction != ""
            and str(expected).strip().lower() != row_auction):
        reasons.append("gate_binding_auction_mismatch")
    return reasons


def _stamped_path_for(source_path: str | Path) -> str:
    src = Path(source_path)
    if src.stem.endswith("_mazi"):
        return str(src)
    return str(src.with_name(f"{src.stem}_mazi{src.suffix or '.jpg'}"))


def _stamp_with_m4_venv(source_path: Path, output_path: Path, auction_number: Any, comp_id: str | None) -> dict[str, Any]:
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, '/Users/stavrosaim4/whatnot-sniper-m4')
from mazi_watermark import stamp_image
source, output, auction, comp_id = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] or None
meta = stamp_image(Path(source), Path(output), auction, comp_id=comp_id, side='front')
print(json.dumps(meta, default=str))
"""
    raw = subprocess.check_output(
        [str(M4_VENV_PYTHON), "-c", script, str(source_path), str(output_path), str(auction_number or ""), str(comp_id or "")],
        text=True,
    )
    return json.loads(raw)


def _display_url_reachable(image_ref: Any) -> tuple[bool, str, int | None]:
    url = best_image_url(image_ref)
    if not url:
        return False, "", None
    if str(url).startswith("/proxy/image/"):
        if _resolve_local_image(str(url)):
            return True, url, 200
        absolute_url = f"http://100.111.48.86:8504{url}"
        try:
            with urllib.request.urlopen(absolute_url, timeout=3) as resp:
                status = int(getattr(resp, "status", 200))
                return 200 <= status < 400, url, status
        except urllib.error.HTTPError as exc:
            return False, url, int(exc.code)
        except Exception:
            return False, url, None
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                status = int(getattr(resp, "status", 200))
                if 200 <= status < 400:
                    return True, url, status
                return False, url, status
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in {405, 501}:
                continue
            return False, url, int(exc.code)
        except Exception:
            return False, url, None
    return False, url, None


def _require_displayable_front(row: dict[str, Any]) -> dict[str, Any]:
    image_ref = (
        row.get("image_front")
        or row.get("image_front_neon_url")
        or ((row.get("raw") or {}).get("front_image_stamped") if isinstance(row.get("raw"), dict) else None)
    )
    ok, url, status = _display_url_reachable(image_ref)
    if not ok:
        raise ValueError(f"cannot_promote_front_image_not_displayable:{status or 'no_response'}")
    return {
        "display_image_url": url,
        "display_image_status": status,
    }


def ensure_stage1_promotion_schema(conn_obj: Any) -> None:
    cur = conn_obj.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stage1_trusted_promotion_rows (
          promotion_id BIGSERIAL PRIMARY KEY,
          promoted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          promoted_by TEXT NOT NULL,
          promotion_decision_event_id BIGINT,
          promotion_reason TEXT NOT NULL,
          binding_proof JSONB NOT NULL DEFAULT '{}'::jsonb,
          source_key TEXT UNIQUE NOT NULL,
          observed_in TEXT,
          comp_id TEXT,
          review_id TEXT,
          trust_bucket TEXT,
          trust_score NUMERIC,
          risk_level TEXT,
          source_file TEXT,
          auction_number INTEGER,
          seller TEXT,
          sold_price NUMERIC,
          feed_generated_at TIMESTAMPTZ,
          first_seen_at TIMESTAMPTZ,
          last_seen_at TIMESTAMPTZ,
          feed_count INTEGER,
          feed_card_id TEXT,
          title TEXT,
          card_name TEXT,
          player TEXT,
          brand TEXT,
          set_name TEXT,
          variant TEXT,
          year TEXT,
          grade TEXT,
          grade_chip TEXT,
          condition TEXT,
          category TEXT,
          sport TEXT,
          image_front_neon_url TEXT,
          image_back_neon_url TEXT,
          image_front TEXT,
          image_back TEXT,
          last_sale_date TEXT,
          pending_reasons JSONB,
          warnings JSONB,
          review_decision TEXT,
          review_notes TEXT,
          reviewed_by TEXT,
          reviewed_at TIMESTAMPTZ,
          raw JSONB
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS stage1_trusted_promotion_rows_promoted_at_idx "
        "ON stage1_trusted_promotion_rows (promoted_at DESC)"
    )
    conn_obj.commit()


def refresh_stage1_views(conn_obj: Any) -> None:
    cur = conn_obj.cursor()
    cur.execute("DROP VIEW IF EXISTS identified_sales_current")
    cur.execute("DROP VIEW IF EXISTS stage1_trusted_sales_current")
    cur.execute(
        """
        CREATE VIEW stage1_trusted_sales_current AS
        SELECT
            NULL::bigint AS feed_observation_id,
            source_key, 'trusted'::text AS observed_in, comp_id, review_id, trust_bucket, trust_score, risk_level,
            source_file, auction_number, seller, sold_price, feed_generated_at, first_seen_at,
            last_seen_at, feed_count, feed_card_id, title, card_name, player, brand, set_name,
            variant, year, grade, grade_chip, condition, category, sport, image_front_neon_url,
            image_back_neon_url, image_front, image_back, last_sale_date, pending_reasons,
            warnings, review_decision, review_notes, reviewed_by, reviewed_at, raw
        FROM stage1_trusted_historical_resweep_rows
        WHERE stage1_state = 'trusted'
        UNION ALL
        SELECT
            NULL::bigint AS feed_observation_id,
            source_key, 'trusted'::text AS observed_in, comp_id, review_id, trust_bucket, trust_score, risk_level,
            source_file, auction_number, seller, sold_price, feed_generated_at, first_seen_at,
            last_seen_at, feed_count, feed_card_id, title, card_name, player, brand, set_name,
            variant, year, grade, grade_chip, condition, category, sport, image_front_neon_url,
            image_back_neon_url, image_front, image_back, last_sale_date, pending_reasons,
            warnings, review_decision, review_notes, reviewed_by, reviewed_at, raw
        FROM stage1_trusted_promotion_rows
        """
    )
    cur.execute(
        """
        CREATE VIEW identified_sales_current AS
        SELECT
            NULL::bigint AS feed_observation_id,
            i.source_key, i.observed_in, i.comp_id, i.review_id, i.trust_bucket, i.trust_score,
            i.risk_level, i.source_file, i.auction_number, i.seller, i.sold_price,
            i.feed_generated_at, i.first_seen_at, i.last_seen_at, i.feed_count, i.feed_card_id,
            i.title, i.card_name, i.player, i.brand, i.set_name, i.variant, i.year, i.grade,
            i.grade_chip, i.condition, i.category, i.sport, i.image_front_neon_url,
            i.image_back_neon_url, i.image_front, i.image_back, i.last_sale_date,
            i.pending_reasons, i.warnings, i.review_decision, i.review_notes, i.reviewed_by,
            i.reviewed_at, i.raw
        FROM identified_sweep_rows i
        WHERE NOT EXISTS (
            SELECT 1 FROM stage1_trusted_promotion_rows p
            WHERE p.source_key = i.source_key
        )
        UNION ALL
        SELECT
            NULL::bigint AS feed_observation_id,
            h.source_key, 'identified'::text AS observed_in, h.comp_id, h.review_id, h.trust_bucket, h.trust_score,
            h.risk_level, h.source_file, h.auction_number, h.seller, h.sold_price,
            h.feed_generated_at, h.first_seen_at, h.last_seen_at, h.feed_count, h.feed_card_id,
            h.title, h.card_name, h.player, h.brand, h.set_name, h.variant, h.year, h.grade,
            h.grade_chip, h.condition, h.category, h.sport, h.image_front_neon_url,
            h.image_back_neon_url, h.image_front, h.image_back, h.last_sale_date,
            h.pending_reasons, h.warnings, h.review_decision, h.review_notes, h.reviewed_by,
            h.reviewed_at, h.raw
        FROM stage1_trusted_historical_resweep_rows h
        WHERE h.stage1_state = 'identified'
          AND NOT EXISTS (
            SELECT 1 FROM stage1_trusted_promotion_rows p
            WHERE p.source_key = h.source_key
          )
        """
    )
    conn_obj.commit()


def watermark_inventory(conn_obj: Any) -> dict[str, int]:
    ensure_stage1_promotion_schema(conn_obj)
    cur = conn_obj.cursor()
    cur.execute(
        """
        SELECT
          COUNT(*)::int AS total_identified,
          COUNT(*) FILTER (
            WHERE COALESCE(image_front, '') ILIKE '%%_mazi.%%'
               OR COALESCE(image_front_neon_url, '') ILIKE '%%_mazi.%%'
               OR COALESCE(raw->>'front_image_stamped', '') ILIKE '%%_mazi.%%'
               OR COALESCE(raw#>>'{evidence_stamp,front_stamped_image}', '') ILIKE '%%_mazi.%%'
               OR COALESCE(raw#>>'{capture_quality,front_stamped}', '') = 'true'
          )::int AS already_watermarked,
          COUNT(*) FILTER (
            WHERE NOT (
              COALESCE(image_front, '') ILIKE '%%_mazi.%%'
              OR COALESCE(image_front_neon_url, '') ILIKE '%%_mazi.%%'
              OR COALESCE(raw->>'front_image_stamped', '') ILIKE '%%_mazi.%%'
              OR COALESCE(raw#>>'{evidence_stamp,front_stamped_image}', '') ILIKE '%%_mazi.%%'
              OR COALESCE(raw#>>'{capture_quality,front_stamped}', '') = 'true'
            )
          )::int AS needs_stamping
        FROM identified_sales_current
        """
    )
    row = cur.fetchone()
    return dict(zip([d.name for d in cur.description], row))


def promote_to_trusted(
    conn_obj: Any,
    payload: dict[str, Any],
    *,
    source_relation: str = "identified_sales_current",
    observed_in_label: str = "identified",
    enforce_pending_gate: bool = False,
) -> dict[str, Any]:
    """Relation-agnostic Identified|Pending -> Trusted promotion.

    `source_relation` is validated against the `_PROMOTION_SOURCE_RELATIONS`
    allowlist before it is interpolated into the FROM clause, so the f-string
    below carries no injection surface. `enforce_pending_gate` runs the
    authoritative Pending->Trusted gate (`_pending_trusted_gate_reasons`); it is
    False for the Identified path, which keeps that path byte-identical.
    """
    if source_relation not in _PROMOTION_SOURCE_RELATIONS:
        raise ValueError(f"unsupported_source_relation:{source_relation}")
    ensure_stage1_promotion_schema(conn_obj)
    cur = conn_obj.cursor()
    source_key = payload.get("source_key")
    comp_id = payload.get("comp_id")
    def _field_value(name: str) -> Any:
        value = row.get(name)
        if name in {"pending_reasons", "warnings", "raw"}:
            return Jsonb(value if value is not None else ([] if name != "raw" else {}))
        return value

    cur.execute(
        f"""
        SELECT *
        FROM {source_relation}
        WHERE source_key = %s AND (%s::text IS NULL OR comp_id = %s)
        LIMIT 1
        """,
        (source_key, comp_id, comp_id),
    )
    row = _row_dict(cur)
    if row is None:
        raise ValueError(f"not_in_{observed_in_label}_state")
    if not row.get("image_front") and not row.get("image_front_neon_url"):
        raise ValueError("cannot_promote_missing_front_image")
    if enforce_pending_gate:
        gate_reasons = _pending_trusted_gate_reasons(row)
        if gate_reasons:
            raise ValueError(
                "pending_trusted_gate_failed:" + ",".join(sorted(set(gate_reasons)))
            )

    # Trusted-readiness hard blocks (single-card + auction binding) apply to EVERY
    # ->Trusted path, manual APPROVE included (2026-07-05). Enforced by default;
    # MAZIDEX_TRUSTED_GATE_ENFORCE=0/false/off reverts to log-only, in which case
    # the block reasons are still recorded on the binding payload below.
    trusted_gate_blocks = _trusted_gate_block_reasons(row)
    trusted_gate_enforced = (
        str(os.environ.get("MAZIDEX_TRUSTED_GATE_ENFORCE", "1")).strip().lower()
        not in ("0", "false", "off")
    )
    if trusted_gate_blocks and trusted_gate_enforced:
        raise ValueError(
            "trusted_gate_blocked:" + ",".join(sorted(set(trusted_gate_blocks)))
        )

    raw = dict(row.get("raw") or {})
    binding: dict[str, Any] = {
        "schema": f"stage1_{observed_in_label}_to_trusted_binding_v1",
        "auction_number": row.get("auction_number"),
        "visual_confirm_required": True,
        "visual_confirm_source": payload.get("reviewer") or "mazidex-admin",
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    if trusted_gate_blocks:
        # log-only mode: surface the would-block verdict on the stored binding
        binding["trusted_gate_block_reasons"] = sorted(set(trusted_gate_blocks))
        binding["trusted_gate_enforced"] = trusted_gate_enforced

    already_watermarked = _already_watermarked(row, raw)

    if already_watermarked:
        binding.update({
            "binding_status": "satisfied_existing_watermark",
            "watermark_action": "recognized_existing",
            "watermark_checked_against_row_auction": row.get("auction_number"),
        })
    else:
        resolved = _resolve_local_image(row.get("image_front"))
        if resolved is None:
            raise ValueError("cannot_stamp_unresolved_local_front_image")
        stamped_abs = Path(_stamped_path_for(resolved))
        stamp_meta = _stamp_with_m4_venv(resolved, stamped_abs, row.get("auction_number"), row.get("comp_id"))
        logical = _logical_path(stamped_abs)
        row["image_front"] = logical
        binding.update({
            "binding_status": "satisfied_new_deterministic_watermark",
            "watermark_action": "stamped_at_promotion",
            "watermark_checked_against_row_auction": row.get("auction_number"),
            "stamp_meta": stamp_meta,
        })

    binding.update(_require_displayable_front(row))

    raw["stage1_trust_promotion"] = binding
    raw["proof_binding_status"] = "watermark_bound"
    row["raw"] = raw
    row["observed_in"] = "trusted"
    row["trust_bucket"] = "trusted"
    row["review_decision"] = "confirm"
    row["review_notes"] = payload.get("notes") or f"{observed_in_label.capitalize()}->Trusted visual Confirm; watermark-bound."
    row["reviewed_by"] = payload.get("reviewer") or "mazidex-admin"
    row["reviewed_at"] = datetime.now(timezone.utc)
    row["pending_reasons"] = []
    row["warnings"] = row.get("warnings") or []

    cur.execute(
        """
        INSERT INTO review_decision_events
            (source_key, observed_in, review_id, comp_id, source_file,
             auction_number, decision, notes, reviewer, row_meta)
        VALUES (%s, %s, %s, %s, %s, %s, 'confirm', %s, %s, %s)
        RETURNING id, created_at
        """,
        (
            row.get("source_key"),
            observed_in_label,
            row.get("review_id"),
            row.get("comp_id"),
            row.get("source_file"),
            row.get("auction_number"),
            row.get("review_notes"),
            row.get("reviewed_by"),
            Jsonb(payload.get("row_meta") or {}),
        ),
    )
    event_id, event_at = cur.fetchone()

    cols_sql = ", ".join(FIELD_COLUMNS)
    placeholders = ", ".join(["%s"] * len(FIELD_COLUMNS))
    update_sql = ", ".join([f"{c}=EXCLUDED.{c}" for c in FIELD_COLUMNS if c != "source_key"])
    cur.execute(
        f"""
        INSERT INTO stage1_trusted_promotion_rows (
          promoted_by, promotion_decision_event_id, promotion_reason, binding_proof,
          {cols_sql}
        )
        VALUES (%s, %s, %s, %s, {placeholders})
        ON CONFLICT (source_key) DO UPDATE SET
          promoted_at=now(),
          promoted_by=EXCLUDED.promoted_by,
          promotion_decision_event_id=EXCLUDED.promotion_decision_event_id,
          promotion_reason=EXCLUDED.promotion_reason,
          binding_proof=EXCLUDED.binding_proof,
          {update_sql}
        RETURNING promotion_id, promoted_at
        """,
        (
            row["reviewed_by"],
            event_id,
            "visual_confirm_watermark_bound",
            Jsonb(binding),
            *[_field_value(c) for c in FIELD_COLUMNS],
        ),
    )
    promotion_id, promoted_at = cur.fetchone()
    conn_obj.commit()
    refresh_stage1_views(conn_obj)
    return {
        "event_id": int(event_id),
        "event_created_at": str(event_at),
        "promotion_id": int(promotion_id),
        "promoted_at": str(promoted_at),
        "decision": "confirm",
        "promotion": f"{observed_in_label}_to_trusted",
        "source_key": row.get("source_key"),
        "comp_id": row.get("comp_id"),
        "binding_proof": binding,
    }


def promote_identified_to_trusted(conn_obj: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Identified -> Trusted (back-compat wrapper; behavior byte-identical)."""
    return promote_to_trusted(conn_obj, payload)


def promote_pending_to_trusted(conn_obj: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Pending -> Trusted. Enforces the authoritative watermark + single-card +
    identity gate server-side (`_pending_trusted_gate_reasons`); the route does
    NOT pre-check membership, so this self-gate is the durable guard."""
    return promote_to_trusted(
        conn_obj,
        payload,
        source_relation="operational_pending_sales",
        observed_in_label="pending",
        enforce_pending_gate=True,
    )
