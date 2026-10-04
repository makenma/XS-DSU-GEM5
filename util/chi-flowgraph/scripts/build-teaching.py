"""Publish focused teaching clips from real CPU runs. Never fabricate observed traffic.

Run `run-litmus.sh tutorial`, plus the 32-entry stress configuration documented in
docs/teaching.md, before rebuilding. Modeled hazard flows use a separate generator.
"""
import collections
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'public/demos'
OPS={'ReadShared':1,'ReadOnce':3,'ReadNoSnp':4,'ReadUnique':7,'CleanShared':8,
     'CleanInvalid':9,'MakeInvalid':10,'MakeUnique':12,'Evict':13,'WriteEvictFull':21,
     'WriteUniquePtl':24,'WriteUniqueFull':25,'WriteBackFull':27,'WriteNoSnpPtl':28,
     'WriteNoSnpFull':29,'StashOnceShared':34,'StashOnceUnique':35,
     'ReadOnceCleanInvalid':36,'ReadOnceMakeInvalid':37,'ReadNotSharedDirty':38}
NOTES={
 'ReadShared':'获取可共享的缓存副本；观察读数据和 CompAck。',
 'ReadOnce':'取得一次数据；请求者不分配该行。',
 'ReadNoSnp':'非一致性读取直达内存路径；不能用来发现其他 RN 的脏副本。',
 'ReadUnique':'取得数据与独占权限；冷缓存路径由 SN 供数。',
 'CleanShared':'本例目标不存在，无脏数据可清理，维护请求直接完成。',
 'CleanInvalid':'本例目标已无效，直接完成；脏副本场景见第三组用例。',
 'MakeInvalid':'允许丢弃脏数据的失效操作；此冷例没有有效副本。',
 'MakeUnique':'准备阶段 RN 已有完整干净行；仅取得权限，调用者保证整行覆盖。',
 'Evict':'准备阶段 RN 有真实干净行；逐出通知不携带 DAT。',
 'WriteEvictFull':'准备阶段 RN 为 UC；写数据来自真实缓存，观察 DBID 与 CopyBackWriteData。',
 'WriteUniquePtl':'内存预置 0xaa；偏移 8、8 字节访问，BE=0x55，仅选中字节被覆盖。',
 'WriteUniqueFull':'完整行一致性写入；先等 DBID，再传递非 CopyBack 数据。',
 'WriteBackFull':'准备阶段普通 store 生成 L1 脏数据；测试接口从实际缓存取得最新值。',
 'WriteNoSnpPtl':'非一致性部分写；HN→SN 必须保留有效字节掩码。',
 'WriteNoSnpFull':'非一致性整行写；观察 RN→HN 与 HN→SN 的独立握手。',
 'StashOnceShared':'原 RN 发一次提示；RN1 实际发起隐式 DataPull 并安装 SC。',
 'StashOnceUnique':'原 RN 发一次提示；RN1 实际发起隐式 DataPull 并安装 UC。',
 'ReadOnceCleanInvalid':'冷行返回数据且不分配；有脏副本时还需保留最新数据，见复杂用例。',
 'ReadOnceMakeInvalid':'冷行返回数据且不分配；允许丢弃的脏副本场景见复杂用例。',
 'ReadNotSharedDirty':'分配读取，但请求者最终不允许安装 SD。',
}

def read(path):return [json.loads(line) for line in (ROOT/path).read_text(encoding='utf-8').splitlines()]

class Capture:
 def __init__(self,path):
  self.path=path;self.records=read(path);self.manifest=self.records[0]
  assert not any(e['type']=='validation' and e['status']=='fail' for e in self.records),path
  self.observed=[e for e in self.records if e['type']=='observed']
  self.roots=collections.defaultdict(list)
  for e in self.observed:self.roots[e['rootId']].append(e)
  self.contexts={e['instructionId'] for e in self.observed if e['kind']=='instruction_context'}
  self.requests=[e for e in self.observed if e['kind']=='flit' and e.get('channel')=='REQ' and e.get('observation')=='TX' and e.get('source','').startswith('RN') and e.get('causeInstructionIds')]
 def lookup(self,root):return next((e for e in self.roots[root] if e['kind']=='lookup' and e['legId']==root+'/up'),None)
 def select(self,predicate):return next(e for e in self.requests if predicate(e,self.roots[e['rootId']]))

