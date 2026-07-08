"""Front-image display resolution — CDN must never mask a real capture.

Standing gate (CLAUDE.md): CDN/listing images are never display/proof evidence.
Display priority: captured card_ front -> stamped _mazi -> recovered
ops_display_image -> suppress. CDN and proof_ never resolve as a front.

Run: venv/bin/python tests/front_image_resolution_tests.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import _cdn_basename, _proof_only_basename, _resolve_display_images

_fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _fail
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        _fail += 1


def resolve(**fields) -> dict:
    row: dict = dict(fields)
    _resolve_display_images(row)
    return row


print("== _cdn_basename predicate ==")
check("bare cdn_ basename is CDN", _cdn_basename("cdn_abc123.jpg"))
check("path with /cdn_ is CDN", _cdn_basename("/Users/x/whatnot_cards/cdn_abc.jpg"))
check("url with /cdn_ is CDN", _cdn_basename("https://cdn.whatnot/whatnot_cards/cdn_x.jpg"))
check("captured front is NOT CDN", not _cdn_basename("card_b12_3_front_x.jpg"))
check("stamped front is NOT CDN", not _cdn_basename("card_b12_3_front_x_mazi.jpg"))
check("empty is NOT CDN", not _cdn_basename(""))
check("None is NOT CDN", not _cdn_basename(None))

print("== _proof_only_basename regression ==")
check("proof_ is proof-only", _proof_only_basename("proof_b1_2.jpg"))
check("review_proof_ is proof-only", _proof_only_basename("review_proof_b1_2.jpg"))
check("card front is NOT proof-only", not _proof_only_basename("card_b1_2_front_x.jpg"))

print("== resolution: good captured front wins ==")
r = resolve(image_front="card_b12_3_front_abc.jpg",
            ops_display_image="cdn_should_not_win.jpg")
check("front basename kept", r["image_front_basename"] == "card_b12_3_front_abc.jpg", r.get("image_front_basename"))
check("front url points at captured front",
      r["image_front_url"] == "/proxy/image/whatnot_cards/card_b12_3_front_abc.jpg", r.get("image_front_url"))
check("not flagged as ops_display", r["image_front_is_ops_display"] is False)

print("== resolution: neon subdir path preserved ==")
r = resolve(image_front_neon_url="https://r2/whatnot_cards/pokemon/card_b1_2_front_x.jpg",
            image_front="card_b1_2_front_x.jpg")
check("subdir preserved in url",
      r["image_front_url"] == "/proxy/image/whatnot_cards/pokemon/card_b1_2_front_x.jpg", r.get("image_front_url"))

print("== resolution: CDN front + recovery -> recovery surfaces ==")
r = resolve(image_front="cdn_listing_xyz.jpg",
            ops_display_image="card_b9_1_front_rec.jpg",
            ops_display_image_source="official_front")
check("cdn front blocked", r.get("cdn_only_front_blocked") is True)
check("recovery basename used", r["image_front_basename"] == "card_b9_1_front_rec.jpg", r.get("image_front_basename"))
check("recovery url surfaced",
      r["image_front_url"] == "/proxy/image/whatnot_cards/card_b9_1_front_rec.jpg", r.get("image_front_url"))
check("flagged as ops_display", r["image_front_is_ops_display"] is True)

print("== resolution: CDN front + NO recovery -> suppressed (not CDN) ==")
r = resolve(image_front="cdn_listing_xyz.jpg")
check("cdn front blocked", r.get("cdn_only_front_blocked") is True)
check("front url empty (suppressed)", r["image_front_url"] == "", r.get("image_front_url"))
check("status cdn_suppressed", r.get("front_image_status") == "cdn_suppressed", r.get("front_image_status"))

print("== resolution: CDN also sitting in ops_display is rejected ==")
r = resolve(image_front="cdn_listing_xyz.jpg",
            ops_display_image="cdn_reference_only.jpg",
            ops_display_image_source="cdn_image",
            ops_display_image_status="reference_only_cdn")
check("cdn ops_display not surfaced", r["image_front_url"] == "", r.get("image_front_url"))

print("== resolution: proof-only front + NO recovery -> suppressed (regression) ==")
r = resolve(image_front="proof_b1_2.jpg")
check("proof front blocked", r.get("proof_only_front_blocked") is True)
check("front url empty (suppressed)", r["image_front_url"] == "", r.get("image_front_url"))

print()
if _fail:
    print(f"OVERALL: FAIL ({_fail} failing)")
    sys.exit(1)
print("OVERALL: PASS")
