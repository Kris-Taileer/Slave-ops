/**
 * Aether — AD Control Panel
 * Моки. Комменты где потом втыкать бэк.
 *
 * GET  /api/services
 * POST /api/services/{id}/start|stop|restart
 * GET/POST /api/services/{id}/compose
 * POST /api/compose/validate
 * GET  /api/utils
 * GET  /api/network
 * GET  /api/logs (или SSE)
 */

const API_BASE = '';
const POLL_INTERVAL = 60000;
const TOKEN_KEY = 'aether_api_token';

function getToken() {
  let t = localStorage.getItem(TOKEN_KEY);
  if (!t) {
    t = (prompt('API-токен панели (см. state/token):') || '').trim();
    if (t) localStorage.setItem(TOKEN_KEY, t);
  }
  return t;
}

async function apiPost(path) {
  const token = getToken();
  if (!token) throw new Error('нет токена');

  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });

  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    throw new Error('неверный токен');
  }

  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    throw new Error((data && data.error) || `HTTP ${res.status}`);
  }
  return data;
}

// General authed request helper (GET/POST/PUT/DELETE + JSON), used by the
// Blocks tab which — unlike the mock tabs above — talks to the real backend.
async function api(method, path, body) {
  const token = getToken();
  if (!token) throw new Error('нет токена');
  const opt = { method, headers: { 'Authorization': `Bearer ${token}` } };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + path, opt);
  if (res.status === 401) { localStorage.removeItem(TOKEN_KEY); throw new Error('неверный токен'); }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

// Real docker-compose services are managed via monitor.sh / the backend API.
// The old hardcoded stub services were removed; this list is intentionally empty.
const services = [];

const utils = [
  {
    key: 'packmate', name: 'Packmate', status: 'up', port: '65000', creds: 'см. .packmate-credentials', extra: 'iface game · regex flag',
    actions: [
      { action: 'start', label: 'Start', cls: 'btn-success' },
      { action: 'restart', label: 'Restart', cls: 'btn-secondary' },
      { action: 'stop', label: 'Stop', cls: 'btn-danger' },
      { action: 'status', label: 'Status', cls: 'btn-ghost' },
      { action: 'configure', label: 'Configure', cls: 'btn-secondary' }
    ]
  },
  {
    key: 'farm', name: 'Ферма', status: 'up', port: '31337', creds: 'token : team_secret_xx', extra: 'teams 1-16 · HTTP',
    actions: [
      { action: 'init', label: 'Init', cls: 'btn-success' },
      { action: 'restart', label: 'Restart', cls: 'btn-secondary' },
      { action: 'down', label: 'Down', cls: 'btn-danger' },
      { action: 'status', label: 'Status', cls: 'btn-ghost' }
    ]
  },
  {
    key: 'firegex', name: 'Firegex', status: 'up', port: '8750', creds: 'admin : firegex', extra: 'transparent mode',
    actions: [
      { action: 'start', label: 'Start', cls: 'btn-success' },
      { action: 'restart', label: 'Restart', cls: 'btn-secondary' },
      { action: 'stop', label: 'Stop', cls: 'btn-danger' },
      { action: 'status', label: 'Status', cls: 'btn-ghost' }
    ]
  }
];

// Network / logs / vulnerability panels no longer ship stub data.
const networkInfo = [];

let logs = [];

const vulnDatabase = {};

let currentTab = 'services';
let currentComposeId = null;
let editor = null;
let usePlainTextarea = false;
let autoScroll = true;

document.addEventListener('DOMContentLoaded', () => {
  try {
    renderServices();
    renderUtils();
    renderNetwork();
    renderLogs();
    setupNav();
    setupCompose();
    setupActions();
    initBlocks();
    updateLastTime();
    updateGlobalStatus();

    setInterval(() => {
      try { mockPing(); } catch (e) {}
      updateLastTime();
    }, POLL_INTERVAL);

    console.log('[Aether] init ok');
  } catch (err) {
    console.error('[Aether] init failed', err);
  }
});

