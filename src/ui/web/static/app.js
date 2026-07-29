const markedOptions = { breaks: true, gfm: true };

let streaming = false;
let currentAssistantMsgEl = null;
let currentReasoningEl = null;
let currentToolAreaEl = null;
let abortController = null;
let ctrlCPressed = false;
let ctrlCTimer = null;
let currentProjectId = null;

const msgContainer = document.getElementById('msg-list');
const inputBox = document.getElementById('input-box');
const inputBoxWrap = document.getElementById('input-box-wrap');
const contextEditor = document.getElementById('context-editor');
const projectList = document.getElementById('project-list');
const projectNameDisplay = document.getElementById('project-name-display');

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function scrollBottom() {
  requestAnimationFrame(() => {
    msgContainer.scrollTop = msgContainer.scrollHeight;
  });
}

function collapseSection(titleEl) {
  const section = titleEl.dataset.section;
  const body = document.getElementById('section-' + section);
  if (!body) return;
  const open = body.classList.toggle('open');
  titleEl.classList.toggle('open');
}

document.querySelectorAll('.debug-section-title').forEach(el => {
  el.addEventListener('click', () => collapseSection(el));
});

// ── Project sidebar ──────────────────────────────────────

function renderProjects(data) {
  projectList.innerHTML = '';
  for (const p of data.projects) {
    addProjectItem(p, p.id === data.current_id);
  }
  projectNameDisplay.textContent = '';
  const cur = data.projects.find(p => p.id === data.current_id);
  if (cur) {
    projectNameDisplay.textContent = '· ' + cur.name;
    currentProjectId = cur.id;
  }
}

function addProjectItem(p, active) {
  const existing = projectList.querySelector(`[data-id="${p.id}"]`);
  if (existing) existing.remove();

  const div = document.createElement('div');
  div.className = 'project-item' + (active ? ' active' : '');
  div.dataset.id = p.id;
  div.innerHTML = `
    <span class="project-name">${escHtml(p.name)}</span>
    <span class="project-del" title="Delete project">&times;</span>`;
  div.addEventListener('click', (e) => {
    if (e.target.classList.contains('project-del')) return;
    if (p.id === currentProjectId) return;
    switchProject(p.id);
  });
  div.querySelector('.project-del').addEventListener('click', (e) => {
    e.stopPropagation();
    deleteProject(p.id);
  });
  projectList.appendChild(div);
}

function updateProjectName(projectId, name) {
  const item = projectList.querySelector(`[data-id="${projectId}"]`);
  if (item) {
    item.querySelector('.project-name').textContent = name;
  }
  if (projectId === currentProjectId) {
    projectNameDisplay.textContent = '· ' + name;
  }
}

function loadProjects() {
  return fetch('/api/projects')
    .then(r => r.json())
    .then(data => {
      renderProjects(data);
      return data;
    });
}

function switchProject(projectId) {
  if (projectId === currentProjectId) return;
  fetch('/api/projects/switch/' + projectId, { method: 'PUT' })
    .then(r => r.json())
    .then(data => {
      currentProjectId = projectId;
      msgContainer.querySelectorAll('.msg').forEach(el => el.remove());
      for (const msg of data.messages) {
        renderMessage(msg);
      }
      scrollBottom();
      updateContextEditor(data.messages);
      loadProjects();
      updateStatus();
    })
    .catch(e => console.error('Switch failed:', e));
}

function createProject() {
  fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: '' }),
  })
    .then(r => r.json())
    .then(data => {
      loadProjects();
      switchProject(data.id);
    })
    .catch(e => console.error('Create failed:', e));
}

function deleteProject(projectId) {
  if (!confirm('Delete this project and all its conversations?')) return;
  fetch('/api/projects/' + projectId, { method: 'DELETE' })
    .then(r => r.json())
    .then(() => {
      if (projectId === currentProjectId) {
        currentProjectId = null;
        msgContainer.querySelectorAll('.msg').forEach(el => el.remove());
        updateContextEditor([]);
      }
      loadProjects();
    })
    .catch(e => console.error('Delete failed:', e));
}

document.getElementById('btn-new-project').addEventListener('click', createProject);

// ── Message rendering ────────────────────────────────────

