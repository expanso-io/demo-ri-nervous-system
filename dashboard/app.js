/* The audience screen. Two tiers: the drone and its flight trail on top, the
   nervous system underneath. Three sense devices on the left hold everything
   they capture; three models turn that into one small message each; one rule
   table decides; one act goes up to the drone. Lanes carry one impulse per
   real message. */

const SAMPLES = 40;

const FRESH_S = 3;

const ACT_STICKY_S = 4.8;

const CLAP_PILL_S = 2.5;

const NOMINAL = { clap: 4, word: 4, hands: 5 };

const NODES = ["hear-clap", "hear-commands", "see-hands"];

const NODE_OF = { clap: "hear-clap", word: "hear-commands", hands: "see-hands" };

const SENSE_OF = { "hear-clap": "clap", "hear-commands": "word", "see-hands": "hands" };

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
  go: COLORS.up,
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

let state = null;

const stageEl = $("stage");

const flowCanvas = $("flow");

const ctx = flowCanvas.getContext("2d");

let stageW = 1440;

let stageH = 680;

let LANES = {};

/* Where the flight arena sits on the stage, in board pixels. */
let sky = { left: 0, top: 0, w: 1, h: 1, airLeft: 0, airW: 1, quadW: 1, groundY: 0, travel: 1, decideTop: 0, decideX: 0 };

const trailCtx = $("trail").getContext("2d");

/* The flight so far. The drone starts at the left of the arena and moves
   right only while it is flying, so the trace behind it is the whole session:
   every climb, every landing, and what the room did to cause each one. */
const flight = { tau: 0, lastT: null, phase: null, pts: [], marks: [], markKey: null, markT: -Infinity };

const FLIGHT_SPAN_S = 200;

const CRAFT_START = 120;

const TRAIL_LABEL = { wake: "wake", up: "up", down: "down", stop: "stop", land: "land" };

const AIRBORNE = ["taking_off", "hovering", "climbing", "descending", "cruising", "landing"];

/* What the presenter does with their arms for each signal the route asks for. */
const ARMS_HINT = { forward: "wave it ahead", left: "wave it left", right: "wave it right" };

const LEG_LABEL = { forward: "ahead", left: "left", right: "right" };

function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function skidY(altitude) {
  return sky.groundY - clamp01(altitude / 2.0) * sky.travel;
}

function flightSpan() {
  return Math.max(1, sky.airW - sky.quadW - 20 - CRAFT_START);
}

/* Left edge of the drone inside the arena for a moment of flight time. Once
   the drone reaches the right edge it stays there and the trace scrolls. */
function craftLeft(tau) {
  const span = flightSpan();
  const px = (tau * span) / FLIGHT_SPAN_S;
  const scroll = Math.max(0, (flight.tau * span) / FLIGHT_SPAN_S - span);

  return CRAFT_START + px - scroll;
}

function craftX(tau) {
  return sky.airLeft + craftLeft(tau) + sky.quadW / 2;
}

/* The one nerve to the drone: out of the top of the rule table, along the
   gap between the tiers, and up to the skids wherever the drone is. */