function renderServices(filter = '') {
  const grid = document.getElementById('services-grid');
  if (!grid) return;

  const filtered = services.filter(s =>
    s.name.toLowerCase().includes((filter || '').toLowerCase())
  );

  if (!filtered.length) {
    grid.innerHTML = '<div style="color:#6b6480;padding:20px">Сервисы не найдены</div>';
    return;
  }

  grid.innerHTML = filtered.map(s => {
    const vulnCount = s.vulns ? s.vulns.length : 0;
    const vulnBadge = vulnCount > 0
      ? `<span style="background:rgba(239,68,68,0.15);color:#ef4444;font-size:11px;font-weight:700;padding:3px 8px;border-radius:20px;margin-left:8px">${vulnCount} vuln</span>`
      : '';

    return `
    <div class="card">
      <div class="card-header">
        <div class="card-title">${escapeHtml(s.name)}${vulnBadge}</div>
        <div class="card-status ${s.status}">
          <span class="status-dot ${s.status}" style="width:7px;height:7px;display:inline-block"></span>
          ${s.status}
        </div>
      </div>
      <div class="card-meta">
        <span>Ports: <b>${escapeHtml(s.ports)}</b></span>
        <span>Containers: ${escapeHtml(s.containers)}</span>
      </div>
      <div class="card-actions">
        <button class="btn btn-sm btn-success" onclick="serviceAction('${s.id}','start')">Start</button>
        <button class="btn btn-sm btn-danger" onclick="serviceAction('${s.id}','stop')">Stop</button>
        <button class="btn btn-sm btn-secondary" onclick="serviceAction('${s.id}','restart')">Restart</button>
        <button class="btn btn-sm btn-ghost" onclick="scanService('${s.id}')">Scan</button>
      </div>
      ${vulnCount > 0 ? `
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid rgba(124,58,237,0.12)">
          ${s.vulns.map(v => `
            <div style="font-size:12.5px;margin-bottom:6px;display:flex;gap:8px;align-items:flex-start">
              <span style="color:${v.severity === 'high' ? '#ef4444' : v.severity === 'medium' ? '#f59e0b' : '#6b6480'};font-weight:700;min-width:52px">${v.severity.toUpperCase()}</span>
              <span>${escapeHtml(v.title)}</span>
            </div>
          `).join('')}
        </div>
      ` : ''}
    </div>
  `}).join('');
}

function renderUtils() {
  const grid = document.getElementById('utils-grid');
  if (!grid) return;

  grid.innerHTML = utils.map(u => `
    <div class="util-card">
      <h3>
        ${escapeHtml(u.name)}
        <span class="util-status" style="background:${u.status === 'up' ? 'rgba(16,185,129,0.13)' : 'rgba(239,68,68,0.13)'};color:${u.status === 'up' ? '#10b981' : '#ef4444'}">
          ${u.status.toUpperCase()}
        </span>
      </h3>
      <div class="util-row"><span class="util-label">Port</span><span class="util-value">${escapeHtml(u.port)}</span></div>
      <div class="util-row"><span class="util-label">Credentials</span><span class="util-value">${escapeHtml(u.creds)}</span></div>
      <div class="util-row"><span class="util-label">Info</span><span class="util-value">${escapeHtml(u.extra)}</span></div>
      <div class="util-actions">
        ${u.key ? u.actions.map(a =>
          `<button class="btn btn-sm ${a.cls}" onclick="utilAction('${u.key}','${a.action}','${escapeHtml(u.name)}')">${escapeHtml(a.label)}</button>`
        ).join('') : `
        <button class="btn btn-sm btn-secondary" onclick="restartUtil('${escapeHtml(u.name)}')">Restart</button>
        <button class="btn btn-sm btn-ghost" onclick="toast('info','Open ${escapeHtml(u.name)}')">Open</button>
        `}
      </div>
    </div>
  `).join('');
}

function renderNetwork() {
  const grid = document.getElementById('network-info');
  if (!grid) return;

  grid.innerHTML = networkInfo.map(n => `
    <div class="net-card">
      <h4>${escapeHtml(n.title)}</h4>
      <div class="value">${escapeHtml(n.value)}</div>
      <div class="sub">${escapeHtml(n.sub)}</div>
    </div>
  `).join('');
}

function renderLogs() {
  const win = document.getElementById('logs-window');
  if (!win) return;

  win.innerHTML = logs.map(l => `
    <div class="log-line ${l.level}"><span class="time">[${l.time}]</span>${escapeHtml(l.msg)}</div>
  `).join('');

  if (autoScroll) win.scrollTop = win.scrollHeight;
}

