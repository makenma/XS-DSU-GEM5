"""Unit tests for the HNF dirty-victim end-to-end checker/parser."""

import gzip
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "gem5"
    / "chi"
    / "check_hnf_dirty_victim_e2e.py"
)
SPEC = importlib.util.spec_from_file_location("hnf_dirty_victim_checker", CHECKER_PATH)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def valid_config(child=True):
    hnf = {
        "type": "HomeNodeFull",
        "node_type": "hnf",
        "direct_sn_fake_data": False,
        "slc_num_sets": 1024 if child else 64,
        "slc_num_ways": 16 if child else 1,
    }
    if child:
        hnf["slcsf"] = {
            "type": "SlcSnoopFilter",
            "slc_num_sets": 64,
            "slc_num_ways": 1,
        }
    return {
        "root": [
            {"nested": {"objects": [hnf]}},
            {"fabric": {"type": "Chi2ClassicMemBridge"}},
        ]
    }


def valid_trace():
    # Two transactions deliberately interleave to exercise live-ID routing.
    return "\n".join(
        [
            "10: hnf: HNF_DV_START victim=1 txn=20 addr=0x8000 "
            "requester_src=2 requester_txn=30 data_hash=00ab",
            "11: hnf: HNF_DV_START victim=2 txn=21 addr=0x8040 "
            "requester_src=3 requester_txn=31 data_hash=cd",
            "12: sn: SN_DV_DAT txn=21 addr=0x8040 dbid=41 bytes=64 data_hash=0xcd",
            "13: sn: SN_DV_DAT txn=20 addr=0x8000 dbid=40 bytes=64 data_hash=ab",
            "14: sn: SN_DV_COMP txn=21 dbid=41 error=0",
            # Original requester completion is independent of victim release.
            "15: hnf: HNF_DV_REQUESTER_DONE src=3 txn=31",
            "16: hnf: HNF_DV_RELEASE victim=2 txn=21 release_req=51",
            "17: sn: SN_DV_COMP txn=20 dbid=40 error=0",
            "18: hnf: HNF_DV_RELEASE victim=1 txn=20 release_req=50",
            "19: hnf: HNF_DV_REQUESTER_DONE src=2 txn=30",
        ]
    ) + "\n"


def valid_simout():
    lines = ["CHI_LITMUS_BEGIN harts=4 iterations=16"]
    lines.extend(
        "CHI_LITMUS_TEST id={} errors=0".format(test_id)
        for test_id in range(7)
    )
    for hart in range(4):
        lines.append(
            "CHI_LITMUS_SUM hart={} alias=50000001f0 "
            "pressure=2800000000ffc000".format(hart)
        )
        lines.append(
            "CHI_LITMUS_BAD hart={} count=0 owner=0 index=0 "
            "expected=0 actual=0".format(hart)
        )
    lines.append("CHI_LITMUS_PASS total_errors=0")
    return "\n".join(lines) + "\n"


class TraceParserTest(unittest.TestCase):
    def test_accepts_interleaved_complete_chains(self):
        records = checker.parse_trace_text(valid_trace())
        chains = checker.validate_trace(records)
        self.assertEqual(2, len(chains))
        self.assertEqual({20, 21}, {chain.start.fields["txn"] for chain in chains})

    def test_rejects_unknown_machine_marker(self):
        with self.assertRaisesRegex(checker.CheckError, "unknown"):
            checker.parse_trace_text("HNF_DV_MYSTERY txn=1\n")

    def test_rejects_wrong_or_extra_fields(self):
        trace = valid_trace().replace(
            "data_hash=00ab", "data_hash=00ab opcode=WriteNoSnpFull", 1
        )
        with self.assertRaisesRegex(checker.CheckError, "extra"):
            checker.parse_trace_text(trace)

    def test_rejects_duplicate_comp(self):
        trace = valid_trace().replace(
            "17: sn: SN_DV_COMP txn=20 dbid=40 error=0",
            "17: sn: SN_DV_COMP txn=20 dbid=40 error=0\n"
            "17: sn: SN_DV_COMP txn=20 dbid=40 error=0",
        )
        with self.assertRaisesRegex(checker.CheckError, "duplicate"):
            checker.validate_trace(checker.parse_trace_text(trace))

    def test_rejects_comp_before_dat(self):
        lines = valid_trace().splitlines()
        lines[2], lines[4] = lines[4], lines[2]
        with self.assertRaisesRegex(checker.CheckError, "before DAT"):
            checker.validate_trace(checker.parse_trace_lines(lines))

    def test_rejects_bad_dat_size(self):
        trace = valid_trace().replace("dbid=41 bytes=64", "dbid=41 bytes=32")
        with self.assertRaisesRegex(checker.CheckError, "expected 64"):
            checker.validate_trace(checker.parse_trace_text(trace))

    def test_rejects_zero_dbid(self):
        trace = valid_trace().replace("addr=0x8040 dbid=41", "addr=0x8040 dbid=0")
        with self.assertRaisesRegex(checker.CheckError, "zero DBID"):
            checker.validate_trace(checker.parse_trace_text(trace))

    def test_rejects_zero_release_request_id(self):
        trace = valid_trace().replace("release_req=51", "release_req=0")
        with self.assertRaisesRegex(checker.CheckError, "must be nonzero"):
            checker.validate_trace(checker.parse_trace_text(trace))

    def test_rejects_downstream_requester_txn_collision(self):
        trace = valid_trace().replace("requester_txn=31", "requester_txn=21", 1)
        with self.assertRaisesRegex(checker.CheckError, "equals requester txn"):
            checker.validate_trace(checker.parse_trace_text(trace))

    def test_rejects_address_or_hash_mismatch(self):
        trace = valid_trace().replace(
            "txn=21 addr=0x8040 dbid=41 bytes=64 data_hash=0xcd",
            "txn=21 addr=0x8080 dbid=41 bytes=64 data_hash=ef",
        )
        with self.assertRaisesRegex(checker.CheckError, "address"):
            checker.validate_trace(checker.parse_trace_text(trace))

    def test_rejects_missing_requester_completion(self):
        trace = valid_trace().replace(
            "19: hnf: HNF_DV_REQUESTER_DONE src=2 txn=30\n", ""
        )
        with self.assertRaisesRegex(checker.CheckError, "without requester"):
            checker.validate_trace(checker.parse_trace_text(trace))


