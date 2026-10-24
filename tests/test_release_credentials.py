# 待确认：真实令牌权限；后续研究：以实际报告上传验证写入能力。
# 风险/优化/验证：空值提前失败、两项独立检查、子进程只接收当前令牌、不输出值或 API 正文。
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_release_credentials as credentials


class CredentialTests(unittest.TestCase):
    def check(self, environment, results):
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(sys, 'argv', ['check', '--release-url', 'https://github.com/o/r/releases/tag/v1']), \
                patch.object(credentials.subprocess, 'run', side_effect=results) as client, \
                contextlib.redirect_stdout(output):
            status = credentials.main()
        return status, client.call_args_list, output.getvalue()

    def test_both_missing_never_calls_api(self):
        status, calls, output = self.check({}, [])
        self.assertEqual(status, 1)
        self.assertEqual(calls, [])
        self.assertIn('READONLY is unavailable', output)
        self.assertIn('WRITEONLY is unavailable', output)

    def test_checks_are_independent_and_isolated(self):
        failure = subprocess.CompletedProcess([], 1)
        success = subprocess.CompletedProcess([], 0)
        status, calls, output = self.check({'READ_TOKEN': 'READ_VALUE', 'WRITE_TOKEN': 'WRITE_VALUE'},
                                          [failure, success])
        self.assertEqual(status, 1)
        self.assertEqual([call.kwargs['env'] for call in calls],
                         [{'GH_TOKEN': 'READ_VALUE'}, {'GH_TOKEN': 'WRITE_VALUE'}])
        self.assertNotIn('READ_VALUE', output)
        self.assertNotIn('WRITE_VALUE', output)

    def test_read_checks_do_not_claim_write_permission(self):
        success = subprocess.CompletedProcess([], 0)
        status, calls, output = self.check({'READ_TOKEN': 'READ_VALUE', 'WRITE_TOKEN': 'WRITE_VALUE'},
                                          [success, success])
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn('upload permission awaits an actual upload', output)


if __name__ == '__main__':
    unittest.main()