function actLane() {
  const x = craftX(flight.tau);
  const gapY = sky.top + sky.h + 12;
  const y = skidY(state?.drone?.altitude_m ?? 0) + 4;

  return polyline([{ x: sky.decideX, y: sky.decideTop }, { x: sky.decideX, y: gapY }, { x, y: gapY }, { x, y }]);
}

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
  const ruleY = (rule) => relRect(document.querySelector(`#rules li[data-rule="${rule}"]`), rect);
  const moveRow = ruleY("move");
  const stopRow = ruleY("stop");

  // Rows are in priority order on both sides, so no lane crosses another.
  const nextLanes = {
    "raw-hands": curve({ x: cam.right, y: cam.midY }, { x: hands.left, y: hands.midY }),
    "raw-word": curve({ x: mic.right, y: mic.top + mic.height * 0.3 }, { x: word.left, y: word.midY }),
    "raw-clap": curve({ x: mic.right, y: mic.top + mic.height * 0.7 }, { x: clap.left, y: clap.midY }),
    "imp-hands": curve({ x: hands.right, y: hands.midY }, { x: decide.left, y: ruleY("land").midY }),
    "imp-word": curve({ x: word.right, y: word.midY }, { x: decide.left, y: (stopRow.bottom + moveRow.top) / 2 }),
    "imp-clap": curve({ x: clap.right, y: clap.midY }, { x: decide.left, y: ruleY("wake").midY }),
  };

  const trailEl = $("trail");
  const trailBox = relRect(trailEl, rect);
  const air = relRect($("air"), rect);
  const quad = relRect($("quad"), rect);
  sky = {
    left: trailBox.left,
    top: trailBox.top,
    w: trailBox.width,
    h: trailBox.height,
    airLeft: air.left,
    airW: air.width,
    quadW: quad.width,
    groundY: air.bottom,
    travel: Math.max(36, air.height - quad.height),
    decideTop: decide.top,
    decideX: decide.left + 90,
  };

  const trailW = Math.round(sky.w * dpr);
  const trailH = Math.round(sky.h * dpr);

  if (trailEl.width !== trailW || trailEl.height !== trailH) {
    trailEl.width = trailW;
    trailEl.height = trailH;
  }

  trailCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  nextLanes.act = actLane();
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
};

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
    ctx.lineWidth = raw ? 3 : 4;
    ctx.setLineDash(raw ? [2, 10] : []);
    ctx.strokeStyle = raw ? "rgba(87, 80, 73, 0.75)" : "rgba(87, 80, 73, 0.42)";
    ctx.fillStyle = "rgba(87, 80, 73, 0.7)";
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

const MONO = getComputedStyle(document.documentElement).getPropertyValue("--mono").trim() || "monospace";

/* One mark per real act, deduplicated so a reflex that fires for ten seconds
   is one mark, not forty. */
function noteFlight(snapshot) {
  const d = snapshot.drone;

  if (!d) return;
  const legs = d.route?.legs?.length ?? 0;
  const step = legs ? Math.min(d.route.step, legs) : 0;

  const reset =
    (flight.lastT !== null && snapshot.t < flight.lastT) ||
    (d.phase === "dark" && flight.phase && flight.phase !== "dark") ||
    (legs > 0 && step < (flight.step ?? 0));

  flight.step = step;

  if (reset) {
    flight.tau = 0;
    flight.pts.length = 0;
    flight.marks.length = 0;
    flight.markKey = null;
    flight.markT = -Infinity;
  }

  const dt = flight.lastT === null || reset ? 0 : Math.min(1, snapshot.t - flight.lastT);

  if (legs) {
    // On a fixed route the drone's place across the arena is how much of the
    // route it has flown, not how long it has been in the air.
    const target = (FLIGHT_SPAN_S * step) / legs;
    flight.tau = Math.min(target, flight.tau + (dt * FLIGHT_SPAN_S) / legs / 0.7);
  } else if (AIRBORNE.includes(d.phase)) {
    flight.tau += dt;
  }

  flight.lastT = snapshot.t;
  flight.phase = d.phase;
  const alt = d.altitude_m ?? 0;
  const lastPt = flight.pts[flight.pts.length - 1];

  if (!lastPt || lastPt.tau !== flight.tau || lastPt.alt !== alt) flight.pts.push({ tau: flight.tau, alt });

  for (const a of snapshot.acts || []) {
    if (a.seen_at <= flight.markT || a.act === "hold" || !TRAIL_LABEL[a.act]) continue;

    if (a.act !== flight.markKey || a.seen_at - flight.markT > 6) flight.marks.push({ key: a.act, tau: flight.tau, alt });
    flight.markKey = a.act;
    flight.markT = a.seen_at;
  }
}

/* The fixed route, drawn across the top of the arena: one bar per leg, named
   for the arm signal that releases it. Flown legs are ink, the leg being
   asked for is lit, the rest wait. */
function drawRoute(c) {
  const route = state?.drone?.route;
  const legs = route?.legs ?? [];

  if (!legs.length) return;
  const xAt = (i) => sky.airLeft + CRAFT_START + (flightSpan() * i) / legs.length + sky.quadW / 2 - sky.left;
  const y = 78;
  c.font = `600 18px ${MONO}`;
  c.textAlign = "center";
  c.lineCap = "round";

  for (let a = 0; a < legs.length; ) {
    let b = a + 1;

    while (b < legs.length && legs[b] === legs[a]) b += 1;
    const done = b <= route.step;
    const current = !done && a <= route.step;
    const color = done ? "rgba(28, 25, 23, 0.85)" : current ? COLORS.ember : "rgba(28, 25, 23, 0.3)";
    c.strokeStyle = color;
    c.fillStyle = color;
    c.lineWidth = current ? 6 : 4;
    c.beginPath();
    c.moveTo(xAt(a) + 8, y);
    c.lineTo(xAt(b) - 8, y);
    c.stroke();
    c.fillText(LEG_LABEL[legs[a]] ?? legs[a], (xAt(a) + xAt(b)) / 2, y - 16);
    a = b;
  }
}

