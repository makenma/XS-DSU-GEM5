"""Executable protocol regression fixtures. These are tests, not gem5 demos."""
import copy
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
BINARY=pathlib.Path(os.environ.get('CHI_REFINE_BINARY',ROOT/'build'/('chi-refine.exe' if os.name=='nt' else 'chi-refine')))
MANIFEST=json.loads((ROOT/'examples/coarse/miss-hit.coarse.jsonl').read_text(encoding='utf-8').splitlines()[0])
PAYLOAD=bytes(range(64)).hex()

def event(opcode,channel,source='RN0',target='HN0',leg='r/up',attempt='0',**fields):
    return dict(type='observed',kind='flit',rootId=leg.split('/')[0],legId=leg,attemptId=attempt,source=source,target=target,
                channel=channel,opcode=opcode,address='0x80004040',fields=fields)

def req(**kwargs):return event('ReadNoSnp','REQ',sizeBytes=64,allowRetry=1,order=0,**kwargs)
def dat(offset,**kwargs):
    e=event('CompData','DAT',source='HN0',target='RN0',beatOffset=offset,resp=1,**kwargs)
    e.update(payload=PAYLOAD[offset*2:(offset+32)*2],validMask='ff'*32,byteEnable='ff'*32,payloadOrigin='validation_fixture')
    return e

def refine(events,complete=True,options=()):
    manifest=copy.deepcopy(MANIFEST);manifest['source']['kind']='validation_fixture';manifest['complete']=complete
    with tempfile.TemporaryDirectory() as folder:
        folder=pathlib.Path(folder);source=folder/'input.jsonl';out=folder/'output.jsonl'
        records=[manifest]
        for i,e in enumerate(copy.deepcopy(events),1):e.update(id=f'o:{i}',seq=i,tick=str(9007199254740993+i));records.append(e)
        source.write_text('\n'.join(map(json.dumps,records))+'\n',encoding='utf-8')
        result=subprocess.run([str(BINARY),'--input',str(source),'--output',str(out),*options],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stderr)
        return [json.loads(line) for line in out.read_text(encoding='utf-8').splitlines()]

def failures(records):return [e for e in records if e['type']=='validation' and e['status']=='fail']
def flits(records,op):return [e for e in records if e['type']=='protocol' and e['kind']=='flit' and e['opcode']==op]

