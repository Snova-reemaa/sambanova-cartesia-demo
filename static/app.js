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
  pickSearch: el('pick-search'), pickLang: el('pick-lang'),
  attach: el('attach'), camera: el('camera'), file: el('file'),
  stage: el('stage'), selfview: el('selfview'), shots: el('shots'),
  grab: el('grab'), clearShots: el('clear-shots'), stageNote: el('stage-note'),
  camPick: el('cam-pick'),
  chooser: el('chooser'), chAsk: el('ch-ask'), chButtons: el('ch-buttons'),
  panel: el('requests-panel'), cards: el('cards'),
  call: el('call'), callView: el('callview'),
  callState: el('call-state'), callStateText: el('call-state-text'),
  callClock: el('call-clock'), capYou: el('cap-you'), capThem: el('cap-them'),
  callMic: el('call-mic'), callCam: el('call-cam'), callUpload: el('call-upload'),
  endCall: el('end-call'), callTrouble: el('call-trouble'), nocam: el('nocam'),
  wrapPanel: el('wrapup-panel'), wrapup: el('wrapup'), wrapMeta: el('wrapup-meta'),
  intro: el('intro'), live: el('live'), liveYou: el('live-you'), liveThem: el('live-them'),
  orb: el('orb'), orbState: el('orb-state'),
  pickCheck: el('pick-check'), pickEarly: el('pick-early'), pickInterp: el('pick-interp'),
  interpSwap: el('interp-swap'), share: el('share'), callShare: el('call-share'),
  shareBar: el('share-bar'), shareStop: el('share-stop'), audioNote: el('m-audio-note'),
  callerPanel: el('caller-panel'), callerTitle: el('caller-title'),
  callerStatus: el('caller-status'), callerCalls: el('caller-calls'),
  memoryReset: el('memory-reset'),
  visuals: el('visuals'), replayCall: el('replay-call'),
  costTurn: el('cost-turn'), costCall: el('cost-call'), costTokens: el('cost-tokens'),
  costChars: el('cost-chars'), costHuman: el('cost-human'),
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
    searchQuery: null, sources: null, searchS: null,
    attachments: 0, sawThumbs: null,
    thinkStart: null, thinkEnd: null, draftLead: null, thinker: null,
    cards: [], speaker: null, replaying: false,
    node: null, card: null,
  };
}

const since = (t) => performance.now() / 1000 - t.startedAt;

/* ------------------------------------------------------------- audio out */

let audioCtx = null;
let playhead = 0;
let sources = [];            // live BufferSourceNodes, so barge-in can stop them

let outNode = null;           // everything we play goes through here...
let outAnalyser = null;      // ...so the voice field can see what is being said
let inAnalyser = null;       // and this one hears the caller

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    outNode = audioCtx.createGain();
    outAnalyser = audioCtx.createAnalyser();
    outAnalyser.fftSize = 512;
    outAnalyser.smoothingTimeConstant = 0.72;
    outNode.connect(outAnalyser);
    outAnalyser.connect(audioCtx.destination);
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
  src.connect(outNode || ctx.destination);

  const at = Math.max(playhead, ctx.currentTime + 0.08);
  src.start(at);
  playhead = at + buf.duration;

  sources.push(src);
  src.onended = () => {
    sources = sources.filter((s) => s !== src);
    // Nothing left to hear: let go of the speaker. Without this playingId
    // stays pinned to the finished turn and the echo guard keeps comparing
    // every later utterance against an answer that stopped long ago.
    if (!sources.length && liveId === null) {
      const done = playingId !== null ? turns.get(playingId) : null;
      if (done && done.status === 'playing') done.status = 'played';
      playingId = null;
      if (settleLater) { settleLater = false; settle(); }
      // The button was only ever hidden by pressing it. Audio finishing on
      // its own cleared the state behind it and left Stop on screen with
      // nothing to stop.
      updateStopButton();
      render();
      if (inCall) refreshCallState();
    }
  };
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
  showLive(id, turn.prompt, turn.node ? turn.node.body.textContent : '');
  turn.played = true;
  if (turn.status === 'ready') turn.status = 'playing';
  flush(turn);
  hideChooser();
  select(id);
  render();
}

// Roughly how much of a turn's text the caller heard before playback stopped.
// Exact once the whole reply is synthesised (chars per audio-second of this
// very reply); before that, an ordinary speaking rate.
const SPOKEN_CPS = 14;

function heardChars(t) {
  if (t.playStart === null || !audioCtx) return 0;
  const heard = Math.min(Math.max(0, audioCtx.currentTime - t.playStart), t.buffered);
  const cps = (t.textDone !== null && t.audioDone !== null && t.buffered > 0)
    ? t.chars / t.buffered : SPOKEN_CPS;
  return Math.round(heard * cps);
}

// Stopping the speaker used to be browser-only: the server kept generating
// and synthesising, held one of the two Cartesia slots until it finished, and
// recorded the whole reply as if it had been heard.
function cutShort(t) {
  if (!t || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (t.replaying) { t.replaying = false; return; }   // stopping a replay changes nothing
  const stillRunning = t.status === 'thinking';
  const unheard = t.playStart === null || !audioCtx
    || (audioCtx.currentTime - t.playStart) < t.buffered - 0.25;
  if (!stillRunning && !unheard) return;          // it finished; nothing to cut
  ws.send(JSON.stringify({ type: 'cancel', turn_id: t.id, heard_chars: heardChars(t) }));
}

function stopAudio() {
  new Set([playingId, liveId]).forEach((id) => {
    if (id !== null) cutShort(turns.get(id));
  });
  sources.forEach((s) => { try { s.stop(); } catch (e) { /* already ended */ } });
  sources = [];
  playhead = audioCtx ? audioCtx.currentTime : 0;

  // Whatever was audible is finished with, whether it was streaming live or
  // playing from the buffer. Marking it played is what stops settle() from
  // picking it back up and resuming an answer the user just talked over.
  [playingId, liveId].forEach((id) => {
    if (id === null) return;
    const t = turns.get(id);
    if (!t) return;
    t.played = true;
    if (t.status === 'playing' || t.status === 'ready') t.status = 'played';
  });

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
let settleLater = false;
function settle() {
  if (running().length) return;         // still work in flight
  // Something is still audible (a handoff line, say): play what is waiting
  // once it finishes, rather than cutting it off.
  if (sources.length || playingId !== null) { settleLater = true; return; }
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
  { key: 'think',   label: 'Reasoning', sub: 'when needed' },
  { key: 'tts',     label: 'Cartesia',  sub: 'audio' },
  { key: 'speaker', label: 'Speaker',   sub: 'playing' },
];

const PAD_L = 104, PAD_R = 12, LANE_H = 20, LANE_GAP = 12, TOP = 14;
const AXIS_Y = TOP + LANES.length * (LANE_H + LANE_GAP) + 6;
// The width follows the panel it sits in, so the timeline is drawn crisp at
// whatever size the layout gives it instead of being scaled or scrolled.
let VIEW_W = 880;
const VIEW_H = 178;

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
  VIEW_W = Math.max(320, Math.floor((c.parentElement && c.parentElement.clientWidth) || 880));
  if (c.width !== VIEW_W * dpr || c.height !== VIEW_H * dpr) {
    c.width = VIEW_W * dpr; c.height = VIEW_H * dpr;
    c.style.width = `${VIEW_W}px`; c.style.height = `${VIEW_H}px`;
  }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, VIEW_W, VIEW_H);

  const plotW = VIEW_W - PAD_L - PAD_R;
  const now = turn ? since(turn) : 0;
  const genEnd = turn && turn.audioDone !== null ? turn.audioDone : now;
  const domain = Math.max(4, genEnd * 1.12);
  const X = (t) => PAD_L + Math.min(t / domain, 1) * plotW;

  const ink = css('--ink'), faint = css('--faint');
  g.font = '10.5px "Schibsted Grotesk", system-ui, sans-serif';

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
      if (id && id !== 'auto') return shortModel(id).slice(0, 18);
    }
    if (lane.key === 'think' && turn && turn.thinker) return shortModel(turn.thinker).slice(0, 18);
    return lane.sub;
  };

  LANES.forEach((lane, row) => {
    const y = TOP + row * (LANE_H + LANE_GAP);
    g.textAlign = 'right';
    g.fillStyle = ink; g.font = '11.5px "Schibsted Grotesk", system-ui, sans-serif';
    g.fillText(lane.label, PAD_L - 12, y + 10);
    g.fillStyle = faint; g.font = '10px "Schibsted Grotesk", system-ui, sans-serif';
    g.fillText(subFor(lane), PAD_L - 12, y + 21);
  });

  if (!turn) {
    g.fillStyle = faint;
    g.font = '11.5px "Schibsted Grotesk", system-ui, sans-serif';
    g.textAlign = 'left';
    g.fillText('idle — waiting for a prompt', PAD_L + 10, TOP + 12);
    return;
  }

  if (turn.firstToken === null) {
    bar(0, 0, now, css('--llm-soft'));
  } else {
    bar(0, 0, turn.firstToken, css('--llm-soft'));
    // The slice of that wait which was actually inference; the rest was wire.
    if (turn.inferTtft) {
      bar(0, Math.max(0, turn.firstToken - turn.inferTtft), turn.firstToken, css('--llm'));
    }
    bar(0, turn.firstToken, turn.textDone !== null ? turn.textDone : now, css('--llm'));

    if (turn.inferTtft && turn.overhead > 0.05) {
      // Label the wire's share of the wait only when the bar is wide enough
      // to hold it; otherwise it spills over the lane labels.
      const wireEnd = X(Math.max(0, turn.firstToken - turn.inferTtft));
      const label = `${Math.round(turn.overhead * 1000)} ms network`;
      g.font = '10px "Schibsted Grotesk", system-ui, sans-serif';
      if (wireEnd - PAD_L > g.measureText(label).width + 10) {
        g.fillStyle = css('--ink');
        g.textAlign = 'center';
        g.fillText(label, (PAD_L + wireEnd) / 2, TOP + 13);
      }
    }
  }

  // The reasoning model works alongside the talker, not after it.
  if (turn.thinkStart !== null) {
    bar(1, turn.thinkStart, turn.thinkEnd !== null ? turn.thinkEnd : now, css('--think'));
  }

  if (turn.firstToken !== null) {
    const ttsEnd = turn.firstAudio !== null ? turn.firstAudio : now;
    bar(2, turn.firstToken, ttsEnd, css('--tts-soft'));
    if (turn.firstAudio !== null) {
      bar(2, turn.firstAudio, turn.audioDone !== null ? turn.audioDone : now, css('--tts'));
    }
  }

  if (turn.firstAudio !== null) {
    const bufEnd = turn.firstAudio + turn.buffered;
    bar(3, turn.firstAudio, bufEnd, css('--tts-soft'));
    if (turn.playStart !== null && audioCtx) {
      const heard = turn.firstAudio + Math.max(0, audioCtx.currentTime - turn.playStart);
      bar(3, turn.firstAudio, Math.min(heard, bufEnd), css('--play'));
    }
    if (bufEnd > domain) {
      const y = TOP + 3 * (LANE_H + LANE_GAP);
      g.fillStyle = css('--ground');
      g.font = '600 11px "Schibsted Grotesk", system-ui, sans-serif';
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
  body.dir = 'auto';            // Arabic replies read right to left
  body.textContent = text;
  what.append(body);
  row.append(w, what);
  ui.log.append(row);
  followLog(true);
  return { row, what, body, cursor: null };
}

// Keep the newest line in view, unless the reader has scrolled up to look at
// something earlier — yanking them back down mid-read is worse than lagging.
function followLog(force) {
  const box = ui.log;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  if (force || nearBottom) box.scrollTop = box.scrollHeight;
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

// One quiet line under a reply, saying what happened behind it.
function note(turn, text, kind) {
  if (!turn || !turn.node) return;
  const p = document.createElement('p');
  p.className = `cmeta note-${kind || 'x'}`;
  p.textContent = text;
  turn.node.row.append(p);
  followLog(false);
}

function showCaller(m) {
  ui.callerPanel.hidden = false;
  ui.callerCalls.innerHTML = '';
  ui.callerPanel.dataset.status = m.status;
  if (m.status === 'verified') {
    ui.callerTitle.textContent = m.name || 'Caller';
    ui.callerStatus.textContent = m.calls.length
      ? `Recognised from order ${m.order}. ${m.calls.length} earlier call${m.calls.length === 1 ? '' : 's'} in memory:`
      : `Recognised from order ${m.order}. First call on record.`;
    m.calls.forEach((c) => {
      const li = document.createElement('li');
      const when = document.createElement('span');
      when.className = 'when';
      when.textContent = c.at.slice(0, 10) + (c.seeded ? '' : ', saved by this demo');
      const what = document.createElement('span');
      what.textContent = c.summary;
      li.append(when, what);
      if (c.actions && c.actions !== 'none') {
        const a = document.createElement('span');
        a.className = 'agreed';
        a.textContent = `Agreed: ${c.actions}`;
        li.append(a);
      }
      ui.callerCalls.append(li);
    });
  } else if (m.status === 'mismatch') {
    ui.callerTitle.textContent = 'Caller not verified';
    ui.callerStatus.textContent = `That email does not match order ${m.order}. Nothing from their history is shared.`;
  } else {
    ui.callerTitle.textContent = 'Caller';
    ui.callerStatus.textContent = `Order ${m.order} belongs to ${m.email}. Waiting for the caller to confirm that email before their history is used.`;
  }
}

ui.memoryReset.addEventListener('click', () => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'memory_reset' }));
});

