'use strict';

const SAMPLE_RATE = 44100;

const el = (id) => document.getElementById(id);
const ui = {
  connDot: el('conn-dot'), connText: el('conn-text'),
  llmModel: el('llm-model'), ttsModel: el('tts-model'), socketNote: el('socket-note'),
  mic: el('mic'), micLevel: el('miclevel'), prompt: el('prompt'), send: el('send'),
  hint: el('hint'), canvas: el('timeline'), log: el('log'),
  pickModel: el('pick-model'), pickVoice: el('pick-voice'),
  mToken: el('m-token'), mAudio: el('m-audio'), mText: el('m-text'),
  mChars: el('m-chars'), mRate: el('m-rate'),
};

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* ------------------------------------------------------------------ state */

let ws = null;
let busy = false;

// Everything about the turn currently on screen. Times are seconds since the
// prompt left the browser; null means "hasn't happened yet".
let turn = null;

function newTurn() {
  return {
    startedAt: performance.now() / 1000,
    firstToken: null,
    firstAudio: null,
    textDone: null,
    audioDone: null,
    playStart: null,     // audio-context clock, for the speaker lane
    buffered: 0,         // seconds of audio handed to the speaker
    chars: 0,
    node: null,          // transcript element being filled in
  };
}

const elapsed = () => turn ? performance.now() / 1000 - turn.startedAt : 0;

/* ------------------------------------------------------------- audio out */

let audioCtx = null;
let playhead = 0;
let carry = null;   // trailing odd byte between binary frames

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
  return audioCtx;
}

