"use strict";
/* Flow-field — the full-grammar topology stage, as a reusable module.
   DESIGN_SYSTEM.md "Flow grammar" implemented once, to copy verbatim:
   per-source emitters, cubic-bézier lanes with guide paths, control lane in
   both directions, weighted flow, fault tint + recovery burst, evenodd
   clip around cards, hover inspection, damped (never killed) reduced motion.

   To reuse: keep everything down to "SHOWCASE WIRING" unchanged and only
   edit the DOM it measures — cast cards carry data-cast="source|edge|
   orchestrator|dest" — plus the lane table and rates below. A real demo
   replaces the simulator's hero.setRates({...}) with its /api/state poll;
   the field itself never fetches. */

/* ------------------------------------------------------------- geometry */

const FLOW_SAMPLES = 40;

function flowPolyline(points) {
  const lens = [0];

  for (let i = 1; i < points.length; i++) {
    lens.push(lens[i - 1] +
      Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y));
  }

  return { points, lens, total: lens[lens.length - 1] || 1 };
}

/* Cubic with horizontal tangents: lanes leave and enter card walls cleanly.
   c1 = from + 0.5·dx, c2 = to − 0.5·dx, sampled to 40 points. */
function flowCurve(from, to) {
  const dx = to.x - from.x;
  const c1 = { x: from.x + dx * 0.5, y: from.y };
  const c2 = { x: to.x - dx * 0.5, y: to.y };
  const pts = [];

  for (let i = 0; i <= FLOW_SAMPLES; i++) {
    const t = i / FLOW_SAMPLES, u = 1 - t;
    pts.push({
      x: u * u * u * from.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * to.x,
      y: u * u * u * from.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * to.y,
    });
  }

  return flowPolyline(pts);
}

function flowPointAt(path, t) {
  const d = t * path.total;
  let i = 1;

  while (i < path.lens.length - 1 && path.lens[i] < d) i++;
  const a = path.points[i - 1], b = path.points[i];
  const seg = path.lens[i] - path.lens[i - 1] || 1;
  const f = Math.min(1, Math.max(0, (d - path.lens[i - 1]) / seg));

  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
}

function flowDistTo(path, pt) {
  let best = Infinity;

  for (let i = 1; i < path.points.length; i++) {
    const a = path.points[i - 1], b = path.points[i];
    const dx = b.x - a.x, dy = b.y - a.y;
    const len = dx * dx + dy * dy || 1;
    const f = Math.max(0, Math.min(1, ((pt.x - a.x) * dx + (pt.y - a.y) * dy) / len));
    best = Math.min(best, Math.hypot(pt.x - a.x - f * dx, pt.y - a.y - f * dy));
  }

  return best;
}

/* ------------------------------------------------------------- the field */

/* Particle rails — radii and speeds by semantic role (factory-powerbi). */
const FLOW_RAIL = {
  source: { r: 1.9, speed: 70 },   // raw readings off the site
  dest:   { r: 2.6, speed: 175 },  // governed flow to a destination
  alert:  { r: 4.4, speed: 520 },  // priority alert, crosses the board fast
  control:{ r: 1.6, speed: 110 },  // orchestrator ⇄ edge, both directions
};

const FLOW_GAIN = 1 / 500;  // events/s → particles/s (factory: 1/550, 1/450)

const FLOW_CAP_LANE = 90;   // particles/s per lane, hard cap

const FLOW_CAP_TOTAL = 1600;

const FLOW_CONTROL_RATE = 12; // signals/s each way on the control lane

