"""Sample 20 Trusted View tiles + verify image fetches.
Also re-run privacy + write-gate gates."""
import json
import urllib.request
import urllib.error

BASE = "http://100.111.48.86:8504"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def fetch_head(url, timeout=5):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.headers.get("Content-Type", "")
            size = int(r.headers.get("Content-Length", "0") or 0)
            body = r.read(64)  # read first bytes to confirm a real image
            return r.status, ct, size, len(body)
    except urllib.error.HTTPError as e:
        return e.code, "", 0, 0
    except Exception as e:
        return -1, str(type(e).__name__), 0, 0


# 20 trusted view tiles
tv = get("/api/v1/queue?name=trusted_view&limit=20")
rows = tv["rows"]
print(f"=== Trusted View: {len(rows)} rows ===")

ok_count = 0
fail_count = 0
samples = []
for r in rows:
    url = r.get("image_front_url") or ""
    bn = r.get("image_front_basename") or ""
    if not url:
        samples.append({"comp_id": r["comp_id"], "ok": False, "reason": "no_url"})
        fail_count += 1
        continue
    code, ct, hdr_size, body_first = fetch_head(url, timeout=5)
    ok = (code == 200 and ct.startswith("image/"))
    if ok:
        ok_count += 1
    else:
        fail_count += 1
    samples.append({
        "comp_id": r["comp_id"],
        "url_prefix": url[:60],
        "basename": bn[:40],
        "code": code,
        "ct": ct,
        "ok": ok,
    })

print(f"image fetches: {ok_count}/{len(rows)} OK")
for s in samples[:5]:
    print(f"  {s['comp_id']:<40} code={s['code']} ct={s['ct'][:15]:<15} ok={s['ok']}")

# Also check Working Queue
wq = get("/api/v1/queue?name=working&limit=20")
wq_ok = 0
for r in wq["rows"]:
    url = r.get("image_front_url") or ""
    if not url:
        continue
    code, ct, _, _ = fetch_head(url, timeout=5)
    if code == 200 and ct.startswith("image/"):
        wq_ok += 1
print(f"Working Queue: {wq_ok}/{len(wq['rows'])} images OK")

# Privacy + health
h = get("/api/v1/health")
ps = get("/api/v1/privacy-self-check")
print()
print(f"health: db_ok={h['db_ok']}  write_enabled={h['review_write_enabled']}")
print(f"privacy: hits={ps['private_text_hits']}  user_paths={ps['raw_user_path_count']}")

# /Users paths in any sampled row
import json as _json
blob = _json.dumps(tv) + _json.dumps(wq)
import re
user_paths = re.findall(r"/Users/[^\"\\s,]+", blob)
print(f"/Users in trusted+working payloads: {len(user_paths)}")
print()
print("=" * 60)
all_ok = (
    ok_count >= 18  # 20/20 ideal; allow 2 transient failures
    and h["db_ok"]
    and not h["review_write_enabled"]
    and not ps["any_private_hit"]
    and ps["raw_user_path_count"] == 0
    and len(user_paths) == 0
)
print("OVERALL:", "PASS" if all_ok else "FAIL")
print(f"  trusted images: {ok_count}/{len(rows)}")
print(f"  working images: {wq_ok}/{len(wq['rows'])}")
