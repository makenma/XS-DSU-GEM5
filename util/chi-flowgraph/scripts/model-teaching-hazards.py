"""Explicit protocol illustrations, NOT gem5 captures or executable CPU results.

The three copyback/snoop races follow Issue H B4.11.1 and B5.6.1/B5.6.3.
Timing is illustrative. All messages, instructions, fields, and payloads carry
modeled provenance; these cases must never count towards CPU coverage.
"""
import copy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];DEST=ROOT/'public/demos'
BASE=json.loads((DEST/'litmus-iriw.chi.jsonl').read_text(encoding='utf-8').splitlines()[0])
DATA='0900000000000000'+'00'*56
OP={'ReadShared':1,'ReadOnce':3,'ReadUnique':7,'WriteBackFull':27,'WriteNoSnpFull':29,
    'SnpShared':1,'SnpOnce':3,'SnpUnique':7,'SnpRespData':1,'CopyBackWriteData':2,
    'NonCopyBackWriteData':3,'CompData':4,'CompAck':2,'CompDBIDResp':5,'DBIDResp':6,'Comp':4}

class Flow:
 def __init__(self,read):
  self.read=read;self.observed=[];self.protocol=[];self.counter=0;self.message=0;self.order=0
  self.rroot='teaching:read';self.wroot='teaching:writeback';self.rid='teaching:cpu0:read';self.wid='teaching:cpu1:writeback';self.producer='teaching:cpu1:prepare'
  self.address='0x1000';self.attach={a['node']:a['router'] for a in BASE['fabric']['attachments']}
 def event(self,tick,kind,root,**kw):
  self.counter+=1;e={'type':'observed','id':f't:{self.counter}','seq':self.counter,'tick':str(tick),'kind':kind,'rootId':root,'legId':root+'/up','address':self.address,'origin':'modeled',**kw};self.observed.append(e);return e
 def instruction(self,tick,hart,line,identity,root):
  self.event(tick,'instruction',root,instructionId=identity,instruction={'id':identity,'hartId':hart,'iteration':0,'dynamicSeq':str(line),'pc':'0','sourceId':f'thread-{hart}','line':line,'origin':'modeled'})
 def msg(self,tick,source,target,channel,opcode,root,description='',fields=None,payload=None,be=None,leg=None):
  self.message+=1;mid=f'teaching-message:{self.message}';leg=leg or root+'/up'
  causes=[self.rid if root==self.rroot else self.wid]
  f={'opcode':OP[opcode],**(fields or {})}
  common={'messageId':mid,'legId':leg,'source':source,'target':target,'channel':channel,'opcode':opcode,'fields':f,'description':description,'causeInstructionIds':causes}
  if payload is not None:common.update(payload=payload,validMask='ff'*(len(payload)//2),byteEnable=be or 'ff'*(len(payload)//2),payloadOrigin='modeled zero data with BE=0' if be=='00'*(len(payload)//2) else 'modeled teaching values',dataProducerInstructionIds=[] if be=='00'*(len(payload)//2) else [self.producer])
  tx=self.event(tick,'flit',root,observation='TX',**common)
  a=self.attach[source];b=self.attach[target];x,y=int(a[1]),int(a[2]);dx,dy=int(b[1]),int(b[2]);hops=[]
  while x!=dx or y!=dy:
   nx=x+(1 if x<dx else -1) if x!=dx else x;ny=y if x!=dx else y+(1 if y<dy else -1)
   hops.append((f'R{x}{y}',f'R{nx}{ny}'));x,y=nx,ny
  for i,(a,b) in enumerate(hops):self.event(tick+1000+i*2000,'route',root,messageId=mid,legId=leg,fields={'fromRouter':a,'toRouter':b},causeInstructionIds=causes)
  end=tick+2000+2000*len(hops);rx=self.event(end,'flit',root,observation='RX',**common)
  self.order+=1
  p={'type':'protocol','id':f'tp:{self.order}','seq':self.order,'kind':'flit','rootId':root,'legId':leg,'source':source,'target':target,'channel':channel,'opcode':opcode,'address':self.address,
     'fields':{k:{'value':v,'origin':'modeled'} for k,v in f.items()},'messageId':mid,'sourceEventIds':[tx['id'],rx['id']],'dependencies':[],'anchorTick':str(tick),'anchorSeq':tx['seq'],'substep':0,'origin':'modeled','rule':'B4.11.1','description':description,'causeInstructionIds':causes}
  for key in ['payload','validMask','byteEnable','payloadOrigin','dataProducerInstructionIds']:
   if key in common:p[key]=common[key]
  self.protocol.append(p);return end,p['id']
 def data(self,tick,source,target,opcode,root,resp,state,description='',empty=False,txnid=1,dbid=1,leg=None):
  ids=[];end=0
  for beat in range(2):
   end,identity=self.msg(tick+beat*1000,source,target,'DAT',opcode,root,description,{'Resp':resp,'CacheState':state,'PassDirty':bool(resp&4),'TxnID':txnid,'DBID':dbid,'DataID':beat*2,'BE':'0x00000000' if empty else '0xffffffff'},'00'*32 if empty else DATA[beat*64:(beat+1)*64],'00'*32 if empty else 'ff'*32,leg)
   ids.append(identity)
  return end,ids
 def arrival(self,identity):
  packet=next(p for p in self.protocol if p['id']==identity)
  return packet['sourceEventIds'][-1]
 def memory_write(self,tick,root):
  leg=root+'/memory';end,_=self.msg(tick,'HN0','SN0','REQ','WriteNoSnpFull',root,'将最新值写到内存。',{'TxnID':40,'ExpCompAck':0},leg=leg)
  end,_=self.msg(end+1000,'SN0','HN0','RSP','DBIDResp',root,fields={'TxnID':40,'DBID':80},leg=leg)
  end,_=self.data(end+1000,'HN0','SN0','NonCopyBackWriteData',root,0,'I',txnid=80,leg=leg)
  end,_=self.msg(end+1000,'SN0','HN0','RSP','Comp',root,fields={'TxnID':40},leg=leg)
  return end
 def build(self):
  read=self.read;snoop={'ReadUnique':'SnpUnique','ReadShared':'SnpShared','ReadOnce':'SnpOnce'}[read]
  self.instruction(0,1,2,self.producer,self.wroot);self.instruction(500,0,3,self.rid,self.rroot);self.instruction(500,1,4,self.wid,self.wroot)
  read_rx,read_id=self.msg(1000,'RN0','HN0','REQ',read,self.rroot,'RN0 的请求先到达 HN0。',{'TxnID':10,'ExpCompAck':1})
  wb_rx,wb_id=self.msg(1000,'RN1','HN0','REQ','WriteBackFull',self.wroot,'并行发出 WriteBackFull；HN0 暂不能处理它。',{'TxnID':20,'ExpCompAck':0})
  wait=self.event(wb_rx,'wait_start',self.wroot,node='HN0',waitId='copyback-hazard',reason='same_address',blocker=self.rroot,causeInstructionIds=[self.wid])
  snoop_rx,snoop_id=self.msg(read_rx+2000,'HN0','RN1','SNP',snoop,self.rroot,'RN1 尚未收到 CopyBack 的 CompDBIDResp，仍必须及时响应 snoop。',{'TxnID':30})
  state={'ReadUnique':'I','ReadShared':'SC','ReadOnce':'UD'}[read];resp={'ReadUnique':4,'ReadShared':5,'ReadOnce':2}[read]
  data_rx,snp_ids=self.data(snoop_rx+2000,'RN1','HN0','SnpRespData',self.rroot,resp,state,'先交付最新值 9；RN1 状态随 snoop 变为 '+state+'。',txnid=30)
  self.event(snoop_rx+2000,'state',self.rroot,node='RN1',state={'state':state,'dirty':state=='UD'},causeInstructionIds=[self.rid])
  target_state={'ReadUnique':'UD','ReadShared':'SC','ReadOnce':'I'}[read]
  data_end,_=self.data(data_rx+2000,'HN0','RN0','CompData',self.rroot,{'ReadUnique':6,'ReadShared':1,'ReadOnce':0}[read],target_state,'读事务取得最新值 9。',txnid=10,dbid=50)
  self.event(data_end,'state',self.rroot,node='RN0',state={'state':target_state,'dirty':target_state=='UD'},causeInstructionIds=[self.rid])
  ack_end,_=self.msg(data_end+2000,'RN0','HN0','RSP','CompAck',self.rroot,'CompAck 使用读数据返回的 DBID=50。',{'TxnID':50})
  # Shared passes dirty responsibility to HN; this example cleans it to memory
  # while completing the shared read, as illustrated by Issue H Figure B5.28.
  release=max(ack_end,self.memory_write(data_rx+2000,self.rroot) if read=='ReadShared' else 0)
  home={'state':'I','sfState':'EN','owner':'RN0','sharers':['RN0'],'dirty':False} if read=='ReadUnique' else {'state':'SN','sfState':'SN','sharers':['RN0','RN1'],'dirty':False,'data':DATA,'validMask':'ff'*64} if read=='ReadShared' else {'state':'I','sfState':'EN','owner':'RN1','sharers':['RN1'],'dirty':False}
  self.event(release,'state',self.rroot,node='HN0',state=home,causeInstructionIds=[self.rid])
  self.event(release,'complete',self.rroot,node='HN0',causeInstructionIds=[self.rid]);self.event(release,'complete',self.rroot,node='RN0',causeInstructionIds=[self.rid])
  self.event(release,'wait_end',self.wroot,node='HN0',waitId='copyback-hazard')
  dbid_end,dbid_id=self.msg(release+2000,'HN0','RN1','RSP','CompDBIDResp',self.wroot,'解除同地址阻塞，分配写回 DBID=60。',{'TxnID':20,'DBID':60})
  empty=read=='ReadUnique';wb_resp=0 if empty else 1 if read=='ReadShared' else 6
  explanation='snoop 后为 I：仍发送 CopyBackWriteData_I，所有 BE 和数据均为 0。' if empty else '使用 snoop 后的 SC 状态，不能再次声称转移脏数据责任。' if read=='ReadShared' else 'SnpOnce 保留 UD；写回仍发送完整有效脏数据，随后更新内存。'
  wb_end,wb_ids=self.data(dbid_end+2000,'RN1','HN0','CopyBackWriteData',self.wroot,wb_resp,state,explanation,empty,txnid=60)
  self.event(wb_end,'state',self.wroot,node='RN1',state={'state':'I','dirty':False},causeInstructionIds=[self.wid])
  self.event(wb_end,'complete',self.wroot,node='RN1',causeInstructionIds=[self.wid])
  final=self.memory_write(wb_end+2000,self.wroot) if read=='ReadOnce' else wb_end
  final_home=home if read=='ReadUnique' else {'state':'SN','sfState':'SN' if read=='ReadShared' else 'I','sharers':['RN0'] if read=='ReadShared' else [],'dirty':False,'data':DATA,'validMask':'ff'*64}
  self.event(final,'state',self.wroot,node='HN0',state=final_home,causeInstructionIds=[self.wid])
  self.event(final,'complete',self.wroot,node='HN0',causeInstructionIds=[self.wid])
  self.observed.sort(key=lambda e:(int(e['tick']),e['seq']))
  for i,e in enumerate(self.observed):e['seq']=i
  by_id={e['id']:e for e in self.observed}
  for p in self.protocol:p['anchorSeq']=by_id[p['sourceEventIds'][0]]['seq']
  key={'ReadUnique':'early-unique','ReadShared':'early-shared','ReadOnce':'early-once'}[read]
  title=f'early snoop · {read} × WriteBackFull'
  manifest=copy.deepcopy(BASE);manifest.update(title=title,description=explanation,complete=True,source={'kind':'educational_model','generator':'scripts/model-teaching-hazards.py'},initialState={'RN1':{'lines':{self.address:{'state':'UD','dirty':True,'data':DATA,'validMask':'ff'*64}}},'RN0':{'lines':{self.address:{'state':'I'}}},'HN0':{'lines':{self.address:{'state':'I','sfState':'EN','owner':'RN1','sharers':['RN1']}}}})
  manifest['sam']={'description':'教学预设地址指向 HN0 / SN0；本例不推断实测 hash。'}
  manifest['clock']={'tickHz':'1000000000000','source':'illustrative teaching timing, not measured'}
  manifest['litmus']={'name':title,'threads':[{'id':'thread-0','hartId':0,'node':'RN0','title':'P0 · 读方','language':'c-model','code':f'// 初始：RN0 为 I，内存 x = 0\n// 两核并发；本请求先到 HN0\nuint64_t v = chi_read({read}, x);\nassert(v == 9);\n'},{'id':'thread-1','hartId':1,'node':'RN1','title':'P1 · 写回方','language':'c-model','code':'// 准备阶段，RN1 持有 UD\nWRITE_ONCE(x, 9);\n// 写回等待期间仍能接收 snoop\nchi_copyback_async(WriteBackFull, x);\nwait_completion();\n'}], 'initial':{'RN1':'UD, x=9','RN0':'I','memory':'x=0'},'results':{'性质':'协议规定的示例终态，非 CPU 运行结果','RN0':target_state,'RN1':'I','memory':0 if read=='ReadUnique' else 9}}
  manifest['teaching']={'id':'teach-hazard-'+key,'group':'hazards','evidence':'protocol-model','summary':explanation,'prerequisites':['协议推演，不是 gem5 实测；C 为示意代码，时间间隔仅用于教学。','HN0 先接受读；RN1 的 WriteBackFull 尚未收到 CompDBIDResp。','初始 RN1=UD、最新 x=9、内存 x=0。'],'outcome':explanation,'focusRoots':[self.rroot,self.wroot],'checkpoints':[{'title':'两核同时发出','eventId':read_id},{'title':'写回等待','eventId':wait['id']},{'title':'snoop 到写回方','eventId':self.arrival(snoop_id)},{'title':'最新数据先走 snoop','eventId':snp_ids[0]},{'title':'写回获 DBID','eventId':self.arrival(dbid_id)},{'title':'最后到底写什么','eventId':wb_ids[0]}],'sources':[{'label':'Issue H · B4.11.1（PDF p.283）','url':'/reference/issue-h.pdf#page=283'},{'label':'Issue H · B5.6 hazard 图（PDF p.313–318）','url':'/reference/issue-h.pdf#page=313'},{'label':'香山 MSHR：嵌套 snoop','url':'https://docs.xiangshan.cc/projects/design/zh-cn/kunminghu-v3/cache/l2cache/MSHR/'}]}
  filename='teach-hazard-'+key+'.chi.jsonl';(DEST/filename).write_text('\n'.join(json.dumps(e,ensure_ascii=False,separators=(',',':')) for e in [manifest,*self.observed,*self.protocol])+'\n',encoding='utf-8')
  code_dir=ROOT/'examples/teaching';code_dir.mkdir(parents=True,exist_ok=True)
  (code_dir/(key+'.c')).write_text('// Protocol teaching pseudocode; not a compiled CPU test.\n\n'+'\n'.join('// '+t['title']+'\n'+t['code'] for t in manifest['litmus']['threads']),encoding='utf-8')
  return {'id':manifest['teaching']['id'],'title':title,'file':filename,'description':explanation,'group':'hazards','evidence':'protocol-model'}

catalog=json.loads((DEST/'catalog.json').read_text(encoding='utf-8'))
new=[Flow(read).build() for read in ['ReadUnique','ReadShared','ReadOnce']]
catalog=[e for e in catalog if e['id'] not in {n['id'] for n in new}]+new
(DEST/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Published 3 explicitly modeled CopyBack/Snoop hazards (not CPU coverage).')