function renderSources(turn) {
  const box = document.createElement('div');
  box.className = 'sources';
  const h = document.createElement('span');
  h.className = 'sh';
  h.textContent = `${turn.sources.length} sources`;
  box.append(h);
  turn.sources.forEach((r) => {
    const a = document.createElement('a');
    a.href = r.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = r.title || r.url;
    box.append(a);
  });
  turn.node.what.append(box);
}

function banner(text) {
  let box = el('session-banner');
  if (!box) {
    box = document.createElement('div');
    box.id = 'session-banner';
    box.className = 'banner';
    ui.log.parentElement.before(box);
  }
  box.textContent = text;
}

const WRAP_KEYS = ['SUMMARY', 'OUTCOME', 'ACTIONS', 'SENTIMENT', 'QUALITY'];

function showWrapup(note) {
  if (!note || !note.text) { ui.wrapPanel.hidden = true; return; }
  ui.wrapup.innerHTML = '';
  note.text.split('\n').forEach((line) => {
    const key = WRAP_KEYS.find((k) => line.toUpperCase().startsWith(k));
    if (!key) return;
    const row = document.createElement('div');
    row.className = 'row';
    const k = document.createElement('span');
    k.className = 'k';
    k.textContent = key.toLowerCase();
    const v = document.createElement('span');
    const value = line.slice(line.indexOf(':') + 1).trim();
    v.className = 'v ' + (key === 'OUTCOME' ? value.toLowerCase().split(/\W/)[0] : '');
    v.textContent = value;
    row.append(k, v);
    ui.wrapup.append(row);
  });
  ui.wrapMeta.textContent =
    `${note.model} read ${note.turns} question${note.turns === 1 ? '' : 's'}`
    + (note.prompt_tokens ? ` · ${note.prompt_tokens} tokens` : '');
  ui.wrapPanel.hidden = false;
}

function renderVision(turn, description) {
  const box = document.createElement('div');
  box.className = 'vision';

  const h = document.createElement('span');
  h.className = 'vh';
  h.textContent = 'what it saw';
  box.append(h);

  if (turn.sawThumbs && turn.sawThumbs.length) {
    const strip = document.createElement('div');
    strip.className = 'thumbs';
    turn.sawThumbs.forEach((url, i) => {
      const img = document.createElement('img');
      img.src = url;
      img.alt = `frame ${i + 1}`;
      strip.append(img);
    });
    box.append(strip);
  }

  const p = document.createElement('p');
  p.textContent = description;
  box.append(p);
  turn.node.what.append(box);
}

/* ------------------------------------------------------------ metrics ui */

const secs = (v) => (v === null || v === undefined) ? '—' : `${v.toFixed(2)}<small>s</small>`;

const ms = (v) => (v === null || v === undefined) ? '—' : `${Math.round(v * 1000)}<small>ms</small>`;

function showMetrics(t) {
  el('m-token').innerHTML = secs(t ? t.firstToken : null);
  el('m-audio').innerHTML = secs(t ? t.firstAudio : null);
  ui.audioNote.textContent = t && t.draftLead
    ? `Answer started ${t.draftLead.toFixed(1)} s before you finished`
    : 'From sending the question to hearing the reply';
  el('m-text').innerHTML = secs(t ? t.textDone : null);
  el('m-chars').textContent = t ? `${t.chars} chars` : '—';
  const rt = t && t.audioDone ? t.buffered / t.audioDone : null;
  el('m-rate').innerHTML = rt ? `${rt.toFixed(1)}<small>×</small>` : '—';

  // SambaNova reports its own time-to-first-token with no network in it, so
  // the gap between the two numbers is what the wire cost.
  el('m-infer').innerHTML = ms(t ? t.inferTtft : null);
  const note = el('m-overhead');
  if (t && t.overhead !== null && t.overhead !== undefined) {
    const tps = t.server && t.server.completion_tokens_after_first_per_sec;
    note.textContent = tps
      ? `${Math.round(t.overhead * 1000)} ms was network · ${Math.round(tps)} tok/s`
      : `${Math.round(t.overhead * 1000)} ms was network`;
  } else {
    note.textContent = "SambaNova's own clock";
  }
}


/* ------------------------------------------------------------ call mode */

// A call is the same pipeline with a different face on it: camera up, mic
// open, and no button to press between turns.
let inCall = false;
let callStartedAt = 0;
let callTimer = null;
let muted = false;

// Every utterance carrying a frame would add ~2.5s of vision to every turn.
// These are the words people use when they mean "look at this".
// 'it' and 'that' are far too common — "what time is it" is not someone
// holding something up to the camera.
const DEICTIC = /\b(this|these|those|here|mine|my)\b/i;
const LOOKING = /\b(look(s|ing)?|see|show|watch|find|match|similar|identify|read|says?|error|message|screen|light|code|serial|model|colou?r|broken|cracked|damaged|what|which)\b/i;

// Asking whether it can see you is itself a request to be looked at, and
// carries no pointing word — "can you see me" has nothing in DEICTIC.
const SEEING = /\b(see|look|watch|showing|holding|view)\b/i;

function wantsEyes(text) {
  if (sharingScreen) return true;      // a shared window is shared for a reason
  return SEEING.test(text) || (DEICTIC.test(text) && LOOKING.test(text));
}

function callState(name, label) {
  ui.callState.className = `pill ${name}`;
  ui.callStateText.textContent = label;
}

function refreshCallState() {
  if (!inCall) return;
  if (muted) return callState('muted', 'muted');
  if (sources.length || playingId !== null) return callState('speaking', 'speaking');
  if (running().length) return callState('thinking', 'thinking');
  callState('listening', listening ? 'listening' : 'starting mic');
}

function callTrouble(message) {
  ui.callTrouble.hidden = !message;
  if (message) ui.callTrouble.textContent = message;
}

