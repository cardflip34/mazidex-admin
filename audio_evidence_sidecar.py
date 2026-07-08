"""Self-contained advisory-audio sidecar for the 8504 workbench drawer.

8504 reads Neon, but advisory audio evidence (the spoken auction-call transcript
excerpt + the audio-derived identity Gemini pulled from it) is written ONLY onto
the whatnot_auctions.json master rows, never into Neon. This module bridges that
gap without touching Neon or the production 9009->Neon import:

  * build_index()  -- read-only pass over the master -> compact comp_id index.
  * load_index()   -- lazy, mtime-invalidated in-process cache.
  * attach_audio() -- attach the indexed fields onto a row dict at request time.

Master selection mirrors ops/audio_evidence_m4_dispatch.sh: the hot-runtime copy
(/Volumes/MAZI_4TB_SSD/m4_live/whatnot_auctions.json) wins when it exists, else
the repo copy. The chosen source is recorded in the index under "_meta" so
source drift is auditable.

ADVISORY ONLY. These fields never promote Trusted / Mazified / public identity
(they mirror the upstream `trust_gate_effect=none`, image-wins policy). Internal
file paths (`*_path`) and audio-job internals are deliberately NOT indexed, so
nothing here can leak a `/Users/...` path or job plumbing into the drawer.

LINK-SUSPECT GUARD (2026-07-03 audit): the upstream seller_auction_time linker
has fused clips from a DIFFERENT sale onto a row when an auction number was
recycled hours later (measured bimodal: 818 fuses <=60s apart vs 23 fuses
4.3-4.9h apart, nothing in between). When the audio_job_link_key and
audio_record_link_key timestamps disagree by more than _LINK_SUSPECT_SECONDS,
the row is indexed as {audio_link_suspect: true, ...} WITHOUT transcript or
identity, so the drawer shows an explicit warning instead of wrong-card
evidence. Rows lacking link keys are treated as normal (older shape).
"""
from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime
from typing import Any

_HOT_MONOLITH = "/Volumes/MAZI_4TB_SSD/m4_live/whatnot_auctions.json"
_REPO_MONOLITH = "/Users/stavrosaim4/whatnot-sniper-m4/whatnot_auctions.json"
_INDEX_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "audio_by_comp_id.json"
)

# Fuse-time disagreement beyond which the audio binding is treated as suspect.
# Measured distribution is bimodal (<=60s good, >4.3h bad, empty in between),
# so any threshold in the gap works; 120s leaves headroom for clock skew.
_LINK_SUSPECT_SECONDS = 120.0

# Only these fields ever reach the drawer bar. No file paths, no job internals.
_TR_KEYS = ("text_excerpt", "duration", "model", "segments", "language")
_OCR_KEYS = (
    "player", "brand", "set_name", "parallel", "year", "serial",
    "grade_value", "grade_company", "rookie_status", "signature_subject",
    "evidence_snippets",
)
# agreed_fields / conflicts power the drawer's per-field CONFIRMED / CONFLICT /
# AUDIO-FILL chips ("which audio tags verified or filled the Gemini identity").
_RECON_KEYS = (
    "verified_source", "final_confidence", "match_score",
    "agreed_fields", "conflicts",
)

_EMPTY = (None, "", [], {})

# Image-identity aliases per audio_ocr field, used to classify AUDIO FILL (audio
# supplied a value the row's image identity — card_ocr AND api_scan — lacks).
# Checked against the row's CURRENT image identity, not the possibly-stale
# snapshot reconcile() saw, so late-arriving Gemini image OCR demotes a fill.
_IMAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "player": ("player", "name"),
    "year": ("year",),
    "brand": ("brand",),
    "set_name": ("set_name", "card_set"),
    "parallel": ("parallel", "variant", "insert_type"),
    "serial": ("serial", "serial_numbered"),
    "grade_value": ("grade_value", "grade"),
    "grade_company": ("grade_company", "grading_company"),
}


