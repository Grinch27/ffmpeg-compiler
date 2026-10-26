# 待确认：完整私有视频的实际编码时长与磁盘空间；本地单测不能替代 GitHub Actions 全片验证。
# 后续研究：下载续传和真实视频日志分析；风险/验证：0 全片、正整数限帧、负数拒绝、存储要求、私有上传。
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import release_av1 as release


def asset(name, content=b'video', identity=1):
    return {'name': name, 'id': identity, 'size': len(content), 'state': 'uploaded',
            'digest': 'sha256:' + hashlib.sha256(content).hexdigest()}


class ReleaseTests(unittest.TestCase):
    def test_frame_scope_and_disk_space(self):
        self.assertEqual(release.frame_options(0), [])
        self.assertEqual(release.frame_options(1200), ['--max-frames', '1200'])
        self.assertEqual(release.frame_options(1), ['--max-frames', '1'])
        with self.assertRaises(ValueError):
            release.frame_options(-1)
        self.assertEqual(release.disk_requirement(1000, 0), 4000 + release.ASSET_LIMIT)
        self.assertEqual(release.disk_requirement(1000, 1200), 2_000_001_000)

    def test_target_and_unsafe_hosts(self):
        self.assertEqual(release.release_target('https://github.com/o/r/releases/tag/v%2F1'), ('o/r', 'v/1'))
        for url in ('https://github.com.evil/o/r/releases/tag/v1',
                    'https://github.com@evil/o/r/releases/tag/v1', '',
                    'https://github.com/o/r/releases/tag/v1?x=1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                release.release_target(url)

    def test_selection_and_ambiguous_inputs(self):
        a, b = asset('clip.mp4.part001'), asset('clip.mp4.part002')
        chosen, split = release.source_assets([b, asset('av1-result-old.mp4'), a])
        self.assertEqual(chosen, [a, b])
        self.assertTrue(split)
        for entries in ([b], [a, a], [a, asset('clip.mp4')], [],
                        [asset('a.mp4'), asset('b.mp4')]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                release.source_assets(entries)

    def test_digest_is_required(self):
        for value in (None, '', 'md5:123'):
            item = asset('clip.mp4')
            item['digest'] = value
            with self.assertRaises(ValueError):
                release.source_assets([item])

    def test_stream_append_hashes_and_corruption(self):
        blobs = {1: b'first-', 2: b'second'}
        def download(*args, stdout, **kwargs):
            stdout.write(blobs[int(args[3].rsplit('/', 1)[1])])
            stdout.flush()
        with tempfile.TemporaryFile('w+b') as sink, patch.object(release, 'gh', download):
            combined = hashlib.sha256()
            release.download('o/r', asset('x.part001', blobs[1], 1), sink, combined)
            release.download('o/r', asset('x.part002', blobs[2], 2), sink, combined)
            sink.seek(0)
            self.assertEqual(sink.read(), b'first-second')
            self.assertEqual(combined.hexdigest(), hashlib.sha256(b'first-second').hexdigest())
            with self.assertRaises(ValueError):
                release.download('o/r', asset('x', b'broken', 1), sink)

    def test_original_checksum_is_required_and_verified(self):
        original = hashlib.sha256(b'original').hexdigest()
        text = (original + '  原始 文件.mp4\n').encode()
        item = asset('ORIGINAL_SHA256SUMS', text)
        def fake_download(repo, selected, sink):
            sink.write(text)
        with patch.object(release, 'download', fake_download):
            self.assertEqual(release.original_digest('o/r', [item]), original)
        with self.assertRaises(ValueError):
            release.original_digest('o/r', [])

    def test_read_write_and_encoder_credentials(self):
        result = subprocess.CompletedProcess([], 0, b'{}', b'')
        with patch.dict(os.environ, {'GH_TOKEN': 'READ', 'GH_RELEASE_WRITE_TOKEN': 'WRITE'}, clear=True), \
                patch.object(release.subprocess, 'run', return_value=result) as runner:
            self.assertNotIn('GH_TOKEN', release.encoder_env())
            self.assertNotIn('GH_RELEASE_WRITE_TOKEN', release.encoder_env())
            release.gh('api', 'repos/o/r')
            self.assertEqual(runner.call_args.kwargs['env'], {'GH_TOKEN': 'READ'})
            release.gh('release', 'upload', 'v1', write=True)
            self.assertEqual(runner.call_args.kwargs['env'], {'GH_TOKEN': 'WRITE'})

    def test_upload_split_and_no_clobber(self):
        sent = []
        def upload(repo, tag, path):
            sent.append((path.name, path.read_bytes()))
            return release.local_asset(path)
        with tempfile.TemporaryDirectory() as temporary, patch.object(release, 'ASSET_LIMIT', 4), \
                patch.object(release, 'publish', upload):
            target = Path(temporary) / 'video.mp4'
            target.write_bytes(b'123456789')
            result = release.upload_video('o/r', 'v1', target, 'av1-result-test')
            self.assertEqual([len(data) for _, data in sent], [4, 4, 1])
            self.assertEqual(b''.join(data for _, data in sent), target.read_bytes())
            self.assertEqual(len(result), 3)
            self.assertFalse(list(Path(temporary).glob('*.part*')))
        with tempfile.TemporaryDirectory() as temporary, patch.object(release, 'gh') as client:
            target = Path(temporary) / 'video.mp4'
            target.write_bytes(b'av1')
            release.publish('o/r', 'v1', target)
            self.assertNotIn('--clobber', client.call_args.args)
            self.assertTrue(client.call_args.kwargs['write'])

    def test_remote_digest_mismatch_fails(self):
        expected = asset('out.mp4')
        with patch.object(release, 'api', return_value=[expected]):
            release.verify_uploaded('o/r', 1, [expected])
            bad = {**expected, 'size': expected['size'] + 1}
            with self.assertRaises(ValueError):
                release.verify_uploaded('o/r', 1, [bad])

    def test_encode_failure_only_uploads_diagnostics(self):
        previous = Path.cwd()
        source = asset('source.mp4')
        def api(endpoint):
            return [source] if '/assets?' in endpoint else {'id': 1}
        def download(repo, selected, sink, combined):
            sink.write(b'video')
            combined.update(b'video')
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}, clear=True), \
                patch.object(sys, 'argv', ['release', '--release-url', 'https://github.com/o/r/releases/tag/v1']), \
                patch.object(release, 'api', api), patch.object(release, 'download', download), \
                patch.object(release.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'encode')), \
                patch.object(release, 'upload_reports', return_value=[]) as reports, \
                patch.object(release, 'upload_video') as upload, contextlib.redirect_stdout(io.StringIO()):
            try:
                os.chdir(temporary)
                with self.assertRaisesRegex(RuntimeError, 'diagnostics'):
                    release.main()
                upload.assert_not_called()
                reports.assert_called_once()
                self.assertFalse(list(Path('.').glob('release-input-*')))
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
