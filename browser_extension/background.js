/* Transport is bound to one paired device. Only static content.js is injected. */
'use strict';
const OPERATIONS = new Set(['web.tabs','web.read','web.elements','web.fill','web.activate','web.wait']);
const RECEIPT_KEY = 'activation_receipts';
const RECEIPT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
let socket = null, reconnectTimer = null, heartbeat = null, connecting = false;
let reconnectDelay = 1000, config = null, pairingRejected = false;
let receiptQueue = Promise.resolve();
const inflight = new Map();
const errorResult = (error,status = 'failed') => ({status,error});
function serverUrl(value) {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash || !['','/'].includes(url.pathname)) throw new Error('invalid_server_url');
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname))) throw new Error('https_required');
  return url.origin;
}
function fingerprint(message) {
  const params = Object.fromEntries(Object.entries(message.params || {}).sort(([a],[b]) => a.localeCompare(b)));
  return JSON.stringify({operation:message.operation,params,external_action:message.authorization?.external_action === true});
}
async function savedReceipt(requestId) {
  await receiptQueue;
  const state = (await chrome.storage.local.get(RECEIPT_KEY))[RECEIPT_KEY] || {};
  return state[requestId];
}
async function storeReceipt(requestId,value) {
  const write = receiptQueue.then(async () => {
    const state = (await chrome.storage.local.get(RECEIPT_KEY))[RECEIPT_KEY] || {};
    const now = Date.now();
    for (const [key,receipt] of Object.entries(state)) {
      if (['success','failed'].includes(receipt.result?.status) && receipt.updated_at && now - receipt.updated_at > RECEIPT_TTL_MS) delete state[key];
    }
    if (!state[requestId] && Object.keys(state).length >= 512) throw new Error('activation_receipt_limit');
    state[requestId] = {...value,created_at:state[requestId]?.created_at || now,updated_at:now}; await chrome.storage.local.set({[RECEIPT_KEY]:state});
  });
  receiptQueue = write.catch(() => {}); return write;
}
async function execute(message) {
  if (!OPERATIONS.has(message.operation) || typeof message.request_id !== 'string' || !message.request_id || message.request_id.length > 128
    || !message.params || typeof message.params !== 'object' || Array.isArray(message.params)
    || Object.keys(message).some(key => !['type','request_id','operation','params','authorization'].includes(key))) return errorResult('invalid_command');
  const requestId = message.request_id, activation = message.operation === 'web.activate', signature = fingerprint(message);
  if (activation) {
    const receipt = await savedReceipt(requestId);
    if (receipt) return receipt.fingerprint === signature ? (receipt.result || errorResult('needs_verification','unknown')) : errorResult('request_id_conflict');
  }
  if (inflight.has(requestId)) return inflight.get(requestId).fingerprint === signature ? inflight.get(requestId).promise : errorResult('request_id_conflict');
  const promise = (async () => {
    if (activation) await storeReceipt(requestId,{fingerprint:signature,pending:true});
    let result;
    try {
      if (message.operation === 'web.tabs') {
        if (Object.keys(message.params).length) result = errorResult('invalid_parameters');
        else {
          const all = await chrome.tabs.query({});
          const available = all.filter(tab => /^https?:\/\//i.test(tab.url || ''));
          const tabs = []; let bytes = 0;
          for (const tab of available.slice(0,100)) {
            const item = {tab_id:tab.id,title:(tab.title || '').slice(0,160),url:tab.url.slice(0,2048),origin:new URL(tab.url).origin,active:!!tab.active};
            bytes += new TextEncoder().encode(JSON.stringify(item)).length;
            if (bytes > 100000) break; tabs.push(item);
          }
          result = {status:'success',tabs,truncated:available.length > tabs.length,response_policy:'speak_result',trust:'untrusted_page_data'};
        }
      } else {
        const tabId = message.params.tab_id;
        if (!Number.isInteger(tabId) || tabId < 0) result = errorResult('invalid_tab_id');
        else {
          const tab = await chrome.tabs.get(tabId);
          if (!/^https?:\/\//i.test(tab.url || '')) result = errorResult('unsupported_page');
          else {
            // Install the static handler into tabs opened before extension installation.
            // A failed action is NEVER retried; only a side-effect-free capability probe precedes dispatch.
            try { await chrome.tabs.sendMessage(tabId,{type:'iru_browser_command',operation:'bridge.ping'}); }
            catch { await chrome.scripting.executeScript({target:{tabId},files:['content.js']}); }
            result = await chrome.tabs.sendMessage(tabId,{...message,type:'iru_browser_command'});
            if (!result || !['success','failed','unknown'].includes(result.status)) result = errorResult('invalid_bridge_result',activation ? 'unknown' : 'failed');
            result = {...result,tab_id:tabId};
          }
        }
      }
    } catch {
      result = errorResult(activation ? 'needs_verification' : 'tab_disconnected',activation ? 'unknown' : 'failed');
    }
    if (activation) {
      // If saving the receipt fails, the existing pending tombstone still prevents another activation.
      try { await storeReceipt(requestId,{fingerprint:signature,result}); } catch { return errorResult('needs_verification','unknown'); }
    }
    return result;
  })();
  inflight.set(requestId,{fingerprint:signature,promise});
  try { return await promise; } finally { inflight.delete(requestId); }
}
function scheduleReconnect() {
  if (reconnectTimer || !config?.token || pairingRejected) return;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); },reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2,30000);
}
async function connect() {
  if (pairingRejected || connecting || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  connecting = true;
  try {
    config = (await chrome.storage.local.get('bridge_config')).bridge_config;
    if (!config?.token || !config?.device_id || !config?.bridge_id) return;
    const origin = serverUrl(config.server_url), url = origin.replace(/^http/,'ws') + '/ws/browser';
    const ws = new WebSocket(url); socket = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({type:'hello',token:config.token,device_id:config.device_id,bridge_id:config.bridge_id}));
      clearInterval(heartbeat); heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'ping'})); },20000);
    };
    ws.onmessage = async event => {
      if (event.data.length > 128000) { ws.close(1009); return; }
      let message; try { message = JSON.parse(event.data); } catch { ws.close(1003); return; }
      if (message.type === 'ready') { reconnectDelay = 1000; await chrome.storage.local.set({bridge_status:{status:'connected',device_id:config.device_id,updated_at:Date.now()}}); return; }
      if (message.type === 'pong') return;
      if (message.type !== 'command') return;
      let result; try { result = await execute(message); } catch { result = errorResult('bridge_operation_failed',message.operation === 'web.activate' ? 'unknown' : 'failed'); }
      if (ws.readyState === WebSocket.OPEN) {
        let wire = JSON.stringify({type:'result',request_id:message.request_id,result});
        if (new TextEncoder().encode(wire).length > 128000) wire = JSON.stringify({type:'result',request_id:message.request_id,result:errorResult('result_too_large')});
        ws.send(wire);
      }
    };
    ws.onclose = async event => { if (socket !== ws) return; socket = null; clearInterval(heartbeat); pairingRejected = [1008,4003,4009].includes(event.code); await chrome.storage.local.set({bridge_status:{status:event.code === 4009 ? 'browser_already_connected' : pairingRejected ? 'pairing_required' : 'disconnected',updated_at:Date.now()}}); scheduleReconnect(); };
    ws.onerror = () => ws.close();
  } catch { scheduleReconnect(); }
  finally { connecting = false; }
}
chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create('bridge_reconnect',{periodInMinutes:1}); connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'bridge_reconnect') connect(); });
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.storage.onChanged.addListener((changes,area) => {
  if (area !== 'local' || !changes.bridge_config) return;
  pairingRejected = false; config = changes.bridge_config.newValue; clearTimeout(reconnectTimer); reconnectTimer = null;
  if (socket) socket.close(); socket = null; clearInterval(heartbeat); connect();
});
connect();
