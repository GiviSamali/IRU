import {route} from './model.mjs';
import {confirmedRelationship} from './diagnostics.mjs';

export function shortestArchitecturePath(model,a,b,types){
 const edgeIds=route(model,a,b,new Set(types),{edgeAllowed:confirmedRelationship});
 if(edgeIds===null)return {status:'not_found',source:a,target:b,edgeIds:[],nodeIds:[],steps:[],types:[...types].sort()};
 const nodeIds=[a],steps=[];let current=a;
 for(const id of edgeIds){const edge=model.edges.get(id);const next=edge.source===current?edge.target:edge.direction==='both'?edge.source:null;if(!next)throw Error('Invalid path direction');steps.push({edge_id:id,source:current,target:next,type:edge.type,direction:edge.direction,evidence:edge.evidence||[]});nodeIds.push(next);current=next;}
 return {status:'found',source:a,target:b,edgeIds,nodeIds,steps,types:[...types].sort()};
}
// Pure state transitions are shared by map, tree and addressed interaction tests.
export function initialInteraction(){return {pathMode:false,a:null,b:null,result:null,highlight:'overview',selected:null,detailsNode:null,detailsOpen:false,diagnosticNode:null,executionActive:false};}
export function reduceInteraction(state,action){
 switch(action.type){
  case 'enter_path':return {...state,pathMode:true,highlight:'path',executionActive:false};
  case 'exit_path':return {...state,pathMode:false,highlight:'overview',selected:null};
  case 'click_node':
   if(state.pathMode){if(!state.a)return {...state,a:action.id,b:null,result:null,highlight:'path'};if(action.id===state.a&&!state.b)return state;return {...state,b:action.id,result:null,highlight:'path'};}
   return {...state,selected:action.id,detailsNode:state.detailsOpen?action.id:state.detailsNode,detailsEdgeId:state.detailsOpen?null:state.detailsEdgeId,diagnosticNode:null,highlight:'selection',executionActive:false};
  case 'double_node':return state.pathMode?state:{...state,selected:action.id,detailsNode:action.id,detailsEdgeId:null,detailsOpen:true,highlight:'selection',executionActive:false};
  case 'open_details':return {...state,detailsNode:action.id,detailsEdgeId:null,detailsOpen:true};
  case 'open_edge':return {...state,detailsEdgeId:action.id,detailsOpen:true};
  case 'close_details':return {...state,detailsOpen:false};
  case 'set_path':return {...state,result:action.result,highlight:'path',executionActive:false};
  case 'new_target':return {...state,b:null,result:null,highlight:'path'};
  case 'swap_path':return {...state,a:state.b,b:state.a,result:null,highlight:'path',executionActive:false};
  case 'reset_path':return {...state,a:null,b:null,result:null,highlight:state.highlight==='path'?'overview':state.highlight};
  case 'diagnostic_node':return {...state,pathMode:false,selected:action.id,diagnosticNode:action.id,highlight:'diagnostic',executionActive:false};
  case 'reset_diagnostics':return {...state,diagnosticNode:null,highlight:state.highlight==='diagnostic'?'overview':state.highlight};
  case 'clear_selection':return state.highlight==='selection'?{...state,selected:null,highlight:'overview'}:state;
  case 'execution':return {...state,pathMode:false,selected:action.id||null,highlight:'execution',executionActive:true};
  case 'stop_execution':return {...state,executionActive:false,highlight:state.highlight==='execution'?'overview':state.highlight};
  case 'overview':return {...state,pathMode:false,selected:null,diagnosticNode:null,highlight:'overview',executionActive:false};
  default:return state;
 }
}
