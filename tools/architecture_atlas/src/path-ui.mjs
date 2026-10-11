export function mountPath(model,{host,onSwap,onNewTarget,onReset,onExit,onTypes,onStep,onDetails}){
 const node=(parent,tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;parent.append(e);return e;};
 const controls=node(host,'div');controls.className='controls';node(controls,'strong','Путь между узлами');
 for(const [label,action] of [['Поменять A ↔ B',onSwap],['Новая точка B',onNewTarget],['Сбросить маршрут',onReset],['Выйти',onExit]]){const b=node(controls,'button',label);b.onclick=action;}
 const endpoints=node(host,'p'),hint=node(host,'p');const filter=node(host,'div');filter.className='path-types';
 for(const type of [...new Set(model.g.edges.map(e=>e.type))].sort()){const label=node(filter,'label');const input=node(label,'input');input.type='checkbox';input.value=type;input.checked=type!=='contains';node(label,'span',' '+type);input.onchange=()=>onTypes(types());}
 const steps=node(host,'div');steps.className='path-steps';
 function types(){return [...filter.querySelectorAll('input:checked')].map(x=>x.value);}
 function render(state){
  endpoints.textContent=`A: ${state.a?model.nodes.get(state.a).name:'не выбрана'} → B: ${state.b?model.nodes.get(state.b).name:'не выбрана'}`;
  const result=state.result;hint.textContent=!state.pathMode?'Режим завершён; маршрут сохранён и не подсвечивается.':!state.a?'Первый клик по узлу выбирает A.':!state.b?'Второй клик по другому узлу выбирает B.':result?.status==='not_found'?`Маршрут не найден. Типы: ${types().join(', ')||'нет'}. Возможно, модель неполна; измените фильтры или поменяйте A и B.`:`Кратчайший путь по выбранным архитектурным связям · ${result?.edgeIds.length??0} переходов. Типы: ${types().join(', ')||'нет'}. Это не фактическое выполнение.`;
  if(state.highlight==='execution')hint.textContent='Сохранён статический маршрут. Сейчас отображается Execution Trace; эти слои не смешиваются.';
  steps.replaceChildren();if(result?.status!=='found')return;
  if(!result.steps.length){node(steps,'p','A = B: ноль переходов, одна точка.');return;}
  for(const [i,step] of result.steps.entries()){
   const card=node(steps,'article');const b=node(card,'button',`${i+1}. ${model.nodes.get(step.source).name} → ${model.nodes.get(step.target).name}`);b.onclick=()=>onStep(step,i);node(card,'small',`${step.type} · ${step.direction} · ${step.source} → ${step.target}`);
   const open=node(card,'button','Сведения об узле');open.onclick=()=>onDetails(step.target);
   const details=node(card,'details');node(details,'summary','Доказательства перехода');for(const r of step.evidence){if(r.path){const a=node(details,'a',`${r.path}:${r.line||1}${r.symbol?' · '+r.symbol:''}`);a.href=`https://github.com/GiviSamali/IRU/blob/${r.git_sha||model.g.git_sha}/${r.path.split('/').map(encodeURIComponent).join('/')}#L${r.line||1}`;a.target='_blank';a.rel='noopener noreferrer';}node(details,'p',r.explanation||'Источник');}
  }
 }
 return {types,render};
}
