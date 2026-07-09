"""mazidex-admin v0 — FastAPI app.

Private Review Workbench backed by Neon/Postgres.
Bind: 100.111.48.86:8504
Write endpoint DISABLED at v0 until operator approval.
"""
from __future__ import annotations

import os
import json
import math
import mimetypes
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

# Ensure mazi_db symlink + venv are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/Users/stavrosaim4/whatnot-sniper-m4")
os.environ.setdefault("MAZI_DB_NO_POOL", "1")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


def _json(content: Any, status_code: int = 200) -> JSONResponse:
    """JSONResponse that runs FastAPI's jsonable_encoder on the payload.

    Without this, Decimal/datetime objects from psycopg crash json.dumps.
    The original endpoints returned dicts so FastAPI did this conversion
    automatically; once we switched to JSONResponse we have to invoke it
    explicitly to preserve backwards compatibility with acceptance tests.
    """
    return JSONResponse(status_code=status_code, content=jsonable_encoder(content))

from config import (
    BIND_HOST,
    BIND_PORT,
    DEFAULT_LIMIT,
    LEGACY_9008_IMAGE_PROXY_BASE,
    MAX_LIMIT,
    VALID_DECISIONS,
    review_write_scope_source_keys,
    review_write_scope_test_run_id,
    review_write_scope_identified_all,
    review_write_enabled,
    row_actions_write_enabled,
)
from decisions import (
    DB_VALID_DECISIONS,
    is_db_supported_decision,
    mazified_blockers,
    mazified_button_state,
    resolve_preferred_row,
    row_allowed_decisions,
    row_hard_block_labels,
    source_drift_meta,
    unmapped_decision_reason,
    validate_decision_payload,
    write_decision,
)
from promotion import (
    promote_identified_to_trusted,
    promote_pending_to_trusted,
    refresh_stage1_views,
    watermark_inventory,
)
from row_actions import (
    append_delete_record,
    build_delete_record,
    pick_deletable_row,
    soft_delete_payload,
)
from queues import (
    DECISIONS_SQL,
    EXTERNAL_COUNTS_SQL,
    EXTERNAL_SQL_MAP,
    QUEUE_COUNTS_SQL,
    QUEUE_MAP,
    ROW_DETAIL_SQL,
    ROW_BY_SOURCE_KEY_SQL,
    SAFETY_FIELD_NAMES,
    external_sql,
    queue_sql,
)
from safety import (
    SAFETY_BADGE_FIELDS,
    best_image_url,
    deep_scrub,
    derive_chips,
    derive_safety_badges,
    extract_basename,
    privacy_scan_payload,
)

# Advisory-audio bridge: Neon carries no audio, so attach the monolith-derived
# transcript excerpt + audio identity onto the drawer row by comp_id (read-only,
# advisory-only, never promotes trust). See audio_evidence_sidecar.py.
from audio_evidence_sidecar import attach_audio as attach_audio_evidence

# Neon connection (read-only by default)
from mazi_db.connection import conn as neon_conn
from mazi_db.normalization.canonical_card_identity import (
    CANONICAL_FIELDS,
    MISSING as CANONICAL_MISSING,
    canonical_value,
    normalize_card_identity,
)

APP_VERSION = "mazidex-admin-v0.0.3"
APP_TITLE = "MaziDex Review Workbench (private)"
STARTED_AT = datetime.now(timezone.utc).isoformat()
PT = ZoneInfo("America/Los_Angeles")
WHATNOT_DIR = Path.home() / "whatnot-sniper-m4"
LIVE_AUCTION_LEDGER_PATH = WHATNOT_DIR / "ops" / "whatnot_auctions_live_append.jsonl"
_PRICE_AUDIT_CACHE_LOCK = Lock()
_PRICE_AUDIT_CACHE: dict[str, Any] = {"key": None, "payload": None, "expires_at": 0.0}
QUEUE_COUNTS_CACHE_TTL_SECONDS = 30.0
_QUEUE_COUNTS_CACHE_LOCK = Lock()
_QUEUE_COUNTS_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "payload": None,
}
IMAGE_PROXY_TIMEOUT_SECONDS = float(os.environ.get("MAZI_8504_IMAGE_PROXY_TIMEOUT_SECONDS", "10"))
IMAGE_PROXY_CACHE_TTL_SECONDS = float(os.environ.get("MAZI_8504_IMAGE_PROXY_CACHE_TTL_SECONDS", "300"))
IMAGE_PROXY_CACHE_MAX_ITEMS = int(os.environ.get("MAZI_8504_IMAGE_PROXY_CACHE_MAX_ITEMS", "512"))
IMAGE_PROXY_CACHE_MAX_BYTES = int(os.environ.get("MAZI_8504_IMAGE_PROXY_CACHE_MAX_BYTES", str(64 * 1024 * 1024)))
IMAGE_PROXY_LOCAL_ROOT = (
    os.environ.get("MAZI_8504_IMAGE_ROOT")
    or os.environ.get("MAZI_IMAGE_LOCAL_ROOT")
    or ""
).strip()
DEFAULT_IMAGE_PROXY_LOCAL_ROOTS = (
    "/Users/stavrosaim4/whatnot-sniper-m4/ops/hot_runtime/whatnot_cards",
    "/Users/stavrosaim4/whatnot-sniper-m4/whatnot_cards",
    "/Users/stavrosaim4/m4_live/whatnot_cards",
    "/Volumes/MAZI_4TB_SSD/m4_live/whatnot_cards",
    "/Volumes/MAZI_4TB_SSD/m4_live/whatnot-sniper-m4/whatnot_cards",
    "/Volumes/MAZI_4TB_SSD/m4_archive/whatnot-sniper-m4/whatnot_cards",
)
DEFAULT_IMAGE_PROXY_LOCAL_ROOT_GLOBS = (
    "/Users/stavrosaim4/whatnot-sniper-m4-worktrees/*/ops/hot_runtime/whatnot_cards",
    "/Users/stavrosaim4/whatnot-sniper-m4*/ops/hot_runtime/whatnot_cards",
    "/Volumes/MAZI_4TB_SSD/m4_live/whatnot-sniper-m4*/ops/hot_runtime/whatnot_cards",
    "/Volumes/MAZI_4TB_SSD/m4_archive/whatnot-sniper-m4*/ops/hot_runtime/whatnot_cards",
)
_IMAGE_PROXY_CACHE_LOCK = Lock()
_IMAGE_PROXY_CACHE: dict[str, dict[str, Any]] = {}

GRADE_COMPANIES = (
    "PSA",
    "BGS",
    "SGC",
    "CGC",
    "CSG",
    "TAG",
    "ISA",
    "WCG",
    "CCG",
    "BCCG",
    "BVG",
    "BECKETT",
)
GRADE_COMPANY_ALIASES = {
    "BECKET": "BECKETT",
    "BECKETT": "BECKETT",
}
GRADE_VALUE_RE = re.compile(r"\b(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4|3|2|1)\b")

# Per-comp grade parser for the multi-grade drawer dropdown. The structured
# external_transactions.grading_company/grade columns are usually NULL for eBay
# corpus rows, and exact_card_match_audit_v1.grade_company/grade_value carry the
# WHATNOT TARGET (card) grade, identical on every comp. The only reliable source
# of a comp's own grade is its listing title, parsed here.
_COMP_GRADE_COMPANY_RE = re.compile(
    r"\b(" + "|".join(
        sorted(set(GRADE_COMPANIES) | set(GRADE_COMPANY_ALIASES), key=len, reverse=True)
    ) + r")\b",
    re.I,
)


def _parse_comp_grade(title: str) -> tuple[str, str, str]:
    """Parse a single eBay comp's grading company + value from its listing title.

    Returns (company, value, group_label). Ungraded/raw listings (no grading
    company token) return ("", "", "Raw/Ungraded"). A graded listing whose title
    omits the numeric grade returns just the company (e.g. ("PSA", "", "PSA")).
    """
    text = title or ""
    cm = _COMP_GRADE_COMPANY_RE.search(text)
    if not cm:
        return ("", "", "Raw/Ungraded")
    company = GRADE_COMPANY_ALIASES.get(cm.group(1).upper(), cm.group(1).upper())
    # Grade value must follow the company token (avoids matching years/card #s).
    tail = text[cm.end(): cm.end() + 14]
    vm = GRADE_VALUE_RE.search(tail)
    value = vm.group(1) if vm else ""
    group = (company + " " + value).strip()
    return (company, value, group)


app = FastAPI(title=APP_TITLE, version=APP_VERSION)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================================
# Audit-only price ladder badges
# ============================================================================
def _audit_file_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (int(st.st_mtime), int(st.st_size))