function setupNav() {
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  currentTab = tab;

  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-tab="${tab}"]`)?.classList.add('active');

  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById(`tab-${tab}`)?.classList.add('active');

  const titles = {
    services: ['Сервисы', 'Управление и мониторинг'],
    blocks: ['Блоки', 'Конвейер скриптов'],
    compose: ['Compose', 'Редактирование docker-compose.yaml'],
    utils: ['Утилиты', 'Packmate · Ферма · Firegex'],
    network: ['Сеть', 'Информация о инфраструктуре'],
    logs: ['Логи', 'События и ошибки']
  };

  if (titles[tab]) {
    document.getElementById('page-title').textContent = titles[tab][0];
    document.getElementById('page-subtitle').textContent = titles[tab][1];
  }

  if (tab === 'logs') renderLogs();
  if (tab === 'compose' && editor && !usePlainTextarea) {
    setTimeout(() => { try { editor.refresh(); } catch(e) {} }, 40);
  }
  if (tab === 'blocks') {
    loadBlocks();
    startBlocksPolling();
    if (selectedBlockId) startOutputPolling();
    if (blockEditor) setTimeout(() => { try { blockEditor.refresh(); } catch (e) {} }, 40);
  } else {
    stopBlocksPolling();
    stopOutputPolling();
  }
}

function setupCompose() {
  const list = document.getElementById('compose-list');
  if (list) {
    list.innerHTML = services.map(s =>
      `<div class="compose-item" data-id="${s.id}" onclick="selectCompose('${s.id}')">${escapeHtml(s.name)}</div>`
    ).join('');
  }

  const ta = document.getElementById('compose-editor');
  if (!ta) return;

  if (typeof CodeMirror !== 'undefined') {
    try {
      editor = CodeMirror.fromTextArea(ta, {
        mode: 'yaml',
        theme: 'material-palenight',
        lineNumbers: true,
        indentUnit: 2,
        tabSize: 2,
        lineWrapping: true
      });
      usePlainTextarea = false;
    } catch (e) {
      usePlainTextarea = true;
      editor = null;
    }
  } else {
    usePlainTextarea = true;
    editor = null;
  }

  document.getElementById('validate-btn')?.addEventListener('click', validateYaml);
  document.getElementById('save-compose-btn')?.addEventListener('click', saveCompose);
}

function getEditorValue() {
  if (editor && !usePlainTextarea) return editor.getValue();
  return document.getElementById('compose-editor')?.value || '';
}

function setEditorValue(val) {
  if (editor && !usePlainTextarea) {
    editor.setValue(val);
  } else {
    const ta = document.getElementById('compose-editor');
    if (ta) ta.value = val;
  }
}

function selectCompose(id) {
  currentComposeId = id;
  document.querySelectorAll('.compose-item').forEach(i => i.classList.remove('active'));
  document.querySelector(`.compose-item[data-id="${id}"]`)?.classList.add('active');

  const svc = services.find(s => s.id === id);
  if (!svc) return;

  document.getElementById('current-compose-name').textContent = svc.name;
  setEditorValue(svc.compose);

  const res = document.getElementById('validation-result');
  if (res) {
    res.textContent = '';
    res.className = 'validation-result';
  }

  if (editor && !usePlainTextarea) {
    setTimeout(() => { try { editor.refresh(); } catch(e) {} }, 30);
  }
}

function validateYaml() {
  const content = getEditorValue().trim();
  const result = document.getElementById('validation-result');
  if (!result) return;

  if (!content) {
    result.textContent = '✗ Пустой файл';
    result.className = 'validation-result err';
    return;
  }
  if (!content.includes('services:')) {
    result.textContent = '✗ Нет ключа services';
    result.className = 'validation-result err';
    return;
  }
  if (content.includes('\t')) {
    result.textContent = '✗ Не используй табы в YAML';
    result.className = 'validation-result err';
    return;
  }

  result.textContent = '✓ YAML ок (мок). На бэке будет docker compose config';
  result.className = 'validation-result ok';
  addLog('success', `YAML validated → ${currentComposeId || '?'}`);
  toast('success', 'YAML проверен');
}

function saveCompose() {
  if (!currentComposeId) {
    toast('warn', 'Сначала выбери сервис');
    return;
  }
  const svc = services.find(s => s.id === currentComposeId);
  if (!svc) return;

  svc.compose = getEditorValue();
  addLog('info', `Compose saved → ${svc.name}`);

  const result = document.getElementById('validation-result');
  if (result) {
    result.textContent = '✓ Сохранено (мок). В реале: docker compose up -d';
    result.className = 'validation-result ok';
  }
  toast('success', `${svc.name} обновлён`);
}

function setupActions() {
  document.getElementById('service-search')?.addEventListener('input', e => {
    renderServices(e.target.value);
  });

  document.getElementById('refresh-btn')?.addEventListener('click', () => {
    mockPing();
    updateLastTime();
    toast('info', 'Данные обновлены');
    addLog('info', 'Manual refresh');
  });

  document.getElementById('restart-all')?.addEventListener('click', () => {
    services.forEach(s => { if (s.status !== 'up') s.status = 'up'; });
    renderServices(document.getElementById('service-search')?.value || '');
    updateGlobalStatus();
    toast('warn', 'Restart All отправлен');
    addLog('warn', 'Restart All requested');
  });

  document.getElementById('scan-all')?.addEventListener('click', scanAll);

  document.getElementById('clear-logs')?.addEventListener('click', () => {
    logs = [];
    renderLogs();
  });

  document.getElementById('auto-scroll')?.addEventListener('change', e => {
    autoScroll = e.target.checked;
  });
}

function serviceAction(id, action) {
  const svc = services.find(s => s.id === id);
  if (!svc) return;

  if (action === 'start') {
    svc.status = 'up';
    addLog('success', `${svc.name} started`);
    toast('success', `${svc.name} → started`);
  } else if (action === 'stop') {
    svc.status = 'down';
    addLog('warn', `${svc.name} stopped`);
    toast('warn', `${svc.name} → stopped`);
  } else if (action === 'restart') {
    svc.status = 'up';
    addLog('info', `${svc.name} restarted`);
    toast('info', `${svc.name} → restarted`);
  }

  renderServices(document.getElementById('service-search')?.value || '');
  updateGlobalStatus();
}

function restartUtil(name) {
  toast('info', `${name}: restart queued`);
  addLog('info', `${name} restart requested`);
}

function utilAction(key, action, label) {
  if (key === 'farm' && action === 'init') {
    toast('warn', 'Init — интерактивный мастер, из веба не запускается');
    addLog('warn', 'Ферма: init — TUI, требует реального терминала. Запусти вручную: cd Farm && ./farm init (после проверки сервисов, до старта фарма)');
    return;
  }

  if (key === 'packmate' && action === 'configure') {
    toast('warn', 'Configure — интерактивный мастер, из веба не запускается');
    addLog('warn', 'Packmate: configure — TUI, требует реального терминала. Запусти вручную: cd packmate && ./packmate-setup.sh configure');
    return;
  }

  addLog('info', `${label}: ${action} requested`);
  apiPost(`/api/utils/${key}/${action}`)
    .then(() => {
      toast('success', `${label}: ${action} queued`);
      addLog('success', `${label}: ${action} queued on backend`);
    })
    .catch(err => {
      toast('error', `${label}: ${err.message}`);
      addLog('error', `${label} ${action} failed: ${err.message}`);
    });
}

function scanService(id) {
  const svc = services.find(s => s.id === id);
  if (!svc) return;

  toast('info', `Сканирую ${svc.name}...`);
  addLog('info', `Vulnerability scan started → ${svc.name}`);

  setTimeout(() => {
    const findings = vulnDatabase[id] || [
      { severity: 'low', title: 'No critical issues found' }
    ];

    const count = Math.floor(Math.random() * findings.length) + 1;
    svc.vulns = findings.slice(0, count);

    renderServices(document.getElementById('service-search')?.value || '');

    const high = svc.vulns.filter(v => v.severity === 'high').length;
    if (high > 0) {
      toast('error', `${svc.name}: ${svc.vulns.length} уязвимостей (${high} high)`);
      addLog('error', `Scan ${svc.name}: ${svc.vulns.length} findings, ${high} high`);
    } else {
      toast('warn', `${svc.name}: ${svc.vulns.length} уязвимостей`);
      addLog('warn', `Scan ${svc.name}: ${svc.vulns.length} findings`);
    }
  }, 700 + Math.random() * 800);
}

function scanAll() {
  toast('info', 'Полный скан всех сервисов...');
  addLog('info', 'Full vulnerability scan started');

  services.forEach((svc, i) => {
    setTimeout(() => scanService(svc.id), i * 550);
  });
}

function mockPing() {
  if (Math.random() > 0.65) {
    const idx = Math.floor(Math.random() * services.length);
    const old = services[idx].status;
    services[idx].status = old === 'up' ? (Math.random() > 0.5 ? 'warn' : 'down') : 'up';
    const level = services[idx].status === 'up' ? 'success' : 'error';
    addLog(level, `Healthcheck: ${services[idx].name} → ${services[idx].status.toUpperCase()}`);
  }
  renderServices(document.getElementById('service-search')?.value || '');
  updateGlobalStatus();
}

function updateGlobalStatus() {
  const pill = document.getElementById('global-status');
  if (!pill) return;

  const hasDown = services.some(s => s.status === 'down');
  const hasWarn = services.some(s => s.status === 'warn');

  if (hasDown) {
    pill.innerHTML = `<span class="status-dot down"></span><span>Degraded</span>`;
  } else if (hasWarn) {
    pill.innerHTML = `<span class="status-dot warn"></span><span>Warnings</span>`;
  } else {
    pill.innerHTML = `<span class="status-dot up"></span><span>All systems up</span>`;
  }
}

function addLog(level, msg) {
  const time = new Date().toTimeString().slice(0, 8);
  logs.push({ time, level, msg });
  if (logs.length > 150) logs.shift();
  if (currentTab === 'logs') renderLogs();
}

function updateLastTime() {
  const el = document.getElementById('last-update');
  if (el) el.textContent = new Date().toTimeString().slice(0, 8);
}

function toast(type, text) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = text;
  container.appendChild(el);

  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(10px)';
    el.style.transition = 'all 0.25s';
    setTimeout(() => el.remove(), 250);
  }, 2800);
}

function escapeHtml(str) {
  if (typeof str !== 'string') return str;
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/* ============================================================================
 * Blocks / pipeline tab — talks to the real backend (pipeline.store + runner)
 * ==========================================================================*/

let blocksData = [];
let selectedBlockId = null;
let blockEditor = null;
let blockOutputOffset = 0;
let blocksTimer = null;
let outputTimer = null;
let connectFrom = null;   // id of the block a connect-drag started from

function initBlocks() {
  renderLegend();
  const ta = document.getElementById('block-editor');
  if (ta && typeof CodeMirror !== 'undefined') {
    try {
      blockEditor = CodeMirror.fromTextArea(ta, {
        mode: 'python', theme: 'material-palenight',
        lineNumbers: true, indentUnit: 4, tabSize: 4, lineWrapping: true
      });
    } catch (e) { blockEditor = null; }
  }
  document.getElementById('block-add')?.addEventListener('click', addBlock);
  document.getElementById('blocks-preset')?.addEventListener('click', () => loadPresets('demo'));
  document.getElementById('blocks-preset-intro')?.addEventListener('click', () => loadPresets('intro'));
  document.getElementById('pipeline-run')?.addEventListener('click', () => {
    api('POST', '/api/pipeline/run')
      .then(() => { toast('info', 'Пайплайн запущен'); loadBlocks(); })
      .catch(err => toast('error', err.message));
  });
  document.getElementById('pipeline-stop')?.addEventListener('click', () => {
    api('POST', '/api/pipeline/stop')
      .then(() => { toast('warn', 'Остановлено'); loadBlocks(); })
      .catch(err => toast('error', err.message));
  });
  document.getElementById('insp-close')?.addEventListener('click', closeInspector);
  document.getElementById('insp-run')?.addEventListener('click', () => blockAction('run'));
  document.getElementById('insp-stop')?.addEventListener('click', () => blockAction('stop'));
  document.getElementById('insp-restart')?.addEventListener('click', () => blockAction('restart'));
  document.getElementById('insp-delete')?.addEventListener('click', deleteSelected);
  document.getElementById('insp-save')?.addEventListener('click', saveSelected);
  document.getElementById('insp-type')?.addEventListener('change', onTypeModeChange);
  document.getElementById('insp-mode')?.addEventListener('change', onTypeModeChange);
  document.getElementById('insp-venv')?.addEventListener('change', onTypeModeChange);

  // drag-to-connect + edge delete + modal dismiss
  document.addEventListener('mousemove', moveConnect);
  document.addEventListener('mouseup', endConnect);
  document.getElementById('graph-hit')?.addEventListener('click', onEdgeClick);
  const modal = document.getElementById('block-modal');
  modal?.addEventListener('mousedown', (e) => { if (e.target === modal) closeInspector(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.hidden) closeInspector();
  });
}

function renderLegend() {
  const el = document.getElementById('blocks-legend');
  if (!el) return;
  const items = [
    ['running', 'работает', 'var(--purple)'],
    ['success', 'успех', 'var(--success)'],
    ['error', 'ошибка/завис', 'var(--danger)'],
    ['blocked', 'ждёт/очередь', 'var(--warning)'],
    ['idle', 'idle/стоп', 'var(--text-muted)'],
  ];
  el.innerHTML = items.map(([, label, color]) =>
    `<span class="lg"><span class="dot" style="background:${color}"></span>${label}</span>`).join('');
}

function startBlocksPolling() { stopBlocksPolling(); blocksTimer = setInterval(loadBlocks, 2000); }
function stopBlocksPolling() { if (blocksTimer) clearInterval(blocksTimer); blocksTimer = null; }
function startOutputPolling() { stopOutputPolling(); pollOutput(); outputTimer = setInterval(pollOutput, 1000); }
function stopOutputPolling() { if (outputTimer) clearInterval(outputTimer); outputTimer = null; }

function statusClass(s) { return 'st-' + (s === 'hanging' ? 'error' : s); }

function loadBlocks() {
  return api('GET', '/api/blocks').then(data => {
    blocksData = data.blocks || [];
    renderGraph();
    if (selectedBlockId) {
      const b = blocksData.find(x => x.id === selectedBlockId);
      if (b) setInspStatus(b.status);
      else closeInspector();
    }
  }).catch(err => {
    if (currentTab === 'blocks') toast('error', 'Блоки: ' + err.message);
  });
}

function nodeHtml(b) {
  const badges = [b.type, b.mode];
  if (b.venv) badges.push('venv');
  if (b.pass_stdout) badges.push('argv');
  const id = escapeHtml(b.id);
  return `
  <div class="block-node ${statusClass(b.status)} ${b.id === selectedBlockId ? 'selected' : ''}" data-id="${id}">
    <div class="bn-name">
      <span>${escapeHtml(b.name)}</span>
      <span class="bn-status ${statusClass(b.status)}">${escapeHtml(b.status)}</span>
    </div>
    <div class="bn-badges">${badges.map(x => `<span class="bn-badge">${escapeHtml(x)}</span>`).join('')}</div>
    <div class="bn-actions">
      <button class="btn btn-success" title="Run" onclick="event.stopPropagation();nodeAction('${id}','run')">▶</button>
      <button class="btn btn-danger" title="Stop" onclick="event.stopPropagation();nodeAction('${id}','stop')">■</button>
      <button class="btn btn-ghost" title="Открыть" onclick="event.stopPropagation();selectBlock('${id}')">✎</button>
    </div>
    <span class="bn-handle" data-src="${id}" title="Потяни к другому блоку, чтобы связать"></span>
  </div>`;
}

function renderGraph() {
  const cols = {};
  blocksData.forEach(b => { (cols[b.level] = cols[b.level] || []).push(b); });
  const keys = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const columnsEl = document.getElementById('graph-columns');
  const emptyEl = document.getElementById('graph-empty');
  if (!columnsEl) return;
  columnsEl.innerHTML = keys.map(k =>
    `<div class="graph-col">${cols[k].map(nodeHtml).join('')}</div>`).join('');
  if (emptyEl) emptyEl.style.display = blocksData.length ? 'none' : 'flex';
  columnsEl.querySelectorAll('.block-node').forEach(n =>
    n.addEventListener('click', () => selectBlock(n.dataset.id)));
  columnsEl.querySelectorAll('.bn-handle').forEach(h =>
    h.addEventListener('mousedown', (e) => startConnect(e, h.dataset.src)));
  requestAnimationFrame(drawEdges);
}

function drawEdges() {
  const svg = document.getElementById('graph-edges');
  const hit = document.getElementById('graph-hit');
  const graph = document.getElementById('blocks-graph');
  if (!svg || !hit || !graph) return;
  const w = graph.scrollWidth, h = graph.scrollHeight;
  [svg, hit].forEach(s => { s.setAttribute('width', w); s.setAttribute('height', h); });
  const gr = graph.getBoundingClientRect();
  const byId = {}; blocksData.forEach(b => byId[b.id] = b);
  const defs = '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
    + 'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    + '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>';
  let vis = defs, hits = '';
  blocksData.forEach(b => {
    const toEl = graph.querySelector(`.block-node[data-id="${cssAttr(b.id)}"]`);
    if (!toEl) return;
    const tr = toEl.getBoundingClientRect();
    (b.depends_on || []).forEach(dep => {
      const fromEl = graph.querySelector(`.block-node[data-id="${cssAttr(dep)}"]`);
      if (!fromEl) return;
      const fr = fromEl.getBoundingClientRect();
      const x1 = fr.right - gr.left + graph.scrollLeft;
      const y1 = fr.top + fr.height / 2 - gr.top + graph.scrollTop;
      const x2 = tr.left - gr.left + graph.scrollLeft;
      const y2 = tr.top + tr.height / 2 - gr.top + graph.scrollTop;
      const mx = (x1 + x2) / 2;
      const src = byId[dep] ? byId[dep].status : 'idle';
      const cls = src === 'success' ? 'ok'
        : (['error', 'hanging', 'blocked', 'stopped'].includes(src) ? 'bad' : '');
      const d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
      vis += `<path class="edge ${cls}" d="${d}" marker-end="url(#arrow)"/>`;
      hits += `<path class="edge-hit" data-from="${escapeHtml(dep)}" data-to="${escapeHtml(b.id)}" d="${d}"/>`;
    });
  });
  svg.innerHTML = vis;
  hit.innerHTML = hits;
}

/* --- drag-to-connect + edge delete --- */

function graphPoint(clientX, clientY) {
  const graph = document.getElementById('blocks-graph');
  const gr = graph.getBoundingClientRect();
  return { x: clientX - gr.left + graph.scrollLeft, y: clientY - gr.top + graph.scrollTop };
}

function startConnect(e, fromId) {
  e.preventDefault();
  e.stopPropagation();
  connectFrom = fromId;
  stopBlocksPolling();               // freeze the graph while wiring
  document.body.style.userSelect = 'none';
}

function moveConnect(e) {
  if (!connectFrom) return;
  const graph = document.getElementById('blocks-graph');
  const drag = document.getElementById('graph-drag');
  const fromEl = graph && graph.querySelector(`.block-node[data-id="${cssAttr(connectFrom)}"]`);
  if (!fromEl || !drag) return;
  const gr = graph.getBoundingClientRect();
  const fr = fromEl.getBoundingClientRect();
  const x1 = fr.right - gr.left + graph.scrollLeft;
  const y1 = fr.top + fr.height / 2 - gr.top + graph.scrollTop;
  const p = graphPoint(e.clientX, e.clientY);
  const mx = (x1 + p.x) / 2;
  drag.setAttribute('width', graph.scrollWidth);
  drag.setAttribute('height', graph.scrollHeight);
  drag.innerHTML = `<path class="dragline" d="M${x1},${y1} C${mx},${y1} ${mx},${p.y} ${p.x},${p.y}"/>`;
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const tgt = el && el.closest ? el.closest('.block-node') : null;
  document.querySelectorAll('.block-node.drop-target').forEach(n => n.classList.remove('drop-target'));
  if (tgt && tgt.dataset.id !== connectFrom) tgt.classList.add('drop-target');
}

function endConnect(e) {
  if (!connectFrom) return;
  const from = connectFrom;
  connectFrom = null;
  document.body.style.userSelect = '';
  const drag = document.getElementById('graph-drag');
  if (drag) drag.innerHTML = '';
  document.querySelectorAll('.block-node.drop-target').forEach(n => n.classList.remove('drop-target'));
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const tgt = el && el.closest ? el.closest('.block-node') : null;
  if (tgt && tgt.dataset.id && tgt.dataset.id !== from) {
    addEdge(from, tgt.dataset.id);   // target depends on source
  } else {
    startBlocksPolling();
  }
}

function addEdge(from, to) {
  const b = blocksData.find(x => x.id === to);
  const deps = ((b && b.depends_on) || []).slice();
  if (deps.includes(from)) { toast('info', 'Связь уже есть'); startBlocksPolling(); return; }
  deps.push(from);
  api('PUT', '/api/blocks/' + encodeURIComponent(to), { depends_on: deps })
    .then(() => { toast('success', `связь ${from} → ${to}`); return loadBlocks(); })
    .catch(err => toast('error', err.message))
    .finally(() => startBlocksPolling());
}

function removeEdge(from, to) {
  const b = blocksData.find(x => x.id === to);
  const deps = ((b && b.depends_on) || []).filter(d => d !== from);
  api('PUT', '/api/blocks/' + encodeURIComponent(to), { depends_on: deps })
    .then(() => { toast('warn', `связь ${from} → ${to} удалена`); return loadBlocks(); })
    .catch(err => toast('error', err.message));
}

function onEdgeClick(e) {
  const t = e.target;
  const from = t && t.getAttribute && t.getAttribute('data-from');
  const to = t && t.getAttribute && t.getAttribute('data-to');
  if (from && to && confirm(`Удалить связь ${from} → ${to}?`)) removeEdge(from, to);
}

function cssAttr(s) { return String(s).replace(/"/g, '\\"'); }

function selectBlock(id) {
  selectedBlockId = id;
  document.querySelectorAll('.block-node').forEach(n => n.classList.toggle('selected', n.dataset.id === id));
  api('GET', '/api/blocks/' + encodeURIComponent(id)).then(b => {
    document.getElementById('block-modal').hidden = false;
    document.getElementById('insp-name').value = b.name || '';
    document.getElementById('insp-type').value = b.type || 'python';
    document.getElementById('insp-mode').value = b.mode || 'task';
    document.getElementById('insp-venv').checked = !!b.venv;
    document.getElementById('insp-req').value = b.requirements || '';
    document.getElementById('insp-args').value = (b.args || []).join(' ');
    document.getElementById('insp-timeout').value = b.timeout != null ? b.timeout : 60;
    document.getElementById('insp-port').value = b.port != null ? b.port : '';
    document.getElementById('insp-pass').checked = !!b.pass_stdout;
    fillDeps(id, b.depends_on || []);
    setInspStatus(b.status);
    if (blockEditor) {
      blockEditor.setOption('mode', b.type === 'sh' ? 'shell' : 'python');
      blockEditor.setValue(b.script || '');
      setTimeout(() => { try { blockEditor.refresh(); } catch (e) {} }, 30);
    } else {
      document.getElementById('block-editor').value = b.script || '';
    }
    onTypeModeChange();
    blockOutputOffset = 0;
    document.getElementById('block-output').textContent = '';
    startOutputPolling();
  }).catch(err => toast('error', err.message));
}

function fillDeps(id, selected) {
  const sel = document.getElementById('insp-deps');
  sel.innerHTML = blocksData.filter(b => b.id !== id).map(b =>
    `<option value="${escapeHtml(b.id)}" ${selected.includes(b.id) ? 'selected' : ''}>${escapeHtml(b.name)}</option>`
  ).join('') || '<option disabled>нет других блоков</option>';
}

function setInspStatus(status) {
  const el = document.getElementById('insp-status');
  if (!el) return;
  el.textContent = status;
  el.className = 'insp-status ' + statusClass(status);
}

function onTypeModeChange() {
  const type = document.getElementById('insp-type').value;
  const mode = document.getElementById('insp-mode').value;
  const venv = document.getElementById('insp-venv').checked;
  document.getElementById('insp-venv-wrap').style.display = type === 'python' ? 'flex' : 'none';
  document.getElementById('insp-req-wrap').style.display = (type === 'python' && venv) ? 'flex' : 'none';
  document.getElementById('insp-timeout-wrap').style.display = mode === 'task' ? 'flex' : 'none';
  document.getElementById('insp-port-wrap').style.display = mode === 'service' ? 'flex' : 'none';
  if (blockEditor) blockEditor.setOption('mode', type === 'sh' ? 'shell' : 'python');
}

function closeInspector() {
  selectedBlockId = null;
  stopOutputPolling();
  const modal = document.getElementById('block-modal');
  if (modal) modal.hidden = true;
  document.querySelectorAll('.block-node').forEach(n => n.classList.remove('selected'));
}

function collectForm() {
  const args = document.getElementById('insp-args').value.trim();
  const portVal = document.getElementById('insp-port').value.trim();
  const deps = Array.from(document.getElementById('insp-deps').selectedOptions)
    .map(o => o.value).filter(v => v);
  return {
    name: document.getElementById('insp-name').value.trim() || selectedBlockId,
    type: document.getElementById('insp-type').value,
    mode: document.getElementById('insp-mode').value,
    venv: document.getElementById('insp-venv').checked,
    requirements: document.getElementById('insp-req').value,
    args: args ? args.split(/\s+/) : [],
    timeout: parseInt(document.getElementById('insp-timeout').value, 10) || 0,
    port: portVal ? parseInt(portVal, 10) : null,
    depends_on: deps,
    pass_stdout: document.getElementById('insp-pass').checked,
    script: blockEditor ? blockEditor.getValue() : document.getElementById('block-editor').value,
  };
}

function saveSelected() {
  if (!selectedBlockId) return;
  api('PUT', '/api/blocks/' + encodeURIComponent(selectedBlockId), collectForm())
    .then(() => { toast('success', 'Сохранено'); return loadBlocks(); })
    .catch(err => toast('error', err.message));
}

function blockAction(action) {
  if (!selectedBlockId) return;
  api('POST', `/api/blocks/${encodeURIComponent(selectedBlockId)}/${action}`)
    .then(() => {
      toast('info', `${selectedBlockId}: ${action}`);
      blockOutputOffset = 0;
      document.getElementById('block-output').textContent = '';
      startOutputPolling();
      loadBlocks();
    })
    .catch(err => toast('error', err.message));
}

function nodeAction(id, action) {
  api('POST', `/api/blocks/${encodeURIComponent(id)}/${action}`)
    .then(() => { toast('info', `${id}: ${action}`); loadBlocks(); })
    .catch(err => toast('error', err.message));
}

function deleteSelected() {
  if (!selectedBlockId) return;
  if (!confirm(`Удалить блок ${selectedBlockId}?`)) return;
  api('DELETE', '/api/blocks/' + encodeURIComponent(selectedBlockId))
    .then(() => { toast('warn', 'Удалён'); closeInspector(); loadBlocks(); })
    .catch(err => toast('error', err.message));
}

function addBlock() {
  const name = (prompt('Имя нового блока:') || '').trim();
  if (!name) return;
  api('POST', '/api/blocks', { name, type: 'python', script: '' })
    .then(r => { toast('success', 'Блок создан'); return loadBlocks().then(() => selectBlock(r.id)); })
    .catch(err => toast('error', err.message));
}

function loadPresets(set) {
  api('POST', '/api/pipeline/presets', { set: set || 'demo' })
    .then(r => {
      const n = (r && r.created != null) ? ` (+${r.created})` : '';
      toast('success', (set === 'intro' ? 'Сервисы загружены' : 'Примеры загружены') + n);
      return loadBlocks();
    })
    .catch(err => toast('error', err.message));
}

function pollOutput() {
  if (!selectedBlockId) return;
  api('GET', `/api/blocks/${encodeURIComponent(selectedBlockId)}/output?since=${blockOutputOffset}`)
    .then(r => {
      const pre = document.getElementById('block-output');
      if (!pre) return;
      if (r.offset < blockOutputOffset) { pre.textContent = ''; }  // file truncated on rerun
      if (r.data) { pre.textContent += r.data; pre.scrollTop = pre.scrollHeight; }
      blockOutputOffset = r.offset;
      const st = document.getElementById('insp-out-status');
      if (st) st.textContent = r.status ? `· ${r.status}` : '';
    })
    .catch(() => {});
}