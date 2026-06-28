// Source-key drawer-safety regression tests for the new pure frontend
// helpers added 2026-05-30: tileHardBlock() (tile hard-block strip, #5)
// and the REASON_LABELS/reasonLabel() pair (visible reason chips, #4).
//
// Like price_honesty_tests.js, this extracts the REAL shipped source from
// static/app.js (brace-/line-matched, eval'd in an IIFE) so we test shipped
// code rather than a copy. Run: node tests/drawer_safety_tests.js  (exit 0 = pass)
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

let pass = 0, fail = 0;
function eq(label, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; }
  else { fail++; console.error(`FAIL ${label}\n  got:  ${g}\n  want: ${w}`); }
}

// ---- extract HARD_BLOCKER_CHIPS + tileHardBlock (module scope) ----------
const cStart = js.indexOf('const HARD_BLOCKER_CHIPS = new Set([');
const fStart = js.indexOf('function tileHardBlock(row) {', cStart);
if (cStart < 0 || fStart < 0) { console.error('FAIL: tileHardBlock not found'); process.exit(1); }
let i = js.indexOf('{', fStart), depth = 0, fEnd = -1;
for (; i < js.length; i++) {
  if (js[i] === '{') depth++;
  else if (js[i] === '}') { depth--; if (depth === 0) { fEnd = i + 1; break; } }
}
const hbSnippet = js.slice(cStart, fEnd);
// eslint-disable-next-line no-eval
const tileHardBlock = eval('(function(){ ' + hbSnippet + ' return tileHardBlock; })()');

// ---- extract REASON_LABELS + reasonLabel (inside openDrawer) ------------
const rlStart = js.indexOf('const REASON_LABELS = {');
const rlAnchor = js.indexOf('const reasonLabel = (r) =>', rlStart);
if (rlStart < 0 || rlAnchor < 0) { console.error('FAIL: reasonLabel not found'); process.exit(1); }
const rlEnd = js.indexOf('\n', rlAnchor);
const rlSnippet = js.slice(rlStart, rlEnd);
// eslint-disable-next-line no-eval
const reasonLabel = eval('(function(){ ' + rlSnippet + ' return reasonLabel; })()');

// ==== tileHardBlock ======================================================
// Mirror of decisions.py _row_has_hard_blocker (str(c).upper() membership).
eq('hb empty chips -> null', tileHardBlock({ chips: [] }), null);
eq('hb no chips field -> null', tileHardBlock({}), null);
eq('hb null row -> null', tileHardBlock(null), null);
eq('hb non-array chips -> null', tileHardBlock({ chips: 'HARD BLOCKED' }), null);
eq('hb exact HARD BLOCKED -> matched', tileHardBlock({ chips: ['x', 'HARD BLOCKED'] }), 'HARD BLOCKED');
eq('hb lowercase hard blocked -> matched (case-insensitive)', tileHardBlock({ chips: ['hard blocked'] }), 'hard blocked');
eq('hb PROOF_BLOCKED -> matched', tileHardBlock({ chips: ['PROOF_BLOCKED'] }), 'PROOF_BLOCKED');
eq('hb PROOF UNVERIFIED HOLD -> matched', tileHardBlock({ chips: ['PROOF UNVERIFIED HOLD'] }), 'PROOF UNVERIFIED HOLD');
eq('hb PROOF_UNVERIFIED_HOLD -> matched', tileHardBlock({ chips: ['PROOF_UNVERIFIED_HOLD'] }), 'PROOF_UNVERIFIED_HOLD');
eq('hb unrelated chip -> null', tileHardBlock({ chips: ['TRUSTED SNAPSHOT', 'PROOF VERIFIED'] }), null);
eq('hb returns first matching chip in original casing', tileHardBlock({ chips: ['Proof_Blocked'] }), 'Proof_Blocked');

// ==== reasonLabel ========================================================
eq('rl source_drift', reasonLabel('source_drift'), 'source drift');
eq('rl unmapped_semantic_decision', reasonLabel('unmapped_semantic_decision'), 'no DB handler');
eq('rl write_gate_closed', reasonLabel('write_gate_closed'), 'write gate closed');
eq('rl already_mazified', reasonLabel('already_mazified'), 'already Mazified');
eq('rl proof_different_sale', reasonLabel('proof_different_sale'), 'proof = different sale');
eq('rl hard_blocker_present', reasonLabel('hard_blocker_present'), 'hard blocked');
eq('rl unknown reason -> underscores to spaces', reasonLabel('some_unknown_reason'), 'some unknown reason');
eq('rl empty string -> empty', reasonLabel(''), '');
eq('rl null -> empty', reasonLabel(null), '');

console.log(`drawer_safety_tests: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
