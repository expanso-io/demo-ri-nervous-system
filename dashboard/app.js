"use strict";
/* Stage dashboard driven by /api/stream. One prompt line for the room, then
   four columns read left to right: the mic and camera, three model nodes,
   the fly-drone rule table, and the drone in a tall flight arena. Nerve
   lanes between the columns carry one impulse per real message. */

const SAMPLES = 40;

const FRESH_S = 3;

const ACT_STICKY_S = 4.8;

const CLAP_PILL_S = 2.5;

const NOMINAL = { clap: 4, word: 4, hands: 5 };

const NODES = ["hear-clap", "hear-commands", "see-hands"];

const NODE_OF = { clap: "hear-clap", word: "hear-commands", hands: "see-hands" };

const SENSE_OF = { "hear-clap": "clap", "hear-commands": "word", "see-hands": "hands" };

const EMPTY = { clap: "no clap", word: "no word", hands: "no count" };

const CMD_TYPES = ["land", "stop", "move", "wake"];

const BEAT_TITLE = { reveal: "Everything that travelled between the devices." };

const COLORS = {
  ember: "#d95d16",
  dim: "#78695a",
  ok: "#0d7a5f",
  err: "#c92a35",
  up: "#1d63b8",
  warn: "#b4690e",
  white: "#38bdf8",
  ink: "#1c1917",
  idle: "#8c8277",
};

const LED = {
  off: COLORS.idle,
  white: COLORS.white,
  blue: COLORS.up,
  amber: COLORS.warn,
  red: COLORS.err,
  green: COLORS.ok,
};

const ACT_COLOR = {
  land: COLORS.err,
  up: COLORS.up,
  down: COLORS.warn,
  wake: COLORS.ember,
  stop: COLORS.ink,
  hold: COLORS.dim,
};

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const SPEED = reduced ? 0.45 : 1;

const $ = (id) => document.getElementById(id);

/* --------------------------------------------------------------- geometry */

function curve(from, to) {
  const dx = to.x - from.x;
  const c1 = { x: from.x + dx * 0.5, y: from.y };
  const c2 = { x: to.x - dx * 0.5, y: to.y };
  const pts = [];

  for (let i = 0; i <= SAMPLES; i++) {
    const t = i / SAMPLES;
    const u = 1 - t;
    pts.push({
      x: u * u * u * from.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * to.x,
      y: u * u * u * from.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * to.y,
    });
  }

  return polyline(pts);
}

function polyline(points) {
  const lens = [0];

  for (let i = 1; i < points.length; i++) {
    lens.push(lens[i - 1] + Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y));
  }

  return { points, lens, total: lens[lens.length - 1] || 1 };
}

function at(path, t) {
  const d = Math.max(0, Math.min(1, t)) * path.total;
  let i = 1;

  while (i < path.lens.length - 1 && path.lens[i] < d) i++;
  const a = path.points[i - 1];
  const b = path.points[i];
  const seg = path.lens[i] - path.lens[i - 1] || 1;
  const f = Math.min(1, Math.max(0, (d - path.lens[i - 1]) / seg));

  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
}

const stageEl = $("stage");

const flowCanvas = $("flow");

const ctx = flowCanvas.getContext("2d");

let stageW = 1440;

let stageH = 680;

let LANES = {};

const BOARD_W = 1920;

const BOARD_H = 1080;

let boardScale = 1;

/* Scale the whole 1920 x 1080 board to the window, centred. */
function fitBoard() {
  const k = Math.min(window.innerWidth / BOARD_W, window.innerHeight / BOARD_H);
  const dx = (window.innerWidth - BOARD_W * k) / 2;
  const dy = (window.innerHeight - BOARD_H * k) / 2;
  boardScale = k;
  document.body.style.transform = `translate(${dx}px, ${dy}px) scale(${k})`;
}

/* Element box in the board's own pixels, whatever the board is scaled to. */
function relRect(el, origin) {
  const r = el.getBoundingClientRect();
  const k = boardScale;

  return {
    left: (r.left - origin.left) / k,
    right: (r.right - origin.left) / k,
    top: (r.top - origin.top) / k,
    bottom: (r.bottom - origin.top) / k,
    width: r.width / k,
    height: r.height / k,
    midY: (r.top - origin.top + r.height * 0.5) / k,
  };
}

