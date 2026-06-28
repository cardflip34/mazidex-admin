# mazidex-admin v0

Private Review Workbench for MAZI. Neon/Postgres-backed. 9009-style tile grid.
NOT public. NOT a trust-promotion path. v0 read-only with decision writes
gated behind an env flag.

## Run

```bash
cd /Users/stavrosaim4/mazidex-admin
set -a && . ./.env.mazi && set +a
./venv/bin/python3 app.py
```

Binds to 100.111.48.86:8504. Open the URL in a Tailscale-connected browser.

## Endpoints

- `GET  /api/v1/health` — db ok, neon counts, write gate state
- `GET  /api/v1/queue/counts` — per-tab counts
- `GET  /api/v1/queue?name=<queue>&limit=<int>` — queue rows
- `GET  /api/v1/row/<comp_id>` — single row + decision history
- `GET  /api/v1/decisions/<comp_id>` — decision history only
- `POST /api/v1/review-decision` — append decision event (gated)
- `GET  /api/v1/privacy-self-check` — payload privacy assertion

## Queues (tab names)

- `working`
- `high_value`
- `proof_review`
- `needs_identity`
- `needs_better_image`
- `chrome_advanced`
- `human_review_ai_approved`
- `rejected_hidden`
- `trusted_view`

## Decision types accepted

```
chrome_advanced_to_human_review
human_confirmed_for_final_gate
needs_identity_enrichment
needs_better_image
proof_binding_unsubstantiated
visual_context_hold
rejected_from_public_path
hidden_from_work_queue
confirm flag reject workable clear   (legacy 9009 parity)
```

## Write gate

The decision-write endpoint is DISABLED by default. To enable after
operator approval:

```bash
export MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED=1
./venv/bin/python3 app.py
```

When CLOSED, POST returns 503 with the validated payload echoed.

## Safety

- NO raw sale-row mutation
- NO trust/Mazified/public-ready promotion
- NO buyer/winner/bidder/customer values in any response
- NO raw /Users paths in any response
- Bots/Streamlit/9008/9009 untouched
