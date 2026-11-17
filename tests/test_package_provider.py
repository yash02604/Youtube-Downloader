import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from web.backend import package_provider as provider


def source_archive(extra=()):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        files = [(f'{provider.ARCHIVE_ROOT}/server/{name}', b'fixture')
                 for name in provider.REQUIRED_FILES]
        files += [(f'{provider.ARCHIVE_ROOT}/LICENSE', b'license'),
                  (f'{provider.ARCHIVE_ROOT}/plugin/ignored.py', b'not bundled')]
        for name, data in files + list(extra):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class ProviderPackagingTests(unittest.TestCase):
    def test_private_launcher_is_static_offline_and_suppresses_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            deno = Path(temporary) / 'deno'
            deno.write_bytes(b'fixture executable')
            wrapper = provider.package_private_runtime(deno)
            script = wrapper.read_bytes().decode('utf-8')
            self.assertEqual(wrapper, deno.with_name('deno-private'))
            self.assertEqual(script, provider.PRIVATE_RUNTIME_SCRIPT)
            self.assertTrue(script.startswith('#!/bin/sh\n'))
            self.assertNotIn('\r', script)
            self.assertIn('exec "$base/deno" run --cached-only --frozen --node-modules-dir=manual "$@" 2>/dev/null', script)
            self.assertIn('exec "$base/deno" "$@" 2>/dev/null', script)
            self.assertNotIn(str(deno), script)
            self.assertEqual(deno.read_bytes(), b'fixture executable')
            if os.name != 'nt':
                self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)

    def test_private_launcher_requires_packaged_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, 'Deno runtime is unavailable'):
                provider.package_private_runtime(Path(temporary) / 'absent-deno')

    def test_checksum_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'server'
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                provider.extract_provider(b'tampered archive', target)
            self.assertFalse(target.exists())

    def test_extracts_only_server_and_license_and_preserves_lockfiles(self):
        archive = source_archive()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                provider, 'ARCHIVE_SHA256', hashlib.sha256(archive).hexdigest()):
            target = provider.extract_provider(archive, Path(temporary) / 'server')
            self.assertEqual((target / 'deno.lock').read_bytes(), b'fixture')
            self.assertEqual((target / 'package-lock.json').read_bytes(), b'fixture')
            self.assertEqual((target / 'LICENSE').read_bytes(), b'license')
            self.assertFalse((target / 'plugin').exists())

    def test_parent_and_absolute_paths_cannot_escape_extraction(self):
        for malicious in (f'{provider.ARCHIVE_ROOT}/server/../../escape',
                          '/absolute/escape', f'{provider.ARCHIVE_ROOT}/server/C:/escape',
                          f'{provider.ARCHIVE_ROOT}/server/..\\escape'):
            with self.subTest(path=malicious), tempfile.TemporaryDirectory() as temporary:
                archive = source_archive([(malicious, b'bad')])
                with patch.object(provider, 'ARCHIVE_SHA256', hashlib.sha256(archive).hexdigest()):
                    with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                        provider.extract_provider(archive, Path(temporary) / 'server')
                self.assertFalse((Path(temporary) / 'escape').exists())

    def test_symlink_member_is_rejected(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            member = tarfile.TarInfo(f'{provider.ARCHIVE_ROOT}/server/linked')
            member.type = tarfile.SYMTYPE
            member.linkname = '../../outside'
            archive.addfile(member)
        data = output.getvalue()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                provider, 'ARCHIVE_SHA256', hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                provider.extract_provider(data, Path(temporary) / 'server')

    def test_missing_source_files_fail_build(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz'):
            pass
        data = output.getvalue()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                provider, 'ARCHIVE_SHA256', hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                provider.extract_provider(data, Path(temporary) / 'server')

    def test_missing_runtime_fails_before_any_download(self):
        with patch.object(provider.urllib.request, 'urlopen') as download:
            with self.assertRaisesRegex(RuntimeError, 'Deno runtime is unavailable'):
                provider.package_provider('absent-deno-binary')
            download.assert_not_called()

    def test_build_installs_frozen_dependencies_and_checks_version_offline(self):
        data = source_archive()
        commands = []
        def run(command, **kwargs):
            commands.append((command, kwargs))
            if command[1] == 'ci':
                (kwargs['cwd'] / 'node_modules').mkdir()
                (kwargs['cwd'] / 'node_modules' / 'runtime-asset.js').write_text('required production asset')
                duplicate = Path(kwargs['env']['DENO_DIR']) / 'npm'
                duplicate.mkdir()
                (duplicate / 'duplicate').touch()
            return SimpleNamespace(stdout='2.0.2\n')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / 'deno'
            deno.write_bytes(b'runtime fixture')
            with patch.object(provider, 'ARCHIVE_SHA256', hashlib.sha256(data).hexdigest()), \
                    patch.object(provider.urllib.request, 'urlopen', return_value=io.BytesIO(data)) as download, \
                    patch.object(provider.subprocess, 'run', side_effect=run):
                server = provider.package_provider(deno, root / 'bgutil')
            self.assertEqual(server, root / 'bgutil' / 'server')
            self.assertTrue((server / 'src' / 'generate_once.ts').is_file())
            self.assertTrue((server / 'node_modules').is_dir())
            self.assertTrue((root / 'bgutil' / 'deno_dir').is_dir())
            self.assertFalse((root / 'bgutil' / 'deno_dir' / 'npm').exists())
            self.assertEqual((server / 'node_modules' / 'runtime-asset.js').read_text(), 'required production asset')
            self.assertFalse((root / 'bgutil' / 'verification-cache').exists())
            download.assert_called_once_with(provider.ARCHIVE_URL, timeout=60)
            self.assertEqual(commands[0][0], ['npm', 'ci', '--omit=dev'])
            smoke = commands[1][0]
            self.assertIn('--cached-only', smoke)
            self.assertIn('--node-modules-dir=manual', smoke)
            self.assertNotIn('--allow-net', smoke)
            self.assertEqual(smoke[-1], '--version')
            self.assertTrue(commands[0][1]['env']['DENO_DIR'].endswith('deno_dir'))
            self.assertEqual(commands[-1][1]['cwd'], server)
            self.assertEqual(commands[-1][1]['env']['DENO_DIR'], str((root / 'bgutil' / 'deno_dir').resolve()))

    def test_subprocess_failure_does_not_leak_output(self):
        failure = subprocess.CalledProcessError(1, ['deno'], output='private path', stderr='secret')
        with patch.object(provider.subprocess, 'run', side_effect=failure):
            with self.assertRaises(RuntimeError) as error:
                provider._run(['deno'], cwd='.', env={}, timeout=1)
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('private', str(error.exception))


if __name__ == '__main__':
    unittest.main()
