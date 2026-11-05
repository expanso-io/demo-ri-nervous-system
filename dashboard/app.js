"use strict";

/* The shared hero owns lane geometry and animation. /api/state owns rates. */

const stage = document.getElementById("stage");

const lanes = [
  { key: "source", label: "source to edge", from: { sel: '[data-cast="source"]', side: "r" },
    to: { sel: '[data-cast="edge"]', side: "l" }, kind: "flow", rail: "source",
    fromKey: "source", toKey: "edge", rate: 0 },
  { key: "dest", label: "edge to destination", from: { sel: '[data-cast="edge"]', side: "r" },
    to: { sel: '[data-cast="dest"]', side: "l" }, kind: "flow", rail: "dest",
    fromKey: "edge", toKey: "dest", rate: 0 },
  { key: "control-down", label: "Cloud deploys to edge",
    from: { sel: '[data-cast="orchestrator"]', side: "b", dx: -7 },
    to: { sel: '[data-cast="edge"]', side: "t", dx: -7 }, kind: "control", rail: "control",
    fromKey: "orchestrator", toKey: "edge", rate: 12 },
  { key: "control-up", label: "edge status to Cloud",
    from: { sel: '[data-cast="edge"]', side: "t", dx: 7 },
    to: { sel: '[data-cast="orchestrator"]', side: "b", dx: 7 }, kind: "control", rail: "control",
    fromKey: "edge", toKey: "orchestrator", rate: 8 },
];

const hero = createFlowHero({ stage, canvas: document.getElementById("flow"), lanes });

const state = { src: 0, edge: 0, rows: 0, nodes: 0 };

const fmt = (n) => Number(n).toLocaleString("en-US");

const feed = document.getElementById("feed");

function renderState() {
  document.getElementById("src-rate").textContent = fmt(state.src);

  document.getElementById("edge-rate").textContent = fmt(state.edge);

  document.getElementById("sink-rows").textContent = fmt(state.rows);

  document.getElementById("k1").textContent = fmt(state.src);

  document.getElementById("k2").textContent = state.src
    ? (100 - state.edge / state.src * 100).toFixed(1) + "%" : "0%";

  document.getElementById("k3").textContent = fmt(Math.max(0, state.src - state.edge));

  document.getElementById("k4").textContent = fmt(state.nodes);

  hero.setRates({ source: state.src, dest: state.edge });

  hero.setFault(false);
}

async function poll() {
  try {
    const response = await fetch("/api/state");

    if (!response.ok) throw new Error(String(response.status));

    Object.assign(state, await response.json());
  } catch {
    // Design-time fallback, before the pipeline is connected.
    state.src = 380 + Math.floor(Math.random() * 70);

    state.edge = 14 + Math.floor(Math.random() * 7);

    state.rows += 5 + Math.floor(Math.random() * 12);

    state.nodes = 14;
  }

  renderState();
}

setInterval(poll, 1000);

poll();

setInterval(() => {
  if (!state.src) return;

  feed.querySelector(".empty")?.remove();

  feed.querySelectorAll(".fresh").forEach((row) => row.classList.remove("fresh"));

  const row = document.createElement("li");

  row.className = "fresh";

  row.textContent = "event normalized → forwarded";

  feed.prepend(row);

  while (feed.children.length > 6) feed.lastChild.remove();
}, 2600);
