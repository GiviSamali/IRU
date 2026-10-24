export const DIRECTION_COLORS={incoming:0xd6a6ff,outgoing:0xffb454,other:0x596574};
export function edgeDirection(edge,node){
 if(edge.source!==node&&edge.target!==node)return 'other';
 if(edge.direction==='both'||edge.source===edge.target)return 'both';
 return edge.target===node?'incoming':'outgoing';
}
export function directConnections(model,id){
 const edges=[...new Map([...model.incoming.get(id),...model.outgoing.get(id)].map(e=>[e.id,e])).values()];
 return {incoming:edges.filter(e=>['incoming','both'].includes(edgeDirection(e,id))),outgoing:edges.filter(e=>['outgoing','both'].includes(edgeDirection(e,id)))};
}