def _field_tags(row: dict[str, Any], ocr: dict[str, Any], recon: dict[str, Any]) -> dict[str, Any]:
    """Per-field reconciliation tags for the drawer callout.

    confirmed: fields where audio AGREED with the Gemini image identity
               (reconciler agreed_fields — fuzzy player match, exact year, ...).
    conflicts: fields where both sources had values and disagreed; image wins.
    filled:    audio field -> value where the row's image identity (card_ocr
               and api_scan) is blank — audio is the only identity candidate.
    """
    agreed = [str(f) for f in (recon.get("agreed_fields") or []) if f]
    conflicts = [
        {
            "field": str(c.get("field") or ""),
            "image_value": c.get("image_value"),
            "audio_value": c.get("audio_value"),
        }
        for c in (recon.get("conflicts") or [])
        if isinstance(c, dict) and c.get("field")
    ]
    tagged = set(agreed) | {c["field"] for c in conflicts}

    card_ocr = row.get("card_ocr") or {}
    api_scan = row.get("api_scan") or {}
    if not isinstance(card_ocr, dict):
        card_ocr = {}
    if not isinstance(api_scan, dict):
        api_scan = {}

    filled: dict[str, Any] = {}
    for field, aliases in _IMAGE_ALIASES.items():
        if field in tagged:
            continue
        val = ocr.get(field)
        if val in _EMPTY:
            continue
        image_has = any(
            card_ocr.get(a) not in _EMPTY or api_scan.get(a) not in _EMPTY
            for a in aliases
        )
        if not image_has:
            filled[field] = val

    out: dict[str, Any] = {}
    if agreed:
        out["confirmed"] = agreed
    if conflicts:
        out["conflicts"] = conflicts
    if filled:
        out["filled"] = filled
    return out


def _default_monolith(candidates: tuple[str, ...] = (_HOT_MONOLITH, _REPO_MONOLITH)) -> str:
    """Mirror the dispatch script's master selection: hot-runtime copy first."""
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return candidates[-1]


def _link_key_ts(key: Any) -> datetime | None:
    """Parse the ISO timestamp tail of a 'seller|auction|2026-05-14T19:21:54' key."""
    if not isinstance(key, str) or "|" not in key:
        return None
    tail = key.rsplit("|", 1)[-1].strip()
    try:
        return datetime.fromisoformat(tail.replace("Z", "+00:00"))
    except ValueError:
        return None


def _link_delta_seconds(evidence: dict[str, Any]) -> float | None:
    """Seconds between the audio job's link key and the record's link key.

    None when either key is missing/unparseable (older row shapes) — those rows
    are treated as normal, not suspect.
    """
    job_ts = _link_key_ts(evidence.get("audio_job_link_key"))
    rec_ts = _link_key_ts(evidence.get("audio_record_link_key"))
    if job_ts is None or rec_ts is None:
        return None
    if (job_ts.tzinfo is None) != (rec_ts.tzinfo is None):
        job_ts = job_ts.replace(tzinfo=None)
        rec_ts = rec_ts.replace(tzinfo=None)
    return abs((job_ts - rec_ts).total_seconds())


