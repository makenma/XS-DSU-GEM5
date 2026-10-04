"""Four independent SE processes sharing physical pages, actual 3x2 CHI mesh.

Run from the pinned gem5 tree with --elf-dir <build/litmus>, --variant iriw.
No host-generated traffic: every request is a classic cache or CPU test-page request.
"""
import argparse
import json
import os
from pathlib import Path
import m5
from _m5.core import seedRandom
from m5.objects import *
from m5.util import addToPath

parser=argparse.ArgumentParser()
parser.add_argument('--elf-dir',required=True)
parser.add_argument('--variant',default='iriw')
parser.add_argument('--rotation',type=int,default=0)
parser.add_argument('--hn-entries',type=int,default=32)
parser.add_argument('--slc-lines',type=int,default=256)
parser.add_argument('--max-ticks',type=int,default=100_000_000)
args=parser.parse_args()
seedRandom(1)
gem5=Path(__file__).resolve().parents[3] if 'configs' in str(__file__) else Path(os.environ['CHI_GEM5_DIR'])
addToPath(str(gem5/'configs'))
from common.xiangshan import XiangshanCore
from common.Caches import L1_ICache,L1_DCache,L1ToL2Bus
elf=Path(args.elf_dir).resolve()
system=System(mem_mode='timing',cache_line_size=64,mem_ranges=[AddrRange('256MiB')])
system.voltage_domain=VoltageDomain()
system.clk_domain=SrcClockDomain(clock='1GHz',voltage_domain=system.voltage_domain)
system.num_cpus=4
system.xiangshan_system=True
system.enable_difftest=False
system.cpu=[XiangshanCore(cpu_id=i) for i in range(4)]
corners=[(0,0),(2,0),(0,1),(2,1)]
def nid(x,y,p=0): return (x<<7)|(y<<4)|(p<<2)
rn_ids=[nid(x,y) for x,y in corners]
hn_ids=[nid(x,y,1) for x,y in corners]
sn_ids=[nid(1,y,2) for y in range(2)]
system.rn=[Cache2ChiBridge(node_id=n,home_node_ids=hn_ids,hnf_hash_pa_bits=48,sink_hnf_txreq=False,extended_profile=True) for n in rn_ids]
system.hn=[ChiExtendedHome(node_id=n,sn_node_ids=sn_ids,sn_hash_masks=[4160],num_entries=args.hn_entries,slc_lines=args.slc_lines) for n in hn_ids]
system.sn=[Chi2ClassicMemBridge(node_id=n) for n in sn_ids]
system.routers=[ChiRouterRefModel(local_x=x,local_y=y) for y in range(2) for x in range(3)]
def router(x,y): return system.routers[y*3+x]
links=[]
for y in range(2):
    for x in range(3):
        if x<2:
            router(x,y).internal_ports[0]=router(x+1,y).internal_peer_ports[2]
            router(x+1,y).internal_ports[2]=router(x,y).internal_peer_ports[0]
            links.append({'from':f'R{x}{y}','to':f'R{x+1}{y}'})
        if y<1:
            router(x,y).internal_ports[3]=router(x,y+1).internal_peer_ports[1]
            router(x,y+1).internal_ports[1]=router(x,y).internal_peer_ports[3]
            links.append({'from':f'R{x}{y}','to':f'R{x}{y+1}'})
system.l2_wrappers=[L2CacheWrapper(num_slices=1,cache_size='256KiB',cache_assoc=8,prefetcher=NULL) for _ in range(4)]
system.l1bus=[L1ToL2Bus() for _ in range(4)]
system.l2bus=[CoherentXBar(width=64,frontend_latency=0,forward_latency=0,response_latency=0,header_latency=0,snoop_response_latency=0,point_of_unification=True) for _ in range(4)]
controls=[]
for i,(x,y) in enumerate(corners):
    cpu=system.cpu[i]
    cpu.branchPred=DecoupledBPUWithBTB(bpDBSwitches=[])
    cpu.store_prefetch_train=False
    cpu.enable_storeSet_train=False
    cpu.addPrivateSplitL1Caches(L1_ICache(size='32KiB',assoc=8,prefetcher=NULL),L1_DCache(size='32KiB',assoc=8,prefetcher=NULL))
    cpu.createInterruptController()
    cpu.connectAllPorts(system.l1bus[i].cpu_side_ports,system.l1bus[i].cpu_side_ports,system.l1bus[i].mem_side_ports)
    wrapper=system.l2_wrappers[i]
    system.l1bus[i].mem_side_ports=wrapper.cpu_side
    wrapper.slices=[L2CacheSlice()]
    sl=wrapper.slices[0]
    sl.inner_cache=ChiIntentCache(size='256KiB',assoc=8,mshrs=64,tgts_per_mshr=20,tag_latency=1,data_latency=2,response_latency=0,cache_level=2,writeback_clean=True,prefetcher=NULL,do_fast_writeline=False)
    wrapper.addCacheAccessor(sl.inner_cache);wrapper.addSliceAccessor(sl);sl.setCacheAccessor(sl.inner_cache)
    sl.cpu_side=wrapper.slice_cpuside_ports;sl.inner_cpu_port=sl.inner_cache.cpu_side
    sl.inner_cache.mem_side=sl.inner_mem_port;sl.mem_side=system.l2bus[i].cpu_side_ports
    system.rn[i].cache_side=system.l2bus[i].mem_side_ports
    control=ChiTestControl(base=0x40000000+i*4096,node_id=rn_ids[i],cache=sl.inner_cache,bridge=system.rn[i],accept_stash=i!=3)
    controls.append(control);control.pio=system.l2bus[i].mem_side_ports
    router(x,y).device_ports[0]=system.rn[i].chi_side
    router(x,y).local_ports[4]=system.hn[i].rxport
