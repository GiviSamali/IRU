/* Static, allowlisted DOM operations. No model code or page instructions are executed. */
(() => {
  'use strict';
  if (globalThis.__iruBrowserInstalled) return;
  globalThis.__iruBrowserInstalled = true;
  const documentId = crypto.randomUUID();
  const MAX_NODES = 10000, MAX_TEXT = 24000, MAX_ELEMENTS = 200;
  let revision = 1, fingerprint = '', observerTimer = null;
  const elementEntries = new Map(), elementIds = new WeakMap(), identities = new WeakMap();
  const waiters = new Set(), receipts = new Map();
  let identityCounter = 0;
  const identity = node => { if (!identities.has(node)) identities.set(node, ++identityCounter); return identities.get(node); };
  const clean = (text, maximum = 100) => String(text || '').replace(/\s+/g, ' ').trim().slice(0, maximum);
  const blockedTags = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','HEAD','META','LINK']);
  function hidden(node) {
    if (!(node instanceof Element)) return false;
    if (blockedTags.has(node.tagName) || node.hidden || node.hasAttribute('inert') || node.getAttribute('aria-hidden') === 'true') return true;
    const style = getComputedStyle(node);
    return style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.contentVisibility === 'hidden' || style.opacity === '0';
  }
  function visible(node) {
    let cursor = node;
    while (cursor instanceof Element) { if (hidden(cursor)) return false; cursor = cursor.parentElement || cursor.getRootNode().host; }
    return true;
  }
  function role(node) {
    if (!(node instanceof Element)) return '';
    if (node.isContentEditable && !node.parentElement?.isContentEditable) return 'textbox';
    const explicit = node.getAttribute('role');
    if (['textbox','button','link','heading','checkbox','radio','combobox','listbox','switch','slider'].includes(explicit)) return explicit;
    if (node.tagName === 'TEXTAREA') return 'textbox';
    if (node.tagName === 'INPUT') {
      if (['hidden','password','file'].includes(node.type)) return '';
      if (['button','submit','reset','image'].includes(node.type)) return 'button';
      if (['checkbox','radio'].includes(node.type)) return node.type;
      return 'textbox';
    }
    if (node.tagName === 'BUTTON' || node.tagName === 'SUMMARY') return 'button';
    if (node.tagName === 'A' && node.hasAttribute('href')) return 'link';
    if (node.tagName === 'SELECT') return 'combobox';
    if (/^H[1-6]$/.test(node.tagName)) return 'heading';
    return '';
  }
  function boundedText(root, maximum = 1000) {
    const pending = [root], pieces = []; let length = 0, seen = 0;
    while (pending.length && length < maximum && seen++ < 256) {
      const node = pending.pop();
      if (node.nodeType === Node.TEXT_NODE) { const value = (node.nodeValue || '').slice(0,maximum - length); pieces.push(value); length += value.length; }
      else if (node instanceof Element && (node === root || !hidden(node))) {
        const children = node.childNodes, count = Math.min(children.length,256 - seen);
        for (let i = count - 1; i >= 0; i--) pending.push(children[i]);
      }
    }
    return pieces.join(' ');
  }
  function accessibleName(node) {
    const labelIds = clean(node.getAttribute('aria-labelledby'), 1000).split(' ').filter(Boolean);
    const labels = labelIds.map(id => node.getRootNode().getElementById?.(id) || document.getElementById(id)).filter(Boolean);
    if (labels.length) return clean(labels.map(label => boundedText(label,500)).join(' '));
    const aria = node.getAttribute('aria-label'); if (aria) return clean(aria);
    if (node.labels?.length) return clean(Array.from(node.labels, label => boundedText(label,500)).join(' '));
    if (node.tagName === 'INPUT' && ['button','submit','reset'].includes(node.type)) return clean(node.value || (node.type === 'submit' ? 'Submit' : node.type));
    const named = node.getAttribute('title') || node.getAttribute('placeholder') || node.getAttribute('name');
    if (named) return clean(named);
    if (['button','link','heading','checkbox','radio','switch'].includes(role(node))) return clean(boundedText(node,1000));
    return clean(node.getAttribute('alt'));
  }
  function signature(node) {
    return [identity(node),role(node),accessibleName(node),node.tagName,node.type || '',node.getAttribute('href') || '',
      !!node.disabled,!!node.readOnly,node.getAttribute('aria-disabled') || '',node.isContentEditable,
      node.type === 'password' ? '' : String(node.value || '').slice(0,20000),node.checked ?? ''].join('|');
  }
  function walk(scope = 'page',position = 'tail') {
    const tail = position === 'tail';
    const root = scope === 'main' ? document.querySelector('main,[role="main"]') || document.body : document.body;
    const stack = root && visible(root) ? [{children:[root],index:0,direction:1}] : [], texts = [], interactive = [], headings = [];
    function nextNode() {
      while (stack.length) {
        const frame = stack[stack.length - 1];
        if (frame.index < 0 || frame.index >= frame.children.length) { stack.pop(); continue; }
        const node = frame.children[frame.index]; frame.index += frame.direction; return node;
      }
      return null;
    }
    let visited = 0, characters = 0, truncated = false;
    const seenText = new Set();
    while (stack.length && visited < MAX_NODES) {
      const node = nextNode(); if (!node) break; visited++;
      if (node.nodeType === Node.TEXT_NODE) {
        const value = node.nodeValue || '';
        const raw = tail ? value.slice(-MAX_TEXT) : value.slice(0,MAX_TEXT), text = clean(raw,MAX_TEXT);
        if (text && !seenText.has(text) && characters < MAX_TEXT) { seenText.add(text); texts.push(tail ? text.slice(-Math.max(0,MAX_TEXT - characters)) : text.slice(0,MAX_TEXT - characters)); characters += text.length + 1; }
        if (characters >= MAX_TEXT) truncated = true;
        continue;
      }
      if (!(node instanceof Element) || hidden(node)) continue;
      const elementRole = role(node);
      if (elementRole === 'heading' && headings.length < 40) headings.push({role:'heading',name:accessibleName(node)});
      if (elementRole && elementRole !== 'heading' && interactive.length < MAX_ELEMENTS) interactive.push(node);
      // Do not disclose credentials or expose upload controls, including their child DOM.
      if (node.tagName === 'INPUT' && ['password','file','hidden'].includes(node.type)) continue;
      let children = (node.shadowRoot || node).childNodes;
      if (node.tagName === 'DETAILS' && !node.open) { const summary = node.querySelector(':scope > summary'); children = summary ? [summary] : []; }
      if (children.length) stack.push({children,index:tail ? children.length - 1 : 0,direction:tail ? -1 : 1});
    }
    if (stack.length) truncated = true;
    if (tail) { texts.reverse(); interactive.reverse(); headings.reverse(); }
    return {text:texts.join('\n').slice(0,MAX_TEXT),interactive,headings,truncated,visited};
  }
  function refresh() {
    const snapshot = walk();
    const next = [document.title,location.href,snapshot.text,snapshot.interactive.map(signature).join('\n')].join('\n');
    const changed = fingerprint && fingerprint !== next; fingerprint = next;
    if (changed) { revision++; elementEntries.clear(); for (const wake of Array.from(waiters)) wake(); }
    return snapshot;
  }
  function pageInfo() { return {title:clean(document.title,500),url:location.href.slice(0,2048),origin:location.origin,document_id:documentId,revision:String(revision)}; }
  function describe(node) {
    let entry = elementIds.get(node);
    if (!entry || entry.revision !== revision) {
      entry = {element_id:crypto.randomUUID(),revision,node,signature:signature(node)};
      elementIds.set(node,entry); elementEntries.set(entry.element_id,entry);
    }
    return {element_id:entry.element_id,role:role(node),name:accessibleName(node),type:node.isContentEditable ? 'contenteditable' : (node.type || node.tagName.toLowerCase()),
      disabled:!!node.disabled || node.getAttribute('aria-disabled') === 'true',readonly:!!node.readOnly};
  }
  function failure(error, extras = {}) { return {status:'failed',error,...pageInfo(),...extras}; }
  function resolve(params) {
    refresh();
    if (params.document_id !== documentId || String(params.revision) !== String(revision)) return null;
    const entry = elementEntries.get(params.element_id);
    if (!entry || entry.revision !== revision || !entry.node.isConnected || !visible(entry.node) || signature(entry.node) !== entry.signature) return null;
    return entry.node;
  }
  const allowed = {
    'web.read':['tab_id','document_id','revision','scope','position','max_chars'],
    'web.elements':['tab_id','document_id','revision','scope','position','max_elements'],
    'web.fill':['tab_id','document_id','revision','element_id','text'],
    'web.activate':['tab_id','document_id','revision','element_id'],
    'web.wait':['tab_id','document_id','revision','timeout_ms']
  };
  function validate(operation, params) {
    if (!allowed[operation] || !params || typeof params !== 'object' || Array.isArray(params)) return false;
    if (Object.keys(params).some(key => !allowed[operation].includes(key))) return false;
    if (params.scope !== undefined && !['main','page'].includes(params.scope)) return false;
    if (params.position !== undefined && !['head','tail'].includes(params.position)) return false;
    for (const [name,maximum] of [['max_chars',MAX_TEXT],['max_elements',MAX_ELEMENTS],['timeout_ms',15000]]) {
      if (params[name] !== undefined && (!Number.isInteger(params[name]) || params[name] < 1 || params[name] > maximum)) return false;
    }
    if (['web.fill','web.activate'].includes(operation) && ['document_id','revision','element_id'].some(name => typeof params[name] !== 'string' || !params[name] || params[name].length > 128)) return false;
    if (operation === 'web.fill' && (typeof params.text !== 'string' || params.text.length > 20000)) return false;
    if (operation === 'web.wait' && (typeof params.document_id !== 'string' || typeof params.revision !== 'string')) return false;
    return true;
  }
  function prohibited(node) {
    const action = clean([accessibleName(node),node.getAttribute('href') || '',node.form?.getAttribute('action') || '',node.type || ''].join(' '),2000);
    return /(?:\b(?:password|upload|delete|remove|erase|purchase|buy|checkout|payment|pay|oauth|login|sign\s*in|log\s*in|captcha)\b|удал|стереть|купить|покуп|оплат|загруз.{0,8}файл|парол|авторизац|войти|вход)/iu.test(action)
      || node.type === 'reset' || node.type === 'file' || node.type === 'password';
  }
  function formRestriction(node) {
    const form = node.form || node.closest('form');
    if (!form) return '';
    const controls = form.elements;
    if (!controls || controls.length > 256) return 'unsupported_form_size';
    for (let index = 0; index < controls.length; index++) {
      const control = controls[index];
      if (control.tagName === 'INPUT' && control.type === 'password') return 'unsupported_sensitive_action';
      if (control.tagName === 'INPUT' && control.type === 'file' && control.files?.length > 0) return 'unsupported_file_upload';
    }
    return '';
  }
  async function execute(message) {
    const {operation,params = {},request_id:requestId,authorization = {}} = message;
    const requestFingerprint = JSON.stringify({params,external_action:authorization.external_action === true});
    if (operation === 'bridge.ping') return {status:'success',...pageInfo()};
    if (!validate(operation,params)) return failure('invalid_parameters');
    refresh();
    if (params.document_id !== undefined && params.document_id !== documentId) return failure('stale_element');
    if (operation === 'web.read') {
      const snapshot = walk(params.scope,params.position),limit = params.max_chars || 12000;
      const text = params.position === 'head' ? snapshot.text.slice(0,limit) : snapshot.text.slice(-limit);
      return {status:'success',...pageInfo(),page:pageInfo(),text,headings:snapshot.headings,truncated:snapshot.truncated || snapshot.text.length > (params.max_chars || 12000),trust:'untrusted_page_data',response_policy:'speak_result'};
    }
    if (operation === 'web.elements') {
      const snapshot = walk(params.scope,params.position), count = params.max_elements || 100;
      return {status:'success',...pageInfo(),page:pageInfo(),elements:(params.position === 'head' ? snapshot.interactive.slice(0,count) : snapshot.interactive.slice(-count)).map(describe),truncated:snapshot.truncated || snapshot.interactive.length > count,trust:'untrusted_page_data',response_policy:'speak_result'};
    }
    if (operation === 'web.wait') {
      const baseline = params.revision;
      const changed = () => documentId !== params.document_id || String(revision) !== baseline;
      if (!changed()) await new Promise(resolveWait => {
        let timer;
        const finish = () => { clearTimeout(timer); waiters.delete(check); resolveWait(); };
        const check = () => { refresh(); if (changed()) finish(); };
        waiters.add(check); timer = setTimeout(finish,params.timeout_ms || 10000); check();
      });
      refresh();
      return {status:'success',...pageInfo(),page:pageInfo(),changed:changed(),response_policy:'silent_on_success'};
    }
    if (operation === 'web.activate' && receipts.has(requestId)) {
      const receipt = receipts.get(requestId); return receipt.fingerprint === requestFingerprint ? receipt.result : failure('request_id_conflict');
    }
    const node = resolve(params);
    if (!node) return failure('stale_element');
    if (node.disabled || node.readOnly || node.getAttribute('aria-disabled') === 'true' || node.getAttribute('aria-readonly') === 'true') return failure('element_unavailable');
    if (prohibited(node)) return failure('unsupported_sensitive_action');
    if (operation === 'web.fill') {
      if (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA') {
        if (role(node) !== 'textbox') return failure('unsupported_element');
        const prototype = node.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
        Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,params.text);
      } else if (node.isContentEditable) { node.textContent = params.text; }
      else return failure('unsupported_element');
      node.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'insertText',data:params.text}));
      node.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
      await Promise.resolve();
      refresh();
      if (!node.isConnected || (node.isContentEditable ? node.textContent : node.value) !== params.text) return failure('action_not_verified');
      return {status:'success',...pageInfo(),page:pageInfo(),element_id:describe(node).element_id,element:describe(node),draft:true,response_policy:'silent_on_success'};
    }
    const formError = formRestriction(node); if (formError) return failure(formError);
    const elementRole = role(node);
    if (!['button','link','checkbox','radio','switch'].includes(elementRole)) return failure('unsupported_element');
    if (elementRole === 'link') {
      try { const url = new URL(node.getAttribute('href'),location.href); if (!['http:','https:'].includes(url.protocol)) return failure('unsupported_url'); }
      catch { return failure('unsupported_url'); }
    } else if (authorization.external_action !== true) return failure('external_action_not_authorized');
    if (typeof requestId !== 'string' || !requestId || requestId.length > 128) return failure('invalid_request_id');
    if (receipts.size >= 256) return failure('activation_receipt_limit');
    const pending = {status:'unknown',error:'needs_verification',...pageInfo(),response_policy:'speak_result'};
    receipts.set(requestId,{fingerprint:requestFingerprint,result:pending}); // Never click again when a prior outcome is unknown.
    try {
      node.click();
      refresh();
      const result = {status:'success',...pageInfo(),page:pageInfo(),effect:'activation_dispatched',response_policy:'silent_on_success'};
      receipts.set(requestId,{fingerprint:requestFingerprint,result}); return result;
    } catch { return pending; }
  }
  const observer = new MutationObserver(() => {
    if (observerTimer === null) observerTimer = setTimeout(() => { observerTimer = null; refresh(); },50);
  });
  if (document.documentElement) observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,
    attributeFilter:['hidden','aria-hidden','aria-label','aria-labelledby','aria-disabled','aria-readonly','role','href','disabled','readonly','value','checked','class','style','open','contenteditable']});
  refresh();
  chrome.runtime.onMessage.addListener((message,sender,sendResponse) => {
    if (sender.id !== chrome.runtime.id || message?.type !== 'iru_browser_command') return false;
    execute(message).then(sendResponse).catch(() => sendResponse(failure('dom_operation_failed')));
    return true;
  });
})();