function renderMessage(msg) {
  if (msg.role === 'system') return;
  if (msg.role === 'user') {
    const div = document.createElement('div');
    div.className = 'msg user';
    div.innerHTML = `<div class="msg-body">${escHtml(msg.content)}</div>`;
    msgContainer.appendChild(div);
  } else if (msg.role === 'assistant') {
    const div = document.createElement('div');
    div.className = 'msg assistant';
    let html = '';

    const reasoning = msg.reasoning_content || msg.reasoning || '';
    const content = msg.content || '';
    const toolCalls = msg.tool_calls || [];

    if (reasoning) {
      html += `<div class="reasoning-wrap">
        <div class="collapse-toggle open">
          <span class="arrow">&#9654;</span>
          <span class="tag reasoning">REASONING</span>
          <span>Thinking</span>
        </div>
        <div class="collapse-body open reasoning-content">${escHtml(reasoning)}</div>
      </div>`;
      html += `<div class="reasoning-divider"></div>`;
    }
    html += `<div class="msg-body">${marked.parse(content || '', markedOptions)}</div>`;

    if (toolCalls.length) {
      html += `<div class="tools-wrap">
        <div class="collapse-toggle open">
          <span class="arrow">&#9654;</span>
          <span class="tag tool">TOOLS</span>
          <span>Tool Calls</span>
        </div>
        <div class="collapse-body open tool-content">`;
      for (const tc of toolCalls) {
        const fn = tc.function || {};
        let argsHtml = '';
        try {
          const args = typeof fn.arguments === 'string' ? JSON.parse(fn.arguments) : fn.arguments;
          if (typeof args === 'object' && args !== null) {
            argsHtml = Object.entries(args)
              .map(([k, v]) => escHtml(k) + ': ' + escHtml(JSON.stringify(v)))
              .join('\n');
          } else {
            argsHtml = escHtml(String(args));
          }
        } catch { argsHtml = escHtml(fn.arguments || ''); }
        html += `<div class="tool-item">
          <div class="tool-name">${escHtml(tc.name || fn.name || '')}</div>
          <div class="tool-args">${argsHtml}</div>
        </div>`;
      }
      html += `</div></div>`;
    }
    div.innerHTML = html;

    div.querySelectorAll('.collapse-toggle').forEach(toggle => {
      const body = toggle.nextElementSibling;
      if (body && body.classList.contains('collapse-body')) {
        toggle.addEventListener('click', () => {
          toggle.classList.toggle('open');
          body.classList.toggle('open');
        });
      }
    });

    msgContainer.appendChild(div);
  } else if (msg.role === 'tool') {
    const lastAssistant = msgContainer.querySelector('.msg.assistant:last-child');
    if (lastAssistant) {
      const toolsWrap = lastAssistant.querySelector('.tools-wrap');
      const toolContent = lastAssistant.querySelector('.tool-content');
      if (toolContent) {
        const resultToggle = document.createElement('div');
        resultToggle.className = 'collapse-toggle open';
        resultToggle.style.cssText = 'border-top:1px solid var(--border);padding:4px 12px';
        const label = msg.tool_call_id || '';
        resultToggle.innerHTML = `
          <span class="arrow">&#9654;</span>
          <span class="tag tool">RESULT</span>
          <span style="font-size:11px;color:var(--tool-result)">${escHtml(label)}</span>`;
        const resultBody = document.createElement('div');
        resultBody.className = 'collapse-body open tool-result-content';
        resultBody.textContent = msg.content || '';
        resultToggle.addEventListener('click', () => {
          resultToggle.classList.toggle('open');
          resultBody.classList.toggle('open');
        });
        toolContent.parentNode.insertBefore(resultToggle, toolContent.nextSibling);
        resultToggle.parentNode.insertBefore(resultBody, resultToggle.nextSibling);
      }
    }
  }
}

// ── Streaming ────────────────────────────────────────────

function addUserMessage(text) {
  const div = document.createElement('div');
  div.className = 'msg user';
  div.innerHTML = `<div class="msg-body">${escHtml(text)}</div>`;
  msgContainer.appendChild(div);
  scrollBottom();
  return div;
}

function createAssistantMessage() {
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `
    <div class="reasoning-wrap" style="display:none">
      <div class="collapse-toggle open">
        <span class="arrow">&#9654;</span>
        <span class="tag reasoning">REASONING</span>
        <span>Thinking</span>
      </div>
      <div class="collapse-body open reasoning-content"></div>
    </div>
    <div class="reasoning-divider" style="display:none"></div>
    <div class="msg-body"></div>
    <div class="tools-wrap" style="display:none">
      <div class="collapse-toggle open">
        <span class="arrow">&#9654;</span>
        <span class="tag tool">TOOLS</span>
        <span>Tool Calls</span>
      </div>
      <div class="collapse-body open tool-content"></div>
    </div>`;
  msgContainer.appendChild(div);
  scrollBottom();
  return div;
}

