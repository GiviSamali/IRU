const STATUS={current:'Описание сверено с реализацией',review_required:'Нужна повторная проверка',missing_description:'Человеческое описание пока отсутствует',orphaned_record:'Запись сохранена, сущность отсутствует',status_unavailable:'Актуальность не рассчитана для этого снимка'};
export async function loadKnowledge(model){
 const paths=['knowledge/nodes.json','knowledge/relationships.json','data/knowledge-status.json','data/critical-review.json'];
 const values=await Promise.all(paths.map(async path=>{try{const response=await fetch(path);if(!response.ok)return null;return await response.json();}catch{return null;}}));
 const [nodes,relations,status,review]=values;const issues=[];
 function records(catalog,key){if(!catalog)return {};if(catalog.schema_version!==1||!catalog[key]||typeof catalog[key]!=='object'||Array.isArray(catalog[key])){issues.push('Некорректная схема '+key);return {};}const result=Object.create(null);for(const [id,record] of Object.entries(catalog[key])){if(!record||record.id!==id){issues.push('Привязка знаний не совпадает с ID: '+id);continue;}result[id]=record;}return result;}
 const criticalReview=review?.schema_version===1&&review.architecture_git_sha===model.g.git_sha&&Array.isArray(review.records)?review:null;if(review&&!criticalReview)issues.push('Разбор важных узлов не совпадает с версией снимка');
 return {criticalReview,nodes:records(nodes,'nodes'),relationships:records(relations,'relationships'),status:status?.architecture_git_sha===model.g.git_sha?status:null,issues};
}
export function nodeArticle(model,knowledge,id){
 const n=model.nodes.get(id);if(!n)throw Error('Unknown node');const record=knowledge.nodes[id];const status=record?(record.reviewed===false?'review_required':knowledge.status?.nodes?.[id]?.status||'status_unavailable'):'missing_description';
 const fact=knowledge.status?.technical_facts?.[id];
 return {record,status,statusLabel:STATUS[status],what:record?.what||fact?.docstring||n.description,why:record?.why||null,how:record?.how||null,signature:fact?.signature||null};
}
export function relationshipArticle(model,knowledge,id){
 const edge=model.edges.get(id);if(!edge)throw Error('Unknown edge');const record=knowledge.relationships[id];
 const source=model.nodes.get(edge.source).name,target=model.nodes.get(edge.target).name;
 const fallback={
  contains:{purpose:`${target} находится внутри ${source} в навигационной иерархии. Это не вызов и не передача данных.`,data_or_signals:'Данные между компонентами этим отношением не установлены.'},
  implements:{purpose:`Исходная сущность ${source} привязана к роли ${target}. Это реализация механизма, а не факт его выполнения.`,data_or_signals:'Роль подтверждается указанным исходным определением; runtime контракт не выводится из implements.'},
  import:{purpose:`Модуль ${source} статически импортирует ${target}. Назначение конкретного использования не установлено.`,data_or_signals:'Имена импортируемых сущностей определены исходным import; фактическая передача runtime данных не подтверждена.'},
  static_call:{purpose:`В реализации ${source} обнаружен именованный вызов ${target}.`,data_or_signals:'Аргументы следует проверять в исходнике. Само ребро не описывает полный контракт и не подтверждает конкретный запуск.'},
  depends_on:{purpose:`Агрегированная архитектурная зависимость ${source} от ${target} выведена из привязанных статических отношений.`,data_or_signals:'Точная цель и данные этой зависимости ещё не описаны человеком; backing source edge остаётся доказательством.'}
 };
 const status=record?(record.reviewed===false?'review_required':knowledge.status?.relationships?.[id]?.status||'status_unavailable'):'missing_description';
 const inferred=edge.confidence==='inferred'||edge.evidence_kind==='inferred';const purpose=record?.purpose||fallback[edge.type]?.purpose||'Тип отношения известен, но его назначение ещё не установлено.';
 return {edge,source,target,record,status,statusLabel:STATUS[status],purpose:(inferred?'Непроверенная функциональная привязка; назначение требует подтверждения. ':'')+purpose,data_or_signals:record?.data_or_signals||fallback[edge.type]?.data_or_signals||'Контракт неизвестен.',conditions:record?.conditions||'Условия исполнения по этому статическому ребру не установлены.',failure_behavior:record?.failure_behavior||'Поведение при недоступности зависимости неизвестно; оно не выводится из степени связности.'};
}
