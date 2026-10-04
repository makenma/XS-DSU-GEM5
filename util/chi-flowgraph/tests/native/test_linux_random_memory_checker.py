"""Synthetic checker mutation tests; these are not guest execution evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('random_memory_checker', ROOT / 'scripts/check-linux-random-memory.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def fixture(runtime=384):
    """Build bounded synthetic records with independently adjustable measured work."""
    requirements = {
        'threads': 4, 'writers': 3, 'writerIds': [1, 2, 3], 'minimumIterations': 128,
        'minimumPagesPerWriter': 64, 'minimumMemoryNumaNodes': 2, 'movesPerRound': 3,
        'allSelectedMovesMustChangeNode': True, 'allPoolPagesMustBeMigrated': True,
        'minimumOverlappingAttempts': 1, 'minimumWindowWritesPerWriter': 1,
        'minimumRuntimeWritesPerWriter': 1, 'minimumPressureBytesPerWriter': 1048576,
        'minimumPressureSamplesPerEpoch': 64, 'minimumDistinctRuntimePressureLinesPerWriter': 64,
        'pressureLineBytes': 64, 'pressureMappingsPerWriter': 2,
        'fullPressureInitializationAndFinalByteCheck': True, 'finalPoolCheckAfterAllWritersJoined': True,
        'allByteErrorsMustBeZero': True, 'guestTimeoutRequired': True, 'unsupportedIsPass': False}
    manifest = {'schemaVersion': 1, 'outputSchemaVersion': 1, 'suite': CHECKER.SUITE,
                'sourceSha256': 'a'*64, 'binary': 'random-memory',
                'binarySha256': hashlib.sha256(b'SYNTHETIC-NOT-AN-ELF').hexdigest(),
                'defaultParameters': dict(CHECKER.PARAMS), 'requirements': requirements,
                'assemblyChecks': dict.fromkeys(('rv64Elf','linuxEcall','sourceTargetRv64gc',
                                                 'noAdvertisedVectorOrZbb'), True)}
    begin = {'schemaVersion': 1, 'suite': CHECKER.SUITE, 'buildId': 'a'*64,
             **CHECKER.PARAMS, 'pageBytes': 4096, 'threadsRequested': 4,
             'affinityCpus': [0,1,2,3], 'numaMemoryNodes': [0,1],
             'prng': 'xorshift32', 'selector': 'per-writer-fisher-yates-v1'}
    moves = []
    selections = list(CHECKER.selections(1,128,64))
    for index, (rnd,slot,writer,page) in enumerate(selections):
        moves.append({'round':rnd,'slot':slot,'writer':writer,'page':page,
                      'beforeNode':index%2,'targetNode':1-index%2,'afterNode':1-index%2,
                      'queryBeforeRc':0,'moveRc':0,'queryAfterRc':0,'pageStatus':1-index%2,
                      'errorNumber':0,'status':'PASS',
                      'completedBefore':[min(index,runtime)]*3,
                      'completedAfter':[min(index+1,runtime)]*3})
    pages, writers = [], []
    for writer in range(1,4):
        for page in range(64):
            epoch = 1 + runtime//64 + (page < runtime%64)
            pages.append({'writer':writer,'page':page,'epoch':epoch,'bytes':4096,'byteErrors':0,
                          'fnv64':CHECKER.page_digest(1,writer,page,epoch,4096)})
        writers.append({'writer':writer,'cpu':writer,'pinError':0,'pagesWritten':64+runtime,
                        'runtimePagesWritten':runtime,'distinctRuntimePages':min(64,runtime),
                        'epochSum':64+runtime,'finalPageByteErrors':0,'mainPageByteErrors':0,
                        'pressureAllocatedBytes':1048576,'pressureInitializedBytes':1048576,
                        'pressureInitializationChecks':1048576,'pressureFinalChecks':1048576,
                        'pressureDistinctRuntimeLines':min(runtime*64,16384),
                        'pressureRuntimeWrites':runtime*64,'pressureRuntimeAliasChecks':runtime*4096,
                        'pressureByteErrors':0,'migrationWindowWrites':min(runtime,384)})
    end = {'status':'PASS','exitCode':0,'threadsStarted':4,'verifierCpu':0,
           'threadCpuIds':[0,1,2,3],'threadPinErrors':[0]*4,'migrationAttempts':384,
           'migrationsPassed':384,'distinctMigratedPages':[64]*3,'overlapAttempts':min(runtime,384),
           'writerWindowWrites':[min(runtime,384)]*3,
           'selectionDigest':CHECKER.selection_digest(selections),
           'totalPageByteErrors':0,'totalPressureByteErrors':0,'errors':0}
    return {'manifest':manifest,'begin':begin,'moves':moves,'pages':pages,'writers':writers,'end':end}


def render(data):
    records = [('BEGIN',data['begin'])] if data['begin'] is not None else []
    records += [('MIGRATION',row) for row in data['moves']]
    for index in range(3):
        records += [('PAGE',row) for row in data['pages'] if row.get('writer') == index+1]
        records += [('WRITER',row) for row in data['writers'] if row.get('writer') == index+1]
    if data['end'] is not None:
        records.append(('END',data['end']))
    return '\n'.join('RANDOM_MEMORY_'+kind+' '+json.dumps(row) for kind,row in records)+'\n'


class RandomMemoryCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture()

    def mutated(self, mutator):
        data = copy.deepcopy(self.original)
        mutator(data)
        return CHECKER.check(render(data), data['manifest'])

    def rejected(self, mutator):
        report = self.mutated(mutator)
        self.assertFalse(report['pass'], report)
        return report

    def test_complete_synthetic_fixture(self):
        result = CHECKER.check(render(self.original),self.original['manifest'])
        self.assertTrue(result['pass'],result)
        self.assertFalse(result['binaryArtifactChecked'])
        self.assertEqual(result['migrationRecords'],384)
        self.assertEqual(result['finalPageRecords'],192)
        self.assertTrue(all(x['runtimeWorkingSetExceedsL2'] for x in result['pressureWorkingSets']))

    def test_short_windows_and_small_runtime_working_set_are_reported_honestly(self):
        data=fixture(runtime=1)
        result=CHECKER.check(render(data),data['manifest'])
        self.assertTrue(result['pass'],result)
        self.assertEqual(result['overlapAttempts'],1)
        for item in result['pressureWorkingSets']:
            self.assertEqual(item['initializedBytes'],1048576)
            self.assertEqual(item['runtimeDistinctBytes'],4096)
            self.assertFalse(item['runtimeWorkingSetExceedsL2'])

    def test_no_cross_window_numa_continuity_assumption(self):
        # The kernel can migrate a page outside a measured query/MOVE/query window.
        result=self.mutated(lambda d: [r.update(beforeNode=0,targetNode=1,afterNode=1,pageStatus=1)
                                      for r in d['moves']])
        self.assertTrue(result['pass'],result)

    def test_manifest_build_requirements_cannot_be_omitted(self):
        for key in ('assemblyChecks','requirements','outputSchemaVersion','sourceSha256','binarySha256','defaultParameters'):
            with self.subTest(key=key):
                self.rejected(lambda d,k=key:d['manifest'].pop(k))

    def test_manifest_weakened_profile_rejected(self):
        self.rejected(lambda d:d['manifest']['requirements'].update(unsupportedIsPass=True))
        self.rejected(lambda d:d['manifest']['assemblyChecks'].update(linuxEcall=False))
        self.rejected(lambda d:d['manifest']['assemblyChecks'].update(rv64Elf=1))

    def test_runtime_build_and_parameters_must_match(self):
        self.rejected(lambda d:d['begin'].update(buildId='b'*64))
        for key in CHECKER.PARAMS:
            with self.subTest(key=key):
                self.rejected(lambda d,k=key:d['begin'].__setitem__(k,d['begin'][k]+1))

    def test_minimum_work_cannot_be_overridden(self):
        for params in ({'iterations':127},{'pagesPerWriter':63},{'pressureBytesPerWriter':4096},
                       {'pressureSamplesPerEpoch':1},{'seed':0},{'seed':True},{'unknown':1}):
            with self.subTest(params=params):
                self.assertFalse(CHECKER.check(render(self.original),self.original['manifest'],params)['pass'])

    def test_all_four_affinities_and_cross_core_verifier_required(self):
        mutations=[lambda d:d['begin'].update(affinityCpus=[0,1,1,3]),
                   lambda d:d['end'].update(threadsStarted=3),
                   lambda d:d['end'].update(threadCpuIds=[0,1,3,2]),
                   lambda d:d['end'].update(threadPinErrors=[0,0,1,0]),
                   lambda d:d['end'].pop('verifierCpu'),
                   lambda d:d['end'].update(verifierCpu=1),
                   lambda d:d['writers'][0].update(cpu=0)]
        for mutation in mutations:
            self.rejected(mutation)

    def test_missing_boundary_is_incomplete(self):
        for key in ('begin','end'):
            result=self.rejected(lambda d,k=key:d.__setitem__(k,None))
            self.assertEqual(result['status'],'INCOMPLETE')

    def test_unsupported_is_never_pass(self):
        result=self.rejected(lambda d:d['end'].update(status='UNSUPPORTED',exitCode=2))
        self.assertEqual(result['status'],'INCOMPLETE')

    def test_success_footer_cannot_hide_abort_duplicate_or_malformed_records(self):
        for extra in ('RANDOM_MEMORY_ABORT {"status":"TIMEOUT","exitCode":124}',
                      'RANDOM_MEMORY_END '+json.dumps(self.original['end']),
                      'RANDOM_MEMORY_FAKE {}','RANDOM_MEMORY_MIGRATION {broken',
                      'RANDOM_MEMORY_MIGRATION {"round":0,"round":1}',
                      'RANDOM_MEMORY_MIGRATION {"round":NaN}'):
            with self.subTest(extra=extra):
                self.assertFalse(CHECKER.check(render(self.original)+extra,self.original['manifest'])['pass'])

    def test_every_migration_record_and_order_are_required(self):
        self.rejected(lambda d:d['moves'].pop(0))
        self.rejected(lambda d:d['moves'].append(copy.deepcopy(d['moves'][0])))
        self.rejected(lambda d:d['moves'].reverse())

    def test_seeded_selection_and_digest_are_independently_recomputed(self):
        self.rejected(lambda d:d['moves'][0].update(page=(d['moves'][0]['page']+1)%64))
        self.rejected(lambda d:d['moves'][0].update(writer=2))
        self.rejected(lambda d:d['end'].update(selectionDigest='0'*16))
        self.rejected(lambda d:d['end'].update(distinctMigratedPages=[63,64,64]))

    def test_query_move_query_must_be_successful_cross_node(self):
        for field,value in [('beforeNode',-1),('targetNode',0),('afterNode',0),('pageStatus',-16),
                            ('queryBeforeRc',-1),('moveRc',1),('queryAfterRc',-1),
                            ('errorNumber',16),('status','FAIL'),('status','UNSUPPORTED')]:
            with self.subTest(field=field):
                self.rejected(lambda d,k=field,v=value:d['moves'][0].__setitem__(k,v))

    def test_progress_windows_cannot_be_fabricated_from_post_window_wait(self):
        self.rejected(lambda d:[r.update(completedAfter=r['completedBefore']) for r in d['moves']])
        self.rejected(lambda d:d['moves'][3].update(completedBefore=[0,0,0]))
        self.rejected(lambda d:d['moves'][0].update(completedAfter=[-1,0,0]))
        self.rejected(lambda d:d['end'].update(overlapAttempts=385))
        self.rejected(lambda d:d['end'].update(writerWindowWrites=[0,384,384]))
        self.rejected(lambda d:d['writers'][0].update(migrationWindowWrites=0))

    def test_each_writer_must_progress_inside_migration_window(self):
        def remove_writer_progress(data):
            for row in data['moves']:
                row['completedBefore'][0]=row['completedAfter'][0]=0
            data['end']['writerWindowWrites'][0]=0
            data['writers'][0]['migrationWindowWrites']=0
        self.rejected(remove_writer_progress)

    def test_all_pages_and_actual_bytes_verified(self):
        self.rejected(lambda d:d['pages'].pop())
        self.rejected(lambda d:d['pages'].append(copy.deepcopy(d['pages'][0])))
        for field,value in [('epoch',0),('bytes',64),('byteErrors',1),('fnv64','0'*16)]:
            self.rejected(lambda d,k=field,v=value:d['pages'][0].__setitem__(k,v))
        # Matching reported PASS/error=0 is insufficient if a final version/hash disagrees.
        self.rejected(lambda d:d['pages'][0].update(epoch=8))

    def test_writer_counts_final_versions_and_alias_bytes_must_reconcile(self):
        for field in ('pagesWritten','runtimePagesWritten','distinctRuntimePages','epochSum',
                      'pressureAllocatedBytes','pressureInitializedBytes','pressureInitializationChecks',
                      'pressureFinalChecks','pressureRuntimeWrites','pressureRuntimeAliasChecks',
                      'pressureDistinctRuntimeLines'):
            with self.subTest(field=field):
                self.rejected(lambda d,k=field:d['writers'][0].__setitem__(k,d['writers'][0][k]-1))
        for field in ('finalPageByteErrors','mainPageByteErrors','pressureByteErrors'):
            self.rejected(lambda d,k=field:d['writers'][0].__setitem__(k,1))

    def test_footer_failures_and_boolean_numbers_rejected(self):
        for field,value in [('status','FAIL'),('exitCode',1),('errors',1),('totalPageByteErrors',1),
                            ('totalPressureByteErrors',1),('migrationsPassed',383),('exitCode',False)]:
            self.rejected(lambda d,k=field,v=value:d['end'].__setitem__(k,v))
        self.rejected(lambda d:d['moves'][0].update(queryBeforeRc=False))
        self.rejected(lambda d:d['writers'][0].update(pinError=False))
        self.rejected(lambda d:d['pages'][0].update(byteErrors=False))

    def test_relative_binary_path_hash_and_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            binary=base/'random-memory'
            binary.write_bytes(b'SYNTHETIC-NOT-AN-ELF')
            manifest=base/'manifest.json'
            manifest.write_text(json.dumps(self.original['manifest']),encoding='utf-8')
            log=base/'serial.txt'; log.write_text(render(self.original),encoding='utf-8')
            report=base/'report.json'
            result=CHECKER.check(log.read_text(),manifest)
            self.assertTrue(result['pass'],result)
            self.assertTrue(result['binaryArtifactChecked'])
            run=subprocess.run([sys.executable,str(ROOT/'scripts/check-linux-random-memory.py'),
                                '--input',str(log),'--manifest',str(manifest),'--output',str(report)],
                               capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            binary.write_bytes(b'MUTATED')
            self.assertFalse(CHECKER.check(log.read_text(),manifest)['pass'])
            binary.unlink()
            self.assertFalse(CHECKER.check(log.read_text(),manifest)['pass'])
            escaped=copy.deepcopy(self.original['manifest']); escaped['binary']='../outside'
            manifest.write_text(json.dumps(escaped),encoding='utf-8')
            self.assertFalse(CHECKER.check(log.read_text(),manifest)['pass'])


if __name__ == '__main__':
    unittest.main()
