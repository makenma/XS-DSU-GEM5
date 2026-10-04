"""Mutation tests for the RTL workload gate; fixtures are not gem5 evidence.

The small traces exercise evidence/coverage validation only. Full CHI handshake
validation remains the separate check-native-wire.py gate.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-native-rtl.py"
OPCODES = {
    "ReadNotSharedDirty": 0x26, "ReadUnique": 0x07, "MakeUnique": 0x0c,
    "CleanShared": 0x08, "CleanInvalid": 0x09, "MakeInvalid": 0x0a,
    "Evict": 0x0d, "WriteBackFull": 0x1b, "WriteCleanFull": 0x17,
    "WriteEvictOrEvict": 0x42, "ReadNoSnp": 0x04, "WriteNoSnpPtl": 0x1c,
}


class NativeRtlCheckerTests(unittest.TestCase):
    @staticmethod
    def fixture():
        result = {
            "pass": True, "exitCode": 0,
            "cause": "exiting with last active thread context",
            "protocolOpcodeInjection": False,
            "timedCpuPath": "KMH CPU -> classic L1 -> ChiNativeL2 -> CHI NoC -> HN -> ChiNativeMemory",
            "cpuExitTick": "1000", "drainEndTick": "2000",
            "sharedPhysical": "0x10000", "sharedBytes": 65536,
            "uncachedMapping": {"physical": "0x30000", "virtual": "0x200000000",
                                "bytes": 4096, "kind": "normal-noncacheable-ram",
                                "cacheableAlias": False},
        }
        manifest = {
            "type": "manifest", "schemaVersion": "1.1", "profile": "h-native-256",
            "source": {"kind": "gem5_run", "nativeChi": True,
                       "generator": "native/gem5/native_l2_run.py"},
            "lineBytes": 64, "dataWidth": 256,
            "nativeL2": {"classicL2": False, "protocolOpcodeInjection": False},
            "program": {"rtlRequestWorkload": True, "protocolOpcodeInjection": False,
                        "uncachedVirtual": "0x200000000", "uncachedBytes": 4096,
                        "uncachedKind": "normal-noncacheable-ram"},
            "topology": [{"id": f"RN{i}", "nodeId": i, "role": "RN-F"} for i in range(4)]
                        + [{"id": "HN0", "nodeId": 4, "role": "HN-F"}],
        }
        events = []
        for index, (name, opcode) in enumerate(OPCODES.items()):
            nc = name in ("ReadNoSnp", "WriteNoSnpPtl")
            events.append({
                "type": "observed", "id": f"o:{index + 1}", "seq": index + 1,
                "kind": "flit", "observation": "TX", "wireEncoding": "native-chi",
                "channel": "REQ", "source": "RN0", "target": "HN0",
                "tick": str(index + 10), "opcode": name,
                "address": "0x30000" if nc else hex(0x10000 + index * 64),
                "fields": {"opcode": opcode, "srcid": 0, "tgtid": 4, "txnid": index,
                           "size": 3 if nc else 6, "memattr": 0 if nc else 13,
                           "order": 2 if nc else 0, "snpattr": int(not nc),
                           "expCompAck": int(name in ("ReadNotSharedDirty", "ReadUnique",
                                                      "MakeUnique", "WriteEvictOrEvict"))},
            })
        log = "\n".join(f"NATIVE_RTL_HART_{hart}_PASS" for hart in range(4)) + "\n"
        config = "\n".join(f"[system.l2{hart}]\ntype=ChiNativeL2\n" for hart in range(4))
        return {"result": result, "manifest": manifest, "events": events, "log": log, "config": config}

    def check(self, mutate=None, diagnostic=False):
        fixture = self.fixture()
        if mutate:
            mutate(fixture)
        with tempfile.TemporaryDirectory(prefix="native-rtl-checker-") as directory:
            folder = Path(directory)
            (folder / "native-l2-result.json").write_text(json.dumps(fixture["result"]), encoding="utf-8")
            (folder / "run.log").write_text(fixture["log"], encoding="utf-8")
            (folder / "config.ini").write_text(fixture["config"], encoding="utf-8")
            rows = [fixture["manifest"], *fixture["events"]]
            (folder / "raw.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            command = [sys.executable, str(CHECKER), str(folder)]
            if diagnostic:
                command.append("--allow-missing-coverage")
            process = subprocess.run(command, capture_output=True, text=True)
            report_path = folder / "native-rtl-result.json"
            self.assertTrue(report_path.is_file(), process.stdout + process.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(process.returncode == 0, report["pass"], process.stdout + process.stderr)
            return report

    def rejected(self, mutate, diagnostic=False):
        report = self.check(mutate, diagnostic)
        self.assertFalse(report["pass"], report)
        return report

    def test_complete_synthetic_evidence_accepted(self):
        report = self.check()
        self.assertTrue(report["pass"])
        self.assertTrue(report["cpuPass"])
        self.assertTrue(report["completeRequestCoverage"])
        self.assertEqual(set(report["requestCounts"]), set(OPCODES))
        self.assertEqual(report["rawTxFlits"], 12)

    def test_cpu_failure_rejected_despite_all_banners(self):
        self.rejected(lambda f: f["result"].__setitem__("pass", False))

    def test_truthy_nonboolean_pass_rejected(self):
        self.rejected(lambda f: f["result"].__setitem__("pass", 1))

    def test_nonzero_cpu_exit_rejected_despite_pass(self):
        self.rejected(lambda f: f["result"].__setitem__("exitCode", 1))

    def test_timeout_rejected_despite_pass(self):
        self.rejected(lambda f: f["result"].__setitem__("cause", "simulate() limit reached"))

    def test_cpu_failure_banner_rejected(self):
        self.rejected(lambda f: f.__setitem__("log", f["log"] + "NATIVE_RTL_FAIL hart=0\n"))

    def test_missing_each_hart_rejected(self):
        for hart in range(4):
            with self.subTest(hart=hart):
                self.rejected(lambda f: f.__setitem__("log", f["log"].replace(f"NATIVE_RTL_HART_{hart}_PASS\n", "")))

    def test_duplicate_hart_banner_rejected(self):
        self.rejected(lambda f: f.__setitem__("log", f["log"] + "NATIVE_RTL_HART_0_PASS\n"))

    def test_suffixed_hart_banner_is_not_a_pass(self):
        self.rejected(lambda f: f.__setitem__("log", f["log"].replace("HART_0_PASS", "HART_0_PASS_FAKE")))

    def test_no_tx_events_rejected(self):
        report = self.rejected(lambda f: f.__setitem__("events", []))
        self.assertEqual(report["rawTxFlits"], 0)

    def test_rx_cannot_supply_request_coverage(self):
        def mutate(f):
            for event in f["events"]:
                event["observation"] = "RX"
        self.rejected(mutate)

    def test_hn_requests_cannot_supply_rn_coverage(self):
        def mutate(f):
            for event in f["events"]:
                event["source"] = "HN0"
        self.rejected(mutate)

    def test_non_native_tx_rejected(self):
        self.rejected(lambda f: f["events"][0].__setitem__("wireEncoding", "coarse"))

    def test_missing_each_required_request_rejected(self):
        for name in OPCODES:
            with self.subTest(opcode=name):
                report = self.rejected(lambda f: f.__setitem__("events", [e for e in f["events"] if e["opcode"] != name]))
                self.assertIn(name, report["missingRequestOpcodes"])

    def test_wrong_opcode_name_rejected(self):
        self.rejected(lambda f: f["events"][0].__setitem__("opcode", "ReadNoSnpTypo"))

    def test_unexpected_rn_opcode_rejected_even_with_all_twelve(self):
        def mutate(f):
            extra = dict(f["events"][0], id="o:13", opcode="WriteNoSnpFull", address="0x14000")
            extra["fields"] = dict(extra["fields"], opcode=0x1d)
            f["events"].append(extra)
        self.rejected(mutate)

    def test_opcode_number_must_match_observed_name(self):
        self.rejected(lambda f: f["events"][0]["fields"].__setitem__("opcode", 0x04))

    def test_missing_wire_opcode_rejected(self):
        self.rejected(lambda f: f["events"][0]["fields"].pop("opcode"))

    def test_nosnp_below_nc_mapping_rejected(self):
        self.rejected(lambda f: f["events"][-1].__setitem__("address", "0x2ffff"))

    def test_nosnp_at_exclusive_mapping_end_rejected(self):
        self.rejected(lambda f: f["events"][-1].__setitem__("address", "0x31000"))

    def test_cacheable_request_in_nc_mapping_rejected(self):
        self.rejected(lambda f: f["events"][0].__setitem__("address", "0x30000"))

    def test_missing_nc_mapping_rejected(self):
        self.rejected(lambda f: f["result"].pop("uncachedMapping"))

    def test_zero_length_nc_mapping_rejected(self):
        self.rejected(lambda f: f["result"]["uncachedMapping"].__setitem__("bytes", 0))

    def test_cacheable_alias_rejected(self):
        self.rejected(lambda f: f["result"]["uncachedMapping"].__setitem__("cacheableAlias", True))

    def test_device_mapping_is_not_normal_nc_evidence(self):
        self.rejected(lambda f: f["result"]["uncachedMapping"].__setitem__("kind", "device"))

    def test_nc_request_attributes_must_match_normal_nc_policy(self):
        for name, value in (("memattr", 2), ("order", 0), ("expCompAck", 1)):
            with self.subTest(field=name):
                self.rejected(lambda f: f["events"][-1]["fields"].__setitem__(name, value))

    def test_rn_wire_node_must_match_topology(self):
        self.rejected(lambda f: f["events"][0]["fields"].__setitem__("srcid", 999))

    def test_opcode_injection_rejected(self):
        self.rejected(lambda f: f["result"].__setitem__("protocolOpcodeInjection", True))

    def test_classic_cpu_path_rejected(self):
        self.rejected(lambda f: f["result"].__setitem__("timedCpuPath", "KMH CPU -> classic L2 -> Cache2ChiBridge"))

    def test_wrong_manifest_profile_rejected(self):
        self.rejected(lambda f: f["manifest"].__setitem__("profile", "h-core-256"))

    def test_non_rtl_program_rejected(self):
        self.rejected(lambda f: f["manifest"]["program"].__setitem__("rtlRequestWorkload", False))

    def test_classic_bridge_configuration_rejected(self):
        self.rejected(lambda f: f.__setitem__("config", f["config"] + "\n[system.bridge]\ntype=Cache2ChiBridge\n"))

    def test_missing_native_l2_configuration_rejected(self):
        self.rejected(lambda f: f.__setitem__("config", f["config"].replace("type=ChiNativeL2", "type=Cache", 1)))

    def test_diagnostic_missing_coverage_never_claims_complete(self):
        report = self.check(lambda f: f["events"].pop(), diagnostic=True)
        self.assertFalse(report["completeRequestCoverage"])
        self.assertIn("WriteNoSnpPtl", report["missingRequestOpcodes"])

    def test_diagnostic_flag_does_not_bypass_cpu_failure(self):
        self.rejected(lambda f: f["result"].__setitem__("pass", False), diagnostic=True)


if __name__ == "__main__":
    unittest.main()