async function enterCall() {
  inCall = true;
  ui.call.hidden = false;
  callTrouble('');
  callState('thinking', 'connecting');

  callStartedAt = Date.now();
  callTimer = setInterval(() => {
    const t = Math.floor((Date.now() - callStartedAt) / 1000);
    ui.callClock.textContent =
      `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
    refreshCallState();
  }, 1000);

  ui.camera.classList.add('on');
  ui.camera.title = 'End the call';

  // A refused camera or mic should cost the call that one thing, not the call.
  const problems = [];

  await startCamera(recall('voiceloop.camera') || undefined);
  showCallVideo();
  if (!camStream) problems.push('camera unavailable');

  startListening();
  // startListening resolves before onstart; give the permission prompt a beat
  // before deciding the mic is a problem.
  setTimeout(() => {
    if (inCall && !listening && !handsFree) {
      callTrouble('Microphone unavailable — you can still type below.');
    }
  }, 2500);

  if (problems.length) callTrouble(`${problems.join(' and ')} — the rest of the call still works.`);
  refreshCallState();
  // The call takes most of the column; keep the latest exchange in view.
  requestAnimationFrame(() => followLog(true));
}

function showCallVideo() {
  const live = !!camStream;
  ui.callView.srcObject = live ? camStream : null;
  ui.callView.classList.toggle('mirrored', live && camMirrored);
  ui.callView.classList.toggle('off', !live);
  ui.nocam.hidden = live;
  ui.callCam.classList.toggle('off', !live);
  ui.callCam.title = live ? 'Camera off' : 'Camera on';
  if (live) ui.callView.play().catch(() => { /* autoplay guard */ });
}

function leaveCall() {
  if (ws && ws.readyState === WebSocket.OPEN && turns.size) {
    ws.send(JSON.stringify({ type: 'wrapup' }));
  }
  inCall = false;
  clearInterval(callTimer);
  callTimer = null;
  stopListening();
  stopAudio();
  stopCamera();
  ui.callView.srcObject = null;
  ui.call.hidden = true;
  ui.camera.classList.remove('on');
  ui.camera.title = 'Start a video call';
  callTrouble('');
  ui.capYou.textContent = '';
  ui.capThem.textContent = '';
  updateStopButton();
  render();
}

ui.endCall.addEventListener('click', leaveCall);

ui.callMic.addEventListener('click', () => {
  muted = !muted;
  ui.callMic.classList.toggle('off', muted);
  ui.callMic.title = muted ? 'Unmute' : 'Mute';
  if (muted) stopListening(); else startListening();
  refreshCallState();
});

ui.callCam.addEventListener('click', async () => {
  if (camStream) stopCamera();
  else await startCamera(recall('voiceloop.camera') || undefined);
  showCallVideo();
});

// Upload from inside the call — a photo, a video, a screenshot of an error.
// Videos become frames the same way they do outside a call.
ui.callUpload.addEventListener('click', () => ui.file.click());

/* --------------------------------------------------------------- pickers */

const remember = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const recall = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };

function fillPickers(m) {
  const models = m.models && m.models.length ? m.models : [m.llm_model];
  const wantModel = recall('voiceloop.model');
  ui.pickModel.innerHTML = '';
  models.forEach((id) => {
    const o = document.createElement('option');
    o.value = id;
    // MiniMax ignores the description the vision model hands it — 0/3 against
    // Llama's 3/3 on the same image — and answers "I can't quite see it from
    // this angle" with a perfectly good description sitting in its context.
    const blind = /minimax/i.test(id);
    o.textContent = shortModel(id) + (blind ? ' — ignores photos' : '');
    o.title = blind
      ? 'Fine for voice, but discards what the camera saw. Use Llama for anything visual.'
      : '';
    ui.pickModel.append(o);
  });
  // Auto routes each turn: small talk to the smallest model, hard questions
  // to a reasoning model working alongside the talker.
  const auto = document.createElement('option');
  auto.value = 'auto';
  auto.textContent = 'Auto, per turn';
  auto.title = `Small talk: ${m.quick_model || 'smallest model'}. Hard questions: ${m.think_model || 'reasoning model'} thinks while ${m.llm_model} talks.`;
  ui.pickModel.prepend(auto);
  ui.pickModel.value = (wantModel === 'auto' || !wantModel) ? 'auto'
    : (models.includes(wantModel) ? wantModel : 'auto');

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
  ui.llmModel.textContent = ui.pickModel.value === 'auto' ? 'Auto' : shortModel(ui.pickModel.value);
}

ui.pickModel.addEventListener('change', () => {
  remember('voiceloop.model', ui.pickModel.value);
  ui.llmModel.textContent = ui.pickModel.value === 'auto' ? 'Auto' : shortModel(ui.pickModel.value);
});
ui.pickVoice.addEventListener('change', () => remember('voiceloop.voice', ui.pickVoice.value));
ui.pickSearch.addEventListener('change', () => remember('voiceloop.search', ui.pickSearch.checked ? '1' : '0'));
if (recall('voiceloop.search') === '0') ui.pickSearch.checked = false;
ui.pickCheck.addEventListener('change', () => remember('voiceloop.check', ui.pickCheck.checked ? '1' : '0'));
ui.pickEarly.addEventListener('change', () => remember('voiceloop.early', ui.pickEarly.checked ? '1' : '0'));
if (recall('voiceloop.check') === '0') ui.pickCheck.checked = false;
if (recall('voiceloop.early') === '0') ui.pickEarly.checked = false;


/* ------------------------------------------------------- attachments */

// Frames are resized before they leave the browser: the vision model sees a
// fixed token budget regardless, and full-size phone photos would blow past the
// websocket message limit for no benefit.
// 768 and 384 both cost gemma exactly 413 prompt tokens — the image is
// normalised either way — so the only thing a bigger frame buys is upload
// time, which is real over a phone connection.
const FRAME_EDGE = 512;
const FRAME_QUALITY = 0.82;
let MAX_FRAMES = 8;

let pending = [];        // data URLs queued for the next turn
let camStream = null;

// Text in a dialog box does not survive 512 px; a screen frame is sent at
// the size it needs to stay readable.
const SCREEN_EDGE = 1440;

function drawScaled(source, w, h) {
  const edge = (sharingScreen && (source === ui.selfview || source === ui.callView)) ? SCREEN_EDGE : FRAME_EDGE;
  const scale = Math.min(1, edge / Math.max(w, h));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', FRAME_QUALITY);
}

function fromImageFile(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      resolve(drawScaled(img, img.naturalWidth, img.naturalHeight));
      URL.revokeObjectURL(url);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('unreadable image')); };
    img.src = url;
  });
}

// A video becomes a handful of stills. Neither vision model takes video, and
// evenly spaced frames are what a support agent would flick through anyway.
function framesFromVideo(file, count) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    const url = URL.createObjectURL(file);
    const out = [];

    const fail = (msg) => { URL.revokeObjectURL(url); reject(new Error(msg)); };

    video.onerror = () => fail('could not read that video');
    video.onloadedmetadata = () => {
      const dur = video.duration;
      if (!isFinite(dur) || dur <= 0) return fail('video has no duration');
      // Skip the very start and end; first and last frames are often black.
      const times = Array.from({ length: count },
        (_, i) => dur * (i + 0.5) / count);
      let i = 0;
      video.onseeked = () => {
        out.push(drawScaled(video, video.videoWidth, video.videoHeight));
        i++;
        if (i < times.length) video.currentTime = times[i];
        else { URL.revokeObjectURL(url); resolve(out); }
      };
      video.currentTime = times[0];
    };
    video.src = url;
  });
}

function renderShots() {
  ui.shots.innerHTML = '';
  pending.forEach((url, i) => {
    const box = document.createElement('div');
    box.className = 'shot';
    const img = document.createElement('img');
    img.src = url;
    img.alt = `attachment ${i + 1}`;
    const n = document.createElement('span');
    n.className = 'n';
    n.textContent = i + 1;
    const x = document.createElement('button');
    x.type = 'button';
    x.textContent = '×';
    x.title = 'Remove';
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      pending.splice(i, 1);
      renderShots();
    });
    box.append(img, n, x);
    ui.shots.append(box);
  });
  updateStage();
}

function updateStage() {
  const live = !!camStream;
  ui.stage.hidden = !live && !pending.length;
  ui.selfview.hidden = !live;
  ui.grab.hidden = !live;
  ui.clearShots.hidden = !pending.length;
  ui.attach.classList.toggle('on', pending.length > 0);
  ui.stageNote.textContent = pending.length
    ? `${pending.length} of ${MAX_FRAMES} attached — they go with your next message`
    : (live ? (sharingScreen
      ? 'Sharing a window. Ask about it and the agent reads a frame of it.'
      : 'Camera on. Capture a frame, or just ask and it grabs one.') : '');
}

function addFrames(urls) {
  const room = MAX_FRAMES - pending.length;
  if (room <= 0) {
    ui.hint.className = 'hint mono warn';
    ui.hint.textContent = `That is the limit — ${MAX_FRAMES} frames per message.`;
    return;
  }
  pending.push(...urls.slice(0, room));
  renderShots();

  // In a call there is no attachment strip in view, so say what was taken and
  // that it is waiting on you to ask something about it.
  if (inCall) {
    const n = pending.length;
    ui.capYou.textContent =
      `${n} ${n === 1 ? 'file' : 'files'} attached — now ask about ${n === 1 ? 'it' : 'them'}`;
  }
}

async function takeFiles(files) {
  for (const file of files) {
    try {
      if (file.type.startsWith('video/')) {
        ui.stageNote.textContent = 'pulling frames out of the video…';
        if (inCall) ui.capYou.textContent = 'pulling frames out of the video…';
        else ui.stage.hidden = false;
        const room = Math.max(1, Math.min(6, MAX_FRAMES - pending.length));
        addFrames(await framesFromVideo(file, room));
      } else if (file.type.startsWith('image/')) {
        addFrames([await fromImageFile(file)]);
      }
    } catch (err) {
      ui.hint.className = 'hint mono warn';
      ui.hint.textContent = `${file.name}: ${err.message}`;
    }
  }
}

// Why the camera fails, in words that say what to do about it.
const CAMERA_TROUBLE = {
  NotAllowedError: 'Camera blocked. Click the camera icon in the address bar and allow it, then press the camera button again.',
  NotFoundError: 'No camera found on this machine. Attaching a photo works the same way.',
  NotReadableError: 'Another app is holding the camera — quit Zoom, Teams or Photo Booth and try again.',
  OverconstrainedError: 'That camera cannot provide a usable size. Pick a different one.',
  SecurityError: 'The browser will only open a camera over https or on localhost.',
};

let camMirrored = false;   // front cameras look wrong unless the preview flips

async function listCameras() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === 'videoinput');
  } catch (e) {
    return [];
  }
}

async function buildCameraPicker() {
  const cams = await listCameras();
  ui.camPick.innerHTML = '';
  cams.forEach((d, i) => {
    const o = document.createElement('option');
    o.value = d.deviceId;
    o.textContent = d.label || `Camera ${i + 1}`;
    ui.camPick.append(o);
  });
  // Only worth showing when there is actually a choice to make.
  ui.camPick.hidden = cams.length < 2;
  const want = recall('voiceloop.camera');
  if (want && cams.some((d) => d.deviceId === want)) ui.camPick.value = want;
}

async function startCamera(deviceId) {
  // No facingMode: asking a laptop for the rear camera is asking for a camera
  // it does not have. Let the browser pick, or use the device the user chose.
  const video = deviceId
    ? { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
    : { width: { ideal: 1280 }, height: { ideal: 720 } };

  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video });
    ui.selfview.srcObject = camStream;
    await ui.selfview.play();

    const track = camStream.getVideoTracks()[0];
    const settings = track.getSettings ? track.getSettings() : {};
    camMirrored = settings.facingMode !== 'environment';
    ui.selfview.classList.toggle('mirrored', camMirrored);

    // Labels are only readable after permission is granted, so build the
    // picker now rather than on page load.
    await buildCameraPicker();
    if (settings.deviceId) {
      ui.camPick.value = settings.deviceId;
      remember('voiceloop.camera', settings.deviceId);
    }

    ui.camera.classList.add('on');
    ui.camera.title = 'Turn the camera off';
    ui.hint.className = 'hint mono';
    ui.hint.textContent = `${track.label || 'camera'} · ${settings.width || '?'}×${settings.height || '?'}`;
    updateStage();
  } catch (err) {
    camStream = null;
    ui.hint.className = 'hint mono warn';
    ui.hint.textContent = CAMERA_TROUBLE[err.name]
      || `Camera failed: ${err.name}. Attaching a photo works the same way.`;
    updateStage();
  }
}

function stopCamera() {
  if (sharingScreen) { stopScreen(); return; }
  if (camStream) camStream.getTracks().forEach((t) => t.stop());
  camStream = null;
  ui.selfview.srcObject = null;
  ui.selfview.classList.remove('mirrored');
  ui.camera.classList.remove('on');
  ui.camera.title = 'Turn the camera on';
  ui.camPick.hidden = true;
  updateStage();
}

function captureFrame(video) {
  const v = video || ui.selfview;
  if (!camStream || !v.videoWidth) return null;
  return drawScaled(v, v.videoWidth, v.videoHeight);
}

function grabFrame() {
  if (!camStream || !ui.selfview.videoWidth) return null;
  // Captured un-mirrored on purpose, even though the preview is flipped: a
  // serial number or error message held up to the lens has to reach the model
  // the right way round.
  const url = drawScaled(ui.selfview, ui.selfview.videoWidth, ui.selfview.videoHeight);
  addFrames([url]);
  return url;
}

ui.camPick.addEventListener('change', async () => {
  remember('voiceloop.camera', ui.camPick.value);
  if (camStream) {
    camStream.getTracks().forEach((t) => t.stop());
    camStream = null;
    await startCamera(ui.camPick.value);
  }
});

ui.attach.addEventListener('click', () => ui.file.click());
ui.file.addEventListener('change', async () => {
  await takeFiles([...ui.file.files]);
  ui.file.value = '';
});
// The camera button is the call button. Pressing video should get you a video
// call, not a preview pane you then have to do something else with.
ui.camera.addEventListener('click', () => (inCall ? leaveCall() : enterCall()));
ui.grab.addEventListener('click', grabFrame);
ui.clearShots.addEventListener('click', () => { pending = []; renderShots(); });

// Drag a photo anywhere onto the page.
['dragenter', 'dragover'].forEach((ev) =>
  document.addEventListener(ev, (e) => {
    if (![...(e.dataTransfer?.types || [])].includes('Files')) return;
    e.preventDefault();
    document.body.classList.add('dropping');
  }));
['dragleave', 'drop'].forEach((ev) =>
  document.addEventListener(ev, (e) => {
    if (ev === 'drop') e.preventDefault();
    document.body.classList.remove('dropping');
  }));
document.addEventListener('drop', (e) => {
  if (e.dataTransfer?.files?.length) takeFiles([...e.dataTransfer.files]);
});

// Paste a screenshot straight in.
document.addEventListener('paste', (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); takeFiles(files); }
});

/* --------------------------------------------------------------- socket */

let reconnectAttempt = 0;
let reconnectTimer = null;
let givingUp = false;

const RELOAD_AFTER = 5;

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectAttempt += 1;

  // A brief network drop reconnects on the same session. A restarted server
  // has thrown the session away, so the socket will keep returning 401 for
  // ever — reloading either comes straight back in or shows the login, both
  // of which beat retrying silently.
  if (reconnectAttempt > RELOAD_AFTER) {
    ui.connText.textContent = 'reconnecting — reloading the page';
    setTimeout(() => location.reload(), 600);
    return;
  }
  // Back off, but never so far that it looks dead: 1s, 2s, 4s, then every 8s.
  const wait = Math.min(1000 * 2 ** (reconnectAttempt - 1), 8000);
  ui.connDot.className = 'dot warn';
  ui.connText.textContent = `reconnecting in ${Math.round(wait / 1000)}s…`;
  if (inCall) callTrouble('Connection lost — reconnecting…');
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    ui.connText.textContent = 'reconnecting…';
    connect();
  }, wait);
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    connected = true;
    const returning = reconnectAttempt > 0;
    reconnectAttempt = 0;
    ui.connDot.className = 'dot live';
    ui.connText.textContent = returning ? 'reconnected' : 'connected';
    if (returning) {
      // A new socket is a new conversation server-side; say so rather than
      // letting it look like the agent forgot.
      addMessage('err', 'note', 'Connection dropped and came back. The agent has lost the thread of the conversation.');
      if (inCall) callTrouble('');
    }
  };
  ws.onclose = () => {
    connected = false;
    ui.send.disabled = true;
    // Hotel wifi drops. Losing the connection should cost a few seconds, not
    // the demo — and it took the buffered answers with it before.
    if (!givingUp) scheduleReconnect();
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
  if (liveId === id || playingId === id) {
    // Claim the speaker. Without this playingId stays null through a live
    // answer, and the next turn is also judged free to stream over the top.
    if (playingId === null) playingId = id;
    flush(turn);
  }

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
      audioAvailable = m.audio !== false;
      if (m.audio === false) {
        // Text still works; say so plainly rather than leaving silence
        // looking like a bug.
        ui.connDot.className = 'dot warn';
        ui.connText.textContent = 'ready, text only';
        ui.hint.className = 'hint mono warn';
        ui.hint.textContent = m.audio_trouble || 'Speech synthesis is unavailable — replies are text only.';
        ui.pickVoice.disabled = true;
      }
      if (m.max_frames) MAX_FRAMES = m.max_frames;
      humanCost = typeof m.human_cost === 'number' ? m.human_cost : null;
      if (m.vision_model) ui.camera.title = `Camera — read by ${m.vision_model}`;
      ui.send.disabled = false;
      ui.mic.disabled = !SpeechCtor;
      break;

    case 'attachments_dropped': {
      // A picture that did not make it used to vanish without a word, and
      // the agent answered about whichever ones survived.
      const why = (m.reasons || []).join('; ');
      ui.hint.className = 'hint mono warn';
      ui.hint.textContent = why
        ? `Some attachments were not sent: ${why}.`
        : 'Some attachments were not sent.';
      break;
    }

    case 'audio_off': {
      // Synthesis gave out part way through the call — credit ran out, most
      // often. The server emitted this and nothing listened for it, so the
      // voice simply stopped and the caller was left watching a reply
      // arrive in silence, with no way to tell that from a bug.
      audioAvailable = false;
      ui.connDot.className = 'dot warn';
      ui.connText.textContent = 'ready, text only';
      ui.hint.className = 'hint mono warn';
      ui.hint.textContent = m.message
        ? `${m.message} Replies are text only from here.`
        : 'The voice stopped working — replies are text only from here.';
      ui.pickVoice.disabled = true;
      const turn = turns.get(m.turn_id);
      if (turn) note(turn, 'voice unavailable — this reply is text only', 'x');
      break;
    }

    case 'reheard':
      reheard = m;
      break;

    case 'turn_start': {
      if (m.interpreting) {
        // The other person answers in the language they just heard.
        setRecLang(m.interpreting.to);
        updateSwap();
      } else if (m.language) {
        followLanguage(m.language);
      }
      const t = newTurn(m.turn_id, m.prompt, m.model);
      t.speaker = m.speaker || null;
      t.attachments = m.attachments || 0;
      if (t.attachments) { t.sawThumbs = lastSent.slice(); lastSent = []; }
      if (m.speculative) t.speculative = true;
      turns.set(m.turn_id, t);
      if (m.intro && m.speaker) {
        // A specialist taking over speaks first; nobody asked anything new.
        showLive(m.turn_id, `${m.speaker.name} from ${m.speaker.team} joined`, '', true);
      } else {
        showLive(m.turn_id, m.prompt, '');
        const you = addMessage('you', 'you', m.prompt);
        // The transcript was repaired on the way in, so what is on screen is
        // not quite what they said. Show the correction rather than rewriting
        // their words behind their back — an order number or an address that
        // silently changes is worse than one that visibly did.
        if (reheard && reheard.turn_id === m.turn_id && you && you.row) {
          const p = document.createElement('p');
          p.className = 'cmeta note-x';
          p.textContent = 'heard ' + reheard.changes
            .map(c => `"${c.from}" — read as "${c.to}"`).join('; ');
          you.row.append(p);
        }
        reheard = null;
      }
      t.node = addMessage('bot', m.speaker ? m.speaker.name : 'reply', '');
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
      turn.node.body.append(document.createTextNode(m.text));
      followLog(false);
      if (m.turn_id === liveTurn) ui.liveThem.append(document.createTextNode(m.text));
      if (inCall) ui.capThem.textContent = turn.node.body.textContent;
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

    case 'searching':
      if (turn) {
        turn.searchQuery = m.query;
        const note = document.createElement('p');
        note.className = 'searching';
        note.textContent = `searching the web for "${m.query}"`;
        turn.node.what.after(note);
        turn.searchNote = note;
      }
      render();
      break;

    case 'sources':
      if (turn) {
        turn.sources = m.results || [];
        turn.searchS = m.took_s;
        if (turn.searchNote) {
          turn.searchNote.className = 'cmeta mono';
          turn.searchNote.textContent =
            `searched "${turn.searchQuery}" in ${Math.round(m.took_s * 1000)} ms`;
        }
        if (turn.sources.length) renderSources(turn);
      }
      render();
      break;

    case 'looking':
      if (turn) {
        turn.speculative = !!m.speculative;
        const note = document.createElement('p');
        note.className = 'searching';
        note.textContent = m.count > 1
          ? `looking through ${m.count} frames…`
          : 'looking at the photo…';
        turn.node.what.after(note);
        turn.lookNote = note;
      }
      render();
      break;

    case 'looked':
      if (turn) {
        turn.visionS = m.took_s;
        if (turn.lookNote) {
          turn.lookNote.className = 'cmeta mono';
          const what = m.screen ? 'your screen'
            : (turn.attachments > 1 ? `${turn.attachments} frames` : 'the image');
          turn.lookNote.textContent = turn.speculative
            ? `${m.model} had already read ${what}, so the wait was ${Math.round(m.took_s * 1000)} ms`
            : `${m.model} read ${what} in ${Math.round(m.took_s * 1000)} ms`;
        }
        renderVision(turn, m.description);
      }
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
        if (liveId === m.turn_id) liveId = null;   // generation done; audio may still be draining
        turn.server = m.metrics.server || null;
        turn.inferTtft = m.metrics.inference_ttft_s;
        turn.overhead = m.metrics.network_overhead_s;
        // A turn the user already stopped stays stopped; don't advertise it
        // as ready to play again.
        turn.status = turn.played ? 'played'
          : ((liveId === m.turn_id || playingId === m.turn_id) ? 'playing' : 'ready');
        if (liveId === m.turn_id) turn.played = true;
        hideCursor(turn.node);
        if (m.metrics.cost) showCost(m.metrics.cost, m.metrics.cost_call);
        addReplay(turn);
      }
      if (m.turn_id === selectedId) showMetrics(turn);
      render();
      break;

    case 'route':
      if (turn) {
        turn.model = m.model;
        turn.thinker = m.thinker || null;
        if (m.tier === 'deep') {
          note(turn, `Needs working out: ${shortModel(m.thinker)} reasoned while ${shortModel(m.model)} talked`, 'route');
        } else if (m.tier === 'interpret') {
          note(turn, `Interpreted ${m.reason} by ${shortModel(m.model)}`, 'route');
        } else {
          const why = m.tier === 'quick' ? 'small talk, so the smallest model'
            : (m.reason === 'chosen in the picker' ? 'the model you picked' : m.reason);
          note(turn, `Answered by ${shortModel(m.model)}: ${why}`, 'route');
        }
      }
      break;

    case 'draft_used':
      if (turn) {
        turn.draftLead = m.lead_s;
        note(turn, `Started answering ${m.lead_s.toFixed(1)} s before you finished`, 'early');
        if (m.turn_id === selectedId) showMetrics(turn);
      }
      break;

    case 'mood':
      if (turn && m.mood !== 'neutral') {
        const said = { frustrated: 'You sound frustrated', settling: 'Calming down',
          confused: 'You sound unsure', hurried: 'You sound in a hurry' }[m.mood] || m.mood;
        note(turn, `${said}: ${m.says}`, 'mood');
      }
      break;

    case 'thinking':
      if (turn) turn.thinkStart = m.t;
      break;

    case 'thought':
      if (turn) {
        turn.thinkEnd = m.t;
        note(turn, m.ok
          ? `${shortModel(m.model)} reasoned for ${m.took_s.toFixed(1)} s`
          : `${shortModel(m.model)} did not answer; the talker finished instead`, 'think');
      }
      break;

    case 'held':
      if (turn) {
        const del = document.createElement('del');
        del.title = `Held back: ${m.reason}`;
        del.textContent = m.sentence + ' ';
        turn.node.body.append(del);
        note(turn, `Held back before speaking: ${m.reason}`, 'held');
      }
      break;

    case 'recovered':
      if (turn) {
        note(turn, m.ok
          ? `Rewrote ${m.held === 1 ? 'that' : 'those'} from the records instead`
          : 'Still not in the records after a rewrite, so it said it would check', 'check');
      }
      break;

    case 'recovered':
      if (turn) {
        note(turn, m.ok
          ? `Rewrote ${m.held === 1 ? 'that' : 'those'} from the records instead`
          : 'Still not in the records after a rewrite, so it said it would check', 'check');
      }
      break;

    case 'checked':
      if (turn) {
        note(turn, m.held
          ? `Fact-checked ${m.checked} sentence${m.checked === 1 ? '' : 's'} against the records, held back ${m.held}`
          : `Fact-checked ${m.checked} sentence${m.checked === 1 ? '' : 's'} against the records`, 'check');
      }
      break;

    case 'caller':
      showCaller(m);
      break;

    case 'order':
      if (turn && m.found) addCard(turn, { ...m, kind: 'order' });
      break;

    case 'knowledge':
      if (turn && m.excerpts && m.excerpts.length) addCard(turn, { kind: 'policy', excerpts: m.excerpts });
      break;

    case 'action':
      if (turn) {
        addCard(turn, { ...m, kind: 'action', action: m.kind });
        note(turn, `Done: ${ACTION_TITLE[m.kind] || m.kind}, reference ${m.ref}`, 'action');
      }
      break;

    case 'handoff':
      if (turn) {
        addCard(turn, { ...m, kind: 'handoff' });
        note(turn, `Transferred to ${m.name} (${m.label})`, 'handoff');
      }
      break;

    case 'memory':
      ui.callerPanel.hidden = true;
      ui.hint.className = 'hint';
      ui.hint.textContent = 'Memory reset. The seeded customers are back, and every call since is forgotten.';
      break;

    case 'cancelled':
      if (turn) {
        hideCursor(turn.node);
        turn.played = true;
        turn.status = 'played';
        if (turn.audioDone === null) turn.audioDone = since(turn);
        const note = document.createElement('p');
        note.className = 'cmeta mono';
        note.textContent = 'interrupted — stopped on the server too';
        turn.node.what.after(note);
      }
      if (liveId === m.turn_id) liveId = null;
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

    case 'spent':
      banner(m.message);
      ui.send.disabled = true;
      stopListening();
      break;

    case 'wrapup':
      showWrapup(m.note);
      if (m.cost_call) showCost(null, m.cost_call);
      if (m.remembered) {
        ui.wrapMeta.textContent += `. Saved to ${m.caller || 'the caller'}'s history.`;
      }
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

  // Camera on but nothing captured. Outside a call, asking at all means you
  // want it looked at. In a call the camera is always up, so attaching every
  // frame would put ~2.5s of vision on every turn — only grab one when the
  // words actually point at something.
  let prelook = null;
  if (pendingLook && inCall && !pending.length && wantsEyes(value)) {
    prelook = pendingLook.id;
    lastSent = [pendingLook.url];       // for the "what it saw" thumbnail
  } else if (!pending.length && camStream && (!inCall || wantsEyes(value))) {
    grabFrame();
  }
  pendingLook = null;

  if (inCall) {
    ui.capYou.textContent = value;
    ui.capThem.textContent = '';
  }

  const images = pending.slice();
  lastSent = images;
  pending = [];
  renderShots();

  ui.prompt.value = '';
  ui.hint.className = 'hint mono';
  hideChooser();
  if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
  const draft = currentDraft && sameWords(currentDraft.text, value) ? currentDraft.id : null;
  currentDraft = null;
  ws.send(JSON.stringify({
    type: 'prompt',
    text: value,
    ...turnOptions(),
    prelook,
    images,
    draft,
  }));
}

// Everything that shapes how a turn is answered. Drafts send the same, so a
// draft made under different settings is never mistaken for the real turn.
function turnOptions() {
  return {
    model: ui.pickModel.value,
    voice_id: ui.pickVoice.value,
    search: ui.pickSearch.checked,
    check: ui.pickCheck.checked,
    language: ui.pickLang.value === 'auto' ? null : ui.pickLang.value,
    mode: ui.pickInterp.checked ? 'interpret' : 'agent',
    pair: interpPair(),
    source: sharingScreen ? 'screen' : 'camera',
    call: inCall,
  };
}

/* ----------------------------------------------------------- early answers */

// When the caller pauses mid-sentence, or stops typing, start answering what
// they have said so far. If that turns out to be the whole question, the
// answer is already written by the time they finish; if they carry on, the
// draft is thrown away and never heard.
let draftSeq = 0;
let currentDraft = null;
let draftTimer = null;
const DRAFT_PAUSE_SPEECH = 320;
const DRAFT_PAUSE_TYPING = 450;

const wordsOf = (t) => (t || '').toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, '').split(/\s+/).filter(Boolean);
const sameWords = (a, b) => wordsOf(a).join(' ') === wordsOf(b).join(' ');

