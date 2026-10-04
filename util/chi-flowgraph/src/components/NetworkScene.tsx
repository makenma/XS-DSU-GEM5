import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { Channel, ReplayState, Session, Step } from '../trace/types';
import type { SimulationClock, TimedTransfer, TransportTimeline } from '../trace/playback';

export const CHANNEL_COLORS: Record<Channel,string> = {REQ:'#5ce4ed',RSP:'#f3c16b',DAT:'#ac95ff',SNP:'#ff7caa'};
export const ROLE_COLORS = {'RN-F':'#5ce4ed','HN-F':'#ac95ff','SN-F':'#f3c16b'};
interface Props {
  session: Session; steps: Step[]; cursor: number; rootId: string;
  clock:SimulationClock;transports:TransportTimeline;raw:boolean;
  state: ReplayState; onPick: (id: string, tick: bigint)=>void; onNode: (id: string)=>void;
}
const label = (text:string,color:string,small=false) => {
  const canvas=document.createElement('canvas');canvas.width=256;canvas.height=96;
  const ctx=canvas.getContext('2d')!;
  ctx.font=`${small?'400':'600'} ${small?22:40}px "Segoe UI",sans-serif`;
  ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillStyle=color;ctx.fillText(text,128,48,250);
  const texture=new THREE.CanvasTexture(canvas);
  const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthWrite:false}));
  sprite.scale.set(small?3.1:3.6,small?.78:.9,1);
  return sprite;
};

