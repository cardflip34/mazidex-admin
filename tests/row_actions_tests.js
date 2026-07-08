// Phase 1 row-action policy tests (docs/MAZIDEX_8504_ROW_ACTIONS_PLAN.md).
//
// Rules under test:
//  - APPROVE appears on EVERY identified + pending row -- the old index<100
//    positional cap is GONE (proven by the policy taking no index AND a
//    source-level pin that the literal is removed from app.js).
//  - SWAP FRONT + DELETE show on identified + pending ONLY (never trusted) and
//    stay 'locked' until their Phase 3/4 backends ship (per-button opts flags).
//  - APPROVE on pending stays 'locked' until the Phase-2 backend.
//  - The scoped write gate governs enabled vs locked (unchanged semantics).
//
// Extracts the REAL tileActionPolicy from static/app.js (prefix + brace match)
// and evaluates it, so we test shipped code, not a copy.
// Run: node tests/row_actions_tests.js   (exit 0 = pass)
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

// eslint-disable-next-line no-eval
const M = eval('(function(){\n'
  + extractFn('tileActionPolicy') + '\n'
  + 'return { tileActionPolicy };\n'
  + '})()');

let pass = 0, fail = 0;
function eq(label, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log(`PASS ${label}`); }
  else { fail++; console.error(`FAIL ${label}\n  got:  ${g}\n  want: ${w}`); }
}

// ---- Fixtures -------------------------------------------------------------
// identified-all gate open (the common production write-scope mode).
const OPEN = { writeEnabled: true, identifiedAll: true, scopeKeys: [] };
const ident = (over) => Object.assign({ observed_in: 'identified', source_key: 'sk-id' }, over || {});
const pend  = (over) => Object.assign({ observed_in: 'pending', source_key: 'sk-pd' }, over || {});
const trust = (over) => Object.assign({ observed_in: 'trusted', source_key: 'sk-tr' }, over || {});

// ---- APPROVE --------------------------------------------------------------
// 1. enabled on identified when the identified-all gate is open
eq('approve enabled identified (gate open)', M.tileActionPolicy(ident(), OPEN).approve, 'enabled');
// 2. enabled on identified via explicit scopeKeys (not identifiedAll)
eq('approve enabled identified (scopeKeys)',
  M.tileActionPolicy(ident(), { writeEnabled: true, scopeKeys: ['sk-id'] }).approve, 'enabled');
// 3. locked on identified when the write gate is closed
eq('approve locked identified (gate closed)',
  M.tileActionPolicy(ident(), { writeEnabled: false, identifiedAll: true }).approve, 'locked');
// 4. pending stays locked until the Phase-2 backend, even when scoped
eq('approve pending locked (no phase2)',
  M.tileActionPolicy(pend(), { writeEnabled: true, scopeKeys: ['sk-pd'] }).approve, 'locked');
// 5. pending enabled once Phase-2 flag + scope
eq('approve pending enabled (phase2 + scope)',
  M.tileActionPolicy(pend(), { writeEnabled: true, scopeKeys: ['sk-pd'], pendingApproveEnabled: true }).approve, 'enabled');
// 6. hidden on trusted
eq('approve hidden trusted', M.tileActionPolicy(trust(), OPEN).approve, 'hidden');
// 7. sticky APPROVED state wins regardless of gate
eq('approve approved sticky',
  M.tileActionPolicy(ident({ __approved: true }), { writeEnabled: false }).approve, 'approved');

// ---- SWAP FRONT (identified + pending only; never trusted) ----------------
// 8. hidden on trusted
eq('swap hidden trusted', M.tileActionPolicy(trust(), OPEN).swapFront, 'hidden');
// 9. locked on identified until Phase-4
eq('swap locked identified (no phase4)', M.tileActionPolicy(ident(), OPEN).swapFront, 'locked');
// 10. locked on pending until Phase-4
eq('swap locked pending (no phase4)',
  M.tileActionPolicy(pend(), { writeEnabled: true, scopeKeys: ['sk-pd'] }).swapFront, 'locked');
