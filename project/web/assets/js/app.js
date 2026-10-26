/**
 * VIBE FORGE WEB CLIENT
 * Meta-Agent Studio: Multi-Workspace Tabbed Interface, Real-time WebSocket Streaming,
 * Prompt Quality Linter, Context Inspector, and Artifact Diff Rendering.
 */

(() => {
  // --- DOM Elements ---
  const el = {
    // Header & Global Badges
    providerStatusBadge: document.getElementById('provider-status-badge'),
    providerHealthDot: document.getElementById('provider-health-dot'),
    providerStatusText: document.getElementById('provider-status-text'),
    apiHealthBadge: document.getElementById('api-health-badge'),

    // Workspace Navigation & Card
    workspaceTabs: document.getElementById('workspace-tabs'),
    btnAddWorkspace: document.getElementById('btn-add-workspace'),
    wsBadgeSelf: document.getElementById('ws-badge-self'),
    wsActiveName: document.getElementById('ws-active-name'),
    wsActiveId: document.getElementById('ws-active-id'),
    wsMetaRuntime: document.getElementById('ws-meta-runtime'),
    wsMetaTest: document.getElementById('ws-meta-test'),
    wsActivePath: document.getElementById('ws-active-path'),
    statWorkspaceLabel: document.getElementById('stat-workspace-label'),

    // Prompt Studio
    providerSelect: document.getElementById('provider-select'),
    modelSelect: document.getElementById('model-select'),
    systemPromptInput: document.getElementById('system-prompt-input'),
    userPromptInput: document.getElementById('user-prompt-input'),
    btnLintPrompt: document.getElementById('btn-lint-prompt'),
    btnExecute: document.getElementById('btn-execute'),
    btnStop: document.getElementById('btn-stop'),
    btnClear: document.getElementById('btn-clear'),

    // Linter Widget
    lintScoreBadge: document.getElementById('lint-score-badge'),
    lintScoreVal: document.getElementById('lint-score-val'),
    tokenEstimateBadge: document.getElementById('token-estimate-badge'),
    linterIssuesList: document.getElementById('linter-issues-list'),

    // Dashboard Tabs & Panes
    taskStatusBadge: document.getElementById('task-status-badge'),
    terminalOutput: document.getElementById('terminal-output'),
    diffContainer: document.getElementById('diff-container'),
    rawJsonOutput: document.getElementById('raw-json-output'),
    statTokens: document.getElementById('stat-tokens'),
    statDuration: document.getElementById('stat-duration'),

    // Context & Tree Inspector
    inspectorManifestJson: document.getElementById('inspector-manifest-json'),
    inspectorContextChips: document.getElementById('inspector-context-chips'),
    inspectorDocPreview: document.getElementById('inspector-doc-preview'),
    inspectorTreeView: document.getElementById('inspector-tree-view'),

    // Modal: Register Workspace
    modalAddWorkspace: document.getElementById('modal-add-workspace'),
    btnCloseModal: document.getElementById('btn-close-modal'),
    btnCancelModal: document.getElementById('btn-cancel-modal'),
    btnConfirmAddWs: document.getElementById('btn-confirm-add-ws'),
    wsInputPath: document.getElementById('ws-input-path'),
    wsInputId: document.getElementById('ws-input-id'),
    wsInputName: document.getElementById('ws-input-name'),
    wsInputPersist: document.getElementById('ws-input-persist'),
    modalError: document.getElementById('modal-error')
  };

  // --- Multi-Workspace & Global State ---
  let providers = [];
  let workspaces = []; // list of summary items from /api/workspaces
  let activeWorkspaceId = 'root';

  // State store keyed by workspace_id
  const workspaceStateStore = {};

  let activeSocket = null;
  let socketTargetWsId = null;
  let executionStartTime = null;
  let timerInterval = null;

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

    async fetchWorkspaces() {
      try {
        const res = await fetch('/api/workspaces');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const list = await res.json();
        return list.map(ws => ({
          id: ws.project_id || ws.id,
          project_id: ws.project_id || ws.id,
          name: ws.name,
          root_path: ws.root_path,
          is_self: Boolean(ws.is_self),
          manifest: ws.manifest,
          runtime: ws.manifest?.runtime || 'custom',
          version: ws.manifest?.version || '0.1.0',
          test_command: ws.manifest?.test_command || null,
          context_files: ws.manifest?.context_files || []
        }));
      } catch (err) {
        console.error('Failed to load workspaces:', err);
        return [{
          id: 'root',
          project_id: 'root',
          name: 'Vibe Forge',
          root_path: 'C:\\Dev\\AI-Agents-App',
          is_self: true,
          runtime: 'python:3.10',
          version: '0.1.0',
          test_command: 'pytest backend/tests',
          context_files: ['README.md', 'AGENTS.md']
        }];
      }
    },

    async fetchWorkspaceDetails(workspaceId) {
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        console.error(`Failed to fetch details for workspace ${workspaceId}:`, err);
        return null;
      }
    },

    async fetchWorkspaceTree(workspaceId) {
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/tree`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        console.error(`Failed to fetch tree for workspace ${workspaceId}:`, err);
        return { tree: ['[Error loading file tree]'] };
      }
    },

    async registerWorkspace(payload) {
      const res = await fetch('/api/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail?.error || data.error || `HTTP ${res.status}`);
      }
      return data;
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

  // --- State Initialization Helper ---
  function getOrCreateWorkspaceState(wsId, wsInfo = null) {
    if (!workspaceStateStore[wsId]) {
      const isSelf = wsInfo?.is_self ?? (wsId === 'root' || wsId === 'self');
      workspaceStateStore[wsId] = {
        userPrompt: isSelf
          ? "Refactor the database connection pool to support automatic retry backoff on connection timeouts."
          : `Analyze and improve the codebase architecture for ${wsInfo?.name || wsId}.`,
        systemPrompt: "You are a Principal Software Architect. Focus on clean code, domain isolation, and resilient patterns. Output unified diffs where appropriate.",
        providerId: el.providerSelect?.value || 'fake',
        model: '',
        terminalHtml: `<div class="terminal-line status">Workspace [${wsInfo?.name || wsId}] initialized. Ready to execute sandboxed tasks.</div>`,
        diffHtml: '<div style="color:#64748b; padding:16px;">No artifacts generated yet...</div>',
        rawEvents: [],
        accumulatedOutput: '',
        statTokens: '0 (0 in / 0 out)',
        statDuration: '0.0s',
        taskStatus: 'IDLE',
        taskStatusColor: 'var(--text-muted)',
        manifest: null,
        contextSummaries: {},
        tree: []
      };
    }
    return workspaceStateStore[wsId];
  }

  // --- UI Updates ---
  function updateProviderUI() {
    const selectedId = el.providerSelect.value;
    const provider = providers.find(p => p.id === selectedId);
    if (!provider) return;

    const isReady = provider.state === 'ready' && provider.health && provider.health.ok;
    el.providerHealthDot.className = `badge-dot ${isReady ? 'active' : ''}`;
    el.providerStatusText.textContent = isReady ? 'Ready' : (provider.health?.reason || provider.state);

    // Update models
    const currentModelVal = el.modelSelect.value;
    el.modelSelect.innerHTML = '';
    const models = provider.capabilities?.models || [];
    if (models.length > 0) {
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        if (m === currentModelVal) opt.selected = true;
        el.modelSelect.appendChild(opt);
      });
    } else {
      const opt = document.createElement('option');
      opt.value = '';
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

  function appendTerminal(type, text, attempt = 1, targetWsId = activeWorkspaceId) {
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

    const state = getOrCreateWorkspaceState(targetWsId);
    if (targetWsId === activeWorkspaceId) {
      el.terminalOutput.appendChild(line);
      el.terminalOutput.scrollTop = el.terminalOutput.scrollHeight;
      state.terminalHtml = el.terminalOutput.innerHTML;
    } else {
      state.terminalHtml += line.outerHTML;
    }
  }

  // --- Workspace Tabs & Context Rendering ---
  function renderWorkspaceTabs() {
    el.workspaceTabs.innerHTML = '';
    workspaces.forEach(ws => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `ws-tab ${ws.id === activeWorkspaceId ? 'active' : ''} ${ws.is_self ? 'self' : ''}`;
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-selected', ws.id === activeWorkspaceId ? 'true' : 'false');
      btn.setAttribute('data-ws-id', ws.id);

      const label = document.createElement('span');
      label.textContent = ws.name || ws.id;
      btn.appendChild(label);

      if (ws.is_self) {
        const badge = document.createElement('span');
        badge.className = 'ws-badge self';
        badge.textContent = 'SELF';
        btn.appendChild(badge);
      }

      btn.addEventListener('click', () => switchWorkspace(ws.id));
      el.workspaceTabs.appendChild(btn);
    });
  }

  function saveCurrentWorkspaceState() {
    if (!activeWorkspaceId) return;
    const state = getOrCreateWorkspaceState(activeWorkspaceId);
    state.userPrompt = el.userPromptInput.value;
    state.systemPrompt = el.systemPromptInput.value;
    state.providerId = el.providerSelect.value;
    state.model = el.modelSelect.value;
    state.terminalHtml = el.terminalOutput.innerHTML;
    state.diffHtml = el.diffContainer.innerHTML;
    state.taskStatus = el.taskStatusBadge.textContent;
    state.taskStatusColor = el.taskStatusBadge.style.color;
    state.statTokens = el.statTokens.textContent;
    state.statDuration = el.statDuration.textContent;
  }

  async function switchWorkspace(wsId) {
    if (wsId === activeWorkspaceId && workspaceStateStore[wsId]?.manifest) {
      return;
    }

    // Save previous active state
    saveCurrentWorkspaceState();

    activeWorkspaceId = wsId;
    const wsInfo = workspaces.find(w => w.id === wsId) || { id: wsId, name: wsId };
    const state = getOrCreateWorkspaceState(wsId, wsInfo);

    // Update Tab UI
    document.querySelectorAll('.ws-tab').forEach(t => {
      const isCurrent = t.getAttribute('data-ws-id') === wsId;
      t.classList.toggle('active', isCurrent);
      t.setAttribute('aria-selected', isCurrent ? 'true' : 'false');
    });

    // Update Context Card
    el.wsActiveName.textContent = wsInfo.name || wsId;
    el.wsActiveId.textContent = `#${wsId}`;
    el.wsActivePath.textContent = wsInfo.root_path || 'resolving...';
    el.wsBadgeSelf.style.display = wsInfo.is_self ? 'inline-block' : 'none';
    el.wsMetaRuntime.textContent = wsInfo.runtime || 'custom';
    el.wsMetaTest.textContent = wsInfo.test_command || 'no test spec';
    el.statWorkspaceLabel.textContent = `${wsInfo.name || wsId} (${wsInfo.is_self ? 'Self' : wsId})`;

    // Restore Prompt Studio & Execution Pane
    el.userPromptInput.value = state.userPrompt;
    el.systemPromptInput.value = state.systemPrompt;
    if (state.providerId && Array.from(el.providerSelect.options).some(o => o.value === state.providerId)) {
      el.providerSelect.value = state.providerId;
    }
    updateProviderUI();
    if (state.model) el.modelSelect.value = state.model;

    el.terminalOutput.innerHTML = state.terminalHtml;
    el.diffContainer.innerHTML = state.diffHtml;
    el.rawJsonOutput.textContent = state.rawEvents.length > 0 ? JSON.stringify(state.rawEvents, null, 2) : '';
    el.taskStatusBadge.textContent = state.taskStatus;
    el.taskStatusBadge.style.color = state.taskStatusColor;
    el.statTokens.textContent = state.statTokens;
    el.statDuration.textContent = state.statDuration;

    triggerLint();

    // Load detailed context & tree in background
    loadWorkspaceContextAndTree(wsId);
  }

  async function loadWorkspaceContextAndTree(wsId) {
    const state = getOrCreateWorkspaceState(wsId);

    // 1. Fetch details
    if (!state.manifest) {
      el.inspectorManifestJson.textContent = 'Loading manifest and context summaries...';
      const details = await API.fetchWorkspaceDetails(wsId);
      if (details) {
        state.manifest = details.manifest;
        state.contextSummaries = details.context_summaries || {};
        if (details.root_path) el.wsActivePath.textContent = details.root_path;
      }
    }

    if (activeWorkspaceId === wsId && state.manifest) {
      el.inspectorManifestJson.textContent = JSON.stringify(state.manifest, null, 2);

      // Context Chips
      el.inspectorContextChips.innerHTML = '';
      const contextFiles = state.manifest.context_files || [];
      if (contextFiles.length === 0) {
        el.inspectorContextChips.innerHTML = '<span style="color:#64748b; font-size:11px;">No context files defined in manifest.</span>';
        el.inspectorDocPreview.textContent = 'No context files configured.';
      } else {
        contextFiles.forEach((file, idx) => {
          const chip = document.createElement('button');
          chip.type = 'button';
          chip.className = `btn-chip ${idx === 0 ? 'active' : ''}`;
          chip.textContent = `📄 ${file}`;
          chip.addEventListener('click', () => {
            document.querySelectorAll('#inspector-context-chips .btn-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            renderDocPreview(file, state.contextSummaries[file]);
          });
          el.inspectorContextChips.appendChild(chip);
        });

        // Preview first file by default
        renderDocPreview(contextFiles[0], state.contextSummaries[contextFiles[0]]);
      }
    }

    // 2. Fetch Tree
    if (!state.tree || state.tree.length === 0) {
      el.inspectorTreeView.textContent = 'Scanning directory tree...';
      const treeData = await API.fetchWorkspaceTree(wsId);
      if (Array.isArray(treeData.tree)) {
        state.tree = treeData.tree;
      } else if (treeData && (treeData.children || treeData.name)) {
        state.tree = [
          `📁 ${treeData.name || wsId}/`,
          ...formatTreeNode(treeData, '')
        ];
      } else {
        state.tree = ['[Empty directory or no files]'];
      }
    }

    if (activeWorkspaceId === wsId) {
      el.inspectorTreeView.textContent = state.tree.length > 0 ? state.tree.join('\n') : 'Empty directory or all files ignored.';
    }
  }

  function formatTreeNode(node, prefix = '') {
    if (!node || !node.children) return [];
    const lines = [];
    const children = node.children || [];
    children.forEach((child, idx) => {
      const isLast = idx === children.length - 1;
      const branch = isLast ? '└── ' : '├── ';
      const icon = child.type === 'directory' ? '📁 ' : '📄 ';
      lines.push(prefix + branch + icon + child.name);
      if (child.children && child.children.length > 0) {
        lines.push(...formatTreeNode(child, prefix + (isLast ? '    ' : '│   ')));
      }
    });
    return lines;
  }

  function renderDocPreview(filename, content) {
    if (!content) {
      el.inspectorDocPreview.textContent = `[File: ${filename}] (Empty or not found)`;
    } else {
      el.inspectorDocPreview.textContent = `--- ${filename} ---\n` + content;
    }
  }

  // --- Modal Logic ---
  function openModal() {
    el.modalAddWorkspace.style.display = 'flex';
    el.modalAddWorkspace.setAttribute('aria-hidden', 'false');
    el.modalError.style.display = 'none';
    el.modalError.textContent = '';
    el.wsInputPath.value = '';
    el.wsInputId.value = '';
    el.wsInputName.value = '';
    el.wsInputPath.focus();
  }

  function closeModal() {
    el.modalAddWorkspace.style.display = 'none';
    el.modalAddWorkspace.setAttribute('aria-hidden', 'true');
  }

  async function handleConfirmAddWorkspace() {
    const path = el.wsInputPath.value.trim();
    const id = el.wsInputId.value.trim() || undefined;
    const name = el.wsInputName.value.trim() || undefined;
    const persist = el.wsInputPersist.checked;

    if (!path) {
      el.modalError.textContent = 'Please enter a target directory path.';
      el.modalError.style.display = 'block';
      return;
    }

    el.btnConfirmAddWs.disabled = true;
    el.btnConfirmAddWs.textContent = 'Registering...';
    el.modalError.style.display = 'none';

    try {
      const res = await API.registerWorkspace({
        path: path,
        project_id: id,
        name: name,
        persist_manifest: persist
      });

      const raw = res.workspace || res;
      const registered = {
        id: raw.project_id || raw.id,
        project_id: raw.project_id || raw.id,
        name: raw.name,
        root_path: raw.root_path,
        is_self: Boolean(raw.is_self),
        manifest: raw.manifest,
        runtime: raw.manifest?.runtime || 'custom',
        version: raw.manifest?.version || '0.1.0',
        test_command: raw.manifest?.test_command || null,
        context_files: raw.manifest?.context_files || []
      };

      // Replace or append
      const existingIdx = workspaces.findIndex(w => w.id === registered.id);
      if (existingIdx >= 0) {
        workspaces[existingIdx] = registered;
      } else {
        workspaces.push(registered);
      }

      renderWorkspaceTabs();
      closeModal();
      await switchWorkspace(registered.id);
    } catch (err) {
      el.modalError.textContent = err.message || 'Failed to register workspace.';
      el.modalError.style.display = 'block';
    } finally {
      el.btnConfirmAddWs.disabled = false;
      el.btnConfirmAddWs.textContent = 'Register & Open Workspace';
    }
  }

  // --- Execution Workflow ---
  async function runVibe() {
    const wsId = activeWorkspaceId;
    const wsState = getOrCreateWorkspaceState(wsId);
    const providerId = el.providerSelect.value;
    const userPrompt = el.userPromptInput.value.trim();
    const systemPrompt = el.systemPromptInput.value.trim();
    const model = el.modelSelect.value || null;

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
    el.rawJsonOutput.textContent = '';

    wsState.accumulatedOutput = '';
    wsState.rawEvents = [];
    wsState.taskStatus = 'RUNNING';
    wsState.taskStatusColor = 'var(--accent-cyan)';

    executionStartTime = Date.now();
    timerInterval = setInterval(() => {
      const elapsed = ((Date.now() - executionStartTime) / 1000).toFixed(1);
      el.statDuration.textContent = `${elapsed}s`;
      wsState.statDuration = `${elapsed}s`;
    }, 100);

    appendTerminal('status', `Connecting to execution engine for workspace [${wsId}]...`, 1, wsId);

    // WebSocket Stream scoped to workspace
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/workspaces/${encodeURIComponent(wsId)}/events`;

    try {
      socketTargetWsId = wsId;
      activeSocket = new WebSocket(wsUrl);

      activeSocket.onopen = () => {
        appendTerminal('status', `Dispatched to [${providerId}] in workspace [${wsId}]`, 1, wsId);
        activeSocket.send(JSON.stringify({
          provider_id: providerId,
          user_prompt: userPrompt,
          system_instructions: systemPrompt,
          model: model,
          workspace_id: wsId
        }));
      };

      activeSocket.onmessage = (event) => {
        const frame = JSON.parse(event.data);
        const targetState = getOrCreateWorkspaceState(socketTargetWsId);
        targetState.rawEvents.push(frame);

        if (socketTargetWsId === activeWorkspaceId) {
          el.rawJsonOutput.textContent = JSON.stringify(targetState.rawEvents, null, 2);
        }

        if (frame.type === 'status') {
          appendTerminal('status', frame.data?.state || 'in progress', frame.attempt, socketTargetWsId);
        } else if (frame.type === 'thought') {
          appendTerminal('thought', frame.data?.text || '', 1, socketTargetWsId);
        } else if (frame.type === 'token') {
          targetState.accumulatedOutput += frame.data?.text || '';
          if (socketTargetWsId === activeWorkspaceId) {
            let lastTokenLine = el.terminalOutput.querySelector('.token-stream-container');
            if (!lastTokenLine) {
              lastTokenLine = document.createElement('div');
              lastTokenLine.className = 'terminal-line token-stream-container';
              lastTokenLine.style.color = '#e2e8f0';
              el.terminalOutput.appendChild(lastTokenLine);
            }
            lastTokenLine.textContent = targetState.accumulatedOutput;
            el.terminalOutput.scrollTop = el.terminalOutput.scrollHeight;
            targetState.terminalHtml = el.terminalOutput.innerHTML;
          }
        } else if (frame.type === 'artifact') {
          if (frame.data?.inline_content) {
            if (socketTargetWsId === activeWorkspaceId) {
              renderDiff(frame.data.inline_content);
              targetState.diffHtml = el.diffContainer.innerHTML;
            } else {
              targetState.diffHtml = `<pre class="diff-line header">${frame.data.name}</pre>`;
            }
          }
          appendTerminal('status', `Artifact generated: ${frame.data?.name}`, 1, socketTargetWsId);
        } else if (frame.type === 'usage') {
          const promptT = frame.data?.prompt_tokens || 0;
          const compT = frame.data?.completion_tokens || 0;
          const usageStr = `${promptT + compT} (${promptT} in / ${compT} out)`;
          targetState.statTokens = usageStr;
          if (socketTargetWsId === activeWorkspaceId) {
            el.statTokens.textContent = usageStr;
          }
        } else if (frame.type === 'done') {
          finishExecution('SUCCEEDED', socketTargetWsId);
        } else if (frame.type === 'error') {
          appendTerminal('error', frame.data?.message || 'Unknown error', 1, socketTargetWsId);
          finishExecution('FAILED', socketTargetWsId);
        }
      };

      activeSocket.onerror = (err) => {
        console.error('WebSocket error:', err);
        appendTerminal('error', 'WebSocket connection failed.', 1, socketTargetWsId);
        finishExecution('FAILED', socketTargetWsId);
      };

      activeSocket.onclose = () => {
        const targetState = getOrCreateWorkspaceState(socketTargetWsId);
        if (targetState.taskStatus === 'RUNNING') {
          finishExecution('FINISHED', socketTargetWsId);
        }
      };

    } catch (err) {
      appendTerminal('error', `Failed to open socket: ${err}`, 1, wsId);
      finishExecution('FAILED', wsId);
    }
  }

  function finishExecution(status, wsId = activeWorkspaceId) {
    if (timerInterval) clearInterval(timerInterval);
    const targetState = getOrCreateWorkspaceState(wsId);
    targetState.taskStatus = status;
    targetState.taskStatusColor = status === 'SUCCEEDED' ? 'var(--accent-emerald)' : 'var(--accent-rose)';

    if (wsId === activeWorkspaceId) {
      el.taskStatusBadge.textContent = status;
      el.taskStatusBadge.style.color = targetState.taskStatusColor;
      el.btnExecute.disabled = false;
      el.btnStop.disabled = true;
    }

    if (activeSocket) {
      activeSocket.close();
      activeSocket = null;
      socketTargetWsId = null;
    }
  }

  // --- Linter Debounce ---
  let lintDebounce = null;
  function triggerLint() {
    clearTimeout(lintDebounce);
    lintDebounce = setTimeout(async () => {
      const report = await API.lintPrompt(el.userPromptInput.value, el.systemPromptInput.value);
      renderLintReport(report);
    }, 350);
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

    el.userPromptInput.addEventListener('input', triggerLint);
    el.systemPromptInput.addEventListener('input', triggerLint);
    el.btnLintPrompt.addEventListener('click', triggerLint);

    // Action buttons
    el.btnExecute.addEventListener('click', runVibe);
    el.btnStop.addEventListener('click', () => finishExecution('CANCELLED', activeWorkspaceId));
    el.btnClear.addEventListener('click', () => {
      el.userPromptInput.value = '';
      el.terminalOutput.innerHTML = '';
      el.diffContainer.innerHTML = '';
      const state = getOrCreateWorkspaceState(activeWorkspaceId);
      state.terminalHtml = '';
      state.diffHtml = '';
      state.userPrompt = '';
      triggerLint();
    });

    // Dashboard Tab switching (Live Console / Diff / Context / Raw)
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

        btn.classList.add('active');
        const tabId = btn.getAttribute('data-tab');
        const targetPane = document.getElementById(tabId);
        if (targetPane) targetPane.classList.add('active');
      });
    });

    // Modal controls
    el.btnAddWorkspace.addEventListener('click', openModal);
    el.btnCloseModal.addEventListener('click', closeModal);
    el.btnCancelModal.addEventListener('click', closeModal);
    el.btnConfirmAddWs.addEventListener('click', handleConfirmAddWorkspace);
    el.modalAddWorkspace.addEventListener('click', (e) => {
      if (e.target === el.modalAddWorkspace) closeModal();
    });
  }

  // --- App Initialization ---
  async function init() {
    setupListeners();

    // 1. Fetch & Initialize Providers
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

    // 2. Fetch & Initialize Workspaces
    workspaces = await API.fetchWorkspaces();
    renderWorkspaceTabs();

    // 3. Switch to default workspace (Self / root)
    const initialWs = workspaces.find(w => w.is_self) || workspaces[0];
    if (initialWs) {
      await switchWorkspace(initialWs.id);
    } else {
      triggerLint();
    }
  }

  window.addEventListener('DOMContentLoaded', init);
})();
