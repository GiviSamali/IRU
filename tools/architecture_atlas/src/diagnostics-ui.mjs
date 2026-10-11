import {analyzeArchitecture,anomalyCategories,rankedRecords,STATIC_TYPES,SEMANTIC_TYPES} from './diagnostics.mjs';
import {visibleNodes} from './model.mjs';

export function mountDiagnostics(model,{host,getExpansion,getFull,onSelect,onDetails,onReset,onAnalysis=()=>{}}){
 let result=null,category='centers',page=0;const pageSize=30;
 const $=id=>host.querySelector('#'+id);
 const node=(parent,tag,value)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;parent.append(e);return e;};
 const heading=node(host,'div');heading.className='heading';node(heading,'strong','Аномалии');const close=node(heading,'button','Закрыть');close.onclick=()=>host.hidden=true;
 function select(id,label,options){const l=node(host,'label',label);const s=node(l,'select');s.id=id;for(const [value,text] of options){const o=node(s,'option',text);o.value=value;}return s;}
 select('diagPreset','Связность',[['static','Статическая'],['semantic','Семантическая'],['custom','Смешанная / выбранные типы']]);
 select('diagScope','Область',[['all','Весь снимок'],['visible','Подграф: раскрытые / видимые'],...Array.from({length:7},(_,i)=>['group:'+i,'Подсистема: '+model.nodes.get('group:'+i).name])]);
 select('diagLayer','Узлы',[['all','Все слои'],['physical','Только реализации'],['logical','Только функциональные']]);
 select('diagProjection','Производные зависимости',[['deduplicate','Не считать вместе с исходными'],['raw','Считать все проекции отдельно']]);
 const typeBox=node(host,'div');typeBox.id='diagTypes';for(const type of [...new Set(model.g.edges.map(e=>e.type))].sort()){const l=node(typeBox,'label');const input=node(l,'input');input.type='checkbox';input.value=type;input.checked=STATIC_TYPES.includes(type);node(l,'span',' '+type);}
 select('diagMetric','Метрика центров',[['totalDegree','Total degree'],['inDegree','In-degree'],['outDegree','Out-degree'],['uniqueNeighbors','Уникальные соседи']]);
 function number(id,label,value,min,max){const l=node(host,'label',label),input=node(l,'input');input.id=id;input.type='number';input.min=min;input.max=max;input.value=value;return input;}
 number('diagThreshold','Порог центров',20,1,100000);number('diagTopN','Top N',10,1,1000);
 select('diagSort','Сортировка списков',[['totalDegree','Total degree ↓'],['inDegree','In-degree ↓'],['outDegree','Out-degree ↓'],['uniqueNeighbors','Соседи ↓'],['name','Название']]);
 const reset=node(host,'button','Сбросить диагностику');reset.onclick=onReset;
 const caution=node(host,'p','Сигналы структуры в выбранном графе. Ноль связей не доказывает неиспользование кода; высокая степень не доказывает единую точку отказа.');caution.className='analysis-caution';
 const metadata=node(host,'p');metadata.id='diagMetadata';const tabs=node(host,'div');tabs.id='diagCategories';const rows=node(host,'div');rows.id='diagRows';const pagination=node(host,'div');pagination.className='controls';
 const prev=node(pagination,'button','←'),status=node(pagination,'small'),next=node(pagination,'button','→');prev.onclick=()=>{page=Math.max(0,page-1);render();};next.onclick=()=>{page++;render();};
 function types(){return [...typeBox.querySelectorAll('input:checked')].map(e=>e.value);}
 function settings(){return {metric:$('diagMetric').value,threshold:Math.max(1,Number($('diagThreshold').value)||20),topN:Math.min(1000,Math.max(1,Math.floor(Number($('diagTopN').value)||10)))};}
 function compute(){
  const scope=$('diagScope').value;let scopeIds=null,scopeLabel='Весь снимок';
  if(scope==='visible'){scopeIds=visibleNodes(model,getExpansion(),getFull()).map(n=>n.id);scopeLabel='Подграф: видимые по раскрытию на момент расчёта';}
  else if(scope!=='all'){scopeIds=[];const todo=[scope];for(let cursor=0;cursor<todo.length;cursor++){scopeIds.push(todo[cursor]);todo.push(...model.children.get(todo[cursor])||[]);}scopeLabel='Подграф: '+model.nodes.get(scope).name;}
  result=analyzeArchitecture(model,{types:types(),scopeIds,scopeLabel,layer:$('diagLayer').value,projectionPolicy:$('diagProjection').value});page=0;render();onAnalysis(result);
 }
 function render(){
  if(!result)return;const categories=anomalyCategories(result,settings());
  const labels={orphans:'Сироты',deadEnds:'Тупики',isolated:'Изолированные',centers:'Центры связности'};
  tabs.replaceChildren();for(const [key,label] of Object.entries(labels)){const b=node(tabs,'button',`${label} · ${key==='centers'?categories.centerCount:categories[key].length}`);b.classList.toggle('active',category===key);b.onclick=()=>{category=key;page=0;render();};}
  const meta=result.metadata;metadata.textContent=`${meta.scopeLabel}; слой ${meta.layer}. ${meta.nodeCount} узлов / ${meta.edgeCount} рёбер. Выбраны: ${meta.selectedTypes.join(', ')||'нет'}. Использованы: ${meta.usedTypes.join(', ')||'нет'}. Политика: ${meta.projectionPolicy}; производных исключено ${meta.suppressedDerivedEdges}. Runtime не учитывается.`;
  const entries=category==='centers'?categories.centers:rankedRecords(categories[category],$('diagSort').value);page=Math.min(page,Math.max(0,Math.ceil(entries.length/pageSize)-1));rows.replaceChildren();
  for(const record of entries.slice(page*pageSize,(page+1)*pageSize)){
   const row=node(rows,'article');const b=node(row,'button',record.name);b.title='Выбрать и сфокусировать';b.onclick=()=>onSelect(record,result);b.ondblclick=()=>onDetails(record.id);
   node(row,'p',`${record.subsystem} · ${record.type} · ${record.confidence}`);node(row,'small',`in ${record.inDegree} · out ${record.outDegree} · total ${record.totalDegree} · соседи ${record.uniqueNeighbors}`);node(row,'small','Типы: '+(record.edgeTypes.join(', ')||'нет'));
   const open=node(row,'button','Открыть сведения');open.onclick=()=>onDetails(record.id);
   const proof=node(row,'details');node(proof,'summary',`Доказательства зависимостей · ${record.references.length}`);let populated=false;proof.ontoggle=()=>{if(!proof.open||populated)return;populated=true;for(const r of record.references){if(r.path){const a=node(proof,'a',`${r.path}:${r.line||1}`);a.href=`https://github.com/GiviSamali/IRU/blob/${r.git_sha||model.g.git_sha}/${r.path.split('/').map(encodeURIComponent).join('/')}#L${r.line||1}`;a.target='_blank';a.rel='noopener noreferrer';}node(proof,'p',r.explanation||'Источник');}};
  }
  if(!entries.length)node(rows,'p','В этой категории и области узлы не найдены.');status.textContent=`${entries.length} в списке · страница ${page+1}/${Math.max(1,Math.ceil(entries.length/pageSize))}`;prev.disabled=page===0;next.disabled=(page+1)*pageSize>=entries.length;
 }
 $('diagPreset').onchange=()=>{const preset=$('diagPreset').value;if(preset!=='custom'){const wanted=preset==='static'?STATIC_TYPES:SEMANTIC_TYPES;for(const input of typeBox.querySelectorAll('input'))input.checked=wanted.includes(input.value);}compute();};
 for(const input of typeBox.querySelectorAll('input'))input.onchange=()=>{$('diagPreset').value='custom';compute();};
 for(const id of ['diagScope','diagLayer','diagProjection'])$(id).onchange=compute;
 for(const id of ['diagMetric','diagThreshold','diagTopN','diagSort'])$(id).onchange=()=>{page=0;render();};
 compute();return {open(){host.hidden=false;},refreshScope(){if($('diagScope').value==='visible')compute();},analysis(){return result;}};
}