// 11. enabled when Phase-4 flag + scoped
eq('swap enabled (phase4 + scope)',
  M.tileActionPolicy(ident(), Object.assign({ swapFrontEnabled: true }, OPEN)).swapFront, 'enabled');
// 12. locked when Phase-4 flag set but the gate is closed
eq('swap locked (phase4, gate closed)',
  M.tileActionPolicy(ident(), { writeEnabled: false, identifiedAll: true, swapFrontEnabled: true }).swapFront, 'locked');

// ---- DELETE (identified + pending) ----------------------------------------
// 13. hidden on trusted
eq('delete hidden trusted', M.tileActionPolicy(trust(), OPEN).delete, 'hidden');
// 14. locked on identified until Phase-3
eq('delete locked identified (no phase3)', M.tileActionPolicy(ident(), OPEN).delete, 'locked');
// 15. locked on pending until Phase-3
eq('delete locked pending (no phase3)',
  M.tileActionPolicy(pend(), { writeEnabled: true, scopeKeys: ['sk-pd'] }).delete, 'locked');
// 16. enabled when Phase-3 flag + scoped (identified)
eq('delete enabled identified (phase3 + scope)',
  M.tileActionPolicy(ident(), Object.assign({ deleteEnabled: true }, OPEN)).delete, 'enabled');
// 17. enabled when Phase-3 flag + scoped (pending via scopeKeys)
eq('delete enabled pending (phase3 + scopeKeys)',
  M.tileActionPolicy(pend(), { writeEnabled: true, scopeKeys: ['sk-pd'], deleteEnabled: true }).delete, 'enabled');

// ---- unknown/external observed_in → all hidden ----------------------------
const extPol = M.tileActionPolicy({ observed_in: '', source_key: 'x' }, OPEN);
eq('approve hidden unknown', extPol.approve, 'hidden');
eq('swap hidden unknown', extPol.swapFront, 'hidden');
eq('delete hidden unknown', extPol.delete, 'hidden');

// ---- source-level pin: the positional cap is GONE -------------------------
eq('source: index<100 cap removed from app.js', /index\s*<\s*100/.test(js), false);

// ---- tileActionOpts(): the 3 phase flags are now TRUE (backends shipped) ---
// tileActionOpts references STATE as a free var; direct eval binds it from this
// scope, and the returned fn closes over it, so we exercise the REAL config.
function loadOpts(STATE) {
  // eslint-disable-next-line no-eval
  const fn = eval('(' + extractFn('tileActionOpts') + ')');
  return fn();
}
const optsClosed = loadOpts({ health: {}, writeEnabled: false });
eq('opts pendingApproveEnabled now true', optsClosed.pendingApproveEnabled, true);
eq('opts deleteEnabled now true', optsClosed.deleteEnabled, true);
eq('opts swapFrontEnabled now true', optsClosed.swapFrontEnabled, true);
eq('opts writeEnabled derives false (gate closed)', optsClosed.writeEnabled, false);
const optsOpen = loadOpts({
  health: { review_write_scope_identified_all: true, review_write_scope_source_keys: ['a', 'b'] },
  writeEnabled: true,
});
eq('opts writeEnabled derives true', optsOpen.writeEnabled, true);
eq('opts identifiedAll derives true', optsOpen.identifiedAll, true);
eq('opts scopeKeys derives from health', optsOpen.scopeKeys, ['a', 'b']);

// ---- integration: REAL opts (flags true) through tileActionPolicy -----------
// POS: gate open + in scope → the shipped bar is live.
const realIdentAll = loadOpts({ health: { review_write_scope_identified_all: true }, writeEnabled: true });
eq('integration swap enabled (real opts + identified scope)',
  M.tileActionPolicy(ident(), realIdentAll).swapFront, 'enabled');