function canDraft(text) {
  if (!ui.pickEarly.checked || ui.pickInterp.checked) return false;
  if (!ws || ws.readyState !== WebSocket.OPEN || !connected) return false;
  if (pending.length || camStream) return false;        // looking needs the real turn
  return wordsOf(text).length >= 3;
}

function sendDraft(text) {
  if (!canDraft(text)) return;
  if (currentDraft && sameWords(currentDraft.text, text)) return;
  const id = ++draftSeq;
  currentDraft = { id, text };
  ws.send(JSON.stringify({ type: 'draft', id, text, ...turnOptions() }));
}

function draftSoon(text, wait) {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => { draftTimer = null; sendDraft(text); }, wait);
}

ui.prompt.addEventListener('input', () => {
  if (listening) return;               // speech drives drafts while the mic is on
  draftSoon(ui.prompt.value.trim(), DRAFT_PAUSE_TYPING);
});

let lastSent = [];

ui.send.addEventListener('click', () => submit());

// Starter prompts on the empty transcript, for demoing without a script.
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-prompt]');
  if (b) submit(b.dataset.prompt);
});
ui.prompt.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
ui.stop.addEventListener('click', () => { stopAudio(); render(); updateStopButton(); });

/* ------------------------------------------------------------------ mic */

const SpeechCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false, micStream = null, meterRaf = null;