export default function NetworkScene(props:Props) {
  const host=useRef<HTMLDivElement>(null);
  const latest=useRef(props); latest.current=props;
  const sceneApi=useRef<{reset:()=>void;focus:()=>void} | undefined>(undefined);
  const statsRef=useRef<HTMLSpanElement>(null);
  const densityRef=useRef<HTMLSpanElement>(null);
  const selectionRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const container=host.current!;
    let renderer:THREE.WebGLRenderer;
    try {renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,powerPreference:'high-performance'});}
    catch {container.textContent='WebGL 不可用；仍可使用下方时序图和消息详情检查轨迹。';return;}
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);
    const scene=new THREE.Scene();scene.fog=new THREE.FogExp2('#080e1a',.021);
    const camera=new THREE.PerspectiveCamera(42,1,.1,120);
    camera.position.set(15,15,19);
    const controls=new OrbitControls(camera,renderer.domElement);
    controls.enableDamping=true;controls.minDistance=8;controls.maxDistance=45;
    controls.maxPolarAngle=Math.PI*.48;controls.target.set(0,.4,0);
    scene.add(new THREE.AmbientLight('#a9c8ee',1.5));
    const light=new THREE.DirectionalLight('#d5e8ff',3);light.position.set(8,16,8);scene.add(light);
    const grid=new THREE.GridHelper(70,70,'#22334b','#131e30');grid.position.y=-.8;scene.add(grid);
    const positions=new Map<string,THREE.Vector3>();
    const fabric=props.session.manifest.fabric;
    const routerPositions=new Map<string,THREE.Vector3>();
    if(fabric) for(const router of fabric.routers) routerPositions.set(router.id,new THREE.Vector3((router.x-1)*8,-.15,(router.y-.5)*9));
    const meshPosition=(id:string,role:string)=>{
      const attachment=fabric?.attachments.find(a=>a.node===id);
      const r=attachment&&routerPositions.get(attachment.router);
      if(!r)return undefined;
      const pos=r.clone();pos.y=0;
      if(role==='RN-F'){pos.x+=Math.sign(pos.x)*2.7;pos.z+=Math.sign(pos.z)*2.5;}
      // Keep Home-node spurs diagonal and outside the router rows so they
      // cannot overlap the X/Y mesh links or look like a reversed mesh hop.
      if(role==='HN-F'){pos.x-=Math.sign(pos.x)*3;pos.z+=Math.sign(pos.z)*2.7;}
      if(role==='SN-F')pos.z+=Math.sign(pos.z)*3.4;
      return pos;
    };
    const pickables:THREE.Object3D[]=[];
    const rings=new Map<string,THREE.Mesh>();
    const nodeMeshes=new Map<string,THREE.Mesh>();
    const routerMeshes=new Map<string,THREE.Mesh>();
    const geometries:THREE.BufferGeometry[]=[];const materials:THREE.Material[]=[];
    const roles=['RN-F','HN-F','SN-F'] as const;
    for (const [stage,role] of roles.entries()) {
      const nodes=props.session.manifest.topology.filter(n=>n.role===role);
      const x=(stage-1)*8;
      const heading=label(role==='RN-F'?'REQUEST NODE':role==='HN-F'?'HOME NODE':'SUBORDINATE',ROLE_COLORS[role],true);
      heading.position.set(x,.1,-Math.max(5,nodes.length*1.4+1.6));if(!fabric)scene.add(heading);
      nodes.forEach((node,i)=>{
        const pos=meshPosition(node.id,node.role)??new THREE.Vector3(x,0,(i-(nodes.length-1)/2)*3.8);positions.set(node.id,pos);
        const group=new THREE.Group();group.position.copy(pos);scene.add(group);
        const baseGeo=new THREE.CylinderGeometry(1.42,1.7,.25,6);geometries.push(baseGeo);
        const baseMat=new THREE.MeshStandardMaterial({color:'#121d30',metalness:.5,roughness:.35});materials.push(baseMat);
        const base=new THREE.Mesh(baseGeo,baseMat);base.position.y=-.55;group.add(base);
        const bodyGeo=new THREE.BoxGeometry(1.72,1.4,1.72);geometries.push(bodyGeo);
        const mat=new THREE.MeshStandardMaterial({color:ROLE_COLORS[node.role],metalness:.4,roughness:.27,transparent:true,opacity:.23,emissive:ROLE_COLORS[node.role],emissiveIntensity:.16});materials.push(mat);
        const body=new THREE.Mesh(bodyGeo,mat);body.position.y=.35;body.userData={nodeId:node.id};group.add(body);pickables.push(body);nodeMeshes.set(node.id,body);
        const edges=new THREE.LineSegments(new THREE.EdgesGeometry(bodyGeo),new THREE.LineBasicMaterial({color:ROLE_COLORS[node.role],transparent:true,opacity:.75}));edges.position.copy(body.position);group.add(edges);
        const ringGeo=new THREE.TorusGeometry(1.2,.035,8,64);geometries.push(ringGeo);
        const ringMat=new THREE.MeshBasicMaterial({color:ROLE_COLORS[node.role],transparent:true,opacity:.65});materials.push(ringMat);
        const ring=new THREE.Mesh(ringGeo,ringMat);ring.rotation.x=-Math.PI/2;ring.position.y=-.35;group.add(ring);rings.set(node.id,ring);
        const name=label(node.label,'#e5edf9');name.position.y=1.75;group.add(name);
        const caption=label(`ID ${node.nodeId} · ${node.role}`,ROLE_COLORS[node.role],true);caption.position.y=-.85;group.add(caption);
        for(let k=0;k<3;k++) {
          const inner=new THREE.Mesh(new THREE.BoxGeometry(1.45,.1,1.3),new THREE.MeshBasicMaterial({color:ROLE_COLORS[node.role],transparent:true,opacity:.35}));
          inner.position.y=.05+k*.37;group.add(inner);
        }
      });
    }
    const link=(a:THREE.Vector3,b:THREE.Vector3)=>scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([a,b]),new THREE.LineBasicMaterial({color:'#476284',transparent:true,opacity:.7})));
    if(fabric){
      for(const [id,pos] of routerPositions){const box=new THREE.Mesh(new THREE.BoxGeometry(.9,.45,.9),new THREE.MeshStandardMaterial({color:'#496885',emissive:'#233851'}));box.position.copy(pos);scene.add(box);routerMeshes.set(id,box);const name=label(id,'#9cafca',true);name.position.copy(pos).add(new THREE.Vector3(0,1,0));scene.add(name);}
      for(const l of fabric.links)link(routerPositions.get(l.from)!,routerPositions.get(l.to)!);
      for(const a of fabric.attachments)if(positions.has(a.node))link(positions.get(a.node)!,routerPositions.get(a.router)!);
    }
    const fit=()=>{const extent=new THREE.Box3().setFromPoints([...positions.values(),...routerPositions.values()]);const size=extent.getSize(new THREE.Vector3());const center=extent.getCenter(new THREE.Vector3());const distance=Math.max(size.x/camera.aspect,size.z,12)/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov/2)))*1.4;controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(distance*.5,distance*.65,distance*.72));controls.maxDistance=distance*3;};
    const shapes:Record<Channel,THREE.BufferGeometry>={REQ:new THREE.ConeGeometry(.3,.7,4),RSP:new THREE.SphereGeometry(.27,10,8),DAT:new THREE.BoxGeometry(.46,.46,.46),SNP:new THREE.OctahedronGeometry(.36)};
    const tokenMeshes={} as Record<Channel,THREE.InstancedMesh>;
    const tokenIds:Record<Channel,string[]>={REQ:[],RSP:[],DAT:[],SNP:[]};
    for(const channel of Object.keys(shapes) as Channel[]){
      geometries.push(shapes[channel]);const mat=new THREE.MeshBasicMaterial({color:CHANNEL_COLORS[channel],depthTest:false,depthWrite:false});materials.push(mat);
      const mesh=new THREE.InstancedMesh(shapes[channel],mat,200);mesh.count=0;mesh.renderOrder=10;mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.userData.channel=channel;mesh.userData.stepIds=tokenIds[channel];
      scene.add(mesh);tokenMeshes[channel]=mesh;
    }
    // Screen-facing rings stay visible even when a packet crosses a node.
    const selectionHalo=new THREE.Group();selectionHalo.name='selected-flit-halo';selectionHalo.visible=false;
    const outerRing=new THREE.Mesh(new THREE.RingGeometry(.71,.79,48),new THREE.MeshBasicMaterial({color:'#ffffff',side:THREE.DoubleSide,depthTest:false,depthWrite:false,fog:false}));
    const innerRing=new THREE.Mesh(new THREE.RingGeometry(.59,.66,48),new THREE.MeshBasicMaterial({color:'#ffffff',side:THREE.DoubleSide,depthTest:false,depthWrite:false,fog:false}));
    outerRing.renderOrder=12;innerRing.renderOrder=12;selectionHalo.add(outerRing,innerRing);scene.add(selectionHalo);
    // A single pooled draw call for all active paths; rebuilding hundreds of
    // Line materials every frame stalls the GPU on dense concurrent captures.
    const trailPositions=new Float32Array(200*64*3),trailColors=new Float32Array(trailPositions.length);
    const trailGeometry=new THREE.BufferGeometry();
    trailGeometry.setAttribute('position',new THREE.BufferAttribute(trailPositions,3).setUsage(THREE.DynamicDrawUsage));
    trailGeometry.setAttribute('color',new THREE.BufferAttribute(trailColors,3).setUsage(THREE.DynamicDrawUsage));
    const trails=new THREE.LineSegments(trailGeometry,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.7}));trails.frustumCulled=false;scene.add(trails);
    let visibleKey='',frameCount=0,lastFps=performance.now();
    type Segment={start:bigint;end:bigint;curve:THREE.CurvePath<THREE.Vector3>};
    const paths=new Map<string,{segments:Segment[];points:THREE.Vector3[]}>();
    const raised=(v:THREE.Vector3)=>v.clone().add(new THREE.Vector3(0,.9,0));
    const pathFor=(transfer:TimedTransfer)=>{
      const saved=paths.get(transfer.id);if(saved)return saved;
      const source=positions.get(transfer.source),target=positions.get(transfer.target);if(!source||!target)return undefined;
      const start=raised(source),end=raised(target),segments:Segment[]=[],points=[start];
      let previous=start,at=transfer.start;
      const segment=(until:bigint,vertices:THREE.Vector3[])=>{
        const curve=new THREE.CurvePath<THREE.Vector3>();
        const verticesWithStart=[previous,...vertices];
        for(let i=1;i<verticesWithStart.length;i++)if(verticesWithStart[i-1].distanceToSquared(verticesWithStart[i])>1e-8)curve.add(new THREE.LineCurve3(verticesWithStart[i-1],verticesWithStart[i]));
        if(curve.curves.length)segments.push({start:at,end:until,curve});
        points.push(...vertices);previous=vertices.at(-1)??previous;at=until;
      };
      for(const hop of transfer.hops){const router=routerPositions.get(hop.from);if(router&&hop.tick>=at&&hop.tick<=transfer.end)segment(hop.tick,[raised(router)]);}
      const from=fabric?.attachments.find(a=>a.node===transfer.source)?.router,to=fabric?.attachments.find(a=>a.node===transfer.target)?.router;
      const via=to&&routerPositions.get(to);
      const vertices=via&&(transfer.hops.length||from===to)?[raised(via),end]:[end];
      segment(transfer.end,vertices);
      const result={segments,points};paths.set(transfer.id,result);return result;
    };
    const dummy=new THREE.Object3D();const ray=new THREE.Raycaster();
    type Pick={flitId:string;tick:bigint}|{nodeId:string};
    let renderedTick=props.clock.read();
    const visibleTokens:{id:string;position:THREE.Vector3}[]=[];
    const projected=new THREE.Vector3();
    const flitMeshes=Object.values(tokenMeshes);
    const pick=(e:PointerEvent):Pick|undefined=>{
      const rect=renderer.domElement.getBoundingClientRect();ray.setFromCamera(new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);
      // Packets render above nodes, so they must also take priority when picking.
      const hit=ray.intersectObjects(flitMeshes,false)[0];
      if(hit?.instanceId!==undefined){const id=tokenIds[hit.object.userData.channel as Channel][hit.instanceId];if(id)return {flitId:id,tick:renderedTick};}
      // A small CSS-pixel target makes tiny/far-away packets practical to click.
      let closest:string|undefined,distance=12*12;
      for(const token of visibleTokens){
        projected.copy(token.position).project(camera);if(projected.z<-1||projected.z>1)continue;
        const dx=rect.left+(projected.x+1)*rect.width/2-e.clientX,dy=rect.top+(1-projected.y)*rect.height/2-e.clientY;
        const squared=dx*dx+dy*dy;if(squared<distance){distance=squared;closest=token.id;}
      }
      if(closest)return {flitId:closest,tick:renderedTick};
      const node=ray.intersectObjects(pickables,false)[0]?.object.userData.nodeId;
      if(node)return {nodeId:node};
    };
    let press:{x:number;y:number;pointerId:number;target?:Pick;dragged:boolean}|undefined;
    const down=(e:PointerEvent)=>{if(e.button===0)press={x:e.clientX,y:e.clientY,pointerId:e.pointerId,target:pick(e),dragged:false};};
    const move=(e:PointerEvent)=>{
      if(press&&Math.hypot(e.clientX-press.x,e.clientY-press.y)>5)press.dragged=true;
      renderer.domElement.style.cursor=press?.dragged?'grabbing':pick(e)?'pointer':'grab';
    };
    const up=(e:PointerEvent)=>{
      const saved=press;press=undefined;
      if(!saved||saved.pointerId!==e.pointerId||saved.dragged||Math.hypot(e.clientX-saved.x,e.clientY-saved.y)>5)return;
      // Snapshot the identity AND displayed tick on press: the packet can move
      // or its instance slot can be reused before the mouse button is released.
      if(saved.target&&'flitId' in saved.target)latest.current.onPick(saved.target.flitId,saved.target.tick);
      else if(saved.target&&'nodeId' in saved.target)latest.current.onNode(saved.target.nodeId);
    };
    const cancel=()=>{press=undefined;renderer.domElement.style.cursor='grab';};
    renderer.domElement.style.cursor='grab';
    renderer.domElement.addEventListener('pointerdown',down);renderer.domElement.addEventListener('pointermove',move);renderer.domElement.addEventListener('pointerup',up);renderer.domElement.addEventListener('pointercancel',cancel);renderer.domElement.addEventListener('pointerleave',cancel);
    const resize=()=>{const w=container.clientWidth,h=container.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();};
    const observer=new ResizeObserver(resize);observer.observe(container);resize();fit();
    sceneApi.current={reset:fit,focus:()=>{
      const p=latest.current;const event=p.steps[p.cursor]?.event;
      if(event?.source&&positions.has(event.source)){const pos=positions.get(event.source)!;controls.target.copy(pos);camera.position.copy(pos).add(new THREE.Vector3(6,7,8));}
    }};
    let animation=0;
    const direction=new THREE.Vector3(0,1,0);
    const render=(time:number)=>{
      animation=requestAnimationFrame(render);const p=latest.current,tick=p.clock.read(time);
      const sample=p.transports.sample(tick),visible=sample.transfers;
      if(densityRef.current){densityRef.current.textContent=`${sample.total} 在途 flit · 显示 ${visible.length} / 200`;densityRef.current.dataset.flitCount=String(visible.length);}
      const selected=p.transports.byEvent.get(p.steps[p.cursor]?.id);
      const key=`${p.rootId}:${visible.map(t=>t.id).join(',')}`;
      if(key!==visibleKey){
        visibleKey=key;
        let vertex=0;
        for(const transfer of visible){
          const path=pathFor(transfer);if(!path)continue;
          const color=new THREE.Color(CHANNEL_COLORS[transfer.channel]).multiplyScalar(transfer.rootId===p.rootId?1:.6);
          for(let i=1;i<path.points.length&&vertex+2<=trailPositions.length/3;i++)for(const point of [path.points[i-1],path.points[i]]){
            point.toArray(trailPositions,vertex*3);color.toArray(trailColors,vertex*3);vertex++;
          }
        }
        trailGeometry.setDrawRange(0,vertex);trailGeometry.attributes.position.needsUpdate=true;trailGeometry.attributes.color.needsUpdate=true;
      }
      const counts:Record<Channel,number>={REQ:0,RSP:0,DAT:0,SNP:0};
      renderedTick=tick;visibleTokens.length=0;selectionHalo.visible=false;
      for(const channel of Object.keys(tokenIds) as Channel[])tokenIds[channel].length=0;
      for(const mesh of routerMeshes.values())(mesh.material as THREE.MeshStandardMaterial).emissiveIntensity=.35;
      const nearNodes=new Set<string>();
      for(const transfer of visible){
        const path=pathFor(transfer);if(!path?.segments.length)continue;
        const segment=path.segments.find(s=>tick<s.end)??path.segments.at(-1)!;
        const progress=segment.end>segment.start?Math.min(1,Math.max(0,Number(tick-segment.start)/Number(segment.end-segment.start))):1;
        const pos=segment.curve.getPoint(progress),tangent=segment.curve.getTangent(progress);
        dummy.position.copy(pos);dummy.scale.setScalar(transfer.id===selected?.id?2:1.5);dummy.quaternion.setFromUnitVectors(direction,tangent.normalize());dummy.updateMatrix();
        const channel=transfer.channel,instance=counts[channel]++;
        tokenIds[channel][instance]=p.raw?transfer.rawId:transfer.protocolId??transfer.rawId;
        tokenMeshes[channel].setMatrixAt(instance,dummy.matrix);
        visibleTokens.push({id:tokenIds[channel][instance],position:pos});
        if(transfer.id===selected?.id){selectionHalo.visible=true;selectionHalo.position.copy(pos);selectionHalo.userData.stepId=tokenIds[channel][instance];innerRing.material.color.set(CHANNEL_COLORS[channel]);}
        for(const [id,position] of routerPositions)if(pos.distanceToSquared(raised(position))<1.2){const material=routerMeshes.get(id)!.material as THREE.MeshStandardMaterial;material.emissive.set(CHANNEL_COLORS[channel]);material.emissiveIntensity=1.6;}
        for(const [id,position] of positions)if(pos.distanceToSquared(raised(position))<1.6)nearNodes.add(id);
      }
      for(const channel of Object.keys(tokenMeshes) as Channel[]){const mesh=tokenMeshes[channel];mesh.count=counts[channel];mesh.instanceMatrix.needsUpdate=true;mesh.computeBoundingSphere();}
      for(const [id,ring] of rings){const waiting=Object.keys(p.state.nodes[id]?.waits??{}).length>0;(ring.material as THREE.MeshBasicMaterial).color.set(waiting?'#ff846e':ROLE_COLORS[props.session.manifest.topology.find(n=>n.id===id)!.role]);}
      for(const [id,mesh] of nodeMeshes)(mesh.material as THREE.MeshStandardMaterial).emissiveIntensity=nearNodes.has(id)?.85:.16;
      controls.update();selectionHalo.quaternion.copy(camera.quaternion);renderer.render(scene,camera);
      if(selectionRef.current){
        selectionRef.current.hidden=!selectionHalo.visible;
        const text=selectionHalo.visible&&selected?`已选 ${selected.channel} · ${selected.opcode} · ${selected.source} → ${selected.target} ｜ ${p.steps[p.cursor]?.kind==='route'?'右侧查看转发记录':'右侧查看字段'}`:'';
        if(selectionRef.current.textContent!==text)selectionRef.current.textContent=text;
      }
      frameCount++;if(time-lastFps>1000){if(statsRef.current)statsRef.current.textContent=`${Math.round(frameCount*1000/(time-lastFps))} FPS`;frameCount=0;lastFps=time;}
    };animation=requestAnimationFrame(render);
    return ()=>{cancelAnimationFrame(animation);observer.disconnect();controls.dispose();renderer.domElement.removeEventListener('pointerdown',down);renderer.domElement.removeEventListener('pointermove',move);renderer.domElement.removeEventListener('pointerup',up);renderer.domElement.removeEventListener('pointercancel',cancel);renderer.domElement.removeEventListener('pointerleave',cancel);scene.traverse(o=>{if(o instanceof THREE.Sprite)o.material.map?.dispose();if(o instanceof THREE.Mesh||o instanceof THREE.Line||o instanceof THREE.Sprite){if('geometry' in o)o.geometry.dispose();const mats=Array.isArray(o.material)?o.material:[o.material];mats.forEach(m=>m.dispose());}});renderer.dispose();container.replaceChildren();};
  },[props.session]);
  return <div className="scene-wrapper"><div ref={host} className="scene-canvas" /><div className="scene-guide">拖动旋转 · 滚轮缩放 · 点击 flit 暂停查看字段 <span ref={densityRef}/></div><div ref={selectionRef} className="scene-selection" role="status" hidden/><div className="scene-actions"><button onClick={()=>sceneApi.current?.focus()} title="聚焦当前事务">聚焦</button><button onClick={()=>sceneApi.current?.reset()} title="重置视角">复位</button><span ref={statsRef}>— FPS</span></div></div>;
}
