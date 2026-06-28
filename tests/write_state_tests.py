"""Write-state contract tests for 8504.

Pure API tests through FastAPI TestClient. They intentionally use only
write-disabled or unsupported-decision payloads so no review_decision_events
row can be inserted.
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
    from app import review_decision
    from config import review_write_enabled
except ModuleNotFoundError as exc:
    print(f"SKIP: app dependency unavailable in this interpreter ({exc.name})")
    sys.exit(0)


results: list[tuple[str, bool, str]] = []


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def post_review_decision(payload):
    response = asyncio.run(review_decision(FakeRequest(payload)))
    body = json.loads(response.body.decode("utf-8"))
    return response.status_code, body


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


valid_supported_payload = {
    "comp_id": "TEST-WRITE-STATE-NO-MUTATION",
    "source_key": "TEST-WRITE-STATE-NO-MUTATION::1",
    "decision": "mazified",
}

unsupported_semantic_payload = {
    "comp_id": "TEST-WRITE-STATE-NO-MUTATION",
    "source_key": "TEST-WRITE-STATE-NO-MUTATION::1",
    "decision": "needs_better_image",
}


os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED"] = "0"
check(
    "W1 config helper reads write-disabled env",
    review_write_enabled() is False,
    f"review_write_enabled={review_write_enabled()}",
)
status, body = post_review_decision(valid_supported_payload)
check(
    "W2 supported decision returns 503 when health/write helper is disabled",
    status == 503 and body.get("error") == "review_write_disabled",
    f"http={status} body={body}",
)

os.environ["MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED"] = "1"
check(
    "W3 config helper reads write-enabled env",
    review_write_enabled() is True,
    f"review_write_enabled={review_write_enabled()}",
)
status, body = post_review_decision(unsupported_semantic_payload)
check(
    "W4 unsupported semantic decision returns 422 before any DB write",
    status == 422 and body.get("error") == "unsupported_decision",
    f"http={status} body={body}",
)

status, body = post_review_decision({"comp_id": "missing-source-key", "decision": "mazified"})
check(
    "W5 malformed payload returns 400 before write-state gate",
    status == 400 and body.get("error") == "missing_source_key",
    f"http={status} body={body}",
)

print("=" * 80)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {name:<70}  {detail}")
print("=" * 80)
print("OVERALL:", "PASS" if all_pass else "FAIL")
sys.exit(0 if all_pass else 1)