function playPcm(buffer) {
  const ctx = ensureAudio();

  let bytes = new Uint8Array(buffer);
  if (carry) {
    const joined = new Uint8Array(carry.length + bytes.length);
    joined.set(carry, 0);
    joined.set(bytes, carry.length);
    bytes = joined;
    carry = null;
  }
  if (bytes.length % 2) {
    carry = bytes.slice(bytes.length - 1);
    bytes = bytes.slice(0, bytes.length - 1);
  }
  if (!bytes.length) return;

  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.length / 2);
  const samples = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;

  const buf = ctx.createBuffer(1, samples.length, SAMPLE_RATE);
  buf.copyToChannel(samples, 0);

  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);

  // A small lead keeps the first chunk from being scheduled in the past.
  const startAt = Math.max(playhead, ctx.currentTime + 0.08);
  src.start(startAt);
  playhead = startAt + buf.duration;

  if (turn) {
    if (turn.playStart === null) turn.playStart = startAt;
    turn.buffered += buf.duration;
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

function drawTimeline() {
  const c = ui.canvas;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== VIEW_W * dpr) {
    c.width = VIEW_W * dpr;
    c.height = VIEW_H * dpr;
  }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, VIEW_W, VIEW_H);

  const plotW = VIEW_W - PAD_L - PAD_R;
  const now = turn ? elapsed() : 0;
  const genEnd = turn && turn.audioDone !== null ? turn.audioDone : now;
  const domain = Math.max(4, genEnd * 1.12);
  const X = (t) => PAD_L + Math.min(t / domain, 1) * plotW;

  const ink = css('--ink'), muted = css('--muted'), faint = css('--faint');
  const hair = css('--hairline'), line = css('--line');

  g.font = '10.5px "IBM Plex Mono", monospace';

  // grid + axis
  const step = niceStep(domain);
  g.strokeStyle = hair;
  g.lineWidth = 1;
  g.fillStyle = faint;
  g.textAlign = 'center';
  for (let t = step; t <= domain; t += step) {
    const x = Math.round(X(t)) + 0.5;
    g.beginPath();
    g.moveTo(x, TOP - 4);
    g.lineTo(x, AXIS_Y);
    g.stroke();
    g.fillText(`${t >= 1 ? t.toFixed(t % 1 ? 1 : 0) : t.toFixed(1)}s`, x, AXIS_Y + 15);
  }

  g.strokeStyle = line;
  g.beginPath();
  g.moveTo(PAD_L, AXIS_Y + 0.5);
  g.lineTo(VIEW_W - PAD_R, AXIS_Y + 0.5);
  g.stroke();

  // t = 0
  g.strokeStyle = ink;
  g.lineWidth = 1.5;
  g.beginPath();
  g.moveTo(PAD_L + 0.5, TOP - 6);
  g.lineTo(PAD_L + 0.5, AXIS_Y);
  g.stroke();
  g.fillStyle = faint;
  g.textAlign = 'center';
  g.fillText('0', PAD_L, AXIS_Y + 15);

  const bar = (row, from, to, color) => {
    const y = TOP + row * (LANE_H + LANE_GAP);
    const x = X(from);
    const w = Math.max(X(to) - x, 1.5);
    g.fillStyle = color;
    if (g.roundRect) {
      g.beginPath();
      g.roundRect(x, y, w, LANE_H, 2);
      g.fill();
    } else {
      g.fillRect(x, y, w, LANE_H);
    }
  };

  // The speaker sublabel carries the headroom figure, where it stays legible
  // instead of being painted over the bar it describes.
  const subFor = (lane) => {
    if (lane.key === 'speaker' && turn && turn.buffered > 0) {
      return `${turn.buffered.toFixed(1)}s buffered`;
    }
    // Name the model that produced this turn, so switching is legible here.
    if (lane.key === 'llm') {
      const id = (turn && turn.model) || (ui.pickModel && ui.pickModel.value);
      if (id) return shortModel(id).slice(0, 18);
    }
    return lane.sub;
  };

  LANES.forEach((lane, row) => {
    const y = TOP + row * (LANE_H + LANE_GAP);
    g.textAlign = 'right';
    g.fillStyle = ink;
    g.font = '11.5px "IBM Plex Mono", monospace';
    g.fillText(lane.label, PAD_L - 12, y + 10);
    g.fillStyle = faint;
    g.font = '10px "IBM Plex Mono", monospace';
    g.fillText(subFor(lane), PAD_L - 12, y + 21);
  });

  if (!turn) {
    g.fillStyle = faint;
    g.font = '11.5px "IBM Plex Mono", monospace';
    g.textAlign = 'left';
    g.fillText('idle — waiting for a prompt', PAD_L + 10, TOP + 12);
    return;
  }

  // lane 0 — SambaNova
  if (turn.firstToken === null) {
    bar(0, 0, now, css('--llm-soft'));
  } else {
    bar(0, 0, turn.firstToken, css('--llm-soft'));
    bar(0, turn.firstToken, turn.textDone !== null ? turn.textDone : now, css('--llm'));
  }

  // lane 1 — Cartesia (starts when the first token is pushed to it)
  if (turn.firstToken !== null) {
    const ttsEnd = turn.firstAudio !== null ? turn.firstAudio : now;
    bar(1, turn.firstToken, ttsEnd, css('--tts-soft'));
    if (turn.firstAudio !== null) {
      bar(1, turn.firstAudio, turn.audioDone !== null ? turn.audioDone : now, css('--tts'));
    }
  }

  // lane 2 — speaker: solid for what has been heard, faint for what is buffered
  if (turn.firstAudio !== null && turn.playStart !== null && audioCtx) {
    const heardEnd = turn.firstAudio + Math.max(0, audioCtx.currentTime - turn.playStart);
    const bufEnd = turn.firstAudio + turn.buffered;
    bar(2, turn.firstAudio, bufEnd, css('--tts-soft'));
    bar(2, turn.firstAudio, Math.min(heardEnd, bufEnd), css('--play'));

    // Audio usually runs past the right edge; mark it rather than rescale the
    // whole chart and squash the latencies this view exists to show.
    if (bufEnd > domain) {
      const y = TOP + 2 * (LANE_H + LANE_GAP);
      g.fillStyle = css('--ground');
      g.font = '600 11px "IBM Plex Mono", monospace';
      g.textAlign = 'right';
      g.fillText('▸', VIEW_W - PAD_R - 4, y + 14);
    }
  }

  // first-sound marker
  if (turn.firstAudio !== null) {
    const x = Math.round(X(turn.firstAudio)) + 0.5;
    g.strokeStyle = css('--tts');
    g.lineWidth = 1.5;
    g.setLineDash([3, 3]);
    g.beginPath();
    g.moveTo(x, TOP - 6);
    g.lineTo(x, AXIS_Y);
    g.stroke();
    g.setLineDash([]);
  }
}

