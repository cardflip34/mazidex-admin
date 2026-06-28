// Task 4 P5/P6 regression tests for the honest price aggregator.
// Extracts the REAL compAggregates() source from static/app.js (brace-matched)
// and evaluates it, so we test shipped code, not a copy.
// Run: node tests/price_honesty_tests.js   (exit 0 = pass)
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const start = js.indexOf('function compAggregates(marketComps) {');
if (start < 0) { console.error('FAIL: compAggregates not found in app.js'); process.exit(1); }
// brace-match from the first "{" after the signature
let i = js.indexOf('{', start), depth = 0, end = -1;
for (; i < js.length; i++) {
  if (js[i] === '{') depth++;
  else if (js[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
const src = js.slice(start, end);
// eslint-disable-next-line no-eval
const compAggregates = eval('(' + src.replace('function compAggregates', 'function') + ')');

let pass = 0, fail = 0;
function eq(label, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; }
  else { fail++; console.error(`FAIL ${label}\n  got:  ${g}\n  want: ${w}`); }
}

const ext = (price, over) => Object.assign({
  is_internal_whatnot: false, included_in_average: true, is_obo: false,
  is_context_only: false, match_type: 'exact_card', trust_status: 'trusted',
  sale_price: price,
}, over || {});
const internal = (price) => ({ is_internal_whatnot: true, sale_price: price });

// 1. empty -> all null, no comps
eq('empty', compAggregates([]),
   { thisSale: null, compHigh: null, compAvg: null, compCount: 0, hasComps: false });

// 2. only the internal Whatnot dot -> THIS SALE set, but it is NEVER a comp
eq('internal-only', compAggregates([internal(45)]),
   { thisSale: 45, compHigh: null, compAvg: null, compCount: 0, hasComps: false });

// 3. internal + two average-safe externals -> high/avg from externals only
eq('two-safe', compAggregates([internal(45), ext(100), ext(200, { match_type: 'close_variant', trust_status: 'TRUSTED' })]),
   { thisSale: 45, compHigh: 200, compAvg: 150, compCount: 2, hasComps: true });

// 4. OBO is excluded from comps even when otherwise eligible
eq('obo-excluded', compAggregates([internal(45), ext(100), ext(200), ext(999, { is_obo: true })]),
   { thisSale: 45, compHigh: 200, compAvg: 150, compCount: 2, hasComps: true });

// 5. context-only excluded
eq('context-excluded', compAggregates([ext(100), ext(200), ext(999, { is_context_only: true })]),
   { thisSale: null, compHigh: 200, compAvg: 150, compCount: 2, hasComps: true });

// 6. not-trusted excluded
eq('nottrusted-excluded', compAggregates([ext(100), ext(999, { trust_status: 'not trusted' })]),
   { thisSale: null, compHigh: 100, compAvg: 100, compCount: 1, hasComps: true });

// 7. context match_type (player_context) excluded
eq('player_context-excluded', compAggregates([ext(100), ext(999, { match_type: 'player_context' })]),
   { thisSale: null, compHigh: 100, compAvg: 100, compCount: 1, hasComps: true });

// 8. included_in_average=false excluded
eq('not-included-excluded', compAggregates([ext(100), ext(999, { included_in_average: false })]),
   { thisSale: null, compHigh: 100, compAvg: 100, compCount: 1, hasComps: true });

// 9. zero/negative price excluded
eq('zero-price-excluded', compAggregates([ext(100), ext(0), ext(-5)]),
   { thisSale: null, compHigh: 100, compAvg: 100, compCount: 1, hasComps: true });

console.log(`price_honesty_tests: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