def _audit_num(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _audit_nested(row: dict[str, Any], *path: str) -> object:
    cur: object = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _audit_sale_key(row: dict[str, Any]) -> str:
    return (
        str(row.get("comp_id") or "")
        or str(row.get("sold_drain_row_key") or "")
        or "|".join(str(row.get(key) or "") for key in ("seller_key", "seller", "bot_pid", "auction_number", "stream_url"))
    )


def _price_audit_payload(day: str | None = None) -> dict[str, Any]:
    """Read-only PT-day ledger audit. Derives badges; writes nothing."""
    day = day or datetime.now(PT).date().isoformat()
    file_key = _audit_file_key(LIVE_AUCTION_LEDGER_PATH)
    cache_key = (day, file_key)
    now = monotonic()
    with _PRICE_AUDIT_CACHE_LOCK:
        cached = _PRICE_AUDIT_CACHE.get("payload")
        if cached and _PRICE_AUDIT_CACHE.get("key") == cache_key and now < float(_PRICE_AUDIT_CACHE.get("expires_at") or 0.0):
            return dict(cached)

    sales: dict[str, dict[str, Any]] = {}
    try:
        f = open(LIVE_AUCTION_LEDGER_PATH, "r", encoding="utf-8")
    except OSError:
        payload = {"red": 0, "amber": 0, "by_key": {}, "generated_at": datetime.now(timezone.utc).isoformat(), "error": "ledger_unavailable"}
        with _PRICE_AUDIT_CACHE_LOCK:
            _PRICE_AUDIT_CACHE.update({"key": cache_key, "payload": payload, "expires_at": now + 30.0})
        return dict(payload)

    with f:
        for line in f:
            if day not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or str(row.get("sold_date") or row.get("timestamp") or "")[:10] != day:
                continue
            price = _audit_num(row.get("sold_price") if row.get("sold_price") is not None else row.get("authoritative_sold_price") if row.get("authoritative_sold_price") is not None else row.get("price"))
            if price is None or price <= 0:
                continue
            item = sales.setdefault(_audit_sale_key(row), {
                "seller": row.get("seller_key") or row.get("seller") or "",
                "auction": row.get("auction_number") or row.get("auction"),
                "price": 0.0,
                "ladder_max": None,
                "obs": 0,
                "keys": set(),
            })
            item["price"] = max(float(item.get("price") or 0.0), price)
            ladder = _audit_num(_audit_nested(row, "capture_quality", "live_price_max_observed"))
            if ladder is None:
                ladder = _audit_num(row.get("live_price_max_observed"))
            if ladder is not None:
                item["ladder_max"] = ladder if item.get("ladder_max") is None else max(float(item["ladder_max"]), ladder)
            for candidate in (
                _audit_nested(row, "capture_quality", "active_winning_frame_count"),
                _audit_nested(row, "evidence_metadata", "active_winning_frame_count"),
                _audit_nested(row, "front_timing_shadow", "frames_seen_active_winning"),
                _audit_nested(row, "capture_quality", "front_timing_shadow", "frames_seen_active_winning"),
                _audit_nested(row, "front_timing_shadow", "status_counts", "winning"),
                _audit_nested(row, "capture_quality", "front_timing_shadow", "status_counts", "winning"),
            ):
                obs = _audit_num(candidate)
                if obs is not None:
                    item["obs"] = max(int(item.get("obs") or 0), int(obs))
            if row.get("comp_id"):
                item["keys"].add(str(row.get("comp_id")))
            seller = str(item.get("seller") or "").strip().lower()
            auction = str(item.get("auction") or "").strip()
            if seller and auction:
                item["keys"].add(f"{seller}|{auction}")

    by_key: dict[str, dict[str, Any]] = {}
    red = amber = 0
    for item in sales.values():
        price = float(item.get("price") or 0.0)
        ladder = item.get("ladder_max")
        obs = int(item.get("obs") or 0)
        ratio = price / float(ladder) if ladder else None
        is_red = bool(ladder and obs >= 1 and price > float(ladder) * 3.0)
        is_amber = bool(price > 500 and obs < 3)
        if is_red:
            red += 1
        if is_amber:
            amber += 1
        badges: list[dict[str, Any]] = []
        if is_red:
            badges.append({
                "code": "PRICE_OUTLIER",
                "severity": "red",
                "label": "PRICE_OUTLIER",
                "tooltip": f"sold ${price:,.0f} / ladder max ${float(ladder):,.0f} / ratio {ratio:.1f} / obs {obs}",
            })
        if is_amber:
            badges.append({
                "code": "LADDER_MISSING_HIGH_VALUE",
                "severity": "amber",
                "label": "LADDER_MISSING_HIGH_VALUE",
                "tooltip": f"sold ${price:,.0f} / obs {obs} / ladder evidence missing - review only",
            })
        audit = {
            "badges": badges,
            "price_outlier": is_red,
            "ladder_missing_high_value": is_amber,
            "sold_price": round(price, 2),
            "ladder_max": round(float(ladder), 2) if ladder else None,
            "ratio": round(float(ratio), 2) if ratio else None,
            "bid_observations": obs,
            "audit_only": True,
        }
        for key in item.get("keys") or []:
            by_key[str(key)] = audit

    payload = {"red": red, "amber": amber, "by_key": by_key, "generated_at": datetime.now(timezone.utc).isoformat()}
    with _PRICE_AUDIT_CACHE_LOCK:
        _PRICE_AUDIT_CACHE.update({"key": cache_key, "payload": payload, "expires_at": now + 30.0})
    return dict(payload)


def _apply_price_audit(row: dict[str, Any]) -> None:
    audit = _price_audit_payload()
    keys = []
    if row.get("comp_id"):
        keys.append(str(row.get("comp_id")))
    seller = str(row.get("seller") or "").strip().lower()
    auction = str(row.get("auction_number") or "").strip()
    if seller and auction:
        keys.append(f"{seller}|{auction}")
    by_key = audit.get("by_key") if isinstance(audit.get("by_key"), dict) else {}
    match = next((by_key.get(key) for key in keys if by_key.get(key)), None)
    row["price_audit"] = match or {
        "badges": [],
        "price_outlier": False,
        "ladder_missing_high_value": False,
        "audit_only": True,
    }
    row["price_audit_badges"] = row["price_audit"].get("badges") or []


# ============================================================================
# Server-side image proxy
# ============================================================================
def _guess_content_type(path: str, fallback: str = "image/jpeg") -> str:
    return mimetypes.guess_type(path)[0] or fallback


def _normalize_proxy_path(path: str) -> tuple[str, str]:
    """Return (source_path, tail) for a safe whatnot_cards image reference."""
    raw = str(path or "").strip().strip('"').replace("\\", "/")
    if "://" in raw:
        raw = urlparse(raw).path.lstrip("/")
    else:
        raw = raw.lstrip("/")
    if not raw:
        raise HTTPException(status_code=400, detail="missing image path")
    if "whatnot_cards/" in raw:
        tail = raw.split("whatnot_cards/", 1)[1].lstrip("/")
    else:
        tail = raw
    parts = [p for p in tail.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        raise HTTPException(status_code=400, detail="unsafe image path")
    tail = "/".join(parts)
    return f"whatnot_cards/{tail}", tail


def _cache_get(source_path: str) -> tuple[bytes, str] | None:
    now = monotonic()
    with _IMAGE_PROXY_CACHE_LOCK:
        cached = _IMAGE_PROXY_CACHE.get(source_path)
        if not cached:
            return None
        if float(cached.get("expires_at") or 0) <= now:
            _IMAGE_PROXY_CACHE.pop(source_path, None)
            return None
        return cached["body"], cached["content_type"]


def _cache_set(source_path: str, body: bytes, content_type: str) -> None:
    if not body:
        return
    with _IMAGE_PROXY_CACHE_LOCK:
        _IMAGE_PROXY_CACHE[source_path] = {
            "body": body,
            "content_type": content_type,
            "size": len(body),
            "expires_at": monotonic() + IMAGE_PROXY_CACHE_TTL_SECONDS,
        }
        while (
            len(_IMAGE_PROXY_CACHE) > IMAGE_PROXY_CACHE_MAX_ITEMS
            or sum(int(v.get("size") or 0) for v in _IMAGE_PROXY_CACHE.values()) > IMAGE_PROXY_CACHE_MAX_BYTES
        ):
            oldest_key = next(iter(_IMAGE_PROXY_CACHE), None)
            if oldest_key is None:
                break
            _IMAGE_PROXY_CACHE.pop(oldest_key, None)


def _r2_config() -> dict[str, str] | None:
    endpoint = os.environ.get("MAZI_R2_ENDPOINT") or os.environ.get("R2_ENDPOINT")
    bucket = os.environ.get("MAZI_R2_BUCKET") or os.environ.get("R2_BUCKET")
    access_key = (
        os.environ.get("MAZI_R2_ACCESS_KEY")
        or os.environ.get("MAZI_R2_ACCESS_KEY_ID")
        or os.environ.get("R2_ACCESS_KEY")
        or os.environ.get("R2_ACCESS_KEY_ID")
    )
    secret_key = (
        os.environ.get("MAZI_R2_SECRET_KEY")
        or os.environ.get("MAZI_R2_SECRET_ACCESS_KEY")
        or os.environ.get("R2_SECRET_KEY")
        or os.environ.get("R2_SECRET_ACCESS_KEY")
    )
    if not (endpoint and bucket and access_key and secret_key):
        return None
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
    }


def _fetch_r2(source_path: str, tail: str) -> tuple[bytes, str] | None:
    cfg = _r2_config()
    if not cfg:
        return None
    try:
        import boto3
        from botocore.client import Config
    except Exception:
        m4_site = Path("/Users/stavrosaim4/whatnot-sniper-m4/venv/lib/python3.12/site-packages")
        if m4_site.exists() and str(m4_site) not in sys.path:
            sys.path.append(str(m4_site))
        try:
            import boto3
            from botocore.client import Config
        except Exception:
            return None
    try:
        client = boto3.client(
            "s3",
            endpoint_url=cfg["endpoint"],
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            config=Config(signature_version="s3v4", retries={"max_attempts": 1}),
        )
        for key in (source_path, tail):
            try:
                obj = client.get_object(Bucket=cfg["bucket"], Key=key)
                body = obj["Body"].read()
                if body:
                    return body, obj.get("ContentType") or _guess_content_type(key)
            except Exception:
                continue
    except Exception:
        return None
    return None


def _legacy_9008_url(source_path: str, tail: str) -> str:
    base = LEGACY_9008_IMAGE_PROXY_BASE.rstrip("/")
    quoted_tail = quote(tail, safe="/._-")
    quoted_source = quote(source_path, safe="/._-")
    if base.endswith("/whatnot_cards"):
        return f"{base}/{quoted_tail}"
    return f"{base}/{quoted_source}"


def _fetch_9008(source_path: str, tail: str) -> tuple[bytes, str] | None:
    if not LEGACY_9008_IMAGE_PROXY_BASE:
        return None
    url = _legacy_9008_url(source_path, tail)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mazi-8504-image-proxy/1.0"})
        with urllib.request.urlopen(req, timeout=IMAGE_PROXY_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
            if not body:
                return None
            return body, resp.headers.get_content_type() or _guess_content_type(source_path)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _fetch_local(source_path: str, tail: str) -> tuple[bytes, str] | None:
    roots = []
    if IMAGE_PROXY_LOCAL_ROOT:
        roots.append(IMAGE_PROXY_LOCAL_ROOT)
    roots.extend(DEFAULT_IMAGE_PROXY_LOCAL_ROOTS)
    for pattern in DEFAULT_IMAGE_PROXY_LOCAL_ROOT_GLOBS:
        roots.extend(str(path) for path in Path("/").glob(pattern.lstrip("/")))
    seen_roots: set[str] = set()
    for root_value in roots:
        try:
            root = Path(root_value).expanduser().resolve()
        except OSError:
            continue
        root_key = str(root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        candidates = [root / tail, root / source_path]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if not (str(resolved) == root_key or str(resolved).startswith(root_key + os.sep)):
                    continue
                if resolved.is_file():
                    return resolved.read_bytes(), _guess_content_type(str(resolved))
            except OSError:
                continue
    return None


@app.get("/proxy/image/{path:path}")
def proxy_image(path: str) -> Response:
    source_path, tail = _normalize_proxy_path(path)
    cached = _cache_get(source_path)
    if cached:
        body, content_type = cached
        print(f"image_proxy source=cache path={source_path}", flush=True)
        return Response(content=body, media_type=content_type)

    for source_name, fetcher in (
        ("r2", _fetch_r2),
        ("9008", _fetch_9008),
        ("local", _fetch_local),
    ):
        fetched = fetcher(source_path, tail)
        if not fetched:
            continue
        body, content_type = fetched
        _cache_set(source_path, body, content_type)
        print(f"image_proxy source={source_name} path={source_path}", flush=True)
        return Response(content=body, media_type=content_type)

    print(f"image_proxy source=miss path={source_path}", flush=True)
    raise HTTPException(status_code=404, detail="image not found")


# Source tabs the SPA renders. Sources mapped to a real queue serve
# from that queue. Sources in EXTERNAL_SQL_MAP serve from
# external_transactions (eBay/Goldin reference comps — never Whatnot,
# never proof-bound, never Trusted, never Mazified, never Public Ready).
# Sources mapped to None remain placeholder tabs that return rows=[] /
# row_count=0 with `placeholder: true`.
SOURCE_QUEUE_MAP: dict[str, str | None] = {
    "identified":         "identified_view",
    "trusted":            "trusted_view",
    "mazified":           "mazified",
    "flagged_review":     "flagged_review",
    "working_queue":      "working",
    "ebay":               None,   # external — served by external_sql()
    "goldin":             None,   # external — served by external_sql()
    "fanatics":           None,
    "heritage":           None,
    "pwcc":               None,
    "vdex_discovery":     None,
}


# Sources that are externally sourced reference comps. The `/api/v1/source`
# endpoint short-circuits for these and reads from `external_transactions`
# instead of any Whatnot view. Per the orchestrator wiring spec these are
# READ-ONLY and tagged with REFERENCE/EXTERNAL chips in the row shape.
EXTERNAL_SOURCE_NAMES: set[str] = set(EXTERNAL_SQL_MAP.keys())  # {'ebay','goldin'}


# Filter description echoed back to the SPA so the QA can confirm
# the row set actually had the OBO/eligibility gate applied.
EXTERNAL_FILTER_SPEC: dict[str, dict[str, Any]] = {
    "ebay":   {"source_code": "ebay",   "best_offer": False, "verified_price_eligible": True},
    "goldin": {"source_code": "goldin",                       "verified_price_eligible": True},
    "fanatics": {"source_code": "fanatics",                    "verified_price_eligible": True},
    "heritage": {"source_code": "heritage",                    "verified_price_eligible": True},
    "pwcc":     {"source_code": "pwcc",                        "verified_price_eligible": True},
}


# Chip cluster shown on every external row tile. The orchestrator-mandated
# set. We never blend any Whatnot proof / trust / public chip with these.
EXTERNAL_CHIPS_BASE: list[str] = [
    "EXTERNAL COMP",
    "REFERENCE ONLY",
    "OBO EXCLUDED",
    "VERIFIED PRICE ELIGIBLE",
]


# ============================================================================
# Global JSON exception handlers — guarantee the SPA never sees an
# Internal Server Error HTML body. Every error becomes JSON.
# ============================================================================
@app.exception_handler(StarletteHTTPException)
async def _http_exception_to_json(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "detail": exc.detail, "status": exc.status_code},
    )


@app.exception_handler(RequestValidationError)
async def _validation_to_json(_req: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def _unhandled_to_json(_req: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "type": type(exc).__name__,
            "detail": str(exc)[:300],
        },
    )


# ============================================================================
# Row shaping
# ============================================================================
def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().strip('"').lower() in {"1", "true", "yes", "on"}


def _proof_only_basename(value: Any) -> bool:
    if not value:
        return False
    bn = os.path.basename(str(value).strip().strip('"')).lower()
    return bn.startswith(("review_proof_", "proof_"))


def _cdn_basename(value: Any) -> bool:
    """True if the ref is a CDN/listing image. Standing gate: CDN/listing images
    are never display or proof evidence, so they must never resolve as a front."""
    if not value:
        return False
    s = str(value).strip().strip('"').lower()
    return "/cdn_" in s or os.path.basename(s).startswith("cdn_")


def _apply_effective_publish_safety(row: dict[str, Any]) -> None:
    """Apply read-surface safety overrides without mutating source rows."""
    cert = str(row.get("mazi_cert_status") or "").strip().strip('"').lower()
    proof = str(row.get("proof_binding_status") or "").strip().strip('"').lower()
    if proof in {"unknown_unreadable", "pending_review"} and cert != "verified" and _truthy(row.get("publish_ready")):
        row["raw_publish_ready"] = row.get("publish_ready")
        row["publish_ready"] = False
        row["effective_publish_block_reason"] = "proof_pending_unverified"


_CANONICAL_PAYLOAD_KEYS = (
    "api_scan",
    "card_ocr",
    "raw_evidence",
    "identity_json",
    "latest_identity",
    "identification",
    "evidence_metadata",
    "capture_quality",
)

_CANONICAL_SCALAR_MAP = {
    "api_scan": {
        "api_scan_player": "player",
        "api_scan_name": "name",
        "api_scan_year": "year",
        "api_scan_brand": "brand",
        "api_scan_card_set": "card_set",
        "api_scan_set_name": "set_name",
        "api_scan_parallel": "parallel",
        "api_scan_variant": "variant",
        "api_scan_insert_type": "insert_type",
        "api_scan_card_number": "card_number",
        "api_scan_grade": "grade",
        "api_scan_grading_company": "grading_company",
        "api_scan_cert_number": "cert_number",
        "api_scan_graded": "graded",
        "api_scan_auto": "auto",
        "api_scan_rookie": "rookie",
        "api_scan_patch": "patch",
        "api_scan_rpa": "rpa",
        "api_scan_serial_numbered": "serial_numbered",
        "api_scan_serial_current": "serial_current",
        "api_scan_serial_total": "serial_total",
        "api_scan_team": "team",
        "api_scan_sport": "sport",
        "api_scan_auction_type": "auction_type",
        "api_scan_card_count": "card_count",
    },
    "card_ocr": {
        "card_ocr_player": "player",
        "card_ocr_year": "year",
        "card_ocr_brand": "brand",
        "card_ocr_card_set": "card_set",
        "card_ocr_set_name": "set_name",
        "card_ocr_serial_numbered": "serial_numbered",
        "card_ocr_auto": "auto",
        "card_ocr_rookie": "rookie",
        "card_ocr_patch": "patch",
        "card_ocr_team": "team",
    },
}

_CANONICAL_SCALAR_KEYS = tuple(
    alias
    for mapping in _CANONICAL_SCALAR_MAP.values()
    for alias in mapping
)

_CANONICAL_FLAT_KEYS = (
    "player",
    "card_name",
    "title",
    "year",
    "brand",
    "set_name",
    "variant",
    "grade",
    "grade_chip",
    "condition",
    "category",
    "sport",
    "auction_type",
    "card_count",
    "serial_numbered",
    "serial_current",
    "serial_total",
    "team",
    "auto",
    "rookie",
    "patch",
    "rpa",
)

_CANONICAL_RESPONSE_FIELDS = set(CANONICAL_FIELDS) | {
    "set_name",
    "grading_company",
    "cert_number",
    "serial_numbered",
    "serial_current",
    "serial_total",
    "team",
    "auction_type",
    "card_count",
}


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "none", "null", "unknown", "n/a", "na", CANONICAL_MISSING}
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _set_if_missing(row: dict[str, Any], key: str, value: Any) -> None:
    if _nonempty(row.get(key)) or value is None or value == CANONICAL_MISSING:
        return
    row[key] = value


def _grade_company_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    upper = text.upper()
    for alias, canonical in GRADE_COMPANY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", upper):
            return canonical
    for candidate in GRADE_COMPANIES:
        if re.search(rf"\b{re.escape(candidate)}\b", upper):
            return candidate
    return ""


def _grade_value_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text or re.fullmatch(r"(raw|ungraded)", text, re.I):
        return ""
    match = GRADE_VALUE_RE.search(text)
    return match.group(1) if match else ""


def _safe_grade_label(row: dict[str, Any]) -> str:
    """Return a display-safe grade label; never render a bare numeric grade.

    Raw/ungraded cards are not "missing" grading data. Only rows with an
    extracted numeric slab grade and no grading company get the explicit
    "Grading company missing" label.
    """
    canonical = row.get("canonical_identity")
    if not isinstance(canonical, dict):
        canonical = {}
    source = (
        canonical_value(canonical, "grade")
        or row.get("grade")
        or row.get("grade_chip")
        or row.get("condition")
    )
    company = _grade_company_label(
        canonical_value(canonical, "grading_company")
        or row.get("grading_company")
        or row.get("grade_company")
        or source
    )
    grade = _grade_value_label(source)
    raw_like = bool(source and re.fullmatch(r"(raw|ungraded)", str(source).strip(), re.I))
    if company and grade:
        return f"{company} {grade}"
    if company:
        return f"{company} grade missing"
    if grade:
        return "Grading company missing"
    if raw_like:
        return str(source).strip()
    return ""


def _apply_safe_grade_display(row: dict[str, Any]) -> None:
    label = _safe_grade_label(row)
    if not label:
        return
    if label == "Raw":
        row["condition"] = "Raw"
        row["grade"] = ""
        row["grade_chip"] = ""
        return
    row["grade_chip"] = label
    row["grade"] = label


def _apply_canonical_descriptor_shape(row: dict[str, Any]) -> None:
    """Flatten existing descriptor evidence for the read surface only."""
    raw_full = row.get("raw_full") if isinstance(row.get("raw_full"), dict) else {}
    payload: dict[str, Any] = {}
    if raw_full:
        payload.update(raw_full)
    for key in _CANONICAL_PAYLOAD_KEYS:
        value = row.get(key)
        if isinstance(value, dict) and value:
            payload[key] = value
    for payload_key, mapping in _CANONICAL_SCALAR_MAP.items():
        payload.setdefault(payload_key, {})
        for alias, target_key in mapping.items():
            value = row.get(alias)
            if _nonempty(value):
                payload[payload_key][target_key] = value
    for key in _CANONICAL_FLAT_KEYS:
        if key in row:
            payload[key] = row.get(key)

    canonical = normalize_card_identity(payload)
    row["canonical_identity"] = canonical
    row["canonical_missing_fields"] = canonical.get("missing_fields", [])

    _set_if_missing(row, "player", canonical_value(canonical, "player") or canonical_value(canonical, "name"))
    _set_if_missing(row, "card_name", canonical_value(canonical, "name") or canonical_value(canonical, "player"))
    _set_if_missing(row, "year", canonical_value(canonical, "year"))
    _set_if_missing(row, "brand", canonical_value(canonical, "brand"))
    _set_if_missing(row, "set_name", canonical_value(canonical, "card_set") or canonical_value(canonical, "set"))
    _set_if_missing(row, "variant", canonical_value(canonical, "variant") or canonical_value(canonical, "parallel") or canonical_value(canonical, "insert_type"))
    _set_if_missing(row, "grade", canonical_value(canonical, "grade"))
    _set_if_missing(row, "grading_company", canonical_value(canonical, "grading_company"))
    _set_if_missing(row, "cert_number", canonical_value(canonical, "cert_number"))
    _set_if_missing(row, "serial_numbered", canonical_value(canonical, "serial_numbered"))
    _set_if_missing(row, "serial_current", canonical_value(canonical, "serial_current"))
    _set_if_missing(row, "serial_total", canonical_value(canonical, "serial_total"))
    _set_if_missing(row, "team", canonical_value(canonical, "team"))
    _set_if_missing(row, "sport", canonical_value(canonical, "sport"))
    _set_if_missing(row, "auction_type", canonical_value(canonical, "auction_type"))
    _set_if_missing(row, "card_count", canonical_value(canonical, "card_count"))

    for key in ("auto", "rookie", "patch", "rpa"):
        value = canonical_value(canonical, key)
        if row.get(key) is None and value is not None:
            row[key] = value

    _apply_safe_grade_display(row)

    row["canonical_descriptors"] = {
        field: canonical.get(field, CANONICAL_MISSING)
        for field in _CANONICAL_RESPONSE_FIELDS
    }


def _resolve_display_images(row: dict[str, Any]) -> None:
    """Resolve the browser-facing image URLs from the already-shaped row.

    Browsers receive only relative 8504 proxy URLs; the server-side proxy fetches
    private R2 / 9008 / local roots. Front display priority (standing gate): captured
    card_ front -> stamped _mazi -> recovered ops_display_image -> suppress. CDN and
    proof_ basenames are never a valid front: they force the recovery fallback, and
    if nothing recoverable exists the tile is suppressed rather than showing CDN.
    """
    # Preserve the ORIGINAL capture class before the identified-review overwrite
    # at the bottom of this function clobbers front_image_status to
    # "displayable_for_review". These two Neon statuses are the rows that never
    # got a clean cropped card front — the tile is showing the full captured
    # Whatnot stream frame, not a card. Verified empirically against the master
    # capture data (source_buffer / front_quality_flag): recovery_display_front
    # and displayable_for_identified_review are ~100% no_timer_safe_official_front
    # rescue/video frames; valid_card_front and mini_intake_classical_front are
    # the clean crops. The frontend badges is_review_frame so a reviewer is never
    # misled into reading a stream frame as a clean identified card image.
    _orig_front_status = str(row.get("front_image_status") or "").strip().lower()
    row["is_review_frame"] = _orig_front_status in (
        "recovery_display_front",
        "displayable_for_identified_review",
    )
    official_front_basename = extract_basename(
        row.get("image_front_neon_url"),
        row.get("image_front"),
    )
    ops_display_image_value = ""
    for candidate in (
        row.get("ops_display_image"),
        row.get("review_context_image"),
        row.get("review_context_image_first"),
    ):
        if candidate and not _proof_only_basename(candidate) and not _cdn_basename(candidate):
            ops_display_image_value = candidate
            break
    row["ops_display_image_basename"] = extract_basename(ops_display_image_value)
    if not row.get("ops_display_image") and ops_display_image_value:
        row["ops_display_image"] = ops_display_image_value
        row["ops_display_image_source"] = row.get("ops_display_image_source") or "raw_fallback"
        row["ops_display_image_status"] = row.get("ops_display_image_status") or "review_only_existing_row"
    row["image_front_is_ops_display"] = False
    row["image_front_basename"] = official_front_basename
    if _proof_only_basename(row["image_front_basename"]):
        row["proof_only_front_blocked"] = True
        row["blocked_front_basename"] = row["image_front_basename"]
        row["image_front_basename"] = ""
        row["front_image_status"] = row.get("front_image_status") or "missing"
    elif _cdn_basename(row["image_front_basename"]):
        row["cdn_only_front_blocked"] = True
        row["blocked_front_basename"] = row["image_front_basename"]
        row["image_front_basename"] = ""
        row["front_image_status"] = row.get("front_image_status") or "cdn_suppressed"
    if not row["image_front_basename"] and row["ops_display_image_basename"]:
        row["image_front_basename"] = row["ops_display_image_basename"]
        row["image_front_is_ops_display"] = True
    row["ops_display_back_image_basename"] = extract_basename(row.get("ops_display_back_image"))
    row["image_back_basename"] = extract_basename(
        row.get("image_back_neon_url"),
        row.get("image_back"),
        row.get("ops_display_back_image_basename"),
    )
    proof_bn = row.get("review_proof_basename") or row.get("original_proof_basename")
    if isinstance(proof_bn, str) and proof_bn.startswith('"') and proof_bn.endswith('"'):
        proof_bn = proof_bn.strip('"')
    row["image_proof_basename"] = extract_basename(proof_bn) if proof_bn else ""

    # Build the front URL from the RESOLVED basename only. Deriving it from the raw
    # image_front(_neon_url) here would re-surface a CDN/proof value that the block
    # above deliberately blanked, defeating the suppression.
    row["image_front_url"] = best_image_url(row.get("image_front_basename"))
    # DISPLAY-ONLY preference (2026-07-09): overlay-bearing fronts (capture
    # fallback-ladder rungs: recovery/mini-intake/container clips) may carry a
    # non-destructive subject-card crop recorded beside raw as `display_crop`
    # by display_crop_worker.py. Prefer it for the DISPLAY url only — the
    # resolved basename (evidence identity, proof panels, watermark checks)
    # stays untouched.
    _raw_obj = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    _crop = _raw_obj.get("display_crop") if isinstance(_raw_obj, dict) else None
    row["display_crop_basename"] = extract_basename(_crop) if _crop else ""
    row["image_front_is_display_crop"] = False
    if row["display_crop_basename"] and row.get("image_front_basename"):
        row["image_front_url"] = best_image_url(row["display_crop_basename"])
        row["image_front_is_display_crop"] = True
    row["image_back_url"] = best_image_url(
        row.get("image_back_neon_url"),
        row.get("image_back"),
        row.get("image_back_basename"),
    )
    row["image_proof_url"] = best_image_url(row.get("image_proof_basename"))

    if (
        str(row.get("observed_in") or "").lower() == "identified"
        and row.get("image_front_url")
        and not row.get("image_front_is_ops_display")
    ):
        row["front_image_status"] = "displayable_for_review"


def _shape_row(raw_tuple: tuple, columns: list[str]) -> dict[str, Any]:
    """Convert a psycopg row + column names into a privacy-scrubbed dict."""
    row = dict(zip(columns, raw_tuple))

    _apply_canonical_descriptor_shape(row)

    # Neon can surface duplicate pending_reasons from upstream JSON merges.
    # Keep first occurrence so filters still match while the operator UI reads cleanly.
    pending_reasons = row.get("pending_reasons")
    if isinstance(pending_reasons, list):
        seen_reasons: set[str] = set()
        deduped_reasons: list[Any] = []
        for reason in pending_reasons:
            key = str(reason)
            if key in seen_reasons:
                continue
            seen_reasons.add(key)
            deduped_reasons.append(reason)
        row["pending_reasons"] = deduped_reasons

    # Promote the proof binding fields from raw->json into top-level
    # so the frontend doesn't need to know about the nested shape.
    for k in (
        "proof_binding_status",
        "proof_image_status",
        "front_image_status",
        "back_image_status",
        "mazi_cert_status",
        "publish_ready",
        "risk_flags",
        "review_proof_basename",
        "original_proof_basename",
        # Row-defect safety signals (NULL upstream today; absent != false)
        "front_capture_class",
        "proof_match_to_front",
        "proof_overlay_text",
        "proof_overlay_risk",
        "non_card_object_detected",
        "price_context_outlier",
        "identity_enrichment_disagreement",
        "public_image_safe_reason",
        "ops_display_image",
        "ops_display_image_source",
        "ops_display_image_status",
        "ops_display_back_image",
        "ops_display_back_image_status",
        "review_context_image",
        "review_context_image_first",
        "proof_internal_image_first",
        "proof_image_first",
        "cdn_image",
    ):
        v = row.get(k)
        if isinstance(v, str) and v.startswith('"') and v.endswith('"'):
            row[k] = v.strip('"')

    _apply_effective_publish_safety(row)

    # Compute caution chips (lane posture)
    chips = derive_chips(row)
    decision = str(row.get("review_decision") or "").strip().lower()
    if decision == "mazified":
        chips = ["MAZIFIED REVIEW", "CHROME MAZIFIED CANDIDATE"] + [
            c for c in chips if str(c).upper() not in {"NOT MAZIFIED"}
        ]
        chips.extend(["NOT PUBLIC READY", "NOT VALUATION SAFE"])
    elif decision == "deny":
        chips = ["DENIED FROM PUBLIC PATH"] + chips
    if row.get("effective_publish_block_reason") == "proof_pending_unverified":
        chips.append("PROOF_UNVERIFIED_HOLD")
    row["chips"] = chips

    # Compute row-defect safety badges (distinct from posture chips).
    # Always exactly len(SAFETY_BADGE_FIELDS) badges; missing fields
    # surface as severity="unknown" -- fail-closed, never "safe".
    row["safety_badges"] = derive_safety_badges(row)
    _apply_price_audit(row)
    row["mazified_blockers"] = mazified_blockers(row)
    row["hard_block_strip"] = row_hard_block_labels(row)

    # Flat safety_signals subobject for clients that prefer named
    # access. Mirrors the badge values; None when absent.
    row["safety_signals"] = {name: row.get(name) for name in SAFETY_FIELD_NAMES}

    # The nested payloads and raw_full are normalization inputs, not API
    # output. Keep the browser contract descriptor-focused and privacy-scrubbed.
    for k in (*_CANONICAL_PAYLOAD_KEYS, *_CANONICAL_SCALAR_KEYS, "raw_full"):
        row.pop(k, None)

    # ---- Image URL resolution ----------------------------------------------
    _resolve_display_images(row)

    # Force decimals to floats and datetimes to ISO strings via deep_scrub
    return deep_scrub(row)


def _exec_query(sql: str, params: tuple) -> list[dict[str, Any]]:
    with neon_conn() as c:
        cur = c.cursor()
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    return [_shape_row(r, cols) for r in rows]


def _market_comps_for_row(row: dict[str, Any], limit: int = 250) -> list[dict[str, Any]]:
    """Verified per-card external comps for the drawer valuation panel.

    Reads the comp candidate base tables directly (not exact_card_match_audit_v1)
    so EVERY grade is returned -- average-safe, below-threshold context, OBO, and
    grade-mismatch context (SGC/CGC/raw). The audit view is intentionally bypassed
    because it hard-filters match_score >= 0.85 and only exposes context rows whose
    exclusion_reason = 'below_include_average_threshold', which hides off-grade and
    raw comps. The frontend groups these by each comp's own parsed grade and shows
    one grade group at a time via a dropdown.

    Each comp's grade comes from its listing title (_parse_comp_grade); the target
    card's grade is returned separately as target_grade_* for default selection.
    OBO and below-threshold rows are never average-safe.
    """
    source_key = str(row.get("source_key") or "").strip()
    comp_id = str(row.get("comp_id") or "").strip()
    if not source_key and not comp_id:
        return []

    identity_clause = "t.whatnot_source_key = %s" if source_key else "t.whatnot_comp_id = %s"
    identity_value = source_key or comp_id
    # average-safe definition mirrors exact_card_match_audit_v1.average_safe
    avg_safe_expr = "(c.include_in_average AND c.best_offer IS NOT TRUE AND e.verified_price_eligible IS TRUE)"
    # DISTINCT ON (e.id): one row per underlying sale. The candidate base table
    # can hold >1 comp_match_candidates row for the same transaction when the
    # same sale matched under different query_variant values (the variant is in
    # the candidate unique key), so without this dedup a single eBay/SCP sale
    # would render -- and average-weight -- multiple times and crowd real comps
    # out under LIMIT. The inner ORDER BY keeps the best representation per sale
    # (average-safe first, then highest score, then newest candidate); the outer
    # query restores the drawer's display ordering and applies LIMIT post-dedup.
    sql = f"""
        SELECT * FROM (
            SELECT DISTINCT ON (e.id)
                e.id                       AS external_txn_id,
                e.source_code              AS external_source,
                e.source_item_id           AS external_source_item_id,
                e.canonical_source_url     AS external_source_url,
                e.title                    AS external_title,
                e.sold_price               AS external_sold_price,
                e.sold_date                AS external_sold_date,
                e.currency                 AS external_currency,
                c.best_offer               AS best_offer,
                e.verified_price_eligible  AS verified_price_eligible,
                c.match_score              AS match_score,
                c.include_in_average       AS include_in_average,
                {avg_safe_expr}            AS average_safe,
                c.exclusion_reason         AS exclusion_reason,
                CASE
                    WHEN {avg_safe_expr} THEN 'average_safe'
                    WHEN c.best_offer THEN 'obo_context_only'
                    ELSE 'context_only'
                END                        AS display_context,
                t.identity_json->>'grade_company' AS target_grade_company,
                t.identity_json->>'grade_value'   AS target_grade_value,
                c.match_details            AS match_details,
                c.id                       AS candidate_id,
                COALESCE(
                    c.match_details->>'image_url',
                    c.match_details->>'thumbnail_url',
                    e.raw->>'image_url'
                ) AS external_image_url
            FROM comp_match_candidates c
            JOIN comp_scrub_targets t ON t.id = c.target_id
            JOIN external_transactions e ON e.id = c.transaction_id
            WHERE {identity_clause}
              AND t.lane = 'whatnot_exact_card'
              AND c.match_score >= 0.78
            ORDER BY
                e.id,
                {avg_safe_expr} DESC,
                c.match_score DESC,
                c.id DESC
        ) d
        ORDER BY
            d.average_safe DESC,
            d.best_offer ASC,
            d.external_sold_date DESC NULLS LAST,
            d.candidate_id DESC
        LIMIT %s
    """
    comps: list[dict[str, Any]] = []
    try:
        with neon_conn() as c:
            cur = c.cursor()
            cur.execute("SET LOCAL statement_timeout = '5000ms'")
            cur.execute(sql, (identity_value, limit))
            cols = [d.name for d in cur.description]
            for raw in cur.fetchall():
                r = dict(zip(cols, raw))
                is_obo = bool(r.get("best_offer"))
                average_safe = bool(r.get("average_safe")) and not is_obo
                context_only = (not average_safe) or is_obo
                details = r.get("match_details") if isinstance(r.get("match_details"), dict) else {}
                comp_company, comp_value, comp_group = _parse_comp_grade(r.get("external_title") or "")
                comps.append({
                    "source": r.get("external_source") or "ebay",
                    "sale_date": r.get("external_sold_date"),
                    "sale_price": r.get("external_sold_price"),
                    "sold_date": r.get("external_sold_date"),
                    "sold_price": r.get("external_sold_price"),
                    "price": r.get("external_sold_price"),
                    # Per-comp grade parsed from THIS listing's title (fixes the
                    # bug where every comp showed the card's own grade).
                    "grade": comp_group,
                    "comp_grade_company": comp_company,
                    "comp_grade_value": comp_value,
                    "grade_group": comp_group,
                    "target_grade_company": (r.get("target_grade_company") or ""),
                    "target_grade_value": (r.get("target_grade_value") or ""),
                    "condition": "",
                    "match_type": "exact_card",
                    "confidence": r.get("match_score"),
                    "included_in_average": average_safe,
                    "image_url": r.get("external_image_url") or "",
                    "thumbnail_url": r.get("external_image_url") or "",
                    "external_url": r.get("external_source_url") or "",
                    "reason_excluded": r.get("exclusion_reason") or r.get("context_reason") or ("best_offer_excluded_from_average" if is_obo else ""),
                    "source_record_id": r.get("external_source_item_id") or "",
                    "marketplace_item_id": r.get("external_source_item_id") or "",
                    "title": r.get("external_title") or "",
                    "seller_or_auction_house": str(r.get("external_source") or "ebay").upper(),
                    "currency": r.get("external_currency") or "USD",
                    "is_obo": is_obo,
                    "is_context_only": context_only,
                    "is_internal_whatnot": False,
                    "trust_status": "trusted_external_match" if average_safe else "trusted_external_context",
                    "data_era": "era-2",
                    "mazified_status": "NOT MAZIFIED",
                    "average_policy": "average_safe" if average_safe else ("never_average" if is_obo else "context_only"),
                    "display_context": r.get("display_context") or ("average_safe" if average_safe else ("obo_context_only" if is_obo else "context_only")),
                    "matched_anchors": details.get("matched_anchors") if isinstance(details, dict) else [],
                })
    except Exception as e:
        return [{
            "source": "Valuation",
            "sale_price": None,
            "title": f"valuation lookup unavailable: {type(e).__name__}",
            "included_in_average": False,
            "is_obo": False,
            "is_context_only": True,
            "reason_excluded": str(e)[:200],
            "match_type": "lookup_error",
            "trust_status": "unavailable",
        }]
    return deep_scrub(comps)


def _source_sibling_summary(row: dict[str, Any], selected: bool) -> dict[str, Any]:
    return deep_scrub({
        "source_view": row.get("source_view") or row.get("observed_in") or "unknown",
        "selected": selected,
        "is_chosen": selected,
        "comp_id": row.get("comp_id"),
        "source_key": row.get("source_key"),
        "feed_observation_id": row.get("feed_observation_id"),
        "review_id": row.get("review_id"),
        "seller": row.get("seller"),
        "auction_number": row.get("auction_number"),
        "sold_price": row.get("sold_price"),
        "title": row.get("title") or row.get("card_name"),
        "image_front_basename": row.get("image_front_basename"),
        "image_proof_basename": row.get("image_proof_basename"),
        "review_decision": row.get("review_decision"),
        "trust_bucket": row.get("trust_bucket"),
    })


def _dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("source_view") or ""),
            str(row.get("source_key") or ""),
            str(row.get("feed_observation_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


# ============================================================================
# External row shaping (eBay / Goldin reference comps)
#
# These rows are NOT Whatnot captures and have a completely different column
# set than `operational_pending_sales`. We project a slim, privacy-scrubbed
# shape with NO proof_binding / trust_bucket / mazi_cert / seller / auction_number
# fields, and we hard-code the EXTERNAL chip cluster.
# ============================================================================
def _shape_external_row(
    raw_tuple: tuple,
    columns: list[str],
    source_code: str,
) -> dict[str, Any]:
    raw_row = dict(zip(columns, raw_tuple))

    # Pull select-list pieces; never echo the entire `raw` jsonb back.
    raw_jsonb = raw_row.get("raw")
    if not isinstance(raw_jsonb, dict):
        raw_jsonb = {}

    def first_public_image(*values: Any) -> str:
        for value in values:
            if isinstance(value, str):
                candidates = [value]
            elif isinstance(value, list):
                candidates = value
            else:
                candidates = []
            for candidate in candidates:
                if isinstance(candidate, dict):
                    candidate = (
                        candidate.get("url")
                        or candidate.get("image_url")
                        or candidate.get("thumbnail_url")
                        or candidate.get("src")
                    )
                if not isinstance(candidate, str):
                    continue
                candidate = candidate.strip()
                if candidate.startswith(("http://", "https://")):
                    return candidate
        return ""

    # Image URL: source CDN URL (eBay i.ebayimg.com, Goldin cloudfront,
    # Fanatics CDN, etc). These are public-internet URLs, NOT /Users/ paths
    # and NOT 9008 proxy.
    image_url = first_public_image(
        raw_jsonb.get("image_url"),
        raw_jsonb.get("imageUrl"),
        raw_jsonb.get("thumbnail_url"),
        raw_jsonb.get("thumbnailUrl"),
        raw_jsonb.get("gallery_url"),
        raw_jsonb.get("primary_image_url"),
        raw_jsonb.get("image_urls"),
        raw_jsonb.get("images"),
        raw_jsonb.get("photo_urls"),
    )
    if isinstance(image_url, str) and image_url.startswith("/Users/"):
        image_url = ""  # defensive — should never happen for ext sources

    # Source URL (link out to the original listing/lot).
    listing_url = (
        raw_row.get("source_url")
        or raw_jsonb.get("url")
        or ""
    )

    # Synthetic comp_id for SPA routing — these rows have NO comp_id in
    # operational_pending_sales / trusted_sales_current. We prefix `ext-`
    # so it's impossible to confuse with a Whatnot comp_id.
    src_iid = raw_row.get("source_item_id") or ""
    db_id = raw_row.get("id")
    synth_comp_id = f"ext-{source_code}-{src_iid or db_id}"

    # Optional sub-fields from `raw` that are safe to expose:
    cond = raw_jsonb.get("condition")
    grade = raw_jsonb.get("grade")

    out: dict[str, Any] = {
        # routing / identity
        "comp_id": synth_comp_id,
        "external": True,
        "source_code": source_code,
        "source_item_id": src_iid,
        "source_url": listing_url,
        # title / normalization
        "title": raw_row.get("title") or "",
        "normalized_title": raw_row.get("normalized_title") or "",
        # price
        "sold_price": raw_row.get("sold_price"),
        "sold_date": raw_row.get("sold_date"),
        "currency": raw_row.get("currency") or "USD",
        # filter-state flags (always FALSE/TRUE here by construction; surfaced
        # so the SPA + QA can prove the filter was applied)
        "best_offer": bool(raw_row.get("best_offer")),
        "verified_price_eligible": bool(raw_row.get("verified_price_eligible")),
        "sold_status": raw_row.get("sold_status") or "",
        "duplicate_status": raw_row.get("duplicate_status") or "",
        # condition / grade if scraper captured them
        "condition": cond or "",
        "grade": grade or "",
        # image
        "image_url": image_url,
        "image_front_url": image_url,
        "image_front_basename": "",   # external sources do NOT use 9008 proxy
        "image_back_url": "",
        "image_back_basename": "",
        "image_proof_url": "",
        "image_proof_basename": "",
        # provenance
        "scraped_at": raw_row.get("scraped_at"),
        "__total_count": raw_row.get("__total_count"),
        # display chips — mandated by orchestrator
        "chips": list(EXTERNAL_CHIPS_BASE),
    }
    # deep_scrub strips any /Users/ paths and removes buyer/winner/etc keys.
    return deep_scrub(out)


def _exec_external_query(
    sql: str,
    params: tuple,
    source_code: str,
) -> list[dict[str, Any]]:
    with neon_conn() as c:
        cur = c.cursor()
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    return [_shape_external_row(r, cols, source_code) for r in rows]


# ============================================================================
# Health
# ============================================================================
@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    # Keep health cheap and bounded. Rich counts live on the queue/source
    # endpoints; health must never block the display surface behind view scans.
    db_ok = False
    write_open = review_write_enabled()
    write_scope_keys = sorted(review_write_scope_source_keys())
    write_scope_test_run_id = review_write_scope_test_run_id()
    write_scope_identified_all = review_write_scope_identified_all()
    try:
        with neon_conn() as c:
            cur = c.cursor()
            cur.execute("SET LOCAL statement_timeout = '3000ms'")
            cur.execute("SELECT 1")
            db_ok = bool(cur.fetchone())
    except Exception as e:
        return {
            "ok": False,
            "db_ok": False,
            "error": type(e).__name__,
            "review_write_enabled": write_open,
            "review_write_scope_source_keys": write_scope_keys,
            "review_write_scope_test_run_id": write_scope_test_run_id,
            "review_write_scope_identified_all": write_scope_identified_all,
            "started_at": STARTED_AT,
            "version": APP_VERSION,
        }
    return {
        "ok": bool(db_ok),
        "db_ok": bool(db_ok),
        "review_write_enabled": write_open,
        "review_write_scope_source_keys": write_scope_keys,
        "review_write_scope_test_run_id": write_scope_test_run_id,
        "review_write_scope_identified_all": write_scope_identified_all,
        "valid_decisions": sorted(VALID_DECISIONS),
        "started_at": STARTED_AT,
        "version": APP_VERSION,
    }


# ============================================================================
# Queue counts
# ============================================================================
@app.get("/api/v1/queue/counts")
def queue_counts() -> dict[str, Any]:
    now = monotonic()
    with _QUEUE_COUNTS_CACHE_LOCK:
        cached = _QUEUE_COUNTS_CACHE.get("payload")
        if cached and now < float(_QUEUE_COUNTS_CACHE.get("expires_at") or 0.0):
            return {**cached, "cache": "hit"}

        out: dict[str, int] = {name: 0 for name in QUEUE_MAP}
        generated_at = datetime.now(timezone.utc).isoformat()
        try:
            with neon_conn() as c:
                cur = c.cursor()
                cur.execute(QUEUE_COUNTS_SQL)
                for name, n in cur.fetchall():
                    out[name] = int(n or 0)
        except Exception as e:
            return {
                "counts": out,
                "error": "counts_failed",
                "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "generated_at": generated_at,
                "cache": "miss",
            }
        payload = {
            "counts": out,
            "generated_at": generated_at,
            "cache": "miss",
            "ttl_seconds": int(QUEUE_COUNTS_CACHE_TTL_SECONDS),
        }
        _QUEUE_COUNTS_CACHE["payload"] = payload
        _QUEUE_COUNTS_CACHE["expires_at"] = monotonic() + QUEUE_COUNTS_CACHE_TTL_SECONDS
        return payload


# ============================================================================
# Queue list
# ============================================================================
@app.get("/api/v1/queue")
def queue(
    name: str = Query(..., description="queue name"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    page: int = Query(1, ge=1),
    search: str | None = Query(None, description="space-separated token search; tokens may appear in any order"),
    sort: str | None = Query(None, description="optional sort key"),
    watermarked_only: bool = Query(False, description="only rows with MAZI-stamped fronts"),
) -> JSONResponse:
    """Return rows for `name`. Never raises — empty/error always returns JSON.

    Shape on success: {name, row_count, rows, generated_at}.
    Shape on error:   {name, row_count: 0, rows: [], error, detail, generated_at}.
    """
    ts = datetime.now(timezone.utc).isoformat()
    if name not in QUEUE_MAP:
        return _json({
            "name": name,
            "row_count": 0,
            "rows": [],
            "error": "unknown_queue",
            "queue": name,
            "detail": f"unknown queue: {name}",
            "generated_at": ts,
        })
    try:
        sql, params = queue_sql(
            name,
            limit,
            sort=sort,
            watermarked_only=watermarked_only,
            search=search,
            page=page,
        )
        rows = _exec_query(sql, params)
        total_count = 0
        if rows:
            total_count = int(rows[0].pop("__total_count", 0) or 0)
            for row in rows[1:]:
                row.pop("__total_count", None)
        payload = {
            "name": name,
            "row_count": len(rows),
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "search": search or "",
            "rows": rows,
            "generated_at": ts,
        }
        return _json(deep_scrub(payload))
    except Exception as e:
        return _json({
            "name": name,
            "row_count": 0,
            "rows": [],
            "error": "queue_failed",
            "queue": name,
            "detail": f"{type(e).__name__}: {str(e)[:300]}",
            "generated_at": ts,
        })


# ============================================================================
# Stage 1 trust-promotion diagnostics
# ============================================================================
@app.get("/api/v1/stage1/watermark-inventory")
def stage1_watermark_inventory() -> JSONResponse:
    with neon_conn() as c:
        return _json(deep_scrub(watermark_inventory(c)))


# ============================================================================
# Source tabs (Trusted Snapshot, Working Queue, eBay, Goldin, Fanatics,
# Heritage, PWCC, VDEX Discovery). Wraps the queue endpoint where the
# source maps to one; returns empty placeholder JSON otherwise. Never
# raises.
# ============================================================================
@app.get("/api/v1/sources")
def sources_list() -> dict[str, Any]:
    return {
        "sources": [
            {"id": k, "queue": v, "wired": (v is not None or k in EXTERNAL_SOURCE_NAMES)}
            for k, v in SOURCE_QUEUE_MAP.items()
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/sources/counts")
def sources_counts() -> dict[str, Any]:
    qc = queue_counts()
    counts = qc.get("counts") or {}
    out: dict[str, int] = {}
    for src, q in SOURCE_QUEUE_MAP.items():
        out[src] = int(counts.get(q, 0)) if q else 0

    # External-comp tabs: read filtered counts from external_transactions.
    # Wrapped in try/except so a DB hiccup doesn't break the whole counts
    # response — placeholders survive at 0 in that case.
    try:
        with neon_conn() as c:
            cur = c.cursor()
            cur.execute(EXTERNAL_COUNTS_SQL)
            for src, n in cur.fetchall():
                if src in SOURCE_QUEUE_MAP:
                    out[src] = int(n or 0)
    except Exception:
        pass

    return {"counts": out, "generated_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/source")
def source(
    name: str = Query(..., description="source id"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    page: int = Query(1, ge=1),
    search: str | None = Query(None, description="space-separated token search; tokens may appear in any order"),
) -> JSONResponse:
    ts = datetime.now(timezone.utc).isoformat()
    if name not in SOURCE_QUEUE_MAP:
        return _json({
            "source": name,
            "row_count": 0,
            "rows": [],
            "placeholder": True,
            "error": "unknown_source",
            "detail": f"unknown source: {name}",
            "generated_at": ts,
        })

    # ---- External reference comps (eBay / Goldin) -------------------------
    # These are NOT Whatnot rows. They read directly from external_transactions
    # with the OBO + verified_price_eligible filters applied. Row shape has
    # NO proof / trust / mazi_cert / seller / auction_number fields.
    if name in EXTERNAL_SOURCE_NAMES:
        try:
            sql, params = external_sql(name, limit, search=search, page=page)
            rows = _exec_external_query(sql, params, source_code=name)
            total_count = 0
            if rows:
                total_count = int(rows[0].pop("__total_count", 0) or 0)
                for row in rows[1:]:
                    row.pop("__total_count", None)
            return _json({
                "source": name,
                "external": True,
                "reference_only": True,
                "filter": EXTERNAL_FILTER_SPEC.get(name, {}),
                "row_count": len(rows),
                "total_count": total_count,
                "page": page,
                "limit": limit,
                "search": search or "",
                "rows": rows,
                "generated_at": ts,
            })
        except Exception as e:
            return _json({
                "source": name,
                "external": True,
                "reference_only": True,
                "row_count": 0,
                "rows": [],
                "error": "external_source_failed",
                "detail": f"{type(e).__name__}: {str(e)[:300]}",
                "generated_at": ts,
            })

    # ---- Whatnot-queue-backed source (trusted_snapshot, working_queue) ----
    q = SOURCE_QUEUE_MAP[name]
    if q is None:
        return _json({
            "source": name,
            "row_count": 0,
            "rows": [],
            "placeholder": True,
            "message": (
                f"{name} source is not wired to Neon yet — "
                "tab renders as empty/safe placeholder."
            ),
            "generated_at": ts,
        })
    inner = queue(name=q, limit=limit, page=page, search=search)
    import json as _stdjson
    try:
        data = _stdjson.loads(inner.body)
    except Exception:
        data = {"rows": [], "row_count": 0}
    data["source"] = name
    data["queue"] = q
    return _json(data)


# ============================================================================
# Header stats — pipeline funnel counts (Pending → Identified → Trusted → Mazified)
# ============================================================================
@app.get("/api/v1/stats/header")
def stats_header() -> dict[str, Any]:
    """Pipeline funnel header counts, computed live from Neon. Empty-safe.

    One authoritative source per stage, mirroring the per-tab queries in
    queues.py (QUEUE_COUNTS_SQL / EXTERNAL_COUNTS_SQL) so the header reconciles
    with the tab counts by construction:
        pending    -> operational_pending_sales              (Pending Review)
        identified -> identified_sales_current               (Identified tab)
        trusted    -> stage1_trusted_sales_current           (Trusted tab, stage-1)
        mazified   -> review_decision_events latest='mazified' (Mazified tab)
        external   -> external_transactions (reference comps only, NOT a stage)
    """
    ts = datetime.now(timezone.utc).isoformat()
    out = {
        "pending_count": 0,
        "identified_count": 0,
        "trusted_count": 0,
        "mazified_count": 0,
        "external_count": 0,
        "generated_at": ts,
    }
    sql = """
        WITH pending AS (
            SELECT COUNT(*) AS n FROM operational_pending_sales
        ),
        identified AS (
            SELECT COUNT(*) AS n FROM identified_sales_current
        ),
        trusted AS (
            SELECT COUNT(*) AS n FROM stage1_trusted_sales_current
        ),
        mazified AS (
            SELECT COUNT(*) AS n FROM (
                SELECT DISTINCT ON (source_key) source_key, decision
                FROM review_decision_events
                ORDER BY source_key, created_at DESC
            ) latest
            WHERE latest.decision = 'mazified'
        ),
        external AS (
            SELECT COUNT(*) AS n FROM external_transactions
            WHERE source_code IN ('ebay','goldin','fanatics','heritage','pwcc')
              AND verified_price_eligible = TRUE
              AND (source_code <> 'ebay' OR best_offer = FALSE)
        )
        SELECT pending.n, identified.n, trusted.n, mazified.n, external.n
        FROM pending, identified, trusted, mazified, external
    """
    try:
        with neon_conn() as c:
            cur = c.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        if row:
            n_pend, n_ident, n_trusted, n_maz, n_ext = row
            out["pending_count"]    = int(n_pend or 0)
            out["identified_count"] = int(n_ident or 0)
            out["trusted_count"]    = int(n_trusted or 0)
            out["mazified_count"]   = int(n_maz or 0)
            out["external_count"]   = int(n_ext or 0)
    except Exception as e:
        out["error"] = "stats_failed"
        out["detail"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


# ============================================================================
# Row detail
# ============================================================================
@app.get("/api/v1/row/{comp_id}")
def row_detail(
    comp_id: str,
    source_key: str | None = Query(None, description="clicked-tile source_key for exact-resolution + drift detection"),
    feed_observation_id: int | None = Query(None, description="clicked-tile feed_observation_id for exact resolution"),
    review_id: str | None = Query(None, description="clicked-tile review_id for drift comparison"),
    seller: str | None = Query(None, description="clicked-tile seller for drift comparison"),
    auction_number: int | None = Query(None, description="clicked-tile auction_number for drift comparison"),
    image_front_basename: str | None = Query(None, description="clicked-tile front basename for drift comparison"),
    image_proof_basename: str | None = Query(None, description="clicked-tile proof basename for drift comparison"),
) -> JSONResponse:
    """Resolve one row by comp_id, preferring exact source_key match when provided.

    SAFETY-RAIL ADDITIONS (2026-05-29):
      - The SPA now passes source_key + feed_observation_id from the
        clicked tile's dataset; we use comp_id as the lookup key (the
        existing 3-table UNION) and then *prefer* the row whose
        source_key matches. If we can't find an exact source_key match
        but a comp_id-only fallback exists, we still return that row
        but attach a `source_drift` warning so the SPA can disable
        action buttons.
      - The payload now also carries:
          row.allowed_decisions:  list[str] -- DB-supported decisions
                                  legal for this specific row state
          row.mazified_button:    {enabled, reason} for drawer UI
          row.row_lookup:         {requested:{...}, resolved:{...}}
                                  for client-side audit logging
          source_drift:           non-null iff requested != resolved
    """
    ts = datetime.now(timezone.utc).isoformat()
    requested_identity = {
        "comp_id": comp_id,
        "source_key": source_key,
        "feed_observation_id": feed_observation_id,
        "review_id": review_id,
        "seller": seller,
        "auction_number": auction_number,
        "image_front_basename": image_front_basename,
        "image_proof_basename": image_proof_basename,
    }
    try:
        with neon_conn() as c:
            cur = c.cursor()
            rows = []
            cols = []
            if source_key:
                cur.execute(ROW_BY_SOURCE_KEY_SQL, (source_key, source_key, source_key, source_key, source_key))
                rows = cur.fetchall()
                cols = [d.name for d in cur.description] if cur.description else []
                if not rows:
                    return _json({
                        "error": "source_key_not_found",
                        "comp_id": comp_id,
                        "source_key": source_key,
                        "requested_identity": requested_identity,
                        "detail": "No row matched the requested source_key; refusing sibling fallback.",
                        "generated_at": ts,
                    }, status_code=404)
            cur.execute(ROW_DETAIL_SQL, (comp_id, comp_id, comp_id, comp_id, comp_id))
            sibling_rows = cur.fetchall()
            sibling_cols = [d.name for d in cur.description] if cur.description else []
            if not rows:
                rows = sibling_rows
                cols = sibling_cols
        if not rows:
            return _json({
                "error": "row_not_found",
                "comp_id": comp_id,
                "requested_identity": requested_identity,
                "detail": f"row not found: {comp_id}",
                "generated_at": ts,
            }, status_code=404)

        # Shape all candidate rows so we can pick the best source_key match.
        shaped_candidates = [_shape_row(r, cols) for r in rows]
        shaped_siblings = [_shape_row(r, sibling_cols) for r in sibling_rows] if sibling_rows else []
        all_candidates = _dedupe_candidates(shaped_candidates + shaped_siblings)

        # Prefer exact source_key match if requested. Shared, unit-tested
        # resolver. Exact source_key wins; if no source_key was supplied,
        # exact feed_observation_id wins before legacy comp_id fallback.
        chosen, exact_match = resolve_preferred_row(all_candidates, source_key, feed_observation_id)
        if chosen is None:
            return _json({
                "error": "row_not_found",
                "comp_id": comp_id,
                "requested_identity": requested_identity,
                "detail": f"row not found: {comp_id}",
                "generated_at": ts,
            }, status_code=404)

        # Drift detection: compare requested identity to resolved row.
        resolved_identity = {
            "comp_id":             chosen.get("comp_id"),
            "source_key":          chosen.get("source_key"),
            "feed_observation_id": chosen.get("feed_observation_id"),
            "review_id":           chosen.get("review_id"),
            "seller":              chosen.get("seller"),
            "auction_number":      chosen.get("auction_number"),
            "image_front_basename": chosen.get("image_front_basename"),
            "image_proof_basename": chosen.get("image_proof_basename"),
        }
        # Only compare fields the client actually sent.
        clicked_for_compare = {k: v for k, v in requested_identity.items() if v not in (None, "")}
        drift = source_drift_meta(clicked_for_compare, resolved_identity)

        # Attach row-state-aware action affordances.
        chosen["allowed_decisions"] = row_allowed_decisions(chosen)
        chosen["mazified_button"]   = mazified_button_state(chosen)
        chosen["mazified_blockers"] = mazified_blockers(chosen)
        chosen["hard_block_strip"] = row_hard_block_labels(chosen)
        chosen["market_comps"] = _market_comps_for_row(chosen)
        # Advisory audio (transcript excerpt + audio-derived identity) for the
        # AUDIO CALLOUT drawer bar. No-op when the row has no indexed audio.
        attach_audio_evidence(chosen)
        chosen["row_lookup"] = {
            "requested": requested_identity,
            "resolved":  resolved_identity,
            "exact_source_key_match": exact_match,
            "candidates_returned": len(all_candidates),
        }

        siblings = [_source_sibling_summary(c, c is chosen) for c in all_candidates]
        chosen["source_siblings"] = siblings

        with neon_conn() as c:
            cur = c.cursor()
            cur.execute(DECISIONS_SQL, (comp_id,))
            d_cols = [d.name for d in cur.description]
            decisions = [dict(zip(d_cols, r)) for r in cur.fetchall()]

        payload = {
            "row":            chosen,
            "decisions":      deep_scrub(decisions),
            "source_drift":   drift,
            "siblings":       siblings,
            "generated_at":   ts,
        }
        return _json(deep_scrub(payload))
    except Exception as e:
        return _json({
            "error": "row_failed",
            "comp_id": comp_id,
            "requested_identity": requested_identity,
            "detail": f"{type(e).__name__}: {str(e)[:300]}",
            "generated_at": ts,
        }, status_code=500)


@app.get("/api/v1/row-by-source-key")
def row_by_source_key(
    source_key: str = Query(..., description="clicked-tile source_key for exact drawer resolution"),
    comp_id: str | None = Query(None, description="clicked-tile comp_id for fallback/drift comparison"),
    feed_observation_id: int | None = Query(None, description="clicked-tile feed_observation_id for drift comparison"),
    review_id: str | None = Query(None, description="clicked-tile review_id for drift comparison"),
    seller: str | None = Query(None, description="clicked-tile seller for drift comparison"),
    auction_number: int | None = Query(None, description="clicked-tile auction_number for drift comparison"),
    image_front_basename: str | None = Query(None, description="clicked-tile front basename for drift comparison"),
    image_proof_basename: str | None = Query(None, description="clicked-tile proof basename for drift comparison"),
) -> JSONResponse:
    return row_detail(
        comp_id=comp_id or "__source_key_lookup__",
        source_key=source_key,
        feed_observation_id=feed_observation_id,
        review_id=review_id,
        seller=seller,
        auction_number=auction_number,
        image_front_basename=image_front_basename,
        image_proof_basename=image_proof_basename,
    )


@app.get("/row-view", response_class=HTMLResponse)
def row_view(source_key: str = Query(..., description="exact source_key to view")) -> HTMLResponse:
    """Read-only single-row card view for deep links outside the paged grid."""
    try:
        rows = _exec_query(
            ROW_BY_SOURCE_KEY_SQL,
            (source_key, source_key, source_key, source_key, source_key),
        )
    except Exception as exc:
        return HTMLResponse(
            status_code=500,
            content=f"<h1>row lookup failed</h1><p>{escape(type(exc).__name__)}: {escape(str(exc)[:300])}</p>",
        )
    if not rows:
        return HTMLResponse(
            status_code=404,
            content=f"<h1>row not found</h1><p><code>{escape(source_key)}</code></p>",
        )

    row = rows[0]
    title = row.get("title") or row.get("card_name") or row.get("player") or "Card row"
    descriptors = " · ".join(
        str(v)
        for v in (
            row.get("player") or row.get("card_name"),
            row.get("year"),
            row.get("brand"),
            row.get("set_name"),
            row.get("variant"),
            row.get("serial_numbered"),
            "AUTO" if _truthy(row.get("auto")) else "",
            row.get("grade") or row.get("grade_chip"),
            row.get("team"),
            row.get("sport"),
        )
        if _nonempty(v)
    )
    image_url = row.get("image_front_url") or row.get("image_proof_url") or ""
    chips = " ".join(f"<span>{escape(str(c))}</span>" for c in (row.get("chips") or []))
    kv = [
        ("source view", row.get("source_view") or row.get("observed_in")),
        ("comp_id", row.get("comp_id")),
        ("source_key", row.get("source_key")),
        ("seller", row.get("seller")),
        ("auction #", row.get("auction_number")),
        ("sold price", row.get("sold_price")),
        ("review decision", row.get("review_decision")),
        ("canonical comps", f"{len(_market_comps_for_row(row))} verified comps/context rows"),
    ]
    kv_html = "".join(
        f"<div class='kv'><b>{escape(str(k))}</b><code>{escape('' if v is None else str(v))}</code></div>"
        for k, v in kv
    )
    img_html = (
        f"<img src='{escape(str(image_url), quote=True)}' alt='front image'>"
        if image_url else "<div class='noimg'>no image</div>"
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(str(title))}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#eef1f5;color:#0f172a;margin:0;padding:22px}}
.wrap{{max-width:1180px;margin:0 auto;background:#fff;border:1px solid #d5dbe5;border-radius:10px;padding:18px}}
a{{color:#1264a3}} h1{{margin:0 0 6px;font-size:24px}} .desc{{color:#475569;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:minmax(220px,360px) 1fr;gap:18px;align-items:start}}
img{{width:100%;border-radius:8px;border:1px solid #d5dbe5;background:#f8fafc}} .chips span{{display:inline-block;margin:0 6px 6px 0;padding:4px 8px;border:1px solid #d5dbe5;border-radius:999px;background:#f8fafc;font-size:12px;font-weight:700}}
.kv{{display:grid;grid-template-columns:150px 1fr;gap:10px;border-top:1px solid #e2e8f0;padding:8px 0}} code{{white-space:pre-wrap;word-break:break-word}} .noimg{{padding:80px 20px;text-align:center;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;color:#64748b}}
</style></head><body><div class="wrap">
<p><a href="/">← MaziDex grid</a></p>
<h1>{escape(str(title))}</h1>
<div class="desc">{escape(descriptors)}</div>
<div class="chips">{chips}</div>
<div class="grid"><div>{img_html}</div><div>{kv_html}</div></div>
</div></body></html>"""
    return HTMLResponse(content=html)


# ============================================================================
# Decision history for a row
# ============================================================================
@app.get("/api/v1/decisions/{comp_id}")
def decisions_for(comp_id: str) -> dict[str, Any]:
    with neon_conn() as c:
        cur = c.cursor()
        cur.execute(DECISIONS_SQL, (comp_id,))
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return deep_scrub({
        "comp_id": comp_id,
        "decisions": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ============================================================================
# Decision write (DISABLED at v0)
# ============================================================================
@app.post("/api/v1/review-decision")
async def review_decision(request: Request) -> JSONResponse:
    """Append-only decision write. Gated CLOSED at v0.

    SAFETY-RAIL ORDERING (2026-05-29):
      1. shape validation        (400 on malformed body)
      2. strict DB-supported check (422 on unmapped semantic decisions
                                   like needs_better_image)
      3. write gate              (503 dry-run echo if gate closed)
      4. DB write                (201 with event_id)
      5. error mapping           (no CheckViolation -> 500; surfaced as 422)
    """
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "malformed_json"}, status_code=400)

    # Step 1 - payload shape
    ok, reason = validate_decision_payload(body)
    if not ok:
        return _json({"error": reason}, status_code=400)

    # Step 2 - strict DB-supported decision check (422 before any DB call)
    decision = body.get("decision") or body.get("decision_type")
    if not is_db_supported_decision(decision):
        return _json(
            {
                "error": "unsupported_decision",
                "reason": unmapped_decision_reason(decision),
                "decision": decision,
                "db_supported_decisions": sorted(DB_VALID_DECISIONS),
                "hint": (
                    "This decision string is recognized by the v0 UI but "
                    "has no DB-side handler today. Map it to one of "
                    "db_supported_decisions before posting."
                ),
            },
            status_code=422,
        )

    # Step 3 - write gate
    write_open = review_write_enabled()
    if not write_open:
        return _json(
            {
                "error": "review_write_disabled",
                "message": (
                    "v0 ships with the write gate CLOSED. Set "
                    "MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED=1 after ORCHESTRATOR "
                    "approval. Validated payload echoed below for dry-run."
                ),
                "validated_payload": deep_scrub(body),
                "would_write_to": "review_decision_events",
            },
            status_code=503,
        )

    scope_keys = review_write_scope_source_keys()
    scope_test_run_id = review_write_scope_test_run_id()
    scope_all_identified = review_write_scope_identified_all()
    if scope_keys or scope_all_identified:
        source_key = str(body.get("source_key") or "").strip()
        row_meta = body.get("row_meta") if isinstance(body.get("row_meta"), dict) else {}
        # scope_all_identified opens Confirm for any row; membership in
        # Identified is still enforced downstream by promote_identified_to_trusted
        # (raises not_in_identified_state). The static allowlist remains honored.
        key_in_scope = scope_all_identified or (source_key in scope_keys)
        if (
            decision != "confirm"
            or not key_in_scope
            or (scope_test_run_id and row_meta.get("trusted_enrichment_test_run_id") != scope_test_run_id)
        ):
            return _json(
                {
                    "error": "review_write_scope_closed",
                    "message": "Write gate is open only for Confirm Identified->Trusted promotion in the configured scope.",
                    "decision": decision,
                    "source_key": source_key,
                    "scope_identified_all": scope_all_identified,
                    "allowed_source_keys": sorted(scope_keys),
                    "required_test_run_id": scope_test_run_id,
                },
                status_code=403,
            )

    # Step 4 - scoped DB write. During this stage the write gate is only open
    # for Confirm on rows still in Identified, where Confirm means a real
    # Identified -> Trusted promotion with watermark-bound proof. Other
    # decisions stay closed until explicitly approved.
    try:
        with neon_conn() as c:
            if decision == "confirm":
                # Try Identified->Trusted first; if the row isn't in Identified,
                # fall back to the Pending->Trusted gate (watermark + single-card
                # + identity, no cert). The Identified attempt does no writes
                # before raising not_in_identified_state (only an idempotent
                # schema-ensure commit + a SELECT), so the connection is clean
                # for the fallback. The pending gate self-enforces eligibility.
                try:
                    result = promote_identified_to_trusted(c, body)
                except ValueError as identified_exc:
                    if str(identified_exc) == "not_in_identified_state":
                        result = promote_pending_to_trusted(c, body)
                    else:
                        raise
            else:
                return _json(
                    {
                        "error": "write_scope_closed",
                        "message": (
                            "The current write gate is scoped to Confirm "
                            "Identified->Trusted promotion only. Public, "
                            "Mazified, flag/reject/clear/workable writes are closed."
                        ),
                        "decision": decision,
                    },
                    status_code=403,
                )
        return _json(deep_scrub(result), status_code=201)
    except PermissionError:
        return _json({"error": "review_write_disabled"}, status_code=503)
    except ValueError as e:
        msg = str(e)
        if msg.startswith(("unmapped_semantic_decision",
                           "recognized_but_not_db_supported",
                           "unknown_decision",
                           "missing_decision")):
            return _json({"error": "unsupported_decision", "reason": msg}, status_code=422)
        return _json({"error": msg}, status_code=400)
    except Exception as e:
        type_name = type(e).__name__
        if "CheckViolation" in type_name or "IntegrityError" in type_name:
            return _json(
                {
                    "error": "db_check_violation",
                    "reason": f"{type_name}: {str(e)[:300]}",
                    "hint": "This indicates a value passed pre-check but failed at the DB. File a bug.",
                },
                status_code=422,
            )
        return _json(
            {"error": "internal_error", "type": type_name},
            status_code=500,
        )


# ============================================================================
# 8504 row actions — DELETE soft-hide (Phase 3a)
# ============================================================================
@app.post("/api/v1/row-action/delete")
async def row_action_delete(request: Request) -> JSONResponse:
    """8504 DELETE row action — Phase 3a soft-hide (reversible; NO bytes moved).

    Appends a `deleted_from_8504` event through the single write_decision INSERT
    path, keeps exactly ONE image with the row (resolve_keep_one_image), and
    writes a JSONL audit line. DELETE is scoped to identified + pending rows
    only; trusted/feed/mazified rows are rejected (409). The physical 6TB
    archive and any local unlink are Phase 3b (env MAZI_DELETE_ARCHIVE_V1) —
    nothing here deletes image bytes.

    Reversible: a later `clear` event un-hides the row (queues.py latest-decision
    exclusion). Gated CLOSED by default behind BOTH MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED
    and MAZIDEX_ADMIN_ROW_ACTIONS_WRITE_ENABLED; migration 019 must be applied or
    the DB CHECK rejects the decision (surfaced as 422).
    """
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "malformed_json"}, status_code=400)

    source_key = str(body.get("source_key") or "").strip()
    if not source_key:
        return _json({"error": "missing_source_key"}, status_code=400)
    reason = body.get("reason")
    operator = str(body.get("operator") or body.get("reviewer") or "andy").strip() or "andy"

    # Dedicated row-action write gate (independent of the Confirm promotion
    # scope). Fail-closed: BOTH flags must be set, so the route ships inert.
    if not (review_write_enabled() and row_actions_write_enabled()):
        return _json(
            {
                "error": "row_actions_write_disabled",
                "message": (
                    "8504 row-action writes are gated CLOSED. After operator "
                    "approval set MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED=1 AND "
                    "MAZIDEX_ADMIN_ROW_ACTIONS_WRITE_ENABLED=1, and apply "
                    "migration 019. Validated request echoed for dry-run."
                ),
                "validated_request": {
                    "action": "delete",
                    "source_key": source_key,
                    "reason": reason,
                },
                "would_write_to": "review_decision_events",
            },
            status_code=503,
        )

    try:
        with neon_conn() as c:
            cur = c.cursor()
            cur.execute(ROW_BY_SOURCE_KEY_SQL, (source_key,) * 5)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if not rows:
                return _json(
                    {"error": "source_key_not_found", "source_key": source_key},
                    status_code=404,
                )
            target = pick_deletable_row(rows)
            if target is None:
                found_views = sorted({
                    str(r.get("source_view") or r.get("observed_in") or "").lower()
                    for r in rows
                } - {""})
                return _json(
                    {
                        "error": "row_not_deletable",
                        "message": (
                            "DELETE is scoped to identified + pending rows only. "
                            "Trusted/feed/mazified rows are out of scope for this action."
                        ),
                        "source_key": source_key,
                        "found_source_views": found_views,
                    },
                    status_code=409,
                )
            payload = soft_delete_payload(target, reason=reason, operator=operator)
            result = write_decision(c, payload, write_enabled=True)
            record = build_delete_record(
                target,
                reason=reason,
                operator=operator,
                event_id=result.get("event_id"),
            )
            # JSONL audit line keeps the FULL local paths (written before the
            # response is privacy-scrubbed). Only filesystem write in Phase 3a.
            ledger_path = append_delete_record(record)
        return _json(
            deep_scrub({
                "status": "deleted_from_8504",
                "source_key": source_key,
                "event_id": result.get("event_id"),
                "created_at": result.get("created_at"),
                "retained_image": record.get("retained_image"),
                "ledger_path": ledger_path,
                "reversible_via": "clear",
                "no_bytes_deleted": True,
                "record": record,
            }),
            status_code=201,
        )
    except PermissionError:
        return _json({"error": "review_write_disabled"}, status_code=503)
    except ValueError as e:
        msg = str(e)
        if msg.startswith(("unmapped_semantic_decision",
                           "recognized_but_not_db_supported",
                           "unknown_decision",
                           "missing_decision",
                           "invalid_decision",
                           "missing_comp_id",
                           "missing_source_key")):
            code = 422 if msg.split(":", 1)[0] in {
                "unmapped_semantic_decision",
                "recognized_but_not_db_supported",
                "unknown_decision",
                "missing_decision",
            } else 400
            key = "unsupported_decision" if code == 422 else "invalid_payload"
            return _json({"error": key, "reason": msg}, status_code=code)
        return _json({"error": msg}, status_code=400)
    except Exception as e:
        type_name = type(e).__name__
        if "CheckViolation" in type_name or "IntegrityError" in type_name:
            return _json(
                {
                    "error": "db_check_violation",
                    "reason": f"{type_name}: {str(e)[:300]}",
                    "hint": "Apply migration 019 to allow deleted_from_8504 before posting.",
                },
                status_code=422,
            )
        return _json({"error": "internal_error", "type": type_name}, status_code=500)


# ============================================================================
# SWAP FRONT row action (Phase 4) — next-best front rebind (identified+pending)
# ============================================================================
# The engine (whatnot-sniper-m4/ops/swap_front_for_source_key.py) always emits a
# status string; the route maps it to an HTTP code. Unmapped → 500.
def _swap_status_code(status: str) -> int:
    if status == "swapped":
        return 201  # real rebind written (frame re-stamped + folded into master)
    if status in ("swap_prepared", "no_alternate_front_available"):
        return 200  # dry-run preview, or explicit valid no-op (nothing mutated)
    if status in ("no_lot_found", "refused_trusted_row"):
        return 409  # on-disk detector lot missing / not a swappable surface
    if status == "row_not_found":
        return 404
    if status == "unparseable_source_key":
        return 400
    if status == "integrity_refused_verified" or status.startswith("build_failed"):
        return 422  # invariant guard tripped / record build failed
    return 500


def _swap_entry_from_row(row: dict) -> dict:
    """Minimal engine `entry` from a resolved 8504 DB row (avoids the 1.1GB master).

    r2b `build_record` reads `comp_id` (→ pid/auction + canonical filename), and
    optionally `capture_quality` (carried forward) + `api_scan` (watermark
    re-verify); the orchestrator reads `comp_id`/`front_image` + the view fields.
    `raw_full` (the row's `raw` JSONB) already carries evidence_stamp/capture_quality/
    api_scan; the authoritative DB columns are overlaid on top so a stale
    `raw.observed_in`/`raw.front_image` can never mislead the view gate.
    """
    raw = row.get("raw_full")
    entry: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    entry["comp_id"] = row.get("comp_id")
    if row.get("source_view") is not None:
        entry["source_view"] = row.get("source_view")
    if row.get("observed_in") is not None:
        entry["observed_in"] = row.get("observed_in")
    if row.get("front_image"):
        entry["front_image"] = row.get("front_image")
    return entry


def _parse_engine_json(stdout: str) -> "dict | None":
    """Return the LAST top-level JSON object printed by the engine.

    `--merge` runs merge_captures.py first (its output may precede the result
    dict), so scan the whole stream and keep the final decodable object.
    """
    s = stdout or ""
    dec = json.JSONDecoder()
    last: "dict | None" = None
    idx, n = 0, len(s)
    while idx < n:
        brace = s.find("{", idx)
        if brace < 0:
            break
        try:
            obj, end = dec.raw_decode(s, brace)
            if isinstance(obj, dict):
                last = obj
            idx = end
        except json.JSONDecodeError:
            idx = brace + 1
    return last


@app.post("/api/v1/row-action/swap-front")
async def row_action_swap_front(request: Request) -> JSONResponse:
    """8504 SWAP FRONT row action — Phase 4 next-best front rebind.

    Rebinds an identified/pending row to its NEXT-BEST front frame (highest
    front-candidate score EXCLUDING the currently-bound frame) from the card's
    on-disk detector-select lot, re-stamps it with the row's auction number, and
    emits a comp_id `_update` rebind (folded into the master via merge_captures).
    NEVER promotes, NEVER sets `verified`. No alternate frame → explicit no-op.
    Scoped to identified + pending rows only (trusted/feed/mazified → 409).

    The engine (ops/swap_front_for_source_key.py) drags `ijson` via the r2b
    importer, so it runs OUT-OF-PROCESS under the whatnot-sniper-m4 venv; the route
    hands it a DB-row-derived minimal entry via `--entry-json` (never the 1.1GB
    master). A real (writing) swap requires an explicit per-call `confirm: true`
    AND sets MAZI_IMAGE_BACKFILL_V1=1 for the subprocess — done ONLY when the write
    gate is open. Any request without `confirm` (or with `dry_run: true`) runs as a
    no-write preview.

    Gated CLOSED by default behind BOTH MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED and
    MAZIDEX_ADMIN_ROW_ACTIONS_WRITE_ENABLED (fail-closed 503 echo).
    """
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "malformed_json"}, status_code=400)

    source_key = str(body.get("source_key") or "").strip()
    if not source_key:
        return _json({"error": "missing_source_key"}, status_code=400)

    # Fail-safe: a real (writing) swap requires an explicit per-call confirm. An
    # explicit dry_run, or a bare request with no confirm, runs as a preview.
    do_write = bool(body.get("confirm")) and not bool(body.get("dry_run"))
    effective_dry_run = not do_write

    # Dedicated row-action write gate — fail-closed (BOTH flags required).
    if not (review_write_enabled() and row_actions_write_enabled()):
        return _json(
            {
                "error": "row_actions_write_disabled",
                "message": (
                    "8504 row-action writes are gated CLOSED. After operator "
                    "approval set MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED=1 AND "
                    "MAZIDEX_ADMIN_ROW_ACTIONS_WRITE_ENABLED=1. A real swap also "
                    "needs MAZI_IMAGE_BACKFILL_V1=1 and confirm:true. Echoed."
                ),
                "validated_request": {
                    "action": "swap_front",
                    "source_key": source_key,
                    "would_write": do_write,
                },
                "would_write_to": "whatnot_cards image store + master (merge_captures)",
            },
            status_code=503,
        )

    # Resolve the row + confirm it's a swappable (identified/pending) surface.
    try:
        with neon_conn() as c:
            cur = c.cursor()
            cur.execute(ROW_BY_SOURCE_KEY_SQL, (source_key,) * 5)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        return _json(
            {"error": "internal_error", "type": type(e).__name__}, status_code=500
        )

    if not rows:
        return _json(
            {"error": "source_key_not_found", "source_key": source_key},
            status_code=404,
        )
    # Shared identified/pending surface picker (same scoping as DELETE). None ⇒
    # the key resolves only to trusted/feed/mazified → not swappable.
    target = pick_deletable_row(rows)
    if target is None:
        found_views = sorted({
            str(r.get("source_view") or r.get("observed_in") or "").lower()
            for r in rows
        } - {""})
        return _json(
            {
                "error": "row_not_swappable",
                "message": (
                    "SWAP FRONT is scoped to identified + pending rows only. "
                    "Trusted/feed/mazified rows are out of scope for this action."
                ),
                "source_key": source_key,
                "found_source_views": found_views,
            },
            status_code=409,
        )

    entry = _swap_entry_from_row(target)

    # Run the engine out-of-process under the whatnot-sniper-m4 venv (ijson wall).
    import subprocess
    import tempfile

    engine = WHATNOT_DIR / "ops" / "swap_front_for_source_key.py"
    py = WHATNOT_DIR / "venv" / "bin" / "python"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump(entry, fh, default=str)
            tmp_path = fh.name
        cmd = [
            str(py), str(engine),
            "--source-key", source_key,
            "--entry-json", tmp_path,
            "--json",
        ]
        env = dict(os.environ)
        if effective_dry_run:
            cmd.append("--dry-run")
        else:
            cmd.append("--merge")
            # Tier-3 real-swap image write. Set ONLY here (gate verified open above).
            env["MAZI_IMAGE_BACKFILL_V1"] = "1"
        proc = subprocess.run(
            cmd, cwd=str(WHATNOT_DIR), env=env,
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return _json(
            {"error": "engine_timeout", "source_key": source_key}, status_code=504
        )
    except Exception as e:
        return _json(
            {"error": "engine_launch_failed", "type": type(e).__name__,
             "detail": str(e)[:300]},
            status_code=500,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    result = _parse_engine_json(proc.stdout)
    if not isinstance(result, dict):
        return _json(
            {
                "error": "engine_bad_output",
                "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[-600:],
                "stdout": (proc.stdout or "")[-600:],
            },
            status_code=502,
        )

    status = str(result.get("status") or "")
    return _json(
        deep_scrub({
            "action": "swap_front",
            "source_key": source_key,
            "dry_run": effective_dry_run,
            "write_committed": do_write and status == "swapped",
            **result,
        }),
        status_code=_swap_status_code(status),
    )


# ============================================================================
# Privacy self-check (used by acceptance tests)
# ============================================================================
@app.get("/api/v1/privacy-self-check")
def privacy_self_check() -> dict[str, Any]:
    """Run a working-queue read, scan the payload for leaks, return scan."""
    sql, params = queue_sql("working", 100)
    rows = _exec_query(sql, params)
    return privacy_scan_payload(rows)


# ============================================================================
# SPA entrypoint
# ============================================================================
@app.get("/", response_class=HTMLResponse)
def index_html() -> HTMLResponse:
    # no-store: the SPA entrypoint must never be cached, otherwise browsers pin a
    # stale copy that references an old ?v= asset token and keep loading outdated
    # app.js/app.css even after a soft reload. The HTML is tiny, so always re-fetch.
    no_cache = {"Cache-Control": "no-store"}
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        return HTMLResponse(
            content="<h1>mazidex-admin v0</h1><p>static/index.html missing</p>",
            status_code=200,
            headers=no_cache,
        )
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200, headers=no_cache)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=BIND_HOST,
        port=BIND_PORT,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
