import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('kernel_build', ROOT / 'scripts/check-linux-kernel-build.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def fixture():
    begin = dict(profile='tinyconfig-smp4', sourceVersion='6.6.52', jobs=4,
                 onlineCpus='0-3', architecture='riscv64', freshOutput=True,
                 compilerTarget='riscv64-linux-gnu', compilerVersion='13.3.0', startSeconds=12)
    end = dict(profile='tinyconfig-smp4', exitCode=0, target='Image', jobs=4,
               elfClass='ELF64', elfMachine='RISC-V', imageBytes=123456, objectFiles=100,
               imageSha256='a'*64, vmlinuxSha256='b'*64, configSha256='c'*64,
               makeLogSha256='d'*64, endSeconds=99)
    return '\n'.join(['CHI_KERNEL_BUILD_BEGIN ' + json.dumps(begin),
                      'CHI_KERNEL_BUILD_CPUSTAT_BEFORE',
                      *[f'cpu{i} 1 0 2 10 0 0 0 0 0 0' for i in range(4)],
                      'CHI_KERNEL_BUILD_CONFIG_READY', '  CC      init/main.o',
                      '  LD      vmlinux', '  OBJCOPY arch/riscv/boot/Image',
                      'CHI_KERNEL_BUILD_CPUSTAT_AFTER',
                      *[f'cpu{i} 11 0 22 20 0 0 0 0 0 0' for i in range(4)],
                      'CHI_KERNEL_BUILD_END ' + json.dumps(end)])


class KernelBuildCheckerTest(unittest.TestCase):
    def test_valid(self):
        result = checker.check(fixture())
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['cpuWorkTicksDelta'], {f'cpu{i}': 30 for i in range(4)})

    def test_mutations_rejected(self):
        cases = {
            'guest failure': fixture() + '\nCHI_KERNEL_BUILD_FAIL reason=make_exit_1',
            'repeated build': fixture() + '\n' + fixture(),
            'empty': '',
            'not fresh': fixture().replace('"freshOutput": true', '"freshOutput": false'),
            'wrong ISA': fixture().replace('"elfMachine": "RISC-V"', '"elfMachine": "X86-64"'),
            'host compiler': fixture().replace('riscv64-linux-gnu', 'x86_64-linux-gnu'),
            'no image': fixture().replace('"imageBytes": 123456', '"imageBytes": 0'),
            'no objects': fixture().replace('"objectFiles": 100', '"objectFiles": 0'),
            'bad hash': fixture().replace('a'*64, 'a'*63),
            'make failed': fixture().replace('"exitCode": 0', '"exitCode": 2'),
            'noninteger exit': fixture().replace('"exitCode": 0', '"exitCode": false'),
            'one job': fixture().replace('"jobs": 4', '"jobs": 1'),
            'wrong profile': fixture().replace('tinyconfig-smp4', 'defconfig'),
            'no elapsed time': fixture().replace('"endSeconds": 99', '"endSeconds": 12'),
            'nonfinite elapsed': fixture().replace('"endSeconds": 99', '"endSeconds": Infinity'),
            'no CPU3 work': fixture().replace('cpu3 11 0 22 20', 'cpu3 1 0 2 20'),
            'CPU0 counter reset': fixture().replace('cpu0 11 0 22 20', 'cpu0 0 0 0 20'),
            'missing CPU3': fixture().replace('cpu3 11 0 22 20 0 0 0 0 0 0\n', ''),
            'duplicate CPU': fixture().replace('cpu3 11', 'cpu2 11'),
            'no config': fixture().replace('CHI_KERNEL_BUILD_CONFIG_READY', 'other'),
            'no compiler log': fixture().replace('  CC      init/main.o', ''),
            'no linker log': fixture().replace('  LD      vmlinux', ''),
            'no Image log': fixture().replace('  OBJCOPY arch/riscv/boot/Image', ''),
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.assertFalse(checker.check(data)['passed'], name)


if __name__ == '__main__':
    unittest.main()
