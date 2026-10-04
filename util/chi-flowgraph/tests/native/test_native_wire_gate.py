"""Mutation tests for the independent raw-traffic validator.

These are explicitly checker fixtures, not gem5-produced demonstration traces.
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("native_wire_gate", ROOT / "scripts/check-native-wire.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def event(channel, opcode, source, target, txnid, **extra):
    fields = dict(qos=0, srcid=source, tgtid=target, txnid=txnid, opcode=opcode)
    if channel == "REQ":
        fields.update(size=6, order=0, allowRetry=1, pcrdtype=0, memattr=13,
                      snpattr=1, expCompAck=1, returnNid=0, returnTxnId=0)
    elif channel == "RSP":
        fields.update(dbid=0, resp=0, respErr=0, pcrdtype=0)
    elif channel == "SNP":
        fields.update(addr="0x200", retToSrc=1, doNotGoToSD=1)
    else:
        fields.update(dbid=0xabc, HomeNID=4, dataid=0, resp=1)
    fields.update(extra)
    e = dict(type="observed", kind="flit", channel=channel, fields=fields,
             address="0x1000", wireEncoding="native-chi", opcode=f"{channel}:{opcode:#x}")
    if channel == "DAT":
        e.update(payload="ab"*32, byteEnable="00"*32, validMask="ff"*32)
    return e


def read_trace():
    return [event("REQ", 0x26, 0, 4, 4095),
            event("DAT", 4, 4, 0, 4095, dataid=2),
            event("DAT", 4, 4, 0, 4095, dataid=0),
            event("RSP", 2, 0, 4, 0xabc)]


def write_trace(partial=False):
    q = event("REQ", 0x1c if partial else 0x1b, 0, 4, 11, expCompAck=0, size=3 if partial else 6)
    if partial:
        q["address"] = "0x1028"
        q["fields"]["snpattr"] = 0
    grant = event("RSP", 6 if partial else 5, 4, 0, 11, dbid=0xdef)
    data = []
    for dataid in ([2] if partial else [0, 2]):
        d = event("DAT", 3 if partial else 2, 0, 4, 0xdef,
                  dbid=0, HomeNID=0, dataid=dataid, resp=0 if partial else 6)
        d.update(payload="00"*8+"ab"*8+"00"*16 if partial else "ab"*32,
                 byteEnable="00"*8+"ff"*8+"00"*16 if partial else "ff"*32)
        data.append(d)
    return [q, grant, *data] + ([event("RSP", 4, 4, 0, 11, dbid=0xdef)] if partial else [])


def evict_or_evict_trace(comp_only=False):
    q = event("REQ", 0x42, 0, 4, 11, expCompAck=1, likelyShared=1, cah=0)
    response = event("RSP", 4 if comp_only else 5, 4, 0, 11, dbid=0xdef)
    if comp_only:
        return [q, response, event("RSP", 2, 0, 4, 0xdef, resp=1)]
    data = []
    for dataid in (2, 0):
        d = event("DAT", 2, 0, 4, 0xdef, dbid=0, HomeNID=0, dataid=dataid, resp=1)
        d["byteEnable"] = "ff" * 32
        data.append(d)
    return [q, response, *data]


def mmio_read_trace():
    q = event("REQ", 4, 0, 4, 20, size=3, memattr=3, snpattr=0, expCompAck=0, order=3)
    q["address"] = "0x1028"
    receipt = event("RSP", 8, 4, 0, 20, dbid=0xaaa)
    d = event("DAT", 4, 4, 0, 20, HomeNID=4, dbid=0, dataid=2, resp=0)
    d.update(payload="00"*8 + "ab"*8 + "00"*16, validMask="00"*8 + "ff"*8 + "00"*16)
    return [q, receipt, d]


class NativeWireGateTests(unittest.TestCase):
    def audit(self, events, mutate_rx=None, allow_open=False):
        records = [dict(type="manifest", topology=[dict(nodeId=0, role="RN-F"),
                   dict(nodeId=4, role="HN-F"), dict(nodeId=8, role="SN-F")])]
        for i, original in enumerate(events):
            tx = copy.deepcopy(original)
            tx.update(messageId=f"msg:{i}", observation="TX")
            rx = copy.deepcopy(tx)
            rx["observation"] = "RX"
            if mutate_rx:
                mutate_rx(i, rx)
            records += [tx, rx]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checker-fixture.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in records), encoding="utf-8")
            return module.Audit(path, allow_open).run()

    def assert_pass(self, events):
        report = self.audit(events)
        self.assertTrue(report["passed"], report["errors"])

    def assert_fail(self, events, phrase):
        report = self.audit(events)
        self.assertFalse(report["passed"])
        self.assertTrue(any(phrase in e["error"] for e in report["errors"]), report["errors"])

    def test_full_read_out_of_order_12bit_ids(self):
        self.assert_pass(read_trace())

    def test_size_is_not_repaired(self):
        trace = read_trace(); trace[0]["fields"]["size"] = 64
        self.assert_fail(trace, "field-width")

    def test_dataid_is_not_repaired(self):
        trace = read_trace(); trace[1]["fields"]["dataid"] = 1
        self.assert_fail(trace, "dataid")

    def test_compack_uses_dbid(self):
        trace = read_trace(); trace[-1]["fields"]["txnid"] = 4095
        self.assert_fail(trace, "compack-dbid")

    def test_router_cannot_change_field(self):
        report = self.audit(read_trace(), lambda i,e: e["fields"].update(size=5) if i == 0 else None)
        self.assertFalse(report["passed"])
        self.assertTrue(any("router altered" in e["error"] for e in report["errors"]))

    def test_copyback_write_dbid_and_two_beats(self):
        self.assert_pass(write_trace())

    def test_wrong_write_txnid(self):
        trace = write_trace(); trace[2]["fields"]["txnid"] = 11
        self.assert_fail(trace, "write-dbid")

    def test_partial_write_natural_lanes(self):
        self.assert_pass(write_trace(partial=True))

    def test_partial_write_wrong_lanes(self):
        trace = write_trace(partial=True)
        trace[2].update(payload="ab"*8+"00"*24, byteEnable="ff"*8+"00"*24)
        self.assert_fail(trace, "natural-lanes")

    def test_unknown_read_bytes_are_not_interpreted_as_zero(self):
        trace = read_trace(); trace[1]["validMask"] = "00"*8 + "ff"*24
        self.assert_pass(trace)

    def test_full_write_be_zero_is_error(self):
        trace = write_trace(); trace[2]["payload"] = "00"+"ab"*31;trace[2]["byteEnable"] = "00"+"ff"*31
        self.assert_fail(trace, "full write")

    def test_comp_only_copyback_requires_ack(self):
        q = write_trace()[0]
        self.assert_pass([q, event("RSP", 4, 4, 0, 11, dbid=0xabc), event("RSP", 2, 0, 4, 0xabc)])

    def test_same_txnid_different_requesters(self):
        trace = read_trace()
        downstream = event("REQ", 4, 4, 8, 4095, expCompAck=0, snpattr=0, returnNid=4, returnTxnId=4095)
        data = [event("DAT", 4, 8, 4, 4095, HomeNID=8, dbid=0, resp=0, dataid=i) for i in (2,0)]
        self.assert_pass([trace[0],downstream,*data,*trace[1:]])

    def test_compdata_homenid_must_be_completer_even_without_ack(self):
        q=event("REQ",4,4,8,9,expCompAck=0,snpattr=0,returnNid=4,returnTxnId=9)
        data=[event("DAT",4,8,4,9,HomeNID=8,dbid=0,resp=0,dataid=i) for i in (0,2)]
        self.assert_pass([q,*data])
        for d in data:d["fields"]["HomeNID"]=0
        self.assert_fail([q,*data], "home-nid")

    def test_separate_comp_must_match_dbid_response(self):
        trace=write_trace(partial=True)
        trace[-1]["fields"]["dbid"]=0
        self.assert_fail(trace, "Comp/DBIDResp DBID mismatch")

    def test_unique_snoop_mandatory_do_not_go_to_sd(self):
        snp=event("SNP",7,4,0,5,doNotGoToSD=0)
        self.assert_fail([snp], "DoNotGoToSD must be one")

    def test_credit_before_retry_and_changed_id(self):
        trace = read_trace()
        retry = copy.deepcopy(trace[0]);retry["fields"].update(allowRetry=0,pcrdtype=3,txnid=6)
        for e in trace[1:3]:e["fields"]["txnid"] = 6
        self.assert_pass([trace[0], event("RSP",7,4,0,123,pcrdtype=3),
                          event("RSP",3,4,0,4095,pcrdtype=3),retry,*trace[1:]])

    def test_wrong_credit_type_rejected(self):
        trace = read_trace()
        retry = copy.deepcopy(trace[0]);retry["fields"].update(allowRetry=0,pcrdtype=3)
        self.assert_fail([trace[0],event("RSP",7,4,0,7,pcrdtype=4),
                          event("RSP",3,4,0,4095,pcrdtype=3),retry], "retry-credit")

    def test_raw_snoop_address(self):
        snp = event("SNP",7,4,0,5)
        data = [event("DAT",1,0,4,5,dbid=0,HomeNID=0,dataid=i,resp=4) for i in (2,0)]
        for d in data:d["byteEnable"]="ff"*32
        self.assert_pass([snp,*data])
        snp["fields"]["addr"] = snp["address"]
        self.assert_fail([snp,*data], "snoop-address")

    def test_coarse_messages_rejected(self):
        trace = read_trace();trace[0]["wireEncoding"]="coarse"
        self.assert_fail(trace, "native-source")

    def test_missing_beat_not_falsely_completed(self):
        self.assert_fail(read_trace()[:2], "incomplete requests")

    def test_truncated_capture_requires_explicit_opt_in(self):
        self.assertTrue(self.audit(read_trace()[:2], allow_open=True)["passed"])

    def test_prefix_report_exposes_open_evidence(self):
        report=self.audit(read_trace()[:2],allow_open=True)
        self.assertEqual(report["capturePolicy"],"allow-open-prefix")
        self.assertFalse(report["completeCaptureValidated"])
        self.assertEqual(report["openEvidence"]["requests"],1)
        self.assertGreater(report["openEvidence"]["completionAcks"],0)

    def test_overlapping_txnid_rejected(self):
        self.assert_fail([read_trace()[0],read_trace()[0]], "reused live requester TxnID")

    def test_makeunique_requires_comp_uc_and_compack(self):
        trace = [event("REQ", 12, 0, 4, 11), event("RSP", 4, 4, 0, 11, dbid=17, resp=2),
                 event("RSP", 2, 0, 4, 17)]
        self.assert_pass(trace)
        self.assert_fail(trace[:-1], "incomplete")
        trace[1]["fields"]["resp"] = 1
        self.assert_fail(trace, "require Comp_UC")

    def test_makeunique_missing_expcompack_is_rejected(self):
        self.assert_fail([event("REQ", 12, 0, 4, 11, expCompAck=0)], "ExpCompAck")

    def test_cmos_and_evict_complete_without_compack(self):
        for opcode in (8, 9, 10, 13):
            with self.subTest(opcode=opcode):
                q = event("REQ", opcode, 0, 4, 11, expCompAck=0)
                self.assert_pass([q, event("RSP", 4, 4, 0, 11)])
                q["fields"]["expCompAck"] = 1
                self.assert_fail([q], "must not request CompAck")

    def test_line_request_rejects_partial_size(self):
        self.assert_fail([event("REQ", 12, 0, 4, 11, size=3)], "64-byte line")

    def test_writeclean_dirty_data_uses_implicit_ack(self):
        trace = write_trace(); trace[0]["fields"]["opcode"] = 0x17
        for resp in (6, 7):
            with self.subTest(resp=resp):
                for d in trace[2:]:
                    d["fields"]["resp"] = resp
                self.assert_pass(trace)

    def test_weoe_comp_only_requires_explicit_ack(self):
        trace = evict_or_evict_trace(True)
        self.assert_pass(trace)
        self.assert_fail(trace[:-1], "incomplete")

    def test_weoe_data_is_implicit_ack_despite_expcompack_one(self):
        trace = evict_or_evict_trace()
        self.assert_pass(trace)
        self.assert_fail(trace + [event("RSP", 2, 0, 4, 0xdef, resp=1)], "unmatched CompAck")

    def test_weoe_requires_expcompack_one(self):
        trace = evict_or_evict_trace(); trace[0]["fields"]["expCompAck"] = 0
        self.assert_fail(trace, "WriteEvictOrEvict")

    def test_clean_eviction_must_not_pass_dirty(self):
        trace = evict_or_evict_trace(); trace[2]["fields"]["resp"] = 6
        self.assert_fail(trace, "must be I, SC or UC")
        trace = evict_or_evict_trace(True); trace[-1]["fields"]["resp"] = 6
        self.assert_fail(trace, "CompAck cache state")

    def test_copyback_rejects_separate_dbid_response(self):
        trace = write_trace(); trace[1]["fields"]["opcode"] = 6
        self.assert_fail(trace, "split DBIDResp")

    def test_recorded_cah_zero_rejects_nonweoe_comp_only(self):
        q = write_trace()[0]; q["fields"]["cah"] = 0
        trace = [q, event("RSP", 4, 4, 0, 11, dbid=0xabc), event("RSP", 2, 0, 4, 0xabc)]
        self.assert_fail(trace, "requires CAH=1")
        q["fields"]["cah"] = 1
        self.assert_pass(trace)

    def test_subline_mmio_read_natural_lanes_and_unknown_bytes(self):
        self.assert_pass(mmio_read_trace())

    def test_nosnp_must_not_set_snpattr(self):
        trace = mmio_read_trace(); trace[0]["fields"]["snpattr"] = 1
        self.assert_fail(trace, "require SnpAttr=0")

    def test_device_partial_write_excludes_bytes_below_address(self):
        trace = write_trace(True)
        trace[0]["address"] = "0x102b"
        trace[0]["fields"]["memattr"] = 3
        self.assert_fail(trace, "BE outside Addr/Size window")
        trace[2].update(payload="00"*11 + "ab"*5 + "00"*16,
                        byteEnable="00"*11 + "ff"*5 + "00"*16)
        self.assert_pass(trace)

    def test_device_size_requires_all_beats_even_when_first_has_no_valid_bytes(self):
        q = event("REQ", 0x1c, 0, 4, 11, size=6, memattr=3, snpattr=0, expCompAck=0)
        q["address"] = "0x1038"
        first = event("DAT", 3, 0, 4, 0xdef, dbid=0, HomeNID=0, dataid=0, resp=0)
        first.update(payload="00"*32, byteEnable="00"*32, validMask="00"*32)
        last = event("DAT", 3, 0, 4, 0xdef, dbid=0, HomeNID=0, dataid=2, resp=0)
        last.update(payload="00"*24 + "ab"*8, byteEnable="00"*24 + "ff"*8)
        trace = [q, event("RSP", 6, 4, 0, 11, dbid=0xdef), first, last,
                 event("RSP", 4, 4, 0, 11, dbid=0xdef)]
        self.assert_pass(trace)
        self.assert_fail(trace[:2] + trace[3:], "incomplete requests")

    def test_transport_cannot_change_validity_evidence(self):
        report = self.audit(read_trace(), lambda i, e: e.update(validMask="00"*32) if i == 1 else None)
        self.assertFalse(report["passed"])
        self.assertTrue(any("router altered" in error["error"] for error in report["errors"]))

    def test_read_compack_must_not_report_copyback_state(self):
        trace = read_trace(); trace[-1]["fields"]["resp"] = 1
        self.assert_fail(trace, "non-CopyBack CompAck Resp")

    def test_compack_resperr_is_zero(self):
        trace = evict_or_evict_trace(True); trace[-1]["fields"]["respErr"] = 1
        self.assert_fail(trace, "CompAck RespErr")


if __name__ == "__main__":
    unittest.main()