let raf = null;
function startDrawing() {
  if (raf) return;
  const tick = () => {
    drawTimeline();
    const settled = turn && turn.audioDone !== null &&
      (!audioCtx || !turn.playStart || audioCtx.currentTime > turn.playStart + turn.buffered);
    if (settled) { raf = null; drawTimeline(); return; }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
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

  // Text lives in its own node so a trailing cursor can sit beside it.
  const body = document.createElement('span');
  body.textContent = text;
  what.append(body);

  row.append(w, what);
  ui.log.append(row);
  return { row, what, body };
}

function showCursor(msg) {
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  msg.what.append(cursor);
  msg.cursor = cursor;
}

function hideCursor(msg) {
  if (msg && msg.cursor) {
    msg.cursor.remove();
    msg.cursor = null;
  }
}

/* ------------------------------------------------------------ metrics ui */

const secs = (v) => v === null || v === undefined
  ? '—'
  : `${v.toFixed(2)}<small>s</small>`;

function resetMetrics() {
  ui.mToken.innerHTML = '—';
  ui.mAudio.innerHTML = '—';
  ui.mText.innerHTML = '—';
  ui.mChars.textContent = '—';
  ui.mRate.innerHTML = '—';
}

/* --------------------------------------------------------------- pickers */

// Remembering the choice per browser is a convenience only; losing it is fine.
const remember = (key, value) => {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
};
const recall = (key) => {
  try { return localStorage.getItem(key); } catch (e) { return null; }
};

// "Meta-Llama-3.3-70B-Instruct" reads as "Llama 3.3 70B" in a 90px lane label.
function shortModel(id) {
  return String(id || '')
    .replace(/^Meta-/, '')
    .replace(/-Instruct$/, '')
    .replace(/-it$/, '')
    .replace(/-/g, ' ');
}

function fillPickers(m) {
  const models = m.models && m.models.length ? m.models : [m.llm_model];
  const wantModel = recall('voiceloop.model');
  ui.pickModel.innerHTML = '';
  models.forEach((id) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = shortModel(id);
    ui.pickModel.append(opt);
  });
  ui.pickModel.value = models.includes(wantModel) ? wantModel : m.llm_model;

  const voices = m.voices || [];
  ui.pickVoice.innerHTML = '';
  if (!voices.length) {
    const opt = document.createElement('option');
    opt.value = m.default_voice;
    opt.textContent = 'default voice';
    ui.pickVoice.append(opt);
    ui.pickVoice.disabled = true;
  } else {
    voices.forEach((v) => {
      const opt = document.createElement('option');
      opt.value = v.id;
      opt.textContent = v.language && v.language !== 'en'
        ? `${v.name} (${v.language})`
        : v.name;
      opt.title = v.description || '';
      ui.pickVoice.append(opt);
    });
    const wantVoice = recall('voiceloop.voice');
    const ids = voices.map((v) => v.id);
    ui.pickVoice.value = ids.includes(wantVoice) ? wantVoice : (
      ids.includes(m.default_voice) ? m.default_voice : ids[0]
    );
  }

  ui.llmModel.textContent = shortModel(ui.pickModel.value);
}

ui.pickModel.addEventListener('change', () => {
  remember('voiceloop.model', ui.pickModel.value);
  ui.llmModel.textContent = shortModel(ui.pickModel.value);
  drawTimeline();
});

ui.pickVoice.addEventListener('change', () => {
  remember('voiceloop.voice', ui.pickVoice.value);
});

/* --------------------------------------------------------------- socket */

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    ui.connDot.className = 'dot live';
    ui.connText.textContent = 'connected';
  };

  ws.onclose = () => {
    ui.connDot.className = 'dot dead';
    ui.connText.textContent = 'disconnected — restart the server and reload';
    setBusy(true);
  };

  ws.onerror = () => {
    ui.connDot.className = 'dot dead';
    ui.connText.textContent = 'connection error';
  };

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) { playPcm(ev.data); return; }
    handleEvent(JSON.parse(ev.data));
  };
}

