const add=(parent,tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;parent.append(el);return el;};
export function renderCriticalRecord(host,record){
 add(host,'h4','Почему этот узел важен');add(host,'p',record.role);add(host,'p',record.importance);
 const metric=record.metrics;for(const [key,label] of [['static','Статические'],['semantic','Агрегированные']]){const m=metric[key];add(host,'small',`${label}: вход ${m.incoming} · выход ${m.outgoing} · соседи ${m.neighbors} · обратная достижимость ${m.reverse_reachable}${m.articulation?' · точка сочленения':''}${m.scc_size>1?' · циклическая группа '+m.scc_size:''}`);}
 add(host,'small','Обратная достижимость — возможные зависимые узлы модели, не доказанный масштаб отказа. У функциональных узлов статическая степень может быть нулевой: реализации считаются в отдельном слое.');
 add(host,'h4','Условный риск');add(host,'p',record.risk);add(host,'h4','Что уже есть в исходнике');add(host,'p',record.protection);add(host,'h4','Что проверять при изменении');add(host,'p',record.checks);
 const proof=add(host,'details');add(proof,'summary','Исходники снимка — доступны офлайн');for(const ref of record.sources){const a=add(proof,'a',`${ref.path}:${ref.line}${ref.symbol?' · '+ref.symbol:''}`);a.href=ref.offline_path;a.target='_blank';a.rel='noopener';add(proof,'small','snapshot '+ref.git_sha.slice(0,12));}
}
export function mountCriticalReview({model,review,host,onSelect}){
 const heading=add(host,'div');heading.className='heading';add(heading,'strong','Важные узлы');const close=add(heading,'button','Закрыть');close.onclick=()=>host.hidden=true;
 if(!review){add(host,'p','Разбор для этого снимка недоступен.');return {open(){host.hidden=false;}};}
 add(host,'p',`${review.records.length} выбранных узлов · снимок ${review.architecture_git_sha.slice(0,12)}`);add(host,'small','Риски условные; связность не доказывает runtime-критичность.');const report=add(host,'a','Полный разбор (офлайн)');report.href='CRITICAL-REVIEW.md';report.target='_blank';
 const search=add(host,'input');search.type='search';search.placeholder='Найти важный узел';search.setAttribute('aria-label','Поиск важных узлов');const rows=add(host,'div');
 function render(){rows.replaceChildren();const query=search.value.toLowerCase();for(const record of review.records.filter(r=>(r.name+' '+r.title+' '+r.role).toLowerCase().includes(query))){if(!model.nodes.has(record.id))continue;const article=add(rows,'article');const button=add(article,'button',record.title);button.onclick=()=>onSelect(record.id);add(article,'small',record.name);add(article,'p',record.importance);const detail=add(article,'details');add(detail,'summary','Описание и риски');renderCriticalRecord(detail,record);}if(!rows.children.length)add(rows,'p','Узлы не найдены.');}
 search.oninput=render;render();return {open(){host.hidden=false;}};
}
