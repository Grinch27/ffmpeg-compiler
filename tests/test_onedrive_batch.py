# 待确认：真实 OneDrive/OAuth；此处只用 mock 验证批处理边界，不能替代云端验收。
# 风险/优化：重点验证凭据隔离、部分失败保留成果及临时文件清理。
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import onedrive_batch as batch

CONFIG = '[od]\ntype = onedrive\naccess_scopes = Files.Read offline_access User.Read\ntoken = MOCK_SECRET\ndrive_id = mock\n'


def entry(name='sample.mp4'):
    return {'Name': name, 'Path': name, 'Size': 4, 'IsDir': False, 'ModTime': '2026-01-01T00:00:00Z'}


class BatchTests(unittest.TestCase):
    def test_listing_boundaries(self):
        self.assertEqual(batch.select_files([entry('b.MP4'), entry('a.mp4'), entry('note.txt')], 2)[0]['Name'], 'a.mp4')
        for entries in ([entry('../x.mp4')], [entry('a.mp4'), entry('b.mp4')], [], [entry('a.mp4'), entry('a.mp4')]):
            with self.assertRaises(ValueError):
                batch.select_files(entries, 1)

    def test_config_constraints(self):
        batch.validate_config(CONFIG)
        for bad in (CONFIG.replace('Files.Read ', 'Files.ReadWrite '), CONFIG+'root_folder_id = other\n', CONFIG+'[extra]\ntype = local\n'):
            with self.assertRaises(ValueError):
                batch.validate_config(bad)

    def test_partial_failure_and_secret_isolation(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            workspace = root / 'workspace'; workspace.mkdir()
            temporary = root / 'temp'; temporary.mkdir()
            previous = Path.cwd()
            configs = []

            def remote(config, *args):
                configs.append(config)
                self.assertFalse(config.is_relative_to(workspace))
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                if args[0] == 'copyto':
                    Path(args[2]).write_bytes(b'data')
                    return ''
                if '--stat' in args:
                    return json.dumps(entry(args[1].split('/')[-1]))
                return json.dumps([entry('good.mp4'), entry('bad.mp4')])

            def encode(command, env):
                self.assertNotIn('ONEDRIVE_RCLONE_CONFIG', env)
                self.assertNotIn('GH_TOKEN', env)
                dest = Path(command[command.index('--output-dir') + 1]); dest.mkdir()
                # First sorted input fails; second succeeds and must still be retained.
                if dest.name == '0001':
                    return type('Result', (), {'returncode': 1})()
                (dest/'output_av1.mp4').write_bytes(b'av1')
                (dest/'report.json').write_text(json.dumps({'status':'success','output_bytes':3,'saved_percent':25,'encode_seconds':1}))
                return type('Result', (), {'returncode': 0})()

            try:
                os.chdir(workspace)
                with patch.dict(os.environ, {'ONEDRIVE_RCLONE_CONFIG': CONFIG, 'RUNNER_TEMP': str(temporary), 'GH_TOKEN':'SECRET'}, clear=True), patch.object(sys, 'argv', ['batch']), patch.object(batch, 'remote_call', remote), patch.object(batch.subprocess, 'run', encode), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(batch.main(), 1)
                result = json.loads((workspace/'output/batch.json').read_text())
                self.assertEqual((result['success_count'], result['failed_count']), (1, 1))
                self.assertTrue((workspace/'output/0002/output_av1.mp4').exists())
                self.assertFalse(any(p.exists() for p in configs))
                self.assertEqual(list(workspace.glob('onedrive-input-*')), [])
                self.assertNotIn('MOCK_SECRET', (workspace/'output/batch.json').read_text())
            finally:
                os.chdir(previous)

    def test_rclone_error_is_redacted(self):
        result = type('Result', (), {'returncode': 1, 'stderr': 'TOKEN=PRIVATE', 'stdout': ''})()
        with patch.object(batch.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'check authorization') as error:
                batch.remote_call(Path('/tmp/config'), 'lsjson', 'od:ffmpeg')
        self.assertNotIn('PRIVATE', str(error.exception))


if __name__ == '__main__':
    unittest.main()
