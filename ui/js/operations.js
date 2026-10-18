/* Own SQLite jobs are the source of truth. Polling here never starts a Worker. */
window.IRUOperations = (() => {
  let owner=null, items=[], selected=null, detail=null, inFlight=null, followUp=null, busy=false, timer=null, open=false;
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
  function contextChanged() {
    if(owner!==state.user?.id || chat!==state.currentChatId) {
      chat=state.currentChatId;selected=null;detail=null;mirrorClosed=null;mirrorHTML='';manualSelection=false;mirrorTask=null;selectedStatus=null;surfaceExpanded=null;
      if(owner!==state.user?.id){items=[];stale=false;}
      render();
    }
  }
  function synchronizeSelection() {
    const scoped=scopedItems();
    if(selected && !scoped.some(i=>i.task_id===selected)){selected=null;detail=null;mirrorClosed=null;}
    const current=scoped.find(i=>i.waiting_confirmation)||scoped.find(i=>!terminal(i.status));
    if(current && (!selected || (!manualSelection && terminal(scoped.find(i=>i.task_id===selected)?.status)))) {
      selected=current.task_id;detail=null;mirrorClosed=null;selectedStatus=null;
    }
    const status=scoped.find(i=>i.task_id===selected)?.status;
    if(status==='waiting_confirmation' && selectedStatus!==status)mirrorClosed=null;
    selectedStatus=status;
  }
  function detailMarkup(i,status) {
    if(!detail)return '<p class="operation-prose">Загружаются сведения задачи…</p>';
    const cd=detail.confirm_data||{}, review=detail.plan_review;
    const answer=IRUSmartUI.REGISTRY.text({key:'mirror-result-'+i.task_id,text:detail.conversational_response||detail.answer||i.summary||'',long:false},{formatAssistantText:true});
    let html=`<div class="operation-detail"><div class="operation-prose mirror-answer">${answer}</div>`;
    if(detail.status==='confirm') {
      if(review && typeof review.revision==='string' && review.revision.length>0 && review.revision.length<=64 && Array.isArray(review.steps))html+=`<div class="operation-prose">${e(review.steps.map((step,n)=>`${n+1}. ${step.title}\n${step.instruction}`).join('\n'))}</div><button data-operation="approve">Выполнить план</button><button data-operation="revise">Изменить план</button>`;
      else if(validCommandConfirmation(detail,i.task_id))html+=`<div class="operation-prose operation-command-preview">${e(cd.command)}</div><button data-operation="confirm" data-confirmation-id="${a(cd.confirmation_id)}">Выполнить</button><button data-operation="deny" data-confirmation-id="${a(cd.confirmation_id)}">Отклонить</button>`;
      else html+='<p class="operation-confirmation-invalid" role="status">Обновите состояние: нет действующего подтверждения точной команды.</p>';
    }
    // Files remain subordinate to the result prose; no helper is inferred to be the main object.
    const files=IRUSmartUI.adapt({...detail,role:'assistant',content:'',conversationalResponse:''},0,state.currentChatId).blocks.filter(block=>block.type==='file');
    html+=`<details><summary>Файлы и ход выполнения</summary>${IRUSmartUI.render({blocks:files},{})}<div class="operation-prose">${e(detail.execution_details||'')}</div>${renderSmartTaskDetails({status,tasks:detail.tasks||[],commands:detail.commands||[],executionDetails:'',loading:false},{...detail,_taskId:i.task_id},'mirror-'+encodeURIComponent(i.task_id))}</details></div>`;
    return html;
  }
  function renderMirror() {
    const i=scopedItems().find(item=>item.task_id===selected);
    const visible=Boolean(i && (surface() || mirrorClosed!==selected));
    const mirror=document.getElementById('operationMirror'), toggle=document.getElementById('mirrorToggle');
    mirror.hidden=!visible;toggle.hidden=!i;toggle.setAttribute('aria-expanded',String(visible));
    if(!visible)return;
    const status=IRUSmartUI.normalizeStatus(i.status)||'unknown';
    const label=i.status==='waiting_input'?'Нужно выбрать окно':i.status==='waiting_confirmation'?'Нужно подтверждение':i.status==='queued'?'В очереди':IRUSmartUI.LABELS[status];
    const attention=detail?.status==='confirm' || detail?.status==='waiting_input';
    const others=scopedItems().filter(item=>item.task_id!==i.task_id);
    const compactView=IRUSmartUI.adapt({role:'assistant',_taskId:i.task_id,taskStatus:i.status,taskTitle:i.title,workerReport:detail?.worker_report},0,state.currentChatId);
    const compactStatus=IRUSmartUI.render({...compactView,blocks:compactView.blocks.filter(block=>block.type==='task').map(block=>({...block,compact:false,label}))},{});
    const extraAttention=others.filter(item=>item.waiting_confirmation || item.status==='waiting_input');
    const html=surface()
      ? `<article class="surface-operation" data-task-id="${a(i.task_id)}" data-status="${a(status)}">${compactStatus}<div class="surface-operation-actions"><button data-operation="surface-toggle" aria-expanded="${surfaceExpanded===i.task_id}">${surfaceExpanded===i.task_id?'Свернуть':'Результат и подробности'}</button>${i.can_cancel?'<button data-operation="cancel">Отменить</button>':''}</div>${stale?'<p role="status">Последнее известное состояние. Соединение потеряно.</p>':''}${attention || surfaceExpanded===i.task_id?detailMarkup(i,status):''}${extraAttention.map(item=>`<p class="surface-attention" role="status">Требует ответа: <button data-operation="details" data-surface-task="${a(item.task_id)}">${e(item.title)}</button></p>`).join('')}${others.length?`<details class="surface-others"><summary>Ещё ${others.length} · задачи и результаты</summary>${others.map(item=>`<button data-operation="details" data-surface-task="${a(item.task_id)}">${e(item.title)} · ${e(IRUSmartUI.LABELS[IRUSmartUI.normalizeStatus(item.status)]||'Исход не подтверждён')}</button>`).join('')}</details>`:''}</article>`
      : `<article data-task-id="${a(i.task_id)}" data-status="${a(status)}"><div class="mirror-object"><span class="mirror-object-icon" aria-hidden="true">▣</span><h2>${e(i.title)}</h2></div><div class="operation-devices">${e(i.device_ids.join(', ')||'Сервер')}</div><span class="smart-status">${e(label)}</span>${stale?'<p role="status">Последнее известное состояние. Соединение потеряно.</p>':''}${i.can_cancel?'<div class="operation-actions"><button data-operation="cancel">Отменить задачу</button></div>':''}${detailMarkup(i,status)}</article>`;
    if(html!==mirrorHTML) {
      const container=document.getElementById('mirrorContent'), wasOpen=mirrorTask===i.task_id && container.querySelector('.operation-detail details')?.open;
      const othersOpen=container.querySelector('.surface-others')?.open;
      container.innerHTML=html;mirrorHTML=html;mirrorTask=i.task_id;
      if(container.querySelector('.operation-detail details'))container.querySelector('.operation-detail details').open=Boolean(wasOpen);
      if(container.querySelector('.surface-others'))container.querySelector('.surface-others').open=Boolean(othersOpen);
      if(validCommandConfirmation(detail,i.task_id))for(const button of container.querySelectorAll('[data-operation="confirm"], [data-operation="deny"]'))
        presentedCommands.set(button,{nonce:detail.confirm_data.confirmation_id,command:detail.confirm_data.command});
      if(detail?.plan_review)for(const button of container.querySelectorAll('[data-operation="approve"], [data-operation="revise"]'))
        presentedCommands.set(button,{revision:detail.plan_review.revision});
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
    const group=i=>i.waiting_confirmation?0:i.status==='running'?1:i.status==='queued'?2:i.status==='partial'?3:['failed','blocked','unknown'].includes(i.status)?4:5;
    const groups=['Нужно подтверждение','Выполняются','Очередь','Частично выполнены','Требуют внимания','Завершены'];
    let previousGroup=null;
    const rows=[...scoped].sort((left,right)=>group(left)-group(right)).map(i=>{
      const status=IRUSmartUI.normalizeStatus(i.status)||'unknown';
      const label=i.status==='waiting_input'?'Нужно выбрать окно':i.status==='waiting_confirmation'?'Нужно подтверждение':i.status==='queued'?'В очереди':IRUSmartUI.LABELS[status];
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
    if(!state.user) {owner=null;items=[];selected=null;detail=null;render();return;}
    contextChanged();
    const id=state.user.id;
    if(owner!==id){owner=id;items=[];selected=null;detail=null;setOpen(sessionStorage.getItem(key())==='true');render();}
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
      if(selected && items.some(i=>i.task_id===selected)) {
        const selectedId=selected;
        const response=await apiFetch(`${API}/api/tasks/${encodeURIComponent(selectedId)}`);
        if(!response.ok)throw Error('Подробности задачи недоступны.');
        if(response.ok){const fresh=await response.json();if(state.user?.id===id&&selected===selectedId&&String(fresh.task?.chat_id)===String(state.currentChatId))detail=fresh.task;}
      }
      if(state.user?.id!==id)return;
      stale=false;document.getElementById('operationsError').textContent='';render();
    } catch(err) {if(state.user?.id===id){stale=true;document.getElementById('operationsError').textContent='Соединение потеряно. Показано последнее состояние.';render();}}
  }
  async function show(id) {
    await refresh();
    if(!scopedItems().some(item=>item.task_id===id))throw Error('Задача не принадлежит текущему чату.');
    selected=id;manualSelection=true;mirrorClosed=null;selectedStatus=null;detail=null;if(surface())surfaceExpanded=id;render();
    const who=state.user?.id;
    const r=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}`);
    if(!r.ok)throw Error('Подробности недоступны.');const data=await r.json();
    if(state.user?.id!==who||selected!==id||String(data.task?.chat_id)!==String(state.currentChatId))return;
    detail=data.task;await refresh();render();
    if(!surface())document.querySelector(`#operationsItems [data-task-id="${CSS.escape(id)}"]`)?.scrollIntoView({block:'nearest'});
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
      } else if(['approve','revise'].includes(action)) {
        const who=state.user?.id;
        const response=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}`);
        if(!response.ok)throw Error('Обновите состояние плана.');
        const fresh=(await response.json()).task;
        if(state.user?.id!==who || selected!==id || String(fresh?.chat_id)!==String(state.currentChatId))throw Error('Контекст задачи изменился.');
        detail=fresh;render();
        if(fresh.status!=='confirm' || !fresh.plan_review || presented.revision!==fresh.plan_review.revision)
          throw Error('План изменился. Проверьте обновлённый план перед решением.');
        const changes=action==='revise'?window.prompt('Что изменить в плане?'):'';if(action==='revise'&&!changes)return;
        endpoint='review-plan';body={revision:presented.revision,action,changes};
      } else if(action!=='cancel')throw Error('Неизвестное действие операции.');
      const r=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}/${endpoint}`,{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(!r.ok)throw Error('Решение устарело или не принято. Обновите операцию.');
      if(endpoint==='command-decision')window.iruVoice?.commandDecisionResolved?.(id);
      if(endpoint==='review-plan')window.iruVoice?.planReviewResolved?.(id);
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
  return Object.freeze({refresh,show,contextChanged,surfaceChanged(){mirrorHTML='';synchronizeSelection();renderMirror();}});
})();