function createThinkingIndicator() {
  const div = document.createElement('div');
  div.className = 'thinking-indicator active';
  div.innerHTML = '<div class="spinner"></div> The model is thinking...';
  msgContainer.appendChild(div);
  scrollBottom();
  return div;
}

function removeThinkingIndicator() {
  const el = msgContainer.querySelector('.thinking-indicator');
  if (el) el.remove();
}

function getOrCreateAssistantMsg() {
  if (!currentAssistantMsgEl) {
    currentAssistantMsgEl = createAssistantMessage();
    currentReasoningEl = null;
    currentToolAreaEl = null;
  }
  return currentAssistantMsgEl;
}

function getReasoningBody(msgEl) {
  const wrap = msgEl.querySelector('.reasoning-wrap');
  if (!wrap) return null;
  if (wrap.style.display === 'none') wrap.style.display = '';
  const cb = wrap.querySelector('.collapse-body');
  const toggle = wrap.querySelector('.collapse-toggle');
  if (!currentReasoningEl) {
    currentReasoningEl = cb;
    toggle.addEventListener('click', () => {
      toggle.classList.toggle('open');
      cb.classList.toggle('open');
    });
  }
  return cb;
}

function getToolsBody(msgEl) {
  const wrap = msgEl.querySelector('.tools-wrap');
  if (!wrap) return null;
  if (wrap.style.display === 'none') wrap.style.display = '';
  const cb = wrap.querySelector('.collapse-body');
  const toggle = wrap.querySelector('.collapse-toggle');
  if (!currentToolAreaEl) {
    currentToolAreaEl = cb;
    toggle.addEventListener('click', () => {
      toggle.classList.toggle('open');
      cb.classList.toggle('open');
    });
  }
  return cb;
}