catalog=[];audit=[]
def clip(capture,ids,key,title,group,summary,prerequisites=None,outcome='',opcode=None):
 roots=set(ids);selected=[copy.deepcopy(e) for e in capture.records[1:] if e['type'] in ('observed','protocol') and e['rootId'] in roots]
 assert any(e['kind']=='complete' for e in selected), (key,'no completion')
 causes={identity for e in selected for identity in e.get('causeInstructionIds',[])+e.get('dataProducerInstructionIds',[])}
 identities={e['id'] for e in selected}
 for e in capture.observed:
  if e.get('instructionId') in causes and e['id'] not in identities:
   selected.append(copy.deepcopy(e));identities.add(e['id'])
 # Protocol dependencies can contain metadata outside the focused root. Keep the
 # referenced evidence, without copying unrelated transactions wholesale.
 by_id={e['id']:e for e in capture.records[1:] if e['type'] in ('observed','protocol')}
 pending=list(selected)
 while pending:
  e=pending.pop()
  for identity in e.get('sourceEventIds',[])+e.get('dependencies',[]):
   if identity in by_id and identity not in identities:
    extra=copy.deepcopy(by_id[identity]);selected.append(extra);pending.append(extra);identities.add(identity)
 observed=sorted((e for e in selected if e['type']=='observed'),key=lambda e:e['seq'])
 protocol=sorted((e for e in selected if e['type']=='protocol'),key=lambda e:e['seq'])
 manifest=copy.deepcopy(capture.manifest)
 manifest['title']=title;manifest['description']=summary;manifest['complete']=True
 manifest['source']['excerptOf']=capture.path
 # Only seed previously observed state. Unobserved L1/L2 contents stay unknown.
 request=next(e for e in observed if e['kind']=='flit' and e.get('channel')=='REQ' and e.get('source','').startswith('RN'))
 addresses={e.get('address') for e in selected};initial={}
 for e in capture.observed:
  if e['seq']>=request['seq']:break
  if e['kind']=='state' and e.get('address') in addresses:
   initial.setdefault(e['node'],{'lines':{}})['lines'][e['address']]=copy.deepcopy(e['state'])
 manifest['initialState']=initial
 used_sources={e['instruction'].get('sourceId') for e in observed if e['kind']=='instruction'}
 used_sources|={e.get('fields',{}).get('callSite',{}).get('sourceId') for e in observed if e['kind']=='instruction_context'}
 manifest['litmus']['threads']=[t for t in manifest['litmus']['threads'] if t['id'] in used_sources]
 manifest['litmus']['results']={'本用例':outcome or '实际 RN 完成记录已采集'}
 manifest['litmus']['name']=title
 lookup=capture.lookup(request['rootId']);condition=lookup['fields'] if lookup else None
 checkpoints=[]
 for kind,label in [('REQ','发出请求'),('lookup','HNF 查询'),('SNP','发出 snoop'),('DAT','数据传输'),('complete','事务完成')]:
  if kind=='complete':
   # A victim/memory child may finish well before the selected RN transaction.
   # The completion checkpoint must not retire the lesson at that child event.
   event=max((e for e in observed if e['kind']=='complete' and e['legId']==e['rootId']+'/up'),key=lambda e:int(e['tick']),default=None)
  elif kind=='lookup':
   event=next((e for e in observed if e['kind']=='lookup' and e['legId']==e['rootId']+'/up'),None)
  else:
   event=next((e for e in observed if (e.get('channel')==kind and e.get('observation')=='TX') or e['kind']==kind),None)
  if event:
   counterpart=next((p for p in protocol if p['kind']=='flit' and event['id'] in p.get('sourceEventIds',[])),None)
   checkpoints.append({'title':label,'eventId':(counterpart or event)['id']})
 manifest['teaching']={'id':key,'group':group,'evidence':'gem5','summary':summary,'opcode':opcode,
  'prerequisites':prerequisites or ['准备操作已在原始 CPU 运行中完成；这里只回放所选事务及其子事务。'],
  'outcome':outcome,'lookup':condition,'focusRoots':list(ids),'checkpoints':checkpoints,
  'sources':[{'label':'本地 Issue H · B4 一致性 / B5 流程','url':'/reference/issue-h.pdf#page=283'},
             {'label':'完整运行 '+Path(capture.path).parent.name,'url':'/'+capture.path.replace('public/','') if capture.path.startswith('public/') else '/reference/'+Path(capture.path).parent.name+'.chi.jsonl'}]}
 validations=[copy.deepcopy(e) for e in capture.records if e['type']=='validation' and e.get('eventIds') and set(e['eventIds'])<=identities]
 assert not any(v['status']=='fail' for v in validations),key
 rows=[manifest,*observed,*protocol,*validations]
 filename=key+'.chi.jsonl';(DEST/filename).write_text('\n'.join(json.dumps(r,ensure_ascii=False,separators=(',',':')) for r in rows)+'\n',encoding='utf-8')
 catalog.append({'id':key,'title':title,'file':filename,'description':summary,'group':group,'evidence':'gem5'})
 audit.append({'id':key,'source':capture.path,'roots':list(ids),'observed':len(observed),'lookup':condition,'opcode':opcode})

