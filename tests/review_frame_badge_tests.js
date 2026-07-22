// Tests for the review-frame badge: rows whose displayed front is the recovered
// ops_display fallback (a full Whatnot stream frame, NOT a cropped card front)
// must render a plain, prominent warning; every other row must render nothing.
//
// Extracts the REAL reviewOnlyDisplayBadge from static/app.js (prefix + brace
// match) and evaluates it, so we test shipped code, not a copy.
// Run: node tests/review_frame_badge_tests.js   (exit 0 = pass)
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

// Minimal escapeAttr shim (the real one just HTML-escapes attribute text).
function escapeAttr(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// eslint-disable-next-line no-eval
const isReviewFrame = eval('(' + extractFn('isReviewFrame') + ')');
// reviewOnlyDisplayBadge calls isReviewFrame; eval it with that in scope.
// eslint-disable-next-line no-eval
const reviewOnlyDisplayBadge = eval(
  '(function(){ ' + extractFn('isReviewFrame') + '; return '
  + extractFn('reviewOnlyDisplayBadge') + '; })()'
);

let failed = 0;
function check(name, cond) {
  console.log((cond ? '[PASS] ' : '[FAIL] ') + name);
  if (!cond) failed++;
}

// --- POSITIVE: server-set is_review_frame (the primary, reliable signal) ---
const reviewRow = { is_review_frame: true };
const reviewOut = reviewOnlyDisplayBadge(reviewRow);
check('is_review_frame row returns a non-empty badge', reviewOut.length > 0);
check('badge names it a STREAM FRAME (not a card)', /STREAM FRAME/.test(reviewOut));
check('badge warns there is NO CARD CROP', /NO CARD CROP/.test(reviewOut));
check('badge uses the review-frame-warn class', /review-frame-warn/.test(reviewOut));
check('tooltip explains no clean cropped front', /no clean cropped card front/i.test(reviewOut));

// --- POSITIVE: legacy ops-display fallback still triggers the badge ---
const opsOut = reviewOnlyDisplayBadge({ image_front_is_ops_display: true });
check('legacy ops-display row still returns the badge', /STREAM FRAME/.test(opsOut));
check('isReviewFrame true for ops-display', isReviewFrame({ image_front_is_ops_display: true }) === true);
check('isReviewFrame true for is_review_frame', isReviewFrame({ is_review_frame: true }) === true);

// --- NEGATIVE: a clean official front -> no badge ---
check('clean official front returns empty string',
  reviewOnlyDisplayBadge({ is_review_frame: false, image_front_is_ops_display: false, image_front_url: '/x.jpg' }) === '');
check('missing flags returns empty string',
  reviewOnlyDisplayBadge({ image_front_url: '/x.jpg' }) === '');
check('isReviewFrame false for a clean front', isReviewFrame({ image_front_url: '/x.jpg' }) === false);
check('null row returns empty string', reviewOnlyDisplayBadge(null) === '');
check('undefined row returns empty string', reviewOnlyDisplayBadge(undefined) === '');

if (failed) { console.error('\n' + failed + ' FAILED'); process.exit(1); }
console.log('\nALL TESTS PASSED');
process.exit(0);
