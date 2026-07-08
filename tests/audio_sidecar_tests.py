"""mazidex-admin v0 — advisory-audio sidecar tests.

Scope: audio_evidence_sidecar.{_slim_row, build_index, load_index, attach_audio}
       — the bridge that feeds the drawer AUDIO CALLOUT bar from the M4 monolith
       (Neon carries no audio). Advisory-only; must never leak internal file
       paths / job internals and must never clobber an existing row value.

Mode:  Pure-function tests over synthetic dicts + a temp index file. NO Neon
       hit, NO monolith read, NO 8504 HTTP hit. Safe to run during live QA.

Usage: cd ~/mazidex-admin && ./venv/bin/python3 tests/audio_sidecar_tests.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import audio_evidence_sidecar as S

_F: list[str] = []


def case(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _F.append(name)


# A realistic monolith audio row, incl. the internal plumbing we must NOT surface.
AUDIO_ROW = {
    "comp_id": "MC-10204",
    "audio_transcript": {
        "path": "/Users/stavrosaimini/whatnot-sniper/captures/audio_evidence/transcripts/x.json",
        "ok": True, "model": "base.en", "language": "en",
        "duration": "103.2", "segments": 14,
        "text_excerpt": "mega go mega sharpie EX live let's go guys three bucks",
    },
    "audio_ocr": {
        "brand": "Pokemon", "set_name": "Mega Sharpie EX", "player": None,
        "evidence_snippets": ["mega EX", "three bucks"],
        "grade_company": None, "grade_value": None,
    },
    "audio_reconciliation": {
        "verified_source": "audio_only", "final_confidence": "0.225",
        "match_score": "0.0", "agreed_fields": [], "conflicts": [],
    },
    "audio_evidence_status": "verified",
    "audio_evidence": {"status": "verified", "audio_job_id": "SECRET_JOB_123"},
    "audio_transcript_path": "/Users/stavrosaimini/whatnot-sniper/captures/audio_evidence/transcripts/x.json",
}
NO_AUDIO_ROW = {"comp_id": "MC-99999", "player": "Nobody"}


# ── 1. _slim_row keeps advisory content ─────────────────────────────────────
slim = S._slim_row(AUDIO_ROW)
case("1a slim keeps transcript excerpt",
     slim["audio_transcript"]["text_excerpt"].startswith("mega go"), detail=str(slim))
case("1b slim keeps audio brand + set", slim["audio_ocr"]["brand"] == "Pokemon"
     and slim["audio_ocr"]["set_name"] == "Mega Sharpie EX")
case("1c slim keeps evidence snippets", slim["audio_ocr"]["evidence_snippets"] == ["mega EX", "three bucks"])
case("1d slim keeps recon verified_source + confidence",
     slim["audio_reconciliation"]["verified_source"] == "audio_only"
     and slim["audio_reconciliation"]["final_confidence"] == "0.225")
case("1e slim keeps audio_evidence_status", slim.get("audio_evidence_status") == "verified")
case("1f slim drops empty audio_ocr.player (not None-valued)", "player" not in slim["audio_ocr"])

# ── 2. Privacy: no file paths, no job internals ─────────────────────────────
blob = json.dumps(slim)
case("2a slim NEVER contains a /Users/ path", "/Users/" not in blob, detail=blob[:200])
case("2b slim drops transcript.path", "path" not in slim["audio_transcript"])
case("2c slim drops audio_job_id / audio_evidence dict", "audio_job_id" not in blob
     and "audio_evidence" not in slim)

# ── 3. No-audio row -> None (stays out of the index) ────────────────────────
case("3 no-audio row slims to None", S._slim_row(NO_AUDIO_ROW) is None)

# ── 4. build_index + load_index round-trip ──────────────────────────────────
tmpdir = tempfile.mkdtemp(prefix="audio_sidecar_test_")
mono_path = os.path.join(tmpdir, "monolith.json")
idx_path = os.path.join(tmpdir, "audio_by_comp_id.json")
with open(mono_path, "w", encoding="utf-8") as fh:
    json.dump([AUDIO_ROW, NO_AUDIO_ROW, {"not": "a dict"}, {"comp_id": None}], fh)
n = S.build_index(mono_path, idx_path)
case("4a build_index counts only audio rows", n == 1, detail=f"n={n}")
loaded = S.load_index(idx_path)
case("4b load_index returns the audio row by comp_id", "MC-10204" in loaded and "MC-99999" not in loaded)
case("4c missing index path -> empty dict", S.load_index(os.path.join(tmpdir, "nope.json")) == {})

# ── 5. attach_audio behavior ────────────────────────────────────────────────
r = {"comp_id": "MC-10204", "title": "some card"}
S.attach_audio(r, idx_path)
case("5a attach fills audio_transcript by comp_id", r.get("audio_transcript", {}).get("text_excerpt", "").startswith("mega go"))
case("5b attach fills audio_ocr", r.get("audio_ocr", {}).get("brand") == "Pokemon")

r_absent = {"comp_id": "MC-00000"}
S.attach_audio(r_absent, idx_path)
case("5c attach is a no-op when comp_id not indexed", "audio_transcript" not in r_absent)

case("5d attach no-op on non-dict", S.attach_audio("nope", idx_path) == "nope")
case("5e attach no-op when row lacks comp_id", S.attach_audio({"x": 1}, idx_path) == {"x": 1})

# never clobbers an existing non-empty value
r_pre = {"comp_id": "MC-10204", "audio_transcript": {"text_excerpt": "OPERATOR EDIT"}}
S.attach_audio(r_pre, idx_path)
case("5f attach never clobbers an existing value",
     r_pre["audio_transcript"]["text_excerpt"] == "OPERATOR EDIT")

# attach must hand out COPIES: mutating a served row must not poison the cache
r_mut = {"comp_id": "MC-10204"}
S.attach_audio(r_mut, idx_path)
r_mut["audio_ocr"]["brand"] = "POISONED"
r_fresh = {"comp_id": "MC-10204"}
S.attach_audio(r_fresh, idx_path)
case("5g attach deep-copies (row mutation cannot poison the cache)",
     r_fresh["audio_ocr"]["brand"] == "Pokemon",
     detail=f"got {r_fresh.get('audio_ocr', {}).get('brand')!r}")

# ── 6. Link-suspect guard (cross-sale contamination) ────────────────────────
# Recycled auction number: job clip recorded ~4.85h after the record's sale.
SUSPECT_ROW = {
    "comp_id": "MC-BAD-1",
    "audio_transcript": {"text_excerpt": "wrong card words entirely"},
    "audio_ocr": {"brand": "Pokemon", "evidence_snippets": ["mega EX"]},
    "audio_reconciliation": {"verified_source": "audio_only"},
    "audio_evidence": {
        "status": "verified",
        "processed_at": "2026-05-15T05:08:02+00:00",
        "audio_job_link_key": "rarebreedcandc|151|2026-05-14T19:21:54",
        "audio_record_link_key": "rarebreedcandc|151|2026-05-14T14:30:38",
    },
}
slim_bad = S._slim_row(SUSPECT_ROW)
case("6a suspect fuse (>120s delta) flags audio_link_suspect",
     slim_bad.get("audio_link_suspect") is True, detail=str(slim_bad))
case("6b suspect payload SUPPRESSES transcript + identity",
     "audio_transcript" not in slim_bad and "audio_ocr" not in slim_bad
     and "audio_reconciliation" not in slim_bad, detail=str(slim_bad))
case("6c suspect delta surfaced in seconds",
     abs(slim_bad.get("audio_link_delta_seconds", 0) - 17476) < 2, detail=str(slim_bad))

# tight fuse (<=120s) stays normal
GOOD_LINK_ROW = json.loads(json.dumps(SUSPECT_ROW))
GOOD_LINK_ROW["audio_evidence"]["audio_job_link_key"] = "rarebreedcandc|151|2026-05-14T14:31:10"
slim_good = S._slim_row(GOOD_LINK_ROW)
case("6d tight fuse (32s) keeps transcript + identity",
     slim_good.get("audio_link_suspect") is None and "audio_transcript" in slim_good)

# missing link keys (older row shape) -> normal, never suspect
NO_KEYS_ROW = json.loads(json.dumps(SUSPECT_ROW))
del NO_KEYS_ROW["audio_evidence"]["audio_job_link_key"]
slim_nokeys = S._slim_row(NO_KEYS_ROW)
case("6e missing link keys -> treated normal (no suspect flag)",
     slim_nokeys.get("audio_link_suspect") is None and "audio_transcript" in slim_nokeys)

# ── 7. processed_at passthrough (pre-fix cue support) ───────────────────────
case("7a processed_at surfaced as audio_processed_at",
     slim_good.get("audio_processed_at") == "2026-05-15T05:08:02+00:00")
case("7b suspect payload also carries processed_at",
     slim_bad.get("audio_processed_at") == "2026-05-15T05:08:02+00:00")

# ── 8. Hot-first master selection ───────────────────────────────────────────
hot = os.path.join(tmpdir, "hot.json")
repo = os.path.join(tmpdir, "repo.json")
with open(repo, "w") as fh:
    fh.write("[]")
case("8a resolver falls back to repo copy when hot absent",
     S._default_monolith((hot, repo)) == repo)
with open(hot, "w") as fh:
    fh.write("[]")
case("8b resolver prefers hot copy when present",
     S._default_monolith((hot, repo)) == hot)

# ── 9. build_index _meta provenance + suspect count ─────────────────────────
mono2 = os.path.join(tmpdir, "mono2.json")
idx2 = os.path.join(tmpdir, "idx2.json")
with open(mono2, "w", encoding="utf-8") as fh:
    json.dump([AUDIO_ROW, SUSPECT_ROW, NO_AUDIO_ROW], fh)
n2 = S.build_index(mono2, idx2)
loaded2 = json.load(open(idx2))
meta = loaded2.get("_meta", {})
case("9a build_index count excludes _meta", n2 == 2, detail=f"n={n2}")
case("9b _meta records source path", meta.get("source") == mono2, detail=str(meta))
case("9c _meta counts suspect rows", meta.get("link_suspect_rows") == 1, detail=str(meta))
case("9d attach ignores _meta (no comp_id collision)",
     S.attach_audio({"comp_id": "_meta"}, idx2).get("rows") is None)

# ── 10. Player-only audio still indexes (frontend gate alignment) ───────────
PLAYER_ONLY = {"comp_id": "MC-PONLY", "audio_ocr": {"player": "Donovan Mitchell"}}
slim_p = S._slim_row(PLAYER_ONLY)
case("10 player-only OCR row still indexed with identity",
     slim_p is not None and slim_p["audio_ocr"]["player"] == "Donovan Mitchell")

# ── 11. _field_tags: CONFIRMED / CONFLICT / FILLED classification ───────────
# both_agree shape (real: MC-20260613233259-p16035-0002-a22)
TAG_ROW = {
    "comp_id": "MC-TAGS",
    "card_ocr": {"player": "BOBBY WITT JR.", "year": "2022"},
    "api_scan": {},
    "audio_ocr": {"player": "Bobby Witt Jr", "year": "2022", "parallel": "refractor",
                  "set_name": "Topps Chrome", "evidence_snippets": ["Bobby Witt refractor"]},
    "audio_reconciliation": {
        "verified_source": "both_agree", "match_score": 1.0,
        "agreed_fields": ["player"],
        "conflicts": [],
    },
}
tags = S._field_tags(TAG_ROW, TAG_ROW["audio_ocr"], TAG_ROW["audio_reconciliation"])
case("11a agreed_fields -> confirmed", tags.get("confirmed") == ["player"], detail=str(tags))
case("11b image-blank audio fields -> filled (parallel + set_name)",
     set(tags.get("filled", {})) == {"parallel", "set_name"}, detail=str(tags))
case("11c image-present field (year) NOT filled", "year" not in tags.get("filled", {}),
     detail=str(tags))

# conflict shape (real: MC-20260613200801-p28122-0002-a154) — image wins
CONFLICT_ROW = {
    "comp_id": "MC-CONF",
    "card_ocr": {"player": "DEVON ACHANE"},
    "audio_ocr": {"player": "Drake London"},
    "audio_reconciliation": {
        "verified_source": "both_disagree_image_won",
        "conflicts": [{"field": "player", "image_value": "DEVON ACHANE",
                       "audio_value": "Drake London", "key_field": True}],
    },
}
ctags = S._field_tags(CONFLICT_ROW, CONFLICT_ROW["audio_ocr"], CONFLICT_ROW["audio_reconciliation"])
case("11d conflict surfaced with image+audio values",
     ctags.get("conflicts") == [{"field": "player", "image_value": "DEVON ACHANE",
                                 "audio_value": "Drake London"}], detail=str(ctags))
case("11e conflicted field never doubles as filled", "player" not in ctags.get("filled", {}))

# alias coverage: card_ocr.card_set blocks set_name fill; api_scan.variant blocks parallel
ALIAS_ROW = {
    "comp_id": "MC-ALIAS",
    "card_ocr": {"card_set": "Prizm"},
    "api_scan": {"variant": "silver"},
    "audio_ocr": {"set_name": "Prizm", "parallel": "silver", "brand": "Panini"},
    "audio_reconciliation": {"verified_source": "audio_only"},
}
atags = S._field_tags(ALIAS_ROW, ALIAS_ROW["audio_ocr"], ALIAS_ROW["audio_reconciliation"])
case("11f image aliases block false fills (card_set/variant)",
     set(atags.get("filled", {})) == {"brand"}, detail=str(atags))

# THE a940 protection: stale audio_only recon + image identity present ->
# audio player must get NO tag (not confirmed, not conflict, NOT filled)
A940_ROW = {
    "comp_id": "MC-A940",
    "card_ocr": {"player": "JA'MARR CHASE"},
    "audio_ocr": {"player": "Dan Marino"},
    "audio_reconciliation": {"verified_source": "audio_only", "agreed_fields": [], "conflicts": []},
}
n940 = S._field_tags(A940_ROW, A940_ROW["audio_ocr"], A940_ROW["audio_reconciliation"])
case("11g stale-recon row with image identity: audio player gets NO tag",
     n940 == {}, detail=str(n940))

# ── 12. _slim_row integration: tags land in the payload ─────────────────────
slim_tags = S._slim_row(TAG_ROW)
case("12a audio_field_tags present in slim payload",
     slim_tags.get("audio_field_tags", {}).get("confirmed") == ["player"])
slim_none = S._slim_row(A940_ROW)
case("12b empty tags omitted from payload", "audio_field_tags" not in slim_none)
case("12c raw agreed_fields/conflicts still pass through in reconciliation",
     slim_tags.get("audio_reconciliation", {}).get("agreed_fields") == ["player"])

# ── 11. Reconciliation chips data passes through slim ───────────────────────
RECON_ROW = {
    "comp_id": "MC-RECON",
    "audio_transcript": {"text_excerpt": "Devon Achane rookie live"},
    "audio_ocr": {"player": "Drake London", "parallel": "refractor"},
    "audio_reconciliation": {
        "verified_source": "both_disagree_image_won",
        "final_confidence": 0.225,
        "match_score": 0.0,
        "agreed_fields": ["year"],
        "conflicts": [{"field": "player", "image_value": "DEVON ACHANE",
                       "audio_value": "Drake London", "key_field": True}],
        "reconciled_fields": {"player": "DEVON ACHANE"},
    },
}
slim_r = S._slim_row(RECON_ROW)
recon_slim = slim_r.get("audio_reconciliation", {})
case("11a agreed_fields passes through slim", recon_slim.get("agreed_fields") == ["year"])
case("11b conflicts pass through slim (with image/audio values)",
     recon_slim.get("conflicts", [{}])[0].get("image_value") == "DEVON ACHANE"
     and recon_slim.get("conflicts", [{}])[0].get("audio_value") == "Drake London")
case("11c bulky reconciled_fields NOT indexed (derivable from row)",
     "reconciled_fields" not in recon_slim)
case("11d empty agreed/conflicts dropped (no noise keys)",
     "agreed_fields" not in (S._slim_row(AUDIO_ROW) or {}).get("audio_reconciliation", {}))

print()
if _F:
    print(f"{len(_F)} FAILED: {_F}")
    sys.exit(1)
print("ALL TESTS PASSED")
sys.exit(0)
