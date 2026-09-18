// Plays back a real run's stored event log at its recorded timing (FR-20).
// Vanilla JS, no dependencies. Every string from the log goes into the page with textContent.
(() => {
  "use strict";

  const root = document.getElementById("replay");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const ui = {
    play: $("rp-play"), scrub: $("rp-scrub"), clock: $("rp-clock"), status: $("rp-status"),
    log: $("rp-log"), labels: $("rp-labels"), tracks: $("rp-tracks"), stats: $("rp-stats"),
    models: $("rp-models"), tally: $("rp-tally"), speeds: Array.from(root.querySelectorAll("[data-speed]")),
  };

  const nf = new Intl.NumberFormat("en-US");
  const secs = (s) => `${Number(s).toFixed(1)} s`;
  const provider = (model) => {
    const p = String(model || "").split("/")[0];
    return p === "groq" || p === "gemini" ? p : "other";
  };
  const PHASES = { propose: "proposal", critique: "critique", options: "options", vote: "vote", memo: "memo" };
  const phase = (step) => {
    const part = String(step || "").split("/")[1] || "";
    return PHASES[part.replace(/\d+$/, "")] || part || "step";
  };
  const LABEL = {
    "run.started": "run started", "run.forecast": "forecast", "step.started": "step started",
    "agent.start": "task", "agent.call": "model call", "agent.answer": "answer", "route.wait": "wait",
    "route.fallback": "fallback", "council.tally": "tally, counted by code", "step.finished": "step finished",
    "run.finished": "run finished",
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function para(text, cls) { return el("p", cls, text); }
  function pre(text) { return el("pre", null, text); }

  let run = null;
  let events = [];
  let segments = [];
  let duration = 1;
  let t = 0;
  let shown = 0;
  let playing = false;
  let speed = 1;
  let lastFrame = 0;
  let stats = null;
  const segButtons = [];
  let playhead = null;

  // ------------------------------------------------------------------ data
  function buildSegments() {
    const open = new Map();
    for (const e of events) {
      const key = `${e.agent}|${e.step}`;
      if (e.kind === "agent.start") {
        open.set(key, { agent: e.agent, step: e.step, t0: e.t, t1: null, model: null, waits: [], tokensIn: 0, tokensOut: 0 });
      } else if (open.has(key)) {
        const s = open.get(key);
        if (e.kind === "route.wait") s.waits.push({ t: e.t, seconds: Number(e.seconds) || 0, model: e.model, reason: e.reason });
        if (e.kind === "agent.call") { s.model = e.model; s.tokensIn += e.tokens_in || 0; s.tokensOut += e.tokens_out || 0; }
        if (e.kind === "agent.answer") { s.t1 = e.t; segments.push(s); open.delete(key); }
      }
    }
  }

  function resetStats() {
    stats = { calls: 0, tin: 0, tout: 0, waits: 0, waited: 0, fallbacks: 0, models: new Map(), tally: null };
  }
  function count(e) {
    if (e.kind === "agent.call") {
      stats.calls += 1;
      stats.tin += e.tokens_in || 0;
      stats.tout += e.tokens_out || 0;
      stats.models.set(e.model, (stats.models.get(e.model) || 0) + 1);
    } else if (e.kind === "route.wait") {
      stats.waits += 1;
      stats.waited += Number(e.seconds) || 0;
    } else if (e.kind === "route.fallback") {
      stats.fallbacks += 1;
    } else if (e.kind === "council.tally") {
      stats.tally = e;
    }
  }

  // ------------------------------------------------------------------ one log entry
  const BODY = {
    "run.started"(e, li) {
      li.append(para(`Goal: ${e.goal}`));
      li.append(para(`${e.org}, ${e.models.length} models available to the router, workspace ${e.workspace}`));
      const d = el("details");
      d.append(el("summary", null, "Models the router could use"));
      d.append(pre(e.models.join("\n")));
      li.append(d);
    },
    "run.forecast"(e, li) {
      li.append(para(`Forecast: ${e.headline} (${e.basis}): about ${Math.round(e.calls)} calls and ${nf.format(Math.round(e.tokens))} tokens.`));
    },
    "step.started"(e, li) {
      li.append(para(e.type === "council"
        ? "A council: members propose without seeing each other, critique once, the chair merges the options, members vote, code counts the votes, the chair writes the memo."
        : `A ${e.type} step.`));
    },
    "agent.start"(e, li) {
      const d = el("details");
      d.append(el("summary", null, `Task: ${phase(e.step)}`));
      d.append(pre(e.task));
      li.append(d);
    },
    "agent.call"(e, li) {
      const p = el("p");
      p.append(el("span", `dot ${provider(e.model)}`));
      p.append(el("strong", null, e.model));
      li.append(p);
      const bits = [`${nf.format(e.tokens_in)} tokens in, ${nf.format(e.tokens_out)} out`];
      if (e.independent === true) bits.push("independent of the other members' model families");
      if (e.independent === false) bits.push("not independent (shares a family with another member)");
      if (e.waited) bits.push(`waited ${secs(e.waited)}`);
      if (e.fallbacks) bits.push(`after ${e.fallbacks} failed attempt${e.fallbacks === 1 ? "" : "s"} on other models`);
      if (e.finish && e.finish !== "stop") bits.push(`finish: ${e.finish}`);
      li.append(para(bits.join(" · ")));
    },
    "agent.answer"(e, li) {
      if (e.vote) {
        const conf = e.vote.confidence === null || e.vote.confidence === undefined ? "" : ` (confidence ${e.vote.confidence})`;
        li.append(para(`Votes ${e.vote.choice}${conf}`, "vote"));
        if (e.vote.reason) li.append(para(e.vote.reason));
      } else {
        li.append(pre(e.text));
      }
    },
    "route.wait"(e, li) {
      li.append(para(`Waits ${secs(e.seconds)} for ${e.model}: ${e.reason}.`));
    },
    "route.fallback"(e, li) {
      li.append(para(`${e.model} failed (attempt ${e.failure}); the router moves on.`));
      const d = el("details");
      d.append(el("summary", null, "Provider's error, as recorded"));
      d.append(pre(e.note));
      li.append(d);
    },
    "council.tally"(e, li) {
      li.append(para(`Rule: ${e.rule}. The chair cannot change this.`));
      const table = el("table");
      const head = el("tr");
      const row = el("tr");
      for (const [option, votes] of Object.entries(e.counts || {})) {
        head.append(el("th", null, option));
        row.append(el("td", null, votes));
      }
      table.append(head, row);
      li.append(table);
      li.append(para(`Winner: ${e.winner}, decided by ${e.decided_by}.` +
        (e.abstained && e.abstained.length ? ` Abstained: ${e.abstained.join(", ")}.` : " No abstentions.")));
    },
    "step.finished"(e, li) {
      li.append(para("The decision record (first 600 characters):"));
      li.append(pre(e.text));
    },
    "run.finished"(e, li) {
      li.append(para(`Run ${e.status}: ${e.calls} model calls, ${nf.format(e.tokens_in)} tokens in + ${nf.format(e.tokens_out)} out, ${e.files} files written.`, "vote"));
    },
  };

  function entry(e) {
    const li = el("li", `ev ev-${e.kind.replace(".", "-")}`);
    const head = el("div", "ev-head");
    head.append(el("span", "ev-t", secs(e.t)));
    if (e.agent) head.append(el("span", "ev-agent", e.agent));
    head.append(el("span", "ev-kind", LABEL[e.kind] || e.kind));
    if (e.step) head.append(el("code", "ev-step", e.step));
    li.append(head);
    const body = BODY[e.kind];
    if (body) body(e, li);
    return li;
  }

  // ------------------------------------------------------------------ timeline chart
  function buildGantt() {
    const agents = [];
    for (const s of segments) if (!agents.includes(s.agent)) agents.push(s.agent);
    const tip = el("div", "tip");
    tip.hidden = true;
    for (const agent of agents) {
      ui.labels.append(el("div", null, agent));
      const track = el("div", "track");
      for (const s of segments.filter((x) => x.agent === agent)) {
        const b = el("button", `seg ${provider(s.model)}`);
        b.type = "button";
        const span = Math.max(s.t1 - s.t0, 0.01);
        b.style.left = `${(s.t0 / duration) * 100}%`;
        b.style.width = `${(span / duration) * 100}%`;
        for (const w of s.waits) {
          const shade = el("span", "wait");
          const w1 = Math.min(w.t + w.seconds, s.t1);
          shade.style.left = `${((w.t - s.t0) / span) * 100}%`;
          shade.style.width = `${(Math.max(w1 - w.t, 0) / span) * 100}%`;
          b.append(shade);
        }
        const lines = [`${s.agent}, ${phase(s.step)}: ${secs(s.t0)} to ${secs(s.t1)}`,
          `${s.model || "no model"}, ${nf.format(s.tokensIn)} in / ${nf.format(s.tokensOut)} out`];
        for (const w of s.waits) lines.push(`waited ${secs(w.seconds)}: ${w.reason}`);
        const text = lines.join("\n");
        b.setAttribute("aria-label", `${text.replace(/\n/g, "; ")}. Jump here.`);
        const show = () => {
          tip.textContent = text;
          tip.hidden = false;
          const box = ui.tracks.getBoundingClientRect();
          const r = b.getBoundingClientRect();
          const left = Math.min(Math.max(r.left - box.left, 0), Math.max(box.width - tip.offsetWidth, 0));
          tip.style.left = `${left}px`;
          tip.style.top = `${r.bottom - box.top + 6}px`;
        };
        const hide = () => { tip.hidden = true; };
        b.addEventListener("pointerenter", show);
        b.addEventListener("focus", show);
        b.addEventListener("pointerleave", hide);
        b.addEventListener("blur", hide);
        b.addEventListener("click", (ev) => { ev.stopPropagation(); seek(s.t0 + 0.001); });
        segButtons.push({ b, s });
        track.append(b);
      }
      ui.tracks.append(track);
    }
    const axis = el("div", "axis");
    for (let x = 0; x < duration * 0.88; x += 30) {
      const tick = el("span", x === 0 ? "start" : null, `${x} s`);
      tick.style.left = `${(x / duration) * 100}%`;
      axis.append(tick);
    }
    const end = el("span", "end", secs(duration));
    end.style.left = "100%";
    axis.append(end);
    ui.tracks.append(axis);
    playhead = el("div", "playhead");
    playhead.style.height = `${agents.length * 26}px`;
    ui.tracks.append(playhead, tip);
    ui.tracks.addEventListener("click", (ev) => {
      const box = ui.tracks.getBoundingClientRect();
      seek(((ev.clientX - box.left) / box.width) * duration);
    });
  }

  // ------------------------------------------------------------------ painting
  function statusText() {
    if (t < 0.03 && !playing) {
      return "Press Play. Time runs as it did on the day: the long pauses are real waits for rate limits.";
    }
    if (t >= duration) {
      const done = events[events.length - 1];
      return `Run ${done.status || run.status} after ${secs(duration)}: ${run.calls} model calls, ${nf.format(run.tokens_in)} tokens in + ${nf.format(run.tokens_out)} out.`;
    }
    for (const s of segments) {
      if (s.t0 > t || s.t1 <= t) continue;
      for (const w of s.waits) {
        if (w.t <= t && t < w.t + w.seconds) {
          return `${s.agent} is waiting for ${w.model}: ${w.reason} (${secs(w.t + w.seconds - t)} left of ${secs(w.seconds)}).`;
        }
      }
      return `${s.agent} is working on its ${phase(s.step)}; the model has not answered yet.`;
    }
    const last = events[shown - 1];
    if (last && last.kind === "council.tally") return "Code has counted the votes.";
    return "Between steps.";
  }

  function paint() {
    ui.clock.textContent = `${secs(t)} / ${secs(duration)}`;
    ui.scrub.value = String(t);
    ui.status.textContent = statusText();
    if (playhead) playhead.style.left = `${(t / duration) * 100}%`;
    for (const { b, s } of segButtons) b.classList.toggle("future", s.t0 > t);
    ui.stats.replaceChildren();
    const rows = [
      ["Model calls", `${stats.calls} / ${run.calls}`],
      ["Tokens in", nf.format(stats.tin)],
      ["Tokens out", nf.format(stats.tout)],
      ["Waits", `${stats.waits} (${secs(stats.waited)})`],
      ["Failed attempts", String(stats.fallbacks)],
    ];
    for (const [k, v] of rows) ui.stats.append(el("dt", null, k), el("dd", null, v));
    ui.models.replaceChildren();
    if (!stats.models.size) ui.models.append(el("li", null, "none yet"));
    for (const [model, n] of stats.models) {
      const li = el("li");
      const name = el("span");
      name.append(el("span", `dot ${provider(model)}`), document.createTextNode(model));
      li.append(name, el("span", null, `${n}×`));
      ui.models.append(li);
    }
    ui.tally.textContent = stats.tally
      ? `${Object.entries(stats.tally.counts).map(([o, n]) => `${o} ${n}`).join(", ")}; winner ${stats.tally.winner} (${stats.tally.rule})`
      : "not yet";
  }

  function seek(target) {
    const next = Math.max(0, Math.min(duration, Number(target) || 0));
    if (next < t || shown === 0) {
      ui.log.replaceChildren();
      shown = 0;
      resetStats();
    }
    t = next;
    const frag = document.createDocumentFragment();
    while (shown < events.length && events[shown].t <= t) {
      const e = events[shown];
      shown += 1;
      count(e);
      frag.append(entry(e));
    }
    if (frag.childNodes.length) {
      const atEnd = ui.log.scrollHeight - ui.log.scrollTop - ui.log.clientHeight < 40;
      ui.log.append(frag);
      if (atEnd || playing) ui.log.scrollTop = ui.log.scrollHeight;
    }
    paint();
  }

  // ------------------------------------------------------------------ playback
  function frame(now) {
    if (!playing) return;
    const dt = Math.min(0.25, Math.max(0, (now - lastFrame) / 1000));
    lastFrame = now;
    seek(t + dt * speed);
    if (t >= duration) { pause(); return; }
    requestAnimationFrame(frame);
  }
  function play() {
    if (t >= duration) seek(0);
    playing = true;
    ui.play.textContent = "Pause";
    ui.play.setAttribute("aria-label", "Pause the replay");
    lastFrame = performance.now();
    requestAnimationFrame(frame);
  }
  function pause() {
    playing = false;
    ui.play.textContent = t >= duration ? "Replay" : "Play";
    ui.play.setAttribute("aria-label", t >= duration ? "Play the replay again" : "Play the replay");
  }

  function init(data) {
    run = data.run;
    events = data.events;
    duration = Math.max(Number(run.duration_s) || 0, 0.1);
    buildSegments();
    buildGantt();
    ui.scrub.max = String(duration);
    ui.scrub.step = "0.1";
    resetStats();
    seek(0);
    ui.play.disabled = false;
    ui.scrub.disabled = false;
    for (const b of ui.speeds) b.disabled = false;
    ui.play.addEventListener("click", () => (playing ? pause() : play()));
    ui.scrub.addEventListener("input", () => seek(ui.scrub.value));
    for (const b of ui.speeds) {
      b.addEventListener("click", () => {
        speed = Number(b.dataset.speed) || 1;
        for (const other of ui.speeds) other.setAttribute("aria-pressed", String(other === b));
      });
    }
  }

  fetch(root.dataset.src, { credentials: "same-origin" })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(init)
    .catch((err) => {
      ui.status.textContent = `The event log could not be loaded (${err.message}). The page must be served over HTTP, `
        + "for example with python -m http.server in the built folder. The raw log is linked below.";
    });
})();