/* The trace: the session as one line that ends at the drone, with what the
   room did marked on it. */
function drawTrail() {
  const c = trailCtx;
  c.clearRect(0, 0, sky.w, sky.h);

  drawRoute(c);

  if (flight.pts.length < 2) return;
  // The trace joins the drone at its left skid, so the drone never covers it.
  const xOf = (tau) => sky.airLeft + craftLeft(tau) + sky.quadW * 0.34 - sky.left;
  const yOf = (alt) => skidY(alt) - sky.top;
  const minX = CRAFT_START;
  c.strokeStyle = "rgba(28, 25, 23, 0.85)";
  c.lineWidth = 5;
  c.lineJoin = "round";
  c.lineCap = "round";
  c.beginPath();
  let started = false;

  for (const pt of flight.pts) {
    const x = xOf(pt.tau);

    if (x < minX) continue;

    if (started) c.lineTo(x, yOf(pt.alt));
    else c.moveTo(x, yOf(pt.alt));
    started = true;
  }

  if (started) c.stroke();
  const x0 = xOf(flight.tau);
  c.font = `600 24px ${MONO}`;
  c.textAlign = "center";

  const labelled = [];

  for (const m of flight.marks) {
    const x = xOf(m.tau);

    if (x < minX) continue;
    const y = yOf(m.alt);
    const color = ACT_COLOR[m.key] ?? COLORS.ember;
    c.strokeStyle = color;
    c.fillStyle = color;
    c.beginPath();
    c.arc(x, y, 8, 0, Math.PI * 2);
    c.fill();

    // A label this close would sit behind the drone; the caption beside the
    // arena says what is happening now.
    if (x0 - x > 60 || Math.abs(yOf(state?.drone?.altitude_m ?? 0) - y) > 40) {
      const ty = y - 18;

      // Two acts at the same spot share one label rather than overprinting.
      if (labelled.some((p) => Math.abs(p.x - x) < 90 && Math.abs(p.y - ty) < 28)) continue;
      labelled.push({ x, y: ty });
      c.strokeStyle = "#efeae0";
      c.lineWidth = 7;
      c.strokeText(TRAIL_LABEL[m.key], x, ty);
      c.fillText(TRAIL_LABEL[m.key], x, ty);
    }
  }
}

let last = performance.now();

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000) * SPEED;
  last = now;
  ctx.clearRect(0, 0, stageW, stageH);
  LANES.act = actLane();
  drawTrail();
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

function label(sense, m) {
  if (!m) return null;

  if (sense === "clap") return `clap ${m.confidence.toFixed(2)}`;

  if (sense === "word") return `${m.value} ${m.confidence.toFixed(2)}`;

  if (m.gesture) return `wave ${m.gesture}`;

  return `${m.up ?? 0} of ${m.seen ?? 0} hands`;
}