function syncLayout() {
  fitBoard();
  const rect = stageEl.getBoundingClientRect();
  stageW = Math.max(1, stageEl.offsetWidth);
  stageH = Math.max(1, stageEl.offsetHeight);
  const dpr = (window.devicePixelRatio || 1) * Math.max(1, boardScale);
  const targetW = Math.round(stageW * dpr);
  const targetH = Math.round(stageH * dpr);

  if (flowCanvas.width !== targetW || flowCanvas.height !== targetH) {
    flowCanvas.width = targetW;
    flowCanvas.height = targetH;
  }

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const mic = relRect($("src-word"), rect);
  const cam = relRect($("src-hands"), rect);
  const clap = relRect($("node-hear-clap"), rect);
  const word = relRect($("node-hear-commands"), rect);
  const hands = relRect($("node-see-hands"), rect);
  const decide = relRect($("node-fly-drone"), rect);

  const nextLanes = {
    "raw-clap": curve({ x: mic.right, y: mic.top + mic.height * 0.34 }, { x: clap.left, y: clap.midY }),
    "raw-word": curve({ x: mic.right, y: mic.top + mic.height * 0.66 }, { x: word.left, y: word.midY }),
    "raw-hands": curve({ x: cam.right, y: cam.midY }, { x: hands.left, y: hands.midY }),
    "imp-clap": curve({ x: clap.right, y: clap.midY }, { x: decide.left, y: decide.top + decide.height * 0.34 }),
    "imp-word": curve({ x: word.right, y: word.midY }, { x: decide.left, y: decide.midY }),
    "imp-hands": curve({ x: hands.right, y: hands.midY }, { x: decide.left, y: decide.top + decide.height * 0.66 }),
  };

  const droneCard = relRect($("drone"), rect);
  const arena = relRect($("arena"), rect);

  for (const cmd of CMD_TYPES) {
    const ruleEl = document.querySelector(`#rules li[data-rule="${cmd}"]`);

    if (ruleEl) {
      nextLanes[`act-${cmd}`] = curve(
        { x: decide.right, y: relRect(ruleEl, rect).midY },
        { x: droneCard.left, y: arena.midY },
      );
    }
  }

  LANES = nextLanes;

  const waveMic = $("wave-mic");
  const wTarget = Math.max(120, Math.round(waveMic.clientWidth * dpr));
  const hTarget = Math.max(40, Math.round(waveMic.clientHeight * dpr));

  if (waveMic.width !== wTarget || waveMic.height !== hTarget) {
    waveMic.width = wTarget;
    waveMic.height = hTarget;
  }
}

syncLayout();

new ResizeObserver(syncLayout).observe(stageEl);

window.addEventListener("resize", syncLayout);

/* ------------------------------------------------------------------ state */

const ui = {
  state: null,
  stateAt: 0,
  lastPulse: null,
  lastWindows: {},
  windowRate: {},
  lastScenePrompt: null,
  lastRealAct: null,
  openSlot: null,
  clickerArmed: false,
  revealSig: "",
  stageCamAttached: false,
  micNotice: { text: "", until: 0 },
  pipePendingUntil: 0,
  cmdCounts: { land: 0, stop: 0, move: 0, wake: 0 },
  cmdLast: {},
  seenCmdKeys: new Set(),
};

let state = null;

function payloadBytes(item) {
  return item ? (item.bytes ?? item.raw?.length ?? 0) : 0;
}

function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch((err) => { console.warn("postJson failed", path, err); });
}

/* --------------------------------------------------------------- waveform */

const micCtx = $("wave-mic").getContext("2d");

function drawWaveform(ctx2d, bars, level, color, now) {
  const width = ctx2d.canvas.width;
  const height = ctx2d.canvas.height;
  ctx2d.clearRect(0, 0, width, height);
  const mid = height / 2;

  ctx2d.strokeStyle = "rgba(87, 80, 73, 0.25)";
  ctx2d.lineWidth = 1;
  ctx2d.beginPath();
  ctx2d.moveTo(8, mid);
  ctx2d.lineTo(width - 8, mid);
  ctx2d.stroke();

  const count = bars.length || 32;
  const padX = width * 0.03;
  const span = (width - padX * 2) / count;
  const barW = Math.max(3, span * 0.6);
  const phase = now * 0.004;

  ctx2d.fillStyle = color;

  for (let i = 0; i < count; i++) {
    const rawVal = (bars[i] ?? 0) / 99;
    const ripple = 0.08 + 0.08 * Math.sin(phase + i * 0.45);
    const amp = Math.min(1, Math.max(ripple, rawVal, level * (0.65 + 0.35 * Math.abs(Math.sin(phase + i * 0.6)))));
    const h = Math.max(height * 0.08, amp * height * 0.86);
    const x = padX + i * span + (span - barW) / 2;
    ctx2d.globalAlpha = 0.4 + 0.6 * amp;
    ctx2d.beginPath();
    ctx2d.roundRect(x, mid - h / 2, barW, h, barW / 2);
    ctx2d.fill();
  }

  ctx2d.globalAlpha = 1;
}

/* -------------------------------------------------------------- flow lines */

const particles = [];

const rings = [];

const heat = {};

const carry = {};

function spawn(lane, opts) {
  if (!LANES[lane]) return;

  particles.push({
    lane,
    t: 0,
    speed: opts.speed,
    color: opts.color,
    size: opts.size,
    alpha: opts.alpha,
    end: opts.end ?? 1,
    trail: opts.trail ?? false,
    ring: opts.ring ?? false,
    fadeFrom: opts.fadeFrom ?? 1,
  });

  if (opts.ring) heat[lane] = { level: 1, color: opts.color };
}

