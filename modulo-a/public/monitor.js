const byId = id => document.getElementById(id);
const view = document.querySelector('[data-view]').dataset.view;
const refreshButton = byId('refresh');
const dateFormat = new Intl.DateTimeFormat('es-CL', { dateStyle: 'short', timeStyle: 'medium' });
const numberFormat = new Intl.NumberFormat('es-CL');
let timer;

function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function formatDate(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? dateFormat.format(date) : '—';
}

function badge(text, tone) {
  return element('span', text, `pill pill-${tone}`);
}

function rows(id, records, columns, emptyText, render) {
  const body = byId(id);
  if (!records.length) {
    const cell = element('td', emptyText, 'monitor-copy');
    cell.colSpan = columns;
    const row = element('tr');
    row.append(cell);
    body.replaceChildren(row);
    return;
  }
  body.replaceChildren(...records.map(record => {
    const row = element('tr');
    for (const value of render(record)) {
      const cell = element('td');
      cell.append(value instanceof Node ? value : String(value ?? '—'));
      row.append(cell);
    }
    return row;
  }));
}

async function getJSON(url) {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(14000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function renderTransactions(comparison) {
  const databases = [comparison?.bd_1, comparison?.bd_backup].filter(db => db?.online);
  const records = new Map();
  for (const db of databases) {
    for (const transaction of db.transacciones) {
      if (!records.has(transaction.id)) records.set(transaction.id, { ...transaction, hosts: [] });
      records.get(transaction.id).hosts.push(db.host);
    }
  }
  const transactions = [...records.values()].sort((a, b) => Date.parse(b.fecha) - Date.parse(a.fecha));
  byId('tx-status').textContent = databases.length === 2
    ? `Últimas 20 operaciones por nodo. ${comparison.sincronizadas ? 'Las muestras coinciden.' : 'Las muestras contienen diferencias.'}`
    : databases.length === 1
      ? `Últimas 20 operaciones de ${databases[0].host}. El otro nodo no respondió.`
      : 'Sin lectura disponible de PostgreSQL.';

  rows('transactions', transactions, 7, databases.length
    ? 'Todavía no hay transacciones registradas.'
    : 'No se pudo consultar el historial.', tx => [
    formatDate(tx.fecha), element('code', tx.id, 'node-id trace-value'),
    `${tx.cuenta_origen} → ${tx.cuenta_destino}`, `${numberFormat.format(tx.monto)} ${tx.moneda}`,
    badge(tx.estado, 'ok'), element('code', tx.nodo || 'Sin registro', 'node-id'),
    element('span', tx.hosts.join(', '), 'node-id')
  ]);
}

function renderAudits(audits) {
  byId('audit-status').textContent = audits
    ? 'Últimas 20 trazas, incluidas las peticiones rechazadas.'
    : 'Sin lectura disponible de MongoDB.';
  rows('audits', audits || [], 6, audits ? 'Todavía no hay trazas.' : 'No se pudo consultar la auditoría.', audit => {
    const state = audit.Estado || audit.estado || 'AUDITADO';
    const detail = audit.DetalleError || audit.detalle_error;
    const result = element('div');
    result.append(badge(state, ['EXITOSA', 'AUDITADO', 'RECIBIDO'].includes(state) ? 'ok' : 'err'));
    if (detail) result.append(element('small', detail, 'trace-value monitor-copy'));
    const origin = audit.CuentaOrigen || audit.cuenta_origen;
    const destination = audit.CuentaDestino || audit.cuenta_destino;
    const amount = audit.Monto ?? audit.monto;
    const currency = audit.Moneda || audit.moneda;
    const operation = element('div', null, 'trace-value');
    operation.append(element('span', `${origin || '—'} → ${destination || '—'}`));
    if (amount != null && amount !== 'N/A') {
      const formatted = Number.isFinite(Number(amount)) ? numberFormat.format(Number(amount)) : amount;
      operation.append(element('small', `${formatted} ${currency && currency !== 'N/A' ? currency : ''}`, 'trace-value monitor-copy'));
    }
    const nodes = element('div');
    for (const [name, id] of [
      ['A', audit.nodo_gateway], ['B', audit.nodo_dominio], ['C', audit.nodo_auditoria]
    ]) {
      nodes.append(element('code', `${name} · ${id || 'Sin registro'}`, 'node-id trace-value'));
    }
    return [
      formatDate(audit.TimestampLlegada || audit.timestamp_llegada),
      element('code', audit.TransaccionID || audit.transaccion_id || '—', 'node-id trace-value'),
      operation, result, nodes,
      element('code', audit.HashOperacion || audit.hash_operacion || '—', 'node-id trace-value trace-hash')
    ];
  });
}

async function refreshActivity() {
  const [transactions, audits] = await Promise.allSettled([
    getJSON('/api/transacciones-bds'), getJSON('/api/auditorias')
  ]);
  const comparison = transactions.status === 'fulfilled' ? transactions.value : null;
  const auditData = audits.status === 'fulfilled' ? audits.value || [] : null;
  renderTransactions(comparison);
  renderAudits(auditData);
  return comparison && auditData && comparison.bd_1?.online && comparison.bd_backup?.online
    ? 'Última lectura'
    : 'Último intento · lectura parcial o indisponible';
}

function renderServices(services) {
  byId('services').replaceChildren(...services.map(service => {
    const card = element('article', null, 'subcard');
    const heading = element('h3', service.nombre, 'monitor-status');
    const [label, tone] = service.estado === 'online' ? ['En línea', 'ok']
      : service.estado === 'error' ? ['Error de consulta', 'warn'] : ['Sin respuesta', 'err'];
    heading.append(badge(label, tone));
    card.append(heading, element('p', service.nodo || 'Instancia sin identificar', 'node-id monitor-copy'));
    return card;
  }));
}

function setBadge(id, text, tone) {
  byId(id).replaceWith(Object.assign(badge(text, tone), { id }));
}

function renderPostgres(postgres) {
  const status = postgres?.modo;
  const [label, tone] = status === 'normal' ? ['Replicando', 'ok']
    : status === 'degradado' ? ['Degradado', 'warn']
      : postgres ? ['Sin primario confirmado', 'err'] : ['Sin datos', 'warn'];
  setBadge('postgres-state', label, tone);
  byId('postgres-summary').textContent = postgres?.primario
    ? `Servidor activo: ${postgres.primario}`
    : postgres ? 'El servicio de dominio no tiene un primario confirmado.' : 'Sondeo de PostgreSQL indisponible.';
  byId('postgres-nodes').replaceChildren(...(postgres?.nodos || []).map(node => {
    const item = element('li', null, 'monitor-status');
    const role = node.rol === 'replica' ? 'Réplica' : 'Primario';
    item.append(element('code', node.host, 'node-id'), node.online
      ? badge(`${role} · ${node.replicando ? 'streaming' : 'sin streaming'}`, node.replicando ? 'ok' : 'warn')
      : badge('Sin respuesta', 'err'));
    return item;
  }));
}

function renderMongo(mongo) {
  const [label, tone] = mongo?.online
    ? (mongo.tipo === 'Replica set' && !mongo.escribible ? ['Sin primario confirmado', 'warn'] : ['Disponible', 'ok'])
    : mongo ? ['Sin conexión', 'err'] : ['Sin datos', 'warn'];
  setBadge('mongo-state', label, tone);
  byId('mongo-summary').textContent = mongo?.online
    ? [mongo.tipo, mongo.set, mongo.miembros?.length ? 'Miembros anunciados por el clúster' : null].filter(Boolean).join(' · ')
    : mongo ? 'El servicio de auditoría no pudo consultar MongoDB.' : 'Sondeo de MongoDB indisponible.';
  byId('mongo-nodes').replaceChildren(...(mongo?.miembros || []).map(host => {
    const item = element('li', null, 'monitor-status');
    item.append(element('code', host, 'node-id'));
    if (host === mongo.primario) item.append(badge('Primario anunciado', 'ok'));
    return item;
  }));
}

async function refreshHealth() {
  const health = await getJSON('/api/monitor/salud').catch(() => null);
  renderServices(health?.servicios || []);
  renderPostgres(health?.postgres);
  renderMongo(health?.mongo);
  byId('event-status').textContent = health
    ? 'Hasta 20 conmutaciones detectadas por este gateway.'
    : 'No se pudo consultar el monitor.';
  rows('events', health?.eventos || [], 4, health ? 'Sin conmutaciones detectadas.' : 'Historial indisponible.', event => [
    formatDate(event.fecha), event.motor,
    element('code', event.desde, 'node-id'), element('code', event.hacia, 'node-id')
  ]);
  return health ? 'Último sondeo' : 'Último intento · Gateway sin respuesta';
}

async function refresh() {
  if (refreshButton.disabled) return;
  clearTimeout(timer);
  refreshButton.disabled = true;
  try {
    const status = view === 'estado' ? await refreshActivity() : await refreshHealth();
    byId('refresh-status').textContent = `${status} · ${formatDate(new Date())}`;
  } catch {
    byId('refresh-status').textContent = 'No se pudo completar la actualización.';
  } finally {
    refreshButton.disabled = false;
    if (!document.hidden) timer = setTimeout(refresh, 5000);
  }
}

refreshButton.addEventListener('click', refresh);
document.addEventListener('visibilitychange', () => {
  clearTimeout(timer);
  if (!document.hidden) refresh();
});
refresh();
