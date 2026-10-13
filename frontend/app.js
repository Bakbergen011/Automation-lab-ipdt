const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const model = { state: null, history: [], events: [], alarms: [], range: 300, custom: null, transport: 'RTU', socket: null, toastTimer: null, drawFrame: null };

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(data?.detail || data || `HTTP ${response.status}`);
  return data;
}

function notify(message, error = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.style.background = error ? '#873f3d' : '#233940';
  toast.classList.add('show');
  clearTimeout(model.toastTimer);
  model.toastTimer = setTimeout(() => toast.classList.remove('show'), 3600);
}

function timeLabel(value) {
  if (!value) return '--:--:--';
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(value));
}

function renderState(state) {
  model.state = state;
  state.temperatures.forEach((sensor, index) => {
    $(`#temp-${index}`).innerHTML = `${sensor.value === null ? '--.-' : Number(sensor.value).toFixed(1)}<i>°C</i>`;
    $(`#sensor-status-${index}`).textContent = sensor.status;
    $(`#quality-${index}`).textContent = sensor.quality;
  });
  $('#setpoint-readout').textContent = Number(state.setpoint).toFixed(1);
  $('#hysteresis-readout').textContent = Number(state.hysteresis / 2).toFixed(1);
  $('#setpoint-input').value = Number(state.setpoint).toFixed(1);
  $('#hysteresis-input').value = Number(state.hysteresis).toFixed(1);
  $('#poll-display').textContent = (state.poll_ms / 1000).toFixed(1);
  $('#poll-input').value = (state.poll_ms / 1000).toFixed(2);
  $('#last-update').textContent = timeLabel(state.last_update);
  $('#run-state').textContent = state.emergency_stop ? 'АВАРИЙНАЯ ОСТАНОВКА' : state.running ? 'КОНТУР В РАБОТЕ' : 'КОНТУР ОСТАНОВЛЕН';
  $('#control-badge').textContent = state.emergency_stop ? 'АВАРИЯ' : state.running ? 'РАБОТА' : 'СТОП';
  $('#control-badge').classList.toggle('running', state.running && !state.emergency_stop);
  $('#start-button').disabled = !state.simulated && (!state.connected || !state.write_enabled);
  $('#start-button').title = state.simulated || state.connected && state.write_enabled ? '' : 'Нужны подключение, карта регистров и явное разрешение выходов';
  $('#heater-state').textContent = state.heater === null ? 'НЕТ ДАННЫХ' : state.heater ? 'ВКЛ' : 'ОТКЛ';
  $('#cooler-state').textContent = state.cooler === null ? 'НЕТ ДАННЫХ' : state.cooler ? 'ВКЛ' : 'ОТКЛ';
  $('#heater-indicator').classList.toggle('on', state.heater);
  $('#cooler-indicator').classList.toggle('on', state.cooler);
  $('#pump-unit').classList.toggle('active', state.running && !state.emergency_stop);
  $('#heater-unit').classList.toggle('active', state.heater);
  $('#cooler-unit').classList.toggle('active', state.cooler);
  const pill = $('#mode-pill');
  pill.classList.toggle('simulated', state.simulated);
  pill.classList.toggle('hardware', !state.simulated);
  pill.innerHTML = `<i></i> ${state.simulated ? 'SIMULATED' : 'HARDWARE'}`;
  $('#footer-mode').textContent = state.mode;
  $('#connection-text').textContent = state.simulated ? 'СИМУЛЯТОР АКТИВЕН' : state.connected ? state.device : 'НЕТ ПОДКЛЮЧЕНИЯ';
  $('#connection-dot').parentElement.classList.toggle('disconnected', !state.connected);
  $('#io-connection').textContent = state.connected ? `СВЯЗЬ · ${state.device}` : 'НЕТ ПОДКЛЮЧЕНИЯ';
  $('#diag-connection').textContent = state.connected ? 'КОНТРОЛЛЕР НА СВЯЗИ' : 'НЕТ ПОДКЛЮЧЕНИЯ';
  if (state.connection_error && !state.simulated) $('#connection-text').title = state.connection_error;
}

