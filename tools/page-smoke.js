#!/usr/bin/env node
// Loads both generated panels in a real DOM and fails if either is broken.
//
// The pages are generated, they share most of their code, and the two
// transports are edited independently -- so it is entirely possible to break
// one page's boot while the other stays fine. That happened: an edit to the
// emulator transport truncated the sequencer engine, buildSequencer() threw,
// and everything after it (including the whole section nav) never ran. The
// page still looked plausible, just with no nav and no sequencer.
//
//   npm install jsdom
//   node tools/page-smoke.js
//
// Checks per page:
//   - no uncaught JavaScript errors during load
//   - the section nav actually got built
//   - every .section has the heading buildNav() needs
//   - the controls the panel depends on exist
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const PAGES = [
  { file: path.join(ROOT, 'index.html'), name: 'hardware panel' },
  { file: path.join(ROOT, 'emulator', 'emulator.html'), name: 'emulator' },
];

const REQUIRED_IDS = [
  'nav', 'sections', 'settingsBtn', 'settingsPanel', 'presetSelect',
  'log', 'chordGrid', 'strum', 'piano', 'pads', 'seqRows', 'gpModes',
];

function check(page) {
  let html = fs.readFileSync(page.file, 'utf8');
  // The wasm module is not present in a bare DOM; the page must survive that.
  html = html.replace(/<script src="8b8\.js"><\/script>/, '');

  const errors = [];
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(w) {
      w.requestAnimationFrame = () => 0;
      w.navigator.requestMIDIAccess = undefined;
      w.AudioContext = function () {};
      w.onerror = (msg, src, line, col, err) => errors.push(err || new Error(msg));
    },
  });

  const d = dom.window.document;
  const problems = [];

  for (const e of errors) {
    problems.push('uncaught error: ' + (e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e));
  }

  const nav = d.getElementById('nav');
  if (!nav) problems.push('no #nav element');
  else if (nav.children.length === 0) problems.push('section nav is empty -- buildNav() did not run or found nothing');

  const sections = [...d.querySelectorAll('.section')];
  if (sections.length < 4) problems.push(`only ${sections.length} .section bands, expected at least 4`);
  for (const s of sections) {
    if (!s.querySelector('.section-head h2')) {
      problems.push(`.section "${s.className}" has no .section-head h2, which buildNav() dereferences`);
    }
  }

  for (const id of REQUIRED_IDS) {
    if (!d.getElementById(id)) problems.push(`missing #${id}`);
  }

  const groups = new Set();
  d.querySelectorAll('.module h2').forEach(h => groups.add(h.textContent));
  if (groups.size < 8) problems.push(`only ${groups.size} modules rendered, expected at least 8`);

  return { problems, nav: nav ? nav.children.length : 0, sections: sections.length, modules: groups.size };
}

let failed = false;
for (const page of PAGES) {
  const r = check(page);
  if (r.problems.length) {
    failed = true;
    console.log(`FAIL  ${page.name}`);
    r.problems.forEach(p => console.log('        ' + p));
  } else {
    console.log(`ok    ${page.name}  (${r.sections} sections, ${r.nav} nav buttons, ${r.modules} modules)`);
  }
}
process.exit(failed ? 1 : 0);
