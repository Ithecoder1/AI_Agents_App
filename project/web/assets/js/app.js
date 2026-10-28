/**
 * VIBE FORGE WEB CLIENT
 * Meta-Agent Studio: Multi-Workspace Tabbed Interface, SQLite-backed Multi-Turn Chat,
 * Real-time WebSocket Streaming, Session Telemetry Bar, and Collapsible Message Inspector.
 */

(() => {
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

  function formatMarkdown(text) {
    if (!text) return '';
    // Code blocks with syntax / copy
    text = text.replace(/```([a-zA-Z0-9_\-\.]*)\n([\s\S]*?)```/g, (match, lang, code) => {
      if (lang === 'diff') {
        const lines = code.split('\n').map(l => {
          let cls = '';
          if (l.startsWith('+') && !l.startsWith('+++')) cls = 'add';
          else if (l.startsWith('-') && !l.startsWith('---')) cls = 'del';
          else if (l.startsWith('@@') || l.startsWith('---') || l.startsWith('+++')) cls = 'header';
          return `<div class="diff-line ${cls}">${escapeHtml(l)}</div>`;
        }).join('');
        return `<div class="diff-viewer">${lines}</div>`;
      }
      return `<pre><code class="language-${lang}">${escapeHtml(code)}</code></pre>`;
    });

    // Inline code
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Line breaks
    text = text.replace(/\n\n/g, '<br/><br/>').replace(/\n/g, '<br/>');
    return text;
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

  function createMessageElement(msg) {
    const isUser = msg.role === 'user';
    const turnDiv = document.createElement('div');
    turnDiv.className = `message-turn ${isUser ? 'user' : 'assistant'}`;
    turnDiv.id = `msg-${msg.id}`;

    if (isUser) {
      turnDiv.innerHTML = `
        <div class="msg-avatar" title="You">U</div>
        <div class="msg-body-wrapper">
          <div class="msg-bubble">
            <div class="msg-text-content">${escapeHtml(msg.content)}</div>
          </div>
          <span style="font-size:10.5px; color:#64748b;">${formatRelativeTime(msg.created_at)}</span>
        </div>
      `;
      return turnDiv;
    }

    // Assistant Message with rich collapsible inspector
    const meta = msg.metadata || {};
    const status = msg.status || meta.extensions?.status || 'completed';
    const contextItems = meta.context_items || [];
    const thoughts = meta.thoughts || [];
    const plan = meta.plan || null;
    const toolCalls = meta.tool_calls || [];
    const logs = meta.logs || [];
    const usage = msg.usage || {};
    const runInfo = meta.extensions || {};

    let inspectorHtml = '';

    // 1. Context items accordion
    if (contextItems.length > 0) {
      const itemsList = contextItems.map(c => `
        <div class="context-file-card">
          <span>📄 ${escapeHtml(c.label || c.path || c.kind)}</span>
          <span style="color:#64748b;">~${Math.round((c.content || '').length / 4)} tokens</span>
        </div>
      `).join('');

      inspectorHtml += `
        <div class="inspector-tray" id="tray-context-${msg.id}">
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

    // 2. Thoughts / Reasoning accordion
    if (thoughts.length > 0) {
      const thoughtSteps = thoughts.map(t => `
        <div class="thought-step-card">
          <strong>🧠 Thought:</strong>
          <div>${escapeHtml(t.text)}</div>
        </div>
      `).join('');

      inspectorHtml += `
        <div class="inspector-tray" id="tray-thoughts-${msg.id}">
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

    // 3. Execution plan accordion
    if (plan && plan.steps && plan.steps.length > 0) {
      const stepRows = plan.steps.map(s => `
        <div class="plan-step-row">
          <span class="step-status-icon ${s.status}">${s.status === 'done' ? '✓' : s.status === 'in_progress' ? '●' : '○'}</span>
          <span>${escapeHtml(s.title)}</span>
        </div>
      `).join('');

      inspectorHtml += `
        <div class="inspector-tray open" id="tray-plan-${msg.id}">
          <button type="button" class="inspector-tray-toggle">
            <span>📋 Execution Plan: ${escapeHtml(plan.summary || 'Step-by-step')}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <div class="inspector-tray-body">
            <div class="plan-container">${stepRows}</div>
          </div>
        </div>
      `;
    }

    // 4. Tool calls & logs accordion
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

      inspectorHtml += `
        <div class="inspector-tray" id="tray-tools-${msg.id}">
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
      const response = await API.sendChatMessage(activeSessionId, {
        user_prompt: userPrompt,
        system_instructions: systemPrompt,
        provider_id: providerId,
        model: model
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

  function handleStreamEvent(event, messageId) {
    const type = event.type;
    const data = event.data || {};

    const msgElement = document.getElementById(`msg-${messageId}`);
    const contentElement = document.getElementById(`msg-content-${messageId}`);

    if (type === 'token') {
      const token = data.text || '';
      if (contentElement) {
        contentElement.innerHTML += escapeHtml(token);
        scrollToBottom();
      }
    } else if (type === 'thought') {
      // Thought received
      console.log('Thought step:', data.text);
    } else if (type === 'done') {
      // Finished!
      if (msgElement) {
        const pill = msgElement.querySelector('.msg-status-pill');
        if (pill) {
          pill.className = 'msg-status-pill completed';
          pill.textContent = '✓ Completed';
        }
      }
      el.btnExecute.style.display = 'inline-flex';
      el.btnStop.style.display = 'none';

      // Refresh full session from server to capture updated tokens & DB state
      API.fetchChatSession(activeSessionId).then(session => {
        activeSessionData = session;
        updateSessionTelemetryBar();
        renderChatSessionsList();
      });

    } else if (type === 'error') {
      if (msgElement) {
        const pill = msgElement.querySelector('.msg-status-pill');
        if (pill) {
          pill.className = 'msg-status-pill error';
          pill.textContent = '✕ Error';
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
