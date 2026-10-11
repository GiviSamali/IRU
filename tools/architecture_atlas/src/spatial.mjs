// Pure deterministic layout. It is computed against the entire model once.
export const NODE_SCALE={system:6,subsystem:3.6,component:2.2,mechanism:1.5,implementation_group:1.1,module:1,class:.8,function:.55};
export function hash(s){let h=2166136261;for(let i=0;i<s.length;i++)h=Math.imul(h^s.charCodeAt(i),16777619);return h>>>0;}
export function spherePoint(i,n,r=1,phase=0){const y=1-2*(i+.5)/n;const a=i*Math.PI*(3-Math.sqrt(5))+phase;const f=Math.sqrt(Math.max(0,1-y*y));return [Math.cos(a)*f*r,y*r,Math.sin(a)*f*r];}
export function layout(model){
 const coordinates=new Map([['iru',[0,0,0]]]);
 const visit=id=>{
  const parent=model.nodes.get(id),origin=coordinates.get(id);
  const children=[...(model.children.get(id)||[])].sort();
  const phase=hash(id)/4294967296*Math.PI*2;
  children.forEach((child,i)=>{
   const n=model.nodes.get(child);
   const base=parent.type==='system'?190:parent.type==='subsystem'?42:parent.type==='component'?20:parent.type==='mechanism'?12:parent.type==='implementation_group'?35:parent.type==='module'?9:6;
   // Large implementation families occupy several nested shells, not a flat ring.
   const shell=parent.type==='system'?1:Math.max(.48,Math.cbrt((i+.6)/Math.max(1,children.length)));
   const density=parent.layer==='physical'?Math.min(1.65,Math.max(1,Math.cbrt(children.length/35))):1;
   const offset=spherePoint(i,children.length,base*shell*density,phase);
   coordinates.set(child,origin.map((v,k)=>v+offset[k]));visit(child);
  });
 };
 visit('iru');
 if(coordinates.size!==model.nodes.size)throw Error('Layout requires a single IRU-rooted hierarchy');
 return coordinates;
}
export function region(model,coordinates,id){const ids=[],todo=[id];while(todo.length){const n=todo.pop();ids.push(n);todo.push(...model.children.get(n)||[]);}const center=coordinates.get(id);let radius=8;for(const n of ids){radius=Math.max(radius,Math.hypot(...coordinates.get(n).map((v,k)=>v-center[k]))+(NODE_SCALE[model.nodes.get(n).type]||.55));}return {center,radius,ids};}
export function fitDistance(radius,fov,aspect){return radius/Math.sin(Math.atan(Math.tan(fov*Math.PI/360)*Math.min(1,Math.max(.1,aspect))))*1.12;}
