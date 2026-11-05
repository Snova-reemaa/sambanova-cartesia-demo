// The speed comparison page. The server races the two providers and times
// them (compare.py); this shows it. Timers count up here only to look
// alive: the moment a side reports, its numbers are replaced by the
// server's own, which are the ones that count.

const $ = (id) => document.getElementById(id);
const ui = {
  ask: $('ask'), a: $('side-a'), b: $('side-b'), question: $('question'), go: $('go'),
  note: $('note'), shown: $('shown-question'), round: $('round'), bars: $('bars'),
  barsNote: $('bars-note'),
  own: $('own'), ownUrl: $('own-url'), ownKey: $('own-key'), ownModel: $('own-model'),
};

// "Your own provider": always offered, needs nothing in .env. Its key stays
// in this page's field; it goes to the server only inside a run request.
const CUSTOM = { name: 'custom', label: 'Your own provider', ready: true, custom: true };
const customSide = () => ({
  name: 'custom', label: `Your provider (${ui.ownModel.value.trim() || 'model'})`,
  model: ui.ownModel.value.trim() || 'your model', deployment: 'serverless/unknown',
  note: "we can't verify how your provider is set up", ready: true,
});
const slots = { a: document.querySelector('[data-slot="a"]'), b: document.querySelector('[data-slot="b"]') };

let providers = [];
let ws = null;
let state = null;     // the run in progress