async function startMeter() {
  if (micStream) return;        // hands-free restarts must not stack streams
  try {
    // Echo cancellation matters more here than anywhere else: without it the
    // mic hears our own answer through the speakers and treats it as speech.
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = ensureAudio();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    ctx.createMediaStreamSource(micStream).connect(analyser);
    inAnalyser = analyser;
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
  inAnalyser = null;
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micStream = null;
}

// Hands-free. The mic stays open between turns: having to press a button
// before each utterance is exactly what makes barge-in pointless, since by the
// time you have reached for the mouse the answer has finished anyway.
let handsFree = false;
let restartTimer = null;
let sinceSpeech = null;      // debounce timer for "they have stopped talking"
let finalText = '';

const SETTLE_MS = 900;       // silence after a final result before sending

// The mic hears our own speaker. Echo cancellation catches most of it, but a
// transcript that is just our own words coming back must not count as speech.
function soundsLikeUs(text) {
  const turn = playingId !== null ? turns.get(playingId) : null;
  const mine = (turn && turn.node ? turn.node.body.textContent : '').toLowerCase();
  if (!mine) return false;
  const words = text.toLowerCase().split(/\s+/).filter((w) => w.length > 3);
  // Bias hard towards believing the human. Mistaking echo for speech only
  // stops audio the user can restart; mistaking speech for echo means they
  // cannot interrupt at all, which is the whole point of hands-free.
  if (words.length < 4) return false;
  const echoed = words.filter((w) => mine.includes(w)).length;
  return echoed / words.length > 0.7;
}

let prelookSeq = 0;
let pendingLook = null;      // { id, url } already being described server-side

function startPrelook() {
  const url = captureFrame(ui.callView.srcObject ? ui.callView : ui.selfview);
  if (!url || !ws || ws.readyState !== WebSocket.OPEN) return;
  const id = ++prelookSeq;
  pendingLook = { id, url };
  ws.send(JSON.stringify({ type: 'prelook', id, images: [url], source: sharingScreen ? 'screen' : 'camera' }));
}

function sendWhatWasSaid() {
  const said = finalText.trim();
  finalText = '';
  ui.prompt.value = '';
  if (said) submit(said);
}

function startListening() {
  if (listening || !SpeechCtor) return;

  rec = new SpeechCtor();
  rec.lang = SPEECH_LANG[recLang] || 'en-US';
  rec.interimResults = true;
  rec.continuous = true;        // stay open; do not stop after one utterance
  handsFree = true;

  rec.onstart = () => {
    listening = true;
    lastMicEvent = Date.now();
    if (inCall) { callTrouble(''); refreshCallState(); }
    ui.mic.classList.add('on');
    ui.prompt.classList.add('listening');
    ui.hint.className = 'hint mono';
    ui.hint.textContent = 'listening — just talk, and talk over it to interrupt';
    startMeter();
  };

  rec.onresult = (ev) => {
    let interim = '';
    let fresh = '';
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const chunk = ev.results[i][0].transcript;
      if (ev.results[i].isFinal) {
        fresh += chunk;
        noteConfidence(ev.results[i][0].confidence);
      } else {
        interim += chunk;
      }
    }

    const heard = (fresh + interim).trim();
    if (!heard) return;

    // Our own voice bouncing back is not an interruption.
    if (soundsLikeUs(heard)) return;

    // Real speech while it is talking: stop, immediately, on the first word.
    if (sources.length || playingId !== null) {
      stopAudio();
      updateStopButton();
      render();
    }

    // Start looking now, while they are still mid-sentence. By the time the
    // utterance lands the description is usually already back, which takes
    // the whole vision round trip off the clock.
    if (inCall && camStream && pendingLook === null) startPrelook();

    finalText += fresh;
    ui.prompt.value = (finalText + interim).trim();
    if (inCall) ui.capYou.textContent = ui.prompt.value;
    lastMicEvent = Date.now();

    // Send once they have actually stopped, not on every pause in a sentence.
    if (sinceSpeech) clearTimeout(sinceSpeech);
    if (finalText.trim()) sinceSpeech = setTimeout(sendWhatWasSaid, SETTLE_MS);

    // Meanwhile, a short pause is enough to start drafting an answer.
    draftSoon(ui.prompt.value, DRAFT_PAUSE_SPEECH);
  };

  rec.onerror = (ev) => {
    if (ev.error === 'no-speech' || ev.error === 'aborted') return;  // routine
    ui.hint.className = 'hint mono warn';
    const why = ev.error === 'not-allowed'
      ? 'Microphone blocked — allow it in the address bar, then press the mic again.'
      : `Speech recognition: ${ev.error}.`;
    ui.hint.textContent = why;
    if (inCall) callTrouble(why);
    if (ev.error === 'not-allowed') stopListening();
  };

  rec.onend = () => {
    listening = false;
    rec = null;
    // Chrome ends the session on its own every so often. Put it straight back
    // unless the user actually turned it off.
    if (handsFree) {
      scheduleRestart(250);
    } else {
      ui.mic.classList.remove('on');
      ui.prompt.classList.remove('listening');
      stopMeter();
    }
  };

  try {
    rec.start();
  } catch (err) {
    // InvalidStateError: the previous session had not released yet. Thrown
    // inside the restart timer this disappears silently and hands-free stops
    // for good, which is exactly the "works once then never again" symptom.
    rec = null;
    listening = false;
    if (handsFree) scheduleRestart(600);
  }
}

