"use strict";
/* The audience screen. Everything drawn comes from /api/stream, which the
   screen server builds from the MQTT bus and each Expanso Edge agent's own
   API. Two kinds of flow, on purpose:
   - raw: sound windows and camera frames, dim and dense, from each device
     into its node. They stop at the node, running or not. Nothing raw
     travels on.
   - impulse: one bright particle per real decision message, node to
     fly-drone, and one per act, fly-drone to the drone. */

const STAGE_W = 1920;

const STAGE_H = 720;

const SAMPLES = 40;

const FRESH_S = 3;

const NOMINAL = { clap: 4, word: 4, hands: 5 };   // windows/s before any telemetry

const ROWS = { clap: 225, word: 435, hands: 645 };

const NODE_OF = { clap: "hear-clap", word: "hear-commands", hands: "see-hands" };

const SENSE_OF = { "hear-clap": "clap", "hear-commands": "word", "see-hands": "hands" };

const EMPTY = { clap: "no clap yet", word: "no word yet", hands: "no count yet" };

const DEVICE_OF = { clap: "hear-clap", word: "hear-commands", hands: "see-hands" };

const COLORS = {
  ember: "#ff9a5a", dim: "#b9ac9c", ok: "#8fe0a0", err: "#ff5d6c",
  up: "#74b8ff", warn: "#f2c14e", white: "#f4ede3", idle: "#6d6259",
};

const LED = {
  off: COLORS.idle, white: COLORS.white, blue: COLORS.up,
  amber: COLORS.warn, red: COLORS.err, green: COLORS.ok,
};

const ACT_COLOR = {
  land: COLORS.err, up: COLORS.up, down: COLORS.warn,
  wake: COLORS.white, stop: COLORS.white, hold: COLORS.dim,
};

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const SPEED = reduced ? 0.35 : 1;

const $ = (id) => document.getElementById(id);

/* --------------------------------------------------------------- geometry */

