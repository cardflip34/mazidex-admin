"""mazidex-admin v0 acceptance tests. Run on M4 against http://100.111.48.86:8504/."""
import json
import urllib.request
import urllib.error

BASE = "http://100.111.48.86:8504"

def get(path):
    return json.load(urllib.request.urlopen(BASE + path))

results = []

# AT-R6 health
h = get("/api/v1/health")
ok = h.get("db_ok") and h.get("ok")
pending = h["neon_counts"]["pending"]
trusted = h["neon_counts"]["trusted"]
results.append(("AT-R6 health db_ok", ok, f"pending={pending} trusted={trusted} write={h['review_write_enabled']}"))

# queue counts
c = get("/api/v1/queue/counts")["counts"]
results.append(("queue counts", True, json.dumps(c)))

# AT-R1 working
w = get("/api/v1/queue?name=working&limit=200")
n_working = w["row_count"]
results.append(("AT-R1 working >=100", n_working >= 100, f"rows={n_working}"))

# AT-R2 high_value sorted desc
hv = get("/api/v1/queue?name=high_value&limit=20")
prices = [float(r["sold_price"]) for r in hv["rows"] if r.get("sold_price") is not None]
sorted_ok = prices == sorted(prices, reverse=True)
all_ge_250 = all(p >= 250 for p in prices)
results.append(("AT-R2 high_value desc+>=250", sorted_ok and all_ge_250 and len(prices) > 0,
                f"rows={hv['row_count']} sample={prices[:5]}"))

# AT-R3 proof_review — P1 expanded this from a single proof_binding_status
# IN (...) check into a UNION predicate (status enum OR pending_reasons
# overlap with INTERSTITIAL_CARRY_FORWARD/CDN_ONLY/BACK_ONLY/ROLE_WARNING/
# SELLER_SUSPECT OR empty image_front OR raw._review_reason text matches).
# A row can now legitimately appear in proof_review with a proof_binding_status
# outside the old enum (or NULL), so the test asserts row-level membership
# against the FULL union instead of pinning to the old enum set.
PROOF_OLD_ENUM = {
    "unknown_unreadable", "mismatch_neighboring_auction", "multiple_auction_numbers",
}
PROOF_PENDING_REASONS = {
    "INTERSTITIAL_CARRY_FORWARD", "CDN_ONLY", "BACK_ONLY",
    "ROLE_WARNING", "SELLER_SUSPECT",
}
PROOF_REASON_FRAGMENTS = (
    "sale_frame_rescue", "interstitial", "auction-mismatch", "binding-mismatch",
    "low-verification-confidence", "missing-front", "multi-card", "popup", "not-card",
)


def _row_matches_proof_review(r):
    """True iff the row satisfies AT LEAST ONE branch of the proof_review union."""
    status = r.get("proof_binding_status") or ""
    if status in PROOF_OLD_ENUM:
        return True
    pending = r.get("pending_reasons") or []
    if isinstance(pending, list) and any(p in PROOF_PENDING_REASONS for p in pending):
        return True
    # Empty image_front is not exposed on the API row (server strips it),
    # but the surrogate image_front_basename ends up empty when the source
    # column was empty. Accept either signal.
    if not (r.get("image_front_basename") or r.get("image_front_url")):
        return True
    # _review_reason text from raw — server doesn't always echo it, but
    # if it does, accept any of the documented fragments.
    rr = ""
    raw = r.get("raw")
    if isinstance(raw, dict):
        rr = str(raw.get("_review_reason") or "").lower()
    if any(frag in rr for frag in PROOF_REASON_FRAGMENTS):
        return True
    return False


pr = get("/api/v1/queue?name=proof_review&limit=20")
proof_statuses = sorted({str(r.get("proof_binding_status") or "") for r in pr["rows"]})
matches = [_row_matches_proof_review(r) for r in pr["rows"]]
ok_proof = pr["row_count"] > 0 and all(matches)
results.append(("AT-R3 proof_review", ok_proof,
                f"rows={pr['row_count']} statuses={proof_statuses}"))