class RefinerTests(unittest.TestCase):
    def test_extended_dataless_opcodes_complete_without_invented_data(self):
        for op,code in [('CleanShared',8),('CleanInvalid',9),('MakeInvalid',10),('MakeUnique',12),('Evict',13),('StashOnceShared',0x22),('StashOnceUnique',0x23)]:
            with self.subTest(op):
                request=event(op,'REQ',sizeBytes=64,allowRetry=1,expCompAck=1 if op=='MakeUnique' else 0,stashNid=0,memattr=5 if op=='Evict' else 13)
                events=[request,event('Comp','RSP',source='HN0',target='RN0',resp=2 if op=='MakeUnique' else 0)]
                if op=='MakeUnique':events.append(event('CompAck','RSP'))
                result=refine(events,options=('--profile','h-extended-256'))
                self.assertFalse(failures(result));self.assertEqual(flits(result,op)[0]['fields']['Opcode']['value'],code)
                self.assertFalse(flits(result,'CompData'))

    def test_extended_small_access_uses_one_data_flit_and_preserves_alignment(self):
        request=req();request['address']='0x80004048';request['fields']['sizeBytes']=8
        data=dat(0);data['address']=request['address'];data.update(payload='123456789abcdef0',validMask='ff'*8,byteEnable='ff'*8)
        result=refine([request,data],options=('--profile','h-extended-256'))
        self.assertFalse(failures(result));wire=flits(result,'CompData');self.assertEqual(len(wire),1)
        self.assertEqual(wire[0]['payload'][16:32],data['payload']);self.assertEqual(wire[0]['validMask'],'00'*8+'ff'*8+'00'*16)

    def test_extended_half_line_at_offset_32_uses_dataid_2(self):
        request=req();request['address']='0x80004060';request['fields']['sizeBytes']=32
        data=dat(0);data['address']=request['address']
        result=refine([request,data],options=('--profile','h-extended-256'))
        self.assertFalse(failures(result));wire=flits(result,'CompData');self.assertEqual(len(wire),1)
        self.assertEqual(wire[0]['fields']['DataID']['value'],2)
        self.assertEqual(wire[0]['payload'],data['payload'])

    def test_extended_retry_retains_id_while_another_request_is_in_flight(self):
        rejected=event('RetryAck','RSP',source='HN0',target='RN0',pcrdtype=7)
        credit=event('PCrdGrant','RSP',source='HN0',target='RN0',leg='credit',pcrdtype=7)
        resend=req(attempt='1');resend['fields'].update(allowRetry=0,pcrdtype=7)
        events=[req(),rejected,req(leg='other/up'),credit,resend,dat(0,leg='other/up'),dat(32,leg='other/up'),dat(0,attempt='1'),dat(32,attempt='1')]
        result=refine(events,options=('--profile','h-extended-256'))
        self.assertFalse(failures(result));requests=flits(result,'ReadNoSnp')
        self.assertNotEqual(requests[0]['fields']['TxnID']['value'],requests[1]['fields']['TxnID']['value'])
        self.assertEqual(requests[0]['fields']['TxnID']['value'],requests[2]['fields']['TxnID']['value'])

    def test_route_observation_does_not_duplicate_flit_and_merge_keeps_both_causes(self):
        request=req();request.update(messageId='m:1',causeInstructionIds=['cpu:0:0:2'])
        route=dict(type='observed',kind='route',rootId='r',legId='r/up',messageId='m:1',fields={'fromRouter':'R00','toRouter':'R10'})
        merged=dict(type='observed',kind='cause_add',rootId='r',legId='r/up',causeInstructionIds=['cpu:0:0:2','cpu:0:0:4'])
        result=refine([request,route,merged,dat(0),dat(32)],options=('--profile','h-extended-256'))
        self.assertFalse(failures(result));self.assertEqual(len(flits(result,'ReadNoSnp')),1)
        self.assertEqual(set(flits(result,'CompData')[0]['causeInstructionIds']),{'cpu:0:0:2','cpu:0:0:4'})

    def test_stash_data_pull_has_no_synthetic_req_and_completes_separately(self):
        parent='r/up';snoop=parent+'/stash';pull=snoop+'/data-pull'
        request=event('StashOnceShared','REQ',sizeBytes=64,allowRetry=1,expCompAck=0,stashNid=1)
        snp=event('SnpStashShared','SNP',source='HN0',target='RN1',leg=snoop,sizeBytes=64);snp['parentLegId']=parent
        alloc=dict(type='observed',kind='allocate',rootId='r',legId=pull,parentLegId=snoop,node='RN1',opcode='ReadNotSharedDirty',implicitDataPull=True)
        rsp=event('SnpResp','RSP',source='RN1',target='HN0',leg=snoop,dataPull=1,resp=8,pullLegId=pull,dbid=9)
        data=[dat(offset) for offset in (0,32)]
        for d in data:d.update(legId=pull,parentLegId=snoop,target='RN1')
        result=refine([request,snp,alloc,rsp,event('Comp','RSP',source='HN0',target='RN0'),*data,event('CompAck','RSP',source='RN1',target='HN0',leg=pull)],options=('--profile','h-extended-256'))
        self.assertFalse(failures(result));self.assertFalse(flits(result,'ReadNotSharedDirty'))
        self.assertEqual(flits(result,'SnpResp')[0]['fields']['DataPull']['value'],1)
        self.assertEqual(len(flits(result,'CompData')),2)

    def test_real_component_examples(self):
        for path in (ROOT/'public/demos').glob('*.chi.jsonl'):
            with self.subTest(path.name):
                records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                self.assertFalse(failures(records))
                self.assertFalse([e for e in records if e['type']=='validation' and e['status']=='unsupported'])
                self.assertTrue([e for e in records if e['type']=='protocol'])

    def test_out_of_order_and_large_tick(self):
        records=refine([req(),dat(32),dat(0)])
        self.assertFalse(failures(records));data=flits(records,'CompData')
        self.assertEqual([p['fields']['DataID']['value'] for p in data],[2,0])
        self.assertEqual(data[0]['anchorTick'],'9007199254740995')

    def test_write_uses_dbid_not_request_id(self):
        request=event('WriteNoSnpFull','REQ',source='HN0',target='SN0',sizeBytes=64,allowRetry=1)
        grant=event('DBIDResp','RSP',source='SN0',target='HN0',dbid=99)
        data=event('NonCopyBackWriteData','DAT',source='HN0',target='SN0',beatOffset=0,txnid=777,dbid=99)
        data.update(payload=PAYLOAD,validMask='ff'*64,byteEnable='ff'*64)
        comp=event('Comp','RSP',source='SN0',target='HN0',dbid=99)
        result=refine([request,grant,data,comp]);self.assertFalse(failures(result))
        dbid=flits(result,'DBIDResp')[0]
        for d in flits(result,'NonCopyBackWriteData'):
            self.assertEqual(d['fields']['TxnID']['value'],dbid['fields']['DBID']['value'])
            self.assertEqual(d['fields']['TxnID']['sourceEventId'],dbid['id'])
            self.assertEqual(d['fields']['DBID']['value'],0)

    def retry(self,grant_type=7,early=True):
        grant=event('PCrdGrant','RSP',source='HN0',target='RN0',leg='credit/grant',pcrdtype=grant_type,txnid=345)
        rejected=event('RetryAck','RSP',source='HN0',target='RN0',pcrdtype=7)
        resend=req(attempt='1');resend['fields'].update(allowRetry=0,pcrdtype=7,txnid=999)
        return refine([req(),*( [grant,rejected] if early else [rejected,grant]),resend,dat(0,attempt='1'),dat(32,attempt='1')])

    def test_credit_can_arrive_early_and_retry_change_id(self):
        for early in (True,False):
            result=self.retry(early=early);self.assertFalse(failures(result))
            grant=flits(result,'PCrdGrant')[0];self.assertEqual(grant['fields']['TxnID']['value'],0)
            requests=flits(result,'ReadNoSnp');self.assertEqual(len(requests),2)
            self.assertIn(grant['id'],requests[1]['dependencies'])
            self.assertEqual(requests[0]['fields']['TxnID']['value'],requests[1]['fields']['TxnID']['value'])
            self.assertNotEqual(requests[0]['attemptId'],requests[1]['attemptId'])

    def test_wrong_credit_type_fails(self):self.assertTrue(failures(self.retry(grant_type=6)))
    def test_reuse_policy_prefers_prior_id_after_an_earlier_request_finishes(self):
        other=req(leg='other/up')
        rejected=event('RetryAck','RSP',source='HN0',target='RN0',pcrdtype=7)
        grant=event('PCrdGrant','RSP',source='HN0',target='RN0',leg='credit/grant',pcrdtype=7)
        resend=req(attempt='1');resend['fields'].update(allowRetry=0,pcrdtype=7)
        result=refine([other,req(),rejected,dat(0,leg='other/up'),dat(32,leg='other/up'),grant,resend,dat(0,attempt='1'),dat(32,attempt='1')])
        self.assertFalse(failures(result))
        requests=[e for e in flits(result,'ReadNoSnp') if e['legId']=='r/up']
        self.assertEqual(requests[0]['fields']['TxnID']['value'],requests[1]['fields']['TxnID']['value'])
        self.assertFalse([e for e in result if e.get('status')=='unsupported'])
    def test_protocol_retry_rotates_id_with_explicit_chiron_coverage(self):
        grant=event('PCrdGrant','RSP',source='HN0',target='RN0',leg='credit/grant',pcrdtype=7)
        rejected=event('RetryAck','RSP',source='HN0',target='RN0',pcrdtype=7)
        resend=req(attempt='1');resend['fields'].update(allowRetry=0,pcrdtype=7)
        result=refine([req(),grant,rejected,resend,dat(0,attempt='1'),dat(32,attempt='1')],options=['--rotate-retry-id'])
        self.assertFalse(failures(result));requests=flits(result,'ReadNoSnp')
        self.assertNotEqual(requests[0]['fields']['TxnID']['value'],requests[1]['fields']['TxnID']['value'])
        self.assertTrue(any(e.get('engine')=='CHIron E.b' and e.get('status')=='unsupported' for e in result))
    def test_disabled_coarse_retry_is_not_silently_rewritten(self):
        request=req();request['fields']['allowRetry']=0
        result=refine([request,dat(0),dat(32)]);self.assertTrue(failures(result))
        self.assertFalse(flits(result,'ReadNoSnp'))
        self.assertEqual(result[1]['fields']['allowRetry'],0)
    def test_missing_retry_field_remains_unknown(self):
        request=req();request['fields'].pop('allowRetry')
        result=refine([request,dat(0),dat(32)])
        self.assertFalse(flits(result,'ReadNoSnp'))
        self.assertNotIn('allowRetry',result[1]['fields'])
        self.assertTrue(any(e.get('status')=='insufficient' for e in result))
    def test_rn_return_fields_are_inapplicable(self):
        request=req(source='RN1');beats=[dat(0),dat(32)]
        for d in beats:d['target']='RN1'
        result=refine([request,*beats]);self.assertFalse(failures(result))
        fields=flits(result,'ReadNoSnp')[0]['fields']
        self.assertEqual(fields['ReturnNID']['value'],0);self.assertEqual(fields['ReturnTxnID']['value'],0)
    def test_downstream_return_fields_target_home_attempt(self):
        request=req(source='HN0',target='SN0');beats=[dat(0),dat(32)]
        for d in beats:d.update(source='SN0',target='HN0')
        result=refine([request,*beats]);self.assertFalse(failures(result))
        fields=flits(result,'ReadNoSnp')[0]['fields']
        self.assertEqual(fields['ReturnNID']['value'],144)
        self.assertEqual(fields['ReturnTxnID']['value'],fields['TxnID']['value'])
    def test_hnf_snf_reserved_order_values_fail(self):
        for order in [2,3]:
            request=req(source='HN0',target='SN0');request['fields']['order']=order
            self.assertTrue(failures(refine([request],complete=False)))
    def test_first_attempt_requires_zero_credit_type(self):
        request=req();request['fields']['pcrdtype']=7
        self.assertTrue(failures(refine([request,dat(0),dat(32)])))
    def test_mandatory_compack_cannot_be_disabled_by_observation(self):
        request=req();request['opcode']='ReadShared';request['fields']['expCompAck']=0
        result=refine([request,dat(0),dat(32)])
        self.assertTrue(any(e.get('rule')=='B2.7.3' for e in failures(result)))
        self.assertEqual(flits(result,'ReadShared')[0]['fields']['ExpCompAck']['origin'],'observed')
    def test_owo_write_remains_raw_until_its_flow_is_supported(self):
        request=event('WriteUniqueFull','REQ',sizeBytes=64,allowRetry=1,expCompAck=1,order=2)
        result=refine([request],complete=False)
        self.assertFalse(flits(result,'WriteUniqueFull'))
        self.assertTrue(any(e.get('status')=='unsupported' for e in result))
    def test_copyback_comp_only_does_not_invent_write_data_handshake(self):
        request=event('WriteBackFull','REQ',sizeBytes=64,allowRetry=1,expCompAck=0)
        comp=event('Comp','RSP',source='HN0',target='RN0',dbid=99)
        result=refine([request,comp])
        self.assertFalse([e for e in result if e['type']=='protocol'])
        self.assertFalse(failures(result))
        self.assertTrue(any(e.get('status')=='unsupported' for e in result))
    def test_credit_alone_does_not_authorize_first_request_allow_retry_zero(self):
        credit=event('PCrdGrant','RSP',source='HN0',target='RN0',leg='credit',pcrdtype=7)
        request=req();request['fields'].update(allowRetry=0,pcrdtype=7)
        result=refine([credit,request,dat(0),dat(32)])
        self.assertTrue(failures(result));self.assertFalse(flits(result,'ReadNoSnp'))
    def test_read_be_zero_does_not_hide_payload_mismatch(self):
        child=req(source='HN0',target='SN0',leg='r/down');child['parentLegId']='r/up'
        child_data=[dat(0,leg='r/down'),dat(32,leg='r/down')]
        for d in child_data:d.update(source='SN0',target='HN0',byteEnable='00'*32,parentLegId='r/up')
        parent_data=[dat(0),dat(32)]
        for d in parent_data:d['payload']='00'*32
        result=refine([req(),child,*child_data,*parent_data])
        self.assertTrue(any(e.get('rule')=='payload-conservation' for e in failures(result)))
        self.assertFalse(flits(result,'CompData')[0]['fields']['BE']['applicable'])
    def test_full_write_rejects_partial_be(self):
        request=event('WriteNoSnpFull','REQ',sizeBytes=64,allowRetry=1)
        grant=event('DBIDResp','RSP',source='HN0',target='RN0',dbid=99)
        data=event('NonCopyBackWriteData','DAT',beatOffset=0,dbid=99)
        data.update(payload='00'*64,validMask='ff'*64,byteEnable='ff'*4+'00'*60)
        self.assertTrue(any(e.get('rule')=='B2.9.3' for e in failures(refine([request,grant,data]))))
    def test_ordered_read_waits_for_receipt_and_does_not_duplicate_it(self):
        request=req(source='HN0',target='SN0');request['fields']['order']=1
        beats=[dat(0),dat(32)]
        for d in beats:d.update(source='SN0',target='HN0')
        receipt=event('ReadReceipt','RSP',source='SN0',target='HN0')
        result=refine([request,*beats,receipt]);self.assertFalse(failures(result))
        release=[e for e in result if e['type']=='protocol' and e.get('fields',{}).get('Action',{}).get('value')=='release']
        self.assertTrue(all(e['anchorSeq']==4 for e in release))
        result=refine([request,receipt,*beats],options=['--ordered-read'])
        self.assertFalse(failures(result));self.assertEqual(len(flits(result,'ReadReceipt')),1)
        self.assertTrue(failures(refine([request,*beats])))
        self.assertFalse(failures(refine([request,*beats],complete=False)))
    def test_write_comp_does_not_release_request_id_before_dbid(self):
        request=event('WriteNoSnpFull','REQ',sizeBytes=64,allowRetry=1)
        comp=event('Comp','RSP',source='HN0',target='RN0',dbid=99)
        other=req(leg='s/up')
        grant=event('DBIDResp','RSP',source='HN0',target='RN0',dbid=99)
        data=event('NonCopyBackWriteData','DAT',beatOffset=0,dbid=99)
        data.update(payload=PAYLOAD,validMask='ff'*64,byteEnable='ff'*64)
        result=refine([request,comp,other,grant,data,dat(0,leg='s/up'),dat(32,leg='s/up')])
        self.assertFalse(failures(result))
        self.assertNotEqual(flits(result,'WriteNoSnpFull')[0]['fields']['TxnID']['value'],flits(result,'ReadNoSnp')[0]['fields']['TxnID']['value'])
    def test_field_overflow_is_not_truncated(self):
        request=req();request['fields']['pcrdtype']=16
        self.assertTrue(failures(refine([request,dat(0),dat(32)])))
    def test_invalid_mask_rejected(self):
        bad=dat(0);bad['validMask']='01'+'ff'*31
        self.assertTrue(failures(refine([req(),bad,dat(32)])))
    def test_wrong_data_opcode_rejected(self):
        bad=dat(0);bad['opcode']='SnpRespData'
        self.assertTrue(failures(refine([req(),bad,dat(32)])))
    def test_same_message_identity_cannot_hide_different_data(self):
        tx=dat(0);tx.update(messageId='m1',observation='TX')
        rx=copy.deepcopy(tx);rx['observation']='RX';rx['payload']='aa'*32
        self.assertTrue(failures(refine([req(),tx,rx,dat(32)])))
    def test_wrong_channel_is_not_reinterpreted(self):
        request=req();request['opcode']='SnpShared'
        result=refine([request,dat(0),dat(32)])
        self.assertTrue(failures(result));self.assertFalse(flits(result,'SnpShared'))
    def test_wrong_observed_write_dbid_rejected(self):
        request=event('WriteNoSnpFull','REQ',sizeBytes=64,allowRetry=1)
        grant=event('DBIDResp','RSP',source='HN0',target='RN0',dbid=99)
        data=event('NonCopyBackWriteData','DAT',beatOffset=0,dbid=98)
        data.update(payload=PAYLOAD,validMask='ff'*64,byteEnable='ff'*64)
        self.assertTrue(failures(refine([request,grant,data])))
    def test_separate_comp_must_match_dbid_response_in_both_orders(self):
        request=event('WriteNoSnpFull','REQ',sizeBytes=64,allowRetry=1)
        grant=event('DBIDResp','RSP',source='HN0',target='RN0',dbid=99)
        comp=event('Comp','RSP',source='HN0',target='RN0',dbid=98)
        for responses in ([grant,comp],[comp,grant]):
            self.assertTrue(any(e.get('rule')=='B2.5.3' for e in failures(refine([request,*responses],complete=False))))
    def test_immediate_write_cannot_use_copyback_data(self):
        request=event('WriteNoSnpFull','REQ',sizeBytes=64,allowRetry=1)
        grant=event('DBIDResp','RSP',source='HN0',target='RN0',dbid=99)
        data=event('CopyBackWriteData','DAT',beatOffset=0,dbid=99,resp=5)
        data.update(payload=PAYLOAD,validMask='ff'*64,byteEnable='ff'*64)
        self.assertTrue(any(e.get('rule')=='B2.5.3' for e in failures(refine([request,grant,data]))))
    def test_data_resp_must_be_consistent_but_resperr_can_vary(self):
        first=dat(0);second=dat(32);second['fields']['resp']=2
        self.assertTrue(any(e.get('rule')=='B2.9.4' for e in failures(refine([req(),first,second]))))
        second['fields']['resp']=1;first['fields']['respErr']=0;second['fields']['respErr']=1
        result=refine([req(),first,second]);self.assertFalse(failures(result))
        self.assertEqual(flits(result,'CompData')[1]['fields']['RespErr']['value'],1)
        self.assertEqual(flits(result,'CompData')[1]['fields']['RespErr']['origin'],'observed')
    def test_profile_does_not_allocate_more_than_1024_request_ids(self):
        result=refine([req(leg=f'r{i}/up') for i in range(1025)],complete=False)
        self.assertEqual(len(flits(result,'ReadNoSnp')),1024)
        self.assertTrue(any(e.get('rule')=='B2.4.2' and e.get('status')=='unsupported' for e in result))
    def test_duplicate_beat_fails(self):self.assertTrue(failures(refine([req(),dat(0),dat(0),dat(32)])))
    def test_missing_beat_fails_only_for_complete_capture(self):
        self.assertTrue(failures(refine([req(),dat(0)])))
        incomplete=refine([req(),dat(0)],complete=False);self.assertFalse(failures(incomplete))
        self.assertTrue(any(e.get('status')=='insufficient' for e in incomplete))
    def test_unknown_payload_stays_absent(self):
        unknown=dat(0);unknown.pop('payload');unknown['validMask']='00'*32
        result=refine([req(),unknown,dat(32)]);self.assertFalse(failures(result))
        self.assertNotIn('payload',flits(result,'CompData')[0])
        self.assertTrue(any(e.get('rule')=='payload' and e.get('status')=='insufficient' for e in result))
    def test_unknown_write_payload_and_unknown_be_are_distinct(self):
        request=event('WriteNoSnpFull','REQ',sizeBytes=64,allowRetry=1)
        grant=event('CompDBIDResp','RSP',source='HN0',target='RN0',dbid=99)
        data=[]
        for offset in [0,32]:
            beat=event('NonCopyBackWriteData','DAT',beatOffset=offset,dbid=99)
            beat.update(validMask='00'*32,byteEnable='ff'*32)
            data.append(beat)
        result=refine([request,grant,*data]);self.assertFalse(failures(result))
        self.assertTrue(all('payload' not in d for d in flits(result,'NonCopyBackWriteData')))
        data[0].pop('byteEnable')
        result=refine([request,grant,*data]);self.assertFalse(failures(result))
        self.assertFalse([e for e in result if e['type']=='protocol'])
        self.assertTrue(any(e.get('rule')=='B2.9.3' and e.get('status')=='insufficient' for e in result))
    def test_partial_byte_enable_preserved(self):
        partial=dat(0);partial['byteEnable']='ff'*4+'00'*28
        result=refine([req(),partial,dat(32)]);self.assertFalse(failures(result));self.assertEqual(flits(result,'CompData')[0]['fields']['BE']['value'],'0xf')
    def test_truncated_request_not_guessed(self):
        result=refine([dat(0),dat(32)],complete=False);self.assertFalse(flits(result,'CompData'));self.assertFalse(failures(result))
    def test_unordered_read_is_not_changed(self):
        self.assertFalse(flits(refine([req(),dat(0),dat(32)]),'ReadReceipt'))
    def test_ordered_read_dbid_inapplicable(self):
        request=req(source='HN0',target='SN0');data=[dat(0),dat(32)]
        for d in data:d.update(source='SN0',target='HN0')
        result=refine([request,*data],options=['--ordered-read']);self.assertFalse(failures(result))
        receipt=flits(result,'ReadReceipt')[0];self.assertEqual(receipt['fields']['DBID']['value'],0);self.assertEqual(receipt['origin'],'modeled');self.assertNotIn('tick',receipt)
    def test_same_ids_do_not_merge_nodes_or_lifetimes(self):
        other=[req(leg='s/up'),dat(0,leg='s/up'),dat(32,leg='s/up')]
        for e in other:
            if e['source']=='RN0':e['source']='RN1'
            if e['target']=='RN0':e['target']='RN1'
        result=refine([req(),other[0],dat(0),other[1],dat(32),other[2]])
        self.assertFalse(failures(result));requests=flits(result,'ReadNoSnp');self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]['fields']['TxnID']['value'],requests[1]['fields']['TxnID']['value']);self.assertNotEqual(requests[0]['legId'],requests[1]['legId'])
    def test_address_outside_wire_width_preserved(self):
        r=req();r['address']='0x20000000000001';result=refine([r],complete=False)
        self.assertEqual(next(e for e in result if e['type']=='observed')['address'],r['address'])
        self.assertFalse(flits(result,'ReadNoSnp'));self.assertTrue(any(e.get('status')=='unsupported' for e in result))

if __name__=='__main__':unittest.main(verbosity=2)