def main():
 for name in ['tutorial','hazards']:
  folder=ROOT/f'build/litmus-{name}'
  binary=ROOT/('build/chi-refine.exe' if sys.platform=='win32' else 'build/chi-refine')
  subprocess.run([str(binary),'--input',str(folder/'raw.jsonl'),'--output',str(folder/'session.chi.jsonl'),'--profile','h-extended-256'],check=True)
  reference=ROOT/'public/reference';reference.mkdir(parents=True,exist_ok=True)
  (reference/f'litmus-{name}.chi.jsonl').write_bytes((folder/'session.chi.jsonl').read_bytes())
 cold=Capture('build/litmus-tutorial/session.chi.jsonl');protocol=Capture('public/demos/litmus-protocol.chi.jsonl');stress=Capture('public/demos/litmus-stress.chi.jsonl');hazards=Capture('build/litmus-hazards/session.chi.jsonl')
 result=json.loads((ROOT/'build/litmus-tutorial/flowgraph-result.json').read_text())
 assert result['done']==[1]*4 and not any(result['errors'])
 assert result['registers'][0][16:19]==[0xaa22aa44aa66aa88,0x987654321,0xaa22aa44aa66aa88]
 for opcode,value in OPS.items():
  req=cold.select(lambda e,rows:e['opcode']==opcode and bool(set(e.get('causeInstructionIds',[]))&cold.contexts) and cold.lookup(e['rootId'])['fields']['slcHit'] is False)
  assert any(e['kind']=='complete' and e.get('node')==req['source'] for e in cold.roots[req['rootId']])
  clip(cold,[req['rootId']],f'teach-basic-{opcode}',f'{opcode} · 0x{value:02X}','basics',NOTES[opcode],
       ['一个 RN 主动发起一次目标请求；准备请求与启动流量已折叠。','HNF 实际 lookup：SLC miss；SF 是否命中按准备条件决定。',NOTES[opcode]],'目标请求在 RN 实际完成；上下游消息均来自 gem5。',opcode)
 for slc,sf in [(False,False),(False,True),(True,False),(True,True)]:
  capture=cold if (slc,sf) in [(False,False),(True,False)] else protocol
  op={(False,False):'ReadOnce',(False,True):'ReadNotSharedDirty',(True,False):'ReadOnce',(True,True):'ReadUnique'}[(slc,sf)]
  req=capture.select(lambda e,rows:e['opcode']==op and capture.lookup(e['rootId']) is not None and capture.lookup(e['rootId'])['fields']=={'slcHit':slc,'sfHit':sf} and (slc or sf is False or e['source']=='RN0'))
  summary={(False,False):'SLC 没有数据，SF 没有其他副本，观察 HN→SN 读取。',(False,True):'SLC 没有数据，但 SF 找到 owner；最新数据由 RN 的 snoop 响应提供。',(True,False):'SLC 已有数据，SF 没有 RN 副本；本例由 HN 直接供数。',(True,True):'SLC 可供数，但取得独占权限仍需按 SF 失效其他 RN 的副本。'}[(slc,sf)]
  clip(capture,[req['rootId']],f'teach-hnf-{int(slc)}{int(sf)}',f'SLC {"hit" if slc else "miss"} / SF {"hit" if sf else "miss"}','hnf',summary,outcome='命中组合来自 HNF lookup 观测；SLC 命中不等于可以跳过一致性检查。')
 def special(capture,req,key,title,summary,extra=None,outcome=''):
  clip(capture,[req['rootId'],*(extra or [])],'teach-hazard-'+key,title,'hazards',summary,outcome=outcome)
 req=stress.select(lambda e,rows:any(x.get('opcode')=='RetryAck' for x in rows))
 special(stress,req,'retry','RetryAck → P-Credit → 重发','请求被拒绝后不能直接重发；跟踪 RetryAck、PCrdGrant 和同一逻辑事务的不同 attempt。')
 wait=next(e for e in hazards.observed if e['kind']=='wait_start' and e.get('reason')=='same_address')
 req=next(e for e in hazards.requests if e['rootId']==wait['rootId'])
 # Match the HN retirement that actually releases this wait. An arbitrary
 # overlapping request can itself be queued behind the selected request.
 released=next(e for e in hazards.observed if e['kind']=='wait_end' and e.get('waitId')==wait['waitId'])
 owner=next(e for e in hazards.observed if e['kind']=='complete' and e['node']==wait['node'] and e.get('address')==wait.get('address') and e['tick']==released['tick'] and e['rootId']!=req['rootId'])
 blocker=next(e for e in hazards.requests if e['rootId']==owner['rootId'])
 special(hazards,req,'same-address','同地址竞争 · HNF 排队','两个 RN 对同一行竞争；第二个请求在 HNF 等待第一个事务释放该行。',[blocker['rootId']], '32 个 HNF 名额的真实四核运行；wait_start / wait_end 保留实际时间。')
 req=stress.select(lambda e,rows:any(x.get('opcode')=='WriteNoSnpFull' and x.get('observation')=='TX' and '/victim:' in x['legId'] for x in rows))
 special(stress,req,'victim','Dirty victim · 替换分支','新请求触发有限容量替换；观察受害行地址、snoop 和 HN→SN 写回，与新行请求分支区分。')
 req=cold.select(lambda e,rows:e['opcode']=='WriteUniquePtl')
 readback=cold.select(lambda e,rows:e['opcode']=='ReadOnce' and (int(e['address'],16)&~63)==(int(req['address'],16)&~63) and int(e['tick'])>int(req['tick']))
 special(cold,req,'partial','部分写 · BE=0 的字节保留','先初始化内存为 0xaa，再写入交替字节，最后由 CPU 读取验证。',[readback['rootId']],'CPU 实际读回 0xaa22aa44aa66aa88。')
 for op,key,title,value in [('ReadOnceCleanInvalid','clean-invalid','读取并清理失效 · 保留脏数据','0x1111'),('ReadOnceMakeInvalid','make-invalid','读取并失效 · 允许丢弃','0x2222')]:
  req=protocol.select(lambda e,rows:e['opcode']==op)
  later=protocol.select(lambda e,rows:e['opcode']=='ReadNoSnp' and e['address']==req['address'] and int(e['tick'])>int(req['tick']))
  special(protocol,req,key,title,'RN1 预先持有 dirty 最新值、内存仍是旧值；比较当前返回数据与后续 ReadNoSnp。',[later['rootId']],f'当前读取返回 {value}；后续内存读取返回 '+('0x1111。' if key=='clean-invalid' else '0，说明允许丢弃与清理写回不同。'))
 req=protocol.select(lambda e,rows:any(x['kind']=='stash_ignored' for x in rows))
 special(protocol,req,'stash-refuse','Stash hint 被拒绝','RN3 不接受 stash；观察不发 DataPull 时，原发起者仍可收到完成。',outcome='提示被拒绝是合法结果；不能伪造一次缓存安装。')
 req=protocol.select(lambda e,rows:e['opcode']=='StashOnceShared' and any(x.get('implicitDataPull') for x in rows))
 special(protocol,req,'stash-pull','Stash · Comp 与目标安装分离','原请求的 Comp 与 RN1 的 DataPull、CompAck、SC 安装是不同阶段。',outcome='目标缓存安装由 RN1 state 观测证明；不能仅凭原请求 Comp 推断安装完成。')
 original=[e for e in json.loads((DEST/'catalog.json').read_text(encoding='utf-8')) if not e['id'].startswith('teach-') or e.get('evidence')=='protocol-model']
 (DEST/'catalog.json').write_text(json.dumps(original+catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (ROOT/'build/qa/teaching-captures.json').write_text(json.dumps({'status':'passed','coldOpcodes':len(OPS),'tutorialRun':result,'actualCases':audit},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(f'Published {len(catalog)} actual CPU teaching clips; all {len(OPS)} basic opcodes have observed SLC misses.')

if __name__=='__main__':main()