# AT-R5 trusted_view ~1300
tv = get("/api/v1/queue?name=trusted_view&limit=200")
results.append(("AT-R5 trusted_view", tv["row_count"] > 0, f"rows={tv['row_count']}"))

# AT-R4 row detail
sample_compid = w["rows"][0]["comp_id"] if w["rows"] else None
detail = get(f"/api/v1/row/{sample_compid}") if sample_compid else None
front_url = (detail or {}).get("row",{}).get("image_front_url","")
chips = (detail or {}).get("row",{}).get("chips") or []
has_chips = "INTERNAL REVIEW ONLY" in chips and "NOT TRUSTED" in chips
results.append(("AT-R4 row detail", bool(detail) and has_chips,
                f"comp_id={sample_compid} chips_count={len(chips)} front_starts={front_url[:60]}"))

# AT-P1/P2/P3 privacy scan
ps = get("/api/v1/privacy-self-check")
priv_clean = not ps["any_private_hit"] and ps["raw_user_path_count"] == 0
results.append(("AT-P1/P2/P3 privacy", priv_clean,
                f"private_hits={ps['private_text_hits']} user_paths={ps['raw_user_path_count']}"))

# AT-S1 write gate closed
req = urllib.request.Request(
    BASE + "/api/v1/review-decision",
    data=json.dumps({"comp_id":"X","source_key":"X","decision":"hidden_from_work_queue"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
try:
    resp = urllib.request.urlopen(req)
    results.append(("AT-S1 write gate closed", False, f"UNEXPECTED 200"))
except urllib.error.HTTPError as e:
    body = e.read().decode()
    gate_closed = e.code == 503 and "review_write_disabled" in body
    results.append(("AT-S1 write gate closed", gate_closed,
                    f"http={e.code} body_has_disabled={('review_write_disabled' in body)}"))

# AT-S2 verify no new rows in review_decision_events after the failed write
h2 = get("/api/v1/health")
de_count_after = h2["neon_counts"]["decision_events"]
results.append(("AT-S2 no DB writes after failed POST", de_count_after == 0,
                f"decision_events count={de_count_after}"))

# AT-S3 verify root SPA loads
import urllib.request
root = urllib.request.urlopen(BASE + "/").read().decode()
spa_ok = "MaziDex Workbench" in root and "INTERNAL REVIEW ONLY" in root
results.append(("AT-S3 root SPA loads", spa_ok, f"len={len(root)}"))

# Sample tile chip rendering check
sample = w["rows"][:3] if w["rows"] else []
chip_samples = [(r["comp_id"], r.get("chips", [])[:5]) for r in sample]
results.append(("sample chips top3", True, json.dumps(chip_samples)))

# ─── v0.0.3 safety badge gates ───────────────────────────────────────
SAFETY_FIELDS = (
    "front_capture_class",
    "proof_match_to_front",
    "proof_overlay_text",
    "proof_overlay_risk",
    "non_card_object_detected",
    "price_context_outlier",
    "identity_enrichment_disagreement",
    "public_image_safe_reason",
)
SAFETY_LABELS = (
    "FRONT CLASS", "PROOF MATCH", "OVERLAY RISK", "NON-CARD",
    "PRICE OUTLIER", "IDENTITY DRIFT", "PUBLIC IMAGE REASON",
)

# AT-SB1  every working-queue row carries all 8 safety_signals keys
def _row_has_all_safety_keys(r):
    sig = r.get("safety_signals") or {}
    return all(k in sig for k in SAFETY_FIELDS)
sample_w = w["rows"][:25] if w["rows"] else []
ok_keys = sample_w and all(_row_has_all_safety_keys(r) for r in sample_w)
results.append(("AT-SB1 safety_signals keys present", bool(ok_keys),
                f"sampled={len(sample_w)}"))

# AT-SB2  missing fields surface as None / null, NEVER literal False.
#   Today every value upstream is NULL. Each row must carry each
#   key set to None (or to a string), and NONE may be the literal boolean False.
def _no_false_in_signals(r):
    sig = r.get("safety_signals") or {}
    for k in SAFETY_FIELDS:
        v = sig.get(k)
        if v is False:
            return False, f"{k}=False"
        if isinstance(v, str) and v.strip().lower() == "false":
            return False, f"{k}=str:false"
    return True, ""
fail_msg = ""
ok_no_false = True
for r in sample_w:
    okv, why = _no_false_in_signals(r)
    if not okv:
        ok_no_false = False
        fail_msg = why
        break
results.append(("AT-SB2 missing fields fail-closed (no False)", ok_no_false,
                f"sampled={len(sample_w)} fail={fail_msg}"))

# AT-SB3  safety_badges list shape: 7 badges per row, each with severity
def _badge_shape_ok(r):
    badges = r.get("safety_badges")
    if not isinstance(badges, list) or len(badges) != 7:
        return False
    for b in badges:
        if not isinstance(b, dict): return False
        if "field" not in b or "label" not in b or "severity" not in b: return False
        if b["severity"] not in ("unknown","info","ok","warn","block"): return False
    return True
ok_shape = sample_w and all(_badge_shape_ok(r) for r in sample_w)
sample_badges = sample_w[0].get("safety_badges") if sample_w else []
labels_in_first = [b.get("label") for b in (sample_badges or [])]
results.append(("AT-SB3 safety_badges shape (7 badges per row)", bool(ok_shape),
                f"first_row_labels={labels_in_first}"))

# AT-SB4  drawer detail row also exposes safety_badges + safety_signals
if sample_compid:
    drow = (detail or {}).get("row", {})
    drawer_keys_ok = (
        isinstance(drow.get("safety_signals"), dict) and
        all(k in drow["safety_signals"] for k in SAFETY_FIELDS) and
        isinstance(drow.get("safety_badges"), list) and
        len(drow["safety_badges"]) == 7
    )
    results.append(("AT-SB4 drawer row safety fields", drawer_keys_ok,
                    f"compid={sample_compid}"))

# AT-SB5  empty queues remain empty (chrome_advanced / human_review_ai /
# rejected_hidden) — adding safety fields must not accidentally surface
# rows in these queues.
empties_ok = True
empty_counts = {}
for q in ("chrome_advanced","human_review_ai_approved","rejected_hidden"):
    j = get(f"/api/v1/queue?name={q}&limit=1")
    empty_counts[q] = j.get("row_count", -1)
    if j.get("row_count", -1) != 0:
        empties_ok = False
results.append(("AT-SB5 empty queues stay empty", empties_ok,
                f"counts={empty_counts}"))

# AT-SB6  external eBay/Goldin remain READ-ONLY -- no decision route
# accepts an external comp_id. Smoke test by POSTing an external-shaped
# id; must still return 503 (write gate closed) — confirming no
# accidental write path was introduced.
ext_body = json.dumps({"comp_id":"EB-1","source_key":"EB-1","decision":"hidden_from_work_queue"}).encode()
req = urllib.request.Request(BASE + "/api/v1/review-decision",
                             data=ext_body, method="POST",
                             headers={"Content-Type":"application/json"})
try:
    urllib.request.urlopen(req)
    results.append(("AT-SB6 external read-only (write blocked)", False, "UNEXPECTED 200"))
except urllib.error.HTTPError as e:
    body = e.read().decode()
    ok_ext = (e.code == 503 and "review_write_disabled" in body)
    results.append(("AT-SB6 external read-only (write blocked)", ok_ext,
                    f"http={e.code}"))

# Print all
print("=" * 80)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] {name:<40}  {detail}")
print("=" * 80)
print("OVERALL:", "PASS" if all_pass else "FAIL")
