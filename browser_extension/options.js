(() => {
  'use strict';
  const form = document.getElementById('pairForm'), status = document.getElementById('status');
  const server = document.getElementById('server'), device = document.getElementById('device'), account = document.getElementById('accountToken');
  const labels = {agent_offline:'Агент устройства ещё не подключён. Повторяем подключение автоматически',connecting:'Проверяем соединение',connected:'Подключено',disconnected:'Связь с сервером отсутствует',pairing_required:'Нужно повторно подключить браузер',browser_already_connected:'Другой браузер уже подключён к этому устройству: сначала отключите его'};
  async function display() {
    const stored = await chrome.storage.local.get(['bridge_config','bridge_status']);
    if (stored.bridge_config) { server.value = stored.bridge_config.server_url; device.value = stored.bridge_config.device_id; }
    let live;
    try { live = await chrome.runtime.sendMessage({type:'iru_bridge_status'}); } catch { live = {status:'disconnected'}; }
    status.textContent = stored.bridge_config ? (labels[live?.status] || 'Ожидаем соединение с сервером') + ': ' + stored.bridge_config.device_id : 'Не подключено.';
  }
  form.addEventListener('submit',async event => {
    event.preventDefault(); const button = document.getElementById('pair'); button.disabled = true;
    const accountToken = account.value.trim(); account.value = '';
    try {
      const url = new URL(server.value.trim());
      if (url.username || url.password || url.search || url.hash || !['','/'].includes(url.pathname)) throw new Error('Введите адрес сервера без пути и секретов в URL.');
      if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname))) throw new Error('Для внешнего сервера требуется HTTPS.');
      const deviceId = device.value.trim(); if (!deviceId || deviceId.length > 128 || !accountToken) throw new Error('Нужны ID устройства и токен аккаунта.');
      const response = await fetch(url.origin + '/api/browser/pair',{method:'POST',headers:{'Content-Type':'application/json','X-Token':accountToken},body:JSON.stringify({device_id:deviceId}),credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('Сервер отклонил привязку (HTTP ' + response.status + '). Проверьте токен и владельца устройства.');
      const paired = await response.json(); if (typeof paired.token !== 'string' || !paired.token) throw new Error('Сервер вернул некорректную привязку.');
      const previous = (await chrome.storage.local.get('bridge_config')).bridge_config;
      await chrome.storage.local.set({bridge_config:{server_url:url.origin,device_id:deviceId,bridge_id:previous?.bridge_id || crypto.randomUUID(),token:paired.token},bridge_status:{status:'connecting',updated_at:Date.now()}});
      status.textContent = 'Привязка создана. Ожидаем соединение с сервером.';
    } catch (error) { status.textContent = error.message || 'Не удалось подключиться.'; }
    finally { button.disabled = false; }
  });
  document.getElementById('disconnect').addEventListener('click',async () => { await chrome.storage.local.remove(['bridge_config','bridge_status']); await display(); });
  chrome.storage.onChanged.addListener((changes,area) => { if (area === 'local' && changes.bridge_status) display(); });
  display();
  setInterval(display,5000);
})();
