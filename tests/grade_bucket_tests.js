// Phase 4 grade-bucketing regression tests for the Bloomberg drawer.
//
// Rule under test: the internal Whatnot anchor (THIS SALE) is a SINGLE-GRADE
// data point. It must appear ONLY in the card's own grade tab -- never plotted
// on an off-grade chart (a PSA 9 sale must not show on the PSA 10 chart). The
// drawer also defaults to the card's OWN grade group so THIS SALE is always the
// first anchor shown, even when that grade has 0 external comps yet.
//
// Extracts the REAL functions from static/app.js (prefix + brace match) and
// evaluates them, so we test shipped code, not a copy. Prefix matching keeps the
// test robust to the signature change (compsForGradeGroup gains a `row` param).
//
// Run: node tests/grade_bucket_tests.js   (exit 0 = pass)
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
// One shared scope holds the RAW_GRADE_GROUP const + every interdependent fn, so
// the extracted functions can call each other exactly as they do in app.js.
// eslint-disable-next-line no-eval
const M = eval('(function(){\n'
  + 'const RAW_GRADE_GROUP = ' + JSON.stringify(RAW_GRADE_GROUP) + ';\n'
  + extractFn('compGradeGroup') + '\n'
  + extractFn('cardGradeGroup') + '\n'
  + extractFn('gradeGroupCounts') + '\n'
  + extractFn('compsForGradeGroup') + '\n'
  + extractFn('gradeGroupSoldComps') + '\n'
  + extractFn('gradeGroupAggregates') + '\n'
  + extractFn('defaultGradeGroup') + '\n'
  + 'return { compGradeGroup, cardGradeGroup, gradeGroupCounts, compsForGradeGroup,'
  + ' gradeGroupSoldComps, gradeGroupAggregates, defaultGradeGroup };\n'
  + '})()');

let pass = 0, fail = 0;
function eq(label, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log(`PASS ${label}`); }
  else { fail++; console.error(`FAIL ${label}\n  got:  ${g}\n  want: ${w}`); }
}

// ---- Fixtures -------------------------------------------------------------
// The screenshot scenario: a PSA 9 card whose corpus has only PSA 10 comps.
// Every external comp echoes the card's own (target) grade = PSA 9.
const ext = (group, price, over) => Object.assign({
  is_internal_whatnot: false,
  grade_group: group,
  sale_price: price,
  is_obo: false,
  target_grade_company: 'PSA',
  target_grade_value: '9',
}, over || {});

const internalPSA9 = {
  is_internal_whatnot: true,
  grade_group: 'PSA 9',
  grade: 'PSA 9',
  sale_price: 135,
};

const row = { grade: 'PSA 9', sold_price: 135 };
const marketComps = [
  internalPSA9,
  ext('PSA 10', 270),
  ext('PSA 10', 250, { is_obo: true }),
];

const subset10 = M.compsForGradeGroup(row, marketComps, 'PSA 10');
const subset9 = M.compsForGradeGroup(row, marketComps, 'PSA 9');

// 1. internal dot is EXCLUDED from an off-grade (PSA 10) subset  [the bug fix]
eq('internal excluded from PSA10 subset', subset10.some((d) => d.is_internal_whatnot), false);

// 2. external PSA 10 comps still bucket INTO the PSA 10 subset
eq('PSA10 externals present in PSA10 subset', subset10.filter((d) => !d.is_internal_whatnot).length, 2);

// 3. internal dot IS included in the card's own (PSA 9) subset
eq('internal present in PSA9 subset', subset9.some((d) => d.is_internal_whatnot), true);

// 4. off-grade externals never leak into the card's own (PSA 9) subset
eq('no PSA10 externals leak into PSA9 subset', subset9.filter((d) => !d.is_internal_whatnot).length, 0);

// 5. raw card: internal anchor only in Raw/Ungraded, never on a graded chart
const rawRow = { grade: '', sold_price: 40 };
const rawInternal = { is_internal_whatnot: true, grade_group: '', grade: '', sale_price: 40 };
const rawComps = [
  rawInternal,
  ext('PSA 10', 270, { target_grade_company: '', target_grade_value: '' }),
];
eq('raw internal present in Raw/Ungraded',
   M.compsForGradeGroup(rawRow, rawComps, 'Raw/Ungraded').some((d) => d.is_internal_whatnot), true);
eq('raw internal excluded from PSA10',
   M.compsForGradeGroup(rawRow, rawComps, 'PSA 10').some((d) => d.is_internal_whatnot), false);

// 6. gradeGroupCounts NEVER counts the internal anchor
const counts = M.gradeGroupCounts(marketComps);
eq('counts: PSA10 == 2 (internal not counted)', counts.get('PSA 10'), 2);
eq('counts: no PSA9 externals', counts.get('PSA 9') || 0, 0);

// 7. defaultGradeGroup opens on the card's OWN grade, even with 0 same-grade comps
eq('default = card own grade (PSA 9)', M.defaultGradeGroup(row, marketComps), 'PSA 9');
// raw card with no stated grade -> defaults to Raw/Ungraded (its own group)
eq('default raw card = Raw/Ungraded', M.defaultGradeGroup(rawRow, rawComps), 'Raw/Ungraded');

// 8. aggregates: THIS SALE renders ONLY on the card's own grade tab
const agg10 = M.gradeGroupAggregates(row, subset10);
eq('PSA10 tab: THIS SALE null (off-grade)', agg10.thisSale, null);
eq('PSA10 tab: compAvg 270 (OBO excluded)', agg10.compAvg, 270);
eq('PSA10 tab: compCount 1', agg10.compCount, 1);
eq('PSA10 tab: oboCount 1', agg10.oboCount, 1);

const agg9 = M.gradeGroupAggregates(row, subset9);
eq('PSA9 tab: THIS SALE 135 (own grade)', agg9.thisSale, 135);
eq('PSA9 tab: compCount 0', agg9.compCount, 0);
eq('PSA9 tab: hasComps false', agg9.hasComps, false);

console.log(`\ngrade_bucket_tests: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
