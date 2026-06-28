"""mazidex-admin v0 - queue_reason / high-value-gate-removal focused tests.

Scope: 2026-05-30 senior-pass shifts in queues.py + static/app.js.

Mode:  Pure-function / string-introspection tests. No service hit, no DB
       hit. Safe to run while Chrome 1/2 are doing live QA on 8504.

Usage: cd ~/mazidex-admin && ./venv/bin/python3 tests/queue_reason_tests.py
"""
from __future__ import annotations

import os
import sys
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import queues  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# --------------------------------------------------------------------------
# Q1 - Every patched queue SQL emits a queue_reason column.
# --------------------------------------------------------------------------
for qname, sql_name in [
    ("working",            "WORKING_QUEUE_SQL"),
    ("high_value",         "HIGH_VALUE_SQL"),
    ("proof_review",       "PROOF_REVIEW_SQL"),
    ("needs_identity",     "NEEDS_IDENTITY_SQL"),
    ("needs_better_image", "NEEDS_BETTER_IMAGE_SQL"),
]:
    sql = getattr(queues, sql_name)
    check(
        f"Q1.{qname} SQL contains 'AS queue_reason'",
        "AS queue_reason" in sql,
        f"len={len(sql)} chars",
    )


# --------------------------------------------------------------------------
# Q2 - WORKING queue admits REVIEW_HIGH_VALUE (high-value gate removed).
# --------------------------------------------------------------------------
sql = queues.WORKING_QUEUE_SQL
check(
    "Q2a WORKING SQL trust_bucket IN list contains REVIEW_HIGH_VALUE",
    "REVIEW_HIGH_VALUE" in sql,
    f"sample: {[l for l in sql.split(chr(10)) if 'trust_bucket' in l]}",
)
check(
    "Q2b WORKING SQL still excludes rejected_from_public_path",
    "rejected_from_public_path" in sql,
    "",
)
check(
    "Q2c WORKING SQL still excludes hidden_from_work_queue",
    "hidden_from_work_queue" in sql,
    "",
)


# --------------------------------------------------------------------------
# Q3 - HIGH_VALUE remains a pure FILTER (no gating). Predicate is price-only.
# --------------------------------------------------------------------------
sql = queues.HIGH_VALUE_SQL
check(
    "Q3a HIGH_VALUE SQL is purely a price-band filter (sold_price >= 100)",
    "sold_price" in sql and ">= 100" in sql,
    "",
)
# trust_bucket may appear in ROW_FIELDS SELECT (it's a returned column).
# The real check is that HIGH_VALUE has no WHERE/AND clause filtering on it.
import re as _re
_where_clauses = _re.findall(r"(?:WHERE|AND)\s+[^\n]*trust_bucket", sql, _re.IGNORECASE)
check(
    "Q3b HIGH_VALUE SQL has NO WHERE/AND trust_bucket gate",
    not _where_clauses,
    f"found_clauses={_where_clauses}",
)
check(
    "Q3c HIGH_VALUE SQL does NOT use PRICE_HIGH_REVIEW pending-reason routing",
    "PRICE_HIGH_REVIEW" not in sql,
    "",
)


# --------------------------------------------------------------------------
# Q4 - queue_reason CASE labels cover all WHERE branches of proof_review
#      (operators get a discrete label even though the predicate is a union).
# --------------------------------------------------------------------------
sql = queues.PROOF_REVIEW_SQL
expected_proof_labels = [
    "proof_binding_unknown_unreadable",
    "proof_binding_neighbor_mismatch",
    "proof_binding_multi_auction",
    "pending_interstitial_carry_forward",
    "pending_cdn_only",
    "pending_back_only",
    "pending_role_warning",
    "pending_seller_suspect",
    "image_front_missing",
    "review_reason_sale_frame_rescue",
    "review_reason_interstitial",
    "review_reason_auction_mismatch",
    "review_reason_binding_mismatch",
    "review_reason_missing_front",
    "review_reason_multi_card",
    "review_reason_popup",
    "review_reason_not_card",
]
missing = [lbl for lbl in expected_proof_labels if lbl not in sql]
check(
    "Q4 PROOF_REVIEW queue_reason covers every WHERE-branch",
    not missing,
    f"missing={missing}",
)


# --------------------------------------------------------------------------
# Q5 - queue_reason values are valid (snake_case, no spaces, no punctuation).
# --------------------------------------------------------------------------
all_reason_labels = re.findall(r"THEN\s+'([a-z0-9_]+)'", queues.PROOF_REVIEW_SQL)
all_reason_labels += re.findall(r"THEN\s+'([a-z0-9_]+)'", queues.WORKING_QUEUE_SQL)
all_reason_labels += re.findall(r"THEN\s+'([a-z0-9_]+)'", queues.HIGH_VALUE_SQL)
all_reason_labels += re.findall(r"THEN\s+'([a-z0-9_]+)'", queues.NEEDS_IDENTITY_SQL)
all_reason_labels += re.findall(r"THEN\s+'([a-z0-9_]+)'", queues.NEEDS_BETTER_IMAGE_SQL)
bad = [lbl for lbl in all_reason_labels if not re.match(r"^[a-z][a-z0-9_]*$", lbl)]
check(
    f"Q5 All {len(all_reason_labels)} queue_reason labels are snake_case",
    not bad,
    f"bad={bad}",
)