function createFlowHero(opts) {
  const stage = opts.stage;
  const canvas = opts.canvas;
  const ctx = canvas.getContext("2d");
  const lanes = opts.lanes.map((lane) => ({ ...lane, path: null, carry: 0 }));
  const cards = opts.cards || "[data-cast]";
  const tip = opts.tooltip || null;
  const faultShare = opts.faultShare ?? 0.22;

  let geom = null;
  let hover = null;          // { kind: "lane", lane } | { kind: "card", key }
  let pointer = null;
  let faulted = false;
  let running = true;
  let burstUntil = 0;        // recovery drain: emit hot until this time
  let backlog = 0;           // events held at the edge during the fault
  let colors = {};
  let motion = 1;
  let raf = 0;
  let last = performance.now();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const emitters = new Map();
  const particles = [];

  const applyMotion = () => { motion = reduced.matches ? 0.35 : 1; };

  reduced.addEventListener("change", applyMotion);
  applyMotion();

  function rel(rect, box) {
    return { left: rect.left - box.left, right: rect.right - box.left,
      top: rect.top - box.top, bottom: rect.bottom - box.top,
      width: rect.width, height: rect.height };
  }

  function anchor(el, side, yFrac, dx) {
    const r = geom.rects.get(el);

    return {
      x: (side === "r" ? r.right : side === "l" ? r.left : r.left + r.width / 2) + (dx || 0),
      y: side === "b" ? r.bottom : side === "t" ? r.top : r.top + r.height * (yFrac ?? 0.5),
    };
  }

  function measure() {
    const box = stage.getBoundingClientRect();

    if (!box.width) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(box.width * dpr);
    canvas.height = Math.round(box.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const style = getComputedStyle(stage);
    colors = {
      accent: style.getPropertyValue("--accent").trim(),
      ok: style.getPropertyValue("--ok").trim(),
      warn: style.getPropertyValue("--warn").trim(),
      err: style.getPropertyValue("--err").trim(),
      line: style.getPropertyValue("--line").trim(),
      dim: (style.getPropertyValue("--quiet") || style.getPropertyValue("--text-dim")).trim(),
    };

    const rects = new Map();
    const clips = [];

    stage.querySelectorAll(cards).forEach((el) => {
      const r = rel(el.getBoundingClientRect(), box);
      rects.set(el, r);
      clips.push(r);
    });

    geom = { w: box.width, h: box.height, rects, clips };

    for (const lane of lanes) {
      const fromEl = stage.querySelector(lane.from.sel);
      const toEl = stage.querySelector(lane.to.sel);

      if (!fromEl || !toEl || !rects.has(fromEl) || !rects.has(toEl)) continue;
      lane.fromEl = fromEl;
      lane.toEl = toEl;
      lane.path = flowCurve(anchor(fromEl, lane.from.side, lane.from.y, lane.from.dx),
        anchor(toEl, lane.to.side, lane.to.y, lane.to.dx));
    }
  }

  new ResizeObserver(() => measure()).observe(stage);
  measure();

  /* ---- emission: rate-driven, probabilistic per frame (factory pattern) */
  function emit(key, perSecond, dt, make) {
    if (perSecond <= 0) return;
    const rate = Math.min(FLOW_CAP_LANE, perSecond) * motion;
    const carry = (emitters.get(key) || 0) + rate * dt;
    const n = Math.floor(carry);
    emitters.set(key, carry - n);

    for (let i = 0; i < Math.min(n, 30); i++) make();
  }

  function spawn(lane, color, rail, extra) {
    if (particles.length >= FLOW_CAP_TOTAL) return;
    particles.push({
      lane, color, t: 0, rail,
      speed: FLOW_RAIL[rail].speed * (extra && extra.jitter ? 0.7 + Math.random() * 0.6 : 1),
      r: FLOW_RAIL[rail].r, fade: 1,
      dissipate: extra && extra.dissipate,
      ...extra,
    });
  }

  function laneColor(lane) {
    if (lane.kind === "control") return colors.accent;

    if (lane.kind === "warn") return colors.warn;

    return lane.rail === "source" ? colors.accent : colors.ok;
  }

  function laneDash(lane) {
    if (lane.kind === "control") return [3, 4]; // orchestrator ⇄ edge, both ways

    if (lane.kind === "warn") return [5, 6];    // degraded / alert lanes

    return null;                                // flow: solid
  }

  function laneRate(lane) {
    let rate = lane.rate;

    if (burstUntil > performance.now()) rate *= 6; // recovery: visible drain

    if (lane.faultScale != null && faulted) rate *= lane.faultScale;

    return rate;
  }

  /* ---- drawing */
  function lit(lane) {
    if (!hover) return true;

    if (hover.kind === "lane") return hover.lane === lane;

    return hover.key === lane.fromKey || hover.key === lane.toKey ||
      hover.key === "orchestrator" && lane.kind === "control";
  }

  function guide(lane, alpha, width) {
    const p = lane.path;

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = laneColor(lane);
    ctx.lineWidth = width || 1.2;
    const dash = laneDash(lane);

    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    p.points.forEach((pt, i) => (i ? ctx.lineTo(pt.x, pt.y) : ctx.moveTo(pt.x, pt.y)));
    ctx.stroke();
    ctx.restore();
  }

  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;

    if (geom) draw(dt, now);
    raf = requestAnimationFrame(frame);
  }

  function draw(dt, now) {
    ctx.clearRect(0, 0, geom.w, geom.h);
    ctx.save();
    /* Particles never cross text: evenodd clip around every card. */
    ctx.beginPath();
    ctx.rect(0, 0, geom.w, geom.h);

    for (const r of geom.clips) ctx.rect(r.left, r.top, r.width, r.height);
    ctx.clip("evenodd");

    for (const lane of lanes) {
      if (!lane.path) continue;

      // Guide path: faint 1.2px at ~0.28 alpha so geometry reads between
      // particles. Fault tints flow lanes toward warn while faulted.
      let alpha = 0.28;
      let kind = lane.kind;

      if (faulted && (lane.rail === "source" || lane.toKey === "edge") && kind === "flow") kind = "warn";
      const shown = { ...lane, kind };
      const on = lit(lane);
      guide(shown, hover ? (on ? 0.75 : 0.06) : alpha, hover && on ? 2.4 : 1.2);

      // Per-source emission: every lane emits from ITS OWN position at its
      // own rate, constantly — before the pipeline is live too. When the
      // edge is idle, source particles dissipate at its boundary (die at
      // 0.94 with a fast fade instead of landing).
      const rail = lane.rail;
      emit(`e-${lane.key}`, laneRate(lane) * FLOW_GAIN, dt, () => {
        const tinted = faulted && lane.rail === "source" &&
          Math.random() < faultShare;

        spawn(lane, tinted ? colors.err : laneColor(lane), rail, {
          dissipate: !running && rail === "source",
          jitter: rail === "alert",
        });
      });
    }

    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      const die = p.dissipate ? 0.94 : 1;
      p.t += p.speed * motion * dt / p.lane.path.total;

      if (p.t >= die) {
        if (p.dissipate && p.t < 1) {
          // held at the edge boundary: fade out in place, don't land
          p.fade = Math.max(0, p.fade - dt * 5);
          p.t = Math.min(p.t, die);

          if (p.fade <= 0) { particles.splice(i, 1); continue; }
        } else {
          particles.splice(i, 1);
          continue;
        }
      }

      const pos = flowPointAt(p.lane.path, p.t);
      let a = p.fade * (hover && !lit(p.lane) ? 0.15 : 1);

      if (p.t < 0.12) a *= p.t / 0.12;           // very light inside the box

      if (die < 1) a *= Math.max(0, 1 - (p.t / die) ** 3);

      ctx.globalAlpha = Math.min(1, a) * 0.95;
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.restore();
    ctx.globalAlpha = 1;
  }

  raf = requestAnimationFrame(frame);

  /* ---- hover inspection: dim unrelated lanes, tooltip with real numbers */
  function hitAt(pt) {
    let best = null, bestD = 14;

    for (const lane of lanes) {
      if (!lane.path) continue;
      const d = flowDistTo(lane.path, pt);

      if (d < bestD) { bestD = d; best = lane; }
    }

    if (best) return { kind: "lane", lane: best };

    if (!geom) return null;

    for (const [el, r] of geom.rects) {
      if (pt.x >= r.left && pt.x <= r.right && pt.y >= r.top && pt.y <= r.bottom) {
        return { kind: "card", key: el.dataset.key || el.dataset.cast };
      }
    }

    return null;
  }

  stage.addEventListener("mousemove", (ev) => {
    if (!geom) return;
    const box = stage.getBoundingClientRect();
    pointer = { x: ev.clientX - box.left, y: ev.clientY - box.top };
    hover = hitAt(pointer);

    if (tip) {
      if (hover && hover.kind === "lane" && hover.lane.stats) {
        tip.hidden = false;
        tip.innerHTML = hover.lane.stats(hover.lane, { faulted, running, backlog });
        const w = tip.offsetWidth, h = tip.offsetHeight;
        tip.style.left = `${Math.max(8, Math.min(pointer.x + 16, geom.w - w - 8))}px`;
        tip.style.top = `${Math.max(8, Math.min(pointer.y + 16, geom.h - h - 8))}px`;
      } else {
        tip.hidden = true;
      }
    }

    stage.style.cursor = hover ? "pointer" : "default";
  });

  stage.addEventListener("mouseleave", () => {
    hover = null;

    if (tip) tip.hidden = true;
  });

  /* ---- public API */
  return {
    setRates(rates) {
      for (const lane of lanes) {
        if (rates[lane.key] != null) lane.rate = rates[lane.key];
      }
    },
    setFault(onOff) {
      if (onOff && !faulted) faulted = true;

      if (!onOff && faulted) {
        faulted = false;
        burstUntil = performance.now() + 1400; // the backlog drains visibly
        backlog = 0;
      }
    },
    setRunning(onOff) { running = onOff; },
    get faulted() { return faulted; },
    get running() { return running; },
    get backlog() { return backlog; },
    addBacklog(n) { backlog += n; },
    measure,
    destroy() { cancelAnimationFrame(raf); reduced.removeEventListener("change", applyMotion); },
  };
}

