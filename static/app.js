/* mazidex-admin v0.0.3 — 9009-aligned SPA.
 *
 * Reads:
 *   GET /api/v1/health
 *   GET /api/v1/queue/counts            → per-queue counts
 *   GET /api/v1/sources/counts          → per-source counts
 *   GET /api/v1/queue?name=...&limit=N  → review queue rows (rows=[] if empty/error)
 *   GET /api/v1/source?name=...&limit=N → source rows (placeholder=true if unwired)
 *   GET /api/v1/row/{comp_id}           → row detail + decision history
 *   GET /api/v1/stats/header            → 4 hero stat numbers
 * Writes (DRY-RUN at v0):
 *   POST /api/v1/review-decision        → 503 until env flag set
 */
'use strict';

const STATE = {
  source: 'identified',         // primary nav
  queue:  'identified_view',    // mapped from source
  category: 'all',
  density:  'standard',
  rows: [],                     // raw rows from server
  visible: [],                  // post-category-filter
  counts: {},                   // queue counts
  sourceCounts: {},             // per-source counts
  health: null,
  writeEnabled: false,
  imgLoadOk: 0,
  imgLoadFail: 0,
  filterText: '',
  page: 1,
  pageSize: 200,
  totalCount: 0,
  initialSourceKey: '',
  initialCompId: '',
  sortMode: 'newest',
  watermarkedOnly: false,
  queueContext: 'trusted_view',
  // Load-cancellation token. Each loadCurrentView call grabs the next
  // value; the active call is the one whose token matches STATE.activeToken
  // at every await boundary. If a user clicks a second tab before the
  // first response returns, the first call's `myToken !== STATE.activeToken`
  // check makes it bail before it can mutate STATE.rows or paint the grid.
  loadToken: 0,
  activeToken: 0,
  operatorPolicyRestrict: false,  // toggled by orchestrator when policy restricts writes despite gate-open
  refreshMode: 'manual',          // Chrome lanes keep place; no background row refresh
  lastControlledRefreshAt: null,
  drawerToken: 0,                 // invalidates stale async drawer responses
  activeDrawerToken: 0,
};

const SOURCE_TO_QUEUE = {
  identified:       'identified_view',
  trusted:          'trusted_view',
  mazified:         'mazified',
  flagged_review:   'flagged_review',
  working_queue:    'working',
  ebay:             null,   // served by /api/v1/source (external)
  goldin:           null,   // served by /api/v1/source (external)
  fanatics:         null,
  heritage:         null,
  pwcc:             null,
  vdex_discovery:   null,
};

// External reference-comp sources: served from external_transactions,
// NOT from any Whatnot view. Tile renderer is slim (no proof/trust chips).
// External tiles are NOT clickable — there is no Whatnot row to open in
// the drawer.
const EXTERNAL_SOURCES = new Set(['ebay', 'goldin', 'fanatics', 'heritage', 'pwcc']);

const PROXY_BASE = "/proxy/image/whatnot_cards";
const PLACEHOLDER_SVG =
  "data:image/svg+xml;utf8," + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 60 80'>" +
    "<rect width='60' height='80' fill='#f8fafc'/>" +
    "<rect x='6' y='10' width='48' height='60' rx='4' fill='none' stroke='#cbd5e1' stroke-width='1.5' stroke-dasharray='3 2'/>" +
    "<text x='30' y='44' font-family='-apple-system,sans-serif' font-size='6' fill='#94a3b8' text-anchor='middle'>no image</text>" +
    "</svg>"
  );

const $  = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

// ─── utilities ────────────────────────────────────────────────────────
function chipClassFor(label) {
  const s = String(label || '').toLowerCase();
  if (s === 'internal review only')      return 'chip internal';
  if (s === 'not trusted')               return 'chip not-trusted';
  if (s === 'not mazified')              return 'chip not-mazified';
  if (s === 'not public ready')          return 'chip not-public-ready';
  if (s === 'not valuation safe')        return 'chip not-valuation-safe';
  if (s === 'mazified review')           return 'chip verified-price-eligible';
  if (s === 'chrome mazified candidate') return 'chip high-value';
  if (s === 'denied from public path')   return 'chip hard-blocked';
  if (s === 'proof internal only')       return 'chip proof-internal-only';
  if (s === 'public image safe false')   return 'chip public-image-safe-false';
  if (s === 'visual context hold')       return 'chip visual-context-hold';
  if (s === 'hard blocked')              return 'chip hard-blocked';
  if (s === 'human review required')     return 'chip human-review-required';
  if (s === 'high value')                return 'chip high-value';
  if (s === 'binding_review')            return 'chip binding-review';
  if (s === 'proof_blocked')             return 'chip proof-blocked';
  if (s === 'identity_repair')           return 'chip identity-repair';
  // External reference-comp chips (eBay / Goldin tiles)
  if (s === 'external comp')             return 'chip external-comp';
  if (s === 'reference only')            return 'chip reference-only';
  if (s === 'obo excluded')              return 'chip obo-excluded';
  if (s === 'verified price eligible')   return 'chip verified-price-eligible';
  return 'chip';
}

function fmtPrice(p) {
  if (p === null || p === undefined || p === '') return '–';
  const n = Number(p);
  if (!isFinite(n)) return String(p);
  return '$' + n.toFixed(2);
}