const PHASE = {
  dark: "asleep",
  taking_off: "taking off",
  hovering: "hovering",
  cruising: "moving",
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

  // The device is delivering audio frames and every sample in them is zero.
  // That is the operating system withholding the microphone, not a quiet room.
  const bytesSeen = Math.max(clapTele?.captured_bytes ?? 0, wordTele?.captured_bytes ?? 0);
  const peak = Math.max(clapTele?.level ?? 0, wordTele?.level ?? 0, ...(clapTele?.wave || [0]), ...(wordTele?.wave || [0]));

  return live && bytesSeen > 120000 && peak === 0 ? "no sound reaching mic" : "";
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
  $("cap-hands").textContent = handsTele?.link === "cut" ? "link cut" : "";

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

    const m = s.latest[sense];
    const slot = document.querySelector(`.slot[data-slot="${sense}"]`);
    const a = age(m, now);
    const text = label(sense, m);
    const k = $(`m-${sense}`);
    k.textContent = text ?? "nothing sent";
    k.classList.toggle("none", !text);
    slot.classList.toggle("fresh", sense !== "hands" && a < FRESH_S);

    if (sense === "hands") {
      const quorum = Boolean(m && n.live && m.seen >= 10);
      slot.classList.toggle("majority", quorum && m.share > 0.5);
      slot.classList.toggle("waiting", quorum && m.share >= 0.2 && m.share <= 0.5);
    }
  }

  const fly = s.nodes["fly-drone"];
  const flyEl = $("node-fly-drone");
  flyEl.dataset.live = fly.live ? "on" : "off";
  flyEl.dataset.state = fly.live ? "on" : "off";

  const running = Object.values(s.nodes).filter((n) => n.live).length;
  const pipeEl = $("pipeline-status");
  pipeEl.textContent = running ? `${running}/4 running` : "off";
  pipeEl.classList.toggle("on", running > 0);
  pipeEl.dataset.reviewPipeline = running ? "on" : "off";

  const pipeBtn = $("btn-pipelines");

  if (pipeBtn && (!ui.pipePendingUntil || performance.now() > ui.pipePendingUntil)) {
    pipeBtn.textContent = running === 4 ? "pause" : "start pipelines";
    pipeBtn.classList.toggle("on", running === 4);
    $("collect-switch").setAttribute("aria-checked", String(running === 4));
    $("collect-switch").dataset.pending = "false";
    $("collect-label").textContent = running === 4 ? "collecting" : running ? `${running} of 4 on` : "off";
  }

  renderDecide(now);
  renderDrone();
  renderFocus(now);

  const scene = s.scene || {};

  if (document.body.dataset.beat !== scene.beat) document.body.dataset.beat = scene.beat || "hook";

  const promptText = scene.prompt || BEAT_TITLE[scene.beat] || "";

  if (promptText !== ui.lastScenePrompt) swapPrompt(promptText);
}

/* Which channels the room is working on. The beat names the channel; any
   channel that has just sent something joins it, so a clap in the middle of
   the hands beat still shows. With nothing going on, nothing is dimmed. */
const BEAT_FOCUS = { clap: ["clap"], shout: ["word"], hands: ["hands"], conflict: ["hands", "word"] };

function renderFocus(now) {
  const focus = new Set(BEAT_FOCUS[state.scene?.beat] ?? []);

  for (const sense of Object.keys(NODE_OF)) {
    const m = state.latest[sense];
    const live = state.nodes[NODE_OF[sense]].live;
    const voting = Boolean(m && ((m.seen >= 10 && m.share >= 0.2) || m.gesture));
    const busy = sense === "hands" ? voting && age(m, now) < ACT_STICKY_S : age(m, now) < ACT_STICKY_S;

    if (live && busy) focus.add(sense);
  }

  for (const el of document.querySelectorAll("[data-ch]")) {
    const mine = el.dataset.ch.split(" ");
    el.classList.toggle("dim", focus.size > 0 && !mine.some((ch) => focus.has(ch)));
  }
}

function renderDecide(now) {
  const act = state.act;

  if (!act) ui.lastRealAct = null;

  if (act && act.act !== "hold") ui.lastRealAct = act;
  // A hold that is waiting on the room is news; it is not hidden behind the last act.
  const recent = !act?.waiting && ui.lastRealAct && age(ui.lastRealAct, now) < ACT_STICKY_S ? ui.lastRealAct : null;
  const shown = recent || act;
  const because = $("because");
  const hold = shown?.act === "hold";
  const waiting = hold && Boolean(shown?.waiting);

  const asleep = state.drone?.phase === "dark";
  const idleHold = hold && !waiting;

  if (!act || idleHold) {
    $("why-label").textContent = "";
    const need = state.drone?.phase === "hovering" ? state.drone?.route?.need : null;
    const hint = ARMS_HINT[need] ? `next: ${ARMS_HINT[need]}` : "";
    $("why").textContent = asleep && state.nodes["fly-drone"].live ? "say up to wake it" : hint;
  } else {
    $("why-label").textContent = hold ? "holding" : `${shown.act}, because`;
    $("why").textContent = plainWhy(shown.because);
  }

  because.classList.toggle("quiet", !act || idleHold);
  because.classList.toggle("land", shown?.act === "land");
  because.classList.toggle("waiting", waiting);
  because.classList.toggle("fresh", Boolean(recent));

  const becauseText = recent?.because ?? "";
  const lostWord = recent?.act === "land" && /> word (up|down)/.test(becauseText);
  const lostStop = recent?.act === "land" && /> word stop/.test(becauseText);

  for (const li of document.querySelectorAll("#rules li")) {
    const rule = li.dataset.rule;
    const won = Boolean(recent && recent.rule === rule);
    const lost = (rule === "move" && lostWord) || (rule === "stop" && lostStop);
    const pending = !won && rule === (shown?.rule ?? "land") && shown?.act === "hold" && Boolean(shown?.waiting);
    li.classList.toggle("won", won);
    li.classList.toggle("lost", lost);
    li.classList.toggle("pending", pending);
    const tag = li.querySelector(".rtag");

    if (tag) tag.textContent = won ? "wins" : lost ? "lost" : pending ? "not yet" : "";
  }

}

