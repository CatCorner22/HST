'use strict';

const $ = (id) => document.getElementById(id);

// ---- tabs ----------------------------------------------------------------
document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $(tab.dataset.panel).classList.add('active');
  });
});

// ---- shared helpers ------------------------------------------------------
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* non-JSON error */ }
    throw new Error(detail);
  }
  return res.json();
}

function describeDirective(d) {
  if (!d) return '';
  const bits = [
    `seed ${d.seed}`, d.opening_move, `${d.dominant_band}→${d.contrast_band}`, d.imagery_domain,
  ];
  if (d.template) bits.push(d.template);
  return bits.join(' · ');
}

// Renders the metrics/judge report the same way everywhere.
function renderReport(report) {
  if (!report) return '';
  const m = report.metrics || report;
  const passed = report.passed !== undefined ? report.passed : m.passed;
  const score = report.score !== undefined ? report.score : m.score;

  const lines = [
    `rhythm       cv ${m.sentence_length_cv.toFixed(2)}   mean ${m.mean_sentence_length.toFixed(1)}w   ` +
      `fragments ${Math.round(m.fragment_rate * 100)}%   longest ${m.longest_sentence}w`,
    `specificity  ${m.specificity_density.toFixed(1)} per 100 words`,
    `adjectives   ${m.adjective_stack_rate.toFixed(2)} stacks per 100 words`,
    `tics         ${m.tic_count} used (${m.tic_rate_per_500.toFixed(1)}/500w, budget ${m.tic_budget})`,
    `registers    ${m.bands.length ? m.bands.join(' → ') : '—'}`,
    `             ${m.register_transitions} transitions, elegiac ${m.has_elegiac ? 'present' : 'ABSENT'}`,
  ];

  const v = report.verdict;
  if (v) {
    lines.push('');
    lines.push(`judge        aim ${v.aim}  anchoring ${v.anchoring}  register ${v.register_control}`);
    lines.push(`             elegiac ${v.elegiac_quality}  argument ${v.argument}  originality ${v.originality}`);
    if (v.reads_as_pastiche) lines.push('             ⚠ READS AS PASTICHE');
    lines.push('');
    lines.push(`strongest:   ${v.strongest}`);
    lines.push(`weakest:     ${v.weakest}`);
  }

  if (m.failures && m.failures.length) {
    lines.push('', 'failures:');
    m.failures.forEach((f) => lines.push(`  - ${f}`));
  }
  if (v && v.fixes && v.fixes.length) {
    lines.push('', 'fixes:');
    v.fixes.forEach((f) => lines.push(`  - ${f}`));
  }

  const head = `<span class="verdict ${passed ? 'pass' : 'fail'}">${passed ? 'PASS' : 'FAIL'}</span>` +
    `  ${score.toFixed(1)}/100   (${m.word_count} words)\n\n`;
  return `<div class="report">${head}${escapeHTML(lines.join('\n'))}</div>`;
}

function escapeHTML(s) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

function busy(button, on, label) {
  button.disabled = on;
  if (on) { button.dataset.label = button.textContent; button.textContent = label || 'Working…'; }
  else if (button.dataset.label) { button.textContent = button.dataset.label; }
}

// ---- chat (SSE) ----------------------------------------------------------
let sessionId = null;
const transcript = $('transcript');

function addTurn(who, cls) {
  const el = document.createElement('div');
  el.className = `turn ${cls}`;
  el.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
  transcript.appendChild(el);
  transcript.scrollTop = transcript.scrollHeight;
  return el.querySelector('.body');
}

