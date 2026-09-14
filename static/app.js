'use strict';

const SAMPLE_RATE = 44100;
const SYSTEM_TURN = 0;        // the assistant's own "which first?" question

const el = (id) => document.getElementById(id);
const ui = {
  connDot: el('conn-dot'), connText: el('conn-text'),
  llmModel: el('llm-model'), ttsModel: el('tts-model'), socketNote: el('socket-note'),
  mic: el('mic'), micLevel: el('miclevel'), prompt: el('prompt'),
  send: el('send'), stop: el('stop'),
  hint: el('hint'), canvas: el('timeline'), log: el('log'),
  pickModel: el('pick-model'), pickVoice: el('pick-voice'),
  chooser: el('chooser'), chAsk: el('ch-ask'), chButtons: el('ch-buttons'),
  panel: el('requests-panel'), cards: el('cards'),
};

const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ------------------------------------------------------------------ state */

let ws = null;
let connected = false;

// Every request ever made this session, by turn id.
const turns = new Map();
let selectedId = null;        // which turn the big timeline shows
let playingId = null;         // which turn is audible right now
let liveId = null;            // turn allowed to play as it streams

const running = () => [...turns.values()].filter((t) => t.status === 'thinking');
const awaiting = () => [...turns.values()]
  .filter((t) => t.status === 'ready' && !t.played)
  .sort((a, b) => a.id - b.id);

function newTurn(id, prompt, model) {
  return {
    id, prompt, model,
    startedAt: performance.now() / 1000,
    firstToken: null, ttsStart: null, firstAudio: null, textDone: null, audioDone: null,
    chunks: [],          // Float32Array pieces, in order
    scheduled: 0,        // how many of those have been handed to the speaker
    buffered: 0,         // seconds of audio received
    carry: null,         // trailing odd byte between binary frames
    chars: 0,
    status: 'thinking',  // thinking | ready | playing | played | failed
    played: false,
    playStart: null,
    node: null, card: null,
  };
}

const since = (t) => performance.now() / 1000 - t.startedAt;

/* ------------------------------------------------------------- audio out */

let audioCtx = null;
let playhead = 0;
let sources = [];            // live BufferSourceNodes, so barge-in can stop them

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
  return audioCtx;
}

function decodePcm(turn, buffer) {
  let bytes = new Uint8Array(buffer);
  if (turn.carry) {
    const joined = new Uint8Array(turn.carry.length + bytes.length);
    joined.set(turn.carry, 0);
    joined.set(bytes, turn.carry.length);
    bytes = joined;
    turn.carry = null;
  }
  if (bytes.length % 2) {
    turn.carry = bytes.slice(bytes.length - 1);
    bytes = bytes.slice(0, bytes.length - 1);
  }
  if (!bytes.length) return null;

  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.length / 2);
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 32768;
  return out;
}

function speak(samples) {
  const ctx = ensureAudio();
  const buf = ctx.createBuffer(1, samples.length, SAMPLE_RATE);
  buf.copyToChannel(samples, 0);

  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);

  const at = Math.max(playhead, ctx.currentTime + 0.08);
  src.start(at);
  playhead = at + buf.duration;

  sources.push(src);
  src.onended = () => { sources = sources.filter((s) => s !== src); };
  return at;
}

// Hand the speaker everything of this turn it has not heard yet.
function flush(turn) {
  while (turn.scheduled < turn.chunks.length) {
    const at = speak(turn.chunks[turn.scheduled]);
    if (turn.playStart === null) turn.playStart = at;
    turn.scheduled++;
  }
}

function playTurn(id) {
  const turn = turns.get(id);
  if (!turn) return;
  stopAudio();                       // one answer at a time
  ensureAudio();
  playingId = id;
  turn.played = true;
  if (turn.status === 'ready') turn.status = 'playing';
  flush(turn);
  hideChooser();
  select(id);
  render();
}

function stopAudio() {
  sources.forEach((s) => { try { s.stop(); } catch (e) { /* already ended */ } });
  sources = [];
  playhead = audioCtx ? audioCtx.currentTime : 0;
  if (playingId !== null) {
    const t = turns.get(playingId);
    if (t && t.status === 'playing') t.status = 'played';
  }
  playingId = null;
  liveId = null;
}

/* ---------------------------------------------------------- the chooser */

