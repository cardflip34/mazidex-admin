"""Gate tests for the durable all-Identified Confirm write scope (2026-06-25).

These assert the new MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL mode:
  * opens the write gate for decision == "confirm" on ANY row (no static
    source_key allowlist required), routing it to promote_identified_to_trusted;
  * still BLOCKS every non-confirm decision with 403 review_write_scope_closed;
  * leaves the static source_key allowlist fail-closed when the mode is off.

No production DB is touched: the confirm path's neon_conn() and
promote_identified_to_trusted() are monkeypatched to fakes, so the test proves
the GATE routes correctly without performing a real promotion.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import app
    from config import review_write_scope_identified_all
except ModuleNotFoundError as exc:
    print(f"SKIP: app dependency unavailable in this interpreter ({exc.name})")
    sys.exit(0)


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def post_review_decision(payload):
    response = asyncio.run(app.review_decision(FakeRequest(payload)))
    body = json.loads(response.body.decode("utf-8"))
    return response.status_code, body


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PROMOTE_CALLS: list[str] = []


def _fake_neon_conn():
    return _FakeConn()


def _fake_promote(conn, body):
    PROMOTE_CALLS.append(str(body.get("source_key")))
    return {"status": "fake_promoted", "source_key": body.get("source_key")}


# Isolate scope env: only the new all-identified flag should be in play.
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED"] = "1"
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_SOURCE_KEYS"] = ""
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_TEST_RUN_ID"] = ""

# --- G1: config helper reads the env flag both ways ----------------------
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL"] = "0"
check(
    "G1a scope_identified_all reads disabled env",
    review_write_scope_identified_all() is False,
    f"value={review_write_scope_identified_all()}",
)
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL"] = "1"
check(
    "G1b scope_identified_all reads enabled env",
    review_write_scope_identified_all() is True,
    f"value={review_write_scope_identified_all()}",
)

confirm_payload = {
    "comp_id": "TEST-GATE-IDENTIFIED-ALL",
    "source_key": "TEST-GATE-IDENTIFIED-ALL::not-in-any-allowlist",
    "decision": "confirm",
}
non_confirm_payload = {
    "comp_id": "TEST-GATE-IDENTIFIED-ALL",
    "source_key": "TEST-GATE-IDENTIFIED-ALL::not-in-any-allowlist",
    "decision": "mazified",
}

# --- G2 + G3: all-identified ON. Patch DB + promote so the gate allow-path
# is observed without a real promotion (no prod write).
_orig_neon = app.neon_conn
_orig_promote = app.promote_identified_to_trusted
app.neon_conn = _fake_neon_conn
app.promote_identified_to_trusted = _fake_promote
try:
    PROMOTE_CALLS.clear()
    status, body = post_review_decision(confirm_payload)
    check(
        "G2 all-identified ON: confirm passes gate -> promote (201, no allowlist)",
        status == 201 and body.get("status") == "fake_promoted" and len(PROMOTE_CALLS) == 1,
        f"http={status} promote_calls={PROMOTE_CALLS} body={body}",
    )

    PROMOTE_CALLS.clear()
    status, body = post_review_decision(non_confirm_payload)
    check(
        "G3 all-identified ON: non-confirm blocked 403 (promote NOT called)",
        status == 403
        and body.get("error") == "review_write_scope_closed"
        and body.get("scope_identified_all") is True
        and len(PROMOTE_CALLS) == 0,
        f"http={status} promote_calls={PROMOTE_CALLS} body={body}",
    )
finally:
    app.neon_conn = _orig_neon
    app.promote_identified_to_trusted = _orig_promote

# --- G4: mode OFF + static allowlist -> unlisted confirm still fail-closed.
# Returns 403 before Step 4, so neon_conn/promote are never reached.
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL"] = "0"
os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_SOURCE_KEYS"] = "SOME-OTHER-ALLOWED-KEY::1"
status, body = post_review_decision(confirm_payload)
check(
    "G4 mode OFF + allowlist set: unlisted confirm blocked 403 (fail-closed)",
    status == 403
    and body.get("error") == "review_write_scope_closed"
    and body.get("scope_identified_all") is False,
    f"http={status} body={body}",
)

print("=" * 80)
all_pass = True
for name, ok, detail in results:
    tag = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{tag}] {name:<72}  {detail}")
print("=" * 80)
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
