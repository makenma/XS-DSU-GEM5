"""Mutation tests for result validation, using synthetic fixtures, never gem5 evidence."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_litmus_checker", ROOT / "scripts/check-native-litmus.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class NativeLitmusCheckerTests(unittest.TestCase):
    def fixture(self, folder, variant="iriw", outcomes=("0000", "1111")):
        iterations = len(outcomes)
        result = dict(pass_=True, exitCode=0, cause="exiting with last active thread context",
                      protocolOpcodeInjection=False, timedCpuPath="KMH CPU -> ChiNativeL2",
                      simulationSeed=1, cpuExitTick="1000", drainEndTick="2000", sharedPhysical="0xc000")
        result["pass"] = result.pop("pass_")
        (folder / "native-l2-result.json").write_text(json.dumps(result))
        manifest = dict(profile="h-native-256", program=dict(variant=variant, iterations=iterations, seed=1))
        (folder / "raw.jsonl").write_text(json.dumps(manifest) + "\n")
        (folder / "config.ini").write_text("\n".join(f"[system.l2{i}]\ntype=ChiNativeL2" for i in range(4)))
        histogram = {f"{i:04b}": outcomes.count(f"{i:04b}") for i in range(16)}
        samples = []
        for index, outcome in enumerate(outcomes):
            rotation = (index + 1) & 3
            x_home = (index // 4 + 1) & 3
            y_home = (x_home + 1 + (index // 16) % 3) & 3
            samples.append(dict(round=index, values=[int(bit) for bit in outcome],
                                readerHarts=[(2 + 4 - rotation) & 3, (3 + 4 - rotation) & 3],
                                xOffset=0x2000 + x_home * 64, yOffset=0x3000 + y_home * 64))
        summary = dict(variant=variant, iterations=iterations, completed=iterations, seed=1,
                       invalidValues=0, errors=0, weakOutcomeCount=histogram["1010"],
                       forbidden=histogram["1010"] if variant == "iriw-fenced" else 0,
                       histogram=histogram)
        self.write_log(folder, samples, summary, iterations)
        return samples, summary

    @staticmethod
    def write_log(folder, samples, summary, iterations=2):
        lines = ["NATIVE_LITMUS_SAMPLE " + json.dumps(sample) for sample in samples]
        lines.append("NATIVE_LITMUS_RESULT " + json.dumps(summary))
        lines += [f"NATIVE_LITMUS_HART_{hart}_PASS completed={iterations}" for hart in range(4)]
        (folder / "run.log").write_text("\n".join(lines) + "\n")

    def check(self, mutate=None, variant="iriw", outcomes=("0000", "1111")):
        with tempfile.TemporaryDirectory(prefix="native-litmus-checker-") as directory:
            folder = Path(directory)
            samples, summary = self.fixture(folder, variant, outcomes)
            if mutate:
                mutate(folder, samples, summary)
            with contextlib.redirect_stdout(io.StringIO()):
                return CHECKER.check(folder, variant, len(outcomes), 1)

    def mutation(self, mutate, **kwargs):
        def apply(folder, samples, summary):
            mutate(samples, summary)
            self.write_log(folder, samples, summary)
        self.assertFalse(self.check(apply, **kwargs))

    def test_valid_relaxed_fixture(self):
        self.assertTrue(self.check())

    def test_valid_fenced_fixture(self):
        self.assertTrue(self.check(variant="iriw-fenced"))

    def test_relaxed_1010_is_recorded_not_rejected(self):
        self.assertTrue(self.check(outcomes=("1010", "1111")))

    def test_fenced_1010_rejected_even_with_pass_banners(self):
        self.assertFalse(self.check(variant="iriw-fenced", outcomes=("1010", "1111")))

    def test_unwritten_sentinel_rejected(self):
        self.mutation(lambda samples, summary: samples[0]["values"].__setitem__(0, 2**64 - 1))

    def test_nonbinary_value_rejected(self):
        self.mutation(lambda samples, summary: samples[0]["values"].__setitem__(0, 2))

    def test_boolean_read_value_rejected(self):
        self.mutation(lambda samples, summary: samples[0]["values"].__setitem__(0, False))

    def test_string_read_value_rejected(self):
        self.mutation(lambda samples, summary: samples[0]["values"].__setitem__(0, "0"))

    def test_missing_round_rejected(self):
        self.mutation(lambda samples, summary: samples.pop())

    def test_duplicate_round_rejected(self):
        self.mutation(lambda samples, summary: samples[1].__setitem__("round", 0))

    def test_reordered_rounds_rejected(self):
        self.mutation(lambda samples, summary: samples.reverse())

    def test_summary_histogram_recomputed_from_samples(self):
        def mutate(samples, summary):
            summary["histogram"]["0000"] = 0
            summary["histogram"]["0101"] = 1
        self.mutation(mutate)

    def test_weak_count_recomputed_from_samples(self):
        self.mutation(lambda samples, summary: summary.__setitem__("weakOutcomeCount", 0),
                      outcomes=("1010", "1111"))

    def test_fenced_summary_cannot_hide_forbidden_sample(self):
        def mutate(samples, summary):
            summary["forbidden"] = 0
            summary["weakOutcomeCount"] = 0
        self.mutation(mutate, variant="iriw-fenced", outcomes=("1010", "1111"))

    def test_same_cache_line_rejected(self):
        self.mutation(lambda samples, summary: samples[0].__setitem__("yOffset", samples[0]["xOffset"]))

    def test_same_reader_hart_rejected(self):
        self.mutation(lambda samples, summary: samples[0].__setitem__("readerHarts", [1, 1]))

    def test_missing_hart_banner_rejected(self):
        def mutate(folder, samples, summary):
            log = folder / "run.log"
            log.write_text(log.read_text().replace("NATIVE_LITMUS_HART_3_PASS completed=2\n", ""))
        self.assertFalse(self.check(mutate))

    def test_wrong_hart_completed_count_rejected(self):
        def mutate(folder, samples, summary):
            log = folder / "run.log"
            log.write_text(log.read_text().replace("HART_3_PASS completed=2", "HART_3_PASS completed=1"))
        self.assertFalse(self.check(mutate))

    def test_timeout_rejected_despite_pass_field(self):
        def mutate(folder, samples, summary):
            path = folder / "native-l2-result.json"
            result = json.loads(path.read_text())
            result["cause"] = "simulate() limit reached"
            path.write_text(json.dumps(result))
        self.assertFalse(self.check(mutate))

    def test_classic_bridge_configuration_rejected(self):
        def mutate(folder, samples, summary):
            path = folder / "config.ini"
            path.write_text(path.read_text() + "\n[system.bridge]\ntype=Cache2ChiBridge\n")
        self.assertFalse(self.check(mutate))

    def test_opcode_injection_rejected(self):
        def mutate(folder, samples, summary):
            path = folder / "native-l2-result.json"
            result = json.loads(path.read_text())
            result["protocolOpcodeInjection"] = True
            path.write_text(json.dumps(result))
        self.assertFalse(self.check(mutate))


if __name__ == "__main__":
    unittest.main()