eq('integration delete enabled (real opts + identified scope)',
  M.tileActionPolicy(ident(), realIdentAll).delete, 'enabled');
eq('integration approve pending enabled (real opts + scopeKey)',
  M.tileActionPolicy(pend(), loadOpts({ health: { review_write_scope_source_keys: ['sk-pd'] }, writeEnabled: true })).approve, 'enabled');
// NEG: gate CLOSED → every button stays locked even though the flags are true.
const realClosed = loadOpts({ health: {}, writeEnabled: false });
eq('integration swap locked (real opts, gate closed)',
  M.tileActionPolicy(ident(), realClosed).swapFront, 'locked');
eq('integration delete locked (real opts, gate closed)',
  M.tileActionPolicy(ident(), realClosed).delete, 'locked');
eq('integration approve locked (real opts, gate closed)',
  M.tileActionPolicy(ident(), realClosed).approve, 'locked');

// ---- source pins: wiring is present -----------------------------------------
eq('source: ROW_ACTIONS_V1 flipped on', /const\s+ROW_ACTIONS_V1\s*=\s*true/.test(js), true);
eq('source: swap click delegation present', /closest\('\.tile-act-swap'\)/.test(js), true);
eq('source: delete click delegation present', /closest\('\.tile-act-delete'\)/.test(js), true);
eq('source: confirm dialog before write', /window\.confirm\(/.test(js), true);
eq('source: swap endpoint referenced', /\/api\/v1\/row-action\/swap-front/.test(js), true);
eq('source: delete endpoint referenced', /\/api\/v1\/row-action\/delete/.test(js), true);

// ---- async handler behavior (rowActionSwapFront / rowActionDelete) ----------
// Extract the REAL async handlers (extractFn drops the leading `async`, so we
// re-add it) and bind their free deps from this scope via direct eval.
function loadHandlers(deps) {
  const { fetchJson, $, STATE, renderGrid, loadCounts, loadCurrentView } = deps;
  // eslint-disable-next-line no-eval
  return eval('(function(){\n'
    + 'async ' + extractFn('rowActionSwapFront') + '\n'
    + 'async ' + extractFn('rowActionDelete') + '\n'
    + 'return { rowActionSwapFront, rowActionDelete };\n'
    + '})()');
}
function fakeEnv(fetchResult, opts) {
  opts = opts || {};
  const rec = { fetch: [], render: 0, counts: 0, view: 0 };
  const footer = { textContent: '' };
  const STATE = { rows: opts.rows || [], visible: opts.visible || [] };
  const H = loadHandlers({
    fetchJson: async (url, o) => {
      rec.fetch.push({ url, method: o.method, body: JSON.parse(o.body) });
      if (fetchResult instanceof Error) throw fetchResult;
      return fetchResult;
    },
    $: (sel) => (sel === '#footer-status' ? footer : { textContent: '' }),
    STATE,
    renderGrid: () => { rec.render++; },
    loadCounts: async () => { rec.counts++; },
    loadCurrentView: async () => { rec.view++; },
  });
  return { H, rec, footer, STATE };
}

async function runAsync() {
  const swapBtn = () => ({ disabled: false, textContent: 'SWAP FRONT' });
  const delBtn = () => ({ disabled: false, textContent: 'DELETE' });

  // SWAP POS: committed swap → correct POST body, SWAPPED, view reloaded.
  {
    const b = swapBtn();
    const { H, rec, footer } = fakeEnv({ action: 'swap_front', status: 'swapped', write_committed: true });
    await H.rowActionSwapFront('sk1', b);
    eq('swap POSTs swap-front url', rec.fetch[0].url, '/api/v1/row-action/swap-front');
    eq('swap POSTs {source_key,confirm:true}', rec.fetch[0].body, { source_key: 'sk1', confirm: true });
    eq('swap success btn → SWAPPED', b.textContent, 'SWAPPED');
    eq('swap success btn stays disabled', b.disabled, true);
    eq('swap success reloads view', rec.view, 1);
    eq('swap success footer committed', /committed/.test(footer.textContent), true);
  }
  // SWAP NEG (valid no-op): no alternate front → re-enabled, NO view reload.
  {
    const b = swapBtn();
    const { H, rec, footer } = fakeEnv({ status: 'no_alternate_front_available' });
    await H.rowActionSwapFront('sk1', b);
    eq('swap no-op btn re-enabled', b.disabled, false);
    eq('swap no-op btn text restored', b.textContent, 'SWAP FRONT');
    eq('swap no-op no view reload', rec.view, 0);
    eq('swap no-op footer explains', /no alternate/.test(footer.textContent), true);
  }
  // SWAP NEG (gate closed 503): body carries error → re-enabled, no reload.
  {
    const b = swapBtn();
    const { H, rec, footer } = fakeEnv({ error: 'row_actions_write_disabled', message: 'gate closed' });
    await H.rowActionSwapFront('sk1', b);
    eq('swap gate-closed btn re-enabled', b.disabled, false);
    eq('swap gate-closed no view reload', rec.view, 0);
    eq('swap gate-closed footer shows error', /row_actions_write_disabled/.test(footer.textContent), true);
  }
  // SWAP NEG (network throw): re-enabled + err message.
  {
    const b = swapBtn();
    const { H, footer } = fakeEnv(new Error('boom'));
    await H.rowActionSwapFront('sk1', b);
    eq('swap throw btn re-enabled', b.disabled, false);
    eq('swap throw footer err', /swap err/.test(footer.textContent), true);
  }

  // DELETE POS: soft-hide → correct POST, row dropped from STATE, re-render+counts.
  {
    const b = delBtn();
    const seed = [{ source_key: 'sk1' }, { source_key: 'sk2' }];
    const { H, rec, footer, STATE } = fakeEnv({ status: 'deleted_from_8504' },
      { rows: seed.slice(), visible: seed.slice() });
    await H.rowActionDelete('sk1', b);
    eq('delete POSTs delete url', rec.fetch[0].url, '/api/v1/row-action/delete');
    eq('delete POSTs {source_key,reason}', rec.fetch[0].body, { source_key: 'sk1', reason: 'operator_8504_delete' });
    eq('delete drops row from STATE.rows', STATE.rows.map((r) => r.source_key), ['sk2']);
    eq('delete drops row from STATE.visible', STATE.visible.map((r) => r.source_key), ['sk2']);
    eq('delete re-renders grid', rec.render, 1);
    eq('delete refreshes counts', rec.counts, 1);
    eq('delete success footer', /DELETED/.test(footer.textContent), true);
  }
  // DELETE NEG (409 not deletable): body error → row kept, btn re-enabled, no render.
  {
    const b = delBtn();
    const seed = [{ source_key: 'sk1' }];
    const { H, rec, STATE } = fakeEnv({ error: 'row_not_deletable' },
      { rows: seed.slice(), visible: seed.slice() });
    await H.rowActionDelete('sk1', b);
    eq('delete error keeps row', STATE.rows.map((r) => r.source_key), ['sk1']);
    eq('delete error btn re-enabled', b.disabled, false);
    eq('delete error no render', rec.render, 0);
    eq('delete error no counts refresh', rec.counts, 0);
  }
  // DELETE NEG (network throw): re-enabled.
  {
    const b = delBtn();
    const { H, footer } = fakeEnv(new Error('neterr'), { rows: [], visible: [] });
    await H.rowActionDelete('sk1', b);
    eq('delete throw btn re-enabled', b.disabled, false);
    eq('delete throw footer err', /delete err/.test(footer.textContent), true);
  }
}

runAsync().then(() => {
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}).catch((e) => {
  console.error('FAIL runAsync threw:', e && e.stack || e);
  process.exit(1);
});