// One place that owns getting the recogniser going again, so a failed restart
// retries instead of ending the session.
function scheduleRestart(delay) {
  if (restartTimer) clearTimeout(restartTimer);
  restartTimer = setTimeout(() => {
    restartTimer = null;
    if (handsFree && !listening) startListening();
  }, delay);
}

// Two ways hands-free dies. Nothing listening at all — a thrown start(), or
// an onend that never came. And worse: a recogniser that still claims to be
// listening but has silently stopped delivering, which no amount of checking
// `listening` will catch. Track when it last said anything instead.
let lastMicEvent = 0;
const MIC_SILENCE_LIMIT = 12000;

setInterval(() => {
  if (!handsFree) return;

  if (!listening && !restartTimer) { scheduleRestart(200); return; }

  if (listening && lastMicEvent && Date.now() - lastMicEvent > MIC_SILENCE_LIMIT) {
    // Claims to be running, has not reported anything in a long time. Abort
    // it and let onend bring a fresh one up.
    lastMicEvent = Date.now();
    if (rec) { try { rec.abort(); } catch (e) { /* already gone */ } }
  }
}, 2000);

function stopListening() {
  handsFree = false;
  if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }
  if (sinceSpeech) { clearTimeout(sinceSpeech); sinceSpeech = null; }
  finalText = '';
  if (rec) { try { rec.stop(); } catch (e) { /* already stopping */ } }
  listening = false;
  ui.mic.classList.remove('on');
  ui.prompt.classList.remove('listening');
  ui.hint.className = 'hint mono';
  ui.hint.textContent = 'Press the mic to go hands-free, or type.';
  stopMeter();
}

ui.mic.addEventListener('click', () => (handsFree ? stopListening() : startListening()));

/* ------------------------------------------------------------ language */

// The recogniser has to be told the language before it hears a word: an
// en-US recogniser turns French into English-sounding nonsense, so detecting
// the language from its transcript can never work for speech. The picker is
// the reliable route; "auto" follows whatever the server detected from typed
// text, which is the case it can actually see.
const SPEECH_LANG = {
  en: 'en-US', es: 'es-ES', fr: 'fr-FR', de: 'de-DE',
  it: 'it-IT', pt: 'pt-PT', nl: 'nl-NL', ar: 'ar-SA',
};
let recLang = 'en';

function clearMishearing() {
  unsure = 0;
  nudged = false;
  ui.pickLang.classList.remove('wants-attention');
  if (ui.hint.className.includes('warn')) {
    ui.hint.className = 'hint mono';
    ui.hint.textContent = '';
  }
  if (inCall && typeof callTrouble === 'function') callTrouble('');
}

function setRecLang(code) {
  if (!SPEECH_LANG[code] || code === recLang) return;
  recLang = code;
  // Restart the live recogniser so the next utterance uses the new language.
  // onend brings it straight back up because hands-free is still on.
  if (rec) { try { rec.abort(); } catch (e) { /* already gone */ } }
}

function followLanguage(code) {
  if (ui.pickLang.value === 'auto') setRecLang(code);
}

/* ------------------------------------------------- mishearing the caller */