function fmtMoneyShort(n) {
  n = Number(n) || 0;
  if (n >= 1e9)  return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6)  return '$' + (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3)  return '$' + (n / 1e3).toFixed(1) + 'K';
  return '$' + n.toFixed(0);
}
function fmtIntShort(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeAttr(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

function searchTokens(value) {
  return String(value || '').toLowerCase().match(/[a-z0-9./#_-]+/g) || [];
}

function bumpStatus() {
  $('#img-stats').textContent =
    `images: ${STATE.imgLoadOk} ok · ${STATE.imgLoadFail} fail`;
}

// Reset the image-load counter for whatever view is about to render.
// Called from renderGrid so it stays per-view (not cumulative across
// category/search/density changes within the same dataset).
function resetImgCounters() {
  STATE.imgLoadOk = 0;
  STATE.imgLoadFail = 0;
  bumpStatus();
}

function presentDescriptor(v) {
  if (v === null || v === undefined) return '';
  const s = String(v).trim();
  if (!s || s.toLowerCase() === 'not-extracted') return '';
  return s;
}

function boolDescriptor(v, label) {
  if (v === true) return label;
  if (String(v).toLowerCase() === 'true') return label;
  return '';
}

function descriptorLine(row) {
  return [
    presentDescriptor(row.player || row.card_name || row.title),
    presentDescriptor(row.year),
    presentDescriptor(row.brand),
    presentDescriptor(row.set_name),
    presentDescriptor(row.variant || row.parallel || row.insert_type),
    presentDescriptor(row.serial_numbered),
    boolDescriptor(row.auto, 'AUTO'),
    boolDescriptor(row.rookie, 'RC'),
    boolDescriptor(row.patch, 'PATCH'),
    boolDescriptor(row.rpa, 'RPA'),
    presentDescriptor(row.grade || row.grade_chip),
    presentDescriptor(row.team),
    presentDescriptor(row.sport),
  ].filter(Boolean).join(' · ');
}

function subtitleLine(r) {
  return descriptorLine(r);
}

const QUEUE_VIEW_NOTES = {
  working: 'All Reasons is a combined review filter; it is not an exclusive folder.',
  high_value: 'High Value Price View sorts/filters by price only. Price does not block MAZIFIED.',
  proof_review: 'Proof Review surfaces proof/image/capture evidence, not audio-only advisory flags.',
  needs_identity: 'Needs Identity surfaces missing or image-supported identity gaps.',
  needs_better_image: 'Needs Better Image surfaces image availability/quality issues.',
  capture_review: 'Capture Review surfaces capture-pipeline review flags.',
  interstitial_carry_forward: 'Interstitial / Carry-Forward surfaces capture-context carry-forward risk.',
  chrome_advanced: 'Chrome Advanced shows rows advanced by Chrome-side review flow.',
  human_review_ai_approved: 'Human Review AI Approved shows rows with final-gate review events.',
  rejected_hidden: 'Rejected / Hidden shows rows removed from the active work queue.',
};

function queueViewNote() {
  if (STATE.source !== 'working_queue') return '';
  return QUEUE_VIEW_NOTES[STATE.queue] || 'Tabs are filters/views. Rows can appear in more than one view.';
}

function updateQueueViewNote() {
  const el = $('#queueViewNote');
  if (!el) return;
  const note = queueViewNote();
  el.hidden = !note;
  el.textContent = note;
}

// 2026-05-30 senior-pass:
//   row.queue_reason is a server-supplied snake_case code that names WHICH
//   branch of the queue's WHERE predicate qualified this row. When present
//   we render it verbatim (human-readable form via _humanizeReason) so the
//   operator sees a stable WHY label, not client-side inference. We keep
//   the legacy inference branch as a fallback for any queue that has not
//   yet been patched with a queue_reason CASE column.
function _humanizeReason(code) {
  if (!code || typeof code !== 'string') return '';
  return code.replace(/_/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase();
}

function whyHere(row) {
  // Prefer server-supplied queue_reason.
  if (row && typeof row.queue_reason === 'string' && row.queue_reason) {
    return `why: ${_humanizeReason(row.queue_reason)}`;
  }
  // Fallback: legacy per-queue inference (kept for graceful degradation).
  const q = STATE.queueContext || STATE.queue || '';
  const reasons = [];
  if (q === 'high_value') {
    reasons.push(`price view: ${fmtPrice(row.sold_price)} >= $100`);
    reasons.push('not a Mazified gate');
    return reasons.join(' · ');
  }
  if (q === 'proof_review') {
    const proof = row.proof_binding_status || row.proof_image_status || row.front_image_status;
    if (proof) reasons.push(`proof/image: ${proof}`);
    const rr = Array.isArray(row.pending_reasons) ? row.pending_reasons : [];
    const proofReasons = rr.filter((r) => !String(r).toLowerCase().includes('audio'));
    if (proofReasons.length) reasons.push(`reason: ${proofReasons.slice(0, 2).join(', ')}`);
    if (!reasons.length) reasons.push('proof/capture evidence review');
  } else if (q === 'needs_identity') {
    reasons.push('identity fields incomplete or image-supported identity review');
  } else if (q === 'needs_better_image') {
    reasons.push('image quality or missing-front review');
  } else if (q === 'working') {
    const bucket = row.trust_bucket ? `bucket: ${row.trust_bucket}` : '';
    const rr = Array.isArray(row.pending_reasons) && row.pending_reasons.length
      ? `reasons: ${row.pending_reasons.slice(0, 2).join(', ')}`
      : '';
    reasons.push(bucket || rr || 'combined review view');
    if (bucket && rr) reasons.push(rr);
  } else if (q === 'trusted_view') {
    reasons.push('trusted snapshot view');
  } else if (q === 'mazified') {
    reasons.push('Mazified review view');
  } else if (q === 'flagged_review') {
    reasons.push('risk flag view');
  }
  return reasons.filter(Boolean).join(' · ');
}

// ─── chip collapse ─────────────────────────────────────────────────
// The full chip cluster (INTERNAL REVIEW ONLY + NOT TRUSTED + NOT MAZIFIED
// + NOT PUBLIC READY + NOT VALUATION SAFE + PROOF INTERNAL ONLY + PUBLIC
// IMAGE SAFE FALSE …) is produced by safety.derive_chips on the server,
// so privacy_self_check and acceptance tests see all of it. But the global
// yellow safety banner already states the same NOT_* status, so showing
// all four chips on every tile is repetitive noise.
//
// collapseChips keeps:
//   - INTERNAL REVIEW ONLY (always, if present)
//   - one summary NOT TRUSTED chip in place of the 4 NOT_* cluster
//   - differentiating chips: PROOF_BLOCKED, IDENTITY_REPAIR, HIGH VALUE,
//     VISUAL CONTEXT HOLD, BINDING_REVIEW, HUMAN REVIEW REQUIRED,
//     HARD BLOCKED
// Drops the visually-redundant PROOF INTERNAL ONLY (implied by PROOF_BLOCKED)
// and PUBLIC IMAGE SAFE FALSE (implied by the summary NOT TRUSTED).
//
// External-source tile chips (EXTERNAL COMP / REFERENCE ONLY / OBO EXCLUDED
// / VERIFIED PRICE ELIGIBLE) pass through unchanged.
const NOT_CLUSTER = new Set([
  'NOT TRUSTED', 'NOT MAZIFIED', 'NOT PUBLIC READY', 'NOT VALUATION SAFE',
]);
const REDUNDANT_CHIPS = new Set([
  'PROOF INTERNAL ONLY',       // implied by PROOF_BLOCKED
  'PUBLIC IMAGE SAFE FALSE',   // implied by the summary NOT TRUSTED
]);
const DIFFERENTIATING_ORDER = [
  'HIGH VALUE',
  'PROOF_BLOCKED',
  'HARD BLOCKED',
  'VISUAL CONTEXT HOLD',
  'BINDING_REVIEW',
  'CAPTURE_REVIEW',
  'INTERSTITIAL_CARRY_FORWARD',
  'IDENTITY_REPAIR',
  'HUMAN REVIEW REQUIRED',
];

function collapseChips(chips) {
  const src = Array.isArray(chips) ? chips.map(String) : [];
  const upper = src.map(c => c.toUpperCase());

  // External chips pass through unchanged.
  if (upper.includes('EXTERNAL COMP') || upper.includes('REFERENCE ONLY')) {
    return src;
  }

  const out = [];
  const seen = new Set();
  const push = (c) => {
    if (!seen.has(c)) { seen.add(c); out.push(c); }
  };

  // Lead with INTERNAL REVIEW ONLY if it's present.
  if (upper.includes('INTERNAL REVIEW ONLY')) push('INTERNAL REVIEW ONLY');

  // Collapse the NOT_* cluster into a single summary chip.
  const hasNotCluster = src.some(c => NOT_CLUSTER.has(String(c).toUpperCase()));
  if (hasNotCluster) push('NOT TRUSTED');

  // Keep differentiating chips in a stable, scannable order.
  for (const wanted of DIFFERENTIATING_ORDER) {
    if (upper.includes(wanted)) push(wanted);
  }

  // Any other chip that isn't in NOT_CLUSTER / REDUNDANT_CHIPS / already
  // emitted — preserve so future server-side additions still surface.
  for (const c of src) {
    const u = String(c).toUpperCase();
    if (NOT_CLUSTER.has(u) || REDUNDANT_CHIPS.has(u)) continue;
    if (u === 'INTERNAL REVIEW ONLY') continue;
    if (DIFFERENTIATING_ORDER.includes(u)) continue;
    push(c);
  }
  return out;
}

// Category filter — runs client-side on the already-loaded rows. If the
// backend lacks a column for a given filter, we fall back to title/text
// matching so the filter is "visibly safe" rather than a no-op.
function matchesCategory(row, cat) {
  if (!cat || cat === 'all') return true;
  const text = [
    row.title, row.card_name, row.player, row.brand, row.set_name,
    row.variant, row.parallel, row.insert_type, row.serial_numbered,
    row.team, row.category, row.sport,
  ].filter(Boolean).join(' ').toLowerCase();
  const category = String(row.category || '').toLowerCase();
  const sport    = String(row.sport    || '').toLowerCase();
  const grade    = String(row.grade    || row.grade_chip || '').toLowerCase();
  const c = cat.toLowerCase();

  // sport-bucket categories: match sport column first, then text
  const sportNames = [
    'basketball','football','baseball','hockey','soccer','ufc','wnba',
  ];
  if (c === 'sports') {
    return sportNames.includes(sport) || sportNames.includes(category)
      || sportNames.some(s => text.includes(s));
  }
  if (sportNames.includes(c)) {
    return sport === c || category === c || text.includes(c);
  }
  if (c === 'pokemon')    return category.includes('pokemon')    || text.includes('pokemon');
  if (c === 'veefriends') return category.includes('veefriends') || text.includes('veefriends');
  if (c === 'slabs')      return Boolean(grade);
  if (c === 'raw')        return !grade;
  if (c === 'autos')      return text.includes('auto');
  if (c === 'rookies')    return /\b(rookie|\src\s|\src$|^rc\s)\b/.test(' ' + text + ' ');
  return text.includes(c);
}

function isWatermarked(row) {
  const bn = String(row.image_front_basename || row.image_front || row.image_front_url || '').toLowerCase();
  const raw = JSON.stringify(row || {}).toLowerCase();
  return bn.includes('_mazi.jpg') || raw.includes('front_stamped') || raw.includes('_mazi.jpg');
}

function imageUrlForBasename(basename) {
  return basename ? `${PROXY_BASE}/${basename}` : '';
}

function reviewOnlyDisplayBadge(row) {
  if (!row || !row.image_front_is_ops_display) return '';
  const source = row.ops_display_image_source || 'review context';
  const status = row.ops_display_image_status || 'review only';
  return `<span class="chip visual-context-hold" title="${escapeAttr(source + ' · ' + status)}">REVIEW IMAGE</span>`;
}

function pipelineStatus(row) {
  const hasIdentity = Boolean(row.player || row.card_name || row.title || row.brand || row.set_name || row.year);
  const cert = String(row.mazi_cert_status || '').toLowerCase();
  const proof = String(row.proof_binding_status || '').toLowerCase();
  const imported = Boolean(row.feed_observation_id || row.source_key || row.observed_in);
  const blocked = row.publish_ready === false || row.effective_publish_block_reason || proof === 'pending_review' || proof === 'unknown_unreadable' || cert !== 'verified';
  return [
    ['9009 / source', row.observed_in || row.source_file || 'unknown'],
    ['Neon row', imported ? 'present' : 'unknown'],
    ['Gemini identity', hasIdentity ? 'present' : 'pending / unknown'],
    ['MAZI cert', row.mazi_cert_status || 'pending / unknown'],
    ['Trust gate', blocked ? `blocked${row.effective_publish_block_reason ? ': ' + row.effective_publish_block_reason : ''}` : 'passed'],
  ];
}

function kvRows(pairs) {
  return pairs.map(([k, v]) => `
    <div class="kv-row">
      <span class="kv-key">${escapeHtml(k)}</span>
      <span class="kv-val">${escapeHtml(v == null || v === '' ? '—' : String(v))}</span>
    </div>`).join('');
}

function basenameRows(row) {
  const rows = [
    ['front basename', row.image_front_basename || ''],
    ['front 8504 proxy URL', row.image_front_basename ? imageUrlForBasename(row.image_front_basename) : ''],
    ['back basename', row.image_back_basename || ''],
    ['back 8504 proxy URL', row.image_back_basename ? imageUrlForBasename(row.image_back_basename) : ''],
    ['proof basename', row.image_proof_basename || ''],
    ['proof 8504 proxy URL', row.image_proof_basename ? imageUrlForBasename(row.image_proof_basename) : ''],
  ];
  return kvRows(rows);
}

const MARKET_SOURCE_STYLE = {
  whatnot:  { label: 'Whatnot',  color: '#f6b81a', badge: 'MAZI CAPTURED' },
  ebay:     { label: 'eBay',     color: '#2d9ce6' },
  goldin:   { label: 'Goldin',   color: '#f5c518' },
  fanatics: { label: 'Fanatics', color: '#16a34a' },
  pwcc:     { label: 'PWCC',     color: '#7c3aed' },
  heritage: { label: 'Heritage', color: '#475569' },
  other:    { label: 'Other',    color: '#64748b' },
};

function safeDate(row) {
  return row.last_sale_date || row.last_seen_at || row.feed_generated_at || row.first_seen_at || '';
}

function identityLine(row) {
  return descriptorLine(row) || '(identity pending)';
}

function buildMarketComps(row) {
  const incoming = Array.isArray(row.market_comps) ? row.market_comps : [];
  const normalized = incoming.map((dot) => ({
    source: dot.source || 'Other',
    sale_date: dot.sale_date || dot.sold_date || '',
    sale_price: dot.sale_price ?? dot.sold_price ?? dot.price,
    grade: dot.grade || '',
    condition: dot.condition || '',
    match_type: dot.match_type || 'player_context',
    confidence: dot.confidence ?? null,
    included_in_average: Boolean(dot.included_in_average),
    image_url: dot.image_url || '',
    thumbnail_url: dot.thumbnail_url || '',
    external_url: dot.external_url || '',
    reason_excluded: dot.reason_excluded || '',
    source_record_id: dot.source_record_id || '',
    marketplace_item_id: dot.marketplace_item_id || '',
    title: dot.title || '',
    seller_or_auction_house: dot.seller_or_auction_house || '',
    currency: dot.currency || 'USD',
    is_obo: Boolean(dot.is_obo),
    is_context_only: Boolean(dot.is_context_only),
    is_internal_whatnot: Boolean(dot.is_internal_whatnot),
    trust_status: dot.trust_status || 'unverified',
    data_era: dot.data_era || 'era-2',
    mazified_status: dot.mazified_status || 'NOT MAZIFIED',
    // Per-comp grade parsed from THIS listing's own title (app.py _parse_comp_grade).
    grade_group: dot.grade_group || dot.grade || '',
    comp_grade_company: dot.comp_grade_company || '',
    comp_grade_value: dot.comp_grade_value || '',
    // The target card's own grade, echoed on every comp (for default selection).
    target_grade_company: dot.target_grade_company || '',
    target_grade_value: dot.target_grade_value || '',
  }));

  if (row.sold_price) {
    normalized.unshift({
      source: 'Whatnot',
      sale_date: safeDate(row),
      sale_price: row.sold_price,
      grade: row.grade || row.grade_chip || '',
      condition: row.condition || '',
      match_type: 'exact_card',
      confidence: row.trust_score ?? null,
      included_in_average: false,
      image_url: row.image_front_url || '',
      thumbnail_url: row.image_front_url || '',
      external_url: '',
      reason_excluded: 'internal Whatnot/MAZI anchor; not average-safe until trust, proof, image, and public gates pass',
      source_record_id: row.source_key || row.comp_id || '',
      marketplace_item_id: row.auction_number || '',
      title: row.title || row.card_name || '',
      seller_or_auction_house: row.seller || '',
      currency: 'USD',
      is_obo: false,
      is_context_only: false,
      is_internal_whatnot: true,
      trust_status: row.trust_bucket || 'NOT TRUSTED',
      data_era: detectDataEra(row),
      mazified_status: row.mazi_cert_status === 'verified' && row.publish_ready === true ? 'MAZI_CAPTURE_REVIEW_REQUIRED' : 'NOT MAZIFIED',
      // The internal Whatnot anchor carries the card's own grade so it appears
      // alongside every grade group as THIS SALE. Never counted as an external comp.
      grade_group: row.grade || row.grade_chip || '',
      comp_grade_company: '',
      comp_grade_value: '',
      target_grade_company: '',
      target_grade_value: '',
    });
  }
  return normalized;
}

// ---- Multi-grade comps: grade/company dropdown -----------------------------
// Every external comp carries grade_group parsed from its OWN listing title
// (app.py _parse_comp_grade): "PSA 9", "PSA 10", "SGC 10", "CGC 9",
// "Raw/Ungraded", ... The drawer shows ONE grade group at a time, chosen via a
// dropdown, so a PSA 8 card never shows a PSA 10 sale as if it were the same
// grade. The internal Whatnot anchor (THIS SALE) is always kept in the subset.
const RAW_GRADE_GROUP = 'Raw/Ungraded';

function compGradeGroup(dot) {
  const g = String(dot.grade_group || dot.grade || '').trim();
  return g || RAW_GRADE_GROUP;
}

// The card's own grade label, taken from the target grade echoed on the comps
// (t.identity_json), falling back to the row's grade chip. '' for a raw card.
function cardGradeGroup(row, marketComps) {
  const withTarget = (marketComps || []).find(
    (d) => !d.is_internal_whatnot && (d.target_grade_company || d.target_grade_value)
  );
  if (withTarget) {
    const g = (String(withTarget.target_grade_company || '').trim()
      + ' ' + String(withTarget.target_grade_value || '').trim()).trim();
    if (g) return g;
  }
  return String(row.grade || row.grade_chip || '').trim();
}

// Map<groupLabel, count> over EXTERNAL comps only (the internal anchor is never counted).
function gradeGroupCounts(marketComps) {
  const counts = new Map();
  (marketComps || []).forEach((d) => {
    if (d.is_internal_whatnot) return;
    const g = compGradeGroup(d);
    counts.set(g, (counts.get(g) || 0) + 1);
  });
  return counts;
}

// Ordered options for the dropdown: card's own grade first (even at count 0),
// then richest graded groups, then Raw/Ungraded last.
function gradeGroupOptions(row, marketComps) {
  const counts = gradeGroupCounts(marketComps);
  const cardGroup = cardGradeGroup(row, marketComps);
  if (cardGroup && !counts.has(cardGroup)) counts.set(cardGroup, 0);
  const isRaw = (label) => label === RAW_GRADE_GROUP;
  return Array.from(counts.entries())
    .sort((a, b) => {
      if (a[0] === cardGroup && b[0] !== cardGroup) return -1;
      if (b[0] === cardGroup && a[0] !== cardGroup) return 1;
      if (isRaw(a[0]) && !isRaw(b[0])) return 1;
      if (isRaw(b[0]) && !isRaw(a[0])) return -1;
      if (b[1] !== a[1]) return b[1] - a[1];
      return a[0].localeCompare(b[0]);
    })
    .map(([label, count]) => ({ label, count }));
}

// Default selection (operator choice 2026-06-27): always the card's OWN grade
// group, so THIS SALE is the first anchor shown -- apples-to-apples -- even when
// that grade has 0 external comps yet (the void-filler fills it later). Raw
// cards (no stated grade) default to Raw/Ungraded, their own group.
function defaultGradeGroup(row, marketComps) {
  return cardGradeGroup(row, marketComps) || RAW_GRADE_GROUP;
}

// The selected grade's comps. The internal Whatnot anchor (THIS SALE) is a
// single-grade data point: it belongs ONLY in the card's own grade tab, never
// on an off-grade chart (a PSA 9 sale must not plot on the PSA 10 chart). So it
// is bucketed by the card's own canonical grade, exactly like every external
// comp is bucketed by its own parsed grade.
function compsForGradeGroup(row, marketComps, group) {
  const cardGroup = cardGradeGroup(row, marketComps) || RAW_GRADE_GROUP;
  return (Array.isArray(marketComps) ? marketComps : [])
    .filter((d) => d.is_internal_whatnot
      ? cardGroup === group
      : compGradeGroup(d) === group);
}

function gradeDropdownHtml(row, marketComps, selectedGroup) {
  const options = gradeGroupOptions(row, marketComps);
  if (!options.length) return '';
  const opts = options.map(({ label, count }) =>
    `<option value="${escapeAttr(label)}"${label === selectedGroup ? ' selected' : ''}>`
    + `${escapeHtml(label)} (${count})</option>`).join('');
  return `
    <div class="bbg-grade-pick">
      <label class="control-label">Grade &middot; Company
        <select id="bbgGradeSelect" class="control-select">${opts}</select>
      </label>
      <span class="bbg-grade-pick-note">${options.length} grade group${options.length === 1 ? '' : 's'} in corpus &middot; one graph, swaps per selection</span>
    </div>`;
}

// Per-grade price header / chart aggregates. COMP AVG/HIGH average the selected
// grade's REAL sold comps (non-OBO, price > 0). Within a single grade this is an
// honest same-grade comp average; OBO stays context-only per the MAZI gate.
function gradeGroupSoldComps(subset) {
  return (subset || []).filter(
    (d) => !d.is_internal_whatnot && !d.is_obo && Number(d.sale_price) > 0
  );
}

function gradeGroupAggregates(row, subset) {
  const internal = (subset || []).find((d) => d.is_internal_whatnot) || null;
  // THIS SALE renders ONLY on the card's own grade tab (where the internal dot
  // is in the subset). Off-grade tabs show '-': the Whatnot anchor is never a
  // cross-grade reference. (No row.sold_price fallback; `row` kept for the
  // stable call signature.)
  const thisSale = (internal && internal.sale_price != null)
    ? Number(internal.sale_price)
    : null;
  const prices = gradeGroupSoldComps(subset)
    .map((d) => Number(d.sale_price))
    .filter((v) => Number.isFinite(v) && v > 0);
  const compCount = prices.length;
  const oboCount = (subset || []).filter((d) => !d.is_internal_whatnot && d.is_obo).length;
  return {
    thisSale,
    compHigh: compCount ? Math.max.apply(null, prices) : null,
    compAvg: compCount ? prices.reduce((a, b) => a + b, 0) / compCount : null,
    compCount,
    oboCount,
    hasComps: compCount > 0,
  };
}

function gradePriceHeaderHtml(row, subset, groupLabel) {
  const agg = gradeGroupAggregates(row, subset);
  const cell = (label, val, extra) => `
    <div class="price-head-cell${extra ? ' ' + extra : ''}">
      <span class="price-head-label">${label}</span>
      <span class="price-head-val">${val == null ? '-' : escapeHtml(fmtPrice(val))}</span>
    </div>`;
  const safeGroup = escapeHtml(groupLabel || RAW_GRADE_GROUP);
  const note = agg.hasComps
    ? `${safeGroup}: ${agg.compCount} sold comp${agg.compCount === 1 ? '' : 's'} averaged`
      + (agg.oboCount ? ` &middot; ${agg.oboCount} OBO shown as context, excluded from average` : '')
    : `${safeGroup}: no sold comps yet`
      + (agg.oboCount ? ` &middot; ${agg.oboCount} OBO shown as context only` : '');
  return `
    <section class="bbg-card price-head-card">
      <div class="price-head-grid">
        ${cell('THIS SALE', agg.thisSale, 'is-this-sale')}
        ${cell('COMP HIGH', agg.compHigh)}
        ${cell('COMP AVG', agg.compAvg)}
      </div>
      <p class="price-head-note">${note}</p>
    </section>`;
}

// The single swapping view: price header + chart + valuation + summary, all
// scoped to one grade group. Re-rendered into #bbgGradeView on dropdown change.
function gradeViewHtml(row, marketComps, selectedGroup) {
  const subset = compsForGradeGroup(row, marketComps, selectedGroup);
  return `
    ${gradePriceHeaderHtml(row, subset, selectedGroup)}
    ${marketChartHtml(row, subset)}
    ${valuationPanelHtml(subset)}
    ${audioCalloutHtml(row)}
    ${externalSummaryHtml(subset)}`;
}

// Honest comp aggregates for the price header.
// THIS SALE  = the internal Whatnot captured sale (row.sold_price).
// COMP HIGH  = max of average-safe EXTERNAL comps (OBO/context/internal excluded).
// COMP AVG   = mean of the same average-safe EXTERNAL comp set.
// When no external comps are wired into market_comps[], compHigh/compAvg are
// null -> the UI shows "-" + "no external comps yet" rather than fabricating.
function compAggregates(marketComps) {
  const list = Array.isArray(marketComps) ? marketComps : [];
  const internal = list.find((d) => d.is_internal_whatnot);
  const thisSale = internal && internal.sale_price != null ? Number(internal.sale_price) : null;
  const isTrustedForAverage = (value) => {
    const t = String(value || '').toLowerCase();
    return t.includes('trusted') && !t.includes('not trusted');
  };
  const averageSafe = list.filter((d) =>
    !d.is_internal_whatnot &&
    d.included_in_average &&
    !d.is_obo &&
    !d.is_context_only &&
    ['exact_card', 'close_variant'].includes(d.match_type) &&
    isTrustedForAverage(d.trust_status) &&
    Number(d.sale_price) > 0
  );
  const prices = averageSafe.map((d) => Number(d.sale_price));
  const compCount = prices.length;
  const compHigh = compCount ? Math.max.apply(null, prices) : null;
  const compAvg = compCount ? (prices.reduce((a, b) => a + b, 0) / compCount) : null;
  return { thisSale, compHigh, compAvg, compCount, hasComps: compCount > 0 };
}

// Labeled price header strip: THIS SALE / COMP HIGH / COMP AVG.
// THIS SALE uses the SAME value the grid tile shows (row.sold_price) so the
// grid and drawer can never disagree. COMP cells are honestly empty until
// external comps are wired into market_comps[].
function priceHeaderHtml(row, marketComps) {
  const agg = compAggregates(marketComps);
  const sale = (agg.thisSale != null)
    ? agg.thisSale
    : (row.sold_price != null ? Number(row.sold_price) : null);
  const cell = (label, val, extra) => `
    <div class="price-head-cell${extra ? ' ' + extra : ''}">
      <span class="price-head-label">${label}</span>
      <span class="price-head-val">${val == null ? '-' : escapeHtml(fmtPrice(val))}</span>
    </div>`;
  const oboCount = (Array.isArray(marketComps) ? marketComps : []).filter((d) => d.is_obo && !d.is_internal_whatnot).length;
  const note = agg.hasComps
    ? `${agg.compCount} average-safe comp${agg.compCount === 1 ? '' : 's'} &middot; ${oboCount} OBO sale${oboCount === 1 ? '' : 's'} shown as context, excluded from average`
    : (oboCount
      ? `0 average-safe comps &middot; ${oboCount} OBO sale${oboCount === 1 ? '' : 's'} shown as context, excluded from average`
      : 'no external comps yet &middot; COMP HIGH / COMP AVG pending canonical_comps');
  return `
    <section class="bbg-card price-head-card">
      <div class="price-head-grid">
        ${cell('THIS SALE', sale, 'is-this-sale')}
        ${cell('COMP HIGH', agg.compHigh)}
        ${cell('COMP AVG', agg.compAvg)}
      </div>
      <p class="price-head-note">${note}</p>
    </section>`;
}

function detectDataEra(row) {
  const ts = Date.parse(row.last_seen_at || row.feed_generated_at || row.first_seen_at || row.last_sale_date || '');
  const era2 = Date.parse('2026-05-26T00:00:00Z');
  return ts && ts >= era2 ? 'era-2' : 'era-1';
}

function whyNotMazified(row) {
  const backend = Array.isArray(row && row.mazified_blockers)
    ? row.mazified_blockers.filter(Boolean)
    : [];
  if (backend.length) return backend;
  const decision = String(row && row.review_decision || '').toLowerCase();
  if (decision === 'mazified' || (row && row.mazi_cert_id)) return ['already_mazified'];
  const reasons = [];
  const proof = String(row.proof_binding_status || '').toLowerCase();
  const cert = String(row.mazi_cert_status || '').toLowerCase();
  if (cert !== 'verified') reasons.push('mazi_cert_status is not verified');
  if (proof !== 'verified' && proof !== 'human_approved') reasons.push('proof binding not verified/human-approved');
  if (!row.publish_ready) reasons.push('publish_ready is false or absent');
  if (row.effective_publish_block_reason) reasons.push(`publish block: ${row.effective_publish_block_reason}`);
  if (!row.image_front_url && !row.image_front_basename) reasons.push('usable front image missing');
  if (row.identity_enrichment_disagreement) reasons.push(`identity disagreement: ${row.identity_enrichment_disagreement}`);
  if (!row.player && !row.card_name && !row.title) reasons.push('identity fields incomplete');
  return reasons;
}

function blockerLabel(reason) {
  const labels = {
    already_mazified: 'ALREADY MAZIFIED',
    proof_missing: 'PROOF MISSING',
    cert_or_evidence_pending: 'CERT PENDING',
    public_or_private_image_unsafe: 'IMAGE UNKNOWN',
    image_missing_or_broken: 'IMAGE UNKNOWN',
    identity_ambiguous: 'IDENTITY REPAIR',
    binding_review: 'BINDING REVIEW',
    proof_different_sale: 'PROOF DIFFERENT SALE',
    hard_blocker_present: 'HARD BLOCKER',
    capture_hard_blocker: 'CAPTURE HARD BLOCK',
    duplicate_or_collision: 'DUPLICATE/COLLISION',
    banned_seller: 'BANNED SELLER',
    not_in_allowed_decisions: 'NOT ALLOWED',
    source_drift: 'SOURCE DRIFT',
  };
  return labels[reason] || String(reason || '').replace(/_/g, ' ').toUpperCase();
}

function blockerChipsHtml(reasons) {
  const list = Array.isArray(reasons) ? reasons.filter(Boolean) : [];
  if (!list.length) return '';
  return `<div class="blocker-chip-row">${list.map((reason) =>
    `<span class="blocker-chip">${escapeHtml(blockerLabel(reason))}</span>`
  ).join('')}</div>`;
}

function sourceLegendHtml() {
  const order = ['whatnot', 'ebay', 'goldin', 'fanatics', 'pwcc', 'heritage'];
  return order.map((key) => {
    const s = MARKET_SOURCE_STYLE[key];
    return `<span class="source-legend"><i style="background:${s.color}"></i>${escapeHtml(s.label)}</span>`;
  }).join('');
}

function chartControlsHtml() {
  const times = ['3M', '6M', '12M', 'All'].map((x, i) =>
    `<button class="bbg-control ${i === 2 ? 'is-active' : ''}" type="button">${x}</button>`).join('');
  const sources = ['Whatnot', 'eBay', 'Goldin', 'Fanatics', 'PWCC', 'Heritage'].map((x) =>
    `<button class="bbg-control source" type="button">${escapeHtml(x)}</button>`).join('');
  const toggles = ['Exact only', 'Include context', 'Exclude OBO', 'Compare grades', 'Verified only'].map((x) =>
    `<span class="bbg-toggle">${escapeHtml(x)}</span>`).join('');
  return `
    <div class="bbg-controls"><div class="bbg-control-row">${times}</div><div class="bbg-control-row">${sources}</div><div class="bbg-control-row">${toggles}</div></div>
  `;
}

function chartDateLabel(value) {
  if (!value) return '';
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return String(value).slice(0, 10);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// Chart stat set. This is called only from marketStats, which now always
// receives a SINGLE grade-group subset (one company+grade at a time). Within one
// grade the honest stat set is every real SOLD external comp: non-internal,
// non-OBO, positive price. include_in_average is grade-vs-the-card and would hide
// every off-grade group, so it is intentionally NOT applied here; OBO stays
// context-only per the MAZI gate.
function averageSafeExternalComps(marketComps) {
  return (Array.isArray(marketComps) ? marketComps : [])
    .filter((d) =>
      !d.is_internal_whatnot &&
      !d.is_obo &&
      Number(d.sale_price) > 0
    )
    .slice()
    .sort((a, b) => (Date.parse(a.sale_date || '') || 0) - (Date.parse(b.sale_date || '') || 0));
}

function marketStats(row, marketComps) {
  const clean = averageSafeExternalComps(marketComps);
  const internal = (marketComps || []).find((d) => d.is_internal_whatnot) || null;
  const start = clean[0] || internal;
  const current = internal || clean[clean.length - 1] || null;
  const prices = clean.map((d) => Number(d.sale_price)).filter((v) => Number.isFinite(v) && v > 0);
  const startPrice = start ? Number(start.sale_price) : null;
  const currentPrice = current ? Number(current.sale_price) : null;
  const dollarChange = startPrice != null && currentPrice != null ? currentPrice - startPrice : null;
  const growth = startPrice > 0 && dollarChange != null ? (dollarChange / startPrice) * 100 : null;
  const avg = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null;
  return {
    clean,
    internal,
    current,
    lastExternal: clean[clean.length - 1] || null,
    startPrice,
    currentPrice,
    dollarChange,
    growth,
    avg,
    low: prices.length ? Math.min(...prices) : null,
    high: prices.length ? Math.max(...prices) : null,
    numberSales: prices.length + (internal ? 1 : 0),
  };
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(Number(value))) return '-';
  const n = Number(value);
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function smoothPathFromCoords(coords) {
  if (!coords.length) return '';
  if (coords.length === 1) return `M ${coords[0].x.toFixed(1)} ${coords[0].y.toFixed(1)}`;
  let d = `M ${coords[0].x.toFixed(1)} ${coords[0].y.toFixed(1)}`;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const p0 = coords[Math.max(0, i - 1)];
    const p1 = coords[i];
    const p2 = coords[i + 1];
    const p3 = coords[Math.min(coords.length - 1, i + 2)];
    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

function chartStatHtml(row, marketComps) {
  const stats = marketStats(row, marketComps);
  const currentDate = stats.current ? stats.current.sale_date : safeDate(row);
  const currentLabel = stats.internal ? 'Current Whatnot' : 'Current Comp';
  const cards = [
    ['Rate of Growth', fmtPct(stats.growth), 'accent'],
    ['Real Dollar Change', stats.dollarChange == null ? '-' : fmtPrice(stats.dollarChange), 'accent'],
    ['Start Price', stats.startPrice == null ? '-' : fmtPrice(stats.startPrice), ''],
    [currentLabel, stats.currentPrice == null ? '-' : fmtPrice(stats.currentPrice), ''],
    ['Number of Sales', String(stats.numberSales || 0), ''],
    ['Average Price', stats.avg == null ? '-' : fmtPrice(stats.avg), ''],
    ['Low Price', stats.low == null ? '-' : fmtPrice(stats.low), ''],
    ['High Price', stats.high == null ? '-' : fmtPrice(stats.high), ''],
  ];
  return `
    <div class="bbg-performance-head">
      <div><span>Last Sold Price</span><strong>${escapeHtml(stats.currentPrice == null ? '-' : fmtPrice(stats.currentPrice))}</strong></div>
      <div><span>Last Sold Date</span><strong>${escapeHtml(chartDateLabel(currentDate) || '-')}</strong></div>
    </div>
    <div class="bbg-performance-grid">
      ${cards.map(([label, value, cls]) => `<div class="bbg-performance-item ${cls}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}
    </div>`;
}

function marketChartHtml(row, marketComps) {
  const priced = marketComps
    .filter((d) => Number(d.sale_price) > 0)
    .slice()
    .sort((a, b) => {
      const ad = Date.parse(a.sale_date || '') || 0;
      const bd = Date.parse(b.sale_date || '') || 0;
      if (ad !== bd) return ad - bd;
      return Number(a.sale_price) - Number(b.sale_price);
    });
  const externalExact = priced.filter((d) => !d.is_internal_whatnot && d.match_type === 'exact_card' && !d.is_context_only);
  const prices = priced.map((d) => Number(d.sale_price));
  const maxPrice = Math.max(...prices, Number(row.sold_price || 0), 1);
  const minPrice = Math.min(...prices, Number(row.sold_price || 0), 0);
  const span = Math.max(maxPrice - minPrice, 1);
  const width = 720;
  const height = 250;
  const leftPad = 54;
  const rightPad = 22;
  const topPad = 22;
  const bottomPad = 38;
  const plotW = width - leftPad - rightPad;
  const plotH = height - topPad - bottomPad;
  const coords = priced.map((d, i) => {
    const x = leftPad + (priced.length === 1 ? plotW * 0.5 : (plotW * i / (priced.length - 1)));
    const y = topPad + (plotH - ((Number(d.sale_price) - minPrice) / span) * plotH);
    return { d, i, x, y };
  });
  const pathD = smoothPathFromCoords(coords);
  const path = pathD ? `<path class="bbg-line" d="${pathD}"></path>` : '';
  const areaD = coords.length >= 2
    ? `${pathD} L ${coords[coords.length - 1].x.toFixed(1)} ${(height - bottomPad).toFixed(1)} L ${coords[0].x.toFixed(1)} ${(height - bottomPad).toFixed(1)} Z`
    : '';
  const area = areaD ? `<path class="bbg-area" d="${areaD}"></path>` : '';
  const points = coords.map(({ d, x, y }) => {
    const sourceKey = String(d.source || 'other').toLowerCase();
    const style = MARKET_SOURCE_STYLE[sourceKey] || MARKET_SOURCE_STYLE.other;
    const r = d.is_context_only ? 4.6 : 4.2;
    const fill = d.is_context_only ? 'transparent' : style.color;
    const opacity = d.data_era === 'era-1' ? 0.55 : 1;
    const stroke = d.is_obo ? '#f97316' : style.color;
    const label = `${style.label} ${fmtPrice(d.sale_price)} ${d.sale_date || ''}`;
    const img = d.thumbnail_url || d.image_url || '';
    const tipW = 172;
    const tipH = 130;
    const tipX = Math.max(8, Math.min(x + 10, width - tipW - 8));
    const tipY = Math.max(8, Math.min(y - tipH - 10, height - tipH - 8));
    const imgHtml = img
      ? `<img src="${escapeAttr(img)}" alt="${escapeAttr(d.title || label)}">`
      : `<div class="bbg-point-noimg"><b>${escapeHtml(style.label)}</b><span>source image pending</span></div>`;
    return `
      <g class="bbg-point" opacity="${opacity}">
        <circle class="bbg-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${d.is_context_only || d.is_obo ? 2.2 : 1.5}"><title>${escapeHtml(label)}</title></circle>
        <foreignObject class="bbg-point-tip" x="${tipX.toFixed(1)}" y="${tipY.toFixed(1)}" width="${tipW}" height="${tipH}">
          <div xmlns="http://www.w3.org/1999/xhtml" class="bbg-point-card">
            ${imgHtml}
            <div class="bbg-point-card-body">
              <b>${escapeHtml(fmtPrice(d.sale_price))}</b>
              <span>${escapeHtml([style.label, d.sale_date, d.grade].filter(Boolean).join(' · '))}</span>
              <small>${escapeHtml(d.title || '')}</small>
            </div>
          </div>
        </foreignObject>
      </g>`;
  }).join('');

  const yMax = maxPrice;
  const yMid = minPrice + span / 2;
  const yMin = minPrice;
  const grid = [0, 0.5, 1].map((g) => {
    const y = topPad + plotH * g;
    const v = g === 0 ? yMax : (g === 0.5 ? yMid : yMin);
    return `<line x1="${leftPad}" y1="${y}" x2="${width - rightPad}" y2="${y}" /><text x="8" y="${y + 4}">${escapeHtml(fmtPrice(v))}</text>`;
  }).join('');

  const emptyText = externalExact.length === 0
    ? '<div class="bbg-empty-state">No exact external comps yet. Fair value pending. Context comps are not averaged. News delayed.</div>'
    : '';

  return `
    <section class="bbg-card bbg-chart-card">
      <div class="bbg-card-head"><div><h3>Sales History</h3><p>Frontend market_comps[] contract preview. Current Whatnot sale is shown as an internal source dot when available.</p></div><div class="bbg-legend">${sourceLegendHtml()}</div></div>
      ${chartControlsHtml()}
      <svg class="bbg-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Sales history chart">
        <rect x="0" y="0" width="${width}" height="${height}" rx="10"></rect>
        <g class="bbg-grid">${grid}</g>
        <line class="bbg-axis" x1="${leftPad}" y1="${height - bottomPad}" x2="${width - rightPad}" y2="${height - bottomPad}"></line>
        ${area}
        ${path}
        ${points}
      </svg>
      ${chartStatHtml(row, marketComps)}
      ${emptyText}
    </section>
  `;
}

function valuationPanelHtml(marketComps) {
  const isTrustedForAverage = (value) => {
    const trust = String(value || '').toLowerCase();
    return trust.includes('trusted') && !trust.includes('not trusted');
  };
  const averageSafe = marketComps.filter((d) =>
    d.included_in_average &&
    !d.is_obo &&
    !d.is_context_only &&
    ['exact_card', 'close_variant'].includes(d.match_type) &&
    isTrustedForAverage(d.trust_status)
  );
  const contextOnly = marketComps.filter((d) => d.is_context_only || ['player_context', 'set_context'].includes(d.match_type));
  const obo = marketComps.filter((d) => d.is_obo);
  return `
    <section class="bbg-card">
      <h3>Valuation</h3>
      <div class="bbg-metric-grid">
        <div><strong>Fair value</strong><span>Pending</span></div>
        <div><strong>Average-safe comps</strong><span>${averageSafe.length}</span></div>
        <div><strong>OBO sales</strong><span>${obo.length}</span></div>
        <div><strong>Context / never averaged</strong><span>${contextOnly.length}</span></div>
      </div>
      <p class="bbg-note">Fair value uses only average-safe rows. OBO rows are shown as labeled same-card context and excluded from median/high/low because the displayed price may be the asking price, not the final accepted offer.</p>
    </section>
  `;
}

function externalSummaryHtml(marketComps) {
  const external = marketComps.filter((d) => !d.is_internal_whatnot);
  const context = marketComps.filter((d) => d.is_context_only || ['player_context', 'set_context'].includes(d.match_type));
  const obo = external.filter((d) => d.is_obo && d.match_type === 'exact_card');
  const clean = external.filter((d) => d.included_in_average && !d.is_obo && !d.is_context_only);
  const oboRows = obo.slice(0, 12).map((d) => `
    <div class="bbg-context-row">
      <strong>Sold OBO: ${escapeHtml(fmtPrice(d.sale_price))}</strong>
      <span>${escapeHtml([d.source, d.sale_date, d.grade].filter(Boolean).join(' · '))}</span>
      <small>${escapeHtml(d.title || '')}</small>
    </div>`).join('');
  return `
    <section class="bbg-card">
      <h3>External sold comps</h3>
      ${external.length
        ? `<div class="bbg-note">${clean.length} average-safe comps. ${obo.length} OBO sales shown as context, excluded from average.</div>`
        : '<div class="bbg-empty-state small">No exact external comps yet. eBay, Goldin, Fanatics, PWCC, and Heritage placeholders are ready for canonical_comps data.</div>'}
    </section>
    <section class="bbg-card">
      <h3>OBO sales / context only</h3>
      ${obo.length
        ? `<div class="bbg-note">${obo.length} OBO sales passed same-card matching. Shown for context only; not used in median/high/low.</div><div class="bbg-context-list">${oboRows}</div>`
        : '<div class="bbg-empty-state small">No matched OBO context sales for this card.</div>'}
    </section>
    <section class="bbg-card">
      <h3>Context comps / never averaged</h3>
      ${context.length
        ? `<div class="bbg-note">${context.length} context rows present. They remain excluded from fair value.</div>`
        : '<div class="bbg-empty-state small">No context comps yet. Context rows will render hollow dots and remain excluded from averages.</div>'}
    </section>
  `;
}

// Audio callout (advisory) — the spoken auction-call transcript + audio-derived
// identity captured for THIS sale, rendered directly above external sold comps.
// ADVISORY ONLY: image identity always wins and this bar NEVER promotes Trusted /
// Mazified / public (upstream trust_gate_effect=none). Internal file paths are
// deliberately not surfaced. Reads the monolith audio shape:
//   row.audio_transcript = { text_excerpt, duration, model, segments, ... }
//   row.audio_ocr        = { player, brand, set_name, parallel, year, serial,
//                            grade_value, grade_company, evidence_snippets[] }
//   row.audio_reconciliation = { verified_source, final_confidence, match_score }
//   row.audio_evidence_status = "verified" | ...
// The extractor's CURRENT_CARD isolation fix landed in commit 6d383bb at
// 2026-07-01 13:31:45 -0700; audio identities processed before this moment came
// from the pre-fix multi-card extractor (measured ~80% wrong-card on the
// suddendeath canary) and get a warning cue.
const AUDIO_ISOLATION_FIX_EPOCH = Date.parse('2026-07-01T20:31:45+00:00');

function audioCalloutHtml(row) {
  const asObj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
  const tr = asObj(row.audio_transcript);
  const ocr = asObj(row.audio_ocr);
  const recon = asObj(row.audio_reconciliation);
  const excerpt = String(tr.text_excerpt || row.audio_transcript_excerpt || '').trim();
  const snippets = (Array.isArray(ocr.evidence_snippets) ? ocr.evidence_snippets : [])
    .map((s) => String(s || '').trim()).filter(Boolean);
  const verifiedSource = String(recon.verified_source || '').trim();
  const confidence = recon.final_confidence != null && recon.final_confidence !== ''
    ? String(recon.final_confidence) : '';
  // Upstream job-completion status says "verified"; relabel so a bare trust
  // word never sits next to audio-only identity (canonical-labels hygiene).
  const rawStatus = String(
    row.audio_evidence_status || asObj(row.audio_evidence).status || ''
  ).trim();
  const status = rawStatus === 'verified' ? 'complete' : rawStatus;

  if (row.audio_link_suspect) {
    const deltaS = Number(row.audio_link_delta_seconds || 0);
    const deltaLabel = deltaS >= 3600 ? `${(deltaS / 3600).toFixed(1)}h` : `${Math.round(deltaS)}s`;
    return `
    <section class="bbg-card">
      <h3>Audio callout <span class="bbg-advisory-tag">advisory · image wins</span></h3>
      <div class="bbg-audio-suspect">AUDIO LINK SUSPECT — the recorded clip was bound from a different sale (auction number recycled ${escapeHtml(deltaLabel)} apart). Transcript and audio identity are suppressed as likely wrong-card evidence.</div>
    </section>`;
  }

  const idRows = [
    ['audio player', ocr.player],
    ['audio brand', ocr.brand],
    ['audio set', ocr.set_name],
    ['audio parallel', ocr.parallel],
    ['audio year', ocr.year],
    ['audio serial', ocr.serial],
    ['audio grade', ocr.grade_value || ocr.grade_company],
  ].filter(([, v]) => v != null && String(v).trim() !== '');

  const hasAudio = Boolean(excerpt || snippets.length || idRows.length || verifiedSource || status);

  if (!hasAudio) {
    return `
    <section class="bbg-card">
      <h3>Audio callout <span class="bbg-advisory-tag">advisory</span></h3>
      <div class="bbg-empty-state small">No audio callout captured for this sale. Audio evidence is advisory only — captured card image identity always wins.</div>
    </section>`;
  }

  const sourceLabel = ({
    both_agree: 'audio confirms image identity',
    both_disagree_image_won: 'audio disagrees — image wins',
    audio_only: 'audio only (no image identity to check)',
    image_only: 'image only (audio gave no identity)',
    neither: 'no identity isolated',
  })[verifiedSource] || verifiedSource;

  const metaBits = [
    sourceLabel ? `reconcile: ${escapeHtml(sourceLabel)}` : '',
    confidence ? `confidence: ${escapeHtml(confidence)}` : '',
    status ? `job: ${escapeHtml(status)}` : '',
    tr.duration ? `clip: ${escapeHtml(String(tr.duration))}s` : '',
  ].filter(Boolean).join(' · ');

  // Per-field reconciliation chips: which audio tags CONFIRMED the Gemini image
  // identity, which CONFLICTED (image wins), and which the audio FILLED because
  // the row's image identity (card_ocr + api_scan) is blank. Computed
  // index-side (audio_evidence_sidecar._field_tags) against the row's CURRENT
  // image identity — never derived client-side, so a field the image already
  // carries can never be mislabeled as an audio fill.
  const FIELD_NAMES = { player: 'player', brand: 'brand', set_name: 'set', parallel: 'parallel', year: 'year', serial: 'serial', grade_value: 'grade', grade_company: 'grader' };
  const fieldLabel = (f) => FIELD_NAMES[f] || String(f);
  const tags = asObj(row.audio_field_tags);
  const chipBits = [
    ...(Array.isArray(tags.confirmed) ? tags.confirmed : []).map((f) =>
      `<span class="bbg-audio-chip agree" title="audio and image identity agree on this field">✓ ${escapeHtml(fieldLabel(f))} confirmed</span>`),
    ...(Array.isArray(tags.conflicts) ? tags.conflicts : []).filter((c) => c && c.field).map((c) =>
      `<span class="bbg-audio-chip conflict" title="image identity wins; audio heard a different value">✕ ${escapeHtml(fieldLabel(c.field))}: image “${escapeHtml(String(c.image_value ?? '—'))}” wins · audio heard “${escapeHtml(String(c.audio_value ?? '—'))}”</span>`),
    ...Object.entries(asObj(tags.filled)).map(([f, v]) =>
      `<span class="bbg-audio-chip fill" title="image identity is blank for this field; audio supplies the only candidate — advisory fill only">+ ${escapeHtml(fieldLabel(f))}: “${escapeHtml(String(v))}” filled by audio (image blank)</span>`),
  ];
  const reconChips = chipBits.length
    ? `<div class="bbg-audio-chips">${chipBits.join('')}</div>`
    : '';

  const processedEpoch = Date.parse(String(row.audio_processed_at || '').replace(/Z$/, '+00:00'));
  const preFixCue = Number.isFinite(processedEpoch) && processedEpoch < AUDIO_ISOLATION_FIX_EPOCH
    ? '<div class="bbg-audio-prefix-cue">pre-isolation-fix extract — this identity came from the old multi-card extractor and may describe a neighboring card from the same clip</div>'
    : '';

  const snippetHtml = snippets.length
    ? `<div class="bbg-context-list">${snippets.slice(0, 6).map((s) =>
        `<div class="bbg-context-row"><small>${escapeHtml(s)}</small></div>`).join('')}</div>`
    : '';

  return `
    <section class="bbg-card">
      <h3>Audio callout <span class="bbg-advisory-tag">advisory · image wins</span></h3>
      ${metaBits ? `<div class="bbg-note">${metaBits}</div>` : ''}
      ${preFixCue}
      ${reconChips}
      ${excerpt ? `<div class="bbg-audio-excerpt">${escapeHtml(excerpt)}</div>` : ''}
      ${idRows.length ? `<div class="meta kv">${kvRows(idRows)}</div>` : ''}
      ${snippetHtml}
      <p class="bbg-note">Advisory only. Spoken auction-call transcript is internal evidence; it never promotes Trusted / Mazified / public identity.</p>
    </section>`;
}

function rawDataHtml(row) {
  const safe = JSON.stringify(row, null, 2);
  return `
    <details class="bbg-raw">
      <summary>Raw data</summary>
      <pre>${escapeHtml(safe)}</pre>
    </details>
  `;
}

function admitToolHtml(row, writeOpen) {
  const allowed = Array.isArray(row.allowed_decisions) ? row.allowed_decisions : [];
  const allowedSet = new Set(allowed);
  const lookup = row.row_lookup || {};
  const exactMatch = lookup.exact_source_key_match === true;
  const observedIn = String(row.observed_in || '').toLowerCase();
  const isIdentified = observedIn === 'identified';
  const canConfirm = allowedSet.has('confirm');
  let disabled = false;
  let reason = '';
  if (!writeOpen) {
    disabled = true;
    reason = 'write gate closed';
  } else if (!isIdentified) {
    disabled = true;
    reason = 'not an Identified row';
  } else if (!exactMatch) {
    disabled = true;
    reason = 'source key not exact';
  } else if (!canConfirm) {
    disabled = true;
    reason = 'Confirm not allowed for this row';
  }
  const status = disabled
    ? `Blocked: ${reason}.`
    : 'Ready: this posts Confirm through the Identified-to-Trusted promotion path.';
  const disabledAttr = disabled ? 'disabled' : '';
  const reasonAttr = disabled ? `data-disabled-reason="${escapeAttr(reason.replace(/\s+/g, '_').toLowerCase())}"` : '';
  const scopeHint = row.source_key
    ? `MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_SOURCE_KEYS=${row.source_key}`
    : '';
  return `
    <section class="bbg-card admit-tool">
      <div class="admit-tool-head">
        <div>
          <h3>Admit tool</h3>
          <div class="admit-tool-status">${escapeHtml(status)}</div>
        </div>
        <button class="action-btn admit-btn"
          data-decision="confirm"
          ${reasonAttr}
          title="${escapeAttr('Admit this exact Identified row into private Trusted Review. It does not Mazify, publish, or make valuation public.')}"
          ${disabledAttr}>ADMIT TO TRUSTED</button>
      </div>
      <div class="admit-tool-copy">
        <div><b>Effect:</b> append Confirm and promote this exact Identified source row to the private trusted review spine.</div>
        <div><b>Not effect:</b> no Mazified cert, no public chart readiness, no valuation-safe override.</div>
        <div><b>Exact source:</b> <code>${escapeHtml(row.source_key || '')}</code></div>
        ${scopeHint ? `<div><b>Scoped gate hint:</b> <code>${escapeHtml(scopeHint)}</code></div>` : ''}
      </div>
    </section>
  `;
}

function drawerStatusChips(row) {
  const bucket = String(row.trust_bucket || '').toUpperCase();
  const decision = String(row.review_decision || '').toLowerCase();
  if (decision === 'mazified' || row.mazi_cert_id) {
    return ['MAZIFIED REVIEW', 'PRIVATE REVIEW', 'NOT PUBLIC CHART READY'];
  }
  if (String(row.mazi_cert_status || '').toLowerCase() === 'front_trust_candidate'
      || String(row.proof_binding_status || '').toLowerCase() === 'front_overlay_verified') {
    return ['FRONT TRUST CANDIDATE', 'NOT MAZIFIED', 'NOT PUBLIC CHART READY'];
  }
  if (bucket === 'TRUSTED_CANDIDATE') {
    return ['TRUSTED CANDIDATE', 'NOT MAZIFIED', 'NOT PUBLIC CHART READY'];
  }
  if (bucket === 'FLAG_REVIEW' || (Array.isArray(row.risk_flags) && row.risk_flags.length)) {
    return ['FLAGGED REVIEW', 'NOT MAZIFIED', 'NEEDS HUMAN REVIEW', 'NOT PUBLIC CHART READY'];
  }
  return ['NOT TRUSTED', 'NOT MAZIFIED', 'NEEDS HUMAN REVIEW', 'NOT PUBLIC CHART READY'];
}

function renderBloombergDrawer(row, decList, actions, writeOpen) {
  const marketComps = buildMarketComps(row);
  const defaultGroup = defaultGradeGroup(row, marketComps);
  const chips = (row.chips || []).map(c =>
    `<span class="${chipClassFor(c)}">${escapeHtml(c)}</span>`).join('');
  const drawerBadges = safetyBadgesHtml(row);
  const drawerPriceAuditBadges = priceAuditBadgesHtml(row);
  const watermarkedFront = isWatermarked(row);
  const pendingReasons = Array.isArray(row.pending_reasons) && row.pending_reasons.length
    ? row.pending_reasons.map(String).join(', ')
    : 'none';
  const slotF = drawerImg(
    row.image_front_url,
    row.image_front_basename,
    row.image_front_is_ops_display ? 'FRONT · REVIEW IMAGE' : 'FRONT',
    row.image_front_is_ops_display
  );
  const slotB = drawerImg(row.image_back_url, row.image_back_basename, 'BACK');
  const slotP = drawerImg(row.image_proof_url, row.image_proof_basename, 'PROOF');
  const blockers = whyNotMazified(row);
  const blockerHtml = blockers.map((b) => `<li>${escapeHtml(b)}</li>`).join('');
  const blockerChipHtml = blockerChipsHtml(blockers);
  const statusChips = drawerStatusChips(row).map((label) => `<span>${escapeHtml(label)}</span>`).join('');
  return `
    <div class="bbg-drawer-shell">
      <div class="bbg-titlebar">
        <div>
          <div class="bbg-kicker">Operator Bloomberg workbench · write gate ${writeOpen ? 'open' : 'closed'}</div>
          <h2>${escapeHtml(row.title || row.card_name || '(no identity)')}</h2>
          <div class="bbg-subtitle">${escapeHtml(identityLine(row))}</div>
        </div>
        <div class="bbg-status-stack">
          ${statusChips}
        </div>
      </div>

      <div class="bbg-layout">
        <main class="bbg-main">
          ${gradeDropdownHtml(row, marketComps, defaultGroup)}
          <div id="bbgGradeView">${gradeViewHtml(row, marketComps, defaultGroup)}</div>
          <section class="bbg-card">
            <h3>market_comps[] frontend contract</h3>
            <div class="bbg-note">Required dot fields are normalized in the client: source, sale_date, sale_price, grade, condition, match_type, confidence, included_in_average, image_url, thumbnail_url, external_url, reason_excluded, source_record_id, marketplace_item_id, title, seller_or_auction_house, currency, is_obo, is_context_only, is_internal_whatnot, trust_status, data_era, mazified_status.</div>
          </section>
        </main>

        <aside class="bbg-side">
          <section class="bbg-card">
            <h3>Captured images</h3>
            <div class="bbg-capture-grid">
              <div class="bbg-capture">${slotF}<div class="slot-label">FRONT / INTERNAL REVIEW</div></div>
              <div class="bbg-capture">${slotB}<div class="slot-label">BACK / INTERNAL REVIEW</div></div>
              <div class="bbg-capture">${slotP}<div class="slot-label">PROOF / STREAM FRAME</div></div>
            </div>
          </section>

          <section class="bbg-card">
            <h3>Record</h3>
            ${blockerChipHtml}
            <div class="tile-chips">${chips}</div>
            ${drawerPriceAuditBadges}
            ${drawerBadges ? `<div class="bbg-badges">${drawerBadges}</div>` : ''}
            <div class="meta kv">
              ${kvRows([
                ['comp_id', row.comp_id],
                ['source_file', row.source_file],
                ['source_key', row.source_key],
                ['feed_observation_id', row.feed_observation_id],
                ['observed_in', row.observed_in],
                ['review_id', row.review_id],
                ['watermarked_front', watermarkedFront ? 'yes' : 'no'],
                ['queue_reason', row.queue_reason || '(server did not supply)'],
              ])}
            </div>
          </section>

          <section class="bbg-card">
            <h3>Sale metadata</h3>
            <div class="meta kv">
              ${kvRows([
                ['THIS SALE (sold_price)', fmtPrice(row.sold_price)],
                ['sale_date', row.last_sale_date || row.last_seen_at || ''],
                ['seller', row.seller],
                ['auction_number', row.auction_number],
                ['source', 'Whatnot / MAZI captured'],
              ])}
            </div>
          </section>

          <section class="bbg-card">
            <h3>Canonical card descriptors</h3>
            <div class="meta kv">
              ${kvRows([
                ['player', row.player],
                ['year', row.year],
                ['brand', row.brand],
                ['set', row.set_name],
                ['parallel / variant', row.variant || row.parallel || row.insert_type],
                ['card_number', row.canonical_descriptors?.card_number],
                ['grade', row.grade || row.canonical_descriptors?.grade],
                ['grading_company', row.grading_company || row.canonical_descriptors?.grading_company],
                ['cert_number', row.cert_number || row.canonical_descriptors?.cert_number],
                ['slab / graded', row.canonical_descriptors?.slab],
                ['auto', row.auto],
                ['rookie', row.rookie],
                ['patch', row.patch],
                ['rpa', row.rpa || row.canonical_descriptors?.rpa],
                ['serial_numbered', row.serial_numbered],
                ['serial_current', row.serial_current],
                ['serial_total', row.serial_total],
                ['team', row.team],
                ['sport', row.sport],
                ['auction_type', row.auction_type],
                ['card_count', row.card_count],
              ])}
            </div>
          </section>

          <section class="bbg-card">
            <h3>Pipeline status</h3>
            <div class="meta kv">${kvRows(pipelineStatus(row))}</div>
          </section>

          <section class="bbg-card">
            <h3>Why not Mazified</h3>
            ${blockerChipHtml}
            <ul class="bbg-blockers">${blockerHtml}</ul>
          </section>

          <section class="bbg-card">
            <h3>Safety / status fields</h3>
            <div class="meta kv">
              ${kvRows([
                ['front_image_status', row.front_image_status],
                ['back_image_status', row.back_image_status],
                ['proof_image_status', row.proof_image_status],
                ['proof_binding_status', row.proof_binding_status],
                ['mazi_cert_status', row.mazi_cert_status],
                ['publish_ready', row.publish_ready],
                ['raw_publish_ready', row.raw_publish_ready],
                ['effective_publish_block_reason', row.effective_publish_block_reason],
                ['pending_reasons', pendingReasons],
                ['write_gate_disabled', writeOpen ? 'no' : 'yes'],
              ])}
            </div>
          </section>

          <section class="bbg-card">
            <h3>Image files</h3>
            <div class="meta kv">${basenameRows(row)}</div>
          </section>

          <section class="bbg-card">
            <h3>Decision history</h3>
            <div class="decisions-list">${decList}</div>
            <h3>Actions</h3>
            ${admitToolHtml(row, writeOpen)}
            <div class="actions">${actions}</div>
            <div class="muted" style="margin-top:8px;font-size:10px;">
              ${writeOpen
                ? 'Write gate OPEN. Actions write to review_decision_events.'
                : 'Write gate CLOSED (v0). Actions are disabled until ORCHESTRATOR approval.'}
            </div>
          </section>

          ${rawDataHtml(row)}
        </aside>
      </div>
    </div>
  `;
}

function sortVisibleRows(rows) {
  const mode = STATE.sortMode || 'newest';
  const timeVal = (r) => Date.parse(r.last_seen_at || r.feed_generated_at || r.first_seen_at || r.last_sale_date || '') || 0;
  const priceVal = (r) => Number(r.sold_price || 0) || 0;
  return rows.slice().sort((a, b) => {
    if (mode === 'oldest') return timeVal(a) - timeVal(b);
    if (mode === 'price_high') return priceVal(b) - priceVal(a) || timeVal(b) - timeVal(a);
    if (mode === 'price_low') return priceVal(a) - priceVal(b) || timeVal(b) - timeVal(a);
    return timeVal(b) - timeVal(a);
  });
}

function applyCategoryAndSearch() {
  const cat = STATE.category;
  const terms = searchTokens(STATE.filterText);
  const filtered = STATE.rows.filter(r => {
    if (!matchesCategory(r, cat)) return false;
    if (STATE.watermarkedOnly && !isWatermarked(r)) return false;
    if (!terms.length) return true;
    const hay = [
      r.title, r.card_name, r.player, r.brand, r.set_name, r.variant,
      r.parallel, r.insert_type, r.serial_numbered, r.serial_current, r.serial_total,
      r.team, r.sport, r.seller, r.auction_number, r.comp_id, r.source_key,
      r.grade, r.grade_chip, r.grading_company, r.cert_number, r.image_front_basename,
    ]
      .filter(Boolean).join(' ').toLowerCase();
    return terms.every(term => hay.includes(term));
  });
  STATE.visible = sortVisibleRows(filtered);
  renderGrid();
}

function renderGrid() {
  const grid = $('#grid');
  updatePagerControls();
  if (STATE.visible.length === 0) {
    if (STATE.rows.length === 0) {
      grid.innerHTML = '<div class="empty">no rows in this view</div>';
    } else {
      grid.innerHTML = `<div class="empty">no rows match category "${escapeHtml(STATE.category)}"${STATE.filterText ? ` &amp; filter "${escapeHtml(STATE.filterText)}"` : ''}</div>`;
    }
    return;
  }
  // Image counter is per-view: every regrid (new source, new queue, new
  // category, new search term, density flip) restarts the count so the
  // footer reads "N ok / M fail" for what's currently on screen, not a
  // cumulative session total.
  resetImgCounters();
  grid.innerHTML = STATE.visible.map((row, index) => tileHtml(row, index)).join('');
  const start = STATE.totalCount ? ((STATE.page - 1) * STATE.pageSize) + 1 : 0;
  const end = Math.min(STATE.page * STATE.pageSize, STATE.totalCount || STATE.rows.length);
  $('#footer-status').textContent =
    `loaded page ${STATE.page} (${start}-${end} of ${STATE.totalCount || STATE.rows.length}) · showing ${STATE.visible.length} (queue=${STATE.queueContext}, cat=${STATE.category})`;
}

function updatePagerControls() {
  const size = $('#pageSizeSelect');
  if (size) size.value = String(STATE.pageSize);
  const page = $('#pageNumber');
  if (page) page.textContent = String(STATE.page);
  const totalPages = Math.max(1, Math.ceil((STATE.totalCount || STATE.rows.length || 0) / STATE.pageSize));
  const total = $('#pageTotal');
  if (total) total.textContent = String(totalPages);
  const label = $('#rowWindowLabel');
  if (label) {
    const start = STATE.totalCount ? ((STATE.page - 1) * STATE.pageSize) + 1 : (STATE.rows.length ? 1 : 0);
    const end = STATE.totalCount ? Math.min(STATE.page * STATE.pageSize, STATE.totalCount) : STATE.rows.length;
    label.textContent = `${start}-${end} of ${STATE.totalCount || STATE.rows.length || 0}`;
  }
  const prev = $('#prevPageBtn');
  const next = $('#nextPageBtn');
  if (prev) prev.disabled = STATE.page <= 1;
  if (next) next.disabled = STATE.page >= totalPages;
}

// Canonical hard-blocker chip set. Mirror of decisions.py
// _HARD_BLOCKER_CHIPS (compared via String(c).toUpperCase()). Keep the two
// in sync. A tile carrying any of these chips gets a visible hard-block
// strip; its drawer actions are already restricted server-side.
const HARD_BLOCKER_CHIPS = new Set([
  'HARD BLOCKED', 'PROOF_BLOCKED', 'PROOF UNVERIFIED HOLD', 'PROOF_UNVERIFIED_HOLD',
]);
function tileHardBlock(row) {
  const strip = Array.isArray(row && row.hard_block_strip) ? row.hard_block_strip : [];
  if (strip.length) return strip.join(' · ');
  const blockers = whyNotMazified(row);
  if (blockers.length) return blockers.map(blockerLabel).join(' · ');
  const chips = Array.isArray(row && row.chips) ? row.chips : [];
  for (const c of chips) {
    if (HARD_BLOCKER_CHIPS.has(String(c).toUpperCase())) return String(c);
  }
  return null;
}

function sourceShortLabel(row) {
  const view = String(row.source_view || row.observed_in || '').trim();
  const sourceKey = String(row.source_key || '').trim();
  const feedId = row.feed_observation_id == null ? '' : String(row.feed_observation_id);
  if (view) return view.toUpperCase();
  if (sourceKey.includes('::')) return sourceKey.split('::')[0].replace(/\.jsonl?$/i, '');
  if (sourceKey) return sourceKey.slice(0, 18);
  if (feedId) return `FEED ${feedId}`;
  return 'SOURCE UNKNOWN';
}

// ── Row-action policy + bar (APPROVE / SWAP FRONT / DELETE) ───────────────
// docs/MAZIDEX_8504_ROW_ACTIONS_PLAN.md. The positional cap that hid APPROVE
// past the 100th rendered tile is GONE. All three backends have shipped
// (Phase 2 pending-promote, Phase 3 soft-hide+archive DELETE, Phase 4 SWAP
// FRONT rebind), so the full 3-button bar is LIVE. Every button still renders
// 'locked' until the scoped write gate opens (isScoped in tileActionPolicy),
// and the server re-checks review_write_enabled()+row_actions_write_enabled()
// on every POST → the bar ships inert until the operator opens the gate.
const ROW_ACTIONS_V1 = true;

// Pure, unit-tested in tests/row_actions_tests.js. Returns the per-row state of
// each button: 'enabled' | 'locked' | 'hidden' | 'approved'. No DOM, no STATE,
// no index → APPROVE can never again be capped by render position.
function tileActionPolicy(row, opts) {
  opts = opts || {};
  const observed = String(row && row.observed_in || '').toLowerCase();
  const isIdentified = observed === 'identified';
  const isPending = observed === 'pending';
  const inActionScope = isIdentified || isPending; // bar shows on identified + pending only
  const sourceKey = String(row && row.source_key || '').trim();
  const scopeKeys = Array.isArray(opts.scopeKeys) ? opts.scopeKeys.map(String) : [];
  const isScoped = opts.writeEnabled === true
    && (((opts.identifiedAll === true) && isIdentified) || (sourceKey && scopeKeys.includes(sourceKey)));
  const approvedLocal = !!(row && row.__approved === true);

  let approve;
  if (approvedLocal) approve = 'approved';
  else if (!inActionScope) approve = 'hidden';
  else if (isPending && opts.pendingApproveEnabled !== true) approve = 'locked'; // Phase 2 backend
  else if (!isScoped) approve = 'locked';
  else approve = 'enabled';

  let swapFront;
  if (!inActionScope) swapFront = 'hidden';                       // never on trusted
  else if (opts.swapFrontEnabled !== true) swapFront = 'locked';  // Phase 4 backend
  else swapFront = isScoped ? 'enabled' : 'locked';

  let del;
  if (!inActionScope) del = 'hidden';
  else if (opts.deleteEnabled !== true) del = 'locked';           // Phase 3 backend
  else del = isScoped ? 'enabled' : 'locked';

  return { approve: approve, swapFront: swapFront, delete: del };
}

// Read the live write-gate + phase flags out of STATE for the renderer.
function tileActionOpts() {
  const h = STATE.health || {};
  return {
    writeEnabled: STATE.writeEnabled === true,
    identifiedAll: h.review_write_scope_identified_all === true,
    scopeKeys: Array.isArray(h.review_write_scope_source_keys) ? h.review_write_scope_source_keys.map(String) : [],
    pendingApproveEnabled: true,  // Phase 2 backend SHIPPED (pending → Trusted promotion)
    deleteEnabled: true,          // Phase 3 backend SHIPPED (soft-hide + 6TB archive)
    swapFrontEnabled: true,       // Phase 4 backend SHIPPED (next-best front rebind)
  };
}

function approveBtnHtml(state) {
  if (state === 'hidden') return '';
  if (state === 'approved') {
    return `<button type="button" class="tile-admit-btn tile-act tile-act-approve is-approved" disabled title="${escapeAttr('Approved into Trusted Review')}">APPROVED</button>`;
  }
  const locked = state !== 'enabled';
  const title = locked
    ? 'Approve option visible. Open the scoped write gate for this exact source_key to enable.'
    : 'Approve this row into private Trusted Review';
  return `<button type="button" class="tile-admit-btn tile-act tile-act-approve${locked ? ' is-locked' : ''}" ${locked ? 'disabled' : ''} title="${escapeAttr(title)}">APPROVE</button>`;
}

function actStubBtnHtml(kind, state, label, lockedTitle, liveTitle) {
  if (state === 'hidden') return '';
  const locked = state !== 'enabled';
  return `<button type="button" class="tile-act ${kind}${locked ? ' is-locked' : ''}" ${locked ? 'disabled' : ''} title="${escapeAttr(locked ? lockedTitle : liveTitle)}">${label}</button>`;
}

function tileAdmitHtml(row, index = 0) {
  const policy = tileActionPolicy(row, tileActionOpts());
  if (!ROW_ACTIONS_V1) {
    // Legacy surface preserved: APPROVE on identified rows only (positional cap
    // removed) plus the sticky APPROVED state. The staged bar stays dark.
    if (policy.approve === 'approved') return approveBtnHtml('approved');
    if (String(row && row.observed_in || '').toLowerCase() !== 'identified') return '';
    return approveBtnHtml(policy.approve);
  }
  if (policy.approve === 'hidden' && policy.swapFront === 'hidden' && policy.delete === 'hidden') return '';
  const swapHtml = actStubBtnHtml('tile-act-swap', policy.swapFront, 'SWAP FRONT',
    'Swap front — rebinds the next-best front frame from disk (ships in a later phase).',
    'Rebind the next-best front image from disk');
  const delHtml = actStubBtnHtml('tile-act-delete', policy.delete, 'DELETE',
    'Delete — removes this card from 8504 and archives its images (ships in a later phase).',
    'Remove this card from 8504 and archive its images');
  return `<div class="tile-action-bar">${approveBtnHtml(policy.approve)}${swapHtml}${delHtml}</div>`;
}

function tileHtml(row, index = 0) {
  if (row && row.external === true) return externalTileHtml(row);
  const bucket = (row.trust_bucket || '').toLowerCase();
  const chips = collapseChips(row.chips || []).map(c =>
    `<span class="${chipClassFor(c)}">${escapeHtml(c)}</span>`).join('');
  const title = row.title || row.card_name || row.player || '(no identity)';
  const sub = subtitleLine(row);
  const url = row.image_front_url || '';
  const bn = row.image_front_basename || '';
  const fallbackUrl = bn ? `${PROXY_BASE}/${bn}` : '';
  const imgEl = url
    ? `<img class="tile-img" src="${escapeAttr(url)}"
            data-basename="${escapeAttr(bn)}"
            data-fallback="${escapeAttr(fallbackUrl)}"
            onerror="imgFallback(this)" onload="imgOk(this)"
            alt="${escapeHtml(title)}">`
    : `<div class="tile-img-fallback show"><div class="icon">⊘</div>no image</div>`;
  const reviewImageBadge = reviewOnlyDisplayBadge(row);
  const badges = safetyBadgesHtml(row);
  const auditBadges = priceAuditBadgesHtml(row);
  const why = whyHere(row);
  const hardBlock = tileHardBlock(row);
  const sourceLabel = sourceShortLabel(row);
  const identifiedReviewReady = String(row.observed_in || '').toLowerCase() === 'identified'
    && row.front_image_status === 'displayable_for_review'
    && !!row.image_front_url;
  const hardBlockTail = identifiedReviewReady ? 'open details to review' : 'actions restricted';
  const hardBlockTitle = identifiedReviewReady
    ? 'Identified row: proof/cert pending until visual Confirm in the detail drawer'
    : 'Hard blocked — review actions are restricted until this clears';
  const hardBlockStrip = hardBlock
    ? `<div class="tile-hardblock-strip" title="${escapeAttr(hardBlockTitle)}">&#9940; ${escapeHtml(hardBlock)} &middot; ${escapeHtml(hardBlockTail)}</div>`
    : '';
  const tileAdmit = tileAdmitHtml(row, index);
  return `
    <div class="tile bucket-${escapeHtml(bucket)}${hardBlock ? ' is-hardblocked' : ''}"
         data-comp-id="${escapeAttr(row.comp_id)}"
         data-source-key="${escapeAttr(row.source_key || "")}"
         data-feed-observation-id="${escapeAttr(row.feed_observation_id ?? "")}"
         data-review-id="${escapeAttr(row.review_id || "")}"
         data-seller="${escapeAttr(row.seller || "")}"
         data-auction-number="${escapeAttr(row.auction_number ?? "")}"
         data-image-front-basename="${escapeAttr(row.image_front_basename || "")}"
         data-image-proof-basename="${escapeAttr(row.image_proof_basename || "")}">
      ${hardBlockStrip}
      <div class="tile-img-wrap">${imgEl}${reviewImageBadge ? `<div class="tile-review-image-badge">${reviewImageBadge}</div>` : ''}</div>
      <div class="tile-body">
        <div class="tile-title">${escapeHtml(title)}</div>
        ${sub ? `<div class="tile-sub">${escapeHtml(sub)}</div>` : ''}
        <div class="tile-meta">
          <span class="price" title="THIS SALE - Whatnot captured sold price"><span class="price-tag">THIS SALE</span>${fmtPrice(row.sold_price)}</span>
          <span class="source-badge" title="${escapeAttr(row.source_key || row.feed_observation_id || 'source unknown')}">${escapeHtml(sourceLabel)}</span>
          <span class="seller">${escapeHtml(row.seller || '')}</span>
          <span class="auction">#${escapeHtml(row.auction_number ?? '')}</span>
        </div>
        ${tileAdmit}
        <div class="tile-chips">${chips}</div>
        ${why ? `<div class="why-here">Why here: ${escapeHtml(why)}</div>` : ''}
        ${auditBadges}
        ${badges}
      </div>
    </div>`;
}

// Render the row-defect safety badges as their own row, separate from
// posture chips. Always emits all 7 badges; absent fields render as
// "<LABEL>: unknown" with the sev-unknown style (NEVER as "safe").
// External (eBay/Goldin) rows skip this entirely (no Whatnot context).
function safetyBadgesHtml(row) {
  const badges = Array.isArray(row.safety_badges) ? row.safety_badges : [];
  if (badges.length === 0) return '';
  const html = badges.map(b => {
    const sev = String(b.severity || 'unknown');
    const label = String(b.label || b.field || '');
    const disp = b.absent || b.value == null || b.value === ''
      ? `${label}: unknown`
      : (b.display || `${label}: ${b.value}`);
    return `<span class="safety-badge sev-${escapeAttr(sev)}" title="${escapeAttr(b.field || '')}">${escapeHtml(disp)}</span>`;
  }).join('');
  return `<div class="safety-badges">${html}</div>`;
}

function priceAuditBadgesHtml(row) {
  const badges = Array.isArray(row && row.price_audit_badges) ? row.price_audit_badges : [];
  if (!badges.length) return '';
  const html = badges.map((b) => {
    const severity = String(b.severity || 'amber').toLowerCase();
    const label = String(b.label || b.code || 'PRICE_AUDIT');
    const title = b.tooltip || 'Audit-only price ladder check; manual review, no blocking';
    return `<span class="price-audit-badge ${escapeAttr(severity)}" title="${escapeAttr(title)}">${escapeHtml(label)}</span>`;
  }).join('');
  return `<div class="price-audit-badges" title="Audit-only price ladder badges; no trust/state mutation">${html}</div>`;
}

// External reference-comp tile (eBay / Goldin). NO trust chips, NO proof
// chips, NO seller/auction columns, NOT clickable (no drawer opens).
function externalTileHtml(row) {
  const chips = (row.chips || []).map(c =>
    `<span class="${chipClassFor(c)}">${escapeHtml(c)}</span>`).join('');
  const title = row.title || '(no title)';
  const url = row.image_front_url || '';
  const imgEl = url
    ? `<img class="tile-img" src="${escapeAttr(url)}"
            onerror="imgFallback(this)" onload="imgOk(this)"
            referrerpolicy="no-referrer"
            alt="${escapeHtml(title)}">`
    : `<div class="tile-img-fallback show"><div class="icon">⊘</div>no image</div>`;
  const dateStr = row.sold_date ? String(row.sold_date).slice(0, 10) : '';
  const grade = row.grade || '';
  const cond = row.condition || '';
  const srcLabel = (row.source_code || '').toUpperCase();
  return `
    <div class="tile tile-external" data-external="1" data-comp-id="${escapeAttr(row.comp_id)}">
      <div class="tile-img-wrap">${imgEl}</div>
      <div class="tile-body">
        <div class="tile-title">${escapeHtml(title)}</div>
        <div class="tile-sub">${escapeHtml(srcLabel)}${dateStr ? ' · ' + escapeHtml(dateStr) : ''}${grade ? ' · ' + escapeHtml(String(grade)) : (cond ? ' · ' + escapeHtml(String(cond)) : '')}</div>
        <div class="tile-meta">
          <span class="price" title="External reference comp"><span class="price-tag price-tag-comp">COMP</span>${fmtPrice(row.sold_price)}</span>
          <span class="seller muted">${escapeHtml(srcLabel)} ref</span>
        </div>
        <div class="tile-chips">${chips}</div>
      </div>
    </div>`;
}

// ─── image error handler ──────────────────────────────────────────────
window.imgFallback = function (imgEl) {
  const fallback = imgEl.dataset.fallback;
  const triedFallback = imgEl.dataset.triedFallback === '1';
  if (fallback && !triedFallback && imgEl.src !== fallback) {
    imgEl.dataset.triedFallback = '1';
    imgEl.src = fallback;
    return;
  }
  imgEl.src = PLACEHOLDER_SVG;
  imgEl.style.objectFit = 'contain';
  const wrap = imgEl.parentElement;
  if (wrap) {
    const fb = document.createElement('div');
    fb.className = 'tile-img-fallback show';
    fb.innerHTML = '<div class="icon">⊘</div>image unavailable';
    wrap.appendChild(fb);
    imgEl.style.opacity = '0';
  }
  STATE.imgLoadFail += 1;
  bumpStatus();
};

window.imgOk = function (_imgEl) {
  STATE.imgLoadOk += 1;
  bumpStatus();
};

// ─── fetch helpers ───────────────────────────────────────────────────
async function fetchJson(url, opts) {
  const r = await fetch(url, opts || {});
  // Even when the server returns 200 with an `error` field we still
  // parse JSON; non-2xx with a JSON body is also tolerated.
  let j = null;
  try { j = await r.json(); }
  catch (e) {
    throw new Error(`${url} ${r.status} non-json`);
  }
  return j;
}

async function loadHealth() {
  try {
    const j = await fetchJson('/api/v1/health');
    STATE.health = j;
    STATE.writeEnabled = (j.review_write_enabled === true);
    const dbState = j.db_ok ? 'ok' : 'err';
    $('#dbPill').textContent =
      `db ${dbState} · pending ${j.neon_counts?.pending ?? '?'} · trusted ${j.neon_counts?.trusted ?? '?'}`;
    const wpill = $('#writePill');
    const wbanner = $('#writeBanner');
    // Footer write-state truth -- /api/v1/health is the source of truth.
    // If writes are open at the gate but operator policy is restricting
    // them, the orchestrator sets STATE.operatorPolicyRestrict = true.
    const fws = $('#footer-write-state');
    if (j.review_write_enabled === true) {
      wpill.classList.remove('is-write');
      wpill.innerHTML = '<span class="dot"></span> Write OPEN';
      wbanner.textContent = 'WRITES OPEN';
      if (fws) {
        if (STATE.operatorPolicyRestrict) {
          fws.textContent = 'Write gate open; actions restricted by operator policy.';
        } else {
          fws.textContent = 'Write gate OPEN \u00b7 decision writes ACCEPTED \u00b7 POST returns 201';
        }
      }
    } else {
      wpill.classList.add('is-write');
      wpill.innerHTML = '<span class="dot"></span> Writes CLOSED';
      wbanner.textContent = 'WRITES CLOSED / READ-ONLY MODE';
      if (fws) {
        fws.textContent = 'READ-ONLY MODE \u00b7 review_write_enabled=false from /api/v1/health \u00b7 actions disabled';
      }
    }
  } catch (e) {
    $('#dbPill').textContent = `db err (${e.message})`;
    STATE.writeEnabled = false;
    const wpill = $('#writePill');
    const wbanner = $('#writeBanner');
    const fws = $('#footer-write-state');
    if (wpill) {
      wpill.classList.add('is-write');
      wpill.innerHTML = '<span class="dot"></span> Writes CLOSED';
    }
    if (wbanner) wbanner.textContent = 'WRITES CLOSED / HEALTH UNKNOWN';
    if (fws) fws.textContent = 'READ-ONLY MODE \u00b7 health unavailable \u00b7 actions disabled';
  }
}

let _countsInFlight = false;
async function loadCounts() {
  if (_countsInFlight) return;
  _countsInFlight = true;
  try {
    const j = await fetchJson('/api/v1/queue/counts');
    STATE.counts = j.counts || {};
    Object.keys(STATE.counts).forEach(name => {
      $$(`.count[data-count="${name}"]`).forEach(el => {
        el.textContent = STATE.counts[name];
      });
    });
  } catch (e) {
    console.warn('counts err', e);
  }
  try {
    const s = await fetchJson('/api/v1/sources/counts');
    STATE.sourceCounts = s.counts || {};
    Object.keys(STATE.sourceCounts).forEach(name => {
      $$(`.tab-count[data-count-for="${name}"]`).forEach(el => {
        const v = STATE.sourceCounts[name];
        el.textContent = (typeof v === 'number') ? v : '—';
      });
    });
  } catch (e) {
    console.warn('source counts err', e);
  } finally {
    _countsInFlight = false;
  }
}

async function loadStats() {
  try {
    const j = await fetchJson('/api/v1/stats/header');
    // Pipeline funnel — pure row counts, one source per stage (reconciles with tab counts).
    $('#statPending').textContent    = fmtIntShort(j.pending_count ?? 0);
    $('#statIdentified').textContent = fmtIntShort(j.identified_count ?? 0);
    $('#statTrusted').textContent    = fmtIntShort(j.trusted_count ?? 0);
    $('#statMazified').textContent   = fmtIntShort(j.mazified_count ?? 0);
    $('#statExternal').textContent   = fmtIntShort(j.external_count ?? 0);
  } catch (e) {
    console.warn('stats err', e);
  }
}

// ─── operator sort/filter controls ───────────────────────────────────
function ensureWorkbenchControls() {
  if ($('#workbenchControls')) return;
  const catNav = $('#catNav');
  if (!catNav) return;
  const bar = document.createElement('div');
  bar.id = 'workbenchControls';
  bar.className = 'subnav workbench-controls';
  bar.innerHTML = `
    <label class="control-label">Sort
      <select id="sortMode" class="control-select">
        <option value="newest">Newest</option>
        <option value="oldest">Oldest</option>
        <option value="price_high">Highest Price</option>
        <option value="price_low">Lowest Price</option>
      </select>
    </label>
    <label class="control-check"><input id="watermarkedOnly" type="checkbox"> Watermarked only</label>
    <button id="controlledRefresh" class="control-button" type="button"
      title="Refresh counts and reload the current batch only when you are between review batches. Cmd/Ctrl-R does the same controlled refresh.">
      Refresh batch
    </button>
    <span id="refreshModeNote" class="control-note">manual refresh · no background batch reload</span>
  `;
  catNav.parentNode.insertBefore(bar, catNav.nextSibling);
  const sortEl = $('#sortMode');
  const wmEl = $('#watermarkedOnly');
  sortEl.value = STATE.sortMode || 'newest';
  sortEl.addEventListener('change', () => { STATE.sortMode = sortEl.value; loadCurrentView(); });
  wmEl.addEventListener('change', () => { STATE.watermarkedOnly = wmEl.checked; loadCurrentView(); });
  $('#controlledRefresh').addEventListener('click', controlledRefresh);
}

async function controlledRefresh() {
  const btn = $('#controlledRefresh');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
  }
  try {
    await loadHealth();
    await loadStats();
    await loadCounts();
    await loadCurrentView();
    STATE.lastControlledRefreshAt = new Date();
    const stamp = STATE.lastControlledRefreshAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const note = $('#refreshModeNote');
    if (note) note.textContent = `manual refresh complete ${stamp} · no background batch reload`;
    $('#footer-status').textContent = `controlled refresh complete · current batch reloaded at ${stamp}`;
  } catch (e) {
    $('#footer-status').textContent = `controlled refresh err: ${e.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Refresh batch';
    }
  }
}

// ─── source / queue selection ────────────────────────────────────────
function setActiveSource(src) {
  STATE.source = src;
  $$('#sourceNav .tab').forEach(t => {
    const active = t.dataset.source === src;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  // The review subnav is meaningful only under Working Queue source.
  $('#reviewSubnav').hidden = (src !== 'working_queue');
  updateQueueViewNote();
}

function pushUrlState() {
  const params = new URLSearchParams();
  params.set('source', STATE.source);
  if (STATE.source === 'working_queue' && STATE.queue) params.set('queue', STATE.queue);
  if (STATE.filterText) params.set('search', STATE.filterText);
  if (STATE.page && STATE.page > 1) params.set('page', String(STATE.page));
  if (STATE.pageSize && STATE.pageSize !== 200) params.set('limit', String(STATE.pageSize));
  const next = `${location.pathname}?${params.toString()}`;
  history.replaceState(null, '', next);
}

function readInitialUrlState() {
  const params = new URLSearchParams(location.search);
  const src = params.get('source');
  const queue = params.get('queue');
  const search = params.get('search') || params.get('q') || '';
  STATE.initialSourceKey = params.get('source_key') || '';
  STATE.initialCompId = params.get('comp_id') || '';
  const page = Number(params.get('page') || '1');
  const limit = Number(params.get('limit') || params.get('page_size') || '200');
  if (src && Object.prototype.hasOwnProperty.call(SOURCE_TO_QUEUE, src)) STATE.source = src;
  if (queue) STATE.queue = queue;
  STATE.filterText = search;
  STATE.page = Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
  STATE.pageSize = [100, 200, 500, 1000].includes(limit) ? limit : 200;
  const input = $('#filterSearch');
  if (input) input.value = STATE.filterText;
  updatePagerControls();
}

function setActiveReviewQueue(q) {
  STATE.queue = q;
  STATE.source = 'working_queue';
  $$('#reviewSubnav .rtab').forEach(t => {
    t.classList.toggle('is-active', t.dataset.queue === q);
  });
  setActiveSource('working_queue');
  updateQueueViewNote();
}

function setActiveCategory(cat) {
  STATE.category = cat;
  $$('#catNav .subtab').forEach(t => {
    t.classList.toggle('is-active', t.dataset.cat === cat);
  });
}

function setActiveDensity(d) {
  STATE.density = d;
  document.body.classList.toggle('density-tiny',     d === 'tiny');
  document.body.classList.toggle('density-standard', d !== 'tiny');
  $$('.density-btn').forEach(b => {
    const active = b.dataset.density === d;
    b.classList.toggle('is-active', active);
    b.setAttribute('aria-checked', active ? 'true' : 'false');
  });
}

// ─── stale-grid prevention ────────────────────────────────────────────
//
// Two things have to happen at every lane / source switch so an empty
// queue never briefly shows the prior lane's tiles:
//
//   1) The grid is cleared SYNCHRONOUSLY on click (before any await),
//      including STATE.rows and STATE.visible — otherwise a category
//      click or search keystroke that fires while the new fetch is
//      still pending would re-render the previous rows.
//
//   2) A monotonically increasing token is captured at the start of
//      each loadCurrentView. At every await boundary the closure checks
//      `myToken === STATE.activeToken`; if the user has clicked again
//      since this call started, the stale call bails out before it can
//      mutate STATE.rows or call applyCategoryAndSearch.
//
// CSS .grid.is-loading dims the (now-empty) "loading…" placeholder so
// it reads as transient rather than as a long blank state.
function nextLoadToken() {
  STATE.loadToken += 1;
  STATE.activeToken = STATE.loadToken;
  return STATE.loadToken;
}

function startLoading() {
  const grid = $('#grid');
  grid.classList.add('is-loading');
  grid.innerHTML = '<div class="empty loading">loading…</div>';
  // Clear in-memory rows immediately so any synchronous re-render that
  // fires during the pending fetch (category click, search keystroke,
  // density toggle) cannot show stale tiles.
  STATE.rows = [];
  STATE.visible = [];
  // Image counter is per-view; reset it now so the footer doesn't keep
  // adding loads from the prior lane onto the new one.
  resetImgCounters();
}

function endLoading() {
  $('#grid').classList.remove('is-loading');
}

async function loadCurrentView() {
  const myToken = nextLoadToken();
  startLoading();
  // Counters are reset inside startLoading() above.

  const src = STATE.source;

  // ---- External reference comps (eBay / Goldin) ------------------------
  // Served by /api/v1/source which reads external_transactions with the
  // OBO + verified_price_eligible filters baked in. Tiles use the slim
  // externalTileHtml renderer and are NOT clickable.
  if (EXTERNAL_SOURCES.has(src)) {
    try {
      const params = new URLSearchParams({
        name: src,
        limit: String(STATE.pageSize),
        page: String(STATE.page),
        search: STATE.filterText || '',
      });
      const j = await fetchJson(`/api/v1/source?${params.toString()}`);
      if (myToken !== STATE.activeToken) return;  // user clicked away
      if (j.error) {
        STATE.rows = [];
        applyCategoryAndSearch();
        $('#grid').innerHTML = `
          <div class="error">
            source "${escapeHtml(src)}" returned an error<br>
            <small>${escapeHtml(j.detail || j.error)}</small>
          </div>`;
        $('#footer-status').textContent = `external src err: ${j.error}`;
        endLoading();
        return;
      }
      STATE.rows = j.rows || [];
      STATE.totalCount = Number(j.total_count || j.row_count || STATE.rows.length || 0);
      STATE.page = Number(j.page || STATE.page || 1);
      STATE.pageSize = Number(j.limit || STATE.pageSize || 200);
      applyCategoryAndSearch();
      const filt = j.filter ? JSON.stringify(j.filter) : '{}';
      STATE.queueContext = `external:${src}`;
      $('#footer-status').textContent =
        `external ${src}: ${STATE.rows.length} rows · filter=${filt} · REFERENCE ONLY · NOT TRUSTED · NOT MAZIFIED`;
    } catch (e) {
      if (myToken !== STATE.activeToken) return;
      STATE.rows = [];
      applyCategoryAndSearch();
      $('#grid').innerHTML = `<div class="error">network err: ${escapeHtml(e.message)}</div>`;
    } finally {
      if (myToken === STATE.activeToken) endLoading();
    }
    return;
  }

  const mappedQueue = SOURCE_TO_QUEUE[src];
  if (mappedQueue === null) {
    // Unwired source — render placeholder, no fetch needed. No await,
    // so the token check isn't strictly required, but we still gate it
    // in case a slower in-flight fetch resolves after this synchronous
    // render and tries to mutate STATE.
    if (myToken !== STATE.activeToken) return;
    STATE.rows = [];
    STATE.visible = [];
    $('#grid').innerHTML = `
      <div class="placeholder">
        <strong>${escapeHtml(src.replace(/_/g,' '))}</strong> source isn't wired to Neon yet.
        <div class="ph-sub">This tab is intentionally read-only / empty-safe so no error reaches the SPA.</div>
      </div>`;
    $('#footer-status').textContent = `source ${src}: placeholder (no Neon source wired)`;
    endLoading();
    return;
  }

  // For Working Queue, the active review subnav decides the queue.
  let qname = mappedQueue;
  if (src === 'working_queue') qname = STATE.queue || 'working';
  STATE.queueContext = qname;

  try {
    const limit = STATE.pageSize || 200;
    const params = new URLSearchParams({
      name: qname,
      limit: String(limit),
      page: String(STATE.page || 1),
      search: STATE.filterText || '',
      sort: STATE.sortMode || 'newest',
      watermarked_only: STATE.watermarkedOnly ? 'true' : 'false',
    });
    const j = await fetchJson(`/api/v1/queue?${params.toString()}`);
    if (myToken !== STATE.activeToken) return;  // user clicked away
    if (j.error) {
      // Empty-safe error rendering — still no console JSON-parse error.
      STATE.rows = [];
      applyCategoryAndSearch();
      $('#grid').innerHTML = `
        <div class="error">
          queue "${escapeHtml(qname)}" returned an error<br>
          <small>${escapeHtml(j.detail || j.error)}</small>
        </div>`;
      $('#footer-status').textContent = `queue err: ${j.error}`;
      endLoading();
      return;
      }
      if (j.name) STATE.queueContext = j.name;
      STATE.rows = j.rows || [];
      STATE.totalCount = Number(j.total_count || j.row_count || STATE.rows.length || 0);
      STATE.page = Number(j.page || STATE.page || 1);
      STATE.pageSize = Number(j.limit || STATE.pageSize || 200);
    applyCategoryAndSearch();
  } catch (e) {
    if (myToken !== STATE.activeToken) return;
    STATE.rows = [];
    applyCategoryAndSearch();
    $('#grid').innerHTML = `<div class="error">network err: ${escapeHtml(e.message)}</div>`;
  } finally {
    if (myToken === STATE.activeToken) endLoading();
  }
}

// ─── drawer / detail ─────────────────────────────────────────────────
function drawerImg(url, basename, label, reviewOnlyDisplay) {
  if (!url && !basename) {
    return `<div class="slot"><div>no ${escapeHtml(label.toLowerCase())}</div></div>`;
  }
  const fallback = basename ? `${PROXY_BASE}/${basename}` : '';
  return `<div class="slot">
            <img src="${escapeAttr(url || fallback)}"
                 data-fallback="${escapeAttr(fallback)}"
                 onerror="imgFallback(this)" onload="imgOk(this)"
                 alt="${escapeHtml(label)}">
            ${reviewOnlyDisplay ? `<div class="tile-review-image-badge"><span class="chip visual-context-hold">REVIEW IMAGE ONLY</span></div>` : ''}
          </div>`;
}

// === DRAWER: source-key-first, drift-aware, row-state-aware buttons ===
//
// openDrawer accepts the FULL identity object captured from the clicked
// tile dataset (comp_id + source_key + feed_observation_id + review_id +
// seller + auction_number). It passes the optional fields as query params
// so the server can prefer an exact source_key match. The response also
// carries:
//   row.allowed_decisions  -- DB-supported decisions legal for this row
//   row.mazified_button    -- {enabled, reason}
//   source_drift           -- non-null iff resolved row != clicked tile
//
// While drift exists, ALL action buttons are disabled and a banner is
// rendered at the top of the drawer.
async function openDrawer(identity) {
  // Back-compat: callers that pass a plain string still work.
  if (typeof identity === 'string') identity = { compId: identity };
  const compId = identity.compId || '';
  if (!compId && !identity.sourceKey) return;

  const myDrawerToken = ++STATE.drawerToken;
  STATE.activeDrawerToken = myDrawerToken;
  const drawerEl = $('#drawer');
  const drawerContent = $('#drawer-content');
  drawerEl.classList.remove('hidden');
  drawerEl.setAttribute('aria-hidden', 'false');
  drawerEl.dataset.activeCompId = compId || '';
  drawerEl.dataset.activeSourceKey = identity.sourceKey || '';
  drawerContent.innerHTML = '<div class="empty">loading...</div>';

  // Prefer source-key-first lookup. Fake/unknown source_key returns a
  // structured backend error instead of silently opening a sibling row.
  const qs = new URLSearchParams();
  if (compId)                    qs.set('comp_id',             compId);
  if (identity.sourceKey)         qs.set('source_key',          identity.sourceKey);
  if (identity.feedObservationId) qs.set('feed_observation_id', identity.feedObservationId);
  if (identity.reviewId)          qs.set('review_id',           identity.reviewId);
  if (identity.seller)            qs.set('seller',              identity.seller);
  if (identity.auctionNumber)     qs.set('auction_number',      identity.auctionNumber);
  if (identity.imageFrontBasename) qs.set('image_front_basename', identity.imageFrontBasename);
  if (identity.imageProofBasename) qs.set('image_proof_basename', identity.imageProofBasename);
  const qsStr = qs.toString();
  const hasExactTileIdentity = !!(identity.sourceKey || identity.feedObservationId);
  const url = identity.sourceKey
    ? `/api/v1/row-by-source-key?${qsStr}`
    : (hasExactTileIdentity
        ? `/api/v1/row/${encodeURIComponent(compId)}?${qsStr}`
        : `/api/v1/row/${encodeURIComponent(compId)}`);
  if (!hasExactTileIdentity) {
    console.warn('opening drawer without source identity', identity);
  }

  try {
    const j = await fetchJson(url);
    if (myDrawerToken !== STATE.activeDrawerToken) return;
    if (j.error) {
      drawerContent.innerHTML =
        `<div class="error">row err: ${escapeHtml(j.detail || j.error)}</div>`;
      return;
    }
    const row   = j.row || {};
    const decs  = j.decisions || [];
    const drift = j.source_drift || null;
    const siblings = Array.isArray(j.siblings) ? j.siblings : [];

    const writeOpen   = STATE.writeEnabled;
    const driftBlock  = !!(drift && drift.drift === true);
    // Buttons disabled when EITHER write gate closed OR drift detected.
    // Per-row allowed_decisions + mazified_button.enabled further
    // restrict which buttons can be enabled.
    const globallyEnabled = writeOpen && !driftBlock;

    // Server-supplied list of DB-valid decisions legal for THIS row.
    const allowed    = Array.isArray(row.allowed_decisions) ? row.allowed_decisions : [];
    const allowedSet = new Set(allowed);
    const mazState   = row.mazified_button || { enabled: false, reason: 'unknown' };
    const mazBlockers = whyNotMazified(row);
    const alreadyMazified = String(row.review_decision || '').toLowerCase() === 'mazified' || !!row.mazi_cert_id;

    // Drawer chip cluster -- uncollapsed.
    const chips = (row.chips || []).map(c =>
      `<span class="${chipClassFor(c)}">${escapeHtml(c)}</span>`).join('');

    // Action button catalog. Each entry:
    //   type         = decision string the API expects
    //   label        = button text
    //   help         = baseline tooltip
    //   dbSupported  = true iff type is in DB_VALID_DECISIONS server-side
    // Human-readable labels for the visible disabled-reason chips (rendered
    // next to each disabled action, not only as tooltip/data attribute).
    const REASON_LABELS = {
      unmapped_semantic_decision: 'no DB handler',
      write_gate_closed:          'write gate closed',
      source_drift:               'source drift',
      not_allowed_for_row:        'not allowed for this row',
      already_mazified:           'already Mazified',
      image_missing_or_broken:    'image missing/broken',
      identity_ambiguous:         'identity ambiguous',
      proof_different_sale:       'proof = different sale',
      hard_blocker_present:       'hard blocked',
      proof_missing:              'proof missing',
      cert_or_evidence_pending:   'cert/evidence pending',
      public_or_private_image_unsafe: 'image unsafe',
      binding_review:             'binding review',
      capture_hard_blocker:       'capture hard blocker',
      duplicate_or_collision:     'duplicate/collision',
      banned_seller:              'banned seller',
      not_in_allowed_decisions:   'not allowed for this row',
      mazified_disabled:          'disabled',
    };
    const reasonLabel = (r) => REASON_LABELS[r] || (r ? String(r).replace(/_/g, ' ') : '');
    const ACTION_CATALOG = [
      ['mazified',                        'MAZIFIED',                   'Move this tile to private Mazified Review. This does not publish it.', true ],
      ['confirm',                         'Confirm',                    'Confirm this tile as a clean review.',                                 true ],
      ['workable',                        'Workable',                   'Mark this tile workable for downstream processing.',                   true ],
      ['flag',                            'Flag',                       'Route this tile to Flagged Review for a second look.',                 true ],
      ['clear',                           'Clear',                      'Clear this tile of pending flags.',                                    true ],
      ['reject',                          'Reject',                     'Reject this tile from the review path.',                               true ],
      ['deny',                            'Deny',                       'Deny this tile from the review path.',                                 true ],
      // -- Unmapped semantic decisions: rendered DISABLED with a clear
      // 'no DB handler today' tooltip. POSTing them would 422.
      ['needs_identity_enrichment',       'Needs Identity',             'Semantic path -- no DB handler today. Disabled.',                      false],
      ['needs_better_image',              'Needs Better Image',         'Semantic path -- no DB handler today. Disabled.',                      false],
      ['proof_binding_unsubstantiated',   'Proof Unsubstantiated',      'Semantic path -- no DB handler today. Disabled.',                      false],
      ['visual_context_hold',             'Visual Hold',                'Semantic path -- no DB handler today. Disabled.',                      false],
      ['chrome_advanced_to_human_review', 'Advance to Human Review',    'Semantic path -- no DB handler today. Disabled.',                      false],
      ['human_confirmed_for_final_gate',  'Human Confirm Final Gate',   'Semantic path -- no DB handler today. Disabled.',                      false],
      ['rejected_from_public_path',       'Reject from Public Path',    'Semantic path -- no DB handler today. Disabled.',                      false],
      ['hidden_from_work_queue',          'Hide from Queue',            'Semantic path -- no DB handler today. Disabled.',                      false],
    ];

    const actions = ACTION_CATALOG.map(([type, label, help, dbSupported]) => {
      let disabled = false;
      let reason = null;
      if (!dbSupported) {
        disabled = true;
        reason = 'unmapped_semantic_decision';
      } else if (!writeOpen) {
        disabled = true;
        reason = 'write_gate_closed';
      } else if (driftBlock) {
        disabled = true;
        reason = 'source_drift';
      } else if (!allowedSet.has(type)) {
        disabled = true;
        reason = 'not_allowed_for_row';
      }
      // Special-case: MAZIFIED gets the rich reason from mazified_button.
      if (type === 'mazified' && !mazState.enabled) {
        disabled = true;
        if (!reason || reason === 'not_allowed_for_row') {
          reason = mazState.reason || 'mazified_disabled';
        }
      }
      if (type === 'mazified' && (mazBlockers.length > 0 || alreadyMazified)) {
        disabled = true;
        if (!reason || reason === 'not_allowed_for_row') {
          reason = mazBlockers[0] || 'already_mazified';
        }
      }
      const tip = disabled ? `${help} [disabled: ${reason}]` : help;
      const attr = disabled ? 'disabled' : '';
      const dataReason = reason ? `data-disabled-reason="${escapeAttr(reason)}"` : '';
      const btnHtml = `<button class="action-btn" data-decision="${type}" ${dataReason} title="${escapeAttr(tip)}" aria-label="${escapeAttr(label)}" ${attr}>${escapeHtml(label)}</button>`;
      const chipHtml = (type === 'mazified' && disabled && mazBlockers.length)
        ? blockerChipsHtml(mazBlockers)
        : ((disabled && reason)
            ? `<span class="reason-chip" title="${escapeAttr(reason)}">${escapeHtml(reasonLabel(reason))}</span>`
            : '');
      return `<span class="action-cell">${btnHtml}${chipHtml}</span>`;
    }).join('');

    const decList = decs.length === 0
      ? '<div class="muted">no decisions yet</div>'
      : decs.map(d =>
          `<div class="row"><span class="dec">${escapeHtml(d.decision)}</span> ` +
          `by ${escapeHtml(d.reviewer || 'unknown')} ` +
          `<span class="ts">${escapeHtml(d.created_at)}</span></div>`
        ).join('');

    // Drift banner (only if drift). Rendered ABOVE the bloomberg drawer.
    const driftBanner = driftBlock
      ? `<div class="drift-banner" role="alert">
           <div class="drift-banner-title">SOURCE ROW DRIFT</div>
           ${blockerChipsHtml(['source_drift'])}
           <div class="drift-banner-msg">${escapeHtml(drift.message)}</div>
           <div class="drift-banner-diffs">${
             (drift.diffs || []).map(d =>
               `<span class="drift-diff">${escapeHtml(d.field)}: clicked=<code>${escapeHtml(String(d.clicked))}</code> resolved=<code>${escapeHtml(String(d.resolved == null ? 'null' : d.resolved))}</code></span>`
             ).join('')
           }</div>
           <div class="drift-banner-policy">${escapeHtml(drift.policy || 'all actions disabled')}</div>
         </div>`
      : '';

    // Sibling source-row selector: shown when more than one source row
    // collides on this comp_id, so the operator can jump to the EXACT sale
    // (re-resolves the drawer by exact source_key -- read-only navigation,
    // never a write). The currently-shown row is marked and inert.
    const siblingSelector = (siblings.length > 1)
      ? `<div class="sibling-selector" role="group" aria-label="Source rows sharing this comp_id">
           <div class="sibling-selector-title">${siblings.length} source rows share comp_id <code>${escapeHtml(String(compId))}</code> &mdash; showing one. Pick the exact sale:</div>
           <div class="sibling-options">${
             siblings.map(s => {
               const isChosen = !!s.is_chosen;
               const sourceView = String(s.source_view || 'source').toUpperCase();
               const lbl = `${escapeHtml(sourceView)} &middot; ${escapeHtml(s.seller || 'unknown seller')} &middot; #${escapeHtml(String(s.auction_number ?? '?'))} &middot; ${fmtPrice(s.sold_price)}`;
               return `<button class="sibling-opt${isChosen ? ' is-chosen' : ''}"`
                 + ` data-sibling-source-key="${escapeAttr(s.source_key || '')}"`
                 + ` data-sibling-foid="${escapeAttr(s.feed_observation_id ?? '')}"`
                 + ` data-sibling-review-id="${escapeAttr(s.review_id || '')}"`
                 + (isChosen ? ' aria-current="true" disabled' : '')
                 + ` title="${escapeAttr('source_key=' + (s.source_key || '(none)'))}">`
                 + `${lbl}${isChosen ? ' <span class="sibling-chosen-tag">SHOWING</span>' : ''}</button>`;
             }).join('')
           }</div>
         </div>`
      : '';

    if (myDrawerToken !== STATE.activeDrawerToken) return;
    drawerContent.innerHTML = siblingSelector + driftBanner + renderBloombergDrawer(row, decList, actions, globallyEnabled);

    // Sibling navigation is always wired (read-only re-resolve), regardless
    // of write-gate / drift state.
    $$('.sibling-opt').forEach(btn => {
      if (btn.classList.contains('is-chosen')) return;
      btn.addEventListener('click', () => openDrawer({
        compId,
        sourceKey:         btn.dataset.siblingSourceKey || undefined,
        feedObservationId: btn.dataset.siblingFoid || undefined,
        reviewId:          btn.dataset.siblingReviewId || undefined,
      }));
    });

    // Grade/company dropdown: swap the single graph + per-grade aggregates in
    // place. Read-only -- rebuilds from the same row payload, no refetch.
    const gradeSelect = drawerContent.querySelector('#bbgGradeSelect');
    const gradeView = drawerContent.querySelector('#bbgGradeView');
    if (gradeSelect && gradeView) {
      gradeSelect.addEventListener('change', () => {
        gradeView.innerHTML = gradeViewHtml(row, buildMarketComps(row), gradeSelect.value);
      });
    }

    if (globallyEnabled) {
      $$('.action-btn').forEach(btn => {
        if (btn.hasAttribute('disabled')) return;
        btn.addEventListener('click', () => postDecision(compId, btn.dataset.decision, row));
      });
    }
  } catch (e) {
    if (myDrawerToken !== STATE.activeDrawerToken) return;
    drawerContent.innerHTML =
      `<div class="error">network err: ${escapeHtml(e.message)}</div>`;
  }
}

function closeDrawer() {
  STATE.activeDrawerToken = ++STATE.drawerToken;
  const drawerEl = $('#drawer');
  drawerEl.classList.add('hidden');
  drawerEl.setAttribute('aria-hidden', 'true');
  drawerEl.removeAttribute('data-active-comp-id');
  drawerEl.removeAttribute('data-active-source-key');
  $('#drawer-content').innerHTML = '';
}

async function postDecision(compId, decision, row) {
  const body = {
    comp_id: compId,
    source_key: row.source_key,
    review_id: row.review_id,
    source_file: row.source_file,
    auction_number: row.auction_number,
    observed_in: row.observed_in,
    decision: decision,
    reviewer: 'mazidex-admin-ui-v0.0.3',
    notes: '',
    row_meta: {
      seller: row.seller,
      sold_price: row.sold_price,
      title: row.title || row.card_name,
      trust_bucket: row.trust_bucket,
    },
  };
  try {
    const j = await fetchJson('/api/v1/review-decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    // fetchJson parses non-2xx JSON bodies, so branch on the body (same pattern
    // as SWAP FRONT / DELETE). A server rejection — e.g. trusted_gate_blocked:*
    // from the promotion hard-block gate — must NOT render as a success or the
    // row silently reappears after reload with no explanation.
    if (j && j.error) {
      $('#footer-status').textContent =
        `${decision} REJECTED for ${row.source_key || compId}: ${j.error}${j.reason ? ' — ' + j.reason : ''}${j.message ? ' — ' + j.message : ''}`;
      return;
    }
    $('#footer-status').textContent =
      `posted ${decision} → event_id ${j.event_id || '(dry-run)'}`;
    if (decision === 'confirm' && row && String(row.observed_in || '').toLowerCase() === 'identified') {
      const sourceKey = String(row.source_key || '');
      STATE.rows = STATE.rows.map((r) => String(r.source_key || '') === sourceKey ? { ...r, __approved: true } : r);
      STATE.visible = STATE.visible.filter((r) => String(r.source_key || '') !== sourceKey);
      renderGrid();
    }
    await loadCounts();
    closeDrawer();
    await loadCurrentView();
  } catch (e) {
    $('#footer-status').textContent = `decision err: ${e.message}`;
  }
}

// SWAP FRONT (Phase 4): POST the source_key to the swap-front route, which runs
// the out-of-process engine (next-best front rebind, never verified, never a
// promotion). `confirm: true` is required server-side for a real (writing) swap;
// the route re-checks the write gate and returns 503 when it is closed. On a
// committed swap we reload the view so the freshly-bound front image shows.
async function rowActionSwapFront(sourceKey, btn) {
  const prevText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'SWAPPING'; }
  const reenable = () => { if (btn) { btn.disabled = false; btn.textContent = prevText; } };
  try {
    const j = await fetchJson('/api/v1/row-action/swap-front', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_key: sourceKey, confirm: true }),
    });
    if (j && j.error) {
      $('#footer-status').textContent =
        `swap ${sourceKey}: ${j.error}${j.message ? ' — ' + j.message : ''}`;
      reenable();
      return;
    }
    const status = String(j && j.status || '');
    if (status === 'swapped') {
      $('#footer-status').textContent = `SWAP FRONT committed for ${sourceKey} — new front bound`;
      if (btn) btn.textContent = 'SWAPPED';
      await loadCurrentView();
    } else if (status === 'no_alternate_front_available') {
      $('#footer-status').textContent =
        `swap ${sourceKey}: no alternate front frame on disk — nothing changed`;
      reenable();
    } else {
      $('#footer-status').textContent = `swap ${sourceKey}: ${status || 'unknown result'}`;
      reenable();
    }
  } catch (e) {
    $('#footer-status').textContent = `swap err: ${e.message}`;
    reenable();
  }
}

// DELETE (Phase 3): soft-hide the row (reversible via a later `clear`) and
// archive its images. The route is gated CLOSED by default; a closed gate
// returns 503 with an `error` field (fetchJson parses non-2xx JSON, so we branch
// on the body, not the HTTP status). On success we optimistically drop the row
// from the current page, mirroring the APPROVE flow.
async function rowActionDelete(sourceKey, btn) {
  const prevText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'DELETING'; }
  const reenable = () => { if (btn) { btn.disabled = false; btn.textContent = prevText; } };
  try {
    const j = await fetchJson('/api/v1/row-action/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_key: sourceKey, reason: 'operator_8504_delete' }),
    });
    if (j && j.error) {
      $('#footer-status').textContent =
        `delete ${sourceKey}: ${j.error}${j.message ? ' — ' + j.message : ''}`;
      reenable();
      return;
    }
    if (String(j && j.status || '') === 'deleted_from_8504') {
      $('#footer-status').textContent = `DELETED ${sourceKey} from 8504 — reversible via clear`;
      STATE.rows = STATE.rows.filter((r) => String(r.source_key || '') !== sourceKey);
      STATE.visible = STATE.visible.filter((r) => String(r.source_key || '') !== sourceKey);
      renderGrid();
      await loadCounts();
    } else {
      $('#footer-status').textContent = `delete ${sourceKey}: ${j && j.status || 'unknown result'}`;
      reenable();
    }
  } catch (e) {
    $('#footer-status').textContent = `delete err: ${e.message}`;
    reenable();
  }
}

// ─── wiring ──────────────────────────────────────────────────────────
document.addEventListener('click', (ev) => {
  const admitBtn = ev.target.closest('.tile-admit-btn');
  if (admitBtn) {
    ev.preventDefault();
    ev.stopPropagation();
    const tile = admitBtn.closest('.tile');
    const sourceKey = tile ? tile.dataset.sourceKey || '' : '';
    const compId = tile ? tile.dataset.compId || '' : '';
    const row = STATE.rows.find((r) =>
      String(r.source_key || '') === sourceKey && String(r.comp_id || '') === compId
    );
    if (!row) {
      $('#footer-status').textContent = 'approve err: source row not found in current page';
      return;
    }
    admitBtn.disabled = true;
    admitBtn.textContent = 'APPROVING';
    postDecision(row.comp_id, 'confirm', row);
    return;
  }
  const swapBtn = ev.target.closest('.tile-act-swap');
  if (swapBtn) {
    ev.preventDefault();
    ev.stopPropagation();
    if (swapBtn.disabled) return;
    const tile = swapBtn.closest('.tile');
    const sourceKey = tile ? tile.dataset.sourceKey || '' : '';
    if (!sourceKey) {
      $('#footer-status').textContent = 'swap err: no source_key on tile';
      return;
    }
    const ok = window.confirm(
      `SWAP FRONT for\n${sourceKey}?\n\n` +
      'Rebinds the next-best front frame from disk and re-stamps it with the ' +
      'auction number. It never marks the card verified and never promotes it. ' +
      'If no alternate front frame exists this is a no-op.');
    if (!ok) return;
    rowActionSwapFront(sourceKey, swapBtn);
    return;
  }
  const delBtn = ev.target.closest('.tile-act-delete');
  if (delBtn) {
    ev.preventDefault();
    ev.stopPropagation();
    if (delBtn.disabled) return;
    const tile = delBtn.closest('.tile');
    const sourceKey = tile ? tile.dataset.sourceKey || '' : '';
    if (!sourceKey) {
      $('#footer-status').textContent = 'delete err: no source_key on tile';
      return;
    }
    const ok = window.confirm(
      `DELETE from 8504\n${sourceKey}?\n\n` +
      'Soft-hides this card from the workbench and archives its images. ' +
      'It is reversible via a later clear. This removes it from the current view.');
    if (!ok) return;
    rowActionDelete(sourceKey, delBtn);
    return;
  }
  const src = ev.target.closest('#sourceNav .tab');
  if (src) {
    STATE.page = 1;
    setActiveSource(src.dataset.source);
    if (src.dataset.source === 'working_queue') setActiveReviewQueue('working');
    pushUrlState();
    loadCurrentView();
    return;
  }
  const rtab = ev.target.closest('#reviewSubnav .rtab');
  if (rtab) {
    STATE.page = 1;
    setActiveReviewQueue(rtab.dataset.queue);
    pushUrlState();
    loadCurrentView();
    return;
  }
  const sub = ev.target.closest('#catNav .subtab');
  if (sub) {
    setActiveCategory(sub.dataset.cat);
    applyCategoryAndSearch();
    return;
  }
  const d = ev.target.closest('.density-btn');
  if (d) {
    setActiveDensity(d.dataset.density);
    return;
  }
  if (ev.target.id === 'prevPageBtn') {
    STATE.page = Math.max(1, STATE.page - 1);
    pushUrlState();
    loadCurrentView();
    return;
  }
  if (ev.target.id === 'nextPageBtn') {
    const totalPages = Math.max(1, Math.ceil((STATE.totalCount || STATE.rows.length || 0) / STATE.pageSize));
    STATE.page = Math.min(totalPages, STATE.page + 1);
    pushUrlState();
    loadCurrentView();
    return;
  }
  const tile = ev.target.closest('.tile');
  if (tile) {
    // External reference-comp tiles have no Whatnot row to inspect — the
    // drawer's row-detail SQL targets operational_pending_sales /
    // trusted_sales_current, neither of which contains ext-* synthetic
    // ids. Skip drawer open on external tiles to avoid a 404 round-trip.
    if (tile.dataset.external === '1') {
      $('#footer-status').textContent =
        'external reference comp — read-only, no review actions available';
      return;
    }
    openDrawer({
      compId:              tile.dataset.compId,
      sourceKey:           tile.dataset.sourceKey || "",
      feedObservationId:   tile.dataset.feedObservationId || "",
      reviewId:            tile.dataset.reviewId || "",
      seller:              tile.dataset.seller || "",
        auctionNumber:       tile.dataset.auctionNumber || "",
        imageFrontBasename:  tile.dataset.imageFrontBasename || "",
        imageProofBasename:  tile.dataset.imageProofBasename || "",
      });
    return;
  }
  if (ev.target.id === 'drawer-close') {
    closeDrawer();
    return;
  }
});

document.addEventListener('keydown', (ev) => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key && ev.key.toLowerCase() === 'r') {
    ev.preventDefault();
    controlledRefresh();
    return;
  }
  if (ev.key === 'Escape') closeDrawer();
});

// search input — debounced filter on already-loaded rows
let _searchTimer = null;
document.addEventListener('input', (ev) => {
  if (ev.target && ev.target.id === 'filterSearch') {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => {
      STATE.filterText = ev.target.value || '';
      STATE.page = 1;
      pushUrlState();
      loadCurrentView();
    }, 250);
  }
});

document.addEventListener('change', (ev) => {
  if (ev.target && ev.target.id === 'pageSizeSelect') {
    STATE.pageSize = Number(ev.target.value || 200);
    STATE.page = 1;
    pushUrlState();
    loadCurrentView();
  }
});

(async function init() {
  ensureWorkbenchControls();
  readInitialUrlState();
  await loadHealth();
  await loadStats();
  loadCounts();
  setActiveSource(STATE.source || 'identified');
  await loadCurrentView();
  if (STATE.initialSourceKey || STATE.initialCompId) {
    openDrawer({
      sourceKey: STATE.initialSourceKey,
      compId: STATE.initialCompId,
    });
  }
  const note = $('#refreshModeNote');
  if (note) note.textContent = 'manual refresh · Cmd/Ctrl-R or Refresh batch between review batches';
})();