function visibleHistory() {
  const now = Date.now();
  const start = model.custom?.from ?? now - model.range * 1000;
  const end = model.custom?.to ?? now;
  return model.history.filter((point) => {
    const timestamp = new Date(point.time).getTime();
    return timestamp >= start && timestamp <= end;
  });
}

function drawChart() {
  const canvas = $('#trend-chart');
  const parent = canvas.parentElement;
  const width = Math.max(240, parent.clientWidth);
  const height = Math.max(130, parent.clientHeight);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const points = visibleHistory().filter((point) => [point.t1, point.t2, point.t3].every(Number.isFinite));
  $('#history-count').textContent = `${points.length} точек`;
  $('#chart-empty').hidden = points.length > 1;
  $('#chart-empty').textContent = model.state?.connected ? 'Накопление архива. Данные появятся через несколько секунд.' : 'Нет связи со стендом. Проверьте подключение и карту регистров.';
  if (points.length < 2) return;
  const pad = { left: 42, right: 13, top: 12, bottom: 48 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const values = points.flatMap((point) => [point.t1, point.t2, point.t3, point.setpoint]).filter(Number.isFinite);
  let low = Math.floor(Math.min(...values) / 5) * 5;
  let high = Math.ceil(Math.max(...values) / 5) * 5;
  if (high - low < 8) { low -= 5; high += 5; }
  ctx.font = '9px IBM Plex Mono, Consolas, monospace';
  ctx.textAlign = 'right';
  for (let tick = low; tick <= high; tick += Math.max(2, Math.ceil((high - low) / 4 / 2) * 2)) {
    const y = pad.top + (high - tick) / (high - low) * plotHeight;
    ctx.strokeStyle = '#e8edef'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillStyle = '#87949a'; ctx.fillText(`${tick}°`, pad.left - 8, y + 3);
  }
  const colors = ['#1698ad', '#4b9b79', '#cd884a', '#a4aeb2'];
  const keys = ['t1', 't2', 't3', 'setpoint'];
  keys.forEach((key, series) => {
    ctx.beginPath(); ctx.strokeStyle = colors[series]; ctx.lineWidth = series === 3 ? 1.3 : 1.8;
    ctx.setLineDash(series === 3 ? [5, 4] : []);
    points.forEach((point, index) => {
      const x = pad.left + (index / (points.length - 1)) * plotWidth;
      const y = pad.top + (high - point[key]) / (high - low) * plotHeight;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.setLineDash([]);
  ctx.textAlign = 'center'; ctx.fillStyle = '#87949a';
  [0, Math.floor((points.length - 1) / 2), points.length - 1].forEach((index) => {
    const x = pad.left + (index / (points.length - 1)) * plotWidth;
    ctx.fillText(timeLabel(points[index].time), x, height - 2);
  });
  ctx.textAlign = 'right'; ctx.fillStyle = '#73828a';
  ctx.fillText('H', pad.left - 8, height - 29);
  ctx.fillText('C', pad.left - 8, height - 16);
  const segmentWidth = plotWidth / (points.length - 1);
  points.slice(0, -1).forEach((point, index) => {
    const x = pad.left + index * segmentWidth;
    ctx.fillStyle = '#edf1f2'; ctx.fillRect(x, height - 35, segmentWidth + 0.5, 5); ctx.fillRect(x, height - 22, segmentWidth + 0.5, 5);
    if (point.heater) { ctx.fillStyle = '#ce8a49'; ctx.fillRect(x, height - 35, segmentWidth + 0.5, 5); }
    if (point.cooler) { ctx.fillStyle = '#62a0ad'; ctx.fillRect(x, height - 22, segmentWidth + 0.5, 5); }
  });
  canvas.dataset.left = pad.left; canvas.dataset.plotWidth = plotWidth; canvas.dataset.count = points.length;
}

function updateTooltip(event) {
  const canvas = $('#trend-chart');
  const points = visibleHistory();
  if (points.length < 2) return;
  const rect = canvas.getBoundingClientRect();
  const pad = Number(canvas.dataset.left || 42);
  const plotWidth = Number(canvas.dataset.plotWidth || rect.width - 55);
  const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - pad) / plotWidth));
  const index = Math.round(fraction * (points.length - 1));
  const point = points[index];
  const tooltip = $('#chart-tooltip');
  tooltip.textContent = `${timeLabel(point.time)}\nT1  ${Number(point.t1).toFixed(2)} °C\nT2  ${Number(point.t2).toFixed(2)} °C\nT3  ${Number(point.t3).toFixed(2)} °C\nSP  ${Number(point.setpoint).toFixed(2)} °C\nНагрев ${point.heater ? 'ВКЛ' : 'ОТКЛ'} · Охлаждение ${point.cooler ? 'ВКЛ' : 'ОТКЛ'}`;
  tooltip.hidden = false;
  tooltip.style.left = `${Math.min(rect.width - 210, Math.max(0, event.clientX - rect.left + 10))}px`;
  tooltip.style.top = `${Math.max(0, event.clientY - rect.top - 60)}px`;
}

async function refreshHistory() {
  try {
    if (model.custom) {
      const from = encodeURIComponent(new Date(model.custom.from).toISOString());
      const to = encodeURIComponent(new Date(model.custom.to).toISOString());
      model.history = await api(`/api/history?from=${from}&to=${to}`);
    } else {
      model.history = await api(`/api/history?seconds=${model.range}`);
    }
    drawChart();
  } catch (error) { console.warn(error); }
}

async function refreshIo() {
  try {
    const rows = await api('/api/io');
    $('#io-rows').innerHTML = rows.map((row) => `<tr><td><span class="channel-badge">${row.type}</span></td><td class="mono">${row.address}</td><td>${row.signal}</td><td class="mono">${row.raw}</td><td class="mono">${row.engineering} ${row.unit}</td><td><span class="channel-status ${row.status === 'НОРМА' ? '' : 'error'}">${row.status}</span>${row.error ? `<br><small>${row.error}</small>` : ''}</td><td class="mono">${timeLabel(row.last_poll)}</td></tr>`).join('');
    for (const kind of ['AI', 'AO', 'DI', 'DO']) $(`#count-${kind.toLowerCase()}`).textContent = rows.filter((row) => row.type === kind).length;
  } catch (error) { notify(error.message, true); }
}

function renderLogs() {
  const render = (items) => items.length ? items.slice(0, 100).map((item) => `<div class="log-row"><span class="log-time">${timeLabel(item.time)}</span><span class="log-source">${item.source}</span><span><b class="log-severity ${item.severity}">${item.severity}</b> · ${item.message}</span></div>`).join('') : '<div class="empty-state">Событий пока нет</div>';
  $('#event-list').innerHTML = render(model.events);
  $('#alarm-list').innerHTML = render(model.alarms);
  $('#alarm-count').textContent = model.alarms.length;
  $('#active-alarm-badge').textContent = `${model.alarms.length} СОБЫТИЙ`;
}

async function refreshDiagnostics() {
  try { const data = await api('/api/events'); model.events = data.events; model.alarms = data.alarms; renderLogs(); } catch (error) { console.warn(error); }
}

async function refreshAll() {
  try { renderState(await api('/api/state')); } catch (error) { notify(error.message, true); }
  await Promise.all([refreshHistory(), refreshIo(), refreshDiagnostics()]);
}

function openSensor(index) {
  const state = model.state;
  if (!state) return;
  const sensor = state.temperatures[index];
  const labels = ['Подача', 'После нагрева', 'Обратная линия'];
  $('#sensor-title').textContent = `${sensor.tag} · ${labels[index]}`;
  $('#sensor-dialog-value').textContent = sensor.value === null ? '--.-' : Number(sensor.value).toFixed(2);
  $('#sensor-dialog-quality').textContent = `Q ${sensor.quality}%`;
  $('#sensor-signal').textContent = !state.connected ? 'Нет подключения к источнику' : state.simulated ? 'Симуляция · не физический сигнал' : 'Modbus · регистр из карты производителя';
  $('#sensor-address').textContent = state.simulated ? `SIM-AI-${index + 1}` : `Регистр ${$('#hw-reg-' + (index + 1)).value || 'не настроен'}`;
  $('#sensor-dialog-status').textContent = sensor.status;
  $('#sensor-updated').textContent = timeLabel(state.last_update);
  $('#sensor-dialog').showModal();
}

async function updateSettings(payload) {
  try { renderState(await api('/api/config', { method: 'PUT', body: JSON.stringify(payload) })); return true; }
  catch (error) { notify(error.message, true); return false; }
}

async function command(commandName) {
  const payload = { command: commandName };
  if (commandName === 'start' && model.state?.mode === 'HARDWARE') {
    const confirmation = prompt('Для запуска реального оборудования введите START HARDWARE CONTROL');
    if (confirmation === null) return;
    payload.confirm_text = confirmation;
  }
  try { renderState(await api('/api/control', { method: 'POST', body: JSON.stringify(payload) })); notify(commandName === 'estop' ? 'Аварийная остановка активирована' : 'Команда принята'); }
  catch (error) { notify(error.message, true); }
}

async function loadPorts() {
  try {
    const data = await api('/api/ports');
    const select = $('#hw-com');
    const current = select.value || 'COM2';
    select.innerHTML = '<option value="">Выберите COM-порт</option>' + data.ports.map((port) => `<option value="${port.port}">${port.port} · ${port.description}</option>`).join('');
    if (data.ports.some((port) => port.port === current)) select.value = current;
    if (!data.ports.length) select.innerHTML = '<option value="">COM-порты не обнаружены</option>';
  } catch (error) { $('#hardware-message').textContent = error.message; }
}

function setTransport(transport) {
  model.transport = transport;
  $$('.mode-choice button').forEach((button) => button.classList.toggle('selected', button.dataset.transport === transport));
  $$('.tcp-field').forEach((element) => element.hidden = transport !== 'TCP');
  $$('.rtu-field').forEach((element) => element.hidden = transport !== 'RTU');
}

function hardwarePayload() {
  const registers = [1, 2, 3].map((index) => $('#hw-reg-' + index).value.trim());
  if (registers.some((value) => value === '')) throw new Error('Введите адреса всех трёх температурных регистров из документации производителя.');
  if ($('#hw-scale').value === '' || $('#hw-offset').value === '') throw new Error('Введите коэффициент и смещение из документации модуля.');
  const payload = {
    transport: model.transport,
    host: $('#hw-host').value.trim(), port: Number($('#hw-port').value),
    com_port: $('#hw-com').value, baudrate: Number($('#hw-baud').value),
    parity: $('#hw-parity').value, stopbits: Number($('#hw-stopbits').value),
    slave_id: Number($('#hw-slave').value), registers: registers.map(Number),
    register_type: $('#hw-register-type').value, data_type: $('#hw-data-type').value,
    byte_order: $('#hw-byte-order').value, scale: Number($('#hw-scale').value), offset: Number($('#hw-offset').value),
    heater_coil: $('#hw-coil-heat').value === '' ? null : Number($('#hw-coil-heat').value),
    cooler_coil: $('#hw-coil-cool').value === '' ? null : Number($('#hw-coil-cool').value),
    selected_device: $('#hw-device').value.trim(), write_armed: $('#hw-write-enable').checked,
    confirm_text: $('#hw-write-confirm').value,
  };
  if (!payload.selected_device) throw new Error('Укажите модель выбранного контроллера/модуля.');
  if (payload.transport === 'RTU' && !payload.com_port) throw new Error('Выберите обнаруженный COM-порт.');
  return payload;
}

async function saveHardware(closeDialog = false) {
  try {
    const hardware = hardwarePayload();
    await api('/api/hardware', { method: 'PUT', body: JSON.stringify(hardware) });
    $('#hardware-message').textContent = hardware.write_armed && hardware.confirm_text === 'ENABLE OUTPUT WRITES' ? 'Настройки сохранены. Разрешение записи активно.' : 'Настройки сохранены. Аппаратный режим остаётся только для чтения.';
    if (closeDialog) $('#hardware-dialog').close();
    return true;
  } catch (error) { $('#hardware-message').textContent = error.message; return false; }
}

async function connectHardware() {
  if (!await saveHardware()) return;
  $('#hardware-message').textContent = 'Проверка соединения и чтение заданных регистров…';
  try {
    renderState(await api('/api/connect', { method: 'POST', body: '{}' }));
    $('#hardware-message').textContent = 'Связь установлена. Режим HARDWARE: только чтение до отдельного запуска.';
    notify('Modbus-подключение установлено');
  } catch (error) { $('#hardware-message').textContent = `Нет подключения: ${error.message}`; notify(`Нет подключения: ${error.message}`, true); }
}

function bindEvents() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', async () => {
    $$('.tab').forEach((item) => item.classList.toggle('active', item === tab));
    $$('.view').forEach((view) => view.classList.remove('active-view'));
    $(`#${tab.dataset.view}-view`).classList.add('active-view');
    if (tab.dataset.view === 'io') await refreshIo();
    if (tab.dataset.view === 'diagnostics') await refreshDiagnostics();
    if (tab.dataset.view === 'overview') requestAnimationFrame(drawChart);
  }));
  $$('.temperature-card,.instrument').forEach((element) => {
    element.addEventListener('click', () => openSensor(Number(element.dataset.sensor)));
    element.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') openSensor(Number(element.dataset.sensor)); });
  });
  $('#apply-setpoint').addEventListener('click', () => updateSettings({ setpoint: Number($('#setpoint-input').value), hysteresis: Number($('#hysteresis-input').value) }));
  $('#setpoint-input').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('#apply-setpoint').click(); });
  $('#hysteresis-input').addEventListener('change', () => updateSettings({ hysteresis: Number($('#hysteresis-input').value) }));
  $('#poll-input').addEventListener('change', () => updateSettings({ poll_ms: Math.round(Number($('#poll-input').value) * 1000) }));
  $('#control-mode').addEventListener('change', () => updateSettings({ control_mode: $('#control-mode').value }));
  $$('#action-segment button').forEach((button) => button.addEventListener('click', async () => {
    $$('#action-segment button').forEach((item) => item.classList.toggle('selected', item === button));
    await updateSettings({ action: button.dataset.action, control_mode: 'MANUAL' });
    $('#control-mode').value = 'MANUAL';
  }));
  $('#start-button').addEventListener('click', () => command('start'));
  $('#stop-button').addEventListener('click', () => command('stop'));
  $('#estop-button').addEventListener('click', () => {
    if (model.state?.emergency_stop) command('reset_estop');
    else if (confirm('Подтвердить аварийный останов?')) command('estop');
  });
  $$('.range-segment button').forEach((button) => button.addEventListener('click', async () => {
    $$('.range-segment button').forEach((item) => item.classList.toggle('selected', item === button));
    const custom = button.dataset.range === 'custom';
    $('#custom-range').hidden = !custom;
    model.range = custom ? 'custom' : Number(button.dataset.range);
    model.custom = null;
    await refreshHistory();
  }));
  $('#apply-range').addEventListener('click', async () => {
    const from = new Date($('#range-from').value).getTime();
    const to = new Date($('#range-to').value).getTime();
    if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) return notify('Проверьте границы произвольного интервала', true);
    model.custom = { from, to };
    await refreshHistory();
  });
  $('#trend-chart').addEventListener('mousemove', updateTooltip);
  $('#trend-chart').addEventListener('mouseleave', () => $('#chart-tooltip').hidden = true);
  $('#export-csv').addEventListener('click', () => { window.location.href = '/api/history.csv'; });
  $('#refresh-io').addEventListener('click', refreshIo);
  $('#hardware-open').addEventListener('click', async () => { await loadPorts(); $('#hardware-dialog').showModal(); });
  $$('.mode-choice button').forEach((button) => button.addEventListener('click', () => setTransport(button.dataset.transport)));
  $('#save-hardware').addEventListener('click', () => saveHardware());
  $('#connect-hardware').addEventListener('click', connectHardware);
  window.addEventListener('resize', () => { cancelAnimationFrame(model.drawFrame); model.drawFrame = requestAnimationFrame(drawChart); });
}

function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws`);
  model.socket = socket;
  socket.addEventListener('message', (event) => {
    const state = JSON.parse(event.data);
    renderState(state);
    if ($('#overview-view').classList.contains('active-view')) refreshHistory();
  });
  socket.addEventListener('close', () => setTimeout(connectSocket, 2500));
  socket.addEventListener('error', () => socket.close());
}

bindEvents();
refreshAll();
connectSocket();
setInterval(() => { refreshIo(); refreshDiagnostics(); }, 10000);
window.addEventListener('load', drawChart);