const tidy = (s) => s.replace(/[?.!,\s]+$/, '').trim();

// Name each answer by what makes it different. Questions asked in one breath
// tend to share an opening ("in one sentence, what is X / Y"), so a label
// built from the first few words names every one of them identically.
function labelsFor(list) {
  const words = list.map((t) => tidy(t.prompt).split(/\s+/));

  let shared = 0;
  const shortest = Math.min(...words.map((w) => w.length));
  while (shared < shortest - 1) {
    const here = words[0][shared].toLowerCase();
    if (!words.every((w) => w[shared].toLowerCase() === here)) break;
    shared++;
  }

  return words.map((w, i) => {
    const rest = w.slice(shared);
    const take = (rest.length ? rest : w).slice(0, 6);
    const text = take.join(' ');
    return text.split(/\s+/).length < w.length - shared ? `${text}…` : text;
  });
}

function label(turn) {
  return tidy(turn.prompt).split(/\s+/).slice(0, 6).join(' ');
}

function askWhichFirst() {
  const waiting = awaiting();
  if (waiting.length < 2) return;

  const names = labelsFor(waiting);
  const list = names.length === 2
    ? `${names[0]}, and ${names[1]}`
    : `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`;
  const sentence = `Your answers on ${list} are ready. Which would you like to hear first?`;

  ui.chAsk.textContent = sentence;
  ui.chButtons.innerHTML = '';
  waiting.forEach((t, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.innerHTML = `<span class="num">${i + 1}</span>`;
    b.append(document.createTextNode(names[i]));
    b.title = t.prompt;
    b.addEventListener('click', () => playTurn(t.id));
    ui.chButtons.append(b);
  });
  ui.chooser.hidden = false;

  // Say it out loud too. The answers are already buffered, so whichever the
  // user picks starts instantly once this finishes.
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'say', text: sentence, voice_id: ui.pickVoice.value }));
  }
}

function hideChooser() {
  ui.chooser.hidden = true;
}

// Called whenever a turn finishes: decide what, if anything, to play.
function settle() {
  if (running().length) return;         // still work in flight
  const waiting = awaiting();
  if (!waiting.length) return;
  if (waiting.length === 1) {
    playTurn(waiting[0].id);            // nothing to choose between
  } else {
    askWhichFirst();
  }
}

/* ------------------------------------------------------------- timeline */

const LANES = [
  { key: 'llm',     label: 'SambaNova', sub: 'text' },
  { key: 'tts',     label: 'Cartesia',  sub: 'audio' },
  { key: 'speaker', label: 'Speaker',   sub: 'playing' },
];

const PAD_L = 104, PAD_R = 12, LANE_H = 20, LANE_GAP = 12, TOP = 14;
const AXIS_Y = TOP + LANES.length * (LANE_H + LANE_GAP) + 6;
const VIEW_W = 880, VIEW_H = 150;

function niceStep(span) {
  const raw = span / 6;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
}

function shortModel(id) {
  return String(id || '')
    .replace(/^Meta-/, '').replace(/-Instruct$/, '').replace(/-it$/, '')
    .replace(/-/g, ' ');
}

