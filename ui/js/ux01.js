/* v0 workspace, adapted to IRUSmartUI. No execution or new polling. */
window.IRUWorkspace=(()=>{
  const e=escapeHTML,a=escapeAttr,M=IRUWorkspaceModel;
  let queued=false,phase='off',owner=null,view=innerWidth>900?'overview':'conversation';
  const icons={idle:'',listening:'♫',thinking:'···',working:'↗',awaiting:'!',success:'✓',partial:'◐',error:'×',unknown:'?'};
  function core(state,size='large') {
    return `<div class="ux-core ${size}" data-state="${a(state)}" role="img" aria-label="Состояние ИРУ: ${a(M.labels[state])}"><svg viewBox="0 0 200 200" aria-hidden="true"><circle class="ux-core-outer" cx="100" cy="100" r="97"/><circle class="ux-core-ticks" cx="100" cy="100" r="88"/><circle class="ux-core-track" cx="100" cy="100" r="74"/><circle class="ux-core-ring" cx="100" cy="100" r="74"/><circle class="ux-core-center" cx="100" cy="100" r="55"/></svg><span>${icons[state]}</span></div>`;
  }
  const statusLabel=s=>({answer:'Ответ',queued:'В очереди',waiting_dependency:'Ждёт связанного результата',waiting_input:'Нужен ответ',waiting_confirmation:'Нужно подтверждение',cancelled:'Остановлено',running:'Выполняется',failed:'Не удалось',blocked:'Нужно уточнение',success:'Готово',unknown:'Не подтверждено',partial:'Частично'}[s]||'В работе');
  const chip=s=>`<span class="ux-chip" data-status="${a(s)}"><i aria-hidden="true"></i>${e(statusLabel(s))}</span>`;
  const riskLabel=r=>({dangerous:'Повышенный риск',high:'Повышенный риск',moderate:'Требует внимания',safe:'Обычное действие'}[r]||'Требует вашего разрешения');
  function update(id,html){const node=document.getElementById(id);if(node && node.innerHTML!==html)node.innerHTML=html;}
  function restoredView(id){
    const key='iru-view:'+id;
    const saved=sessionStorage.getItem(key)||sessionStorage.getItem('iru-view')||localStorage.getItem('iru-view');
    const next=['overview','conversation'].includes(saved)?saved:(innerWidth>900?'overview':'conversation');
    if(saved==='components')sessionStorage.setItem(key,next);
    if(sessionStorage.getItem('iru-view')==='components')sessionStorage.removeItem('iru-view');
    if(localStorage.getItem('iru-view')==='components')localStorage.removeItem('iru-view');
    return next;
  }
  function setView(next,save=true){
    if(!['overview','conversation'].includes(next))return;
    view=next;
    if(save && state.user)sessionStorage.setItem('iru-view:'+state.user.id,next);
    applyView();refresh();
  }
  function applyView(){
    const mini=document.documentElement.classList.contains('iru-mini');
    const shown=mini?'conversation':view;
    document.documentElement.dataset.uxView=shown;
    for(const [name,id] of [['overview','uxOverview'],['conversation','chatMessages']]){
      const node=document.getElementById(id);node.hidden=shown!==name;
      const tab=document.getElementById('ux-tab-'+name);tab.setAttribute('aria-selected',String(shown===name));tab.tabIndex=shown===name?0:-1;
    }
    document.getElementById('uxMiniStatus').hidden=!mini;
  }
  function smart(message,index){
    const adapted=IRUSmartUI.adapt(message,index,state.currentChatId);
    const hasAction=adapted.blocks.some(b=>b.type==='action');
    adapted.blocks=adapted.blocks.filter(b=>!['task','action'].includes(b.type)).map(b=>({...b,key:'overview-'+b.key}));
    const context={expanded:state.expandedSmartBlocks,formatAssistantText:true,actionDetails:renderMessageActions};
    return adapted.blocks.map(block=>{
      const html=IRUSmartUI.render({blocks:[block]},context);
      return block.type==='goal' && block.message?.length>700
        ? `<p class="ux-result-preview">${e(block.message.slice(0,240))}…</p><details class="ux-result-full"><summary>Читать полный результат</summary>${html}</details>`:html;
    }).join('')+(hasAction?'<button data-ux-open-conversation>Посмотреть предложение в разговоре ↗</button>':'');
  }
  function paint(){
    queued=false;if(!document.getElementById('uxOverview'))return;
    if(owner!==state.user?.id){owner=state.user?.id;view=restoredView(owner);}
    applyView();
    const source=window.IRUOperations?.presentationSnapshot()||{};
    const model=M.derive({...source,messages:state.messages,chatId:state.currentChatId,userId:state.user?.id,voicePhase:phase});
    const c=model.core;
    update('uxMiniStatus',`${core(c,'small')}<div><strong>${e(M.labels[c])}</strong><span>${e(model.decisions[0]?.title||model.active[0]?.title||'Чем могу помочь?')}</span></div>`);
    if(document.documentElement.dataset.uxView!=='overview') {
      update('uxResults','');update('uxDecisions','');
      return;
    }
    document.getElementById('uxActiveSection').hidden=!model.active.length;
    document.getElementById('uxResultsSection').hidden=!model.results.length;
    document.getElementById('uxDecisionsSection').hidden=!model.decisions.length;
    update('uxHero',`${core(c)}<div class="ux-hero-copy"><p class="ux-eyebrow">В ЭТОМ РАЗГОВОРЕ</p><h1>${e(M.labels[c])}</h1><p>${e(model.stale && model.active.length?'Обновить состояние пока не удалось. Показаны последние известные данные.':M.hints[c])}</p></div>`);
    document.getElementById('uxActiveCount').textContent=model.active.length||'';
    document.getElementById('uxDecisionCount').textContent=model.decisions.length||'';
    update('uxActive',model.active.length?model.active.map(task=>`<article class="ux-task-card" data-goal-id="${a(task.id)}">${chip(task.status)}<h3>${e(task.title)}</h3><div class="ux-activity" role="progressbar" aria-label="Выполняется; точный прогресс неизвестен"></div>${task.operationId?`<button data-ux-operation="${a(task.operationId)}">Посмотреть работу ↗</button>`:'<button data-ux-open-conversation>Открыть разговор ↗</button>'}</article>`).join(''):'<div class="ux-empty">Сейчас нет активных поручений.<br>Можно начать с одной фразы.</div>');
    const uniqueResults=[],seenGoals=new Set();
    for(const result of model.results){const id=result.message.goalId||result.message.goal_id;if(id && seenGoals.has(id))continue;if(id)seenGoals.add(id);uniqueResults.push(result);}
    update('uxResults',uniqueResults.slice(0,6).map(result=>`<article class="ux-result-card" data-result-status="${a(result.status)}"><div class="ux-result-meta">${chip(result.status)}<span>${result.message.created_at?e(new Date(Number(result.message.created_at)*1000).toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'})):''}</span></div>${smart(result.message,result.index)}</article>`).join('')||'<div class="ux-empty">Здесь появятся ответы, файлы и выполненные действия.</div>');
    update('uxDecisions',model.decisions.map(decision=>`<article class="ux-approval-card" data-confirmation-id="${a(decision.message?.commandConfirmation?.confirmation_id||'')}"><h3>${e(decision.title)}</h3>${decision.message?.commandConfirmation?.risk?`<p>${e(riskLabel(decision.message.commandConfirmation.risk))}. Проверьте действие перед разрешением.</p>`:''}${decision.message?.confirmTaskId?`${decision.message.commandConfirmation?.command?`<details class="ux-approval-details"><summary>Точное действие</summary><pre>${e(decision.message.commandConfirmation.command)}</pre></details>`:''}${renderMessageActions({confirmTaskId:decision.message.confirmTaskId},decision.index)}`:decision.operationId?`<button data-ux-operation="${a(decision.operationId)}"${decision.stale?' disabled':''}>Открыть запрос решения ↗</button>`:'<p>Ответьте в поле поручения ниже.</p><button data-ux-input>Ответить</button>'}</article>`).join('')||'<div class="ux-quiet"><span aria-hidden="true">✓</span> Ваше участие не требуется</div>');
    const devices=Object.entries(state.devices||{});
    update('uxDevices',devices.length?`<ul class="ux-device-list">${devices.map(([id,device])=>{
      const connected=device.connected===true||device.online===true;
      const busy=(source.items||[]).some(item=>['running','waiting_confirmation','waiting_input'].includes(item.status) && item.device_ids?.includes(id));
      return `<li><span class="ux-device-symbol" aria-hidden="true">▣</span><div><strong>${e(device.info?.hostname||id)}</strong><span>${e(device.info?.os||'Устройство')}</span></div><span class="ux-connection ${connected?'online':''}">${connected?busy?'В этом разговоре':'На связи':'Нет связи'}</span></li>`;
    }).join('')}</ul>`:'<div class="ux-empty">Нет подключённых устройств. Разговор доступен и без них.</div>');
    if(model.stale && model.active.length)update('uxActiveCount','Нет свежих данных');
  }
  function refresh(){if(!queued){queued=true;queueMicrotask(paint);}}
  document.addEventListener('DOMContentLoaded',()=>{
    document.getElementById('uxOverview').addEventListener('click',event=>{
      const button=event.target.closest('[data-action=confirm-task],[data-action=deny-task]');
      if(!button)return;
      const shown=button.closest('.ux-approval-card')?.dataset.confirmationId;
      const current=state.messages[Number(button.dataset.index)];
      if(current?.confirmTaskId!==button.dataset.taskId || shown && shown!==current?.commandConfirmation?.confirmation_id){
        event.preventDefault();event.stopImmediatePropagation();
        showToast('Подтверждение изменилось. Проверьте актуальное действие.',true);refresh();
      }
    },true);
    bindChatMessageActions(document.getElementById('uxOverview'));
    document.querySelector('.ux-navigation').addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key) || event.target.getAttribute('role')!=='tab')return;
      const tabs=[...document.querySelectorAll('[data-ux-view]')].filter(tab=>getComputedStyle(tab).display!=='none');
      const i=tabs.indexOf(event.target),j=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;
      event.preventDefault();setView(tabs[j].dataset.uxView);tabs[j].focus();
    });
    document.addEventListener('click',event=>{
      const target=event.target.closest('[data-ux-view],[data-ux-open-conversation],[data-ux-input],[data-ux-operation],[data-ux-message]');
      if(!target)return;
      if(target.dataset.uxView)setView(target.dataset.uxView);
      else if(target.hasAttribute('data-ux-open-conversation')||target.hasAttribute('data-ux-message')){
        setView('conversation');
        if(target.dataset.uxMessage){
          const index=Number(target.dataset.uxMessage),message=state.messages[index];
          if(message){const key=IRUSmartUI.adapt(message,index,state.currentChatId).key;
            const node=document.querySelector(`#chatMessages [data-message-key="${CSS.escape(key)}"]`);
            if(node){node.tabIndex=-1;node.focus({preventScroll:true});node.scrollIntoView({block:'center'});}}
        }
      }
      else if(target.hasAttribute('data-ux-input'))document.getElementById('chatInput').focus();
      else if(target.dataset.uxOperation){setView('conversation');IRUOperations.show(target.dataset.uxOperation).catch(error=>showToast(error.message,true));}

    });
    new MutationObserver(refresh).observe(document.documentElement,{attributes:true,attributeFilter:['class']});
    const device=document.getElementById('deviceLabel');if(device)new MutationObserver(refresh).observe(device,{childList:true,subtree:true,characterData:true});
    window.addEventListener('resize',()=>{applyView();refresh();});
    refresh();
  });
  return Object.freeze({refresh,setView,core,voiceChanged(value){phase=value;refresh();}});
})();
