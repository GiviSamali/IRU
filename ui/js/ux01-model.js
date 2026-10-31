/* UX-01: a read-only projection of existing chat/Goal/Operations data. */
const IRUWorkspaceModel = (() => {
  const terminal=s=>['success','partial','failed','blocked','unknown','cancelled'].includes(s);
  const labels={idle:'Готов к разговору',listening:'Слушаю вас',thinking:'Разбираю поручение',working:'ИРУ работает',awaiting:'Нужно ваше решение',success:'Готово',partial:'Выполнено частично',error:'Возникла проблема',unknown:'Исход не подтверждён'};
  const hints={idle:'Поручите задачу или просто начните разговор.',listening:'Говорите — я слушаю.',thinking:'Уточняю смысл запроса и готовлю ответ.',working:'Текущая работа и её результаты — здесь.',awaiting:'Проверьте предложенное действие. Решение остаётся за вами.',success:'Результат поручения подтверждён.',partial:'Полученную часть результата можно использовать. Ограничения указаны ниже.',error:'Завершить поручение не удалось. Причина — в результате.',unknown:'Результат действия установить не удалось. Это не подтверждение успеха.'};
  const canonical=p=>p?.status==='success' && p.goal_completed!==true ? 'unknown' : p?.status||'unknown';
  function derive({messages=[],items=[],snapshots=new Map(),chatId=null,userId=null,voicePhase='off',stale=false}={}) {
    if(userId==null)return {core:'idle',active:[],results:[],decisions:[],history:[],stale:false};
    const goals=new Map(), decisions=[],results=[],history=[];
    let request='';
    messages.forEach((m,index)=>{
      if(m.role==='user'){request=String(m.content||'');history.push({index,text:request,status:'user'});return;}
      if(m.taskKind==='worker' || m.hideAfterPlanChoice)return;
      const p=m.goalPresentation||m.goal_presentation;
      const valid=p?.goal_id && p.goal_id===(m.goalId||m.goal_id);
      if(valid)goals.set(p.goal_id,{id:p.goal_id,status:canonical(p),title:request||'Текущее поручение',message:m,index,presentation:p});
      if(m.confirmTaskId || m.inputRequired?.phase==='waiting' || m.taskStatus==='waiting_input')decisions.push({id:m.confirmTaskId||m._taskId,title:request||m.taskTitle||'Нужен ваш ответ',message:m,index});
      const text=m.conversationalResponse||m.conversational_response||m.content||m.text||'';
      if(text && !m.loading && !m.confirmTaskId && m.taskStatus!=='waiting_input') {
        const negative=['partial','unknown','failed','blocked','cancelled'].includes(m.workerReport?.status||m.taskStatus)?(m.workerReport?.status||m.taskStatus):'answer';
        if(!valid || terminal(canonical(p)))results.push({index,message:m,status:valid?canonical(p):negative,title:request||'Ответ ИРУ'});
        history.push({index,text:String(text),status:valid?canonical(p):'answer'});
      }
    });
    // Group parallel Workers by Goal. Member success never substitutes for the
    // scheduler's canonical aggregate verdict. Retired attempts are excluded.
    const seen=new Set();
    for(const item of items.filter(i=>String(i.chat_id)===String(chatId) && i.required_for_goal!==false && !i.superseded_by)) {
      const id=item.goal_id||item.task_id;
      if(terminal(item.status) && !goals.has(id))continue;
      if(seen.has(id))continue;seen.add(id);
      const snap=item.goal_id?snapshots.get(item.goal_id):null;
      let status=snap?.presentation?canonical(snap.presentation):item.goal_id?(terminal(item.status)?'unknown':item.status):item.status;
      if(!goals.has(id))goals.set(id,{id,status,title:snap?.goal?.original_request||item.goal_title||item.title||'Текущее поручение',operationId:item.task_id,stale});
      if(!decisions.some(d=>d.id===item.task_id) && (item.waiting_confirmation || ['waiting_confirmation','waiting_input'].includes(item.status)))
        decisions.push({id:item.task_id,title:item.goal_title||item.title||'Нужно ваше решение',operationId:item.task_id,stale});
    }
    const active=[...goals.values()].filter(g=>!terminal(g.status));
    const loading=messages.some(m=>m.role!=='user' && m.loading && m.taskKind!=='worker');
    const latest=[...goals.values()].filter(g=>terminal(g.status)).sort((a,b)=>(b.index??-1)-(a.index??-1))[0] || [...results].reverse().find(r=>r.status!=='answer');
    let core=decisions.length?'awaiting':active.length?'working':loading?'thinking':voicePhase==='listening'?'listening':
      latest?({success:'success',partial:'partial',failed:'error',blocked:'error',unknown:'unknown'}[latest.status]||'idle'):'idle';
    const resultGoals=new Set();
    const uniqueResults=results.reverse().filter(result=>{
      const id=result.message.goalId||result.message.goal_id;
      if(!id)return true;if(resultGoals.has(id))return false;resultGoals.add(id);return true;
    });
    return {core,active,results:uniqueResults,decisions,history:history.reverse(),stale,goals:[...goals.values()]};
  }
  return Object.freeze({derive,labels,hints,terminal});
})();
if(typeof module!=='undefined' && module.exports)module.exports=IRUWorkspaceModel;
