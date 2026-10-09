/* Presentation-only workspaces: never execute or overwrite assistant messages. */
window.IRUContentSurface = (() => {
  const layouts=new Map();let editor=null, editing=null;
  const LIMIT=100000;
  const scope=()=>`${state.user?.id}:${state.currentChatId}`;
  const storageKey=id=>`iru-surface:${scope()}:${id}`;
  const clamp=(n,min,max)=>Math.max(min,Math.min(max,Number.isFinite(n)?n:min));
  function load(id) {
    const key=storageKey(id);
    if(!layouts.has(key)) {
      let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}');}catch{}
      if(!saved||typeof saved!=='object')saved={};
      const value={zoom:clamp(saved.zoom??1,.75,2),x:clamp(saved.x,0,32),y:clamp(saved.y,0,32),expanded:saved.expanded===true};
      for(const k of ['original','draft','saved'])if(typeof saved[k]==='string'&&saved[k].length<=LIMIT)value[k]=saved[k];
      layouts.set(key,value);
    }
    return layouts.get(key);
  }
  function persist(id,value,key=storageKey(id)) {
    try{localStorage.setItem(key,JSON.stringify(value));}
    catch{if(editor)editor.querySelector('.surface-editor-state').textContent='Черновик в памяти страницы. Хранилище браузера недоступно.';}
  }
  const button=(action,label)=>{const b=document.createElement('button');b.type='button';b.dataset.surfaceAction=action;b.textContent=label;const titles={'zoom-in':'Увеличить','zoom-out':'Уменьшить','zoom-reset':'Сбросить масштаб','expand':'Раскрыть рабочую область'};if(titles[action]){b.title=titles[action];b.setAttribute('aria-label',titles[action]);}return b;};
  function apply(workspace,value) {
    const object=workspace.querySelector('.surface-object');
    workspace.classList.toggle('surface-expanded',value.expanded);
    workspace.style.setProperty('--surface-zoom',value.zoom);
    const x=Math.min(value.x,Math.max(0,workspace.clientWidth-object.offsetWidth));
    object.style.transform=`translate(${x}px,${value.y}px)`;
    workspace.querySelector('[data-surface-action="zoom-reset"]').textContent=Math.round(value.zoom*100)+'%';
    workspace.querySelector('[data-surface-action="expand"]').setAttribute('aria-expanded',String(value.expanded));
    workspace.querySelector('[data-surface-action="expand"]').textContent=value.expanded?'Свернуть':'Раскрыть';
  }
  function saveVersion(workspace,value) {
    workspace.querySelector('.surface-saved-version')?.remove();
    if(typeof value.saved!=='string')return;
    const section=document.createElement('section');section.className='surface-saved-version';
    const title=document.createElement('strong');title.textContent='Пользовательская версия';
    const body=document.createElement('div');body.className='surface-version-text';body.textContent=value.saved;
    section.append(title,body);workspace.append(section);
  }
  function mount(element,id,{editable=false}={}) {
    if(element.closest('.content-surface'))return element.closest('.content-surface');
    const workspace=document.createElement('section');workspace.className='content-surface';workspace.dataset.surfaceId=id;workspace.setAttribute('aria-label','Рабочее содержимое');
    const toolbar=document.createElement('div');toolbar.className='surface-toolbar';
    const handle=button('move','⠿');handle.className='surface-handle';handle.title='Перемещение: перетащите или используйте стрелки';handle.setAttribute('aria-label','Переместить содержимое');
    toolbar.append(handle,button('zoom-out','−'),button('zoom-reset','100%'),button('zoom-in','+'),button('expand','Раскрыть'));
    if(editable)toolbar.append(button('edit','Редактировать'));
    const moves=document.createElement('div');moves.className='surface-moves';
    for(const [action,label] of [['left','←'],['up','↑'],['down','↓'],['right','→']]){const b=button(action,label);b.setAttribute('aria-label','Переместить '+({left:'влево',right:'вправо',up:'вверх',down:'вниз'})[action]);moves.append(b);}toolbar.append(moves);
    const bounds=document.createElement('div');bounds.className='surface-bounds';
    const object=document.createElement('div');object.className='surface-object';
    element.replaceWith(workspace);object.append(element);bounds.append(object);workspace.append(toolbar,bounds);
    const value=load(id);saveVersion(workspace,value);apply(workspace,value);
    const update=()=>{apply(workspace,value);persist(id,value);};
    toolbar.addEventListener('click',event=>{
      const action=event.target.closest('[data-surface-action]')?.dataset.surfaceAction;
      if(!action)return;
      if(action==='zoom-in')value.zoom=clamp(value.zoom+.1,.75,2);
      if(action==='zoom-out')value.zoom=clamp(value.zoom-.1,.75,2);
      if(action==='zoom-reset')value.zoom=1;
      if(action==='expand')value.expanded=!value.expanded;
      if(action==='edit'){edit(workspace,id,element.querySelector('.smart-text-content')?.textContent||element.textContent);return;}
      if(action==='left')value.x=clamp(value.x-8,0,32);
      if(action==='right')value.x=clamp(value.x+8,0,32);
      if(action==='up')value.y=clamp(value.y-8,0,32);
      if(action==='down')value.y=clamp(value.y+8,0,32);
      update();
    });
    handle.addEventListener('keydown',event=>{
      const actions={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'};
      if(actions[event.key]){event.preventDefault();toolbar.querySelector(`[data-surface-action="${actions[event.key]}"]`).click();}
    });
    let drag=null;
    handle.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();handle.setPointerCapture(event.pointerId);drag={px:event.clientX,py:event.clientY,x:value.x,y:value.y};});
    handle.addEventListener('pointermove',event=>{if(!drag)return;value.x=clamp(drag.x+event.clientX-drag.px,0,32);value.y=clamp(drag.y+event.clientY-drag.py,0,32);apply(workspace,value);});
    const finish=()=>{if(drag){drag=null;persist(id,value);}};handle.addEventListener('pointerup',finish);handle.addEventListener('pointercancel',finish);
    return workspace;
  }
  function enhance(container) {
    container.querySelectorAll('.smart-text.collapsible').forEach(element=>{
      if(element.closest('.msg.user'))return;
      mount(element,element.dataset.smartKey,{editable:true});
    });
    if(editing && editing.scope!==scope()) {editor.hidden=true;}
    else if(editing)editor.hidden=false;
  }
  function editorUI() {
    if(editor)return editor;
    editor=document.createElement('section');editor.id='surfaceEditor';editor.className='surface-editor';editor.setAttribute('aria-label','Редактор пользовательской версии');editor.hidden=true;
    const title=document.createElement('strong');title.textContent='Редактор · отдельная версия';
    const toolbar=document.createElement('div');toolbar.className='surface-editor-toolbar';toolbar.append(title);
    for(const [a,l] of [['undo','Отменить'],['redo','Повторить'],['save','Сохранить версию'],['discard','Сбросить изменения'],['close','Закрыть']])toolbar.append(button(a,l));
    const textarea=document.createElement('textarea');textarea.maxLength=LIMIT;textarea.setAttribute('aria-label','Текст пользовательской версии');textarea.spellcheck=true;
    const message=document.createElement('div');message.className='surface-editor-state';message.textContent='Оригинал истории не меняется. В файл автоматически не сохраняется.';
    editor.append(toolbar,textarea,message);document.getElementById('chatMessages').before(editor);
    textarea.addEventListener('input',()=>{
      if(!editing)return;
      editing.undo.push(editing.value.draft);if(editing.undo.length>50)editing.undo.shift();editing.redo=[];
      editing.value.draft=textarea.value;persist(editing.id,editing.value,editing.key);
    });
    textarea.addEventListener('keydown',event=>{
      if((event.ctrlKey||event.metaKey)&&['z','y'].includes(event.key.toLowerCase())){event.preventDefault();history(event.key.toLowerCase()==='y'||event.shiftKey?'redo':'undo');}
    });
    toolbar.addEventListener('click',event=>{
      const action=event.target.closest('[data-surface-action]')?.dataset.surfaceAction;if(!editing||editing.scope!==scope()||!action)return;
      if(action==='undo'||action==='redo'){history(action);return;}
      if(action==='save'){editing.value.saved=textarea.value;editing.value.draft=textarea.value;persist(editing.id,editing.value,editing.key);const w=findWorkspace(editing.id);if(w)saveVersion(w,editing.value);message.textContent='Версия сохранена в этом браузере. Оригинал истории не изменён.';}
      if(action==='discard') {
        const base=editing.value.saved??editing.value.original;
        if(textarea.value!==base&&!window.confirm('Сбросить несохранённые изменения этой версии?'))return;
        editing.value.draft=base;textarea.value=base;editing.undo=[];editing.redo=[];persist(editing.id,editing.value,editing.key);
      }
      if(action==='close'){editor.hidden=true;editing=null;}
    });return editor;
  }
  function findWorkspace(id){return Array.from(document.querySelectorAll('.content-surface')).find(w=>w.dataset.surfaceId===id);}
  function history(direction) {
    const from=editing[direction],to=editing[direction==='undo'?'redo':'undo'];if(!from.length)return;
    to.push(editing.value.draft);editing.value.draft=from.pop();editor.querySelector('textarea').value=editing.value.draft;persist(editing.id,editing.value,editing.key);
  }
  function edit(workspace,id,source) {
    if(source.length>LIMIT){showToast('Текст слишком большой для встроенного редактора.',true);return;}
    const value=load(id);if(value.original===undefined)value.original=source;
    if(value.draft===undefined)value.draft=value.saved??value.original;
    const box=editorUI();if(editing?.key!==storageKey(id))editing={id,key:storageKey(id),scope:scope(),value,undo:[],redo:[]};
    box.querySelector('textarea').value=value.draft;box.hidden=false;persist(id,value);box.querySelector('textarea').focus();
  }
  document.addEventListener('DOMContentLoaded',()=>enhance(document.getElementById('chatMessages')));
  return Object.freeze({enhance,mount});
})();