function emitRaw(dt) {
  for (const sense of Object.keys(NODE_OF)) {
    const node = NODE_OF[sense];
    const tele = state?.devices?.[node];
    const cut = tele?.link === "cut";
    const live = state?.nodes?.[node]?.live;
    const perSecond = (state ? (ui.windowRate[node] ?? NOMINAL[sense]) : NOMINAL[sense]) * 4.5;
    carry[sense] = (carry[sense] ?? 0) + perSecond * dt;

    while (carry[sense] >= 1) {
      carry[sense] -= 1;
      spawn(`raw-${sense}`, {
        speed: 0.72 + Math.random() * 0.22,
        color: cut ? COLORS.err : COLORS.idle,
        size: 2.2 + Math.random() * 1.0,
        alpha: 0.55,
        end: cut ? 0.38 : live ? 1 : 0.88,
        fadeFrom: cut ? 0.14 : live ? 0.92 : 0.62,
      });
    }
  }
}

function arrowHead(path) {
  const pts = path.points;

  if (pts.length < 2) return;
  const end = pts[pts.length - 1];
  const prev = pts[pts.length - 2];
  const ang = Math.atan2(end.y - prev.y, end.x - prev.x);
  const len = 11;

  ctx.beginPath();
  ctx.moveTo(end.x, end.y);
  ctx.lineTo(end.x - len * Math.cos(ang - 0.48), end.y - len * Math.sin(ang - 0.48));
  ctx.lineTo(end.x - len * Math.cos(ang + 0.48), end.y - len * Math.sin(ang + 0.48));
  ctx.closePath();
  ctx.fill();
}

function trace(path) {
  ctx.beginPath();
  path.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.stroke();
}

/* Raw lanes are dotted: grains that stop at the model. Decision and act
   lanes are solid nerves that light up in the colour of the last impulse. */
function guides(dt) {
  ctx.lineCap = "round";

  for (const [name, path] of Object.entries(LANES)) {
    const raw = name.startsWith("raw-");
    ctx.globalAlpha = 1;
    ctx.lineWidth = raw ? 2 : 4;
    ctx.setLineDash(raw ? [2, 9] : []);
    ctx.strokeStyle = raw ? "rgba(87, 80, 73, 0.5)" : "rgba(87, 80, 73, 0.2)";
    ctx.fillStyle = "rgba(87, 80, 73, 0.45)";
    trace(path);
    ctx.setLineDash([]);

    const hot = heat[name];

    if (hot && hot.level > 0.02) {
      ctx.globalAlpha = Math.min(1, hot.level);
      ctx.lineWidth = 4 + 4 * hot.level;
      ctx.strokeStyle = hot.color;
      ctx.fillStyle = hot.color;
      trace(path);
      hot.level -= dt * 0.55;
    }

    if (!raw) arrowHead(path);
  }

  ctx.globalAlpha = 1;
}