class ConfigAndSimoutParserTest(unittest.TestCase):
    def test_recurses_and_prefers_final_child_geometry(self):
        evidence = checker.validate_config(valid_config(child=True))
        self.assertTrue(evidence.slc_path.endswith(".slcsf"))

    def test_accepts_stage_a_flat_geometry(self):
        evidence = checker.validate_config(valid_config(child=False))
        self.assertFalse(evidence.slc_path.endswith(".slcsf"))

    def test_rejects_fake_hnf_or_wrong_final_geometry(self):
        config = valid_config()
        hnf = config["root"][0]["nested"]["objects"][0]
        hnf["direct_sn_fake_data"] = True
        with self.assertRaisesRegex(checker.CheckError, "real SN bridge"):
            checker.validate_config(config)
        hnf["direct_sn_fake_data"] = False
        hnf["slcsf"]["slc_num_ways"] = 2
        with self.assertRaisesRegex(checker.CheckError, "expected 64x1"):
            checker.validate_config(config)

    def test_accepts_all_workload_oracles(self):
        checker.validate_simout_text(valid_simout())

    def test_rejects_missing_or_duplicate_workload(self):
        missing = valid_simout().replace("CHI_LITMUS_TEST id=6 errors=0\n", "")
        with self.assertRaisesRegex(checker.CheckError, "test id keys"):
            checker.validate_simout_text(missing)
        duplicate = valid_simout().replace(
            "CHI_LITMUS_TEST id=6 errors=0",
            "CHI_LITMUS_TEST id=6 errors=0\nCHI_LITMUS_TEST id=6 errors=0",
        )
        with self.assertRaisesRegex(checker.CheckError, "repeats test id"):
            checker.validate_simout_text(duplicate)

    def test_rejects_bad_checksum_bad_count_and_fail(self):
        checksum = valid_simout().replace("alias=50000001f0", "alias=1", 1)
        with self.assertRaisesRegex(checker.CheckError, "alias checksum"):
            checker.validate_simout_text(checksum)
        bad = valid_simout().replace("hart=2 count=0", "hart=2 count=1")
        with self.assertRaisesRegex(checker.CheckError, "BAD diagnostic"):
            checker.validate_simout_text(bad)
        bad_hex = valid_simout().replace(
            "hart=2 count=0 owner=0 index=0 expected=0 actual=0",
            "hart=2 count=1 owner=3 index=3312 expected=4000000000cf0 "
            "actual=deadbeef",
        )
        with self.assertRaisesRegex(checker.CheckError, "BAD diagnostic"):
            checker.validate_simout_text(bad_hex)
        failed = valid_simout().replace(
            "CHI_LITMUS_PASS total_errors=0",
            "CHI_LITMUS_FAIL total_errors=1",
        )
        with self.assertRaisesRegex(checker.CheckError, "PASS"):
            checker.validate_simout_text(failed)


class FileAndCliTest(unittest.TestCase):
    def _write_inputs(self, root, gzip_trace):
        config_path = root / "config.json"
        simout_path = root / "simout"
        trace_path = root / ("trace.log.gz" if gzip_trace else "trace.log")
        config_path.write_text(json.dumps(valid_config()), encoding="utf-8")
        simout_path.write_text(valid_simout(), encoding="utf-8")
        if gzip_trace:
            with gzip.open(trace_path, "wt", encoding="utf-8") as stream:
                stream.write(valid_trace())
        else:
            trace_path.write_text(valid_trace(), encoding="utf-8")
        return config_path, simout_path, trace_path

    def test_plain_and_gzip_trace_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plain = self._write_inputs(root, False)
            self.assertEqual(2, len(checker.verify_files(*plain)))
            compressed = self._write_inputs(root, True)
            self.assertEqual(2, len(checker.verify_files(*compressed)))

    def test_cli_emits_stable_pass_marker(self):
        with TemporaryDirectory() as directory:
            paths = self._write_inputs(Path(directory), True)
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "--config-json",
                str(paths[0]),
                "--simout",
                str(paths[1]),
                "--trace",
                str(paths[2]),
            ]
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = checker.main(argv)
            self.assertEqual(0, result)
            self.assertIn(checker.PASS_MARKER, stdout.getvalue())
            self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
