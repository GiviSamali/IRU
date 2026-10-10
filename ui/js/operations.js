/* Own SQLite jobs are the source of truth. Polling here never starts a Worker. */
window.IRUOperations = (() => {
  let owner=null, items=[], selected=null, detail=null, inFlight=null, followUp=null, busy=false, timer=null, open=false;
  const e=escapeHTML, a=escapeAttr, terminal=s=>['success','partial','failed','blocked','unknown','cancelled'].includes(s);
  const key=()=>`iru-operations-open:${state.user?.id}`;
  const renderedRows=new Map(), presentedCommands=new WeakMap();let renderedOwner=null;
  const validCommandConfirmation=(task,id)=>task?.task_id===id && task.status==='confirm' && !task.plan_review
    && typeof task.confirm_data?.confirmation_id==='string'
    && task.confirm_data.confirmation_id.trim().length>0 && task.confirm_data.confirmation_id.length<=64
    && typeof task.confirm_data.command==='string' && task.confirm_data.command.trim().length>0;
  function setOpen(value) {
    open=value;document.getElementById('operationsList').hidden=!value;
    document.getElementById('operationsToggle').setAttribute('aria-expanded',String(value));
    if(state.user)sessionStorage.setItem(key(),String(value));
  }
  function render() {
    const active=items.filter(i=>!terminal(i.status));
    const negative=items.filter(i=>['partial','failed','blocked','unknown'].includes(i.status));
    const indicator=active.length ? `${active.length} в работе / очереди` : negative.length ? `${negative.length} требуют внимания` : items.length ? 'Нет активных' : 'Нет задач';
    if(document.getElementById('operationsIndicator').textContent!==indicator)document.getElementById('operationsIndicator').textContent=indicator;
    const container=document.getElementById('operationsItems');
    const presentation=captureMessagePresentation(document.getElementById('operationsItems'));
    const disclosures=new Map(Array.from(container.querySelectorAll('.operation-item')).map(row=>[row.dataset.taskId,row.querySelector('.operation-detail details')?.open]));
    const scroll=document.getElementById('operationsList').scrollTop;
    const rows=items.map(i=>{
      const status=IRUSmartUI.normalizeStatus(i.status)||'unknown';
      const label=i.status==='waiting_confirmation'?'Нужно подтверждение':i.status==='queued'?'В очереди':IRUSmartUI.LABELS[status];
      let extra='';
      if(selected===i.task_id && detail) {
        const cd=detail.confirm_data||{}, review=detail.plan_review;
        const confirmation=detail.status==='confirm';
        extra=`<div class="operation-detail"><div class="operation-prose">${e(detail.conversational_response||detail.answer||i.summary)}</div>`;
        if(confirmation) {
          if(review) extra+=`<div class="operation-prose">${e(review.steps.map((s,n)=>`${n+1}. ${s.title}\n${s.instruction}`).join('\n'))}</div><button data-operation="approve">Выполнить план</button><button data-operation="revise">Изменить план</button>`;
          else if(validCommandConfirmation(detail,i.task_id)) extra+=`<div class="operation-prose operation-command-preview">${e(cd.command)}</div><button data-operation="confirm" data-confirmation-id="${a(cd.confirmation_id)}">Выполнить</button><button data-operation="deny" data-confirmation-id="${a(cd.confirmation_id)}">Отклонить</button>`;
          else extra+='<p class="operation-confirmation-invalid" role="status">Нет точной команды или действующего идентификатора подтверждения. Обновите состояние задачи.</p>';
        }
        extra+=`<details><summary>Ход выполнения</summary><div class="operation-prose">${e(detail.execution_details||'')}</div>${renderSmartTaskDetails({status,tasks:detail.tasks||[],commands:detail.commands||[],executionDetails:'',loading:false},{...detail,_taskId:i.task_id},'dock-'+encodeURIComponent(i.task_id))}</details></div>`;
      }
      return {id:i.task_id,html:`<article class="operation-item" data-message-key="dock-${a(i.task_id)}" data-task-id="${a(i.task_id)}" data-status="${a(status)}"><div class="operation-heading"><strong>${e(i.title)}</strong><span class="smart-status">${e(label)}</span></div><div class="operation-devices">${e(i.device_ids.join(', ')||'Сервер')}</div><div class="operation-actions"><button data-operation="details">${selected===i.task_id?'Обновить подробности':'Подробнее'}</button>${i.can_cancel?'<button data-operation="cancel">Отменить</button>':''}</div>${extra}</article>`};
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
  }
  async function refresh() {
    if(!state.user) {owner=null;items=[];selected=null;detail=null;render();return;}
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
      if(selected && items.some(i=>i.task_id===selected)) {
        const selectedId=selected;
        const response=await apiFetch(`${API}/api/tasks/${encodeURIComponent(selectedId)}`);
        if(response.ok){const fresh=await response.json();if(state.user?.id===id&&selected===selectedId)detail=fresh.task;}
      }
      if(state.user?.id!==id)return;
      document.getElementById('operationsError').textContent='';render();
    } catch(err) {if(state.user?.id===id)document.getElementById('operationsError').textContent='Соединение потеряно. Показано последнее состояние.';}
  }
  async function show(id) {
    await refresh();
    setOpen(true);selected=id;detail=null;
    const who=state.user?.id;
    const r=await apiFetch(`${API}/api/tasks/${encodeURIComponent(id)}`);
    if(!r.ok)throw Error('Подробности недоступны.');const data=await r.json();
    if(state.user?.id!==who||selected!==id)return;
    detail=data.task;await refresh();render();
    document.querySelector(`#operationsItems [data-task-id="${CSS.escape(id)}"]`)?.scrollIntoView({block:'nearest'});
  }
  async function act(action,id,presented={}) {
    if(busy)return;
    if(action==='details'){await show(id);return;}
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
        if(state.user?.id!==who)throw Error('Аккаунт изменился. Обновите состояние задачи.');
        if(selected===id){detail=fresh;render();}
        if(!validCommandConfirmation(fresh,id) || presented.nonce!==fresh.confirm_data.confirmation_id
            || presented.command!==fresh.confirm_data.command) {
          throw Error('Команда или подтверждение изменились. Обновите состояние и проверьте команду заново.');
        }
        endpoint='command-decision';body={confirmation_id:presented.nonce,accepted:action==='confirm',via_voice:false};
      } else if(['approve','revise'].includes(action)) {
        const changes=action==='revise'?window.prompt('Что изменить в плане?'):'';if(action==='revise'&&!changes)return;
        endpoint='review-plan';body={revision:detail.plan_review.revision,action,changes};
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
    document.getElementById('operationsToggle').addEventListener('click',()=>{setOpen(!open);refresh();});
    document.getElementById('operationsItems').addEventListener('click',event=>{
      const button=event.target.closest('[data-operation]'), row=button?.closest('[data-task-id]');
      if(row)act(button.dataset.operation,row.dataset.taskId,presentedCommands.get(button)||{}).catch(err=>showToast(err.message,true));
    });
    bindChatMessageActions(document.getElementById('operationsItems'));
    bindProductV2ExecutionCards(document.getElementById('operationsItems'));
    refresh();timer=setInterval(()=>{if(!document.hidden)refresh();},2500);
  });
  window.addEventListener('online',refresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  window.addEventListener('pagehide',()=>{clearInterval(timer);timer=null;});
  window.addEventListener('pageshow',()=>{if(!timer)timer=setInterval(()=>{if(!document.hidden)refresh();},2500);refresh();});
  return Object.freeze({refresh,show});
})();
