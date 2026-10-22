/* Mini presentation only. Operations owns snapshots, selection and all actions. */
window.IRUMiniOperations = (() => {
  const e=escapeHTML, a=escapeAttr;
  const active=s=>['queued','waiting_dependency','running','waiting_confirmation','waiting_input'].includes(s);
  const attention=i=>i.waiting_confirmation || ['waiting_confirmation','waiting_input'].includes(i.status);
  function row(item) {
    const status=IRUSmartUI.normalizeStatus(item.status)||'unknown';
    const label=attention(item)?'Требуется ответ':item.status==='waiting_dependency'?'Ждёт связанного результата':IRUSmartUI.LABELS[status];
    const icon=status==='success'?'✓':status==='running'?'':status==='waiting'?'○':status==='cancelled'?'—':'!';
    return `<div class="mini-task-row" data-task-id="${a(item.task_id)}" data-status="${a(status)}"${attention(item)?' data-surface-attention="true"':''}>
      <span class="mini-task-state" role="img" aria-label="${a(label)}">${icon}</span>
      <button class="mini-task-title" data-operation="details" title="${a(item.title)} · ${a(label)}">${e(item.title||'Задача')}</button>
      ${!item.goal_id && item.can_cancel?'<button class="mini-task-cancel" data-operation="cancel">Отменить задачу</button>':''}
      ${attention(item)?'<span class="mini-task-attention">Нужен ответ</span>':''}
    </div>`;
  }
  function render({items,snapshots,errors,expandedGoals,selected,expandedTask,detailHTML,stale}) {
    const goals=new Map(), standalone=[];
    for(const item of items) {
      if(!item.goal_id){standalone.push(item);continue;}
      if(!goals.has(item.goal_id))goals.set(item.goal_id,[]);
      goals.get(item.goal_id).push(item);
    }
    let html='';
    for(const [id,known] of goals) {
      const snapshot=snapshots.get(id), goal=snapshot?.goal;
      const members=goal?.tasks?.filter(task=>task.required_for_goal!==false).map(task=>({
        ...known.find(item=>item.task_id===task.task_id),task_id:task.task_id,goal_id:id,
        title:task.objective||'Задача',
        status:task.report?.status || (task.status==='success'?'unknown':task.status),
        waiting_confirmation:task.status==='waiting_confirmation'
      })) || known;
      const unavailable=stale || errors.has(id) || !goal;
      const open=expandedGoals.has(id);
      const title=goal?.original_request || known[0].goal_title || 'Цель';
      // The server projection is authoritative for the overall Goal result.
      const status=snapshot?.presentation?.status || known[0].goal_status || 'unknown';
      const canCancel=members.some(item=>active(item.status));
      const selectedMember=members.find(item=>item.task_id===selected);
      const showDetail=selectedMember && (expandedTask===selected || attention(selectedMember));
      html+=`<section class="mini-goal" data-goal-id="${a(id)}">
        <h3 class="mini-goal-title">${e(title)}</h3>
        <div class="mini-goal-box">
          ${members.map(row).join('')}
          ${unavailable?'<p class="mini-snapshot-note" role="status">Показаны доступные сведения. Полный состав и состояние цели не подтверждены.</p>':''}
          <div class="mini-goal-actions">
            <button data-operation="goal-toggle" aria-expanded="${open}">${open?'Свернуть':'Подробнее'}</button>
            ${canCancel?`<button data-operation="goal-cancel"${unavailable?' disabled':''}>Остановить цель</button>`:''}
          </div>
          ${open?`<div class="mini-goal-summary"><span>${e(IRUSmartUI.LABELS[IRUSmartUI.normalizeStatus(status)]||'Результат не подтверждён')}</span><p>${e(snapshot?.presentation?.message||'Подробности поручения — по нажатию на его название.')}</p></div>`:''}
          ${showDetail?detailHTML(selectedMember):''}
        </div>
      </section>`;
    }
    if(standalone.length)html+=`<div class="mini-standalone">${standalone.map(item=>row(item)+((expandedTask===item.task_id || attention(item)) && selected===item.task_id?detailHTML(item):'')).join('')}</div>`;
    return html;
  }
  return Object.freeze({render,attention});
})();