let last = performance.now();

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000) * SPEED;
  last = now;
  ctx.clearRect(0, 0, stageW, stageH);
  guides(dt);
  emitRaw(dt);

  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i];
    const path = LANES[p.lane];

    if (!path) { particles.splice(i, 1); continue; }

    p.t += (p.speed * dt * 160) / path.total;

    if (p.t >= p.end) {
      if (p.ring) rings.push({ ...at(path, 1), color: p.color, t: 0 });
      particles.splice(i, 1);
      continue;
    }

    const pt = at(path, p.t);
    const fade = p.t > p.fadeFrom ? 1 - (p.t - p.fadeFrom) / Math.max(0.01, p.end - p.fadeFrom) : 1;
    const enter = Math.min(1, p.t * 6);
    const alpha = p.alpha * fade * enter;
    ctx.fillStyle = p.color;

    if (p.trail) {
      for (let k = 7; k >= 1; k--) {
        const tp = at(path, Math.max(0, p.t - k * 0.035));
        ctx.globalAlpha = alpha * (0.4 * (1 - k / 8));
        ctx.beginPath();
        ctx.arc(tp.x, tp.y, p.size * (1 - k * 0.09), 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.globalAlpha = alpha * 0.22;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, p.size * 2.3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, p.size, 0, Math.PI * 2);
    ctx.fill();
  }

  for (let i = rings.length - 1; i >= 0; i--) {
    const r = rings[i];
    r.t += dt / 0.7;

    if (r.t >= 1) { rings.splice(i, 1); continue; }

    ctx.globalAlpha = 0.8 * (1 - r.t);
    ctx.strokeStyle = r.color;
    ctx.lineWidth = 4 * (1 - r.t) + 1;
    ctx.beginPath();
    ctx.arc(r.x, r.y, 10 + 34 * r.t, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.globalAlpha = 1;
  render(now);
  requestAnimationFrame(frame);
}

/* ----------------------------------------------------------------- render */

function age(item, now) {
  if (!item || !state) return Infinity;

  return state.t + (now - ui.stateAt) / 1000 - item.seen_at;
}

function ageText(seconds) {
  if (!Number.isFinite(seconds)) return "";

  return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
}

function label(sense, m) {
  if (!m) return null;

  if (sense === "clap") return `clap ${m.confidence.toFixed(2)}`;

  if (sense === "word") return `${m.value} ${m.confidence.toFixed(2)}`;

  return `hands ${Math.round(m.share * 100)}%`;
}

const PHASE = {
  dark: "dark",
  taking_off: "taking off",
  hovering: "hovering",
  climbing: "climbing",
  descending: "descending",
  landing: "landing",
  parked: "parked",
};

function mergeWave(a, b) {
  const len = Math.max(a?.length ?? 0, b?.length ?? 0, 32);
  const out = new Array(len).fill(0);

  for (let i = 0; i < len; i++) {
    out[i] = Math.max(a?.[i] ?? 0, b?.[i] ?? 0);
  }

  return out;
}

function renderMicSwitch(clapTele, wordTele, now) {
  const live = wordTele?.input === "live" || clapTele?.input === "live";
  const raw = String(wordTele?.mic || clapTele?.mic || "").toLowerCase();
  const isDji = live && raw.includes("dji");
  const isMac = live && (raw.includes("macbook") || raw.includes("built-in") || raw.includes("internal"));
  const macBtn = $("mic-mac");
  const djiBtn = $("mic-dji");
  macBtn.classList.toggle("active", isMac);
  macBtn.setAttribute("aria-pressed", String(isMac));
  djiBtn.classList.toggle("active", isDji);
  djiBtn.setAttribute("aria-pressed", String(isDji));

  if (ui.micNotice.until > now) return ui.micNotice.text;

  if (live && raw && !isDji && !isMac) return raw.slice(0, 14);

  if (!live) return "";

  const bytesSeen = Math.max(clapTele?.captured_bytes ?? 0, wordTele?.captured_bytes ?? 0);
  const micPeak = Math.max(clapTele?.level ?? 0, wordTele?.level ?? 0, ...(clapTele?.wave || [0]), ...(wordTele?.wave || [0]));

  if (bytesSeen > 120000 && micPeak === 0) return "mic muted / no access";

  return isDji ? "dji live" : "mac live";
}

function renderSenses(now) {
  const clapTele = state.devices["hear-clap"];
  const wordTele = state.devices["hear-commands"];
  const handsTele = state.devices["see-hands"];

  const micCut = clapTele?.link === "cut" && wordTele?.link === "cut";
  const micPartialCut = clapTele?.link === "cut" || wordTele?.link === "cut";
  $("src-word").dataset.link = micCut ? "cut" : "ok";
  const micStatus = renderMicSwitch(clapTele, wordTele, now);

  const micLocal = Math.max(wordTele?.windows ?? 0, (wordTele?.sent ?? 0) + (wordTele?.dropped ?? 0), clapTele?.windows ?? 0, (clapTele?.sent ?? 0) + (clapTele?.dropped ?? 0));

  $("raw-word").textContent = String(micLocal);
  $("cap-word").textContent = micPartialCut ? "link cut" : micStatus;

  const clapAge = age(state.latest.clap, now);
  const clapHot = Boolean(state.nodes["hear-clap"].live && clapAge < CLAP_PILL_S);
  $("clap-pill").classList.toggle("hot", clapHot);

  const wordAge = age(state.latest.word, now);
  const activeWord = state.nodes["hear-commands"].live && wordAge < ACT_STICKY_S ? state.latest.word?.value : null;

  for (const pill of document.querySelectorAll("#word-pills .wpill")) {
    pill.classList.toggle("active", pill.dataset.w === activeWord);
  }

  const micLevel = Math.max(clapTele?.level ?? 0, wordTele?.level ?? 0);
  const micColor = activeWord === "up" ? COLORS.up : activeWord === "down" ? COLORS.warn : clapHot ? COLORS.ember : COLORS.ink;

  drawWaveform(micCtx, mergeWave(clapTele?.wave, wordTele?.wave), micLevel, micColor, now);

  $("src-hands").dataset.link = handsTele?.link ?? "ok";
  const camLocal = Math.max(handsTele?.windows ?? 0, (handsTele?.sent ?? 0) + (handsTele?.dropped ?? 0));
  $("raw-hands").textContent = String(camLocal);
  $("cap-hands").textContent = handsTele?.link === "cut" ? "link cut" : "watching";

  const mHands = state.latest.hands;
  const handsLive = Boolean(state.nodes["see-hands"].live && mHands);
  const tally = $("hands-tally");
  const voteFill = $("vote-fill");

  if (handsLive) {
    const up = mHands.up ?? 0;
    const seen = mHands.seen ?? 0;
    const share = mHands.share ?? 0;
    const pct = Math.max(0, Math.min(100, Math.round(share * 100)));
    const majority = seen >= 10 && share > 0.5;
    const waiting = !majority && seen >= 10 && share >= 0.2;
    voteFill.style.width = `${pct}%`;
    voteFill.classList.toggle("majority", majority);
    voteFill.classList.toggle("waiting", waiting);
    tally.textContent = `${up} of ${seen}`;
    tally.classList.toggle("majority", majority);
    tally.classList.toggle("waiting", waiting);
  } else {
    voteFill.style.width = "0%";
    voteFill.classList.remove("majority", "waiting");
    tally.textContent = "--";
    tally.classList.remove("majority", "waiting");
  }
}

function cmdKeyOf(item) {
  if (!item || item.act === "hold") return null;

  if (item.rule && CMD_TYPES.includes(item.rule)) return item.rule;

  if (item.act === "up" || item.act === "down") return "move";

  return CMD_TYPES.includes(item.act) ? item.act : null;
}

function trackCommands(snapshot) {
  const acts = snapshot.acts || [];

  if (!acts.length && !snapshot.nodes?.["fly-drone"]?.sent) {
    ui.cmdCounts = { land: 0, stop: 0, move: 0, wake: 0 };
    ui.cmdLast = {};
    ui.seenCmdKeys.clear();

    return;
  }

  for (const item of acts) {
    const cmd = cmdKeyOf(item);

    if (!cmd) continue;
    const sig = `${cmd}:${item.act}:${item.t}:${item.because}`;

    if (!ui.seenCmdKeys.has(sig)) {
      ui.seenCmdKeys.add(sig);
      ui.cmdCounts[cmd] = (ui.cmdCounts[cmd] ?? 0) + 1;
    }

    ui.cmdLast[cmd] = item;
  }
}

function renderCommands(now) {
  const act = state.act;

  if (act && act.act !== "hold") ui.lastRealAct = act;
  const recent = ui.lastRealAct && age(ui.lastRealAct, now) < ACT_STICKY_S ? ui.lastRealAct : null;
  const activeCmd = recent ? cmdKeyOf(recent) : null;

  for (const cmd of CMD_TYPES) {
    const box = $(`cmd-${cmd}`);
    const lastItem = ui.cmdLast[cmd];
    const isActive = activeCmd === cmd;
    box.dataset.active = String(isActive);

    if (cmd === "move") box.dataset.dir = recent?.act === "down" ? "down" : "up";

    const ago = lastItem ? ageText(age(lastItem, now)) : "";
    $(`cs-${cmd}`).textContent = isActive
      ? (cmd === "move" ? recent.act : "now")
      : ago ? `${ago} ago` : "idle";
  }
}

function render(now) {
  if (!state) return;
  const s = state;
  renderSenses(now);

  for (const sense of Object.keys(NODE_OF)) {
    const node = NODE_OF[sense];
    const tele = s.devices[node];
    const n = s.nodes[node];
    const cut = tele?.link === "cut";
    const liveState = cut ? "cut" : n.live ? "on" : "off";
    const el = $(`node-${node}`);
    el.dataset.live = liveState;
    el.dataset.state = liveState;
    $(`n-${node}`).textContent = n.sent;
    $(`s-${node}`).textContent = cut ? "cut" : n.live ? "running" : "off";

    const m = s.latest[sense];
    const slot = document.querySelector(`.slot[data-slot="${sense}"]`);
    const a = age(m, now);
    const text = label(sense, m);
    const lastEl = $(`l-${node}`);
    lastEl.textContent = text ?? "nothing sent";
    lastEl.classList.toggle("none", !text);
    lastEl.classList.toggle("fresh", sense !== "hands" && a < FRESH_S);

    if (sense === "hands") {
      const quorum = Boolean(m && n.live && m.seen >= 10);
      lastEl.classList.toggle("majority", quorum && m.share > 0.5);
      lastEl.classList.toggle("waiting", quorum && m.share >= 0.2 && m.share <= 0.5);
    }

    const k = $(`m-${sense}`);
    k.textContent = text ?? EMPTY[sense];
    k.classList.toggle("none", !text);
    $(`a-${sense}`).textContent = m ? `${node} · ${bytes(payloadBytes(m))} · ${ageText(a)}` : node;
    slot.classList.toggle("fresh", a < FRESH_S);
    slot.classList.toggle("stale", Number.isFinite(a) && a >= FRESH_S);
  }

  const fly = s.nodes["fly-drone"];
  const flyEl = $("node-fly-drone");
  flyEl.dataset.live = fly.live ? "on" : "off";
  flyEl.dataset.state = fly.live ? "on" : "off";
  $("n-fly-drone").textContent = fly.sent;

  const running = Object.values(s.nodes).filter((n) => n.live).length;
  const pipeEl = $("pipeline-status");
  pipeEl.textContent = running ? `${running}/4 running` : "off";
  pipeEl.classList.toggle("on", running > 0);
  pipeEl.dataset.reviewPipeline = running ? "on" : "off";

  const pipeBtn = $("btn-pipelines");

  if (pipeBtn && (!ui.pipePendingUntil || performance.now() > ui.pipePendingUntil)) {
    pipeBtn.textContent = running === 4 ? "pause" : "start pipelines";
    pipeBtn.classList.toggle("on", running === 4);
  }

  renderDecide(now);
  renderHistory(now);
  renderCommands(now);
  renderDrone();

  const scene = s.scene || {};

  if (document.body.dataset.beat !== scene.beat) document.body.dataset.beat = scene.beat || "hook";

  const promptText = scene.prompt || BEAT_TITLE[scene.beat] || "";

  if (promptText !== ui.lastScenePrompt) swapPrompt(promptText);
}

function renderDecide(now) {
  const act = state.act;

  if (!act) ui.lastRealAct = null;

  if (act && act.act !== "hold") ui.lastRealAct = act;
  const recent = ui.lastRealAct && age(ui.lastRealAct, now) < ACT_STICKY_S ? ui.lastRealAct : null;
  const shown = recent || act;
  const because = $("because");

  if (!act) {
    because.textContent = state.nodes["fly-drone"].live ? "ready" : "off";
  } else if (shown) {
    because.textContent = shown.act === "hold" ? shown.because : `${shown.act}: ${shown.because}`;
  }

  because.classList.toggle("land", shown?.act === "land");
  because.classList.toggle("waiting", shown?.act === "hold" && Boolean(shown?.waiting));

  const becauseText = recent?.because ?? "";
  const lostWord = recent?.act === "land" && /> word (up|down)/.test(becauseText);
  const lostStop = recent?.act === "land" && /> word stop/.test(becauseText);

  for (const li of document.querySelectorAll("#rules li")) {
    const rule = li.dataset.rule;
    const won = Boolean(recent && recent.rule === rule);
    const lost = (rule === "move" && lostWord) || (rule === "stop" && lostStop);
    const pending = !won && rule === "land" && shown?.act === "hold" && Boolean(shown?.waiting);
    li.classList.toggle("won", won);
    li.classList.toggle("lost", lost);
    li.classList.toggle("pending", pending);
    const tag = li.querySelector(".rtag");

    if (tag) tag.textContent = won ? "wins" : lost ? "lost" : pending ? "not yet" : "";
  }

  const slotK = $("m-act");

  if (!shown) {
    slotK.textContent = "no act";
    slotK.classList.add("none");
    $("a-act").textContent = "fly-drone";
  } else {
    slotK.textContent = shown.act === "hold" ? `hold · ${shown.because}` : `${shown.act} · ${shown.because}`;
    slotK.classList.remove("none");
    $("a-act").textContent = `fly-drone · ${bytes(payloadBytes(shown))} · ${ageText(age(shown, now))}`;
  }

  document.querySelector('.slot[data-slot="act"]').classList.toggle("fresh", Boolean(recent));
}

function renderHistory(now) {
  const real = (state.acts || [])
    .filter((a) => a.act !== "hold")
    .filter((a, i, all) => i === 0 || a.act !== all[i - 1].act || a.because !== all[i - 1].because)
    .slice(-3)
    .reverse();

  const list = $("history");
  const key = real.map((a) => a.seen_at).join(",");

  if (list.dataset.key === key) {
    real.forEach((a, i) => {
      const s = list.children[i]?.lastChild;

      if (s) s.textContent = ageText(age(a, now));
    });

    return;
  }

  list.dataset.key = key;
  list.replaceChildren(...real.map((a) => {
    const li = document.createElement("li");
    const left = document.createElement("span");
    const b = document.createElement("b");
    b.className = a.act;
    b.textContent = a.act;
    left.append(b, ` · ${a.because}`);
    const right = document.createElement("span");
    right.textContent = ageText(age(a, now));
    li.append(left, right);

    return li;
  }));
}

function renderDrone() {
  const d = state.drone;

  if (!d || !d.phase) return;
  const card = $("drone");
  card.dataset.phase = d.phase;
  card.dataset.airborne = String(["taking_off", "hovering", "climbing", "descending", "landing"].includes(d.phase));
  $("adapter-tag").textContent = d.adapter || "airframe";

  const bat = $("bat");
  const hasBat = Number.isFinite(d.battery);
  bat.textContent = hasBat ? `battery ${d.battery}%` : "battery --";
  bat.dataset.level = !hasBat || d.battery >= 30 ? "ok" : d.battery >= 20 ? "warn" : "crit";

  const airH = $("air")?.clientHeight || 160;
  const quadH = $("quad")?.clientHeight || 82;
  const h = Math.max(36, airH - quadH);
  const ratio = Math.max(0, Math.min(1, d.altitude_m / 2.0));
  $("quad").style.bottom = `${ratio * h}px`;
  $("shadow").style.transform = `scale(${1 - ratio * 0.45})`;
  $("shadow").style.opacity = String(0.9 - ratio * 0.6);

  const target = $("target");
  target.style.bottom = `${Math.max(0, Math.min(1, d.target_m / 2.0)) * h}px`;
  target.style.borderTopColor = d.phase === "descending" ? COLORS.warn : COLORS.up;
  target.style.opacity = d.target_m > 0 && d.phase !== "parked" ? "0.9" : "0";

  const ledColor = LED[d.leds?.color] ?? COLORS.idle;
  const led = $("led");
  const ledGlow = $("led-glow");
  led.style.fill = ledColor;
  ledGlow.style.fill = d.leds?.color && d.leds.color !== "off" ? ledColor : "transparent";
  const breathe = d.leds?.mode === "breathing" ? "breathe 2.4s ease-in-out infinite" : "none";
  led.style.animation = breathe;
  ledGlow.style.animation = breathe;

  $("phase").textContent = PHASE[d.phase] ?? d.phase;
  $("phase").style.color = {
    parked: COLORS.ok,
    landing: COLORS.err,
    climbing: COLORS.up,
    descending: COLORS.warn,
    dark: COLORS.idle,
  }[d.phase] ?? COLORS.ink;
  // The target altitude is the dashed line in the arena, so the readout is
  // only where the drone is now.
  $("alt").textContent = d.phase === "dark" ? "on table" : `${d.altitude_m.toFixed(1)} m`;

  const camBox = $("stage-cam");
  const camImg = $("cam-img");
  const camLabel = $("cam-label");
  const camOn = Boolean(state.stage_cam);
  const camTag = state.stage_cam_name ? String(state.stage_cam_name).replace(/\s*StreamCamera$/i, "") : "";
  camBox.dataset.active = String(camOn);
  camImg.hidden = !camOn;
  camLabel.textContent = camOn ? (camTag || "camera live") : "camera · offline";

  if (camOn && !ui.stageCamAttached) {
    camImg.src = "/api/stage-cam.mjpg";
    ui.stageCamAttached = true;
  }
}

/* The prompt is one line at one size. On a narrower projector a long line
   is scaled to fit rather than cut off. */
function fitPrompt() {
  const el = $("prompt");
  const room = el.parentElement.clientWidth - 64;
  const need = el.offsetWidth;
  el.style.transform = need > room ? `scale(${room / need})` : "none";
}

function swapPrompt(text) {
  ui.lastScenePrompt = text;
  const el = $("prompt");
  el.classList.add("swap");
  setTimeout(() => { el.textContent = text; fitPrompt(); el.classList.remove("swap"); }, 220);
}

document.fonts?.ready.then(fitPrompt);

/* ------------------------------------------------------------ the reveal */

function pretty(raw) {
  let obj;

  try { obj = JSON.parse(raw); } catch { return document.createTextNode(String(raw)); }

  const lines = JSON.stringify(obj, null, 2).split("\n");
  const frag = document.createDocumentFragment();

  for (const line of lines) {
    const m = line.match(/^(\s*)("[^"]+":\s*)?(.*?)(,?)$/);
    const indent = m?.[1] ?? "";
    const key = m?.[2] ?? "";
    const val = m?.[3] ?? line;
    const comma = m?.[4] ?? "";
    frag.append(indent);

    if (key) frag.append(span("k", key));
    const cls = /^".*"$/.test(val) ? "s" : /^-?\d/.test(val) ? "n" : /^(true|false|null)$/.test(val) ? "b" : "";
    frag.append(cls ? span(cls, val) : val, comma, "\n");
  }

  return frag;
}

function span(cls, text) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;

  return el;
}

function messageOnly(raw) {
  try {
    return JSON.stringify(JSON.parse(raw));
  } catch { return raw; }
}

function buildReveal() {
  const sig = ["clap", "word", "hands"].map((s) => state?.latest?.[s]?.raw || "").join("|");
  const cards = $("reveal-cards");

  if (sig !== ui.revealSig || !cards.children.length) {
    ui.revealSig = sig;
    cards.replaceChildren();
    const titles = { clap: "from the mic", word: "from the mic", hands: "from the camera" };

    for (const sense of ["clap", "word", "hands"]) {
      const m = state?.latest?.[sense];
      const card = document.createElement("div");
      card.className = "card";
      const h = document.createElement("h3");
      h.textContent = NODE_OF[sense];
      const l = document.createElement("p");
      l.className = "l";
      l.append(`${titles[sense]} · `, m ? bold(bytes(payloadBytes(m))) : "nothing yet");
      const pre = document.createElement("pre");
      pre.className = "json";

      if (m?.raw) pre.append(pretty(messageOnly(m.raw)));
      card.append(h, l, pre);
      cards.append(card);
    }
  }

  const kept = state?.kept;

  if (kept) {
    const share = kept.raw_bytes > 0 ? kept.decision_bytes / kept.raw_bytes : 0;
    $("kept-raw").textContent = bytes(kept.raw_bytes);
    $("kept-raw").dataset.what = "of sound and pictures";
    $("kept-sent").textContent = bytes(kept.decision_bytes);
    $("kept-sent").dataset.what = `in ${kept.decisions} decisions`;
    $("kept-bar-raw").style.width = "100%";
    $("kept-bar-sent").style.width = `${Math.min(100, share * 100)}%`;
  }
}

function bold(text) {
  const b = document.createElement("b");
  b.textContent = text;

  return b;
}

function bytes(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;

  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;

  return `${n} B`;
}

/* ------------------------------------------------------------- the stream */

function apply(snapshot) {
  const now = performance.now();

  for (const [device, tele] of Object.entries(snapshot.devices || {})) {
    const count = (tele.sent ?? 0) + (tele.dropped ?? 0);
    const prev = ui.lastWindows[device];

    if (prev && now - prev.at > 900) {
      ui.windowRate[device] = Math.max(0, ((count - prev.count) * 1000) / (now - prev.at));
      ui.lastWindows[device] = { count, at: now };
    } else if (!prev) {
      ui.lastWindows[device] = { count, at: now };
    }
  }

  trackCommands(snapshot);

  const pulses = snapshot.pulses || [];
  const top = pulses.length ? pulses[pulses.length - 1].id : 0;

  if (ui.lastPulse === null) {
    ui.lastPulse = top;
  } else {
    for (const p of pulses) {
      if (p.id <= ui.lastPulse) continue;

      if (p.kind === "sense") {
        const sense = SENSE_OF[p.node];

        if (sense) {
          // The camera reports several times a second. A routine count is a
          // small impulse; a count that could land the drone is a full one.
          const hands = snapshot.latest?.hands;
          const loud = sense !== "hands" || (hands?.seen >= 10 && hands?.share >= 0.2);
          spawn(`imp-${sense}`, {
            speed: 1.05,
            color: sense === "hands" && hands?.share > 0.5 ? COLORS.err : COLORS.ember,
            size: loud ? 8 : 4,
            alpha: loud ? 0.95 : 0.7,
            trail: true,
            ring: loud,
          });

          if (loud) flash(`node-${p.node}`);
        }
      } else if (p.kind === "act") {
        const actItem = snapshot.act;
        const cmd = cmdKeyOf(actItem);

        if (cmd) {
          spawn(`act-${cmd}`, {
            speed: 1.0,
            color: ACT_COLOR[actItem?.act] ?? COLORS.ember,
            size: 9,
            alpha: 0.95,
            trail: true,
            ring: true,
          });
        }
      }
    }

    ui.lastPulse = top;
  }

  state = snapshot;
  ui.stateAt = now;

  if (snapshot.scene?.beat === "reveal") buildReveal();
  else ui.revealSig = "";

  if (ui.openSlot) fillInspect(ui.openSlot);
}

function flash(id) {
  const el = $(id);
  el.classList.add("fired");
  setTimeout(() => el.classList.remove("fired"), 450);
}

function connect() {
  const source = new EventSource("/api/stream");
  source.onmessage = (event) => apply(JSON.parse(event.data));
  source.onerror = () => { source.close(); setTimeout(connect, 1000); };
}

/* ----------------------------------------------------------- interaction */

function fillInspect(slot) {
  const item = slot === "act"
    ? (ui.lastRealAct && age(ui.lastRealAct, performance.now()) < ACT_STICKY_S ? ui.lastRealAct : state?.act)
    : state?.latest?.[slot];

  $("inspect-title").textContent = slot === "act" ? "fly-drone: act" : `${NODE_OF[slot]}: message`;
  const pre = $("inspect-json");
  pre.replaceChildren();

  if (item?.raw) pre.append(pretty(messageOnly(item.raw)));
  else pre.textContent = "nothing sent yet";
}

for (const slot of document.querySelectorAll(".slot")) {
  slot.addEventListener("click", () => {
    ui.openSlot = slot.dataset.slot;
    fillInspect(ui.openSlot);
    $("inspect").dataset.open = "true";
  });
}

function closeInspect() {
  ui.openSlot = null;
  $("inspect").dataset.open = "false";
}

$("inspect").addEventListener("click", closeInspect);

for (const node of NODES) {
  $(`node-${node}`).addEventListener("click", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    const cut = state?.devices?.[node]?.link !== "cut";
    postJson("/api/link", { node, cut });
  });
}

function toggleClicker() {
  ui.clickerArmed = !ui.clickerArmed;
  const btn = $("btn-clicker");
  btn.textContent = ui.clickerArmed ? "clicker: armed" : "clicker: off";
  btn.setAttribute("aria-pressed", String(ui.clickerArmed));
}

$("btn-clicker").addEventListener("click", toggleClicker);

$("btn-kill").addEventListener("click", () => { postJson("/api/kill", {}); });

$("btn-cut").addEventListener("click", () => { postJson("/api/kill", { cut: true }); });

$("btn-pipelines").addEventListener("click", () => {
  const running = Object.values(state?.nodes || {}).filter((n) => n.live).length;
  const action = running === 4 ? "stop" : "start";
  $("btn-pipelines").textContent = action === "start" ? "starting..." : "pausing...";
  ui.pipePendingUntil = performance.now() + 8000;
  void postJson("/api/pipelines", { action });
});

async function switchMic(target) {
  try {
    const resp = await fetch("/api/mic", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device: target, input_mode: "live" }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const msg = err.error ? String(err.error).slice(0, 18) : "mic error";
      ui.micNotice = {
        text: target === "dji" ? "no dji mic" : msg,
        until: performance.now() + 2600,
      };

      return;
    }

    ui.micNotice = { text: "", until: 0 };
  } catch (err) {
    console.warn("switchMic failed", err);
  }
}

for (const opt of document.querySelectorAll("#mic-switch .mic-opt")) {
  opt.addEventListener("click", () => {
    const target = opt.dataset.mic;

    if (target) void switchMic(target);
  });
}

for (const pill of document.querySelectorAll("#word-pills .wpill")) {
  pill.addEventListener("click", () => {
    const clip = pill.dataset.w;

    if (clip) postJson("/api/cue", { sense: "word", clip });
  });
}

window.addEventListener("review-alert", () => {
  const alertEl = $("link-alert");

  if (alertEl) alertEl.hidden = false;
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeInspect();
    postJson("/api/kill", event.shiftKey ? { cut: true } : {});

    return;
  }

  if (!ui.clickerArmed) return;

  if (event.key === "PageUp" || event.key === "ArrowUp") {
    event.preventDefault();
    postJson("/api/cue", { sense: "word", clip: "up" });
  } else if (event.key === "PageDown" || event.key === "ArrowDown") {
    event.preventDefault();
    postJson("/api/cue", { sense: "word", clip: "down" });
  }
});

connect();

requestAnimationFrame(frame);
