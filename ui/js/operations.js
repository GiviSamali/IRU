/* Own SQLite jobs are the source of truth. Polling here never starts a Worker. */
window.IRUOperations = (() => {
  let owner=null, items=[], selected=null, detail=null, historicalItem=null, inFlight=null, followUp=null, busy=false, timer=null, open=false;
  const e=escapeHTML, a=escapeAttr, terminal=s=>['success','partial','failed','blocked','unknown','cancelled'].includes(s);
  const key=()=>`iru-operations-open:${state.user?.id}`;
  const renderedRows=new Map(), presentedCommands=new WeakMap();let renderedOwner=null;
  let chat=null, mirrorClosed=null, mirrorHTML='', stale=false, manualSelection=false, mirrorTask=null, selectedStatus=null;
  let surfaceExpanded=null;
  const surface=()=>document.documentElement.classList.contains('iru-mini');
  const validCommandConfirmation=(task,id)=>task?.task_id===id && task.status==='confirm' && !task.plan_review
    && typeof task.confirm_data?.confirmation_id==='string'
    && task.confirm_data.confirmation_id.trim().length>0 && task.confirm_data.confirmation_id.length<=64
    && typeof task.confirm_data.command==='string' && task.confirm_data.command.trim().length>0;
  function setOpen(value) {
    open=value;document.getElementById('operationsList').hidden=!value;
    document.getElementById('operationsDock').hidden=!value;
    document.getElementById('operationsToggle').setAttribute('aria-expanded',String(value));
    if(state.user)sessionStorage.setItem(key(),String(value));
  }
  const scopedItems=()=>items.filter(i=>String(i.chat_id)===String(state.currentChatId));
  // The global Operations snapshot is bounded; an owner-authorized historical task
  // can be viewed even when it is absent from that snapshot or from this chat.
  const historicalPreview=task=>({
    task_id:task.task_id,chat_id:task.chat_id,
    title:String(task.assigned_objective||task.message||'Задача').slice(0,160),
    status:task.presentation_status||task.worker_report?.status||task.status||'unknown',
    device_ids:Array.isArray(task.device_ids)?task.device_ids:[],
    summary:task.conversational_response||task.answer||'',
    goal_id:task.goal_id||null,can_cancel:false,waiting_confirmation:false
  });
  const displayedTask=()=>scopedItems().find(item=>item.task_id===selected)
    || (historicalItem?.task_id===selected?historicalItem:null);
  function contextChanged() {
    if(owner!==state.user?.id || chat!==state.currentChatId) {
      chat=state.currentChatId;selected=null;detail=null;historicalItem=null;mirrorClosed=null;mirrorHTML='';manualSelection=false;mirrorTask=null;selectedStatus=null;surfaceExpanded=null;
      if(owner!==state.user?.id){items=[];stale=false;}
      render();
    }
  }
  function synchronizeSelection() {
    const scoped=scopedItems();
    if(historicalItem && (historicalItem.task_id!==selected || scoped.some(i=>i.task_id===selected)))historicalItem=null;
    if(selected && !scoped.some(i=>i.task_id===selected) && historicalItem?.task_id!==selected){selected=null;detail=null;mirrorClosed=null;}
    // A manually opened historical card must not be displaced by background polling.
    if(historicalItem?.task_id===selected){selectedStatus=historicalItem.status;return;}
    const current=scoped.find(i=>i.waiting_confirmation)||scoped.find(i=>!terminal(i.status));
    if(current && (!selected || (!manualSelection && terminal(scoped.find(i=>i.task_id===selected)?.status)))) {
      selected=current.task_id;detail=null;mirrorClosed=null;selectedStatus=null;
    }
    const status=scoped.find(i=>i.task_id===selected)?.status;
    if(status==='waiting_confirmation' && selectedStatus!==status)mirrorClosed=null;
    if(status==='success' && selectedStatus && selectedStatus!=='success')surfaceExpanded=null;
    selectedStatus=status;
  }
  function syncSurfaceCommand() {
    const row=document.querySelector('#adaptiveConversation .msg.user');
    if(!row)return;
    const item=scopedItems().find(i=>i.task_id===selected);
    const message=[...state.messages].reverse().find(m=>m.role==='user');
    const normalize=value=>String(value||'').trim().replace(/\s+/g,' ');
    const visible=surface() && item && !document.getElementById('operationMirror').hidden;
    row.classList.toggle('surface-command-duplicate',Boolean(visible && normalize(message?.content)
      && normalize(message?.content)===normalize(item.title)));
  }
  function detailMarkup(i,status) {
    if(!detail)return '<p class="operation-prose">Загружаются сведения задачи…</p>';
    const readOnly=historicalItem?.task_id===i.task_id;
    const cd=detail.confirm_data||{};
    const answer=IRUSmartUI.REGISTRY.text({key:'mirror-result-'+i.task_id,text:detail.conversational_response||detail.answer||i.summary||'',long:false},{formatAssistantText:true});
    let html=`<div class="operation-detail"><div class="operation-prose mirror-answer">${answer}</div>`;
    if(readOnly){
      const source=state.chats?.find(chat=>String(chat.id)===String(detail.chat_id));
      const label=source?.title||'Чат №'+String(detail.chat_id);
      html+='<div class="operation-prose">Источник: '+e(label)+' · Только просмотр</div>';
    }
    if(i.goal_id) {
      const related=scopedItems().filter(item=>item.goal_id===i.goal_id);
      const dependencies=(i.dependencies||[]).map(edge=>related.find(item=>item.task_id===edge.source_task_id)?.title||'Связанное поручение');
      html+=`<div class="operation-prose"><strong>Цель:</strong> ${e(i.goal_title||'')}<br>Поручений: ${e(String(i.goal_task_count||related.length))} · ${e(IRUSmartUI.LABELS[IRUSmartUI.normalizeStatus(i.goal_status)]||i.goal_status||'')}${dependencies.length?`<br>Зависит от: ${dependencies.map(e).join('; ')}`:''}</div>`;
    }
    if(detail.status==='confirm' && !readOnly) {
      if(validCommandConfirmation(detail,i.task_id))html+=`<div class="operation-prose operation-command-preview">${e(cd.command)}</div><button data-operation="confirm" data-confirmation-id="${a(cd.confirmation_id)}">Выполнить</button><button data-operation="deny" data-confirmation-id="${a(cd.confirmation_id)}">Отклонить</button>`;
      else html+='<p class="operation-confirmation-invalid" role="status">Обновите состояние: нет действующего подтверждения точной команды.</p>';
    }
    // Files remain subordinate to the result prose; no helper is inferred to be the main object.
    const files=IRUSmartUI.adapt({...detail,role:'assistant',content:'',conversationalResponse:''},0,state.currentChatId).blocks.filter(block=>block.type==='file');
    html+=`<details><summary>Файлы и ход выполнения</summary>${IRUSmartUI.render({blocks:files},{})}<div class="operation-prose">${e(detail.execution_details||'')}</div>${renderSmartTaskDetails({status,tasks:detail.tasks||[],commands:detail.commands||[],executionDetails:'',loading:false},{...detail,_taskId:i.task_id},'mirror-'+encodeURIComponent(i.task_id))}</details></div>`;
    return html;
  }
  function renderMirror() {
    const queue=document.getElementById('adaptiveQueue');
    if(queue) {
      const queued=items.filter(item=>['queued','waiting_dependency'].includes(item.status));
      queue.hidden=!queued.length;
      const text=queued.length ? `${stale?'Последнее состояние · ':''}В очереди: ${queued.length} · Следующая: ${queued[0].title}` : '';
      if(queue.textContent!==text)queue.textContent=text;
    }
    const i=displayedTask();
    const visible=Boolean(i && (surface() || mirrorClosed!==selected));
    const mirror=document.getElementById('operationMirror'), toggle=document.getElementById('mirrorToggle');
    if(mirror.hidden!==!visible)mirror.hidden=!visible;
    if(toggle.hidden!==!i)toggle.hidden=!i;
    if(toggle.getAttribute('aria-expanded')!==String(visible))toggle.setAttribute('aria-expanded',String(visible));
    syncSurfaceCommand();
    if(!visible)return;
    const status=IRUSmartUI.normalizeStatus(i.status)||'unknown';
    const label=i.status==='waiting_input'?'Нужно выбрать окно':i.status==='waiting_confirmation'?'Нужно подтверждение':i.status==='waiting_dependency'?'Ждёт связанного результата':i.status==='queued'?'В очереди':IRUSmartUI.LABELS[status];
    const attention=i.waiting_confirmation || i.status==='waiting_input' || detail?.status==='confirm' || detail?.status==='waiting_input';
    const others=scopedItems().filter(item=>item.task_id!==i.task_id);
    const compactView=IRUSmartUI.adapt({role:'assistant',_taskId:i.task_id,taskStatus:i.status,taskTitle:i.title,workerReport:detail?.worker_report},0,state.currentChatId);
    const compactStatus=IRUSmartUI.render({...compactView,blocks:compactView.blocks.filter(block=>block.type==='task').map(block=>({...block,compact:false,label}))},{});
    const extraAttention=others.filter(item=>item.waiting_confirmation || item.status==='waiting_input');
    const surfaceAttention=String(Boolean(attention || i.waiting_confirmation || i.status==='waiting_input' || extraAttention.length));
    if(mirror.dataset.surfaceAttention!==surfaceAttention)mirror.dataset.surfaceAttention=surfaceAttention;
    const expanded=attention || surfaceExpanded===i.task_id;
    const brief=!expanded && ['success','running','waiting'].includes(status);
    const resultLabel=status==='success'?'Результат':['blocked','failed','partial','unknown'].includes(status)?'Причина и результат':'Подробности';
    const html=surface()
      ? `<article class="surface-operation${brief?' surface-operation-brief':''}" data-task-id="${a(i.task_id)}" data-status="${a(status)}" data-attention="${Boolean(attention)}" data-expanded="${Boolean(expanded)}">${compactStatus}<div class="surface-operation-actions"><button data-operation="surface-toggle" aria-expanded="${surfaceExpanded===i.task_id}">${surfaceExpanded===i.task_id?'Свернуть':resultLabel}</button>${i.can_cancel?'<button data-operation="cancel">Отменить</button>':''}</div>${stale?'<p role="status">Последнее известное состояние. Соединение потеряно.</p>':''}${expanded?detailMarkup(i,status):''}${extraAttention.map(item=>`<p class="surface-attention" role="status">Требует ответа: <button data-operation="details" data-surface-task="${a(item.task_id)}">${e(item.title)}</button></p>`).join('')}</article>`
      : `<article data-task-id="${a(i.task_id)}" data-status="${a(status)}"><div class="mirror-object"><span class="mirror-object-icon" aria-hidden="true">▣</span><h2>${e(i.title)}</h2></div><div class="operation-devices">${e(i.device_ids.join(', ')||'Сервер')}</div><span class="smart-status">${e(label)}</span>${stale?'<p role="status">Последнее известное состояние. Соединение потеряно.</p>':''}${i.can_cancel?'<div class="operation-actions"><button data-operation="cancel">Отменить задачу</button></div>':''}${detailMarkup(i,status)}</article>`;
    if(html!==mirrorHTML) {
      const container=document.getElementById('mirrorContent'), wasOpen=mirrorTask===i.task_id && container.querySelector('.operation-detail details')?.open;
      container.innerHTML=html;mirrorHTML=html;mirrorTask=i.task_id;
      if(container.querySelector('.operation-detail details'))container.querySelector('.operation-detail details').open=Boolean(wasOpen);
      if(validCommandConfirmation(detail,i.task_id))for(const button of container.querySelectorAll('[data-operation="confirm"], [data-operation="deny"]'))
        presentedCommands.set(button,{nonce:detail.confirm_data.confirmation_id,command:detail.confirm_data.command});
      if(stale)for(const button of container.querySelectorAll('[data-operation]'))if(!['details','surface-toggle'].includes(button.dataset.operation))button.disabled=true;
    }
  }
  function render() {
    const scoped=scopedItems();
    const active=scoped.filter(i=>!terminal(i.status));
    const negative=scoped.filter(i=>['partial','failed','blocked','unknown'].includes(i.status));
    const indicator=active.length ? `${active.length} в работе / очереди` : negative.length ? `${negative.length} требуют внимания` : scoped.length ? 'Нет активных' : 'Нет задач';
    if(document.getElementById('operationsIndicator').textContent!==indicator)document.getElementById('operationsIndicator').textContent=indicator;
    const container=document.getElementById('operationsItems');
    const presentation=captureMessagePresentation(document.getElementById('operationsItems'));
    const disclosures=new Map(Array.from(container.querySelectorAll('.operation-item')).map(row=>[row.dataset.taskId,row.querySelector('.operation-detail details')?.open]));
    const scroll=document.getElementById('operationsList').scrollTop;
    const group=i=>i.waiting_confirmation?0:i.status==='running'?1:['queued','waiting_dependency'].includes(i.status)?2:i.status==='partial'?3:['failed','blocked','unknown'].includes(i.status)?4:5;
    const groups=['Нужно подтверждение','Выполняются','Очередь','Частично выполнены','Требуют внимания','Завершены'];
    let previousGroup=null;
    const rows=[...scoped].sort((left,right)=>group(left)-group(right)).map(i=>{
      const status=IRUSmartUI.normalizeStatus(i.status)||'unknown';
      const label=i.status==='waiting_input'?'Нужно выбрать окно':i.status==='waiting_confirmation'?'Нужно подтверждение':i.status==='waiting_dependency'?'Ждёт связанного результата':i.status==='queued'?'В очереди':IRUSmartUI.LABELS[status];
      const number=group(i), heading=number!==previousGroup?`<div class="operation-group">${e(groups[number])}</div>`:'';previousGroup=number;
      return {id:i.task_id,html:`<article class="operation-item${selected===i.task_id?' selected':''}" data-message-key="dock-${a(i.task_id)}" data-task-id="${a(i.task_id)}" data-status="${a(status)}">${heading}<div class="operation-heading"><strong>${e(i.title)}</strong><span class="smart-status">${e(label)}</span></div><div class="operation-devices">${e(i.device_ids.join(', ')||'Сервер')}</div><div class="operation-actions"><button data-operation="details" aria-pressed="${selected===i.task_id}">${selected===i.task_id?'Выбрана':'Показать'}</button>${i.can_cancel?'<button data-operation="cancel">Отменить</button>':''}</div></article>`};
    });
    // Reconcile by task ID and the rendered projection, not by volatile timestamps.
    // Reading/selection/focus in an unchanged row survives updates of other jobs.
    if(renderedOwner!==owner){container.replaceChildren();renderedRows.clear();renderedOwner=owner;}
    const current=new Map(Array.from(container.querySelectorAll(':scope > .operation-item')).map(row=>[row.dataset.taskId,row]));
    let changed=false;
    for(const [index,{id,html}] of rows.entries()) {
      let node=current.get(id);
      if(!node || renderedRows.get(id)!==html) {
        const template=document.createElement('template');template.innerHTML=html;
        const replacement=template.content.firstElementChild;
        if(node)node.replaceWith(replacement);else container.append(replacement);
        node=replacement;renderedRows.set(id,html);changed=true;
        if(selected===id && validCommandConfirmation(detail,id)) {
          // Keep the exact source string, including CRLF. HTML text normalizes line endings.
          for(const button of node.querySelectorAll('[data-operation="confirm"], [data-operation="deny"]'))
            presentedCommands.set(button,{nonce:detail.confirm_data.confirmation_id,command:detail.confirm_data.command});
        }
        const details=node.querySelector('.operation-detail details');if(details)details.open=Boolean(disclosures.get(id));
      }
      if(container.children[index]!==node){container.insertBefore(node,container.children[index]||null);changed=true;}
      current.delete(id);
    }
    for(const [id,node] of current){node.remove();renderedRows.delete(id);changed=true;}
    const empty=container.querySelector(':scope > .operation-empty');
    if(rows.length)empty?.remove();
    else if(!empty){const message=document.createElement('p');message.className='operation-empty';message.textContent='Поручения появятся здесь. Можно продолжать разговор, пока ИРУ работает.';container.append(message);}
    if(changed){restoreMessagePresentation(container,presentation);document.getElementById('operationsList').scrollTop=scroll;}
    renderMirror();
  }
  async function refresh() {
    if(!state.user) {owner=null;items=[];selected=null;detail=null;historicalItem=null;render();return;}
    contextChanged();
    const id=state.user.id;
    if(owner!==id){owner=id;items=[];selected=null;detail=null;historicalItem=null;setOpen(sessionStorage.getItem(key())==='true');render();}
    if(inFlight) {
      // A caller after a decision needs a read newer than the current request.
      // Coalesce overlapping callers without discarding their refresh promise.
      if(!followUp)followUp=inFlight.then(()=>{followUp=null;return refresh();});
      return followUp;
    }
    inFlight=readSnapshot(id).finally(()=>{inFlight=null;});
    return inFlight;
  }
  async function readSnapshot(id) {
    try {
      const r=await apiFetch(`${API}/api/operations`);if(!r.ok)throw Error('Не удалось обновить операции.');
      const data=await r.json();if(state.user?.id!==id)return;
      items=Array.isArray(data.operations)?data.operations:[];
      synchronizeSelection();
      if(selected && (items.some(i=>i.task_id===selected) || historicalItem?.task_id===selected)) {
        const selectedId=selected;
        const response=await apiFetch(`${API}/api/tasks/${encodeURIComponent(selectedId)}`);
        if(!response.ok)throw Error('Подробности задачи недоступны.');
        if(response.ok){
          const fresh=await response.json();
          if(state.user?.id===id && selected===selectedId && fresh.task?.task_id===selectedId
              && (String(fresh.task.chat_id)===String(state.currentChatId) || historicalItem?.task_id===selectedId)){
            detail=fresh.task;
            if(historicalItem?.task_id===selectedId)historicalItem=historicalPreview(fresh.task);
          }
        }
      }
      if(state.user?.id!==id)return;
      stale=false;document.getElementById('operationsError').textContent='';render();
    } catch(err) {if(state.user?.id===id){stale=true;document.getElementById('operationsError').textContent='Соединение потеряно. Показано последнее состояние.';render();}}
  }
  async function show(id) {
    const who=state.user?.id, openedChat=state.currentChatId;
    await refresh();
    if(state.user?.id!==who || state.currentChatId!==openedChat)return;
    // Only the owner-checked task endpoint decides whether a task is readable.
    // The recent Operations list is a presentation snapshot, not an access list.
    const r=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}`);
    if(!r.ok)throw Error('Задача недоступна или не найдена.');
    const data=await r.json();
    if(!data.task || data.task.task_id!==id)throw Error('Некорректные сведения задачи.');
    if(state.user?.id!==who || state.currentChatId!==openedChat)return;
    selected=id;manualSelection=true;mirrorClosed=null;selectedStatus=null;detail=data.task;
    historicalItem=scopedItems().some(item=>item.task_id===id)?null:historicalPreview(data.task);
    if(surface())surfaceExpanded=id;
    render();
    await refresh();
    if(state.user?.id===who && state.currentChatId===openedChat && selected===id && !surface())
      document.querySelector(`#operationsItems [data-task-id="${CSS.escape(id)}"]`)?.scrollIntoView({block:'nearest'});
  }
  async function act(action,id,presented={}) {
    if(busy)return;
    if(action==='surface-toggle'){surfaceExpanded=surfaceExpanded===id?null:id;renderMirror();return;}
    if(action==='details'){await show(id);return;}
    if(stale)throw Error('Обновите состояние задачи перед действием.');
    busy=true;document.getElementById("operationsItems").setAttribute("aria-busy","true");
    try {
      // Reload nonce/revision before enabling a decision; server remains the authority.
      if(action!=='cancel' && (selected!==id || !detail))await show(id);
      let endpoint=action, body={};
      if(['confirm','deny'].includes(action)) {
        // Validate the state again, but never substitute a newly issued nonce/command
        // for the specific command the user actually saw and chose.
        const who=state.user?.id;
        const response=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}`);
        if(!response.ok)throw Error('Подтверждение недоступно. Обновите состояние задачи.');
        const fresh=(await response.json()).task;
        if(state.user?.id!==who || selected!==id || String(fresh?.chat_id)!==String(state.currentChatId))throw Error('Контекст задачи изменился. Обновите состояние.');
        if(selected===id){detail=fresh;render();}
        if(!validCommandConfirmation(fresh,id) || presented.nonce!==fresh.confirm_data.confirmation_id
            || presented.command!==fresh.confirm_data.command) {
          throw Error('Команда или подтверждение изменились. Обновите состояние и проверьте команду заново.');
        }
        endpoint='command-decision';body={confirmation_id:presented.nonce,accepted:action==='confirm',via_voice:false};
      } else if(action!=='cancel')throw Error('Неизвестное действие операции.');
      const r=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}/${endpoint}`,{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(!r.ok)throw Error('Решение устарело или не принято. Обновите операцию.');
      if(endpoint==='command-decision')window.iruVoice?.commandDecisionResolved?.(id);
      const index=state.messages.findIndex(m=>m._taskId===id);
      if(index>=0){state.messages[index].confirmTaskId=null;state.messages[index].planReview=null;state.messages[index].loading=true;renderMessages();pollTask(id,index);}
      await show(id);
    }finally{busy=false;document.getElementById("operationsItems").setAttribute("aria-busy","false");}
  }
  document.addEventListener('DOMContentLoaded',()=>{
    document.getElementById('operationsClose').addEventListener('click',()=>setOpen(false));
    document.getElementById('mirrorClose').addEventListener('click',()=>{mirrorClosed=selected;renderMirror();});
    document.getElementById('mirrorToggle').addEventListener('click',()=>{mirrorClosed=mirrorClosed===selected?null:selected;renderMirror();});
    document.getElementById('operationsToggle').addEventListener('click',()=>{setOpen(!open);refresh();});
    for(const element of ['operationsItems','mirrorContent'])document.getElementById(element).addEventListener('click',event=>{
      if(event.target.closest('[data-action]'))return;
      const button=event.target.closest('[data-operation]'), row=button?.closest('[data-task-id]')||event.target.closest('.operation-item');
      if(row)act(button?.dataset.operation||'details',button?.dataset.surfaceTask||row.dataset.taskId,button?presentedCommands.get(button)||{}:{}).catch(err=>showToast(err.message,true));
    });
    bindChatMessageActions(document.getElementById('mirrorContent'));
    bindProductV2ExecutionCards(document.getElementById('mirrorContent'));
    bindChatMessageActions(document.getElementById('operationsItems'));
    bindProductV2ExecutionCards(document.getElementById('operationsItems'));
    // In compact mode Mirror lives inside chatMessages. Its existing delegated
    // handlers own these events; don't let the outer chat execute them twice.
    for(const type of ['click','keydown'])document.getElementById('mirrorContent').addEventListener(type,event=>{
      if(surface())event.stopPropagation();
    });
    refresh();timer=setInterval(()=>{if(!document.hidden)refresh();},2500);
  });
  window.addEventListener('online',refresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  window.addEventListener('pagehide',()=>{clearInterval(timer);timer=null;});
  window.addEventListener('pageshow',()=>{if(!timer)timer=setInterval(()=>{if(!document.hidden)refresh();},2500);refresh();});
  return Object.freeze({refresh,show,contextChanged,syncSurfaceCommand,surfaceChanged(){mirrorHTML='';synchronizeSelection();renderMirror();}});
})();