function curve(from, to) {
  const dx = to.x - from.x;
  const c1 = { x: from.x + dx * 0.5, y: from.y };
  const c2 = { x: to.x - dx * 0.5, y: to.y };
  const pts = [];

  for (let i = 0; i <= SAMPLES; i++) {
    const t = i / SAMPLES, u = 1 - t;
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
  const d = t * path.total;
  let i = 1;

  while (i < path.lens.length - 1 && path.lens[i] < d) i++;
  const a = path.points[i - 1], b = path.points[i];
  const seg = path.lens[i] - path.lens[i - 1] || 1;
  const f = Math.min(1, Math.max(0, (d - path.lens[i - 1]) / seg));

  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
}

const LANES = {};

for (const sense of Object.keys(ROWS)) {
  const y = ROWS[sense];
  LANES[`raw-${sense}`] = curve({ x: 408, y }, { x: 560, y });
  LANES[`imp-${sense}`] = curve({ x: 900, y }, { x: 1060, y: 435 });
  LANES[`ctl-${sense}`] = polyline([{ x: 930, y: 78 }, { x: 930, y: y - 48 }, { x: 900, y: y - 48 }]);
}

LANES.act = curve({ x: 1440, y: 435 }, { x: 1520, y: 435 });

LANES["ctl-fly"] = polyline([{ x: 1250, y: 78 }, { x: 1250, y: 150 }]);

/* ------------------------------------------------------------------ state */

let state = null;

let stateAt = 0;               // performance.now() when the snapshot arrived

let lastPulse = null;          // highest pulse id already drawn

let lastWindows = {};          // device -> {count, at}

const windowRate = {};         // device -> windows/s, measured

let lastScenePrompt = null;

let lastRealAct = null;

let openSlot = null;

/* -------------------------------------------------------------- particles */

const canvas = $("flow");

const ctx = canvas.getContext("2d");

const dpr = window.devicePixelRatio || 1;

canvas.width = STAGE_W * dpr;

canvas.height = STAGE_H * dpr;

ctx.scale(dpr, dpr);

const particles = [];

const carry = {};

function spawn(lane, opts) {
  particles.push({
    lane, t: 0, speed: opts.speed, color: opts.color, size: opts.size,
    alpha: opts.alpha, end: opts.end ?? 1, glow: opts.glow ?? false, fadeFrom: opts.fadeFrom ?? 1,
  });
}

function emitRaw(dt) {
  for (const sense of Object.keys(ROWS)) {
    const device = DEVICE_OF[sense];
    const node = NODE_OF[sense];
    const tele = state?.devices?.[device];
    const cut = tele?.link === "cut";
    const live = state?.nodes?.[node]?.live;
    // Each window is drawn as six grains: dense enough to read as a stream.
    const perSecond = (state ? (windowRate[device] ?? 0) : NOMINAL[sense]) * 6;
    carry[sense] = (carry[sense] ?? 0) + perSecond * dt;

    while (carry[sense] >= 1) {
      carry[sense] -= 1;
      spawn(`raw-${sense}`, {
        speed: 0.55 + Math.random() * 0.25,
        color: cut ? COLORS.err : COLORS.dim,
        size: 1.6 + Math.random() * 0.8,
        alpha: 0.55,
        // Off: the window dissipates at the node's boundary. Cut: it never
        // gets far from the device. On: it enters the node and stops there.
        end: cut ? 0.3 : live ? 1 : 0.86,
        fadeFrom: cut ? 0.1 : live ? 0.9 : 0.55,
      });
    }
  }
}

function emitControl(dt) {
  for (const sense of Object.keys(ROWS)) {
    const agent = state?.nodes?.[NODE_OF[sense]]?.agent ?? false;
    controlPair(`ctl-${sense}`, agent, dt);
  }

  controlPair("ctl-fly", state?.nodes?.["fly-drone"]?.agent ?? false, dt);
}

function controlPair(lane, agent, dt) {
  if (!agent) return;
  carry[lane] = (carry[lane] ?? 0) + 0.7 * dt;

  if (carry[lane] >= 1) {
    carry[lane] -= 1;
    spawn(lane, { speed: 0.5, color: COLORS.dim, size: 1.6, alpha: 0.5 });
    spawn(`${lane}~up`, { speed: 0.5, color: COLORS.ok, size: 1.6, alpha: 0.55 });
  }
}

function laneOf(name) {
  if (name.endsWith("~up")) {
    const base = LANES[name.slice(0, -3)];

    if (!base.reversed) base.reversed = polyline([...base.points].reverse());

    return base.reversed;
  }

  return LANES[name];
}

function guides() {
  ctx.lineWidth = 1;

  for (const [name, path] of Object.entries(LANES)) {
    ctx.strokeStyle = name.startsWith("ctl") ? "rgba(185,172,156,.16)" : "rgba(185,172,156,.12)";
    ctx.setLineDash(name.startsWith("ctl") ? [3, 5] : []);
    ctx.beginPath();
    path.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.stroke();
  }

  ctx.setLineDash([]);
}

let last = performance.now();

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000) * SPEED;
  last = now;
  ctx.clearRect(0, 0, STAGE_W, STAGE_H);
  guides();
  emitRaw(dt);
  emitControl(dt);

  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i];
    const path = laneOf(p.lane);
    p.t += (p.speed * dt * 160) / path.total;

    if (p.t >= p.end) { particles.splice(i, 1); continue; }

    const pt = at(path, p.t);
    const fade = p.t > p.fadeFrom ? 1 - (p.t - p.fadeFrom) / Math.max(0.01, p.end - p.fadeFrom) : 1;
    const enter = Math.min(1, p.t * 6);
    ctx.globalAlpha = p.alpha * fade * enter;
    ctx.fillStyle = p.color;

    if (p.glow) {
      ctx.beginPath();
      ctx.globalAlpha = 0.25 * fade;
      ctx.arc(pt.x, pt.y, p.size * 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = p.alpha * fade;
    }

    ctx.beginPath();
    ctx.arc(pt.x, pt.y, p.size, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.globalAlpha = 1;
  render(now);
  requestAnimationFrame(frame);
}

/* ----------------------------------------------------------------- render */

function age(item, now) {
  if (!item || !state) return Infinity;

  return state.t + (now - stateAt) / 1000 - item.seen_at;
}

function ageText(seconds) {
  if (!Number.isFinite(seconds)) return "";

  return seconds < 10 ? `${seconds.toFixed(1)} s ago` : `${Math.round(seconds)} s ago`;
}

function label(sense, m) {
  if (!m) return null;

  if (sense === "clap") return `clap ${m.confidence.toFixed(2)}`;

  if (sense === "word") return `${m.value} ${m.confidence.toFixed(2)}`;

  return `hands ${Math.round(m.share * 100)}%`;
}

const PHASE = {
  dark: "dark", taking_off: "taking off", hovering: "hovering", climbing: "climbing",
  descending: "descending", landing: "landing", parked: "parked",
};

function render(now) {
  if (!state) return;
  const s = state;

  for (const sense of Object.keys(ROWS)) {
    const device = DEVICE_OF[sense];
    const tele = s.devices[device];
    const card = $(`src-${sense}`);
    card.dataset.link = tele?.link ?? "ok";
    $(`lvl-${sense}`).style.width = `${Math.round((tele?.level ?? 0) * 100)}%`;
    const rate = windowRate[device];
    const unit = sense === "hands" ? "frames/s" : "windows/s";
    const verb = sense === "hands" ? "watching" : "listening";
    $(`cap-${sense}`).textContent = tele?.link === "cut"
      ? "link cut: nothing reaches its node"
      : `${verb} · ${rate ? rate.toFixed(0) : "0"} ${unit}`;

    const node = NODE_OF[sense];
    const n = s.nodes[node];
    const cut = tele?.link === "cut";
    const el = $(`node-${node}`);
    el.dataset.live = cut ? "cut" : n.live ? "on" : "off";
    $(`n-${node}`).textContent = n.sent;
    $(`s-${node}`).textContent = cut ? "no fresh instruction" : n.live ? "running" : "off";

    const m = s.latest[sense];
    const slot = document.querySelector(`.slot[data-slot="${sense}"]`);
    const a = age(m, now);
    const text = label(sense, m);
    const k = $(`m-${sense}`);
    k.textContent = text ?? EMPTY[sense];
    k.classList.toggle("none", !text);
    $(`a-${sense}`).textContent = m ? `${node} · ${ageText(a)}` : node;
    slot.classList.toggle("fresh", a < FRESH_S);
    slot.classList.toggle("stale", Number.isFinite(a) && a >= FRESH_S);
  }

  const fly = s.nodes["fly-drone"];
  $("node-fly-drone").dataset.live = fly.live ? "on" : "off";
  $("n-fly-drone").textContent = fly.sent;

  const joined = Object.values(s.nodes).filter((n) => n.agent).length;
  const running = Object.values(s.nodes).filter((n) => n.live).length;
  $("cloud-nodes").textContent = `${joined} of 4 nodes joined`;
  $("cloud-nodes").classList.toggle("on", joined === 4);
  $("cloud-jobs").textContent = running ? `${running} of 4 pipelines running` : "pipelines off";
  $("cloud-jobs").classList.toggle("on", running === 4);

  renderDecide(now);
  renderHistory(now);
  renderDrone();

  const scene = s.scene || {};

  if (document.body.dataset.beat !== scene.beat) document.body.dataset.beat = scene.beat || "hook";

  if (scene.prompt !== lastScenePrompt) swapPrompt(scene.prompt || "");
}

function renderDecide(now) {
  const act = state.act;

  if (!act) lastRealAct = null;


  if (act && act.act !== "hold") lastRealAct = act;
  const recent = lastRealAct && age(lastRealAct, now) < 3.5 ? lastRealAct : null;
  const shown = recent || act;
  const because = $("because");

  if (!act) {
    because.textContent = state.nodes["fly-drone"].live ? "no instruction" : "off";
  } else if (shown) {
    because.textContent = shown.act === "hold" ? shown.because : `${shown.act}: ${shown.because}`;
  }

  because.classList.toggle("land", shown?.act === "land");
  because.classList.toggle("waiting", shown?.act === "hold" && !!shown?.waiting);

  for (const li of document.querySelectorAll("#rules li")) {
    const rule = li.dataset.rule;
    li.classList.toggle("won", !!recent && recent.rule === rule);
    const lostWord = recent?.act === "land" && /> word (up|down)/.test(recent.because);
    const lostStop = recent?.act === "land" && /> word stop/.test(recent.because);
    li.classList.toggle("lost", (rule === "move" && lostWord) || (rule === "stop" && lostStop));
  }

  const slotK = $("m-act");

  if (!shown) {
    slotK.textContent = "no act yet";
    slotK.classList.add("none");
    $("a-act").textContent = "fly-drone";
  } else {
    slotK.textContent = shown.act === "hold" ? `hold · ${shown.because}` : `${shown.act} · ${shown.because}`;
    slotK.classList.remove("none");
    $("a-act").textContent = `fly-drone · ${ageText(age(shown, now))}`;
  }

  document.querySelector('.slot[data-slot="act"]').classList.toggle("fresh", !!recent);
}

function renderHistory(now) {
  const real = (state.acts || []).filter((a) => a.act !== "hold").slice(-5).reverse();
  const list = $("history");
  const key = real.map((a) => a.seen_at).join(",");

  if (list.dataset.key === key) {
    real.forEach((a, i) => { const s = list.children[i]?.lastChild;

 if (s) s.textContent = ageText(age(a, now)); });

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
  const h = 380 - 80;
  $("quad").style.bottom = `${Math.max(0, Math.min(1, d.altitude_m / 2.0)) * h}px`;
  const target = $("target");
  target.style.bottom = `${Math.max(0, Math.min(1, d.target_m / 2.0)) * h + 76}px`;
  target.style.opacity = d.target_m > 0 && d.phase !== "parked" ? "0.8" : "0";
  const led = $("led");
  led.style.fill = LED[d.leds?.color] ?? COLORS.idle;
  led.style.animation = d.leds?.mode === "breathing" ? "breathe 2.4s ease-in-out infinite" : "none";
  $("phase").textContent = PHASE[d.phase] ?? d.phase;
  $("phase").style.color = d.phase === "parked" ? COLORS.ok : d.phase === "landing" ? COLORS.err : COLORS.white;
  const alt = `${d.altitude_m.toFixed(1)} m`;
  $("alt").textContent = d.phase === "dark" ? "on the table" : d.target_m > 0 && Math.abs(d.target_m - d.altitude_m) > 0.05
    ? `${alt} · heading for ${d.target_m.toFixed(1)} m` : alt;
}

function swapPrompt(text) {
  lastScenePrompt = text;
  const el = $("prompt");
  el.classList.add("swap");
  setTimeout(() => { el.textContent = text; el.classList.remove("swap"); }, 380);
}

/* ------------------------------------------------------------ the reveal */

function pretty(raw) {
  let obj;

  try { obj = JSON.parse(raw); } catch { return document.createTextNode(String(raw)); }

  const lines = JSON.stringify(obj, null, 2).split("\n");
  const frag = document.createDocumentFragment();

  for (const line of lines) {
    const m = line.match(/^(\s*)("[^"]+":\s*)?(.*?)(,?)$/);
    const indent = m?.[1] ?? "", key = m?.[2] ?? "", val = m?.[3] ?? line, comma = m?.[4] ?? "";
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
    const obj = JSON.parse(raw);

    return JSON.stringify(obj);
  } catch { return raw; }
}

function buildReveal() {
  const cards = $("reveal-cards");
  cards.replaceChildren();
  const titles = { clap: "from the room mic", word: "from the handheld mic", hands: "from the camera" };

  for (const sense of ["clap", "word", "hands"]) {
    const m = state?.latest?.[sense];
    const card = document.createElement("div");
    card.className = "card";
    const h = document.createElement("h3");
    h.textContent = NODE_OF[sense];
    const l = document.createElement("p");
    l.className = "l";
    l.textContent = titles[sense];
    const pre = document.createElement("pre");
    pre.className = "json";

    if (m?.raw) pre.append(pretty(messageOnly(m.raw)));
    card.append(h, l, pre);
    cards.append(card);
  }

  const kept = state?.kept;

  if (kept) {
    $("kept").innerHTML = "";
    $("kept").append(
      "Stayed on the devices: ", bold(bytes(kept.raw_bytes)), " of sound and pictures. ",
      "Left them: ", bold(bytes(kept.decision_bytes)), ` in ${kept.decisions} decisions.`,
    );
  }
}

function bold(text) { const b = document.createElement("b"); b.textContent = text;

 return b; }

function bytes(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;

  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;

  return `${n} bytes`;
}

/* ------------------------------------------------------------- the stream */

function apply(snapshot) {
  const now = performance.now();

  for (const [device, tele] of Object.entries(snapshot.devices || {})) {
    const count = (tele.sent ?? 0) + (tele.dropped ?? 0);
    const prev = lastWindows[device];

    if (prev && now - prev.at > 900) {
      windowRate[device] = Math.max(0, ((count - prev.count) * 1000) / (now - prev.at));
      lastWindows[device] = { count, at: now };
    } else if (!prev) {
      lastWindows[device] = { count, at: now };
    }
  }

  const pulses = snapshot.pulses || [];
  const top = pulses.length ? pulses[pulses.length - 1].id : 0;

  if (lastPulse === null) {
    lastPulse = top;     // a refresh shows the true state, never redraws old flow
  } else {
    for (const p of pulses) {
      if (p.id <= lastPulse) continue;

      if (p.kind === "sense") {
        const sense = SENSE_OF[p.node];

        if (sense) {
          spawn(`imp-${sense}`, { speed: 1.1, color: COLORS.ember, size: 5, alpha: 1, glow: true });
          flash(`node-${p.node}`);
        }
      } else if (p.kind === "act") {
        const act = snapshot.act?.act ?? "hold";
        spawn("act", {
          speed: 0.9, color: ACT_COLOR[act] ?? COLORS.dim, size: act === "hold" ? 3 : 5,
          alpha: act === "hold" ? 0.6 : 1, glow: act !== "hold",
        });
      }
    }

    lastPulse = top;
  }

  const wasReveal = state?.scene?.beat === "reveal";
  state = snapshot;
  stateAt = now;

  if (snapshot.scene?.beat === "reveal" && !wasReveal) buildReveal();

  if (openSlot) fillInspect(openSlot);
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
  const item = slot === "act" ? (lastRealAct && age(lastRealAct, performance.now()) < 3.5 ? lastRealAct : state?.act) : state?.latest?.[slot];
  $("inspect-title").textContent = slot === "act" ? "fly-drone: the act it sent" : `${NODE_OF[slot]}: the message it sent`;
  const pre = $("inspect-json");
  pre.replaceChildren();

  if (item?.raw) pre.append(pretty(messageOnly(item.raw)));
  else pre.textContent = "nothing sent yet";
}

for (const slot of document.querySelectorAll(".slot")) {
  slot.addEventListener("click", () => {
    openSlot = slot.dataset.slot;
    fillInspect(openSlot);
    $("inspect").dataset.open = "true";
  });
}

function closeInspect() {
  openSlot = null;
  $("inspect").dataset.open = "false";
}

$("inspect").addEventListener("click", closeInspect);

for (const node of ["hear-clap", "hear-commands", "see-hands"]) {
  $(`node-${node}`).addEventListener("click", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    const cut = state?.devices?.[node]?.link !== "cut";
    fetch("/api/link", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ node, cut }),
    });
  });
}

// Escape is the presenter's stop: land now, from any state, first press.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeInspect();
  fetch("/api/kill", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
});

connect();

requestAnimationFrame(frame);
