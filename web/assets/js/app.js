/**
 * VIBE FORGE WEB CLIENT
 * Handles real-time WebSocket streaming, prompt linting, provider health,
 * and artifact diff rendering.
 */

(() => {
  // --- DOM Elements ---
  const el = {
    providerSelect: document.getElementById('provider-select'),
    providerHealthDot: document.getElementById('provider-health-dot'),
    providerStatusText: document.getElementById('provider-status-text'),
    modelSelect: document.getElementById('model-select'),
    systemPromptInput: document.getElementById('system-prompt-input'),
    userPromptInput: document.getElementById('user-prompt-input'),
    btnLintPrompt: document.getElementById('btn-lint-prompt'),
    btnExecute: document.getElementById('btn-execute'),
    btnStop: document.getElementById('btn-stop'),
    btnClear: document.getElementById('btn-clear'),
    lintScoreBadge: document.getElementById('lint-score-badge'),
    lintScoreVal: document.getElementById('lint-score-val'),
    tokenEstimateBadge: document.getElementById('token-estimate-badge'),
    linterIssuesList: document.getElementById('linter-issues-list'),
    terminalOutput: document.getElementById('terminal-output'),
    diffContainer: document.getElementById('diff-container'),
    rawJsonOutput: document.getElementById('raw-json-output'),
    taskStatusBadge: document.getElementById('task-status-badge'),
    statTokens: document.getElementById('stat-tokens'),
    statDuration: document.getElementById('stat-duration'),
    apiHealthBadge: document.getElementById('api-health-badge')
  };

  let providers = [];
  let activeSocket = null;
  let executionStartTime = null;
  let timerInterval = null;
  let accumulatedOutput = "";
  let rawEvents = [];

  // --- Presets ---
  const PRESETS = {
    refactor: {
      system: "You are a Principal Software Architect. Focus on clean code, domain isolation, and resilient patterns. Provide a unified diff.",
      prompt: "Refactor the database connection pool to support automatic retry backoff on connection timeouts."
    },
    review: {
      system: "You are a Senior Security & Performance Reviewer. Be concise, rigorous, and identify edge cases.",
      prompt: "Review the authentication token exchange mechanism for timing attacks and token leakage in logs."
    },
    tests: {
      system: "You are a Lead QA Engineer specialized in property-based testing and contract verification.",
      prompt: "Generate an exhaustive parameterized test suite verifying all task state machine transitions."
    }
  };

  // --- API Client ---
  const API = {
    async fetchProviders() {
      try {
        const res = await fetch('/api/providers');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        console.warn('Using fallback provider list:', err);
        return [{
          id: 'fake',
          display_name: 'Fake (offline demo)',
          state: 'ready',
          health: { ok: true, reason: null },
          capabilities: { models: ['fake-model'] }
        }];
      }
    },

    async lintPrompt(userPrompt, systemInstructions) {
      try {
        const res = await fetch('/api/prompts/lint', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_prompt: userPrompt, system_instructions: systemInstructions })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        console.error('Linting error:', err);
        return null;
      }
    }
  };

  // --- UI Updates ---
  function updateProviderUI() {
    const selectedId = el.providerSelect.value;
    const provider = providers.find(p => p.id === selectedId);
    if (!provider) return;

    const isReady = provider.state === 'ready' && provider.health && provider.health.ok;
    el.providerHealthDot.className = `badge-dot ${isReady ? 'active' : ''}`;
    el.providerStatusText.textContent = isReady ? 'Ready' : (provider.health?.reason || provider.state);

    // Update models
    el.modelSelect.innerHTML = '';
    const models = provider.capabilities?.models || [];
    if (models.length > 0) {
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        el.modelSelect.appendChild(opt);
      });
    } else {
      const opt = document.createElement('option');
      opt.value = 'default';
      opt.textContent = 'Default Engine Model';
      el.modelSelect.appendChild(opt);
    }
  }

  function renderLintReport(report) {
    if (!report) return;

    el.lintScoreVal.textContent = report.score;
    el.lintScoreBadge.className = 'score-pill ' + (report.score >= 80 ? 'good' : report.score >= 50 ? 'warn' : 'bad');
    el.tokenEstimateBadge.textContent = `~${report.token_estimate} tokens`;

    el.linterIssuesList.innerHTML = '';
    if (report.issues.length === 0) {
      const item = document.createElement('div');
      item.className = 'issue-item info';
      item.innerHTML = '<strong>✨ Excellent prompt!</strong> Adheres to all quality and security dimensions.';
      el.linterIssuesList.appendChild(item);
    } else {
      report.issues.forEach(issue => {
        const item = document.createElement('div');
        item.className = `issue-item ${issue.severity}`;
        item.innerHTML = `
          <div style="display:flex; justify-content:space-between;">
            <span class="issue-code">${issue.rule_id}</span>
            <span style="font-size:10px; text-transform:uppercase;">${issue.severity}</span>
          </div>
          <div>${issue.message}</div>
          <div style="color:#94a3b8; font-size:10.5px;">💡 ${issue.suggestion}</div>
        `;
        el.linterIssuesList.appendChild(item);
      });
    }
  }

  function renderDiff(diffText) {
    el.diffContainer.innerHTML = '';
    const lines = diffText.split('\n');
    lines.forEach(line => {
      const lineDiv = document.createElement('div');
      lineDiv.className = 'diff-line';
      if (line.startsWith('+') && !line.startsWith('+++')) {
        lineDiv.classList.add('add');
      } else if (line.startsWith('-') && !line.startsWith('---')) {
        lineDiv.classList.add('del');
      } else if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')) {
        lineDiv.classList.add('header');
      }
      lineDiv.textContent = line || ' ';
      el.diffContainer.appendChild(lineDiv);
    });
  }

  function appendTerminal(type, text, attempt = 1) {
    const line = document.createElement('div');
    line.className = `terminal-line ${type}`;

    if (type === 'status') {
      line.textContent = `[STATUS] ${text} (attempt ${attempt})`;
    } else if (type === 'thought') {
      line.textContent = `💭 [THOUGHT] ${text}`;
    } else if (type === 'token') {
      line.textContent = text;
    } else if (type === 'error') {
      line.textContent = `❌ [ERROR] ${text}`;
    } else {
      line.textContent = text;
    }

    el.terminalOutput.appendChild(line);
    el.terminalOutput.scrollTop = el.terminalOutput.scrollHeight;
  }

  // --- Execution Workflow ---
  async function runVibe() {
    const providerId = el.providerSelect.value;
    const userPrompt = el.userPromptInput.value.trim();
    const systemPrompt = el.systemPromptInput.value.trim();
    const model = el.modelSelect.value;

    if (!userPrompt) {
      alert('Please enter a prompt intention before executing.');
      return;
    }

    // Reset UI
    el.btnExecute.disabled = true;
    el.btnStop.disabled = false;
    el.taskStatusBadge.textContent = 'RUNNING';
    el.taskStatusBadge.style.color = 'var(--accent-cyan)';
    el.terminalOutput.innerHTML = '';
    el.diffContainer.innerHTML = '<div style="color:#64748b; padding:16px;">No artifacts generated yet...</div>';
    accumulatedOutput = "";
    rawEvents = [];
    el.rawJsonOutput.textContent = '';

    executionStartTime = Date.now();
    timerInterval = setInterval(() => {
      const elapsed = ((Date.now() - executionStartTime) / 1000).toFixed(1);
      el.statDuration.textContent = `${elapsed}s`;
    }, 100);

    appendTerminal('status', 'Connecting to execution engine...', 1);

    // WebSocket Stream
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/execute`;

    try {
      activeSocket = new WebSocket(wsUrl);

      activeSocket.onopen = () => {
        appendTerminal('status', `Dispatched to [${providerId}]`, 1);
        activeSocket.send(JSON.stringify({
          provider_id: providerId,
          user_prompt: userPrompt,
          system_instructions: systemPrompt,
          model: model
        }));
      };

      activeSocket.onmessage = (event) => {
        const frame = JSON.parse(event.data);
        rawEvents.push(frame);
        el.rawJsonOutput.textContent = JSON.stringify(rawEvents, null, 2);

        if (frame.type === 'status') {
          appendTerminal('status', frame.data?.state || 'in progress', frame.attempt);
        } else if (frame.type === 'thought') {
          appendTerminal('thought', frame.data?.text || '');
        } else if (frame.type === 'token') {
          accumulatedOutput += frame.data?.text || '';
          let lastTokenLine = el.terminalOutput.querySelector('.token-stream-container');
          if (!lastTokenLine) {
            lastTokenLine = document.createElement('div');
            lastTokenLine.className = 'terminal-line token-stream-container';
            lastTokenLine.style.color = '#e2e8f0';
            el.terminalOutput.appendChild(lastTokenLine);
          }
          lastTokenLine.textContent = accumulatedOutput;
          el.terminalOutput.scrollTop = el.terminalOutput.scrollHeight;
        } else if (frame.type === 'artifact') {
          if (frame.data?.inline_content) {
            renderDiff(frame.data.inline_content);
          }
          appendTerminal('status', `Artifact generated: ${frame.data?.name}`);
        } else if (frame.type === 'usage') {
          const promptT = frame.data?.prompt_tokens || 0;
          const compT = frame.data?.completion_tokens || 0;
          el.statTokens.textContent = `${promptT + compT} (${promptT} in / ${compT} out)`;
        } else if (frame.type === 'done') {
          finishExecution('SUCCEEDED');
        } else if (frame.type === 'error') {
          appendTerminal('error', frame.data?.message || 'Unknown error');
          finishExecution('FAILED');
        }
      };

      activeSocket.onerror = (err) => {
        console.error('WebSocket error:', err);
        appendTerminal('error', 'WebSocket connection failed.');
        finishExecution('FAILED');
      };

      activeSocket.onclose = () => {
        if (el.taskStatusBadge.textContent === 'RUNNING') {
          finishExecution('FINISHED');
        }
      };

    } catch (err) {
      appendTerminal('error', `Failed to open socket: ${err}`);
      finishExecution('FAILED');
    }
  }

  function finishExecution(status) {
    if (timerInterval) clearInterval(timerInterval);
    el.taskStatusBadge.textContent = status;
    el.taskStatusBadge.style.color = status === 'SUCCEEDED' ? 'var(--accent-emerald)' : 'var(--accent-rose)';
    el.btnExecute.disabled = false;
    el.btnStop.disabled = true;
    if (activeSocket) {
      activeSocket.close();
      activeSocket = null;
    }
  }

  // --- Event Listeners ---
  function setupListeners() {
    el.providerSelect.addEventListener('change', updateProviderUI);

    // Preset buttons
    document.querySelectorAll('.preset-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const key = e.target.getAttribute('data-preset');
        if (PRESETS[key]) {
          el.systemPromptInput.value = PRESETS[key].system;
          el.userPromptInput.value = PRESETS[key].prompt;
          triggerLint();
        }
      });
    });

    // Linter on input
    let lintDebounce = null;
    function triggerLint() {
      clearTimeout(lintDebounce);
      lintDebounce = setTimeout(async () => {
        const report = await API.lintPrompt(el.userPromptInput.value, el.systemPromptInput.value);
        renderLintReport(report);
      }, 350);
    }

    el.userPromptInput.addEventListener('input', triggerLint);
    el.systemPromptInput.addEventListener('input', triggerLint);
    el.btnLintPrompt.addEventListener('click', triggerLint);

    // Action buttons
    el.btnExecute.addEventListener('click', runVibe);
    el.btnStop.addEventListener('click', () => finishExecution('CANCELLED'));
    el.btnClear.addEventListener('click', () => {
      el.userPromptInput.value = '';
      el.terminalOutput.innerHTML = '';
      el.diffContainer.innerHTML = '';
      triggerLint();
    });

    // Dashboard Tab switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

        btn.classList.add('active');
        const tabId = btn.getAttribute('data-tab');
        document.getElementById(tabId).classList.add('active');
      });
    });
  }

  // --- App Initialization ---
  async function init() {
    setupListeners();

    // Load Providers
    providers = await API.fetchProviders();
    el.providerSelect.innerHTML = '';
    providers.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.display_name} (${p.kind})`;
      if (p.state !== 'ready') opt.textContent += ` [${p.state}]`;
      el.providerSelect.appendChild(opt);
    });

    updateProviderUI();
    triggerInitialLint();
  }

  function triggerInitialLint() {
    API.lintPrompt(el.userPromptInput.value, el.systemPromptInput.value).then(renderLintReport);
  }

  window.addEventListener('DOMContentLoaded', init);
})();