# --------------------------------------------------------------------------
# Q6 - The SPA whyHere() prefers row.queue_reason when present.
# --------------------------------------------------------------------------
js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
check(
    "Q6a app.js whyHere() reads row.queue_reason FIRST",
    "row.queue_reason" in js and js.find("row.queue_reason") < js.find("STATE.queueContext"),
    "expected row.queue_reason check before STATE.queueContext fallback",
)
check(
    "Q6b app.js _humanizeReason helper present",
    "_humanizeReason" in js,
    "",
)
check(
    "Q6c drawer Record card surfaces queue_reason",
    "['queue_reason'" in js or "[\"queue_reason\"" in js,
    "drawer kvRows should include the queue_reason field",
)


# --------------------------------------------------------------------------
# Q7 - No code in mazidex-admin uses sold_price as a HARD gate / router.
#      (Sort/filter is fine; routing decisions based on price is not.)
# --------------------------------------------------------------------------
import glob
violations: list[str] = []
for path in glob.glob(os.path.join(ROOT, "*.py")):
    text = open(path, encoding="utf-8").read()
    # Look for price-thresholded routing patterns. We allow sort/filter
    # (ORDER BY, WHERE sold_price >=) but flag bool-gate patterns.
    for line in text.splitlines():
        s = line.strip()
        # The HIGH VALUE chip emission in safety.derive_chips is the only
        # legitimate >= 1000 price-aware code in mazidex-admin; it labels,
        # not routes. Allowlist by chip emission.
        if "HIGH_VALUE_LABEL" in s or "HIGH VALUE" in s:
            continue
        if re.search(r"if\s+.*sold_price.*>=\s*\d+\s*[:)]", s):
            # Allow comment lines
            if s.lstrip().startswith("#"):
                continue
            violations.append(f"{os.path.basename(path)}: {s[:120]}")
check(
    "Q7 No `if sold_price >= NNN:` route/gate patterns in mazidex-admin Python",
    not violations,
    f"violations={violations}",
)


# --------------------------------------------------------------------------
# Q8 - safety.py still classifies 'valid_card_front' as a valid front_capture
#      class value (it's an enum value, not a column name). Documents that
#      mazidex-admin reads the CORRECT field (front_capture_class), not the
#      broken neon_ops_app.py path that reads frontImageStatus.
# --------------------------------------------------------------------------
import safety  # noqa: E402
sev = safety._severity_for("front_capture_class", "valid_card_front")
check(
    "Q8a 'valid_card_front' value -> severity 'ok'",
    sev == "ok",
    f"sev={sev}",
)
sev = safety._severity_for("front_capture_class", "non_card")
check(
    "Q8b 'non_card' value -> severity 'block'",
    sev == "block",
    f"sev={sev}",
)
# Document that mazidex-admin does NOT read the broken field at all.
import app  # noqa: E402
app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
check(
    "Q8c mazidex-admin app.py does NOT reference broken frontImageStatus camelCase",
    "frontImageStatus" not in app_src,
    "",
)


# --------------------------------------------------------------------------
# Print + exit.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Q9 - The 5 remaining review queues now emit queue_reason too (P7 completion).
# --------------------------------------------------------------------------
for qname, sql_name, needle in [
    ("capture_review",             "CAPTURE_REVIEW_SQL",             "capture_review_pending"),
    ("interstitial_carry_forward", "INTERSTITIAL_CARRY_FORWARD_SQL", "interstitial_carry_forward_unsubstantiated"),
    ("chrome_advanced",            "CHROME_ADVANCED_SQL",            "latest.decision AS queue_reason"),
    ("human_review_ai_approved",   "HUMAN_REVIEW_AI_APPROVED_SQL",   "latest.decision AS queue_reason"),
    ("rejected_hidden",            "REJECTED_HIDDEN_SQL",            "latest.decision AS queue_reason"),
]:
    sql = getattr(queues, sql_name)
    check(
        f"Q9 {qname} emits queue_reason",
        ("AS queue_reason" in sql) and (needle in sql),
        sql_name,
    )

# Q9b - dynamic-sort variants keep queue_reason through the SELECT q.* wrap.
for qname in ("capture_review", "chrome_advanced", "rejected_hidden"):
    dsql, _ = queues.queue_sql(qname, limit=10, sort="price_high")
    check(
        f"Q9b {qname} dynamic-sort variant preserves queue_reason",
        "queue_reason" in dsql,
        "",
    )

# Q9c - queue_reason stays metadata: never appears as a write-gate predicate.
for sql_name in ("CAPTURE_REVIEW_SQL", "CHROME_ADVANCED_SQL", "REJECTED_HIDDEN_SQL"):
    sql = getattr(queues, sql_name)
    check(
        f"Q9c {sql_name} does not gate on queue_reason",
        "WHERE queue_reason" not in sql and "AND queue_reason" not in sql,
        "",
    )


print("=" * 80)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {name:<72}  {detail}")
print("=" * 80)
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
