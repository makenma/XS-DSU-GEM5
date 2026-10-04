"""Synthetic causal-trace mutations for the native RTL behavior gate.

These are validator tests, never claimed as CPU or simulator coverage.
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_rtl_behavior", ROOT / "scripts/check-native-rtl-behavior.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TraceFixture:
    def __init__(self):
        self.events = []
        self.ids = {"RN0": 0, "HN0": 4, "SN0": 8}

    def add(self, name, root, leg, parent="", address=0x14000, **extra):
        event = {"type": "observed", "id": name, "rootId": root, "legId": leg,
                 "parentLegId": parent, "address": hex(address), "tick": str(100 + len(self.events)),
                 **extra}
        self.events.append(event)
        return event

    def flit(self, name, root, leg, channel, op, source, target, tid,
             parent="", address=0x14000, **extra):
        fields = {"opcode": op, "srcid": self.ids[source], "tgtid": self.ids[target],
                  "txnid": tid, "resp": 0, "dbid": 0, **extra.pop("fields", {})}
        tx = self.add(name + ":tx", root, leg, parent, address, kind="flit", observation="TX",
                      wireEncoding="native-chi", messageId=name, channel=channel,
                      source=source, target=target, fields=fields, **extra)
        rx = copy.deepcopy(tx)
        rx.update(id=name + ":rx", observation="RX", tick=str(100 + len(self.events)))
        self.events.append(rx)

    def data(self, name, root, leg, source, target, dbid, op=2, resp=1,
             parent="", address=0x14000, value=0x123456789abcdef):
        payload = value.to_bytes(8, "little") + bytes(56)
        for dataid in (0, 2):
            start = dataid * 16
            self.flit(f"{name}:{dataid}", root, leg, "DAT", op, source, target, dbid, parent, address,
                      fields={"dataid": dataid, "resp": resp}, payload=payload[start:start + 32].hex(),
                      byteEnable="ff" * 32, validMask="ff" * 32)

    def make_unique(self):
        root, leg = "mu", "mu/up"
        self.add("mu:allocate", root, leg, kind="allocate", node="RN0",
                 fields={"fullOverwritePromise": True, "inputPacket": "InvalidateReq"})
        self.flit("mu:req", root, leg, "REQ", 12, "RN0", "HN0", 9)
        self.flit("mu:comp", root, leg, "RSP", 4, "HN0", "RN0", 9, fields={"dbid": 71, "resp": 2})
        self.flit("mu:ack", root, leg, "RSP", 2, "RN0", "HN0", 71)

    def weoe(self, data):
        root = "weoe-data" if data else "weoe-comp"
        leg = root + "/up"
        self.flit(root + ":req", root, leg, "REQ", 0x42, "RN0", "HN0", 10)
        self.flit(root + ":comp", root, leg, "RSP", 5 if data else 4, "HN0", "RN0", 10,
                  fields={"dbid": 72})
        if data:
            self.data(root + ":dat", root, leg, "RN0", "HN0", 72)
        else:
            self.flit(root + ":ack", root, leg, "RSP", 2, "RN0", "HN0", 72)

    def cmo(self, name, offset, op, release_op, value):
        root, release = name, name + "/up"
        cmo, memory = release + "/cmo:1", release + "/cmo:1/memory-write:2"
        address = 0x10000 + offset
        self.flit(name + ":release", root, release, "REQ", release_op, "RN0", "HN0", 11, address=address)
        self.flit(name + ":release-dbid", root, release, "RSP", 5, "HN0", "RN0", 11,
                  address=address, fields={"dbid": 73})
        self.data(name + ":release-dat", root, release, "RN0", "HN0", 73, resp=7,
                  address=address, value=value)
        self.flit(name + ":cmo", root, cmo, "REQ", op, "RN0", "HN0", 12, release, address)
        self.flit(name + ":memory", root, memory, "REQ", 0x1d, "HN0", "SN0", 13, cmo, address)
        self.flit(name + ":memory-dbid", root, memory, "RSP", 6, "SN0", "HN0", 13, cmo, address,
                  fields={"dbid": 74})
        self.data(name + ":memory-dat", root, memory, "HN0", "SN0", 74, op=3, resp=0,
                  parent=cmo, address=address, value=value)
        payload = value.to_bytes(8, "little") + bytes(56)
        self.add(name + ":memory-port-write", root, memory, cmo, address, kind="memory_request", node="SN0",
                 fields={"isWrite": True, "sizeBytes": 64, "command": "WriteReq"},
                 payload=payload.hex(), byteEnable="ff" * 64, validMask="ff" * 64)
        self.add(name + ":memory-port-response", root, memory, cmo, address,
                 kind="memory_response", node="SN0", fields={"isWrite": True, "sizeBytes": 64, "command": "WriteResp"})
        self.flit(name + ":memory-comp", root, memory, "RSP", 4, "SN0", "HN0", 13, cmo, address,
                  fields={"dbid": 74})
        self.flit(name + ":cmo-comp", root, cmo, "RSP", 4, "HN0", "RN0", 12, release, address)

    @classmethod
    def complete(cls):
        trace = cls()
        trace.make_unique()
        trace.weoe(False)
        trace.weoe(True)
        trace.cmo("clean", 0x1000, 8, 0x17, 0xaabbccdd01020304)
        trace.cmo("flush", 0x1100, 9, 0x1b, 0xddccbbaa04030201)
        return trace


class NativeRtlBehaviorTests(unittest.TestCase):
    def check(self, mutate=None):
        trace = TraceFixture.complete()
        if mutate:
            mutate(trace)
        with tempfile.TemporaryDirectory(prefix="native-rtl-behavior-") as directory:
            folder = Path(directory)
            (folder / "native-l2-result.json").write_text(json.dumps({"sharedPhysical": "0x10000"}))
            rows = [{"profile": "h-native-256"}, *trace.events]
            (folder / "raw.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            return MODULE.BehaviorAudit(folder).run()

    def rejected(self, mutate, reason=None):
        report = self.check(mutate)
        self.assertFalse(report["pass"], report)
        if reason:
            self.assertTrue(any(reason in issue for issue in report["issues"]), report["issues"])
        return report

    @staticmethod
    def replace(trace, prefix, field, value):
        for event in trace.events:
            if event["id"].startswith(prefix):
                event[field] = value

    @staticmethod
    def field(trace, prefix, field, value):
        for event in trace.events:
            if event["id"].startswith(prefix):
                event.setdefault("fields", {})[field] = value

    @staticmethod
    def drop(trace, prefix):
        trace.events[:] = [e for e in trace.events if not e["id"].startswith(prefix)]

    def test_complete_causal_fixture_passes(self):
        report = self.check()
        self.assertTrue(report["pass"], report["issues"])
        self.assertEqual(report["makeUniqueCount"], 1)
        self.assertEqual(report["writeEvictOrEvictBranches"],
                         {"Comp_CompAck": 1, "CompDBIDResp_CopyBackWriteData": 1})
        self.assertEqual(len(report["cmoMemoryChecks"]), 2)
        self.assertTrue(all(item["comparedBytes"] == 64 for item in report["cmoMemoryChecks"]))

    def test_missing_each_weoe_branch_rejected(self):
        for prefix in ("weoe-comp", "weoe-data"):
            with self.subTest(branch=prefix):
                self.rejected(lambda t: self.drop(t, prefix), "missing completed WEOE branch")

    def test_weoe_comp_requires_ack(self):
        self.rejected(lambda t: self.drop(t, "weoe-comp:ack"), "CompAck")

    def test_weoe_ack_uses_completion_dbid(self):
        self.rejected(lambda t: self.field(t, "weoe-comp:ack", "txnid", 10), "compack-dbid")

    def test_weoe_data_requires_both_beats(self):
        self.rejected(lambda t: self.drop(t, "weoe-data:dat:2"), "data-coverage")

    def test_weoe_data_cannot_add_ack(self):
        def mutate(trace):
            trace.flit("weoe-data:ack", "weoe-data", "weoe-data/up", "RSP", 2, "RN0", "HN0", 72)
        self.rejected(mutate, "extra CompAck")

    def test_weoe_data_cannot_be_dirty(self):
        self.rejected(lambda t: self.field(t, "weoe-data:dat", "resp", 6), "clean-eviction")

    def test_missing_makeunique_rejected(self):
        self.rejected(lambda t: self.drop(t, "mu:"), "no completed MakeUnique")

    def test_makeunique_requires_uc(self):
        self.rejected(lambda t: self.field(t, "mu:comp", "resp", 1), "makeunique-uc")

    def test_makeunique_requires_real_full_overwrite(self):
        self.rejected(lambda t: self.field(t, "mu:allocate", "fullOverwritePromise", False), "makeunique-full-overwrite")

    def test_makeunique_requires_compack(self):
        self.rejected(lambda t: self.drop(t, "mu:ack"), "CompAck")

    def test_makeunique_compdata_is_rejected(self):
        def mutate(trace):
            trace.flit("mu:compdata", "mu", "mu/up", "DAT", 4, "HN0", "RN0", 9)
        self.rejected(mutate, "makeunique-no-data")

    def test_same_address_cannot_replace_explicit_cmo_parent(self):
        def mutate(trace):
            for event in trace.events:
                if event["legId"] == "clean/up/cmo:1":
                    event["parentLegId"] = "some-other-release/up"
        self.rejected(mutate, "explicit-parent")

    def test_memory_child_must_name_cmo_parent(self):
        def mutate(trace):
            for event in trace.events:
                if event["legId"] == "clean/up/cmo:1/memory-write:2":
                    event["parentLegId"] = "clean/up"
        self.rejected(mutate, "explicit HN memory-write child")

    def test_cmo_root_mismatch_cannot_join_by_address(self):
        def mutate(trace):
            for event in trace.events:
                if event["legId"] == "clean/up/cmo:1/memory-write:2":
                    event["rootId"] = "unrelated-root"
        self.rejected(mutate, "explicit HN memory-write child")

    def test_cpu_known_value_must_be_present_in_release(self):
        self.rejected(lambda t: self.replace(t, "clean:release-dat:0", "payload", "00" * 32), "cpu-value")

    def test_stale_hn_bytes_cannot_overwrite_cpu_value(self):
        self.rejected(lambda t: self.replace(t, "clean:memory-dat:0", "payload", "00" * 32), "memory-value")

    def test_unknown_release_bytes_cannot_prove_preservation(self):
        self.rejected(lambda t: self.replace(t, "clean:release-dat:2", "validMask", "00" * 32), "known-data")

    def test_partial_downstream_be_rejected(self):
        self.rejected(lambda t: self.replace(t, "clean:memory-dat:2", "byteEnable", "ff" * 31 + "00"), "byte-enable")

    def test_duplicate_dataid_rejected(self):
        self.rejected(lambda t: self.field(t, "clean:memory-dat:2", "dataid", 0), "data-coverage")

    def test_dat_txnid_is_dbid_not_reqid(self):
        self.rejected(lambda t: self.field(t, "clean:memory-dat", "txnid", 13), "write-dbid")

    def test_missing_real_memory_port_write_rejected(self):
        self.rejected(lambda t: self.drop(t, "clean:memory-port-write"), "actual SN memory-port write acceptance")

    def test_wrong_actual_memory_port_payload_rejected(self):
        self.rejected(lambda t: self.replace(t, "clean:memory-port-write", "payload", "00" * 64), "memory-port-value")

    def test_wrong_actual_memory_port_mask_rejected(self):
        self.rejected(lambda t: self.replace(t, "clean:memory-port-write", "byteEnable", "ff" * 63 + "00"), "memory-port-value")

    def test_missing_actual_memory_response_rejected(self):
        self.rejected(lambda t: self.drop(t, "clean:memory-port-response"), "actual SN memory-port write response")

    def test_actual_memory_response_must_follow_write(self):
        def mutate(trace):
            response = next(e for e in trace.events if e["id"] == "clean:memory-port-response")
            trace.events.remove(response)
            index = next(i for i, e in enumerate(trace.events) if e["id"] == "clean:memory-port-write")
            trace.events.insert(index, response)
        self.rejected(mutate, "actual memory write response follows accepted write")

    def test_cmo_must_wait_for_hn_reception_of_sn_completion(self):
        def mutate(trace):
            completion = [e for e in trace.events if e["id"].startswith("clean:cmo-comp")]
            trace.events[:] = [e for e in trace.events if e not in completion]
            index = next(i for i, e in enumerate(trace.events) if e["id"] == "clean:memory-comp:rx")
            trace.events[index:index] = completion
        self.rejected(mutate, "HN receives SN completion before completing CMO")

    def test_missing_rx_is_not_completed_behavior(self):
        self.rejected(lambda t: self.drop(t, "clean:memory-comp:rx"), "RX for")

    def test_tx_rx_data_mismatch_rejected(self):
        self.rejected(lambda t: self.replace(t, "clean:memory-dat:0:rx", "payload", "00" * 32), "TX/RX payload mismatch")


if __name__ == "__main__":
    unittest.main()