// Auto cannot detect the language from speech. SpeechRecognition.lang takes
// one tag, so the mic is always listening in a single language, and a caller
// speaking another one gets transcribed into nonsense that still looks like
// English — the server then detects English and never switches. The loop
// never closes.
//
// What we can see is that Chrome is unsure. A run of low-confidence finals
// means it is hearing speech it cannot place, which is exactly what another
// language sounds like to the wrong model. So stop failing silently and ask.
const UNSURE_BELOW = 0.6;
const UNSURE_RUN = 3;

let unsure = 0;
let nudged = false;

function heardClearly(confidence) {
  // Some browsers report 0 for every result; treat that as no opinion rather
  // than as certainty that we misheard.
  if (!confidence) return true;
  return confidence >= UNSURE_BELOW;
}

function noteConfidence(confidence) {
  if (heardClearly(confidence)) {
    unsure = 0;
    return;
  }
  unsure += 1;
  if (unsure < UNSURE_RUN || nudged) return;
  if (ui.pickLang.value !== 'auto') return;   // they already told us
  nudged = true;
  askWhichLanguage();
}

function askWhichLanguage() {
  const text = "I am not catching that clearly. If you are speaking another "
             + "language, pick it next to the mic — I can only listen in one "
             + "at a time.";
  ui.hint.className = 'hint mono warn';
  ui.hint.textContent = text;
  if (inCall && typeof callTrouble === 'function') callTrouble(text);
  // Draw the eye to the control that fixes it.
  ui.pickLang.classList.add('wants-attention');
  setTimeout(() => ui.pickLang.classList.remove('wants-attention'), 12000);
}

/* ----------------------------------------------------------- interpreter */

// Two people, one in English and one in the language picked (Arabic if the
// picker is on Auto). Each utterance is translated into the other language,
// and the recogniser switches to the language the next speaker will use.
function interpPair() {
  const v = ui.pickLang.value;
  return v && v !== 'auto' && v !== 'en' ? v : 'ar';
}

const LANG_NAME = { en: 'English', ar: 'Arabic', es: 'Spanish', fr: 'French',
  de: 'German', it: 'Italian', pt: 'Portuguese', nl: 'Dutch' };

function updateSwap() {
  ui.interpSwap.hidden = !ui.pickInterp.checked;
  ui.interpSwap.textContent = `Next speaker: ${LANG_NAME[recLang] || recLang}`;
}

ui.pickInterp.addEventListener('change', () => {
  if (ui.pickInterp.checked) {
    setRecLang('en');
    ui.hint.className = 'hint';
    ui.hint.textContent = `Interpreting between English and ${LANG_NAME[interpPair()]}. Take turns; the microphone switches language after each person.`;
  } else {
    setRecLang(ui.pickLang.value === 'auto' ? 'en' : ui.pickLang.value);
    ui.hint.textContent = 'Press the mic and talk. It sends when you stop.';
  }
  updateSwap();
});

ui.interpSwap.addEventListener('click', () => {
  setRecLang(recLang === 'en' ? interpPair() : 'en');
  updateSwap();
});

/* ----------------------------------------------------------- screen sharing */

// The shared window takes the camera's place: the same frames, the same
// "look before they finish talking", the same vision model, with a prompt
// that reads it as a screen and frames big enough to read.
let sharingScreen = false;

async function startScreen() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    ui.hint.className = 'hint warn';
    ui.hint.textContent = 'This browser cannot share a window. Chrome or Edge on a computer can.';
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 5, displaySurface: 'window' }, audio: false,
      selfBrowserSurface: 'exclude', surfaceSwitching: 'include',
    });
  } catch (err) {
    ui.hint.className = 'hint warn';
    ui.hint.textContent = err.name === 'NotAllowedError'
      ? 'Sharing was cancelled.'
      : `Could not share the screen: ${err.name}.`;
    return;
  }
  if (camStream) camStream.getTracks().forEach((t) => t.stop());
  camStream = stream;
  sharingScreen = true;
  camMirrored = false;
  ui.selfview.srcObject = stream;
  ui.selfview.classList.remove('mirrored');
  try { await ui.selfview.play(); } catch (e) { /* autoplay guard */ }
  stream.getVideoTracks()[0].addEventListener('ended', stopScreen);
  document.body.classList.add('sharing');
  ui.shareBar.hidden = false;
  ui.share.classList.add('on');
  ui.callShare.classList.add('on');
  ui.hint.className = 'hint';
  ui.hint.textContent = 'Sharing. Ask about what is on screen; a frame is read only when you ask.';
  updateStage();
  if (inCall) showCallVideo();
}

function stopScreen() {
  if (!sharingScreen) return;
  sharingScreen = false;
  if (camStream) camStream.getTracks().forEach((t) => t.stop());
  camStream = null;
  ui.selfview.srcObject = null;
  document.body.classList.remove('sharing');
  ui.shareBar.hidden = true;
  ui.share.classList.remove('on');
  ui.callShare.classList.remove('on');
  updateStage();
  if (inCall) showCallVideo();
}

const toggleScreen = () => (sharingScreen ? stopScreen() : startScreen());
ui.share.addEventListener('click', toggleScreen);
ui.callShare.addEventListener('click', toggleScreen);
ui.shareStop.addEventListener('click', stopScreen);

ui.pickLang.addEventListener('change', () => {
  remember('voiceloop.lang', ui.pickLang.value);
  setRecLang(ui.pickLang.value === 'auto' ? 'en' : ui.pickLang.value);
  // They answered the question, so stop asking it.
  clearMishearing();
});
{
  const saved = recall('voiceloop.lang');
  if (saved && [...ui.pickLang.options].some((o) => o.value === saved)) {
    ui.pickLang.value = saved;
    if (saved !== 'auto') recLang = saved;
  }
}

if (!SpeechCtor) {
  ui.mic.disabled = true;
  ui.mic.title = 'This browser has no speech recognition';
  ui.hint.textContent = 'Speech input needs Chrome, Edge, or Safari — typing works everywhere.';
}

/* ------------------------------------------------------------ cards */

// What the agent is talking about, on screen while it says it: the order it
// looked up, the policy it is quoting, the receipt for what it just did, the
// colleague who just joined. Built from data the agent already has, so they
// cost nothing.
const ACTION_TITLE = { replacement: 'Replacement sent', refund: 'Refund issued', return_label: 'Returns label emailed' };
const STATUS_SAYS = {
  'in transit': 'In transit', delivered: 'Delivered', refunded: 'Refunded', cancelled: 'Cancelled',
  preparing: 'Being prepared', returning: 'Return in progress',
};

function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

const money = (v) => (typeof v === 'number' ? `£${v.toFixed(2)}` : '');

function buildCard(c) {
  const card = h('article', `vcard vcard-${c.kind}`);
  if (c.kind === 'order') {
    const head = h('header', 'vc-head');
    head.append(h('span', 'vc-kicker', 'Order'), h('span', 'vc-big', c.number));
    const status = h('span', `vc-status s-${(c.status || '').replace(/\s+/g, '-')}`, STATUS_SAYS[c.status] || c.status);
    head.append(status);
    card.append(head);
    const items = (c.items || []).map((i) => `${i.qty} × ${i.name}`).join(', ');
    if (items) card.append(h('p', 'vc-line', items));
    const facts = h('dl', 'vc-facts');
    const fact = (k, v) => { if (v) { const d = h('div'); d.append(h('dt', null, k), h('dd', null, v)); facts.append(d); } };
    fact('Carrier', c.carrier ? `${c.carrier}${c.tracking ? `, ${c.tracking}` : ''}` : null);
    fact('Last scan', c.last_scan);
    fact('Promised', c.promised);
    fact('Delivered', c.delivered_on);
    fact('Paid', money(c.paid));
    card.append(facts);
    if (c.days_late) {
      card.append(h('p', `vc-flag ${c.counts_as_lost ? 'lost' : ''}`,
        `${c.days_late} working day${c.days_late === 1 ? '' : 's'} late${c.counts_as_lost ? ', which counts as lost' : ''}`));
    }
  } else if (c.kind === 'policy') {
    card.append(h('span', 'vc-kicker', 'From the policy'));
    c.excerpts.forEach((x) => {
      card.append(h('p', 'vc-title', x.heading));
      card.append(h('p', 'vc-line', x.text));
    });
  } else if (c.kind === 'action') {
    card.append(h('span', 'vc-kicker', 'Done'));
    card.append(h('p', 'vc-title', ACTION_TITLE[c.action] || 'Done'));
    card.append(h('p', 'vc-ref', c.ref));
    const bits = [`Order ${c.order}`, c.items];
    if (c.action === 'refund' && typeof c.amount === 'number') bits.push(`${money(c.amount)} to the original payment method`);
    if (c.action === 'return_label' && c.email) bits.push(`emailed to ${c.email}`);
    card.append(h('p', 'vc-line', bits.filter(Boolean).join('. ')));
  } else if (c.kind === 'handoff') {
    const row = h('div', 'vc-person');
    row.append(h('span', 'vc-avatar', (c.name || '?').slice(0, 1)));
    const who = h('div');
    who.append(h('p', 'vc-title', `${c.name} joined`), h('p', 'vc-line', `${c.label[0].toUpperCase()}${c.label.slice(1)}. Has everything said so far.`));
    row.append(who);
    card.append(row);
  }
  return card;
}

function addCard(turn, data) {
  turn.cards.push(data);
  if (turn.id === liveTurn || data.kind === 'handoff') {
    const card = buildCard(data);
    ui.visuals.append(card);
    while (ui.visuals.children.length > 3) ui.visuals.firstElementChild.remove();
  }
}

/* ------------------------------------------------------------ cost */

let humanCost = null;

