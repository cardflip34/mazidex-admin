// Price-fence regression tests for the Bloomberg drawer (2026-07-14).
//
// Rule under test: a comp flagged price_outlier by app.py's grade-group median
// fence is EXCLUDED from COMP AVG / COMP HIGH but stays in the chart subset as
// a context dot. The 2026-07-14 audit found 612 grade tabs whose average was
// distorted >=3x by one comp ($7,950 "/10 PSA9" gold on a raw Wemby chart).
//
// Extracts the REAL functions from static/app.js (prefix + brace match) and
// evaluates them, so we test shipped code, not a copy.
//
// Run: node tests/price_outlier_tests.js   (exit 0 = pass)
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

const RAW_GRADE_GROUP = 'Raw/Ungraded';
// eslint-disable-next-line no-eval
const M = eval('(function(){\n'
  + 'const RAW_GRADE_GROUP = ' + JSON.stringify(RAW_GRADE_GROUP) + ';\n'
  + extractFn('gradeGroupSoldComps') + '\n'
  + extractFn('gradeGroupAggregates') + '\n'
  + extractFn('averageSafeExternalComps') + '\n'
  + 'return { gradeGroupSoldComps, gradeGroupAggregates, averageSafeExternalComps };\n'
  + '})()');

let pass = 0, fail = 0;
function eq(label, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log(`PASS ${label}`); }
  else { fail++; console.error(`FAIL ${label}\n  got:  ${g}\n  want: ${w}`); }
}

// ---- Fixtures ---------------------------------------------------------------
// The live Wemby scenario in miniature: raw tab with cheap sales + one flagged
// $7,950 outlier + one OBO.
const ext = (price, over) => Object.assign({
  is_internal_whatnot: false,
  is_obo: false,
  price_outlier: '',
  sale_price: price,
  sale_date: '2026-06-10',
  grade_group: RAW_GRADE_GROUP,
}, over || {});

const subset = [
  ext(30),
  ext(29.99),
  ext(36.95),
  ext(14.5),
  ext(7950, { price_outlier: 'high' }),
  ext(250, { is_obo: true }),
  { is_internal_whatnot: true, is_obo: false, price_outlier: '', sale_price: 97, sale_date: '2026-07-12', grade_group: RAW_GRADE_GROUP },
];

// (a) sold comps used for the average exclude the flagged outlier + OBO + internal
eq('soldComps excludes outlier/obo/internal',
  M.gradeGroupSoldComps(subset).map((d) => d.sale_price),
  [30, 29.99, 36.95, 14.5]);

// (b) aggregates: avg/high computed WITHOUT the $7,950; outlierCount reported
const agg = M.gradeGroupAggregates({}, subset);
eq('compHigh without outlier', agg.compHigh, 36.95);
eq('compAvg without outlier', Math.round(agg.compAvg * 100) / 100, 27.86);
eq('compCount without outlier', agg.compCount, 4);
eq('outlierCount reported', agg.outlierCount, 1);
eq('oboCount unchanged', agg.oboCount, 1);

// (c) an OBO that also breaches the fence stays an OBO (never double-counted
// as an averaged comp, never counted in outlierCount which is non-OBO only)
const subset2 = subset.map((d) => d.is_obo ? Object.assign({}, d, { price_outlier: 'high' }) : d);
const agg2 = M.gradeGroupAggregates({}, subset2);
eq('obo outlier not in outlierCount', agg2.outlierCount, 1);
eq('obo outlier still in oboCount', agg2.oboCount, 1);

// (d) low-side outlier is excluded from the average too
const subset3 = [ext(30), ext(28), ext(33), ext(31), ext(0.5, { price_outlier: 'low' })];
const agg3 = M.gradeGroupAggregates({}, subset3);
eq('low outlier excluded from avg', Math.round(agg3.compAvg * 100) / 100, 30.5);
eq('low outlier counted', agg3.outlierCount, 1);

// (e) no flags -> behavior identical to before the fence
const subset4 = [ext(10), ext(12), ext(14)];
const agg4 = M.gradeGroupAggregates({}, subset4);
eq('no-flag avg unchanged', agg4.compAvg, 12);
eq('no-flag outlierCount 0', agg4.outlierCount, 0);

// (f) marketStats input helper also skips outliers (growth/avg honesty)
eq('averageSafeExternalComps excludes outlier',
  M.averageSafeExternalComps(subset).map((d) => d.sale_price),
  [30, 29.99, 36.95, 14.5]);

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