function drawTimeline() {
  const turn = selectedId !== null ? turns.get(selectedId) : null;

  const c = ui.canvas;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== VIEW_W * dpr) { c.width = VIEW_W * dpr; c.height = VIEW_H * dpr; }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, VIEW_W, VIEW_H);

  const plotW = VIEW_W - PAD_L - PAD_R;
  const now = turn ? since(turn) : 0;
  const genEnd = turn && turn.audioDone !== null ? turn.audioDone : now;
  const domain = Math.max(4, genEnd * 1.12);
  const X = (t) => PAD_L + Math.min(t / domain, 1) * plotW;

  const ink = css('--ink'), faint = css('--faint');
  g.font = '10.5px "IBM Plex Mono", monospace';

  const step = niceStep(domain);
  g.strokeStyle = css('--hairline');
  g.lineWidth = 1;
  g.fillStyle = faint;
  g.textAlign = 'center';
  for (let t = step; t <= domain; t += step) {
    const x = Math.round(X(t)) + 0.5;
    g.beginPath(); g.moveTo(x, TOP - 4); g.lineTo(x, AXIS_Y); g.stroke();
    g.fillText(`${t >= 1 ? t.toFixed(t % 1 ? 1 : 0) : t.toFixed(1)}s`, x, AXIS_Y + 15);
  }

  g.strokeStyle = css('--line');
  g.beginPath(); g.moveTo(PAD_L, AXIS_Y + 0.5); g.lineTo(VIEW_W - PAD_R, AXIS_Y + 0.5); g.stroke();

  g.strokeStyle = ink; g.lineWidth = 1.5;
  g.beginPath(); g.moveTo(PAD_L + 0.5, TOP - 6); g.lineTo(PAD_L + 0.5, AXIS_Y); g.stroke();
  g.fillStyle = faint; g.textAlign = 'center';
  g.fillText('0', PAD_L, AXIS_Y + 15);

  const bar = (row, from, to, color) => {
    const y = TOP + row * (LANE_H + LANE_GAP);
    const x = X(from);
    const w = Math.max(X(to) - x, 1.5);
    g.fillStyle = color;
    if (g.roundRect) { g.beginPath(); g.roundRect(x, y, w, LANE_H, 2); g.fill(); }
    else g.fillRect(x, y, w, LANE_H);
  };

  const subFor = (lane) => {
    if (lane.key === 'speaker' && turn && turn.buffered > 0) {
      return `${turn.buffered.toFixed(1)}s buffered`;
    }
    if (lane.key === 'llm') {
      const id = (turn && turn.model) || (ui.pickModel && ui.pickModel.value);
      if (id) return shortModel(id).slice(0, 18);
    }
    return lane.sub;
  };

  LANES.forEach((lane, row) => {
    const y = TOP + row * (LANE_H + LANE_GAP);
    g.textAlign = 'right';
    g.fillStyle = ink; g.font = '11.5px "IBM Plex Mono", monospace';
    g.fillText(lane.label, PAD_L - 12, y + 10);
    g.fillStyle = faint; g.font = '10px "IBM Plex Mono", monospace';
    g.fillText(subFor(lane), PAD_L - 12, y + 21);
  });

  if (!turn) {
    g.fillStyle = faint;
    g.font = '11.5px "IBM Plex Mono", monospace';
    g.textAlign = 'left';
    g.fillText('idle — waiting for a prompt', PAD_L + 10, TOP + 12);
    return;
  }

  if (turn.firstToken === null) {
    bar(0, 0, now, css('--llm-soft'));
  } else {
    bar(0, 0, turn.firstToken, css('--llm-soft'));
    bar(0, turn.firstToken, turn.textDone !== null ? turn.textDone : now, css('--llm'));
  }

  if (turn.firstToken !== null) {
    const ttsEnd = turn.firstAudio !== null ? turn.firstAudio : now;
    bar(1, turn.firstToken, ttsEnd, css('--tts-soft'));
    if (turn.firstAudio !== null) {
      bar(1, turn.firstAudio, turn.audioDone !== null ? turn.audioDone : now, css('--tts'));
    }
  }

  if (turn.firstAudio !== null) {
    const bufEnd = turn.firstAudio + turn.buffered;
    bar(2, turn.firstAudio, bufEnd, css('--tts-soft'));
    if (turn.playStart !== null && audioCtx) {
      const heard = turn.firstAudio + Math.max(0, audioCtx.currentTime - turn.playStart);
      bar(2, turn.firstAudio, Math.min(heard, bufEnd), css('--play'));
    }
    if (bufEnd > domain) {
      const y = TOP + 2 * (LANE_H + LANE_GAP);
      g.fillStyle = css('--ground');
      g.font = '600 11px "IBM Plex Mono", monospace';
      g.textAlign = 'right';
      g.fillText('▸', VIEW_W - PAD_R - 4, y + 14);
    }
  }

  if (turn.firstAudio !== null) {
    const x = Math.round(X(turn.firstAudio)) + 0.5;
    g.strokeStyle = css('--tts'); g.lineWidth = 1.5;
    g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x, TOP - 6); g.lineTo(x, AXIS_Y); g.stroke();
    g.setLineDash([]);
  }
}