function usd(v) {
  if (typeof v !== 'number') return '—';
  if (v === 0) return '$0';
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(v < 1 ? 3 : 2)}`;
}

const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));

function showCost(turnCost, callCost) {
  if (turnCost) ui.costTurn.textContent = usd(turnCost.total_usd);
  if (callCost) {
    ui.costCall.textContent = usd(callCost.total_usd);
    ui.costTokens.textContent = `${compact(callCost.prompt_tokens)} in, ${compact(callCost.completion_tokens)} out`;
    ui.costChars.textContent = `${compact(callCost.characters)} characters`;
    const human = typeof callCost.human_usd === 'number' ? callCost.human_usd : humanCost;
    if (human && callCost.total_usd > 0) {
      ui.costHuman.hidden = false;
      ui.costHuman.textContent = `A human-handled call: about ${usd(human)}. This one so far: ${Math.round(human / callCost.total_usd)}× cheaper.`;
    }
  }
}

/* ------------------------------------------------------------ replay */

// Every reply's audio is still in the browser, so any of them can be heard
// again with its transcript, cards and timeline, or the whole call in order.
let replayingAll = false;

function replayTurn(id) {
  const t = turns.get(id);
  if (!t || !t.chunks.length) return Promise.resolve();
  stopAudio();
  ensureAudio();
  t.replaying = true;
  playingId = id;
  t.playStart = null;
  let seconds = 0;
  t.chunks.forEach((c) => {
    const at = speak(c);
    if (t.playStart === null) t.playStart = at;
    seconds += c.length / SAMPLE_RATE;
  });
  document.querySelectorAll('.msg.replaying').forEach((e) => e.classList.remove('replaying'));
  if (t.node) {
    t.node.row.classList.add('replaying');
    t.node.row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  ui.visuals.innerHTML = '';
  showLive(id, t.speaker && !t.prompt ? `${t.speaker.name} joined` : t.prompt, t.node ? t.node.body.textContent : '', true);
  t.cards.forEach((c) => ui.visuals.append(buildCard(c)));
  select(id);
  startDrawing();
  updateStopButton();
  return new Promise((resolve) => {
    setTimeout(() => {
      if (t.node) t.node.row.classList.remove('replaying');
      t.replaying = false;
      resolve();
    }, seconds * 1000 + 250);
  });
}

function addReplay(turn) {
  if (!turn.node || !turn.chunks.length || turn.node.row.querySelector('.replay')) return;
  const b = h('button', 'replay', 'Replay');
  b.type = 'button';
  b.addEventListener('click', () => { replayingAll = false; replayTurn(turn.id); });
  turn.node.row.append(b);
  ui.replayCall.disabled = false;
}

ui.replayCall.addEventListener('click', async () => {
  if (replayingAll) {                   // pressed again: stop
    replayingAll = false;
    stopAudio();
    ui.replayCall.textContent = 'Replay call';
    return;
  }
  replayingAll = true;
  ui.replayCall.textContent = 'Stop replay';
  const ids = [...turns.values()].filter((t) => t.chunks.length).sort((a, b) => a.id - b.id).map((t) => t.id);
  for (const id of ids) {
    if (!replayingAll) break;
    await replayTurn(id);
  }
  replayingAll = false;
  ui.replayCall.textContent = 'Replay call';
});

/* ------------------------------------------------------------ voice field */

// The centre of the page is the conversation itself: rings that bend with
// what is actually being heard. White and driven by the caller's mic while
// listening, orange and circling while SambaNova works, white again and
// driven by Cartesia's audio while it speaks.

// Set by the server when it repairs a transcript, consumed by the next
// turn_start so the correction appears under what the caller said.
let reheard = null;

// Whether the voice is working at all. Set from the ready frame and
// cleared if synthesis gives out mid-call.
let audioAvailable = true;

let liveTurn = null;

function showLive(id, prompt, text, keepCards) {
  if (liveTurn !== id && !keepCards) ui.visuals.innerHTML = '';
  liveTurn = id;
  ui.intro.hidden = true;
  ui.live.hidden = false;
  ui.liveYou.textContent = prompt;
  ui.liveThem.textContent = text;
}

function phase() {
  if (sources.length || playingId !== null) return 'speaking';
  if (running().length) return 'thinking';
  if (listening) return 'listening';
  return 'idle';
}

const PHASE_LABEL = {
  idle: 'Ready', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking',
};
const INTERP_LABEL = {
  idle: 'Interpreter', listening: 'Listening', thinking: 'Translating', speaking: 'Speaking',
};

const REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
const BANDS = 8;
const level = new Float32Array(BANDS);
let energy = 0;
let spin = 0;
let lastPhase = null;
let palette = null;
let freqBuf = null;

function readPalette() {
  palette = {
    paper: css('--paper'), heat: css('--llm'), mint: css('--tts'), deep: css('--deep'),
  };
}

function hear(analyser) {
  if (!analyser) {
    for (let i = 0; i < BANDS; i++) level[i] *= 0.9;
    energy *= 0.9;
    return;
  }
  if (!freqBuf || freqBuf.length !== analyser.frequencyBinCount) {
    freqBuf = new Uint8Array(analyser.frequencyBinCount);
  }
  analyser.getByteFrequencyData(freqBuf);
  // Speech lives in the bottom third of the spectrum; spread it log-ish.
  const top = Math.floor(freqBuf.length * 0.35);
  let sum = 0;
  for (let b = 0; b < BANDS; b++) {
    const from = Math.floor(top * Math.pow(b / BANDS, 1.6));
    const to = Math.max(from + 1, Math.floor(top * Math.pow((b + 1) / BANDS, 1.6)));
    let v = 0;
    for (let i = from; i < to; i++) v += freqBuf[i];
    v = v / (to - from) / 255;
    level[b] = level[b] * 0.65 + v * 0.35;
    sum += level[b];
  }
  energy = energy * 0.8 + (sum / BANDS) * 0.2;
}

function drawOrb(now) {
  requestAnimationFrame(drawOrb);
  const c = ui.orb;
  if (!c || !c.offsetParent) return;          // hidden during a call
  if (!palette) readPalette();

  const p = phase();
  const label = `${p}${ui.pickInterp.checked ? ':i' : ''}`;
  if (label !== lastPhase) {
    lastPhase = label;
    ui.orbState.textContent = (ui.pickInterp.checked ? INTERP_LABEL : PHASE_LABEL)[p];
    ui.orbState.dataset.phase = p;
  }

  const source = p === 'speaking' ? outAnalyser : (p === 'listening' ? inAnalyser : null);
  if (source) {
    hear(source);
  } else if (!REDUCED) {
    // Nothing to listen to: let the field drift slowly, so a silent page
    // still reads as alive, and churn a little harder while the model works.
    const tt = now / 1000;
    const base = p === 'thinking' ? 0.16 : 0.07;
    for (let b = 0; b < BANDS; b++) {
      const target = base * (0.6 + 0.4 * Math.sin(tt * (0.5 + b * 0.13) + b * 1.7));
      level[b] = level[b] * 0.94 + target * 0.06;
    }
    energy = energy * 0.94 + base * 0.06;
  }

  const box = c.parentElement.getBoundingClientRect();
  const size = Math.max(160, Math.min(box.width, box.height));
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(size * dpr)) {
    c.width = c.height = Math.round(size * dpr);
    c.style.width = c.style.height = `${size}px`;
  }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, size, size);

  const t = REDUCED ? 0 : now / 1000;
  const cx = size / 2, cy = size / 2, R = size / 2;
  spin += p === 'thinking' ? 0.018 : 0.002;

  // A soft core whose colour says who is talking.
  const core = p === 'thinking' ? palette.heat : (p === 'speaking' ? palette.mint : palette.paper);
  const glow = g.createRadialGradient(cx, cy, 0, cx, cy, R * 0.62);
  glow.addColorStop(0, core);
  glow.addColorStop(1, 'transparent');
  g.globalAlpha = 0.08 + energy * 0.35 + (p === 'thinking' ? 0.08 : 0);
  g.fillStyle = glow;
  g.beginPath(); g.arc(cx, cy, R * 0.62, 0, Math.PI * 2); g.fill();
  g.globalAlpha = 1;

  const RINGS = 7;
  const STEPS = 200;
  for (let r = 0; r < RINGS; r++) {
    const base = R * (0.30 + r * 0.085);
    const amp = R * (0.012 + 0.16 * energy) * (1 - r / (RINGS * 1.6));
    g.beginPath();
    for (let s = 0; s <= STEPS; s++) {
      const a = (s / STEPS) * Math.PI * 2;
      let d = Math.sin(a * 3 + t * 0.6 + r) * R * 0.006;      // always alive, barely
      for (let b = 0; b < BANDS; b++) {
        d += level[b] * Math.sin(a * (b + 2) + t * (0.8 + b * 0.35) + r * 0.9) * amp / 1.6;
      }
      const rad = base + d;
      const x = cx + Math.cos(a + spin * (r % 2 ? 1 : -1)) * rad;
      const y = cy + Math.sin(a + spin * (r % 2 ? 1 : -1)) * rad;
      if (s === 0) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.closePath();

    const fade = 1 - r / RINGS;
    if (p === 'thinking') {
      const circ = Math.PI * 2 * base;
      g.setLineDash([circ * (0.18 + 0.05 * r), circ * 0.06]);
      g.lineDashOffset = -(now / 1000) * 90 * (r % 2 ? 1 : -1);
      g.strokeStyle = palette.heat;
      g.globalAlpha = 0.25 + 0.65 * fade;
    } else {
      g.setLineDash([]);
      g.strokeStyle = p === 'speaking' && r === 0 ? palette.mint : palette.paper;
      g.globalAlpha = (p === 'idle' ? 0.16 : 0.3) + (p === 'idle' ? 0.34 : 0.6) * fade;
    }
    g.lineWidth = r === 0 ? 2 : 1.15;
    g.stroke();
  }
  g.globalAlpha = 1;
  g.setLineDash([]);
}

requestAnimationFrame(drawOrb);

ui.capYou.dir = 'auto';
ui.capThem.dir = 'auto';
window.addEventListener('resize', drawTimeline);
ui.send.disabled = true;
ui.mic.disabled = true;
drawTimeline();
connect();