system.controls=controls
for i in range(2): router(1,i).local_ports[8]=system.sn[i].chi_side
system.membus=NoncoherentXBar(width=32,frontend_latency=1,forward_latency=1,response_latency=1,header_latency=1)
system.system_port=system.membus.cpu_side_ports
system.memory=[SimpleMemory(range=AddrRange(0,size='256MiB',masks=[4160],intlvMatch=i),latency='20ns') for i in range(2)]
for i in range(2):
    system.sn[i].mem_side=system.membus.cpu_side_ports
    system.membus.mem_side_ports=system.memory[i].port
for r in system.rn: r.mem_side=system.membus.cpu_side_ports
binaries=[str(elf/f'{args.variant}-{i}.elf') for i in range(4)]
system.workload=SEWorkload.init_compatible(binaries[0])
for i,cpu in enumerate(system.cpu):
    process=Process(pid=100+i,executable=binaries[i],cmd=[binaries[i]])
    cpu.workload=process;cpu.createThreads()
root=Root(full_system=False,system=system)
topology=[{'id':f'{prefix}{i}','label':f'{prefix}{i}','nodeId':n,'role':role} for prefix,ids,role in [('RN',rn_ids,'RN-F'),('HN',hn_ids,'HN-F'),('SN',sn_ids,'SN-F')] for i,n in enumerate(ids)]
attachments=[{'node':f'{prefix}{i}','router':f'R{x}{y}'} for prefix in ['RN','HN'] for i,(x,y) in enumerate(corners)]
attachments += [{'node':f'SN{i}','router':f'R1{i}'} for i in range(2)]
litmus=json.loads((elf/f'{args.variant}-sources.json').read_text())
litmus['toolchain']=json.loads((elf/'toolchain.json').read_text())
litmus['rotation']=args.rotation
manifest={'type':'manifest','schemaVersion':'1.1','title':{'iriw':'四核 IRIW · 3×2 mesh','protocol':'CPU 协议测试 · 19 类 REQ','stress':'四核重试与 dirty victim · 3×2 mesh','tutorial':'单 RN 教学 · 20 类 REQ 冷 SLC'}[args.variant],
 'description':'实际 XiangshanCore / 私有 classic L2，4 RN-F / 4 HN-F / 2 SN-F；两个 SAM 均来自仿真配置。',
 'source':{'kind':'gem5_run','gem5Commit':'65f3241a6cd486dffddaca251536f5c691af9a1f','chironCommit':'55cd1e1d92142960af2b57c686fc9f35ce7286be','generator':'native/gem5/litmus.py'},
 'protocol':'ARM IHI 0050 Issue H','profile':'h-extended-256','lineBytes':64,'dataWidth':256,'topology':topology,'complete':False,'initialState':{},
 'clock':{'tickHz':'1000000000000'},'litmus':litmus,
 'sam':{'description':'RN→HN: CMN SCG XOR (PA48)；HN→SN: PA[6] XOR PA[12]。','homeNodeIds':[f'HN{i}' for i in range(4)]},
 'fabric':{'routers':[{'id':f'R{x}{y}','x':x,'y':y,'object':f'system.routers{y*3+x}'} for y in range(2) for x in range(3)],'links':links,'attachments':attachments},
 'endpointPorts':{**{f'system.rn{i}.chi_side':n for i,n in enumerate(rn_ids)},**{f'system.hn{i}.rxport':n for i,n in enumerate(hn_ids)},**{f'system.sn{i}.chi_side':n for i,n in enumerate(sn_ids)}}}
out=Path(m5.options.outdir);out.mkdir(parents=True,exist_ok=True)
settings=out/'flowgraph-config.json';settings.write_text(json.dumps({'manifest':manifest},ensure_ascii=False));os.environ['CHI_FLOWGRAPH_CONFIG']=str(settings)
m5.instantiate()
if args.rotation: system.workload.allocPhysPages(args.rotation,0)
shared=system.workload.allocPhysPages(4,0)
for i,cpu in enumerate(system.cpu):
    cpu.workload[0].map(0x100000000,shared,16384,cacheable=True)
    cpu.workload[0].map(0x200000000,0x40000000+i*4096,4096,cacheable=False)
    if args.variant!='iriw': system.controls[i].setSharedBase(shared)
event=None
while m5.curTick()<args.max_ticks:
    event=m5.simulate(min(1000000,args.max_ticks-m5.curTick()))
    if all(c.resultWord(2048)==1 for c in system.controls): break
result={'tick':str(m5.curTick()),'cause':event.getCause(),'done':[c.resultWord(2048) for c in system.controls],
        'registers':[[c.resultWord(1024+j*8) for j in range(32)] for c in system.controls],'sharedPhysical':hex(shared),
        'errors':[c.resultWord(272) for c in system.controls]}
(out/'flowgraph-result.json').write_text(json.dumps(result))
m5.stats.dump();print('FLOWGRAPH_LITMUS',json.dumps(result))
if result['done']!=[1,1,1,1] or any(result['errors']): raise RuntimeError('CPU litmus/test did not complete successfully')
