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

const services = [
  {
    id: 'web',
    name: 'web-service',
    status: 'up',
    ports: '80, 443',
    containers: 'nginx + app',
    compose: `version: "3.8"
services:
  web:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    restart: unless-stopped
    networks:
      - ad_net
  app:
    image: team-web:latest
    expose:
      - "3000"
    networks:
      - ad_net

networks:
  ad_net:
    external: true`
  },
  {
    id: 'api',
    name: 'api-gateway',
    status: 'up',
    ports: '8080',
    containers: 'api + redis',
    compose: `version: "3.8"
services:
  api:
    image: api:latest
    ports:
      - "8080:8080"
    environment:
      - REDIS_URL=redis:6379
    depends_on:
      - redis
    networks:
      - ad_net
  redis:
    image: redis:alpine
    networks:
      - ad_net

networks:
  ad_net:
    external: true`
  },
  {
    id: 'db',
    name: 'postgres-db',
    status: 'warn',
    ports: '5432',
    containers: 'postgres',
    compose: `version: "3.8"
services:
  db:
    image: postgres:15-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_PASSWORD: supersecret
      POSTGRES_DB: ad
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks:
      - ad_net

volumes:
  pgdata:

networks:
  ad_net:
    external: true`
  },
  {
    id: 'checker',
    name: 'checker',
    status: 'down',
    ports: '9000',
    containers: 'checker',
    compose: `version: "3.8"
services:
  checker:
    image: checker:latest
    ports:
      - "9000:9000"
    restart: always
    networks:
      - ad_net

networks:
  ad_net:
    external: true`
  },
  {
    id: 'sploit',
    name: 'sploit-runner',
    status: 'up',
    ports: '—',
    containers: 'sploit',
    compose: `version: "3.8"
services:
  sploit:
    image: sploit:latest
    environment:
      - TEAM_TOKEN=xxx
      - FARM_URL=http://farm:31337
    networks:
      - ad_net

networks:
  ad_net:
    external: true`
  }
];

const utils = [
  { name: 'Packmate', status: 'up', port: '8081', creds: 'admin : p@ckm4te', extra: 'iface eth0 · regex flag' },
  { name: 'Ферма', status: 'up', port: '31337', creds: 'token : team_secret_xx', extra: 'teams 1-16 · HTTP' },
  { name: 'Firegex', status: 'up', port: '8750', creds: 'admin : firegex', extra: 'transparent mode' }
];

const networkInfo = [
  { title: 'Board Address', value: '10.10.10.1', sub: 'jury / checksystem' },
  { title: 'Team Range', value: '10.60.1.0 — 10.60.16.0', sub: '16 команд' },
  { title: 'Protocol', value: 'TCP + HTTP', sub: 'flag submission' },
  { title: 'First / Last IP', value: '10.60.1.2 / 10.60.16.2', sub: 'vulnbox' },
  { title: 'Docker Network', value: 'ad_net', sub: 'overlay' },
  { title: 'VPN', value: 'WireGuard', sub: 'active · 16 peers' }
];

let logs = [
  { time: '11:28:01', level: 'info', msg: 'Aether panel started' },
  { time: '11:28:03', level: 'success', msg: 'Discovered 5 services in /services' },
  { time: '11:28:05', level: 'info', msg: 'Packmate is up on :8081' },
  { time: '11:28:06', level: 'info', msg: 'Farm worker + submitter started' },
  { time: '11:28:08', level: 'success', msg: 'Firegex deployed successfully' },
  { time: '11:29:12', level: 'warn', msg: 'postgres-db high memory (82%)' },
  { time: '11:30:01', level: 'info', msg: 'Healthcheck cycle started' },
  { time: '11:30:04', level: 'error', msg: 'checker is DOWN — connection refused :9000' },
  { time: '11:30:15', level: 'info', msg: 'Ping cycle completed' }
];

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

  grid.innerHTML = filtered.map(s => `
    <div class="card">
      <div class="card-header">
        <div class="card-title">${escapeHtml(s.name)}</div>
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
      </div>
    </div>
  `).join('');
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
        <button class="btn btn-sm btn-secondary" onclick="restartUtil('${escapeHtml(u.name)}')">Restart</button>
        <button class="btn btn-sm btn-ghost" onclick="toast('info','Open ${escapeHtml(u.name)}')">Open</button>
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
  const activeBtn = document.querySelector(`[data-tab="${tab}"]`);
  if (activeBtn) activeBtn.classList.add('active');

  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  const panel = document.getElementById(`tab-${tab}`);
  if (panel) panel.classList.add('active');

  const titles = {
    services: ['Сервисы', 'Управление и мониторинг'],
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