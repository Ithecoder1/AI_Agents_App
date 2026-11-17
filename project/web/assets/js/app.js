/**
 * VIBE FORGE WEB CLIENT
 * Meta-Agent Studio: Multi-Workspace Tabbed Interface, SQLite-backed Multi-Turn Chat,
 * Real-time WebSocket Streaming, Session Telemetry Bar, and Collapsible Message Inspector.
 */

(() => {
  // Initialize Mermaid with Vibe Forge Dark Theme
  if (window.mermaid) {
    try {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        themeVariables: {
          darkMode: true,
          background: '#0a0f1e',
          primaryColor: '#6366f1',
          primaryTextColor: '#f8fafc',
          primaryBorderColor: '#818cf8',
          lineColor: '#38bdf8',
          secondaryColor: '#1e293b',
          tertiaryColor: '#0f172a'
        }
      });
    } catch (e) {
      console.warn('Failed to initialize Mermaid:', e);
    }
  }

  // --- DOM Elements ---
  const el = {
    // Header & Global Badges
    providerStatusBadge: document.getElementById('provider-status-badge'),
    providerHealthDot: document.getElementById('provider-health-dot'),
    providerStatusText: document.getElementById('provider-status-text'),
    apiHealthBadge: document.getElementById('api-health-badge'),

    // Workspace Navigation & Actions
    workspaceTabs: document.getElementById('workspace-tabs'),
    btnToggleWsDrawer: document.getElementById('btn-toggle-ws-drawer'),
    btnAddWorkspace: document.getElementById('btn-add-workspace'),

    // Chat Sidebar
    chatSessionsList: document.getElementById('chat-sessions-list'),
    btnNewChat: document.getElementById('btn-new-chat'),
    chatSearchInput: document.getElementById('chat-search-input'),

    // Session Telemetry Bar
    sessionTitle: document.getElementById('session-title'),
    btnEditTitle: document.getElementById('btn-edit-title'),
    sessionWorkspaceTag: document.getElementById('session-workspace-tag'),
    providerSelect: document.getElementById('provider-select'),
    modelSelect: document.getElementById('model-select'),
    sessionStatTokens: document.getElementById('session-stat-tokens'),
    sessionStatCost: document.getElementById('session-stat-cost'),
    sessionStatTurns: document.getElementById('session-stat-turns'),

    // Messages Container
    messagesContainer: document.getElementById('messages-container'),
    messagesInner: document.getElementById('messages-inner'),
    chatWelcome: document.getElementById('chat-welcome'),

    // Composer
    systemInstructionsDrawer: document.getElementById('system-instructions-drawer'),
    btnToggleSystem: document.getElementById('btn-toggle-system'),
    systemPromptInput: document.getElementById('system-prompt-input'),
    userPromptInput: document.getElementById('user-prompt-input'),
    lintScoreBadge: document.getElementById('lint-score-badge'),
    lintScoreText: document.getElementById('lint-score-text'),
    tokenEstimateBadge: document.getElementById('token-estimate-badge'),
    requireApprovalCheck: document.getElementById('require-approval-check'),
    btnExecute: document.getElementById('btn-execute'),
    btnStop: document.getElementById('btn-stop'),

    // Workspace Drawer (Right Slide-over)
    wsDrawer: document.getElementById('ws-drawer'),
    btnCloseWsDrawer: document.getElementById('btn-close-ws-drawer'),
    drawerWsName: document.getElementById('drawer-ws-name'),
    wsActivePath: document.getElementById('ws-active-path'),
    wsMetaRuntime: document.getElementById('ws-meta-runtime'),
    wsMetaTest: document.getElementById('ws-meta-test'),
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

  // --- Global Application State ---
  let providers = [];
  let workspaces = [];
  let activeWorkspaceId = 'root';
  let activeSessionId = null;
  let activeSessionData = null; // full session dict with messages & telemetry
  let workspaceSessions = []; // list of session summaries for active workspace

  let activeWebSocket = null;
  let activeStreamMessageId = null;
  let lintDebounceTimer = null;

  // Quick prompt presets
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
          runtime: ws.manifest?.runtime || 'python:3.10',
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
          test_command: 'pytest project/tests',
          context_files: ['README.md', 'AGENTS.md']
        }];
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

    async fetchWorkspaceChats(projectId) {
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(projectId)}/chats`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        console.error(`Failed to load chats for workspace ${projectId}:`, err);
        return [];
      }
    },

    async createChat(projectId, title = null, providerId = 'fake', model = null) {
      const res = await fetch(`/api/workspaces/${encodeURIComponent(projectId)}/chats`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title || undefined,
          provider_id: providerId,
          model: model || undefined
        })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },

    async fetchChatSession(sessionId) {
      const res = await fetch(`/api/chats/${encodeURIComponent(sessionId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },

    async renameChat(sessionId, title) {
      const res = await fetch(`/api/chats/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },

    async deleteChat(sessionId) {
      const res = await fetch(`/api/chats/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE'
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },

    async sendChatMessage(sessionId, payload) {
      const res = await fetch(`/api/chats/${encodeURIComponent(sessionId)}/messages`, {
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

    async cancelChatTurn(sessionId) {
      const res = await fetch(`/api/chats/${encodeURIComponent(sessionId)}/cancel`, {
        method: 'POST'
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },

    async approveChatTurn(sessionId) {
      const res = await fetch(`/api/chats/${encodeURIComponent(sessionId)}/approve`, {
        method: 'POST'
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
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

  // --- Formatters ---
  function formatRelativeTime(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    const now = new Date();
    const diffSec = Math.floor((now - date) / 1000);
    if (diffSec < 60) return 'Just now';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function formatTokens(num) {
    if (num == null) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
    return String(num);
  }

  function formatCost(cost) {
    if (cost == null || isNaN(cost)) return '$0.000';
    return `$${Number(cost).toFixed(4)}`;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function applyInlineMarkdown(escapedLine) {
    // Inline code first so asterisks inside code spans are untouched
    let out = escapedLine.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return out;
  }

  function formatMarkdown(text) {
    if (!text) return '';

    const codePlaceholders = [];
    // Extract fenced code blocks (supports closed fences cleanly)
    let working = text.replace(/```([a-zA-Z0-9_\-\.]*)\n([\s\S]*?)```/g, (match, lang, code) => {
      let blockHtml;
      if (lang === 'diff') {
        const lines = code.split('\n').map(l => {
          let cls = '';
          if (l.startsWith('+') && !l.startsWith('+++')) cls = 'add';
          else if (l.startsWith('-') && !l.startsWith('---')) cls = 'del';
          else if (l.startsWith('@@') || l.startsWith('---') || l.startsWith('+++')) cls = 'header';
          return `<div class="diff-line ${cls}">${escapeHtml(l)}</div>`;
        }).join('');
        blockHtml = `<div class="diff-viewer">${lines}</div>`;
      } else {
        blockHtml = `<pre><code class="language-${escapeHtml(lang)}">${escapeHtml(code)}</code></pre>`;
      }
      const token = `\x00CODEBLOCK_${codePlaceholders.length}\x00`;
      codePlaceholders.push(blockHtml);
      return `\n${token}\n`;
    });

    const lines = working.split(/\r?\n/);
    const output = [];
    let i = 0;

    while (i < lines.length) {
      const rawLine = lines[i];
      const trimmed = rawLine.trim();

      // Code block placeholder line
      const cbMatch = trimmed.match(/^\x00CODEBLOCK_(\d+)\x00$/);
      if (cbMatch) {
        output.push(codePlaceholders[Number(cbMatch[1])]);
        i++;
        continue;
      }

      // Markdown Table (at least 2 consecutive pipe-delimited rows)
      if (trimmed.startsWith('|') && trimmed.endsWith('|') && i + 1 < lines.length && lines[i + 1].trim().startsWith('|')) {
        const tableLines = [];
        while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
          tableLines.push(lines[i].trim());
          i++;
        }
        const parseRow = (rowStr) => rowStr.slice(1, -1).split('|').map(cell => cell.trim());
        const isSeparator = (cells) => cells.every(c => /^:?-{2,}:?$/.test(c));

        const headerCells = parseRow(tableLines[0]);
        const hasSep = tableLines.length > 1 && isSeparator(parseRow(tableLines[1]));
        const bodyRows = tableLines.slice(hasSep ? 2 : 1);

        let tableHtml = '<table class="md-table">';
        if (hasSep) {
          tableHtml += '<thead><tr>' + headerCells.map(c => `<th>${applyInlineMarkdown(escapeHtml(c))}</th>`).join('') + '</tr></thead>';
        } else {
          bodyRows.unshift(tableLines[0]);
        }
        if (bodyRows.length > 0) {
          tableHtml += '<tbody>' + bodyRows.map(r => {
            const cells = parseRow(r);
            return '<tr>' + cells.map(c => `<td>${applyInlineMarkdown(escapeHtml(c))}</td>`).join('') + '</tr>';
          }).join('') + '</tbody>';
        }
        tableHtml += '</table>';
        output.push(tableHtml);
        continue;
      }

      // Horizontal Rule
      if (/^---+$/.test(trimmed)) {
        output.push('<hr class="md-hr"/>');
        i++;
        continue;
      }

      // Headings (# to ####)
      const hMatch = trimmed.match(/^(#{1,4})\s+(.*)$/);
      if (hMatch) {
        const level = hMatch[1].length;
        output.push(`<h${level} class="md-heading md-h${level}">${applyInlineMarkdown(escapeHtml(hMatch[2]))}</h${level}>`);
        i++;
        continue;
      }

      // Blockquote
      if (trimmed.startsWith('> ')) {
        output.push(`<blockquote class="md-quote">${applyInlineMarkdown(escapeHtml(trimmed.slice(2)))}</blockquote>`);
        i++;
        continue;
      }

      // Bullet list item
      if (/^[-*]\s+/.test(trimmed)) {
        output.push(`<div class="md-list-item"><span class="md-bullet">•</span><span>${applyInlineMarkdown(escapeHtml(trimmed.replace(/^[-*]\s+/, '')))}</span></div>`);
        i++;
        continue;
      }

      // Blank line -> visual break if previous element isn't already a block spacer
      if (trimmed === '') {
        if (output.length > 0 && !output[output.length - 1].endsWith('<div class="md-spacer"></div>')) {
          output.push('<div class="md-spacer"></div>');
        }
        i++;
        continue;
      }

      // Regular line
      output.push(`<div class="md-line">${applyInlineMarkdown(escapeHtml(rawLine))}</div>`);
      i++;
    }

    return output.join('');
  }

  // --- UI Renderers ---
  function updateProvidersUI() {
    const selectedId = el.providerSelect.value;
    const provider = providers.find(p => p.id === selectedId) || providers[0];
    if (!provider) return;

    const isReady = provider.state === 'ready' && provider.health && provider.health.ok;
    el.providerHealthDot.className = `badge-dot ${isReady ? 'active' : ''}`;
    el.providerStatusText.textContent = isReady ? 'Ready' : (provider.health?.reason || provider.state);

    // Populate models
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
      opt.textContent = 'Default Model';
      el.modelSelect.appendChild(opt);
    }
  }

  function renderWorkspaceTabs() {
    el.workspaceTabs.innerHTML = '';
    workspaces.forEach(ws => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `ws-tab ${ws.project_id === activeWorkspaceId ? 'active' : ''}`;
      btn.dataset.id = ws.project_id;
      btn.innerHTML = `
        <span>${escapeHtml(ws.name || ws.project_id)}</span>
        ${ws.is_self ? '<span class="ws-tab-badge">SELF</span>' : ''}
      `;
      btn.addEventListener('click', () => switchWorkspace(ws.project_id));
      el.workspaceTabs.appendChild(btn);
    });
  }

  function renderChatSessionsList() {
    const filter = el.chatSearchInput.value.trim().toLowerCase();
    const filtered = workspaceSessions.filter(s =>
      !filter || (s.title && s.title.toLowerCase().includes(filter))
    );

    el.chatSessionsList.innerHTML = '';
    if (filtered.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'sessions-empty';
      empty.textContent = filter ? 'No matching chats found.' : 'No chats yet in this workspace. Click "+ New Chat" above!';
      el.chatSessionsList.appendChild(empty);
      return;
    }

    filtered.forEach(session => {
      const item = document.createElement('div');
      item.className = `session-item ${session.id === activeSessionId ? 'active' : ''}`;
      item.dataset.id = session.id;

      const totalTok = session.telemetry?.total_tokens || 0;
      const msgCount = session.message_count || 0;

      item.innerHTML = `
        <div class="session-item-content">
          <div class="session-item-title">${escapeHtml(session.title || 'Untitled Session')}</div>
          <div class="session-item-meta">
            <span>${formatRelativeTime(session.updated_at || session.created_at)}</span>
            <span class="session-item-badge">${msgCount} msg${msgCount === 1 ? '' : 's'}</span>
            ${totalTok > 0 ? `<span class="session-item-badge">${formatTokens(totalTok)} tok</span>` : ''}
          </div>
        </div>
        <button type="button" class="session-item-delete" title="Delete Session" aria-label="Delete Session">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
      `;

      // Click to select session
      item.addEventListener('click', (e) => {
        if (e.target.closest('.session-item-delete')) return;
        selectChatSession(session.id);
      });

      // Click delete
      const delBtn = item.querySelector('.session-item-delete');
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (confirm(`Delete chat "${session.title}"?`)) {
          await deleteSession(session.id);
        }
      });

      el.chatSessionsList.appendChild(item);
    });
  }

  function updateSessionTelemetryBar() {
    if (!activeSessionData) {
      el.sessionTitle.textContent = 'New Chat';
      el.sessionWorkspaceTag.textContent = `#${activeWorkspaceId}`;
      el.sessionStatTokens.textContent = '0';
      el.sessionStatCost.textContent = '$0.000';
      el.sessionStatTurns.textContent = '0';
      return;
    }

    el.sessionTitle.textContent = activeSessionData.title || 'Untitled Chat';
    el.sessionWorkspaceTag.textContent = `#${activeSessionData.project_id || activeWorkspaceId}`;

    const tel = activeSessionData.telemetry || {};
    const totalTokens = tel.total_tokens || 0;
    const inTokens = tel.total_input_tokens || 0;
    const outTokens = tel.total_output_tokens || 0;
    const reasoningTokens = tel.total_reasoning_tokens || 0;

    el.sessionStatTokens.textContent = formatTokens(totalTokens);
    el.sessionStatTokens.parentElement.title = `Total: ${totalTokens} (In: ${inTokens} · Out: ${outTokens} · Reasoning: ${reasoningTokens})`;
    el.sessionStatCost.textContent = formatCost(tel.total_cost_usd || 0);
    el.sessionStatTurns.textContent = String(activeSessionData.messages ? activeSessionData.messages.length : (activeSessionData.message_count || 0));

    // Align provider & model select
    if (activeSessionData.provider_id && el.providerSelect.querySelector(`option[value="${activeSessionData.provider_id}"]`)) {
      el.providerSelect.value = activeSessionData.provider_id;
      updateProvidersUI();
    }
    if (activeSessionData.model_name && activeSessionData.model_name !== 'default') {
      el.modelSelect.value = activeSessionData.model_name;
    }
  }

  function renderMessagesThread() {
    el.messagesInner.innerHTML = '';
    const messages = activeSessionData?.messages || [];

    if (messages.length === 0) {
      el.messagesInner.appendChild(el.chatWelcome);
      return;
    }

    messages.forEach(msg => {
      const turnDiv = createMessageElement(msg);
      el.messagesInner.appendChild(turnDiv);
    });

    scrollToBottom();
  }

  function buildPlanStepsHtml(steps) {
    const iconFor = (st) => st === 'done' ? '✓' : st === 'in_progress' ? '●' : st === 'failed' ? '✕' : '○';
    return (steps || []).map(s => {
      const st = s.status || 'pending';
      return `
        <div class="plan-step-row ${escapeHtml(st)}">
          <span class="step-status-icon ${escapeHtml(st)}">${iconFor(st)}</span>
          <span class="plan-step-title">${escapeHtml(s.title || '')}</span>
        </div>
      `;
    }).join('');
  }

  function buildTurnMetricsHtml(meta, msgId, extraData = {}) {
    if (!meta) return '';
    const sysInstructions = meta.system_instructions || meta.extensions?.system_instructions || '';
    const activeTools = meta.active_tools || meta.extensions?.active_tools || [];
    const activeToolsCount = meta.active_tools_count ?? meta.extensions?.active_tools_count ?? activeTools.length;
    const injectedSchemas = meta.injected_schemas || meta.extensions?.injected_schemas || [];
    const usedFiles = meta.used_files || meta.extensions?.used_files || [];
    const totalDuration = meta.total_duration ?? meta.extensions?.total_duration ?? meta.extensions?.duration_seconds;
    const usage = meta.usage_metrics || meta.extensions?.usage_metrics || meta.extensions?.usage || null;
    const executedCommand = meta.executed_command || meta.extensions?.executed_command || meta.full_context_payload?.executed_command || extraData.executedCommand || null;
    const fcp = meta.full_context_payload || meta.extensions?.full_context_payload || {
      workspace: meta.workspace || meta.extensions?.workspace || {
        id: activeWorkspaceId,
        name: activeWorkspaceId,
      },
      provider: meta.provider || {
        id: meta.extensions?.provider_id || (activeSessionData?.provider_id || 'antigravity-headless'),
        model: meta.extensions?.model || 'default',
      },
      system_instructions: sysInstructions,
      active_tools: activeTools,
      active_tools_count: activeToolsCount,
      injected_schemas: injectedSchemas,
      used_files: usedFiles,
      total_duration: totalDuration,
      usage_metrics: usage,
      executed_command: executedCommand,
    };
    if (executedCommand && !fcp.executed_command) {
      fcp.executed_command = executedCommand;
    }

    const rawEvents = meta.raw_events || meta.extensions?.raw_events || activeStreamEvents[msgId] || [];
    const rawPayload = {
      full_context_payload: fcp,
      ...(rawEvents && rawEvents.length ? { raw_events: rawEvents } : {}),
      ...(extraData.thoughts?.length ? { thoughts: extraData.thoughts } : {}),
      ...(extraData.toolCalls?.length ? { tool_calls: extraData.toolCalls } : {}),
      ...(extraData.logs?.length ? { logs: extraData.logs } : {}),
      ...(extraData.usage ? { usage: extraData.usage } : {}),
    };

    let html = `
      <div class="turn-metrics-panel" id="metrics-${msgId}">
        <div class="inspector-header-bar">
          <div class="inspector-header-left">
            <span class="inspector-context-badge">FULL CONTEXT PAYLOAD</span>
          </div>
          <div class="inspector-header-right">
            <label class="json-toggle-label" title="Toggle between formatted inspector and raw JSON log stream">
              <input type="checkbox" class="json-toggle-checkbox" data-msg-id="${msgId}" id="toggle-json-${msgId}">
              <span class="json-toggle-slider"></span>
              <span class="json-toggle-text">JSON View</span>
            </label>
          </div>
        </div>

        <div class="inspector-formatted-view" id="formatted-view-${msgId}">
          <div class="turn-metrics-header">
            <div class="metrics-pill active-tools-pill" title="Number of active tools in this turn">
              <span class="metrics-icon">🔧</span>
              <span class="metrics-pill-label">Active Tools:</span>
              <strong class="metrics-pill-val">${activeToolsCount}</strong>
            </div>
            ${totalDuration ? `
              <div class="metrics-pill duration-pill" title="Execution duration">
                <span class="metrics-icon">⏱️</span>
                <span class="metrics-pill-label">Duration:</span>
                <strong class="metrics-pill-val">${Number(totalDuration).toFixed(2)}s</strong>
              </div>
            ` : ''}
            ${usage && (usage.total_tokens || usage.input_tokens) ? `
              <div class="metrics-pill usage-pill" title="Tokens: in=${usage.input_tokens}, out=${usage.output_tokens}, think=${usage.thinking_tokens || 0}, cache=${usage.cache_read_tokens || 0}">
                <span class="metrics-icon">📊</span>
                <span class="metrics-pill-label">Tokens:</span>
                <strong class="metrics-pill-val">${usage.total_tokens || (usage.input_tokens + usage.output_tokens)}</strong>
              </div>
            ` : ''}
            ${activeTools.length > 0 ? `
              <div class="active-tools-chips">
                ${activeTools.map(t => `<span class="tool-tag-chip" title="Active tool: ${escapeHtml(t)}">${escapeHtml(t)}</span>`).join('')}
              </div>
            ` : (activeToolsCount === 0 ? '<span class="tool-tag-chip tool-less">Direct QA (0-tools)</span>' : '')}
          </div>
    `;

    const persistedMermaid = meta.mermaid_code || meta.extensions?.mermaid_code;
    if (persistedMermaid) {
      html += `
        <div class="inspector-tray open workflow-inspector-tray approved" id="tray-workflow-${msgId}">
          <button type="button" class="inspector-tray-toggle" id="tray-workflow-toggle-${msgId}">
            <span class="workflow-tray-title">
              <span class="workflow-tray-icon">🔀</span>
              <strong class="workflow-tray-label">Deterministic Workflow Graph</strong>
              <span class="workflow-header-badge approved" id="workflow-badge-${msgId}">✓ Workflow Graph</span>
            </span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <div class="workflow-mermaid-svg-wrapper" id="workflow-svg-wrap-${msgId}">
              <div style="font-size:11px; color:#94a3b8; font-family:var(--font-mono);">Rendering workflow graph...</div>
            </div>
          </div>
        </div>
      `;
    }

    if (executedCommand) {
      html += `
        <div class="inspector-tray" id="tray-cmd-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>💻 Executed CLI Command</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <pre class="metrics-pre-block command-pre" style="color:var(--accent-cyan); font-family:var(--font-mono); font-size:11px; white-space:pre-wrap; word-break:break-all;">${escapeHtml(executedCommand)}</pre>
          </div>
        </div>
      `;
    }

    if (usage && (usage.total_tokens || usage.input_tokens)) {
      html += `
        <div class="inspector-tray" id="tray-usage-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>📊 Usage Metrics: input=${usage.input_tokens || 0}, output=${usage.output_tokens || 0}, thinking=${usage.thinking_tokens || 0}, cache=${usage.cache_read_tokens || 0}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <pre class="metrics-pre-block usage-json-pre">${escapeHtml(JSON.stringify(usage, null, 2))}</pre>
          </div>
        </div>
      `;
    }

    if (sysInstructions) {
      html += `
        <div class="inspector-tray" id="tray-sys-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>📜 System Instructions (${sysInstructions.length} chars)</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <pre class="metrics-pre-block system-instructions-pre">${escapeHtml(sysInstructions)}</pre>
          </div>
        </div>
      `;
    }

    if (usedFiles.length > 0) {
      html += `
        <div class="inspector-tray" id="tray-files-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>📁 Files Used (${usedFiles.length})</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <div class="active-tools-chips" style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 6px;">
              ${usedFiles.map(f => `<span class="tool-tag-chip" style="color:var(--accent-cyan); border-color:rgba(56,189,248,0.3); font-family:var(--font-mono); font-size:11.5px;" title="File: ${escapeHtml(f)}">📄 ${escapeHtml(f)}</span>`).join('')}
            </div>
          </div>
        </div>
      `;
    }

    if (injectedSchemas.length > 0) {
      const schemasList = injectedSchemas.map((s, idx) => {
        const toolName = s.name || s.title || (activeTools[idx] || `Tool #${idx + 1}`);
        const toolDesc = s.description || s.summary || '';
        const schemaParams = s.parameters || s.schema || s;
        return `
          <div class="schema-item-card">
            <div class="schema-card-header">
              <strong style="color:var(--accent-cyan); font-family:var(--font-mono); font-size:11.5px;">📦 ${escapeHtml(toolName)}</strong>
              ${toolDesc ? `<span class="schema-card-desc">${escapeHtml(toolDesc)}</span>` : ''}
            </div>
            <pre class="metrics-pre-block schema-json-pre">${escapeHtml(JSON.stringify(schemaParams, null, 2))}</pre>
          </div>
        `;
      }).join('');

      html += `
        <div class="inspector-tray" id="tray-schemas-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>📦 Injected Schemas (${injectedSchemas.length} schema${injectedSchemas.length === 1 ? '' : 's'})</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <div class="schemas-items-list">${schemasList}</div>
          </div>
        </div>
      `;
    }

    const contextItems = extraData.contextItems || [];
    const thoughts = extraData.thoughts || [];
    const plan = extraData.plan || null;
    const toolCalls = extraData.toolCalls || [];
    const logs = extraData.logs || [];

    // Context items accordion
    if (contextItems.length > 0) {
      const itemsList = contextItems.map(c => `
        <div class="context-file-card">
          <span>📄 ${escapeHtml(c.label || c.path || c.kind)}</span>
          <span style="color:#64748b;">~${Math.round((c.content || '').length / 4)} tokens</span>
        </div>
      `).join('');

      html += `
        <div class="inspector-tray" id="tray-context-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>📁 Injected Project Context (${contextItems.length} files)</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <div class="context-items-list">${itemsList}</div>
          </div>
        </div>
      `;
    }

    // Execution plan accordion
    if (plan && plan.steps && plan.steps.length > 0) {
      html += `
        <div class="inspector-tray open" id="tray-plan-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span class="plan-tray-summary">📋 Execution Plan: ${escapeHtml(plan.summary || 'Step-by-step')}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <div class="plan-container">${buildPlanStepsHtml(plan.steps)}</div>
          </div>
        </div>
      `;
    }

    // Thoughts / Reasoning accordion
    if (thoughts.length > 0) {
      const thoughtSteps = thoughts.map(t => `
        <div class="thought-step-card">
          <strong>🧠 Thought:</strong>
          <div>${escapeHtml(t.text)}</div>
        </div>
      `).join('');

      html += `
        <div class="inspector-tray" id="tray-thoughts-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>🧠 Reasoning &amp; Internal Thoughts (${thoughts.length} step${thoughts.length === 1 ? '' : 's'})</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            ${thoughtSteps}
          </div>
        </div>
      `;
    }

    // Tool calls & logs accordion
    if (toolCalls.length > 0 || logs.length > 0) {
      const toolRows = toolCalls.map(tc => `
        <div style="margin-bottom:6px; font-family:var(--font-mono);">
          <strong style="color:var(--accent-cyan);">🔧 ${escapeHtml(tc.name)}</strong>
          <pre style="background:rgba(0,0,0,0.4); padding:6px; border-radius:4px; font-size:10.5px; margin-top:2px;">${escapeHtml(JSON.stringify(tc.args, null, 2))}</pre>
          ${tc.result ? `<div style="color:#10b981; font-size:10.5px; margin-top:2px;">Result: ${escapeHtml(typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result))}</div>` : ''}
        </div>
      `).join('');

      const logRows = logs.map(l => `
        <div style="font-family:var(--font-mono); font-size:10.5px; color:#94a3b8;">
          [${escapeHtml(l.stream)}] ${escapeHtml(l.text)}
        </div>
      `).join('');

      html += `
        <div class="inspector-tray" id="tray-tools-${msgId}">
          <button type="button" class="inspector-tray-toggle">
            <span>🔧 Tool Executions &amp; Runtime Logs (${toolCalls.length + logs.length} events)</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            ${toolRows}
            ${logRows}
          </div>
        </div>
      `;
    }

    html += `
        </div>

        <div class="inspector-raw-json-view" id="raw-json-view-${msgId}" style="display: none;">
          <pre class="metrics-pre-block raw-json-pre">${escapeHtml(JSON.stringify(rawPayload, null, 2))}</pre>
        </div>
      </div>
    `;
    return html;
  }

  function createMessageElement(msg) {
    const isUser = msg.role === 'user';
    const turnDiv = document.createElement('div');
    turnDiv.className = `message-turn ${isUser ? 'user' : 'assistant'}`;
    turnDiv.id = `msg-${msg.id}`;

    if (isUser) {
      const userMeta = msg.metadata || {};
      const userMetricsHtml = buildTurnMetricsHtml(userMeta, msg.id);

      turnDiv.innerHTML = `
        <div class="msg-avatar" title="You">U</div>
        <div class="msg-body-wrapper">
          <div class="msg-bubble">
            <div class="msg-text-content">${escapeHtml(msg.content)}</div>
            ${userMetricsHtml ? `<div class="msg-inspector user-inspector" style="margin-top:10px; margin-bottom:0;">${userMetricsHtml}</div>` : ''}
          </div>
          <span style="font-size:10.5px; color:#64748b;">${formatRelativeTime(msg.created_at)}</span>
        </div>
      `;

      turnDiv.querySelectorAll('.inspector-tray-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
          const tray = btn.closest('.inspector-tray');
          tray.classList.toggle('open');
        });
      });

      turnDiv.querySelectorAll('.json-toggle-checkbox').forEach(chk => {
        chk.addEventListener('change', () => {
          const mid = chk.dataset.msgId;
          const fmtView = document.getElementById(`formatted-view-${mid}`);
          const rawView = document.getElementById(`raw-json-view-${mid}`);
          if (chk.checked) {
            if (fmtView) fmtView.style.display = 'none';
            if (rawView) rawView.style.display = 'block';
          } else {
            if (fmtView) fmtView.style.display = 'block';
            if (rawView) rawView.style.display = 'none';
          }
        });
      });

    return turnDiv;
    }

    // Assistant Message with rich collapsible inspector
    const meta = msg.metadata || {};
    const rawStatus = msg.status || meta.extensions?.status || 'completed';
    const status = rawStatus === 'complete' ? 'completed' : rawStatus;
    const contextItems = meta.context_items || [];
    const thoughts = meta.thoughts || [];
    const plan = meta.plan || null;
    const toolCalls = meta.tool_calls || [];
    const logs = meta.logs || [];
    const usage = msg.usage || {};
    const runInfo = meta.extensions || {};

    const inspectorHtml = buildTurnMetricsHtml(meta, msg.id, {
      contextItems,
      thoughts,
      plan,
      toolCalls,
      logs,
      usage,
    });

    // Turn telemetry footer
    const latencyStr = usage.latency_ms ? `${usage.latency_ms}ms` : null;
    const tokensStr = usage.total_tokens ? `${usage.total_tokens} tokens` : null;
    const costStr = usage.cost_usd ? formatCost(usage.cost_usd) : null;

    turnDiv.innerHTML = `
      <div class="msg-avatar" title="Vibe Agent">⚡</div>
      <div class="msg-body-wrapper" style="width:100%;">
        <div class="msg-bubble">
          <div class="msg-header">
            <span class="msg-model-tag">${escapeHtml(runInfo.model || 'assistant')}</span>
            <span class="msg-status-pill ${status}">
              ${status === 'streaming' ? '<span class="badge-dot active pulse"></span> Generating...' : status === 'completed' ? '✓ Completed' : status === 'cancelled' ? '⚠ Cancelled' : '✕ Error'}
            </span>
          </div>

          ${inspectorHtml ? `<div class="msg-inspector">${inspectorHtml}</div>` : ''}

          <div class="msg-text-content" id="msg-content-${msg.id}">
            ${formatMarkdown(msg.content)}
          </div>

          ${(latencyStr || tokensStr || costStr) ? `
            <div class="msg-telemetry-footer">
              ${latencyStr ? `<span>⏱ ${latencyStr}</span>` : ''}
              ${tokensStr ? `<span>🪙 ${tokensStr}</span>` : ''}
              ${costStr ? `<span>💵 ${costStr}</span>` : ''}
            </div>
          ` : ''}
        </div>
      </div>
    `;

    // Attach collapsible tray toggle handlers
    turnDiv.querySelectorAll('.inspector-tray-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const tray = btn.closest('.inspector-tray');
        tray.classList.toggle('open');
      });
    });

    turnDiv.querySelectorAll('.json-toggle-checkbox').forEach(chk => {
      chk.addEventListener('change', () => {
        const mid = chk.dataset.msgId;
        const fmtView = document.getElementById(`formatted-view-${mid}`);
        const rawView = document.getElementById(`raw-json-view-${mid}`);
        if (chk.checked) {
          if (fmtView) fmtView.style.display = 'none';
          if (rawView) rawView.style.display = 'block';
        } else {
          if (fmtView) fmtView.style.display = 'block';
          if (rawView) rawView.style.display = 'none';
        }
      });
    });

    // If message has persisted workflow Mermaid diagram, render it
    const persistedMermaid = runInfo.mermaid_code || meta.mermaid_code || (meta.extensions && meta.extensions.mermaid_code);
    if (persistedMermaid) {
      setTimeout(() => {
        renderOrUpdateMermaidWorkflow(msg.id, {
          mermaid_code: persistedMermaid,
          state: status === 'completed' ? 'completed' : status,
          phase: status === 'completed' ? 'COMPLETED' : 'APPROVED',
        });
      }, 0);
    }

    return turnDiv;
  }

  function scrollToBottom() {
    el.messagesContainer.scrollTop = el.messagesContainer.scrollHeight;
  }

  // --- Workspace & Session Transitions ---
  async function switchWorkspace(workspaceId) {
    if (activeWorkspaceId === workspaceId && workspaceSessions.length > 0) return;
    activeWorkspaceId = workspaceId;

    renderWorkspaceTabs();

    // Update Drawer info for active workspace
    const ws = workspaces.find(w => w.project_id === workspaceId);
    if (ws) {
      el.drawerWsName.textContent = ws.name || ws.project_id;
      el.wsActivePath.textContent = ws.root_path || '';
      el.wsMetaRuntime.textContent = ws.runtime || 'python:3.10';
      el.wsMetaTest.textContent = ws.test_command || 'pytest';
      el.inspectorManifestJson.textContent = ws.manifest ? JSON.stringify(ws.manifest, null, 2) : 'No manifest.yaml found';

      // Load files and tree
      el.inspectorContextChips.innerHTML = '';
      (ws.context_files || []).forEach(file => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'inspector-chip';
        chip.textContent = file;
        chip.addEventListener('click', () => {
          el.inspectorDocPreview.textContent = `Context file: ${file}\nInjected automatically into LLM system prompt for workspace [${ws.name}].`;
        });
        el.inspectorContextChips.appendChild(chip);
      });

      API.fetchWorkspaceTree(workspaceId).then(data => {
        el.inspectorTreeView.textContent = (data.tree || []).join('\n');
      });
    }

    // Load workspace chat sessions
    await loadWorkspaceChats(workspaceId);
  }

  async function loadWorkspaceChats(workspaceId) {
    el.chatSessionsList.innerHTML = '<div class="sessions-empty">Loading conversations...</div>';
    workspaceSessions = await API.fetchWorkspaceChats(workspaceId);
    renderChatSessionsList();

    if (workspaceSessions.length > 0) {
      // Pick first session
      await selectChatSession(workspaceSessions[0].id);
    } else {
      // Auto-create initial chat for the workspace
      await createNewChat();
    }
  }

  async function selectChatSession(sessionId) {
    if (activeSessionId === sessionId && activeSessionData) return;
    activeSessionId = sessionId;
    renderChatSessionsList();

    try {
      activeSessionData = await API.fetchChatSession(sessionId);
      updateSessionTelemetryBar();
      renderMessagesThread();
    } catch (err) {
      console.error(`Failed to load chat session ${sessionId}:`, err);
    }
  }

  async function createNewChat() {
    try {
      const ws = workspaces.find(w => w.project_id === activeWorkspaceId);
      const title = `New Chat in ${ws?.name || activeWorkspaceId}`;
      const providerId = el.providerSelect.value || 'fake';
      const model = el.modelSelect.value || undefined;

      const created = await API.createChat(activeWorkspaceId, title, providerId, model);
      workspaceSessions.unshift(created);
      renderChatSessionsList();
      await selectChatSession(created.id);
    } catch (err) {
      console.error('Failed to create new chat:', err);
    }
  }

  async function deleteSession(sessionId) {
    try {
      await API.deleteChat(sessionId);
      workspaceSessions = workspaceSessions.filter(s => s.id !== sessionId);
      renderChatSessionsList();
      if (activeSessionId === sessionId) {
        if (workspaceSessions.length > 0) {
          await selectChatSession(workspaceSessions[0].id);
        } else {
          await createNewChat();
        }
      }
    } catch (err) {
      console.error(`Failed to delete chat session ${sessionId}:`, err);
    }
  }

  async function renameActiveSession() {
    if (!activeSessionId) return;
    const currentTitle = el.sessionTitle.textContent;
    const newTitle = prompt('Enter new session title:', currentTitle);
    if (!newTitle || newTitle.trim() === '' || newTitle.trim() === currentTitle) return;

    try {
      const updated = await API.renameChat(activeSessionId, newTitle.trim());
      if (activeSessionData) activeSessionData.title = updated.title;
      const found = workspaceSessions.find(s => s.id === activeSessionId);
      if (found) found.title = updated.title;
      updateSessionTelemetryBar();
      renderChatSessionsList();
    } catch (err) {
      console.error('Failed to rename session:', err);
    }
  }

  // --- Sending Message & Streaming WebSocket Execution ---
  async function handleSendTurn() {
    const userPrompt = el.userPromptInput.value.trim();
    if (!userPrompt || !activeSessionId) return;

    const systemPrompt = el.systemPromptInput.value.trim();
    const providerId = el.providerSelect.value || 'fake';
    const model = el.modelSelect.value || undefined;

    // Reset input
    el.userPromptInput.value = '';
    el.userPromptInput.style.height = 'auto';

    // UI state: running
    el.btnExecute.style.display = 'none';
    el.btnStop.style.display = 'inline-flex';

    try {
      const requireApproval = el.requireApprovalCheck ? el.requireApprovalCheck.checked : true;
      const response = await API.sendChatMessage(activeSessionId, {
        user_prompt: userPrompt,
        system_instructions: systemPrompt,
        provider_id: providerId,
        model: model,
        require_approval: requireApproval
      });

      // Append user message immediately
      if (!activeSessionData.messages) activeSessionData.messages = [];
      activeSessionData.messages.push(response.user_message);

      // Append assistant streaming placeholder
      const assistantMsg = response.assistant_message;
      activeSessionData.messages.push(assistantMsg);
      renderMessagesThread();

      // Connect WebSocket to stream
      connectTurnWebSocket(activeSessionId, response.message_id, response.cursor || 0);

    } catch (err) {
      console.error('Failed to start turn:', err);
      alert(`Execution error: ${err.message}`);
      el.btnExecute.style.display = 'inline-flex';
      el.btnStop.style.display = 'none';
    }
  }

  function connectTurnWebSocket(sessionId, messageId, cursor = 0) {
    if (activeWebSocket) {
      activeWebSocket.close();
      activeWebSocket = null;
    }

    activeStreamMessageId = messageId;
    activeStreamText[messageId] = '';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/chats/${encodeURIComponent(sessionId)}/events?cursor=${cursor}&message_id=${encodeURIComponent(messageId)}`;

    const socket = new WebSocket(wsUrl);
    activeWebSocket = socket;

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleStreamEvent(msg, messageId);
      } catch (err) {
        console.error('Error handling WebSocket message:', err);
      }
    };

    socket.onerror = (err) => {
      console.warn('Chat WebSocket error:', err);
    };

    socket.onclose = () => {
      if (activeWebSocket === socket) {
        activeWebSocket = null;
      }
      el.btnExecute.style.display = 'inline-flex';
      el.btnStop.style.display = 'none';
    };
  }

  const activeStreamEvents = {};
  const activeStreamText = {};

  function renderOrUpdatePlanTray(messageId, planData) {
    const msgElement = document.getElementById(`msg-${messageId}`);
    if (!msgElement || !planData) return;

    const steps = Array.isArray(planData.steps) ? planData.steps : [];
    if (steps.length === 0) return;

    const summary = planData.summary || planData.title || 'Step-by-step';
    let tray = document.getElementById(`tray-plan-${messageId}`);

    if (!tray) {
      const fmtView = document.getElementById(`formatted-view-${messageId}`);
      if (!fmtView) return;

      tray = document.createElement('div');
      tray.className = 'inspector-tray open';
      tray.id = `tray-plan-${messageId}`;
      tray.innerHTML = `
        <button type="button" class="inspector-tray-toggle">
          <span class="plan-tray-summary">📋 Execution Plan: ${escapeHtml(summary)}</span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
        </button>
        <div class="inspector-tray-body">
          <div class="plan-container"></div>
        </div>
      `;

      const workflowTray = document.getElementById(`tray-workflow-${messageId}`);
      const header = fmtView.querySelector('.turn-metrics-header');
      if (workflowTray && workflowTray.nextSibling) {
        fmtView.insertBefore(tray, workflowTray.nextSibling);
      } else if (workflowTray) {
        fmtView.appendChild(tray);
      } else if (header && header.nextSibling) {
        fmtView.insertBefore(tray, header.nextSibling);
      } else {
        fmtView.appendChild(tray);
      }

      tray.querySelector('.inspector-tray-toggle').addEventListener('click', () => {
        tray.classList.toggle('open');
      });
    }

    const summarySpan = tray.querySelector('.plan-tray-summary');
    if (summarySpan) {
      summarySpan.textContent = `📋 Execution Plan: ${summary}`;
    }

    const container = tray.querySelector('.plan-container');
    if (container) {
      container.innerHTML = buildPlanStepsHtml(steps);
    }
  }

  function finalizeLivePlanTray(messageId, finalStatus) {
    const tray = document.getElementById(`tray-plan-${messageId}`);
    if (!tray) return;

    const iconFor = (st) => st === 'done' ? '✓' : st === 'in_progress' ? '●' : st === 'failed' ? '✕' : '○';
    tray.querySelectorAll('.plan-step-row').forEach(row => {
      const icon = row.querySelector('.step-status-icon');
      const isPending = row.classList.contains('pending');
      const isInProgress = row.classList.contains('in_progress');

      let nextStatus = null;
      if (finalStatus === 'completed') {
        if (isPending || isInProgress) nextStatus = 'done';
      } else if (finalStatus === 'error') {
        if (isInProgress) nextStatus = 'failed';
      }

      if (nextStatus) {
        row.className = `plan-step-row ${nextStatus}`;
        if (icon) {
          icon.className = `step-status-icon ${nextStatus}`;
          icon.textContent = iconFor(nextStatus);
        }
      }
    });
  }

  async function renderOrUpdateMermaidWorkflow(messageId, data) {
    const msgElement = document.getElementById(`msg-${messageId}`);
    if (!msgElement) return;

    let tray = document.getElementById(`tray-workflow-${messageId}`);
    const mermaidCode = data.mermaid_code;
    const isWaitingApproval = data.state === 'waiting_approval';
    const isApproved = (data.state === 'running' && data.phase === 'APPROVED') || data.state === 'completed';
    const isCancelled = data.state === 'cancelled';

    const badgeClass = isWaitingApproval ? 'waiting' : isApproved ? 'approved' : isCancelled ? 'cancelled' : '';
    const badgeText = isWaitingApproval ? '⏳ Waiting Approval' : isApproved ? '✓ Approved' : isCancelled ? '✕ Cancelled' : '● Executing';

    if (!tray) {
      tray = document.createElement('div');
      tray.id = `tray-workflow-${messageId}`;
      tray.className = `inspector-tray open workflow-inspector-tray ${badgeClass}`;

      tray.innerHTML = `
        <button type="button" class="inspector-tray-toggle" id="tray-workflow-toggle-${messageId}">
          <span class="workflow-tray-title">
            <span class="workflow-tray-icon">🔀</span>
            <strong class="workflow-tray-label">Deterministic Workflow Graph</strong>
            <span class="workflow-header-badge ${badgeClass}" id="workflow-badge-${messageId}">
              ${badgeText}
            </span>
          </span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
        </button>
        <div class="inspector-tray-body">
          <div class="workflow-approval-bar" id="workflow-approval-bar-${messageId}" style="${isWaitingApproval ? 'display:flex;' : 'display:none;'}">
            <div class="workflow-approval-info">
              <div class="workflow-approval-prompt">⚠️ Human Review &amp; Approval Required</div>
              <div class="workflow-approval-subtext">Review the deterministic graph and planned sub-graphs before execution begins.</div>
            </div>
            <div class="workflow-approval-actions">
              <button type="button" class="btn-cancel-workflow" id="btn-cancel-wf-${messageId}">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                <span>Cancel</span>
              </button>
              <button type="button" class="btn-approve-workflow" id="btn-approve-wf-${messageId}">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
                <span>Approve &amp; Run</span>
              </button>
            </div>
          </div>
          <div class="workflow-mermaid-svg-wrapper" id="workflow-svg-wrap-${messageId}">
            <div style="font-size:11px; color:#94a3b8; font-family:var(--font-mono);">Rendering workflow graph...</div>
          </div>
        </div>
      `;

      const fmtView = document.getElementById(`formatted-view-${messageId}`);
      if (fmtView) {
        const header = fmtView.querySelector('.turn-metrics-header');
        if (header && header.nextSibling) {
          fmtView.insertBefore(tray, header.nextSibling);
        } else {
          fmtView.insertBefore(tray, fmtView.firstChild);
        }
      } else {
        let inspector = msgElement.querySelector('.msg-inspector');
        if (!inspector) {
          inspector = document.createElement('div');
          inspector.className = 'msg-inspector';
          const content = msgElement.querySelector('.msg-text-content');
          if (content) {
            msgElement.querySelector('.msg-bubble').insertBefore(inspector, content);
          } else {
            msgElement.querySelector('.msg-bubble').appendChild(inspector);
          }
        }
        inspector.insertBefore(tray, inspector.firstChild);
      }

      const toggleBtn = tray.querySelector('.inspector-tray-toggle');
      if (toggleBtn) {
        toggleBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          tray.classList.toggle('open');
        });
      }

      const btnApprove = document.getElementById(`btn-approve-wf-${messageId}`);
      if (btnApprove) {
        btnApprove.addEventListener('click', async () => {
          btnApprove.disabled = true;
          btnApprove.innerHTML = '<span>Approving...</span>';
          try {
            if (activeWebSocket && activeWebSocket.readyState === WebSocket.OPEN) {
              activeWebSocket.send(JSON.stringify({ action: 'approve' }));
            }
            await API.approveChatTurn(activeSessionId);
            const badge = document.getElementById(`workflow-badge-${messageId}`);
            if (badge) {
              badge.className = 'workflow-header-badge approved';
              badge.textContent = '✓ Approved & Executing';
            }
            const bar = document.getElementById(`workflow-approval-bar-${messageId}`);
            if (bar) bar.style.display = 'none';
          } catch (err) {
            console.error('Approve turn error:', err);
            btnApprove.disabled = false;
            btnApprove.textContent = 'Retry Approve';
          }
        });
      }

      const btnCancel = document.getElementById(`btn-cancel-wf-${messageId}`);
      if (btnCancel) {
        btnCancel.addEventListener('click', async () => {
          btnCancel.disabled = true;
          try {
            if (activeWebSocket && activeWebSocket.readyState === WebSocket.OPEN) {
              activeWebSocket.send(JSON.stringify({ action: 'cancel' }));
            }
            await API.cancelChatTurn(activeSessionId);
            const badge = document.getElementById(`workflow-badge-${messageId}`);
            if (badge) {
              badge.className = 'workflow-header-badge cancelled';
              badge.textContent = '✕ Cancelled';
            }
            const bar = document.getElementById(`workflow-approval-bar-${messageId}`);
            if (bar) bar.style.display = 'none';
          } catch (err) {
            console.error('Cancel turn error:', err);
          }
        });
      }
    }

    const badge = document.getElementById(`workflow-badge-${messageId}`);
    const bar = document.getElementById(`workflow-approval-bar-${messageId}`);
    if (isWaitingApproval) {
      tray.className = 'inspector-tray open workflow-inspector-tray waiting-approval';
      if (badge) {
        badge.className = 'workflow-header-badge waiting';
        badge.textContent = '⏳ Waiting Approval';
      }
      if (bar) bar.style.display = 'flex';
    } else if (isApproved) {
      tray.className = 'inspector-tray open workflow-inspector-tray approved';
      if (badge) {
        badge.className = 'workflow-header-badge approved';
        badge.textContent = data.state === 'completed' ? '✓ Completed' : '✓ Approved & Executing';
      }
      if (bar) bar.style.display = 'none';
    } else if (isCancelled) {
      tray.className = 'inspector-tray open workflow-inspector-tray cancelled';
      if (badge) {
        badge.className = 'workflow-header-badge cancelled';
        badge.textContent = '✕ Cancelled';
      }
      if (bar) bar.style.display = 'none';
    }

    if (mermaidCode) {
      const wrapper = document.getElementById(`workflow-svg-wrap-${messageId}`);
      if (wrapper) {
        if (window.mermaid) {
          try {
            const cleanId = 'mermaid_svg_' + messageId.replace(/[^a-zA-Z0-9_-]/g, '_') + '_' + Math.floor(Math.random() * 10000);
            const { svg } = await window.mermaid.render(cleanId, mermaidCode);
            wrapper.innerHTML = svg;
          } catch (mErr) {
            console.warn('Mermaid render error:', mErr);
            wrapper.innerHTML = `<pre style="font-size:10.5px; font-family:var(--font-mono); color:#94a3b8; text-align:left; background:rgba(0,0,0,0.3); padding:8px; border-radius:4px; margin:0;">${escapeHtml(mermaidCode)}</pre>`;
          }
        } else {
          wrapper.innerHTML = `<pre style="font-size:10.5px; font-family:var(--font-mono); color:#94a3b8; text-align:left; background:rgba(0,0,0,0.3); padding:8px; border-radius:4px; margin:0;">${escapeHtml(mermaidCode)}</pre>`;
        }
      }
    }
  }

  function handleStreamEvent(event, messageId) {
    activeStreamEvents[messageId] = activeStreamEvents[messageId] || [];
    const rawItem = event.raw_payload || event.data?.raw_payload || event;
    activeStreamEvents[messageId].push(rawItem);

    const type = event.type;
    const data = event.data || {};

    const msgElement = document.getElementById(`msg-${messageId}`);
    const contentElement = document.getElementById(`msg-content-${messageId}`);

    // If status event carries workflow or waiting_approval state, update Mermaid card
    if (data.mermaid_code || data.state === 'waiting_approval' || (data.state === 'running' && data.phase === 'APPROVED') || data.state === 'cancelled') {
      renderOrUpdateMermaidWorkflow(messageId, data);
    }

    // Update Raw JSON view in real-time
    const rawPre = document.querySelector(`#raw-json-view-${messageId} .raw-json-pre`);
    if (rawPre) {
      try {
        const cur = JSON.parse(rawPre.textContent || '{}');
        cur.live_events = activeStreamEvents[messageId];
        rawPre.textContent = JSON.stringify(cur, null, 2);
      } catch (e) {
        // Fallback
      }
    }

    if (type === 'status') {
      const executedCmd = data.executed_command || data.command;
      if (executedCmd && msgElement) {
        if (rawPre) {
          try {
            const cur = JSON.parse(rawPre.textContent || '{}');
            if (cur.full_context_payload) {
              cur.full_context_payload.executed_command = executedCmd;
              rawPre.textContent = JSON.stringify(cur, null, 2);
            }
          } catch (e) {}
        }
        let tray = document.getElementById(`tray-cmd-${messageId}`);
        if (!tray) {
          const fmtView = document.getElementById(`formatted-view-${messageId}`);
          if (fmtView) {
            tray = document.createElement('div');
            tray.className = 'inspector-tray';
            tray.id = `tray-cmd-${messageId}`;
            tray.innerHTML = `
              <button type="button" class="inspector-tray-toggle">
                <span>💻 Executed CLI Command</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
              </button>
              <div class="inspector-tray-body">
                <pre class="metrics-pre-block command-pre" style="color:var(--accent-cyan); font-family:var(--font-mono); font-size:11px; white-space:pre-wrap; word-break:break-all;">${escapeHtml(executedCmd)}</pre>
              </div>
            `;
            const header = fmtView.querySelector('.turn-metrics-header');
            if (header && header.nextSibling) {
              fmtView.insertBefore(tray, header.nextSibling);
            } else {
              fmtView.appendChild(tray);
            }
            tray.querySelector('.inspector-tray-toggle').addEventListener('click', () => {
              tray.classList.toggle('open');
            });
          }
        }
      }
    } else if (type === 'plan') {
      renderOrUpdatePlanTray(messageId, data);
    } else if (type === 'token') {
      const token = data.text || '';
      if (contentElement) {
        activeStreamText[messageId] = (activeStreamText[messageId] || '') + token;
        contentElement.innerHTML = formatMarkdown(activeStreamText[messageId]);
        scrollToBottom();
      }
    } else if (type === 'thought') {
      const thoughtText = data.text || '';
      if (msgElement) {
        let tray = document.getElementById(`tray-thoughts-${messageId}`);
        if (!tray) {
          const fmtView = document.getElementById(`formatted-view-${messageId}`);
          if (fmtView) {
            tray = document.createElement('div');
            tray.className = 'inspector-tray open';
            tray.id = `tray-thoughts-${messageId}`;
            tray.innerHTML = `
              <button type="button" class="inspector-tray-toggle">
                <span>🧠 Reasoning &amp; Internal Thoughts</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
              </button>
              <div class="inspector-tray-body"></div>
            `;
            fmtView.appendChild(tray);
            tray.querySelector('.inspector-tray-toggle').addEventListener('click', () => {
              tray.classList.toggle('open');
            });
          }
        }
        if (tray) {
          const body = tray.querySelector('.inspector-tray-body');
          if (body) {
            const card = document.createElement('div');
            card.className = 'thought-step-card';
            card.innerHTML = `<strong>🧠 Thought:</strong><div>${escapeHtml(thoughtText)}</div>`;
            body.appendChild(card);
            scrollToBottom();
          }
        }
      }
    } else if (type === 'tool_call') {
      const toolName = data.name || 'tool';
      const toolArgs = data.args || {};
      if (msgElement) {
        let tray = document.getElementById(`tray-tools-${messageId}`);
        if (!tray) {
          const fmtView = document.getElementById(`formatted-view-${messageId}`);
          if (fmtView) {
            tray = document.createElement('div');
            tray.className = 'inspector-tray open';
            tray.id = `tray-tools-${messageId}`;
            tray.innerHTML = `
              <button type="button" class="inspector-tray-toggle">
                <span>🔧 Tool Executions &amp; Runtime Logs</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
              </button>
              <div class="inspector-tray-body"></div>
            `;
            fmtView.appendChild(tray);
            tray.querySelector('.inspector-tray-toggle').addEventListener('click', () => {
              tray.classList.toggle('open');
            });
          }
        }
        if (tray) {
          const body = tray.querySelector('.inspector-tray-body');
          if (body) {
            const card = document.createElement('div');
            card.style.marginBottom = '6px';
            card.style.fontFamily = 'var(--font-mono)';
            card.id = `live-tool-${messageId}-${toolName}`;
            card.innerHTML = `
              <strong style="color:var(--accent-cyan);">🔧 ${escapeHtml(toolName)}</strong>
              <pre style="background:rgba(0,0,0,0.4); padding:6px; border-radius:4px; font-size:10.5px; margin-top:2px;">${escapeHtml(JSON.stringify(toolArgs, null, 2))}</pre>
            `;
            body.appendChild(card);
            scrollToBottom();
          }
        }
      }
    } else if (type === 'tool_result') {
      const toolName = data.name || 'tool';
      const result = data.result;
      if (msgElement) {
        const liveCard = document.getElementById(`live-tool-${messageId}-${toolName}`);
        if (liveCard) {
          const resDiv = document.createElement('div');
          resDiv.style.color = '#10b981';
          resDiv.style.fontSize = '10.5px';
          resDiv.style.marginTop = '2px';
          resDiv.textContent = `Result: ${typeof result === 'string' ? result : JSON.stringify(result)}`;
          liveCard.appendChild(resDiv);
          scrollToBottom();
        }
      }
    } else if (type === 'log') {
      const logText = data.text || data.line || '';
      const stream = data.stream || 'stdout';
      if (msgElement) {
        let tray = document.getElementById(`tray-tools-${messageId}`);
        if (tray) {
          const body = tray.querySelector('.inspector-tray-body');
          if (body) {
            const row = document.createElement('div');
            row.style.fontFamily = 'var(--font-mono)';
            row.style.fontSize = '10.5px';
            row.style.color = '#94a3b8';
            row.textContent = `[${stream}] ${logText}`;
            body.appendChild(row);
            scrollToBottom();
          }
        }
      }
    } else if (type === 'done') {
      const finishReason = data.finish_reason || '';
      const isCancelled = finishReason === 'cancelled';
      const isFailed = finishReason === 'error' || data.status === 'error';
      const finalStatus = isCancelled ? 'cancelled' : isFailed ? 'error' : 'completed';

      if (msgElement) {
        const pill = msgElement.querySelector('.msg-status-pill');
        if (pill) {
          pill.className = `msg-status-pill ${finalStatus}`;
          pill.textContent = isCancelled ? '⚠ Cancelled' : isFailed ? '✕ Error' : '✓ Completed';
        }

        finalizeLivePlanTray(messageId, finalStatus);

        const wfTray = document.getElementById(`tray-workflow-${messageId}`);
        const wfBadge = document.getElementById(`workflow-badge-${messageId}`);
        const wfBar = document.getElementById(`workflow-approval-bar-${messageId}`);
        if (wfBar) wfBar.style.display = 'none';
        if (wfTray && wfBadge) {
          if (isCancelled || isFailed) {
            wfTray.className = 'inspector-tray open workflow-inspector-tray cancelled';
            wfBadge.className = 'workflow-header-badge cancelled';
            wfBadge.textContent = isCancelled ? '✕ Cancelled' : '✕ Error';
          } else {
            wfTray.className = 'inspector-tray open workflow-inspector-tray approved';
            wfBadge.className = 'workflow-header-badge approved';
            wfBadge.textContent = '✓ Completed';
          }
        }
      }
      el.btnExecute.style.display = 'inline-flex';
      el.btnStop.style.display = 'none';

      // Refresh full session from server to capture updated tokens & DB state
      API.fetchChatSession(activeSessionId).then(session => {
        if (session && session.id === activeSessionId) {
          activeSessionData = session;
          updateSessionTelemetryBar();
          renderChatSessionsList();
          renderMessagesThread();
        }
      });

    } else if (type === 'error') {
      if (msgElement) {
        const pill = msgElement.querySelector('.msg-status-pill');
        if (pill) {
          pill.className = 'msg-status-pill error';
          pill.textContent = '✕ Error';
        }
        finalizeLivePlanTray(messageId, 'error');
        const wfTray = document.getElementById(`tray-workflow-${messageId}`);
        const wfBadge = document.getElementById(`workflow-badge-${messageId}`);
        const wfBar = document.getElementById(`workflow-approval-bar-${messageId}`);
        if (wfBar) wfBar.style.display = 'none';
        if (wfTray && wfBadge) {
          wfTray.className = 'inspector-tray open workflow-inspector-tray cancelled';
          wfBadge.className = 'workflow-header-badge cancelled';
          wfBadge.textContent = '✕ Error';
        }
      }
      el.btnExecute.style.display = 'inline-flex';
      el.btnStop.style.display = 'none';
    }
  }

  async function handleStopTurn() {
    if (!activeSessionId) return;
    try {
      if (activeWebSocket && activeWebSocket.readyState === WebSocket.OPEN) {
        activeWebSocket.send(JSON.stringify({ action: 'cancel' }));
      }
      await API.cancelChatTurn(activeSessionId);
    } catch (err) {
      console.error('Cancel turn error:', err);
    }
  }

  // --- Prompt Linter Debouncer ---
  function triggerPromptLint() {
    clearTimeout(lintDebounceTimer);
    lintDebounceTimer = setTimeout(async () => {
      const prompt = el.userPromptInput.value.trim();
      const system = el.systemPromptInput.value.trim();
      if (!prompt) {
        el.lintScoreVal && (el.lintScoreVal.textContent = '100');
        el.tokenEstimateBadge.textContent = '~0 tokens';
        el.lintScoreBadge.className = 'linter-pill good';
        return;
      }
      const report = await API.lintPrompt(prompt, system);
      if (report) {
        const score = report.score || 100;
        el.lintScoreText.textContent = `Quality: ${score}`;
        el.tokenEstimateBadge.textContent = `~${report.token_estimate || 0} tokens`;
        el.lintScoreBadge.className = 'linter-pill ' + (score >= 80 ? 'good' : score >= 50 ? 'warn' : 'bad');
      }
    }, 300);
  }

  // --- Modal: Register Target Workspace ---
  function openAddWorkspaceModal() {
    el.wsInputPath.value = '';
    el.wsInputId.value = '';
    el.wsInputName.value = '';
    el.modalError.style.display = 'none';
    el.modalAddWorkspace.style.display = 'flex';
    el.wsInputPath.focus();
  }

  function closeAddWorkspaceModal() {
    el.modalAddWorkspace.style.display = 'none';
  }

  async function submitAddWorkspace() {
    const path = el.wsInputPath.value.trim();
    if (!path) {
      el.modalError.textContent = 'Target directory path is required.';
      el.modalError.style.display = 'block';
      return;
    }
    const payload = {
      path,
      project_id: el.wsInputId.value.trim() || undefined,
      name: el.wsInputName.value.trim() || undefined,
      persist_manifest: el.wsInputPersist.checked
    };

    try {
      el.btnConfirmAddWs.disabled = true;
      el.btnConfirmAddWs.textContent = 'Registering...';
      const registered = await API.registerWorkspace(payload);
      closeAddWorkspaceModal();

      // Refresh workspaces
      workspaces = await API.fetchWorkspaces();
      renderWorkspaceTabs();
      await switchWorkspace(registered.project_id || registered.id);
    } catch (err) {
      el.modalError.textContent = err.message || 'Failed to register target directory.';
      el.modalError.style.display = 'block';
    } finally {
      el.btnConfirmAddWs.disabled = false;
      el.btnConfirmAddWs.textContent = 'Register & Open Workspace';
    }
  }

  // --- Event Listeners Setup ---
  function setupEventListeners() {
    // Drawer toggle
    el.btnToggleWsDrawer.addEventListener('click', () => {
      el.wsDrawer.classList.toggle('open');
    });
    el.btnCloseWsDrawer.addEventListener('click', () => {
      el.wsDrawer.classList.remove('open');
    });

    // Chat sessions sidebar
    el.btnNewChat.addEventListener('click', createNewChat);
    el.chatSearchInput.addEventListener('input', renderChatSessionsList);

    // Title edit
    el.sessionTitle.addEventListener('click', renameActiveSession);
    el.btnEditTitle.addEventListener('click', renameActiveSession);

    // Provider select
    el.providerSelect.addEventListener('change', updateProvidersUI);

    // Presets
    document.querySelectorAll('.preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const presetKey = btn.dataset.preset;
        const preset = PRESETS[presetKey];
        if (preset) {
          el.userPromptInput.value = preset.prompt;
          el.systemPromptInput.value = preset.system;
          triggerPromptLint();
          el.userPromptInput.focus();
        }
      });
    });

    // System instructions drawer toggle
    el.btnToggleSystem.addEventListener('click', () => {
      const isCollapsed = el.systemInstructionsDrawer.classList.toggle('collapsed');
      el.btnToggleSystem.setAttribute('aria-expanded', String(!isCollapsed));
    });

    // User prompt textarea input & Enter to send
    el.userPromptInput.addEventListener('input', () => {
      el.userPromptInput.style.height = 'auto';
      el.userPromptInput.style.height = Math.min(el.userPromptInput.scrollHeight, 180) + 'px';
      triggerPromptLint();
    });

    el.userPromptInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSendTurn();
      }
    });

    el.systemPromptInput.addEventListener('input', triggerPromptLint);

    // Send and stop buttons
    el.btnExecute.addEventListener('click', handleSendTurn);
    el.btnStop.addEventListener('click', handleStopTurn);

    // Add workspace modal
    el.btnAddWorkspace.addEventListener('click', openAddWorkspaceModal);
    el.btnCloseModal.addEventListener('click', closeAddWorkspaceModal);
    el.btnCancelModal.addEventListener('click', closeAddWorkspaceModal);
    el.btnConfirmAddWs.addEventListener('click', submitAddWorkspace);
  }

  // --- Bootstrap ---
  async function init() {
    setupEventListeners();

    // Load providers and workspaces in parallel
    const [fetchedProviders, fetchedWorkspaces] = await Promise.all([
      API.fetchProviders(),
      API.fetchWorkspaces()
    ]);

    providers = fetchedProviders;
    workspaces = fetchedWorkspaces;

    // Populate providers in selector
    el.providerSelect.innerHTML = '';
    providers.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.display_name} (${p.state})`;
      if (!p.enabled || p.state !== 'ready') opt.disabled = true;
      el.providerSelect.appendChild(opt);
    });

    updateProvidersUI();
    renderWorkspaceTabs();

    // Default to 'root' workspace
    await switchWorkspace('root');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
