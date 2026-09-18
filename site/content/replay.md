---
title: Replay of a real run
description: A council of five agents decides a question on free Groq and Gemini keys, replayed at its real timing from Cadre's stored event log. Not a screen recording.
script: replay.js
wide: true
---

# Replay of a real run

> **This is a replay of the stored event log of a real run on free Groq and Gemini keys,
> 2026-09-17, run …230536 — not a screen recording.** Every event, model name, token count and
> pause below is what Cadre recorded while the run happened. The page only plays it back, at the
> recorded timing: at 1× it takes as long as the run did.

<div id="replay" class="replay" data-src="assets/replay-decision-board.json">
<div class="replay-bar">
<button type="button" id="rp-play" class="btn primary" disabled aria-label="Play the replay">Play</button>
<span class="speeds" role="group" aria-label="Playback speed"><button type="button" data-speed="1" aria-pressed="true" disabled>1×</button><button type="button" data-speed="4" aria-pressed="false" disabled>4×</button><button type="button" data-speed="16" aria-pressed="false" disabled>16×</button></span>
<input type="range" id="rp-scrub" class="scrub" min="0" max="146" step="0.1" value="0" disabled aria-label="Position in the run, in seconds">
<output id="rp-clock" class="clock" for="rp-scrub">0.0 s</output>
</div>
<p id="rp-status" class="replay-status">Loading the event log…</p>
<div class="gantt" role="group" aria-label="Timeline, one lane per agent. Select a bar to jump to that step.">
<div id="rp-labels" class="gantt-labels"></div>
<div id="rp-tracks" class="gantt-tracks"></div>
</div>
<p class="legend"><span class="k-groq">answered on Groq</span><span class="k-gemini">answered on Google AI Studio (Gemini)</span><span class="k-wait">waiting for a rate limit</span><span class="k-future">not reached yet</span></p>
<div class="replay-grid">
<ol id="rp-log" class="replay-log" aria-label="Events so far"></ol>
<aside class="replay-side" aria-label="Totals so far">
<div><h3>So far</h3><dl id="rp-stats"></dl></div>
<div><h3>Models that answered</h3><ul id="rp-models"></ul></div>
<div><h3>Vote tally</h3><p id="rp-tally">not yet</p></div>
</aside>
</div>
</div>
<noscript><p>The player needs JavaScript. The same run is summarised in the tables below, and the raw event log is <a href="assets/replay-decision-board.json">assets/replay-decision-board.json</a>.</p></noscript>

## The run

<!-- replay:facts -->

It is the last row of the [M5 table](numbers.html#m5-every-template-on-live-keys-2026-09-17): the
`decision-board` template started through the API, succeeded in 146 s with 14 calls and
13,186 + 7,920 tokens, and the totals the replay ends on are those same numbers.

## What you are watching

- **A council.** Four members (CFO, CTO, CMO, risk officer) each propose without seeing the others,
  then critique once. The CEO merges the proposals into options, the members vote in strict JSON,
  **code counts the votes**, and the CEO writes the memo from the tally without being able to change
  it.
- **Real waits.** At 8.6 s Gemini 3.7 Flash answered 503 "high demand", so the router rested it for
  30 s; its next try hit the free tier's daily request limit (a 429), and the CTO's proposal went to
  Gemini 3.5 Flash-Lite instead. At 51 s the CEO waited 50 s because Groq's 8,000 tokens per minute
  were in use, and at 109 s another 34.7 s because Groq reported its token window spent. Those waits
  are most of the run's length; the hatched parts of the timeline show them.
- **Independence, recorded per call.** Each member's call says whether its model family differed
  from the other members': 3 calls were independent, 6 were not, and the first member's and the
  chair's calls record none. Only two families (gpt-oss and Gemini) answered in this run.

## How this page was made

`site/export_replay.py` reads Cadre's SQLite store read-only and writes
[`assets/replay-decision-board.json`](assets/replay-decision-board.json), which this page plays. It
keeps each event's kind, agent, step and time offset (seconds since the run's first event, from the
events' own timestamps) and a short summary: the text, model, verdict, tally or vote. It rewrites
local paths to a neutral `~/.cadre/…` form, cuts long texts at about 600 characters (so the memo and
the proposals are shortened), and drops every other field. It refuses to write the file if any key
prefix, the API token or the machine's user name survives. The site build never opens the database.

## Every model call in this run

This is the same log as a table: every call names the model that answered.

<!-- replay:calls -->