async function sendMessage() {
  const text = inputBox.value.trim();
  if (!text || streaming) return;

  inputBox.value = '';
  streaming = true;
  abortController = new AbortController();
  inputBoxWrap.classList.add('working');

  addUserMessage(text);

  const thinkingEl = createThinkingIndicator();
  currentAssistantMsgEl = null;
  currentReasoningEl = null;
  currentToolAreaEl = null;

  try {
    const resp = await fetch('/api/chat/sse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: text }),
      signal: abortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let curEvent = '';
    let curData = '';

    function dispatchEvent(eventType, data) {
      switch (eventType) {
        case 'project-info':
          currentProjectId = data.id;
          addProjectItem({id: data.id, name: data.name}, true);
          projectNameDisplay.textContent = '· ' + data.name;
          break;

        case 'project-name':
          updateProjectName(data.id, data.name);
          break;

        case 'step-start':
          currentAssistantMsgEl = null;
          currentReasoningEl = null;
          currentToolAreaEl = null;
          removeThinkingIndicator();
          break;

        case 'reasoning-start':
          getOrCreateAssistantMsg();
          getReasoningBody(currentAssistantMsgEl);
          break;

        case 'reasoning-end': {
          const msgEl = getOrCreateAssistantMsg();
          const divider = msgEl.querySelector('.reasoning-divider');
          if (divider) divider.style.display = '';
          break;
        }

        case 'reasoning-delta':
          if (currentReasoningEl) {
            currentReasoningEl.textContent += data;
            scrollBottom();
          }
          break;

        case 'text-start':
          getOrCreateAssistantMsg();
          break;

        case 'text-delta': {
          const msgEl = getOrCreateAssistantMsg();
          const body = msgEl.querySelector('.msg-body');
          if (body) {
            const raw = (body.dataset.raw || body.textContent) + data;
            body.dataset.raw = raw;
            body.innerHTML = marked.parse(raw, markedOptions);
            scrollBottom();
          }
          break;
        }

        case 'tool-call': {
          removeThinkingIndicator();
          const msgEl = getOrCreateAssistantMsg();
          const tb = getToolsBody(msgEl);
          const toolDiv = document.createElement('div');
          toolDiv.className = 'tool-item';
          let argsHtml = '';
          if (typeof data.input === 'object' && data.input !== null) {
            argsHtml = Object.entries(data.input)
              .map(([k, v]) => escHtml(JSON.stringify(k)) + ': ' + escHtml(JSON.stringify(v)))
              .join('\n');
          } else {
            argsHtml = escHtml(String(data.input));
          }
          toolDiv.innerHTML = `
            <div class="tool-name">${escHtml(data.name)}</div>
            <div class="tool-args">${argsHtml}</div>`;
          tb.appendChild(toolDiv);
          scrollBottom();
          break;
        }

        case 'tool-result': {
          if (!currentToolAreaEl) break;
          const label = data.attachments?.length
            ? `${data.name} (${data.attachments.length} attachment${data.attachments.length > 1 ? 's' : ''})`
            : data.name;
          const resultDiv = document.createElement('div');
          resultDiv.className = 'collapse-toggle open';
          resultDiv.style.cssText = 'border-top:1px solid var(--border);padding:4px 12px';
          resultDiv.innerHTML = `
            <span class="arrow">&#9654;</span>
            <span class="tag tool">RESULT</span>
            <span style="font-size:11px;color:var(--tool-result)">${escHtml(label)}</span>`;
          const resultBody = document.createElement('div');
          resultBody.className = 'collapse-body open tool-result-content';
          resultBody.textContent = data.result || '';
          resultDiv.addEventListener('click', () => {
            resultDiv.classList.toggle('open');
            resultBody.classList.toggle('open');
          });
          currentToolAreaEl.appendChild(resultDiv);
          currentToolAreaEl.appendChild(resultBody);
          scrollBottom();
          break;
        }

        case 'tool-error': {
          if (!currentToolAreaEl) break;
          const errDiv = document.createElement('div');
          errDiv.className = 'collapse-toggle open';
          errDiv.style.cssText = 'border-top:1px solid var(--border);padding:4px 12px';
          errDiv.innerHTML = `
            <span class="arrow">&#9654;</span>
            <span class="tag error">ERROR</span>
            <span style="font-size:11px;color:var(--error)">${escHtml(data.name)}</span>`;
          const errBody = document.createElement('div');
          errBody.className = 'collapse-body open tool-error-content';
          errBody.textContent = data.error || '';
          errDiv.addEventListener('click', () => {
            errDiv.classList.toggle('open');
            errBody.classList.toggle('open');
          });
          currentToolAreaEl.appendChild(errDiv);
          currentToolAreaEl.appendChild(errBody);
          scrollBottom();
          break;
        }

        case 'step-finish':
          if (data.usage) {
            const tu = data.usage;
            document.getElementById('st-total').textContent = tu.total_tokens ?? '—';
            const limit = data.context_limit || 0;
            const pct = data.context_usage_pct || 0;
            document.getElementById('st-context').textContent =
              limit > 0 ? (tu.total_tokens ?? 0) + ' / ' + limit + ' (' + pct + '%)' : '—';
            document.getElementById('hdr-tokens').textContent =
              (tu.prompt_tokens ?? '?') + '→' + (tu.completion_tokens ?? '?') + ' (' + (tu.total_tokens ?? '?') + ')';
          }
          break;

        case 'finish':
          break;

        case 'provider-error':
          removeThinkingIndicator();
          const errMsg = document.createElement('div');
          errMsg.className = 'msg assistant';
          errMsg.innerHTML = `<div class="msg-body" style="color:var(--error)">${escHtml(data.error || 'Unknown error')}</div>`;
          msgContainer.appendChild(errMsg);
          scrollBottom();
          break;

        case 'done':
          if (data.messages) {
            updateContextEditor(data.messages);
          }
          break;
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n');
      buf = parts.pop() || '';

      for (const line of parts) {
        if (line.startsWith('event: ')) {
          curEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          curData = line.slice(6);
          if (curEvent && curData) {
            try {
              dispatchEvent(curEvent, JSON.parse(curData));
            } catch (e) {
              console.warn('SSE parse error:', curEvent, curData, e);
            }
            curEvent = '';
            curData = '';
          }
        }
      }
    }

  } catch (e) {
    removeThinkingIndicator();
    if (e.name !== 'AbortError') {
      const errMsg = document.createElement('div');
      errMsg.className = 'msg assistant';
      errMsg.innerHTML = `<div class="msg-body" style="color:var(--error)">${escHtml(e.message)}</div>`;
      msgContainer.appendChild(errMsg);
      scrollBottom();
    }
  } finally {
    streaming = false;
    inputBox.focus();
    inputBoxWrap.classList.remove('working');
    inputBoxWrap.classList.remove('interrupt-pending');
    abortController = null;
    ctrlCPressed = false;
    if (ctrlCTimer) { clearTimeout(ctrlCTimer); ctrlCTimer = null; }
    updateStatus();
  }
}

function updateContextEditor(messages) {
  contextEditor.value = JSON.stringify(messages, null, 2);
}

function updateHeader(model, tokens, workdir) {
  document.getElementById('hdr-model').textContent = model || '—';
  const t = tokens || {};
  document.getElementById('hdr-tokens').textContent =
    (t.prompt_tokens ?? '?') + '→' + (t.completion_tokens ?? '?') + ' (' + (t.total_tokens ?? '?') + ')';
  document.getElementById('hdr-workdir').textContent = workdir || '—';
}

function updateStatus() {
  fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      document.getElementById('st-model').textContent = data.model || '—';
      document.getElementById('st-base-url').textContent = data.base_url || '—';
      const tu = data.token_usage || {};
      document.getElementById('st-total').textContent = tu.total_tokens ?? '—';
      const limit = data.context_limit || 0;
      const pct = data.context_usage_pct || 0;
      document.getElementById('st-context').textContent =
        limit > 0 ? (tu.total_tokens ?? 0) + ' / ' + limit + ' (' + pct + '%)' : '—';
      document.getElementById('st-workdir').textContent = data.workdir || '—';
      updateHeader(data.model, data.token_usage, data.workdir);
    })
    .catch(() => {});
}