let raf = null;
function startDrawing() {
  if (raf) return;
  const tick = () => {
    drawTimeline();
    const quiet = !running().length && playingId === null;
    if (quiet) { raf = null; drawTimeline(); return; }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
}

/* ------------------------------------------------------------------ cards */

const CHIP = {
  thinking: 'thinking', queued: 'queued', ready: 'ready',
  playing: 'playing', played: 'played', failed: 'failed',
};

function render() {
  const all = [...turns.values()].sort((a, b) => a.id - b.id);
  ui.panel.hidden = all.length < 2;
  if (all.length < 2) { ui.cards.innerHTML = ''; return; }

  ui.cards.innerHTML = '';
  all.forEach((t) => {
    const card = document.createElement('div');
    card.className = 'card' + (t.id === selectedId ? ' selected' : '');
    card.tabIndex = 0;

    const q = document.createElement('div');
    q.className = 'cq';
    q.textContent = t.prompt;

    // Text finished but no synthesis slot yet: Cartesia's plan limit, not us.
    const queued = t.status === 'thinking' && t.textDone !== null && t.ttsStart === null;
    const state = queued ? 'queued' : t.status;

    const row = document.createElement('div');
    row.className = 'crow';
    const chip = document.createElement('span');
    chip.className = `chip ${queued ? 'queued' : t.status}`;
    chip.textContent = CHIP[state] || state;
    const meta = document.createElement('span');
    meta.className = 'cmeta';
    meta.textContent = queued
      ? 'waiting for a synthesis slot'
      : (t.firstAudio !== null
          ? `${t.firstAudio.toFixed(2)}s · ${t.buffered.toFixed(1)}s audio`
          : (t.firstToken !== null ? `${t.firstToken.toFixed(2)}s to first token` : '…'));
    row.append(chip, meta);

    const bar = document.createElement('div');
    bar.className = 'cbar';
    const fill = document.createElement('i');
    const pct = t.status === 'thinking'
      ? (t.firstToken !== null ? 45 : 15)
      : 100;
    fill.style.width = `${pct}%`;
    bar.append(fill);

    card.append(q, row, bar);
    card.addEventListener('click', () => {
      if (t.status === 'ready' || t.status === 'played') playTurn(t.id);
      else select(t.id);
    });
    ui.cards.append(card);
  });
}

function select(id) {
  selectedId = id;
  render();
  drawTimeline();
}

/* ------------------------------------------------------------ transcript */

function addMessage(cls, who, text) {
  if (ui.log.firstElementChild && ui.log.firstElementChild.className === 'empty') {
    ui.log.innerHTML = '';
  }
  const row = document.createElement('div');
  row.className = `msg ${cls}`;
  const w = document.createElement('div');
  w.className = 'who';
  w.textContent = who;
  const what = document.createElement('div');
  what.className = 'what';
  const body = document.createElement('span');
  body.textContent = text;
  what.append(body);
  row.append(w, what);
  ui.log.append(row);
  return { row, what, body, cursor: null };
}

function showCursor(m) {
  const c = document.createElement('span');
  c.className = 'cursor';
  m.what.append(c);
  m.cursor = c;
}

function hideCursor(m) {
  if (m && m.cursor) { m.cursor.remove(); m.cursor = null; }
}

/* ------------------------------------------------------------ metrics ui */

const secs = (v) => (v === null || v === undefined) ? '—' : `${v.toFixed(2)}<small>s</small>`;

function showMetrics(t) {
  ui.mToken = ui.mToken || el('m-token');
  el('m-token').innerHTML = secs(t ? t.firstToken : null);
  el('m-audio').innerHTML = secs(t ? t.firstAudio : null);
  el('m-text').innerHTML = secs(t ? t.textDone : null);
  el('m-chars').textContent = t ? `${t.chars} chars` : '—';
  const rt = t && t.audioDone ? t.buffered / t.audioDone : null;
  el('m-rate').innerHTML = rt ? `${rt.toFixed(1)}<small>×</small>` : '—';
}

/* --------------------------------------------------------------- pickers */

const remember = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const recall = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };

function fillPickers(m) {
  const models = m.models && m.models.length ? m.models : [m.llm_model];
  const wantModel = recall('voiceloop.model');
  ui.pickModel.innerHTML = '';
  models.forEach((id) => {
    const o = document.createElement('option');
    o.value = id; o.textContent = shortModel(id);
    ui.pickModel.append(o);
  });
  ui.pickModel.value = models.includes(wantModel) ? wantModel : m.llm_model;

  const voices = m.voices || [];
  ui.pickVoice.innerHTML = '';
  if (!voices.length) {
    const o = document.createElement('option');
    o.value = m.default_voice; o.textContent = 'default voice';
    ui.pickVoice.append(o);
    ui.pickVoice.disabled = true;
  } else {
    voices.forEach((v) => {
      const o = document.createElement('option');
      o.value = v.id;
      o.textContent = v.language && v.language !== 'en' ? `${v.name} (${v.language})` : v.name;
      o.title = v.description || '';
      ui.pickVoice.append(o);
    });
    const want = recall('voiceloop.voice');
    const ids = voices.map((v) => v.id);
    ui.pickVoice.value = ids.includes(want) ? want
      : (ids.includes(m.default_voice) ? m.default_voice : ids[0]);
  }
  ui.llmModel.textContent = shortModel(ui.pickModel.value);
}

ui.pickModel.addEventListener('change', () => {
  remember('voiceloop.model', ui.pickModel.value);
  ui.llmModel.textContent = shortModel(ui.pickModel.value);
});
ui.pickVoice.addEventListener('change', () => remember('voiceloop.voice', ui.pickVoice.value));

/* --------------------------------------------------------------- socket */

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    connected = true;
    ui.connDot.className = 'dot live';
    ui.connText.textContent = 'connected';
  };
  ws.onclose = () => {
    connected = false;
    ui.connDot.className = 'dot dead';
    ui.connText.textContent = 'disconnected — restart the server and reload';
    ui.send.disabled = true;
    ui.mic.disabled = true;
  };
  ws.onerror = () => {
    ui.connDot.className = 'dot dead';
    ui.connText.textContent = 'connection error';
  };
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) { onAudio(ev.data); return; }
    onEvent(JSON.parse(ev.data));
  };
}

function onAudio(buffer) {
  if (buffer.byteLength < 3) return;
  const id = new DataView(buffer).getUint16(0);
  const body = buffer.slice(2);

  // The assistant's own question always plays straight away.
  if (id === SYSTEM_TURN) {
    const samples = decodePcm({ carry: null }, body);
    if (samples) speak(samples);
    return;
  }

  const turn = turns.get(id);
  if (!turn) return;

  const samples = decodePcm(turn, body);
  if (!samples) return;

  turn.chunks.push(samples);
  turn.buffered += samples.length / SAMPLE_RATE;

  // Stream straight to the speaker only when this is the sole request in
  // flight; otherwise hold it so the user gets to choose the order.
  if (liveId === id || playingId === id) flush(turn);

  updateStopButton();
}

function onEvent(m) {
  const turn = m.turn_id ? turns.get(m.turn_id) : null;

  switch (m.type) {
    case 'ready':
      ui.ttsModel.textContent = m.tts_model;
      ui.socketNote.textContent = `socket warm in ${Math.round(m.socket_setup_ms)} ms`;
      ui.connText.textContent = 'ready';
      fillPickers(m);
      ui.send.disabled = false;
      ui.mic.disabled = !SpeechCtor;
      break;

    case 'turn_start': {
      const t = newTurn(m.turn_id, m.prompt, m.model);
      turns.set(m.turn_id, t);
      addMessage('you', 'you', m.prompt);
      t.node = addMessage('bot', 'reply', '');
      showCursor(t.node);
      // Only a lone request may play as it streams.
      liveId = (running().length === 1 && !awaiting().length && playingId === null)
        ? m.turn_id : null;
      select(m.turn_id);
      startDrawing();
      break;
    }

    case 'first_token':
      if (turn) turn.firstToken = m.t;
      if (m.turn_id === selectedId) showMetrics(turn);
      render();
      break;

    case 'token':
      if (!turn) break;
      turn.chars += m.text.length;
      turn.node.body.textContent += m.text;
      if (m.turn_id === selectedId) showMetrics(turn);
      break;

    case 'text_done':
      if (turn) turn.textDone = m.t;
      if (m.turn_id === selectedId) showMetrics(turn);
      render();
      break;

    case 'tts_start':
      if (turn) turn.ttsStart = m.t;
      render();
      break;

    case 'first_audio':
      if (turn) turn.firstAudio = m.t;
      if (m.turn_id === selectedId) showMetrics(turn);
      render();
      break;

    case 'done':
      if (turn) {
        turn.audioDone = m.metrics.total_s;
        turn.status = (liveId === m.turn_id || playingId === m.turn_id) ? 'playing' : 'ready';
        if (liveId === m.turn_id) turn.played = true;
        hideCursor(turn.node);
      }
      if (m.turn_id === selectedId) showMetrics(turn);
      render();
      break;

    case 'idle':
      settle();
      render();
      updateStopButton();
      break;

    case 'say_start':
      ui.hint.className = 'hint mono';
      ui.hint.textContent = 'asking which you want first…';
      break;

    case 'say_done':
      ui.hint.textContent = 'Pick one above, or press the mic to ask something else.';
      break;

    case 'rejected':
      ui.hint.className = 'hint mono warn';
      ui.hint.textContent = m.message;
      break;

    case 'error':
      addMessage('err', 'error', m.message);
      if (turn) {
        hideCursor(turn.node);
        turn.status = 'failed';
        turn.audioDone = since(turn);
      }
      render();
      break;
  }
}

