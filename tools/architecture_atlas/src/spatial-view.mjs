import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {layout,region,fitDistance,NODE_SCALE} from './spatial.mjs';
const COLORS=[0x75b4e4,0xa197dc,0xe0b56d,0x72c2ad,0x899dcc,0xd78f8c,0xaccb85];

export class SpatialView {
 constructor(model,host,labelHost){
  this.model=model;this.host=host;this.labelHost=labelHost;this.glowStrength=1;this.ids=[...model.nodes.keys()];this.indices=new Map(this.ids.map((id,i)=>[id,i]));
  this.positions=new Map([...layout(model)].map(([id,p])=>[id,new THREE.Vector3(...p)]));this.visible=new Set();this.selected='iru';this.labels=[];this.labelRefresh=0;
  this.renderer=new THREE.WebGLRenderer({antialias:true});this.renderer.setClearColor(0x070b10);this.renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));host.append(this.renderer.domElement);
  this.scene=new THREE.Scene();this.scene.fog=new THREE.FogExp2(0x070b10,.0011);this.camera=new THREE.PerspectiveCamera(50,1,.1,4000);this.camera.position.set(0,110,700);
  this.controls=new OrbitControls(this.camera,this.renderer.domElement);this.controls.enableDamping=true;this.controls.dampingFactor=.08;this.controls.minDistance=12;this.controls.maxDistance=2400;
  this.sphere=new THREE.SphereGeometry(1,12,10);this.nodeMaterial=new THREE.MeshBasicMaterial({transparent:false});this.mesh=new THREE.InstancedMesh(this.sphere,this.nodeMaterial,this.ids.length);this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);this.scene.add(this.mesh);
  this.edgeList=[...model.edges.values()];this.edgeGeometry=new THREE.BufferGeometry();this.edgePositions=new Float32Array(this.edgeList.length*6);this.edgeColors=new Float32Array(this.edgeList.length*6);this.edgeAlpha=new Float32Array(this.edgeList.length*2);
  this.edgeGeometry.setAttribute('position',new THREE.BufferAttribute(this.edgePositions,3).setUsage(THREE.DynamicDrawUsage));this.edgeGeometry.setAttribute('color',new THREE.BufferAttribute(this.edgeColors,3).setUsage(THREE.DynamicDrawUsage));this.edgeGeometry.setAttribute('visibility',new THREE.BufferAttribute(this.edgeAlpha,1).setUsage(THREE.DynamicDrawUsage));
  this.edgeMaterial=new THREE.ShaderMaterial({transparent:true,depthWrite:false,vertexShader:'attribute vec3 color; attribute float visibility; varying vec3 tint; varying float alpha; varying float distanceToCamera; void main(){vec4 p=modelViewMatrix*vec4(position,1.0);distanceToCamera=-p.z;tint=color;alpha=visibility;gl_Position=projectionMatrix*p;}',fragmentShader:'varying vec3 tint; varying float alpha; varying float distanceToCamera; void main(){if(alpha<0.001)discard;float depth=exp(-distanceToCamera*0.0009);gl_FragColor=vec4(tint,alpha*depth);}'});
  this.lines=new THREE.LineSegments(this.edgeGeometry,this.edgeMaterial);this.lines.frustumCulled=false;this.scene.add(this.lines);
  const bytes=new Uint8Array(64*64*4);for(let y=0;y<64;y++)for(let x=0;x<64;x++){const d=Math.hypot(x-31.5,y-31.5)/31.5,i=(y*64+x)*4;bytes[i]=bytes[i+1]=bytes[i+2]=255;bytes[i+3]=Math.round(255*Math.max(0,1-d)**3);}this.glowTexture=new THREE.DataTexture(bytes,64,64);this.glowTexture.needsUpdate=true;this.glows=[];
  const glow=(id,color,size)=>{const mat=new THREE.SpriteMaterial({map:this.glowTexture,color,transparent:true,opacity:.3,blending:THREE.AdditiveBlending,depthWrite:false});const s=new THREE.Sprite(mat);s.position.copy(this.positions.get(id));s.scale.set(size,size,1);this.scene.add(s);this.glows.push({id,s,size});return s;};
  glow('iru',0xffbb59,42);for(const n of model.nodes.values())if(n.type==='subsystem')glow(n.id,COLORS[Number(n.id.split(':')[1])],24);
  this.selectedGlow=glow('iru',0xffd88c,22);this.selectedGlow.material.depthTest=false;this.selectedGlow.renderOrder=8;
  this.core=new THREE.Mesh(new THREE.SphereGeometry(5.5,24,20),new THREE.MeshBasicMaterial({color:0xf2c16e,depthTest:false}));this.core.renderOrder=7;this.scene.add(this.core);
  this.ray=new THREE.Raycaster();this.matrix=new THREE.Matrix4();this.quaternion=new THREE.Quaternion();this.scale=new THREE.Vector3();this.color=new THREE.Color();
  this.constellations=new Map();for(const n of model.nodes.values()){let p=n;while(p.parent_id&&p.type!=='subsystem')p=model.nodes.get(p.parent_id);this.constellations.set(n.id,p.type==='subsystem'?Number(p.id.split(':')[1]):-1);}
  this.resize();this.focus('iru',true);
 }
 position(id){return this.positions.get(id);}
 setVisible(nodes){
  this.visible=new Set(nodes.map(n=>n.id));for(let i=0;i<this.ids.length;i++){const id=this.ids[i],n=this.model.nodes.get(id),s=this.visible.has(id)?NODE_SCALE[n.type]||.55:0;this.scale.setScalar(s);this.matrix.compose(this.position(id),this.quaternion,this.scale);this.mesh.setMatrixAt(i,this.matrix);}this.mesh.instanceMatrix.needsUpdate=true;this.mesh.computeBoundingSphere();
  for(let i=0;i<this.edgeList.length;i++){const e=this.edgeList[i],visible=this.visible.has(e.source)&&this.visible.has(e.target);const a=visible?this.position(e.source):new THREE.Vector3(),b=visible?this.position(e.target):a;a.toArray(this.edgePositions,i*6);b.toArray(this.edgePositions,i*6+3);}this.edgeGeometry.attributes.position.needsUpdate=true;this.labelRefresh=0;
 }
 paint(selected,nodeIds,edgeIds,pathEdges,types,{kind='selection',pathNodes:explicitPathNodes=null}={}){
  this.selected=selected;this.activeTypes=types;const pathNodes=new Set(explicitPathNodes||[]);for(const id of pathEdges){const e=this.model.edges.get(id);if(e){pathNodes.add(e.source);pathNodes.add(e.target);}}
  const routeActive=kind==='path'||kind==='execution',overview=kind==='overview';
  for(let i=0;i<this.ids.length;i++){const id=this.ids[i];this.color.setHex(id==='iru'?0xf2c16e:COLORS[this.constellations.get(id)]||0x7d8fa4);const active=routeActive?pathNodes.has(id):nodeIds.has(id);if(!overview&&id!==selected&&!active)this.color.multiplyScalar(.19);if(routeActive&&active)this.color.setHex(kind==='path'?0xffcb68:0xf6be82);if(!overview&&id===selected)this.color.setHex(0xffdf99);this.mesh.setColorAt(i,this.color);}this.mesh.instanceColor.needsUpdate=true;
  for(let i=0;i<this.edgeList.length;i++){const e=this.edgeList[i],enabled=this.visible.has(e.source)&&this.visible.has(e.target)&&types.has(e.type),active=routeActive?pathEdges.has(e.id):edgeIds.has(e.id);const structural=e.type==='contains';const dim=!overview&&!active;const accent=kind==='path'?0xffc451:kind==='diagnostic'?0x7dd8d0:0xffd184;this.color.setHex(active?accent:structural?0x52667d:e.evidence_kind==='static_dependency'?0x6d91b9:0x789b9c);this.color.toArray(this.edgeColors,i*6);this.color.toArray(this.edgeColors,i*6+3);this.edgeAlpha[i*2]=this.edgeAlpha[i*2+1]=!enabled?0:active?.95:dim?.035:structural?.1:.28;}
  this.edgeGeometry.attributes.color.needsUpdate=true;this.edgeGeometry.attributes.visibility.needsUpdate=true;
  for(const g of this.glows){g.s.visible=this.visible.has(g.id);g.s.material.opacity=(g.id==='iru'?.4:nodeIds.has(g.id)?.3:.15)*this.glowStrength;}
  this.selectedGlow.position.copy(this.position(selected));this.selectedGlow.visible=!overview&&this.visible.has(selected);const size=(NODE_SCALE[this.model.nodes.get(selected).type]||.55)*7;this.selectedGlow.scale.set(size,size,1);this.selectedGlow.material.opacity=.65*this.glowStrength;
  this.labelRefresh=0;
 }
 focus(id,overview=false){const p=overview?new THREE.Vector3():this.position(id);let radius=overview?this.bounds('iru').radius:this.model.nodes.get(id).type==='subsystem'||this.model.nodes.get(id).type==='component'?this.bounds(id).radius:Math.max(8,(NODE_SCALE[this.model.nodes.get(id).type]||1)*5);const distance=Math.max(18,fitDistance(radius,this.camera.fov,this.camera.aspect));let direction=this.camera.position.clone().sub(this.controls.target).normalize();if(direction.lengthSq()<.1)direction.set(.25,.2,1).normalize();this.transition={from:this.camera.position.clone(),to:p.clone().addScaledVector(direction,distance),targetFrom:this.controls.target.clone(),target:p.clone(),start:performance.now()};}
 bounds(id){return region(this.model,new Map([...this.positions].map(([key,v])=>[key,v.toArray()])),id);}
 resize(){const w=this.host.clientWidth,h=this.host.clientHeight;this.renderer.setSize(w,h,false);this.camera.aspect=w/Math.max(h,1);this.camera.updateProjectionMatrix();this.labelRefresh=0;}
 quality(value){this.renderer.setPixelRatio(Math.min(devicePixelRatio,value));for(const g of this.glows)g.s.material.opacity=value<1?.12:.3;this.resize();}
 pick(event){const rect=this.renderer.domElement.getBoundingClientRect();this.ray.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),this.camera);for(const hit of this.ray.intersectObject(this.mesh)){const id=this.ids[hit.instanceId];if(this.visible.has(id))return id;}return null;}
 update(now){
  if(this.transition){const t=Math.min(1,(now-this.transition.start)/700),s=t*t*(3-2*t);this.camera.position.lerpVectors(this.transition.from,this.transition.to,s);this.controls.target.lerpVectors(this.transition.targetFrom,this.transition.target,s);if(t===1)this.transition=null;}this.controls.update();
  if(now-this.labelRefresh>180){this.labelRefresh=now;this.refreshLabels();}
  const w=this.host.clientWidth,h=this.host.clientHeight;const occupied=[];
  for(const {id,el,width} of this.labels){const p=this.position(id).clone().project(this.camera);const chosen=id===this.selected;let x=(p.x+1)*w/2+6,y=(-p.y+1)*h/2;let hidden=!chosen&&(p.z>1||p.z< -1||x<0||x+width>w||y<50||y>h-50);if(chosen){x=Math.max(6,Math.min(w-width-6,x));y=Math.max(60,Math.min(h-45,y));}const rect=[x,y,width,17];if(!chosen&&occupied.some(r=>x<r[0]+r[2]+5&&x+width>r[0]-5&&y<r[1]+r[3]&&y+17>r[1]))hidden=true;el.hidden=hidden;if(!hidden){occupied.push(rect);el.style.transform=`translate(${x}px,${y}px)`;}}
 }
 refreshLabels(){
  const rank=n=>n.id===this.selected?-100:n.type==='system'?-90:n.type==='subsystem'?-80:n.level;
  const candidates=[...this.visible].map(id=>this.model.nodes.get(id)).filter(n=>{const d=this.camera.position.distanceTo(this.position(n.id));return n.id===this.selected||n.type==='system'||n.type==='subsystem'||d<(n.type==='component'?250:n.type==='mechanism'?130:n.type==='module'?85:55);}).sort((a,b)=>rank(a)-rank(b)||this.camera.position.distanceTo(this.position(a.id))-this.camera.position.distanceTo(this.position(b.id))).slice(0,80);
  const old=new Map(this.labels.map(l=>[l.id,l]));const next=[];for(const n of candidates){let label=old.get(n.id);if(label)old.delete(n.id);else{const el=document.createElement('span');el.className='label';el.textContent=n.type==='module'?n.name.split('/').pop():n.name;this.labelHost.append(el);label={id:n.id,el,width:Math.min(260,el.textContent.length*6.3)};}label.el.classList.toggle('chosen',n.id===this.selected);next.push(label);}for(const l of old.values())l.el.remove();this.labels=next;
 }
 dispose(){this.controls.dispose();this.mesh.dispose();this.sphere.dispose();this.nodeMaterial.dispose();this.edgeGeometry.dispose();this.edgeMaterial.dispose();this.glowTexture.dispose();for(const {s} of this.glows)s.material.dispose();this.core.geometry.dispose();this.core.material.dispose();this.renderer.dispose();}
}