function handleEvent(m) {
  switch (m.type) {
    case 'ready':
      ui.ttsModel.textContent = m.tts_model;
      ui.socketNote.textContent = `socket warm in ${Math.round(m.socket_setup_ms)} ms`;
      ui.connText.textContent = 'ready';
      fillPickers(m);
      setBusy(false);
      break;

    case 'turn_start':
      turn = newTurn();
      turn.model = m.model;
      playhead = 0;
      carry = null;
      resetMetrics();
      addMessage('you', 'you', m.prompt);
      turn.node = addMessage('bot', 'reply', '');
      showCursor(turn.node);
      startDrawing();
      break;

    case 'first_token':
      if (turn) turn.firstToken = m.t;
      ui.mToken.innerHTML = secs(m.t);
      break;

    case 'token':
      if (!turn) break;
      turn.chars += m.text.length;
      turn.node.body.textContent += m.text;
      ui.mChars.textContent = `${turn.chars} chars`;
      break;

    case 'text_done':
      if (turn) turn.textDone = m.t;
      ui.mText.innerHTML = secs(m.t);
      break;

    case 'first_audio':
      if (turn) turn.firstAudio = m.t;
      ui.mAudio.innerHTML = secs(m.t);
      break;

    case 'done': {
      if (turn) {
        turn.audioDone = m.metrics.total_s;
        hideCursor(turn.node);
      }
      const r = m.metrics.realtime_factor;
      ui.mRate.innerHTML = r ? `${r.toFixed(1)}<small>×</small>` : '—';
      ui.mText.innerHTML = secs(m.metrics.last_token_s);
      setBusy(false);
      break;
    }

    case 'error':
      addMessage('err', 'error', m.message);
      if (turn) {
        hideCursor(turn.node);
        turn.audioDone = elapsed();
      }
      setBusy(false);
      break;
  }
}

function setBusy(state) {
  busy = state;
  ui.send.disabled = state;
  ui.mic.disabled = state || !SpeechCtor;
  // Swapping model or voice mid-turn would mislabel the timeline.
  ui.pickModel.disabled = state;
  ui.pickVoice.disabled = state || ui.pickVoice.options.length < 2;
}

function submit(text) {
  const value = (text || ui.prompt.value).trim();
  if (!value || busy || !ws || ws.readyState !== WebSocket.OPEN) return;
  ensureAudio();                 // must be created inside a user gesture
  setBusy(true);
  ui.prompt.value = '';
  ws.send(JSON.stringify({
    type: 'prompt',
    text: value,
    model: ui.pickModel.value,
    voice_id: ui.pickVoice.value,
  }));
}

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
      for (let i = 0; i < data.length; i++) {
        peak = Math.max(peak, Math.abs(data[i] - 128) / 128);
      }
      ui.micLevel.style.height = `${Math.min(100, peak * 260)}%`;
      meterRaf = requestAnimationFrame(loop);
    };
    loop();
  } catch (err) {
    // No meter is fine — speech recognition has its own mic access.
  }
}

function stopMeter() {
  if (meterRaf) cancelAnimationFrame(meterRaf);
  meterRaf = null;
  ui.micLevel.style.height = '0%';
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micStream = null;
}

function startListening() {
  if (listening || busy || !SpeechCtor) return;

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
      if (ev.results[i].isFinal) finalText += chunk;
      else interim += chunk;
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

function stopListening() {
  if (rec) rec.stop();
}

/* --------------------------------------------------------------- wiring */

ui.send.addEventListener('click', () => submit());
ui.prompt.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') submit();
});

ui.mic.addEventListener('click', () => {
  listening ? stopListening() : startListening();
});

if (!SpeechCtor) {
  ui.mic.disabled = true;
  ui.mic.title = 'This browser has no speech recognition';
  ui.hint.textContent = 'Speech input needs Chrome, Edge, or Safari — typing works everywhere.';
}

window.addEventListener('resize', drawTimeline);
setBusy(true);
drawTimeline();
connect();
