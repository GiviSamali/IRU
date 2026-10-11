import {levelPreset} from './model.mjs';
// Existing command buttons keep their IDs and handlers; menus only manage focus.
export function mountTopNavigation(header){
 const roots=[...header.querySelectorAll('[data-menu-root]')];
 const compact=header.querySelector('#compactCommands'),narrow=matchMedia('(max-width:1000px)');function adapt(){if(compact)compact.open=!narrow.matches;}adapt();narrow.addEventListener('change',adapt);
 function close(except=null){for(const root of roots)if(root!==except){root.open=false;for(const child of root.querySelectorAll('details'))child.open=false;}if(!except&&compact&&narrow.matches)compact.open=false;}
 for(const root of roots)root.addEventListener('toggle',()=>{if(root.open)close(root);});
 header.addEventListener('click',event=>{if(event.target.closest('button[data-menu-action]')){const root=event.target.closest('[data-menu-root]');close();(narrow.matches?compact:root)?.querySelector('summary').focus();}});
 document.addEventListener('pointerdown',event=>{if(!header.contains(event.target))close();});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){const open=roots.find(root=>root.open)||(narrow.matches&&compact?.open?compact:null);if(open){event.preventDefault();close();(narrow.matches?compact:open).querySelector('summary').focus();}}});
 header.addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;const root=event.target.closest('[data-menu-root]');if(!root?.open)return;const choices=[...root.querySelectorAll('button,input,select,summary')].filter(el=>el.getClientRects().length&&!el.disabled);const index=choices.indexOf(document.activeElement);if(!choices.length)return;event.preventDefault();choices[(index+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length].focus();});
 const observer=new ResizeObserver(()=>document.documentElement.style.setProperty('--atlas-header-height',header.offsetHeight+'px'));observer.observe(header);
 return {close};
}
export function mountNodeFilters(model,{levelsHost,typesHost,onChange}){
 let filters={levels:null,nodeTypes:null};const levels=[...new Set(model.g.nodes.map(n=>n.level))].sort((a,b)=>a-b);const types=[...new Set(model.g.nodes.map(n=>n.type))].sort();
 const node=(parent,tag,value)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;parent.append(e);return e;};
 const levelChoices=node(levelsHost,'div');levelChoices.className='filter-choices';
 for(const level of levels){const label=node(levelChoices,'label');const input=node(label,'input');input.type='checkbox';input.value=level;input.checked=true;node(label,'span',' '+level);input.onchange=()=>{filters={...filters,levels:new Set([...levelChoices.querySelectorAll('input:checked')].map(x=>Number(x.value)))};sync();onChange(filters);};}
 const preset=node(levelsHost,'div');preset.className='preset-controls';const n=node(preset,'select');n.setAttribute('aria-label','Уровень N');for(const level of levels){const o=node(n,'option',String(level));o.value=level;}n.value=levels.includes(2)?'2':String(levels[0]);
 for(const [action,label] of [['all','Все'],['only','Только выбранный уровень'],['until','До уровня N'],['from','От уровня N'],['reset','Сбросить фильтр']]){const b=node(preset,'button',label);b.dataset.levelAction=action;b.onclick=()=>{filters={...filters,levels:levelPreset(levels,action,Number(n.value))};sync();onChange(filters);};}
 const typeChoices=node(typesHost,'div');typeChoices.className='filter-choices';for(const type of types){const label=node(typeChoices,'label');const input=node(label,'input');input.type='checkbox';input.value=type;input.checked=true;node(label,'span',' '+type);input.onchange=()=>{filters={...filters,nodeTypes:new Set([...typeChoices.querySelectorAll('input:checked')].map(x=>x.value))};sync();onChange(filters);};}
 const allTypes=node(typesHost,'button','Все типы / сброс');allTypes.onclick=()=>{filters={...filters,nodeTypes:null};sync();onChange(filters);};
 function sync(){for(const input of levelChoices.querySelectorAll('input'))input.checked=filters.levels===null||filters.levels.has(Number(input.value));for(const input of typeChoices.querySelectorAll('input'))input.checked=filters.nodeTypes===null||filters.nodeTypes.has(input.value);}
 return {get:()=>filters,set(value){filters=value;sync();onChange(filters);},reset(){filters={levels:null,nodeTypes:null};sync();},levels,types};
}
