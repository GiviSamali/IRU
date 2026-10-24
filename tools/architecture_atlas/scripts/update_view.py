"""One-time v0.2 source composition; execution player block is kept verbatim."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'src/app.mjs'
source=path.read_text(encoding='utf-8-sig')
start=source.index('const palette=');end=source.index('function reveal(')
source=source[:start]+'''const expanded=new Set(['iru',...Array.from({length:7},(_,i)=>'group:'+i)]);
let full=false,selected='iru',mode='Architecture',related=new Set(),relatedEdges=new Set(),pathEdges=new Set(),startNode=null,physicalTree=false;
const view=new SpatialView(model,$('viewport'),$('labels'));
const {renderer,scene,camera,controls}=view;
const position=id=>view.position(id);
let visible=[],index=new Map();
const pulseGeometry=new THREE.SphereGeometry(1.7,14,10),pulseMaterial=new THREE.MeshBasicMaterial({color:0xffdd88});const pulse=new THREE.Mesh(pulseGeometry,pulseMaterial);pulse.visible=false;scene.add(pulse);
function types(){return new Set([...document.querySelectorAll('.edgeType:checked')].map(x=>x.value));}
function paint(){const selection=selectionRelations(model,selected,$('direction').value,$('transitive').checked,types());related=selection.nodeIds;relatedEdges=selection.edgeIds;view.paint(selected,related,relatedEdges,pathEdges,types());}
function rebuild(){visible=visibleNodes(model,expanded,full);index=new Map(visible.map((n,i)=>[n.id,i]));view.setVisible(visible);paint();tree();}
function focus(id,overview=false){view.focus(id,overview);}
''' +source[end:]
source=source.replace("import {validateGraph,visibleNodes,search,dependencies,route,importTrace,pulseEdge}","import {SpatialView} from './spatial-view.mjs';\nimport {validateGraph,visibleNodes,search,selectionRelations,route,importTrace,pulseEdge}")
start=source.index('const ray=new THREE.Raycaster()');end=source.index("renderer.domElement.addEventListener('click'",start)
source=source[:start]+"let pointerStart=null;renderer.domElement.addEventListener('pointerdown',e=>pointerStart=[e.clientX,e.clientY]);const pick=e=>view.pick(e);\n"+source[end:]
start=source.index('function resize()');end=source.index(' if(playing&&mode',start)
source=source[:start]+'''function resize(){view.resize();}const observer=new ResizeObserver(resize);observer.observe($('viewport'));rebuild();details();resize();view.focus('iru',true);
let frame=0;function animate(now){frame=requestAnimationFrame(animate);view.update(now);
'''+source[end:]
start=source.index(' for(const {n,el} of labels)');end=source.index(' renderer.render(',start)
source=source[:start]+source[end:]
source=source.replace("observer.disconnect();controls.dispose();disposeLines(lines);disposeLines(hierarchy);nodeMesh.dispose();sphere.dispose();nodeMaterial.dispose();", "observer.disconnect();view.dispose();")
source=source.replace("renderer.setPixelRatio(Math.min(devicePixelRatio,Number($('quality').value)));resize();", "view.quality(Number($('quality').value));")
path.write_text(source,encoding='utf-8')
