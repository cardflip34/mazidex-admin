// mazidex-admin — AUDIO CALLOUT drawer bar tests.
//
// Scope: static/app.js audioCalloutHtml() — the advisory audio bar rendered
//        above "External sold comps" in the Bloomberg drawer.
// Mode:  extracts the REAL functions from static/app.js by brace-matching
//        (no copies, no DOM). Safe to run any time.
// Usage: cd ~/mazidex-admin && node tests/audio_callout_tests.js
'use strict';
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

function extractFn(name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('not found: ' + name);
  let i = src.indexOf('{', start), depth = 0;
  for (let j = i; j < src.length; j++) {
    const ch = src[j];
    if (ch === '{') depth++;
    else if (ch === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error('unbalanced: ' + name);
}
function extractConst(name) {
  const m = src.match(new RegExp('const ' + name + ' = [^;]+;'));
  if (!m) throw new Error('const not found: ' + name);
  return m[0];
}

const { audioCalloutHtml } = new Function(
  [extractFn('escapeHtml'), extractFn('kvRows'),
   extractConst('AUDIO_ISOLATION_FIX_EPOCH'), extractFn('audioCalloutHtml'),
   'return { audioCalloutHtml };'].join('\n\n')
)();

let pass = 0, fail = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { console.log('[PASS] ' + name); pass++; }
  else { console.log('[FAIL] ' + name + (detail ? ' — ' + detail : '')); fail++; failures.push(name); }
}

// ── A. Normal post-fix audio row ──
const post = {
  comp_id: 'MC-1',
  audio_transcript: { text_excerpt: 'Jaden Daniels rookie here we go live', duration: 23.94 },
  audio_ocr: { player: 'Jayden Daniels', brand: 'Panini', evidence_snippets: ['Jaden Daniels rookie'] },
  audio_reconciliation: { verified_source: 'audio_only', final_confidence: '0.4' },
  audio_evidence_status: 'verified',
  audio_processed_at: '2026-07-02T22:25:36+00:00',
};
const hA = audioCalloutHtml(post);
check('A1 renders excerpt + identity + snippets', hA.includes('bbg-audio-excerpt')
  && hA.includes('Jayden Daniels') && hA.includes('bbg-context-list'));
check('A2 advisory label present', hA.includes('advisory') && hA.includes('image wins'));
check('A3 trust-word relabeled: no bare "status: verified"', !hA.includes('status: verified')
  && hA.includes('job: complete'));
check('A4 post-fix row has NO pre-fix cue', !hA.includes('bbg-audio-prefix-cue'));

// ── B. Pre-fix extract gets the warning cue ──
const pre = JSON.parse(JSON.stringify(post));
pre.audio_processed_at = '2026-06-28T06:43:05+00:00';
const hB = audioCalloutHtml(pre);
check('B1 pre-fix extract shows the cue', hB.includes('bbg-audio-prefix-cue')
  && hB.includes('pre-isolation-fix'));
check('B2 cue does not suppress the content (still advisory-visible)',
  hB.includes('bbg-audio-excerpt') && hB.includes('Jayden Daniels'));
const zform = JSON.parse(JSON.stringify(post));
zform.audio_processed_at = '2026-06-28T06:43:05Z';
check('B3 Z-suffix timestamps also parse for the cue',
  audioCalloutHtml(zform).includes('bbg-audio-prefix-cue'));
const noTs = JSON.parse(JSON.stringify(post));
delete noTs.audio_processed_at;
check('B4 missing processed_at -> no cue, no crash',
  !audioCalloutHtml(noTs).includes('bbg-audio-prefix-cue'));

// ── C. Link-suspect row suppresses everything but the warning ──
const suspect = {
  comp_id: 'MC-10204',
  audio_link_suspect: true,
  audio_link_delta_seconds: 17476,
  audio_evidence_status: 'verified',
};
const hC = audioCalloutHtml(suspect);
check('C1 suspect renders the AUDIO LINK SUSPECT warning', hC.includes('bbg-audio-suspect')
  && hC.includes('AUDIO LINK SUSPECT'));
check('C2 suspect shows human delta (4.9h)', hC.includes('4.9h'), hC);
check('C3 suspect suppresses excerpt/identity/snippets', !hC.includes('bbg-audio-excerpt')
  && !hC.includes('kv-row') && !hC.includes('bbg-context-list'));
check('C4 suspect never shows placeholder text', !hC.includes('No audio callout captured'));
const suspectSmall = audioCalloutHtml({ audio_link_suspect: true, audio_link_delta_seconds: 90 });
check('C5 sub-hour delta rendered in seconds', suspectSmall.includes('90s'));

// ── D. Placeholder + gate alignment ──
const hD = audioCalloutHtml({ comp_id: 'X-1', title: 'no audio here' });
check('D1 placeholder for no-audio row', hD.includes('No audio callout captured'));
const playerOnly = audioCalloutHtml({ audio_ocr: { player: 'Donovan Mitchell' } });
check('D2 player-only identity renders the bar, NOT the placeholder (gate aligned with sidecar)',
  playerOnly.includes('Donovan Mitchell') && !playerOnly.includes('No audio callout captured'));

// ── F. Reconciliation tag chips (server-computed audio_field_tags contract) ──
// Chips come ONLY from row.audio_field_tags (sidecar-computed against the
// row's current card_ocr/api_scan) — never derived client-side from ocr/recon.
const tagged = JSON.parse(JSON.stringify(post));
tagged.audio_reconciliation.verified_source = 'both_disagree_image_won';
tagged.audio_field_tags = {
  confirmed: ['year'],
  conflicts: [{ field: 'player', image_value: 'DEVON ACHANE', audio_value: 'Drake London' }],
  filled: { set_name: 'Mega Sharpie EX', parallel: 'refractor' },
};
const hF = audioCalloutHtml(tagged);
check('F1 confirmed chip renders for agreed field', hF.includes('bbg-audio-chip agree')
  && hF.includes('year confirmed'));
check('F2 conflict chip names both values, image wins', hF.includes('bbg-audio-chip conflict')
  && hF.includes('image “DEVON ACHANE” wins') && hF.includes('audio heard “Drake London”'));
check('F3 filled chips label image-blank fill', hF.includes('bbg-audio-chip fill')
  && hF.includes('Mega Sharpie EX') && hF.includes('refractor') && hF.includes('image blank'));
check('F4 field names humanized (set_name -> set)', hF.includes('+ set:'), hF);
check('F5 verified_source relabeled human-readable', hF.includes('audio disagrees — image wins')
  && !hF.includes('reconcile: both_disagree_image_won'));
check('F6 NO tags -> NO chips even with ocr identity + recon present (a940 protection)',
  !audioCalloutHtml(post).includes('bbg-audio-chip'));
const xssTag = audioCalloutHtml({
  audio_reconciliation: { verified_source: 'audio_only' },
  audio_field_tags: {
    conflicts: [{ field: 'player', image_value: '<b>X</b>', audio_value: 'y' }],
    filled: { brand: '<script>alert(1)</script>' },
  },
});
check('F7 chip values escaped (conflict + fill)', !xssTag.includes('<script>')
  && xssTag.includes('&lt;script&gt;') && xssTag.includes('&lt;b&gt;X&lt;/b&gt;'));

// ── E. Safety ──
const xss = audioCalloutHtml({ audio_transcript: { text_excerpt: '<img src=x onerror=alert(1)>' },
  audio_reconciliation: { verified_source: 'audio_only' } });
check('E1 excerpt HTML escaped', !xss.includes('<img src=x') && xss.includes('&lt;img'));
check('E2 no /Users/ path in any branch', ![hA, hB, hC, hD].some((h) => h.includes('/Users/')));

console.log('\n' + (fail ? fail + ' FAILED: ' + failures.join(', ') : 'ALL ' + pass + ' PASSED'));
process.exit(fail ? 1 : 0);