// "accounts/fireworks/models/gpt-oss-120b" in a picker is noise; the panel
// shows the full id, the picker the model's own name.
const shortModel = (m) => (m || '').split('/').pop();
const fmt = (s) => (s === null || s === undefined) ? '—' : s.toFixed(2);
const fmtRate = (r) => (r === null || r === undefined) ? '—' : `${Math.round(r)}`;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/compare/ws`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => { ui.note.textContent = 'Disconnected. Reload to reconnect.'; ui.go.disabled = true; };
}

function fillProviders() {
  for (const [select, want] of [[ui.a, 'sambanova'], [ui.b, 'fireworks']]) {
    select.innerHTML = '';
    for (const p of [...providers, CUSTOM]) {
      const o = document.createElement('option');
      o.value = p.name;
      o.textContent = p.custom ? p.label
        : (p.ready ? `${p.label} · ${shortModel(p.model)}` : `${p.label} (${p.why})`);
      o.disabled = !p.ready;
      select.append(o);
    }
    const first = providers.find((p) => p.name === want && p.ready) || providers.find((p) => p.ready);
    if (first) select.value = first.name;
  }
  if (ui.a.value === ui.b.value) {
    const other = providers.find((p) => p.ready && p.name !== ui.a.value);
    if (other) ui.b.value = other.name;
  }
  // One ready provider is enough: the other side can be the visitor's own.
  const ready = providers.filter((p) => p.ready).length;
  ui.go.disabled = ready < 1;
  preview();
  if (ready < 1) {
    ui.note.className = 'cmp-note bad';
    ui.note.textContent = 'Two providers need a key and a model before they can be compared: '
      + providers.filter((p) => !p.ready).map((p) => `${p.label}: ${p.why}`).join(' · ');
  }
}

function buildPanel(slot, side) {
  const node = slots[slot];
  node.innerHTML = '';
  node.append($('panel').content.cloneNode(true));
  node.className = 'cmp-panel';
  const title = node.querySelector('.cmp-name');
  title.textContent = side.label;
  // "Your provider (some-long-model-id)" would wrap at full size.
  title.classList.toggle('long', side.label.length > 18);
  const tag = node.querySelector('.cmp-tag');
  tag.textContent = side.deployment;
  tag.classList.toggle('dedicated', side.deployment === 'dedicated');
  // A reasoning model says how hard it was asked to think, so a side on its
  // provider's default is never mistaken for a like-for-like run.
  node.querySelector('.cmp-model').textContent = side.reasoning
    ? `${side.model} · reasoning ${side.reasoning}` : side.model;
  tag.classList.toggle('unknown', side.deployment === 'serverless/unknown');
  const caveat = node.querySelector('.cmp-caveat');
  caveat.textContent = side.note || '';
  caveat.hidden = !side.note;
  return {
    node, name: side.name, label: side.label,
    // The headline: first token and tokens per second, neither of which a
    // shorter reply can win by being short.
    ttft: node.querySelector('[data-v="ttft"]'), tps: node.querySelector('[data-v="tps"]'),
    total: node.querySelector('[data-v="total"]'), per100: node.querySelector('[data-v="per100"]'),
    sub: node.querySelector('[data-v="sub"]'),
    reply: node.querySelector('.cmp-reply'), error: node.querySelector('.cmp-error'),
    badge: node.querySelector('.cmp-badge'),
    first: null, firstAt: null, done: null,
  };
}

// Before anything runs, the panels already say who is racing.
function preview() {
  if (state && !state.finished) return;
  ui.own.hidden = ui.a.value !== 'custom' && ui.b.value !== 'custom';
  for (const [slot, select] of [['a', ui.a], ['b', ui.b]]) {
    const side = select.value === 'custom' ? customSide() : providers.find((p) => p.name === select.value);
    if (!side) continue;
    const panel = buildPanel(slot, side);
    panel.reply.textContent = '';
    panel.reply.dataset.empty = 'The reply streams in here.';
  }
}
ui.a.addEventListener('change', preview);
ui.b.addEventListener('change', preview);
ui.ownModel.addEventListener('input', preview);

function blank(p) {
  p.ttft.textContent = '0.00'; p.tps.textContent = '—'; p.tps.classList.remove('est');
  p.total.textContent = '—'; p.per100.textContent = '—'; p.sub.textContent = '';
}

function resetRound() {
  for (const p of Object.values(state.panels)) {
    p.first = null; p.firstAt = null; p.done = null;
    p.node.classList.remove('done', 'failed', 'won');
    p.node.classList.add('running');
    p.badge.hidden = true;
    p.error.hidden = true;
    p.reply.textContent = '';
    // Before "go" both sides are warming their connections, off the clock.
    p.reply.classList.remove('thinking');
    p.reply.dataset.empty = 'Lining up…';
    blank(p);
  }
  ui.bars.hidden = true;
}

// Count up while running, at the display's own pace. First token counts
// until it arrives; tokens per second shows a rough running figure from the
// text so far (dimmed, "≈") until the provider's count replaces it.
function tick() {
  if (!state || !state.goAt) return;
  const nowMs = performance.now();
  const now = (nowMs - state.goAt) / 1000;
  let live = false;
  for (const p of Object.values(state.panels)) {
    if (p.done) continue;
    live = true;
    p.total.textContent = `${fmt(now)} s`;
    if (p.first === null) {
      p.ttft.textContent = fmt(now);
    } else {
      const since = (nowMs - p.firstAt) / 1000;
      if (since > 0.3 && p.reply.textContent.length) {
        p.tps.textContent = `≈${Math.round(p.reply.textContent.length / 4 / since)}`;
        p.tps.classList.add('est');
      }
    }
  }
  if (live) requestAnimationFrame(tick);
}

function showResult(p, r) {
  p.ttft.textContent = fmt(r.ttft);
  p.tps.textContent = fmtRate(r.tps);
  p.tps.classList.remove('est');
  p.total.textContent = `${fmt(r.total)} s`;
  p.per100.textContent = r.per100 ? `${fmt(r.per100)} s` : '—';
}

function handle(m) {
  if (m.type === 'providers') { providers = m.providers; fillProviders(); return; }
  if (m.type === 'refused') { ui.note.className = 'cmp-note bad'; ui.note.textContent = m.why; ui.go.disabled = false; return; }
  if (m.type === 'start') {
    state = { panels: {}, bySide: {}, runs: m.runs, goAt: null, results: [] };
    ['a', 'b'].forEach((slot, i) => {
      const panel = buildPanel(slot, m.sides[i]);
      state.panels[slot] = panel;
      state.bySide[m.sides[i].name] = panel;
    });
    ui.shown.textContent = `“${m.question}”`;
    ui.shown.hidden = false;
    ui.note.className = 'cmp-note';
    ui.note.textContent = 'Same question, same settings, both sent at the same moment.';
    return;
  }
  if (!state) return;
  const p = m.side ? state.bySide[m.side] : null;
  switch (m.type) {
    case 'round':
      ui.round.hidden = state.runs === 1;
      ui.round.textContent = `Run ${m.n} of ${m.of}`;
      resetRound();
      break;
    case 'go':
      state.goAt = performance.now();
      for (const x of Object.values(state.panels)) x.reply.classList.add('thinking');
      requestAnimationFrame(tick);
      break;
    case 'first_token':
      p.first = m.t;
      p.firstAt = performance.now();
      p.ttft.textContent = fmt(m.t);
      break;
    case 'text':
      p.reply.classList.remove('thinking');
      p.reply.textContent += m.text;
      p.reply.scrollTop = p.reply.scrollHeight;
      break;
    case 'side_done':
      p.done = m;
      p.node.classList.remove('running');
      p.reply.classList.remove('thinking');
      delete p.reply.dataset.empty;
      if (m.ok) {
        p.node.classList.add('done');
        showResult(p, m);
        // Replies differ in length, so say how long each one was.
        p.sub.textContent = (m.tokens ? `${m.tokens} tokens${m.tokens_from === 'estimate' ? ' (estimated)' : ''}` : '')
          + (m.truncated ? ' · cut off at the token limit' : '');
        p.tps.title = m.provider_tps ? `${p.label} reports ${Math.round(m.provider_tps)} tok/s itself` : '';
      } else {
        p.node.classList.add('failed');
        p.ttft.textContent = '—'; p.tps.textContent = '—'; p.tps.classList.remove('est');
        p.total.textContent = '—'; p.per100.textContent = '—';
        p.error.hidden = false;
        p.error.innerHTML = '';
        p.error.append(document.createTextNode(m.message));
        if (m.detail) {
          const small = document.createElement('small');
          small.textContent = m.detail;
          p.error.append(small);
        }
      }
      state.results.push(m);
      if (Object.values(state.panels).every((x) => x.done) && state.runs === 1) {
        crown(m.side && singleRun());
      }
      break;
    case 'summary':
      summarise(m);
      state.finished = true;
      ui.go.disabled = false;
      break;
  }
}

const METRICS = ['ttft', 'tps', 'total', 'per100'];

function singleRun() {
  const [a, b] = Object.values(state.panels).map((x) => x.done);
  const summary = { sides: {}, faster: null };
  for (const r of [a, b]) {
    const side = { runs: 1, ok: r.ok ? 1 : 0, failures: r.ok ? [] : [r.message] };
    for (const k of METRICS) side[k] = r.ok && r[k] ? { median: r[k], worst: r[k] } : null;
    summary.sides[r.side] = side;
  }
  // Faster means more tokens per second: what a shorter reply cannot fake.
  if (a.ok && b.ok && a.tps && b.tps) {
    const [fast, slow] = a.tps >= b.tps ? [a, b] : [b, a];
    summary.faster = { side: fast.side, by: fast.tps / slow.tps, on: 'tokens per second' };
  }
  return summary;
}

// The faster side, quietly: a glow and a badge, nothing that jumps.
function crown(summary) {
  if (!summary) return;
  // A side that failed still gets its row in the bars, saying so; only
  // the badge needs two results to compare.
  if (!summary.faster) { drawBars(summary); return; }
  const winner = state.bySide[summary.faster.side];
  winner.node.classList.add('won');
  const by = summary.faster.by;
  winner.badge.textContent = by >= 1.05 ? `${by.toFixed(1)}× the tokens/sec` : 'Faster';
  winner.badge.title = 'Compared on tokens per second, which does not depend on how long the reply is';
  winner.badge.hidden = false;
  drawBars(summary);
}

function summarise(m) {
  if (state.runs > 1) {
    // Five runs: the panels now show the typical result, with the worst
    // under it, and how many of the five got through.
    for (const [name, side] of Object.entries(m.sides)) {
      const p = state.bySide[name];
      if (side.ttft) {
        p.node.classList.remove('failed');
        p.node.classList.add('done');
        const med = Object.fromEntries(METRICS.map((k) => [k, side[k] ? side[k].median : null]));
        showResult(p, med);
        p.sub.textContent = `median of ${side.ok} · worst first token ${fmt(side.ttft.worst)} s`
          + (side.tps ? ` · worst ${fmtRate(side.tps.worst)} tok/s` : '')
          + (side.ok < side.runs ? ` · ${side.runs - side.ok} of ${side.runs} failed` : '')
          + (side.truncated ? ` · ${side.truncated} cut off` : '');
      } else {
        p.sub.textContent = `all ${side.runs} runs failed: ${side.failures[0] || ''}`;
      }
    }
    ui.round.textContent = `${state.runs} runs · median and worst`;
    crown(m);
  }
}

function drawBars(summary) {
  ui.bars.querySelectorAll('.cmp-bar-row').forEach((n) => n.remove());
  const names = Object.keys(state.bySide);
  const rows = [
    ['ttft', 'First token', 'seconds · lower is faster', (v) => `${fmt(v)} s`, false],
    ['tps', 'Tokens / sec', 'measured by us · higher is faster', (v) => `${fmtRate(v)}`, true],
    ['total', 'Full reply', 'seconds · depends on reply length', (v) => `${fmt(v)} s`, false],
    ['per100', 'Per 100 tokens', 'full reply ÷ length · lower is faster', (v) => `${fmt(v)} s`, false],
  ];
  for (const [key, label, hint, show, high] of rows) {
    const values = names.map((n) => summary.sides[n] && summary.sides[n][key] ? summary.sides[n][key].median : null);
    const top = Math.max(...values.filter((v) => v !== null), 0);
    const row = document.createElement('div');
    row.className = 'cmp-bar-row';
    const head = document.createElement('div');
    head.className = 'cmp-bar-label';
    head.textContent = label;
    const small = document.createElement('small');
    small.textContent = hint;
    head.append(small);
    row.append(head);
    names.forEach((n, i) => {
      const bar = document.createElement('div');
      bar.className = 'cmp-bar';
      bar.dataset.slot = i === 0 ? 'a' : 'b';
      const fill = document.createElement('i');
      const value = document.createElement('b');
      if (values[i] === null) {
        bar.classList.add('failed');
        value.textContent = `${state.bySide[n].label}: no result`;
        fill.style.width = '0';
      } else {
        value.textContent = `${state.bySide[n].label} · ${show(values[i])}`;
        fill.style.width = '0';
        requestAnimationFrame(() => { fill.style.width = `${Math.max(4, (values[i] / (top || 1)) * 100)}%`; });
      }
      bar.title = high ? 'Longer is faster' : 'Shorter is faster';
      bar.append(fill, value);
      row.append(bar);
    });
    ui.bars.insertBefore(row, ui.barsNote);
  }
  ui.barsNote.textContent = (state.runs > 1
    ? `Median of ${state.runs} runs each. `
    : 'One run; use “5 runs” for the typical result rather than a lucky one. ')
    + 'Faster means more tokens per second, measured the same way for both. Full reply time depends on how long each reply is, so per 100 tokens is shown beside it.';
  ui.bars.hidden = false;
}

ui.ask.addEventListener('submit', (e) => {
  e.preventDefault();
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const runs = Number(document.querySelector('input[name="runs"]:checked').value);
  ui.go.disabled = true;
  ui.note.className = 'cmp-note';
  ui.note.textContent = runs > 1 ? 'Running five times…' : 'Running…';
  const request = { type: 'run', question: ui.question.value, sides: [ui.a.value, ui.b.value], runs };
  if (request.sides.includes('custom')) {
    const url = ui.ownUrl.value.trim();
    if (!/^https:\/\//i.test(url)) {
      ui.go.disabled = false;
      ui.note.className = 'cmp-note bad';
      ui.note.textContent = 'Your provider needs an https:// base URL.';
      return;
    }
    request.custom = { base_url: url, key: ui.ownKey.value, model: ui.ownModel.value.trim() };
  }
  ws.send(JSON.stringify(request));
});

connect();
