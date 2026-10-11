import {confirmedRelationship} from './diagnostics.mjs';
export function effectiveHighlightKind(interaction,programmed){return interaction.highlight==='execution'&&interaction.executionActive?'execution':programmed.active?'programmatic':interaction.highlight;}

export function createProgrammaticHighlight(model,onChange=()=>{}){
 let state={active:false,nodeIds:new Set(),edgeIds:new Set(),label:''};
 function identifiers(value,limit,kind){if(!Array.isArray(value)||value.length>limit||value.some(id=>typeof id!=='string'))throw Error('Expected bounded '+kind+' ID array');return new Set(value);}
 function set(nodeIds,edgeIds,label){
  const nodes=identifiers(nodeIds,5000,'node'),edges=identifiers(edgeIds,20000,'edge');
  for(const id of nodes)if(!model.nodes.has(id))throw Error('Unknown node ID: '+id);
  for(const id of edges){const edge=model.edges.get(id);if(!edge||!confirmedRelationship(edge))throw Error('Unknown or unconfirmed architectural edge: '+id);nodes.add(edge.source);nodes.add(edge.target);}
  if(typeof label!=='string'||label.length>200)throw Error('Invalid highlight label');
  // Validation completes before state changes. Never executes code or builds edges.
  state={active:nodes.size>0||edges.size>0,nodeIds:nodes,edgeIds:edges,label};onChange(snapshot());return snapshot();
 }
 function snapshot(){return {active:state.active,node_ids:[...state.nodeIds],edge_ids:[...state.edgeIds],label:state.label};}
 const api=Object.freeze({highlight_nodes:(ids,label='Группа узлов')=>set(ids,[],label),highlight_edges:(ids,label='Архитектурные связи')=>set([],ids,label),highlight_subgraph:(nodes,edges,label='Архитектурный подграф')=>set(nodes,edges,label),clear_highlight:()=>set([],[],''),get_state:snapshot});
 return {api,state:()=>state};
}

export function shortRightClick(start,end,{distance=5,duration=450}={}){
 return !!start&&start.button===2&&end.button===2&&start.pointerId===end.pointerId&&end.time-start.time>=0&&end.time-start.time<=duration&&Math.max(Math.hypot(end.x-start.x,end.y-start.y),end.maxDistance||0)<=distance;
}