/* ------------------------------------------------------------------ input */

function updateStopButton() {
  const audible = playingId !== null || sources.length > 0;
  ui.stop.hidden = !audible;
}

function submit(text) {
  const value = (text || ui.prompt.value).trim();
  if (!value || !connected || ws.readyState !== WebSocket.OPEN) return;
  ensureAudio();                  // needs a user gesture to start
  ui.prompt.value = '';
  ui.hint.className = 'hint mono';
  hideChooser();
  ws.send(JSON.stringify({
    type: 'prompt',
    text: value,
    model: ui.pickModel.value,
    voice_id: ui.pickVoice.value,
  }));
}

ui.send.addEventListener('click', () => submit());
ui.prompt.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
ui.stop.addEventListener('click', () => { stopAudio(); render(); updateStopButton(); });

/* ------------------------------------------------------------------ mic */

const SpeechCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false, micStream = null, meterRaf = null;

async function startMeter() {
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = ensureAudio();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(micStream).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    const loop = () => {
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128) / 128);
      ui.micLevel.style.height = `${Math.min(100, peak * 260)}%`;
      meterRaf = requestAnimationFrame(loop);
    };
    loop();
  } catch (err) { /* the meter is optional */ }
}

function stopMeter() {
  if (meterRaf) cancelAnimationFrame(meterRaf);
  meterRaf = null;
  ui.micLevel.style.height = '0%';
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micStream = null;
}

function startListening() {
  if (listening || !SpeechCtor) return;

  // Barge-in: talking over the assistant stops it.
  stopAudio();
  updateStopButton();
  render();

  rec = new SpeechCtor();
  rec.lang = 'en-US';
  rec.interimResults = true;
  rec.continuous = false;
  let finalText = '';

  rec.onstart = () => {
    listening = true;
    ui.mic.classList.add('on');
    ui.prompt.classList.add('listening');
    ui.prompt.value = '';
    ui.hint.className = 'hint mono';
    ui.hint.textContent = 'listening…';
    startMeter();
  };

  rec.onresult = (ev) => {
    let interim = '';
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const chunk = ev.results[i][0].transcript;
      if (ev.results[i].isFinal) finalText += chunk; else interim += chunk;
    }
    ui.prompt.value = (finalText + interim).trim();
  };

  rec.onerror = (ev) => {
    ui.hint.className = 'hint mono warn';
    ui.hint.textContent = ev.error === 'not-allowed'
      ? 'Microphone blocked — allow it in the address bar, or just type.'
      : `Speech recognition: ${ev.error}. You can type instead.`;
  };

  rec.onend = () => {
    listening = false;
    rec = null;
    ui.mic.classList.remove('on');
    ui.prompt.classList.remove('listening');
    stopMeter();
    const said = finalText.trim();
    if (said) {
      ui.hint.textContent = 'Press the mic and speak — it sends when you stop.';
      submit(said);
    } else if (!ui.hint.classList.contains('warn')) {
      ui.hint.textContent = 'Heard nothing. Try again, or type it.';
    }
  };

  rec.start();
}

ui.mic.addEventListener('click', () => { listening ? rec && rec.stop() : startListening(); });

if (!SpeechCtor) {
  ui.mic.disabled = true;
  ui.mic.title = 'This browser has no speech recognition';
  ui.hint.textContent = 'Speech input needs Chrome, Edge, or Safari — typing works everywhere.';
}

window.addEventListener('resize', drawTimeline);
ui.send.disabled = true;
ui.mic.disabled = true;
drawTimeline();
connect();