/* The rule table writes "hands 0.83 > word up". The room reads
   "hands 83% beats word up". */
function plainWhy(text) {
  return String(text ?? "")
    .replace(/hands (\d(?:\.\d+)?)/, (_, share) => `hands ${Math.round(Number(share) * 100)}%`)
    .replace(" > ", " beats ");
}

function renderDrone() {
  const d = state.drone;

  if (!d || !d.phase) return;
  const card = $("drone");
  card.dataset.phase = d.phase;
  card.dataset.airborne = String(AIRBORNE.includes(d.phase));
  const bat = $("bat");
  const hasBat = Number.isFinite(d.battery);
  bat.textContent = hasBat ? `battery ${d.battery}%` : "battery --";
  bat.dataset.level = !hasBat || d.battery >= 30 ? "ok" : d.battery >= 20 ? "warn" : "crit";

  const h = sky.travel;
  const ratio = clamp01(d.altitude_m / 2.0);
  const left = `${craftLeft(flight.tau)}px`;
  $("quad").style.left = left;
  $("shadow").style.left = left;
  $("target").style.left = left;
  $("quad").style.bottom = `${ratio * h}px`;
  $("shadow").style.transform = `scale(${1 - ratio * 0.45})`;
  $("shadow").style.opacity = String(0.9 - ratio * 0.6);

  const target = $("target");
  target.style.bottom = `${clamp01(d.target_m / 2.0) * h}px`;
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
  $("alt").textContent = d.phase === "dark" ? "" : `${d.altitude_m.toFixed(1)} m`;

  const camBox = $("stage-cam");
  const camImg = $("cam-img");
  const camLabel = $("cam-label");
  const camOn = Boolean(state.stage_cam);
  camBox.dataset.active = String(camOn);
  camImg.hidden = !camOn;
  camLabel.textContent = camOn ? "" : "camera off";

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
          spawn("act", {
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

  noteFlight(snapshot);

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

/* Collection on or off: start or pause all four pipelines in Expanso Cloud. */
function toggleCollection() {
  const running = Object.values(state?.nodes || {}).filter((n) => n.live).length;
  const action = running === 4 ? "stop" : "start";
  $("btn-pipelines").textContent = action === "start" ? "starting..." : "pausing...";
  $("collect-label").textContent = action === "start" ? "starting" : "pausing";
  $("collect-switch").dataset.pending = "true";
  ui.pipePendingUntil = performance.now() + 8000;
  void postJson("/api/pipelines", { action });
}

$("btn-pipelines").addEventListener("click", toggleCollection);

$("collect-switch").addEventListener("click", toggleCollection);

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

const FORCE_KEYS = {
  ArrowUp: ["word", "up"],
  ArrowDown: ["word", "down"],
  s: ["word", "stop"],
  c: ["clap", "clap"],
  w: ["hands", "forward"],
  a: ["hands", "left"],
  d: ["hands", "right"],
};

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeInspect();
    postJson("/api/kill", event.shiftKey ? { cut: true } : {});

    return;
  }

  // The stage fallback: a key sends the decision a model should have made.
  const forced = FORCE_KEYS[event.key.length === 1 ? event.key.toLowerCase() : event.key];

  if (forced && !event.metaKey && !event.ctrlKey && !event.altKey) {
    event.preventDefault();

    if (!event.repeat) postJson("/api/cue", { sense: forced[0], force: forced[1] });

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