def _slim_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the minimal advisory-audio payload for a master row, or None.

    None when the row carries no usable audio evidence (so absent-audio rows
    stay out of the index entirely and the drawer shows its honest placeholder).
    Suspect-linked rows return a warning payload WITHOUT transcript/identity.
    """
    tr = row.get("audio_transcript") or {}
    ocr = row.get("audio_ocr") or {}
    recon = row.get("audio_reconciliation") or {}
    evidence = row.get("audio_evidence") or {}
    if not isinstance(tr, dict):
        tr = {}
    if not isinstance(ocr, dict):
        ocr = {}
    if not isinstance(recon, dict):
        recon = {}
    if not isinstance(evidence, dict):
        evidence = {}

    has_audio = (
        tr.get("text_excerpt")
        or ocr.get("evidence_snippets")
        or ocr.get("player")
        or recon.get("verified_source")
    )
    if not has_audio:
        return None

    status = row.get("audio_evidence_status") or evidence.get("status")
    processed_at = evidence.get("processed_at")

    delta = _link_delta_seconds(evidence)
    if delta is not None and delta > _LINK_SUSPECT_SECONDS:
        # Wrong-card binding (recycled auction number hours later). Suppress the
        # transcript/identity; surface only the warning so the drawer never
        # presents another sale's evidence as this row's audio.
        out: dict[str, Any] = {
            "audio_link_suspect": True,
            "audio_link_delta_seconds": round(delta),
        }
        if status:
            out["audio_evidence_status"] = status
        if processed_at:
            out["audio_processed_at"] = processed_at
        return out

    slim_tr = {k: tr[k] for k in _TR_KEYS if tr.get(k) not in _EMPTY}
    slim_ocr = {k: ocr[k] for k in _OCR_KEYS if ocr.get(k) not in _EMPTY}
    slim_recon = {k: recon[k] for k in _RECON_KEYS if recon.get(k) not in _EMPTY}
    tags = _field_tags(row, ocr, recon)

    out = {}
    if slim_tr:
        out["audio_transcript"] = slim_tr
    if slim_ocr:
        out["audio_ocr"] = slim_ocr
    if slim_recon:
        out["audio_reconciliation"] = slim_recon
    if tags:
        out["audio_field_tags"] = tags
    if status:
        out["audio_evidence_status"] = status
    if processed_at:
        out["audio_processed_at"] = processed_at
    return out or None


def build_index(
    monolith_path: str | None = None,
    out_path: str = _INDEX_DEFAULT,
) -> int:
    """Build the comp_id -> advisory-audio index from the master. Returns count.

    Atomic write (temp + os.replace) so a concurrent load_index() never sees a
    half-written file. Read-only w.r.t. the master. NOTE: json.load's the full
    ~1.4GB master (multi-GB transient RAM) — schedule regens off-peak.
    """
    src = monolith_path or _default_monolith()
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else list(data.values())

    idx: dict[str, Any] = {}
    suspect = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = row.get("comp_id")
        if not cid:
            continue
        slim = _slim_row(row)
        if slim:
            idx[str(cid)] = slim
            if slim.get("audio_link_suspect"):
                suspect += 1

    idx["_meta"] = {
        "source": src,
        "source_mtime": os.path.getmtime(src),
        "rows": len(idx),
        "link_suspect_rows": suspect,
        "link_suspect_threshold_seconds": _LINK_SUSPECT_SECONDS,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, out_path)
    return len(idx) - 1  # exclude _meta


_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_mtime: float | None = None


def load_index(index_path: str = _INDEX_DEFAULT) -> dict[str, Any]:
    """Return the cached index, reloading only when the file mtime changes.

    Missing/broken index -> empty dict (drawer degrades to its placeholder).
    """
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(index_path)
    except OSError:
        return {}
    with _lock:
        if _cache is not None and _cache_mtime == mtime:
            return _cache
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            _cache = loaded if isinstance(loaded, dict) else {}
        except Exception:
            _cache = {}
        _cache_mtime = mtime
        return _cache


def attach_audio(row: dict[str, Any], index_path: str = _INDEX_DEFAULT) -> dict[str, Any]:
    """Mutate `row` in place, attaching advisory audio fields by comp_id.

    No-op when `row` is not a dict, lacks a comp_id, or has no indexed audio.
    Never clobbers a non-empty value already present on the row. Attaches
    DEEP COPIES so later in-place mutation of the response row can never
    poison the shared process-wide cache.
    """
    if not isinstance(row, dict):
        return row
    cid = row.get("comp_id")
    # Underscore-prefixed keys are index metadata (_meta), never audio payloads.
    if not cid or str(cid).startswith("_"):
        return row
    audio = load_index(index_path).get(str(cid))
    if not audio:
        return row
    for key, value in audio.items():
        if not row.get(key):
            row[key] = copy.deepcopy(value)
    return row


if __name__ == "__main__":
    import sys

    mono = sys.argv[1] if len(sys.argv) > 1 else None
    n = build_index(mono)
    src = mono or _default_monolith()
    print(f"indexed {n} audio rows from {src} -> {_INDEX_DEFAULT}")
