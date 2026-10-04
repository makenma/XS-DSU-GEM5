"""Synthetic orchestration/negative tests, never actual Linux completion evidence."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('native_linux_complete', ROOT / 'scripts/check-native-linux-complete.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class NativeLinuxCompleteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.core, self.kernel, self.random = (self.base/name for name in ('core','kernel','random'))
        for directory in (self.core,self.kernel,self.random): directory.mkdir()
        (self.core/'host-exit-code.txt').write_text('0\n')
        self.driver_path = self.base/'synthetic-driver.json'
        self.driver = {'source':'exec-session-completion','sessionId':30470,
                       'core':str(self.core),'kernelBuild':str(self.kernel),
                       'execResult':{'exit_code':0,'output':'synthetic unit fixture only'},
                       'observedAtUtc':'2026-10-04T01:00:00Z'}
        self.save_driver()
        self.core_report = {'status':'PASS','passed':True,'knownFailure':False,
                            'failures':[],'incomplete':[],
                            'runs':{name:{'directory':str(directory),'passed':True,
                                          'result':{'gem5Sha256':'a'*64}}
                                    for name,directory in (('core',self.core),('kernel-build',self.kernel))},
                            'requestCoverage':{'complete':True,'missing':[],
                                               'counts':dict.fromkeys(CHECKER.REQUESTS,'1')}}
        self.random_report = {'status':'PASS','passed':True,'knownFailure':False,
                              'failures':[],'incomplete':[],'gem5Sha256':'a'*64,
                              'directory':str(self.random)}
        self.base_check = Mock(side_effect=lambda *args:copy.deepcopy(self.core_report))
        self.random_check = Mock(side_effect=lambda *args:copy.deepcopy(self.random_report))
        self.modules = {'check-native-linux-validation.py':SimpleNamespace(check=self.base_check),
                        'check-native-linux-random-memory.py':SimpleNamespace(check=self.random_check)}

    def save_driver(self):
        self.driver_path.write_text(json.dumps(self.driver),encoding='utf-8')

    def run_check(self):
        with patch.object(CHECKER,'module',side_effect=lambda name:self.modules[name]):
            return CHECKER.check(self.core,self.kernel,self.random,self.driver_path)

    def rejected(self, known=True):
        report=self.run_check()
        self.assertFalse(report['passed'],report)
        if known: self.assertTrue(report['knownFailure'],report)
        return report

    def test_complete_composition_calls_both_actual_recheck_entrypoints(self):
        report=self.run_check()
        self.assertTrue(report['passed'],report)
        self.base_check.assert_called_once_with(self.core,self.kernel)
        self.random_check.assert_called_once_with(self.random)
        self.assertTrue(report['checks']['driverCompletion']['passed'])

    def test_two_original_runs_alone_are_incomplete(self):
        self.random_report.update(status='INCOMPLETE',passed=False,incomplete=['not run'])
        report=self.rejected(known=False)
        self.assertEqual(report['status'],'INCOMPLETE')

    def test_missing_real_driver_completion_is_incomplete(self):
        self.driver_path.unlink()
        report=self.rejected(known=False)
        self.assertEqual(report['status'],'INCOMPLETE')
        self.assertFalse(report['knownFailure'])

    def test_running_exec_session_never_passes(self):
        self.driver['execResult']={'session_id':30470,'exit_code':None,'output':'still running'}
        self.save_driver()
        self.assertEqual(self.rejected(known=False)['status'],'INCOMPLETE')

    def test_exit_zero_with_remaining_session_is_incomplete(self):
        self.driver['execResult']['session_id']=30470
        self.save_driver()
        self.assertEqual(self.rejected(known=False)['status'],'INCOMPLETE')

    def test_nonzero_or_noninteger_driver_exit_is_known_failure(self):
        for value in (1,134,-9,'0',False,0.0):
            with self.subTest(value=repr(value)):
                self.driver['execResult']={'exit_code':value}
                self.save_driver()
                self.rejected()

    def test_nonzero_driver_failure_survives_other_incompleteness(self):
        self.driver['execResult']['exit_code']=134
        self.save_driver()
        self.random_report.update(status='INCOMPLETE',passed=False,incomplete=['not run'])
        report=self.rejected()
        self.assertEqual(report['status'],'INCOMPLETE')
        self.assertTrue(any('driver exited' in detail for detail in report['failures']))

    def test_driver_tool_error_survives_zero_exit(self):
        self.driver['execResult']['error']='transport failure'
        self.save_driver(); self.rejected()
        self.driver['execResult']={'exit_code':0,'isError':True}
        self.save_driver(); self.rejected()

    def test_wrong_source_session_path_or_timestamp_rejected(self):
        original=copy.deepcopy(self.driver)
        variants=[('source','shell-footer'),('sessionId',1),('sessionId','30470'),
                  ('core',str(self.random)),('kernelBuild',str(self.core)),('core','relative/core'),
                  ('observedAtUtc','2026-10-04T01:00:00'),('observedAtUtc','bad'),
                  ('observedAtUtc','2026-10-04T01:00:00+08:00')]
        for key,value in variants:
            with self.subTest(key=key,value=value):
                self.driver=copy.deepcopy(original); self.driver[key]=value
                self.save_driver(); self.rejected()

    def test_malformed_duplicate_or_invented_footer_cannot_pass(self):
        for text in ('{}','[]','broken','{"source":"exec-session-completion","source":"fake"}',
                     '{"execResult":{"exit_code":NaN}}'):
            with self.subTest(text=text):
                self.driver_path.write_text(text,encoding='utf-8'); self.rejected()

    def test_core_host_process_requires_its_own_actual_zero(self):
        for value in ('1\n','134\n','00\n','PASS\n'):
            with self.subTest(value=value):
                (self.core/'host-exit-code.txt').write_text(value)
                self.rejected()
        (self.core/'host-exit-code.txt').unlink()
        self.assertEqual(self.rejected(known=False)['status'],'INCOMPLETE')

    def test_all_three_model_identities_must_match(self):
        self.random_report['gem5Sha256']='b'*64
        self.rejected()
        self.random_report['gem5Sha256']='a'*64
        self.core_report['runs']['kernel-build']['result']['gem5Sha256']='b'*64
        self.rejected()

    def test_missing_model_identity_cannot_be_hidden_by_pass(self):
        self.random_report.pop('gem5Sha256')
        self.rejected()

    def test_actual_base_twelve_request_coverage_required(self):
        original=copy.deepcopy(self.core_report['requestCoverage'])
        for request in CHECKER.REQUESTS:
            with self.subTest(request=request):
                self.core_report['requestCoverage']=copy.deepcopy(original)
                self.core_report['requestCoverage']['counts'][request]='0'
                self.rejected()
        self.core_report['requestCoverage']=copy.deepcopy(original)
        self.core_report['requestCoverage']['counts'].pop('ReadUnique')
        self.rejected()
        self.core_report['requestCoverage']=copy.deepcopy(original)
        self.core_report['requestCoverage']['counts']['ReadUnique']=1
        self.rejected()

    def test_lower_known_failure_is_not_hidden_by_pass_or_incomplete(self):
        self.random_report.update(knownFailure='false')
        self.rejected()
        self.random_report.update(knownFailure=True)
        self.rejected()
        self.random_report.update(status='INCOMPLETE',passed=False,incomplete=['unfinished'])
        self.assertEqual(self.rejected()['status'],'INCOMPLETE')

    def test_nested_run_and_directory_identity_required(self):
        self.core_report['runs']['core']['passed']=False
        self.rejected()
        self.core_report['runs']['core']['passed']=True
        self.random_report['directory']=str(self.core)
        self.rejected()

    def test_checker_exceptions_become_known_failures(self):
        self.base_check.side_effect=KeyError('broken census')
        report=self.rejected()
        self.assertTrue(any('KeyError' in detail for detail in report['failures']))
        self.random_check.assert_called_once()

    def test_existing_pass_files_are_never_used_as_results(self):
        for directory in (self.core,self.kernel,self.random):
            (directory/'validation.json').write_text('{"status":"PASS","passed":true}')
        self.core_report.update(status='INCOMPLETE',passed=False,incomplete=['no actual execution'])
        self.rejected(known=False)
        self.base_check.assert_called_once()
        self.random_check.assert_called_once()

    def test_real_entrypoints_missing_execution_and_driver_remain_incomplete(self):
        # No mock here: genuine composed checkers see only empty run directories.
        # There is deliberately no fabricated driver completion artifact.
        self.driver_path.unlink()
        report=CHECKER.check(self.core,self.kernel,self.random,self.driver_path)
        self.assertFalse(report['passed'],report)
        self.assertEqual(report['status'],'INCOMPLETE')
        self.assertFalse(report['checks']['driverCompletion']['passed'])


if __name__ == '__main__':
    unittest.main()