$('chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('chat-input');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';

  addTurn('you', 'you').textContent = message;
  const body = addTurn('correspondent', 'bot');
  $('chat-directive').textContent = '';

  let res;
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  } catch (err) {
    body.parentElement.classList.add('err');
    body.textContent = `connection failed: ${err.message}`;
    return;
  }

  // Parse the SSE stream by hand — EventSource cannot issue POSTs.
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split('\n\n');
    buffer = frames.pop();          // keep the trailing partial frame

    for (const frame of frames) {
      const evMatch = frame.match(/^event: (.+)$/m);
      const dataMatch = frame.match(/^data: (.+)$/m);
      if (!evMatch || !dataMatch) continue;
      const payload = JSON.parse(dataMatch[1]);

      switch (evMatch[1]) {
        case 'session':
          sessionId = payload.session_id;
          break;
        case 'delta':
          body.textContent += payload.text;
          transcript.scrollTop = transcript.scrollHeight;
          break;
        case 'error':
          body.parentElement.classList.add('err');
          body.textContent += `\n[${payload.kind}: ${payload.message}]`;
          break;
        case 'done':
          $('chat-directive').textContent = describeDirective(payload.directive);
          break;
      }
    }
  }
});

$('chat-reset').addEventListener('click', async () => {
  if (sessionId) await fetch(`/api/session/${sessionId}`, { method: 'DELETE' });
  sessionId = null;
  transcript.innerHTML = '';
  $('chat-directive').textContent = '';
});

// Enter sends, Shift+Enter breaks the line.
$('chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('chat-form').requestSubmit();
  }
});

// ---- compose -------------------------------------------------------------
$('compose-go').addEventListener('click', async () => {
  const assignment = $('compose-input').value.trim();
  if (!assignment) return;
  const out = $('compose-out');
  const btn = $('compose-go');
  busy(btn, true, 'Filing…');
  out.innerHTML = '<p class="spinner">Working. Long-form with the revision loop can take a minute or two.</p>';

  const seedRaw = $('compose-seed').value;
  try {
    const data = await postJSON('/api/compose', {
      assignment,
      seed: seedRaw ? Number(seedRaw) : null,
      judge: $('compose-judge').checked,
      revise: $('compose-revise').checked,
    });
    out.innerHTML =
      `<div class="prose">${escapeHTML(data.text)}</div>` +
      `<p class="note">${escapeHTML(describeDirective(data.directive))} · revision ${data.revision}</p>` +
      renderReport(data.report);
  } catch (err) {
    out.innerHTML = `<p class="note warn">${escapeHTML(err.message)}</p>`;
  } finally {
    busy(btn, false);
  }
});

// ---- transfer ------------------------------------------------------------
$('transfer-go').addEventListener('click', async () => {
  const text = $('transfer-input').value.trim();
  if (!text) return;
  const out = $('transfer-out');
  const btn = $('transfer-go');
  busy(btn, true, 'Restyling…');
  out.innerHTML = '<p class="spinner">Working…</p>';

  const seedRaw = $('transfer-seed').value;
  try {
    const data = await postJSON('/api/transfer', {
      text,
      seed: seedRaw ? Number(seedRaw) : null,
    });
    let warn = '';
    if (!data.facts_preserved) {
      warn = `<p class="note warn">Numbers in the source not found in the output — ` +
        `may be spelled out, check them: ${escapeHTML(data.dropped_facts.join(', '))}</p>`;
    }
    out.innerHTML = `<div class="prose">${escapeHTML(data.text)}</div>${warn}` + renderReport(data.report);
  } catch (err) {
    out.innerHTML = `<p class="note warn">${escapeHTML(err.message)}</p>`;
  } finally {
    busy(btn, false);
  }
});

// ---- score ---------------------------------------------------------------
$('score-go').addEventListener('click', async () => {
  const text = $('score-input').value.trim();
  if (!text) return;
  const out = $('score-out');
  const btn = $('score-go');
  busy(btn, true, 'Scoring…');
  out.innerHTML = '<p class="spinner">Working…</p>';

  try {
    const data = await postJSON('/api/score', { text, judge: $('score-judge').checked });
    out.innerHTML = renderReport(data);
  } catch (err) {
    out.innerHTML = `<p class="note warn">${escapeHTML(err.message)}</p>`;
  } finally {
    busy(btn, false);
  }
});
