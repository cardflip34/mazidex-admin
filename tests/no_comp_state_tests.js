// Zero-comp deliberate-state banner tests for the Bloomberg drawer (2026-07-16).
//
// Rule under test: a Trusted card with ZERO exact-card comps renders a
// deliberate, explained banner (noCompStateHtml) IN PLACE OF the bare
// empty-chart text. Buckets: rare_by_design (unique 1/1 vs low print run),
// pending_fill (deep scrub still gathering), coverage_gap (nothing matched).
// Unknown/missing state must fall back to the coverage_gap copy.
//
// Extracts the REAL functions from static/app.js (prefix + brace match) and
// evaluates them, so we test shipped code, not a copy.
//
// Run: node tests/no_comp_state_tests.js   (exit 0 = pass)
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

function extractFn(name) {
  const sig = 'function ' + name + '(';
  const start = js.indexOf(sig);
  if (start < 0) { console.error('FAIL: ' + name + ' not found in app.js'); process.exit(1); }
  let i = js.indexOf('{', start), depth = 0, end = -1;
  for (; i < js.length; i++) {
    if (js[i] === '{') depth++;
    else if (js[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
  }
  return js.slice(start, end);
}

// noCompStateHtml depends on the app's own escapers; pull all three so the
// extracted function runs exactly as it does in app.js.
// eslint-disable-next-line no-eval
const M = eval('(function(){\n'
  + extractFn('escapeHtml') + '\n'
  + extractFn('escapeAttr') + '\n'
  + extractFn('noCompStateHtml') + '\n'
  + 'return { noCompStateHtml };\n'
  + '})()');

let pass = 0, fail = 0;
function check(label, html, wantSubstrings, banSubstrings) {
  const missing = (wantSubstrings || []).filter((s) => !html.includes(s));
  const leaked = (banSubstrings || []).filter((s) => html.includes(s));
  if (!missing.length && !leaked.length) { pass++; console.log(`PASS ${label}`); }
  else {
    fail++;
    console.error(`FAIL ${label}`);
    missing.forEach((s) => console.error(`  missing: ${s}`));
    leaked.forEach((s) => console.error(`  must not contain: ${s}`));
    console.error(`  html: ${html}`);
  }
}

// (1) rare_by_design / unique -> THIS SALE IS the market
check('unique 1/1 banner',
  M.noCompStateHtml({ bucket: 'rare_by_design', detail: { rarity_tier: 'unique', print_run: 1, matched_token: '1/1' } }),
  [
    'UNIQUE CARD (1/1) — no comparable sales can exist. This sale IS the market price.',
    'data-no-comp-bucket="rare_by_design"',
    'bbg-empty-state',
    'bbg-no-comp-state',
  ],
  ['Print run', 'Building the comp history', 'No exact-match sales']);

// (2) rare_by_design / low print run -> sparse-by-design copy with the real /N
check('ultra_rare /5 banner',
  M.noCompStateHtml({ bucket: 'rare_by_design', detail: { rarity_tier: 'ultra_rare', print_run: 5, matched_token: '/5' } }),
  ['Print run /5 — comparable sales are expected to be sparse.', 'data-no-comp-bucket="rare_by_design"'],
  ['UNIQUE CARD', 'Building the comp history', 'No exact-match sales']);

check('rare /25 banner',
  M.noCompStateHtml({ bucket: 'rare_by_design', detail: { rarity_tier: 'rare', print_run: 25 } }),
  ['Print run /25 — comparable sales are expected to be sparse.'],
  ['UNIQUE CARD']);

// (3) pending_fill -> comps being gathered
check('pending_fill banner',
  M.noCompStateHtml({ bucket: 'pending_fill', detail: { job_status: 'running' } }),
  [
    'Building the comp history — live sources are being scraped now.',
    'data-no-comp-bucket="pending_fill"',
  ],
  ['UNIQUE CARD', 'Print run', 'No exact-match sales']);

// (4) coverage_gap -> honest nothing-matched copy
check('coverage_gap banner',
  M.noCompStateHtml({ bucket: 'coverage_gap', detail: { job_status: 'completed' } }),
  [
    'No exact-match sales found in any connected source yet.',
    'data-no-comp-bucket="coverage_gap"',
  ],
  ['UNIQUE CARD', 'Print run', 'Building the comp history']);

// (5) unknown / missing / malformed states fall back to coverage_gap copy
check('unknown bucket falls back to coverage_gap',
  M.noCompStateHtml({ bucket: 'mystery_bucket' }),
  ['No exact-match sales found in any connected source yet.', 'data-no-comp-bucket="coverage_gap"'],
  ['UNIQUE CARD', 'Print run', 'Building the comp history']);

check('null state falls back to coverage_gap',
  M.noCompStateHtml(null),
  ['No exact-match sales found in any connected source yet.', 'data-no-comp-bucket="coverage_gap"']);

check('empty object falls back to coverage_gap',
  M.noCompStateHtml({}),
  ['No exact-match sales found in any connected source yet.', 'data-no-comp-bucket="coverage_gap"']);

// (6) rare_by_design with a missing/garbage print_run never fabricates a number
check('rare_by_design missing print_run renders /?',
  M.noCompStateHtml({ bucket: 'rare_by_design', detail: { rarity_tier: 'rare' } }),
  ['Print run /? — comparable sales are expected to be sparse.']);

// (7) the marketChartHtml empty path actually swaps in the banner (source-level
// wiring check: the state-aware branch must sit inside the emptyText ternary).
const chartSrc = extractFn('marketChartHtml');
check('marketChartHtml wires row.no_comp_state into the empty-chart slot',
  chartSrc,
  ['row.no_comp_state', 'noCompStateHtml(row.no_comp_state)', 'No exact external comps yet.']);

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
