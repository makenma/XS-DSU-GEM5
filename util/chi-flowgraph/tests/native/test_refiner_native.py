"""Native conversion must expose original wire values, including invalid ones."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_native_wire_gate import event, read_trace, write_trace, evict_or_evict_trace, mmio_read_trace

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get("CHI_REFINE_BINARY", ROOT / "build" / ("chi-refine.exe" if os.name == "nt" else "chi-refine")))
NAMES = {("REQ",0x26):"ReadNotSharedDirty",("REQ",4):"ReadNoSnp",("REQ",0x1b):"WriteBackFull",
         ("REQ",7):"ReadUnique",("REQ",12):"MakeUnique",("REQ",13):"Evict",
         ("REQ",8):"CleanShared",("REQ",9):"CleanInvalid",("REQ",10):"MakeInvalid",
         ("REQ",0x17):"WriteCleanFull",("REQ",0x42):"WriteEvictOrEvict",
         ("REQ",0x1c):"WriteNoSnpPtl",("REQ",0x1d):"WriteNoSnpFull",("DAT",4):"CompData",
         ("DAT",2):"CopyBackWriteData",("DAT",3):"NonCopyBackWriteData",("RSP",2):"CompAck",
         ("RSP",3):"RetryAck",("RSP",4):"Comp",("RSP",5):"CompDBIDResp",("RSP",6):"DBIDResp",
         ("RSP",7):"PCrdGrant",("RSP",8):"ReadReceipt",("SNP",7):"SnpUnique"}
NODE_NAMES = {0:"RN0",4:"HN0",8:"SN0"}


def records_for(events, profile="h-native-256"):
    result = [dict(type="manifest", schemaVersion="1.1", profile=profile,
        title="Native checker fixture", lineBytes=64,dataWidth=256,complete=True,
        source={"kind":"validation_fixture"}, protocol="ARM IHI 0050 Issue H",
        initialState={},clock={"tickHz":"1000000000000"},
        topology=[dict(id="RN0",nodeId=0,role="RN-F"),dict(id="HN0",nodeId=4,role="HN-F"),dict(id="SN0",nodeId=8,role="SN-F")])]
    for n, original in enumerate(events):
        tx = copy.deepcopy(original)
        f=tx["fields"]
        tx.update(rootId="root:actual",legId="leg:actual",attemptId="observed-attempt",parentLegId="",
                  source=NODE_NAMES[f["srcid"]],target=NODE_NAMES[f["tgtid"]],messageId=f"message:{n}",
                  observation="TX",opcode=NAMES.get((tx["channel"],f["opcode"]),"Unknown"))
        rx=copy.deepcopy(tx);rx["observation"]="RX"
        for e in (tx,rx):
            seq=len(result);e.update(id=f"o:{seq}",seq=seq,tick=str(9007199254740993+seq))
            result.append(e)
    return result


def refine(records, options=(), expect_error=False):
    with tempfile.TemporaryDirectory() as temporary:
        source=Path(temporary)/"native.jsonl";target=Path(temporary)/"session.chi.jsonl"
        source.write_text("\n".join(map(json.dumps,records)),encoding="utf-8")
        proc=subprocess.run([str(BINARY),"--input",str(source),"--output",str(target),*options],capture_output=True,text=True)
        if expect_error:
            return proc
        if proc.returncode:
            raise AssertionError(proc.stderr)
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]


def protocol(records):
    return [r for r in records if r["type"]=="protocol"]


def failures(records):
    return [r for r in records if r["type"]=="validation" and r["status"]=="fail"]


class NativeRefinerTests(unittest.TestCase):
    def test_auto_profile_keeps_observed_records_and_wire_values(self):
        original=records_for(read_trace());result=refine(original)
        self.assertEqual(original[1:],[e for e in result if e["type"]=="observed"])
        self.assertEqual(result[0]["profile"],"h-native-256")
        p=protocol(result);self.assertEqual(len(p),4)
        self.assertEqual([e["kind"] for e in p],["flit"]*4)
        self.assertEqual(p[0]["fields"]["TxnID"]["value"],4095)
        self.assertEqual(p[0]["fields"]["Size"]["value"],6)
        self.assertEqual(p[1]["fields"]["DataID"]["value"],2)
        self.assertEqual(p[1]["fields"]["DBID"]["value"],0xabc)
        self.assertEqual(p[3]["fields"]["TxnID"]["value"],0xabc)
        self.assertEqual(p[0]["origin"],"observed")
        self.assertEqual(p[0]["anchorTick"],original[1]["tick"])
        self.assertEqual(p[0]["sourceEventIds"],[original[1]["id"],original[2]["id"]])
        self.assertFalse(failures(result),failures(result))

    def test_no_false_transaction_joint_pass(self):
        result=refine(records_for(read_trace()))
        joint=[e for e in result if e["type"]=="validation" and e["rule"]=="transaction-joint"]
        self.assertEqual([e["status"] for e in joint],["unsupported"])
        self.assertTrue(any(e["rule"]=="field-mapping" and e["status"]=="pass" for e in result if e["type"]=="validation"))

    def test_homenid_preserved_in_actual_sn_response(self):
        e=event("DAT",4,8,4,9,HomeNID=8,dbid=0,resp=0,dataid=2)
        p=protocol(refine(records_for([e])))[0]
        self.assertEqual(p["fields"]["HomeNID"]["value"],8)
        self.assertEqual(p["fields"]["HomeNID"]["origin"],"observed")

    def test_comp_dbid_mismatch_not_rewritten(self):
        trace=write_trace(partial=True);trace[-1]["fields"]["dbid"]=17
        p=protocol(refine(records_for(trace)))
        self.assertEqual(p[1]["fields"]["DBID"]["value"],0xdef)
        self.assertEqual(p[-1]["fields"]["DBID"]["value"],17)

    def test_field_sources_and_declared_defaults(self):
        p=protocol(refine(records_for(read_trace())))
        self.assertEqual(p[0]["fields"]["TxnID"]["origin"],"observed")
        self.assertEqual(p[0]["fields"]["TxnID"]["sourceField"],"txnid")
        self.assertEqual(p[1]["fields"]["RespErr"]["origin"],"modeled")
        self.assertEqual(p[1]["fields"]["RespErr"]["value"],0)

    def test_invalid_size_retained(self):
        trace=read_trace();trace[0]["fields"]["size"]=64
        result=refine(records_for(trace))
        self.assertEqual(protocol(result)[0]["fields"]["Size"]["value"],64)
        self.assertTrue(failures(result))

    def test_invalid_dataid_retained(self):
        trace=read_trace();trace[1]["fields"]["dataid"]=1
        result=refine(records_for(trace))
        self.assertEqual(protocol(result)[1]["fields"]["DataID"]["value"],1)
        self.assertTrue(any(e["rule"]=="B2.9.4" for e in failures(result)))

    def test_write_txnid_is_not_mapped(self):
        result=refine(records_for(write_trace()))
        self.assertFalse(failures(result),failures(result))
        p=protocol(result)
        self.assertEqual(p[0]["fields"]["TxnID"]["value"],11)
        self.assertEqual(p[2]["fields"]["TxnID"]["value"],0xdef)
        self.assertEqual(p[2]["fields"]["DBID"]["value"],0)
        self.assertEqual(p[2]["fields"]["HomeNID"]["value"],0)

    def test_pcredit_txnid_preserved_even_when_inapplicable(self):
        result=refine(records_for([event("RSP",7,4,0,123,pcrdtype=3)]))
        self.assertEqual(protocol(result)[0]["fields"]["TxnID"]["value"],123)

    def test_receipt_dbid_preserved_and_marked_inapplicable(self):
        result=refine(records_for([event("RSP",8,8,4,11,dbid=0xabc)]))
        dbid=protocol(result)[0]["fields"]["DBID"]
        self.assertEqual(dbid["value"],0xabc)
        self.assertFalse(dbid["applicable"])

    def test_partial_payload_not_repadded(self):
        result=refine(records_for(write_trace(partial=True)))
        p=protocol(result)[2];source=write_trace(partial=True)[2]
        self.assertEqual(p["payload"],source["payload"])
        self.assertEqual(p["validMask"],source["validMask"])
        self.assertEqual(p["byteEnable"],source["byteEnable"])
        self.assertEqual(p["fields"]["BE"]["value"],0xff00)

    def test_malformed_payload_not_split_or_padded(self):
        trace=read_trace();trace[1]["payload"]="ab"*64
        result=refine(records_for(trace))
        self.assertEqual(protocol(result)[1]["payload"],"ab"*64)
        self.assertEqual(len(protocol(result)),4)
        self.assertTrue(any(e["rule"]=="native-DAT-width" for e in failures(result)))

    def test_missing_required_native_field_not_invented(self):
        trace=read_trace();del trace[0]["fields"]["size"]
        result=refine(records_for(trace));size=protocol(result)[0]["fields"]["Size"]
        self.assertIsNone(size["value"]);self.assertEqual(size["origin"],"unknown")

    def test_missing_native_payload_is_unknown_not_padded(self):
        trace=read_trace();del trace[1]["payload"]
        result=refine(records_for(trace));self.assertNotIn("payload",protocol(result)[1])
        self.assertTrue(any(e["rule"]=="native-DAT-evidence" and e["status"]=="insufficient"
                            for e in result if e["type"]=="validation"))

    def test_unimplemented_optional_nonzero_is_not_falsely_checked_as_zero(self):
        trace=read_trace();trace[0]["fields"]["DoDWT"]=1
        result=refine(records_for(trace));p=protocol(result)[0]
        self.assertEqual(p["fields"]["DoDWT"]["value"],1)
        self.assertTrue(any(e["rule"]=="native-fixed-profile" and e["status"]=="unsupported"
                            for e in result if e["type"]=="validation"))

    def test_snp_address_is_already_encoded(self):
        result=refine(records_for([event("SNP",7,4,0,15)]))
        p=protocol(result)[0]
        self.assertEqual(p["fields"]["Addr"]["value"],"0x200")
        self.assertEqual(p["address"],"0x1000")
        self.assertNotIn("Size",p["fields"])

    def test_native_options_cannot_rewrite_timing_or_ids(self):
        for option in ("--ordered-read","--rotate-retry-id"):
            with self.subTest(option=option):
                p=refine(records_for(read_trace()),[option],expect_error=True)
                self.assertNotEqual(p.returncode,0)
                self.assertIn("preserve observed",p.stderr)

    def test_explicit_native_profile_supported(self):
        result=refine(records_for(read_trace(),profile="source-native-profile"),["--profile","h-native-256"])
        self.assertEqual(result[0]["profile"],"source-native-profile")
        self.assertEqual(result[0]["refinement"]["wireProfile"],"h-native-256")

    def test_coarse_wire_is_never_expanded_in_native_profile(self):
        trace=read_trace();trace[0]["wireEncoding"]="gem5-coarse"
        result=refine(records_for(trace))
        self.assertTrue(any(e["rule"]=="native-source" for e in failures(result)))
        self.assertFalse(any(e["channel"]=="REQ" for e in protocol(result)))

    def test_twelve_native_request_opcodes_use_actual_fields(self):
        requests = []
        for opcode in (0x26, 7, 12, 13, 0x1b, 0x17, 0x42, 8, 9, 10, 4, 0x1c):
            request = event("REQ", opcode, 0, 4, opcode, cah=0)
            request["fields"]["expCompAck"] = int(opcode in (0x26, 7, 12, 0x42))
            if opcode == 13:
                request["fields"]["memattr"] = 5
            if opcode in (4, 0x1c):
                request["address"] = "0x1028"
                request["fields"].update(size=3, snpattr=0, memattr=3, order=3)
            requests.append(request)
        result = refine(records_for(requests))
        self.assertFalse(failures(result), failures(result))
        mappings = [r for r in result if r["type"] == "validation" and r["rule"] == "field-mapping"]
        self.assertEqual(len(mappings), 12)
        self.assertTrue(all(r["status"] == "pass" for r in mappings), mappings)
        self.assertEqual([p["fields"]["Opcode"]["value"] for p in protocol(result)],
                         [request["fields"]["opcode"] for request in requests])

    def test_weoe_both_flows_have_no_added_compack_or_data(self):
        for comp_only in (False, True):
            with self.subTest(comp_only=comp_only):
                trace = evict_or_evict_trace(comp_only)
                result = refine(records_for(trace))
                self.assertFalse(failures(result), failures(result))
                self.assertEqual(len(protocol(result)), len(trace))
                self.assertEqual(protocol(result)[0]["fields"]["Opcode"]["value"], 0x42)
                if comp_only:
                    self.assertTrue(any(r.get("rule") == "field-mapping" and r["status"] == "unsupported"
                                        and "CompAck.Resp" in r["message"] for r in result))
                    self.assertEqual(protocol(result)[-1]["fields"]["Resp"]["value"], 1)

    def test_likelyshared_actual_one_is_checked_and_preserved(self):
        trace = evict_or_evict_trace()
        result = refine(records_for(trace))
        field = protocol(result)[0]["fields"]["LikelyShared"]
        self.assertEqual((field["value"], field["origin"], field["sourceField"]), (1, "observed", "likelyShared"))
        self.assertFalse(any(r.get("rule") == "native-fixed-profile" for r in result))
        self.assertFalse(failures(result), failures(result))

    def test_cah_zero_is_observed_and_one_is_explicitly_unsupported(self):
        trace = evict_or_evict_trace()
        result = refine(records_for(trace))
        field = protocol(result)[0]["fields"]["CAH"]
        self.assertEqual((field["value"], field["origin"], field["sourceField"]), (0, "observed", "cah"))
        trace[0]["fields"]["cah"] = 1
        result = refine(records_for(trace))
        self.assertEqual(protocol(result)[0]["fields"]["CAH"]["value"], 1)
        self.assertTrue(any(r.get("rule") == "native-fixed-profile" and r["status"] == "unsupported"
                            for r in result))
        self.assertFalse(any(r.get("rule") == "field-mapping" and "p:1" in r.get("eventIds", [])
                             for r in result))

    def test_mmio_read_keeps_subline_unknown_mask_and_receipt(self):
        trace = mmio_read_trace(); result = refine(records_for(trace))
        self.assertFalse(failures(result), failures(result))
        p = protocol(result)
        self.assertEqual(p[0]["fields"]["Size"]["value"], 3)
        self.assertEqual(p[0]["fields"]["Order"]["value"], 3)
        self.assertEqual(p[-1]["fields"]["DataID"]["value"], 2)
        self.assertEqual(p[-1]["validMask"], trace[-1]["validMask"])
        self.assertEqual(len(p), 3)


if __name__=="__main__":
    unittest.main()