function loadConfig() {
  fetch('/api/config')
    .then(r => r.json())
    .then(cfg => {
      document.getElementById('cfg-url').value = cfg.base_url || '';
      document.getElementById('cfg-model').value = cfg.model || '';
    })
    .catch(() => {});
}

function loadMessages() {
  fetch('/api/messages')
    .then(r => r.json())
    .then(data => {
      updateContextEditor(data.messages || []);
    })
    .catch(() => {});
}

// ── Initialization ───────────────────────────────────────

inputBox.addEventListener('keydown', e => {
  if (e.key === 'c' && e.ctrlKey && streaming) {
    e.preventDefault();
    if (!ctrlCPressed) {
      ctrlCPressed = true;
      inputBoxWrap.classList.add('interrupt-pending');
      inputBoxWrap.classList.remove('working');
      ctrlCTimer = setTimeout(() => {
        ctrlCPressed = false;
        inputBoxWrap.classList.remove('interrupt-pending');
        inputBoxWrap.classList.add('working');
      }, 3000);
    } else {
      inputBoxWrap.classList.remove('interrupt-pending');
      if (ctrlCTimer) { clearTimeout(ctrlCTimer); ctrlCTimer = null; }
      ctrlCPressed = false;
      if (abortController) {
        abortController.abort();
      }
    }
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.getElementById('cfg-apply').addEventListener('click', () => {
  const body = {
    base_url: document.getElementById('cfg-url').value.trim(),
    model: document.getElementById('cfg-model').value.trim(),
  };
  fetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(r => r.json())
    .then(() => {
      updateStatus();
      loadMessages();
    })
    .catch(e => console.error('Config update failed:', e));
});

document.getElementById('cfg-refresh').addEventListener('click', loadConfig);

document.getElementById('ctx-apply').addEventListener('click', () => {
  let msgs;
  try {
    msgs = JSON.parse(contextEditor.value);
    if (!Array.isArray(msgs)) throw new Error('Must be an array');
  } catch (e) {
    alert('Invalid JSON: ' + e.message);
    return;
  }
  fetch('/api/messages', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: msgs }),
  })
    .then(r => r.json())
    .then(() => updateStatus())
    .catch(e => console.error('Messages update failed:', e));
});

document.getElementById('ctx-clear').addEventListener('click', () => {
  if (!confirm('Clear all messages?')) return;
  fetch('/api/messages/clear', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      updateContextEditor(data.messages || []);
      msgContainer.querySelectorAll('.msg').forEach(el => el.remove());
      updateStatus();
    })
    .catch(e => console.error('Clear failed:', e));
});

document.getElementById('btn-sidebar-toggle').addEventListener('click', () => {
  const sidebar = document.getElementById('sidebar');
  sidebar.style.display = sidebar.style.display === 'none' ? '' : 'none';
});

// ── Bootstrap ────────────────────────────────────────────
loadProjects().then(data => {
  if (data.current_id) {
    fetch('/api/messages')
      .then(r => r.json())
      .then(data => {
        for (const msg of data.messages || []) {
          renderMessage(msg);
        }
        scrollBottom();
        updateContextEditor(data.messages || []);
      })
      .catch(() => {});
  }
});
loadConfig();
updateStatus();


