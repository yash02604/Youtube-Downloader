"""Build-only packaging of bgutil's immutable, lockfile-backed Deno provider.

The Python plugin is installed separately by uv. No source or dependency download
belongs in the request path. Native canvas is installed for the build platform;
build this bundle on the same OS/architecture as the deployed function.
"""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


PROVIDER_VERSION = '2.0.2'
PROVIDER_COMMIT = '26475e9d3665b972c4aeb83b4dbaafcf64b88438'
ARCHIVE_SHA256 = 'a8144eab6a4f38f320063688531f461afe18c0a58be2dd3152b780f4851e954d'
ARCHIVE_ROOT = f'bgutil-ytdlp-pot-provider-{PROVIDER_COMMIT}'
ARCHIVE_URL = (
    'https://codeload.github.com/Brainicism/bgutil-ytdlp-pot-provider/'
    f'tar.gz/{PROVIDER_COMMIT}'
)
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_BYTES = 50 * 1024 * 1024
REQUIRED_FILES = (
    'deno.json', 'deno.lock', 'package.json', 'package-lock.json',
    'src/generate_once.ts', 'src/session_manager.ts', 'src/utils.ts',
)

# Upstream's plugin inherits stderr, whose exception text can include session
# data. Keep its subprocess private and disallow runtime dependency downloads.
# The executable path is resolved by the shell, never interpolated by Python.
PRIVATE_RUNTIME_SCRIPT = '''#!/bin/sh
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if [ "$1" = run ]; then
    shift
    exec "$base/deno" run --cached-only --frozen "$@" 2>/dev/null
else
    exec "$base/deno" "$@" 2>/dev/null
fi
'''


def package_private_runtime(deno):
    """Write the Linux function's private provider launcher beside real Deno."""
    deno = Path(deno)
    if not deno.is_file():
        raise RuntimeError('Packaged Deno runtime is unavailable at build time')
    target = deno.with_name('deno-private')
    target.write_bytes(PRIVATE_RUNTIME_SCRIPT.encode('utf-8'))
    target.chmod(0o755)
    return target


def extract_provider(archive, destination):
    """Verify before writing, then copy only regular server files and license."""
    if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError('Provider source archive checksum mismatch')
    destination = Path(destination)
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
        selected = []
        total = 0
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.name
                    or not path.parts or path.parts[0] != ARCHIVE_ROOT):
                raise RuntimeError('Unsafe provider source archive path')
            parts = path.parts[1:]
            if not parts or (parts[0] != 'server' and parts != ('LICENSE',)):
                continue
            if not (member.isdir() or member.isfile()):
                raise RuntimeError('Unsafe provider source archive member')
            if member.isdir():
                continue
            relative = Path(*parts[1:]) if parts[0] == 'server' else Path('LICENSE')
            if not relative.parts or ':' in str(relative):
                raise RuntimeError('Unsafe provider source archive path')
            total += member.size
            if total > MAX_SOURCE_BYTES:
                raise RuntimeError('Provider source archive exceeds size limit')
            selected.append((member, relative))
        for member, relative in selected:
            target = destination / relative
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError('Unsafe provider source archive path')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as stream, target.open('wb') as output:
                shutil.copyfileobj(stream, output)
    for relative in (*REQUIRED_FILES, 'LICENSE'):
        if not (destination / relative).is_file():
            raise RuntimeError('Pinned provider source is incomplete')
    return destination


def _run(command, *, cwd, env, timeout):
    try:
        return subprocess.run(
            command, cwd=cwd, env=env, timeout=timeout, check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Dependency output can contain local paths and environment details.
        detail = str(getattr(exc, 'stderr', '') or '').lower()
        category = 'UNCLASSIFIED'
        if any(marker in detail for marker in ('err_dlopen_failed', 'libcairo', 'cannot open shared object')):
            category = 'NATIVE_LIBRARY_UNAVAILABLE'
        elif 'lockfile' in detail or 'frozen' in detail:
            category = 'LOCKFILE_OR_CACHE_UNAVAILABLE'
        elif 'module not found' in detail or 'cannot find module' in detail:
            category = 'MODULE_UNAVAILABLE'
        stage = 'DEPENDENCY_INSTALL' if len(command) > 1 and command[1] == 'install' else 'OFFLINE_IMPORT'
        raise RuntimeError(f'Pinned provider {stage} failed ({category})') from None


def package_provider(deno, destination=None):
    deno = Path(deno).resolve()
    if not deno.is_file():
        raise RuntimeError('Packaged Deno runtime is unavailable at build time')
    destination = (Path(destination) if destination else
                   Path(__file__).parent / '_runtime' / 'bgutil').resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response:
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
    except (OSError, ValueError):
        raise RuntimeError('Pinned provider source download failed') from None
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise RuntimeError('Provider source archive exceeds size limit')
    with tempfile.TemporaryDirectory(prefix='bgutil-build-', dir=destination.parent) as temporary:
        staging = Path(temporary)
        server = extract_provider(archive, staging / 'server')
        cache = staging / 'deno_dir'
        cache.mkdir()
        token_cache = staging / 'verification-cache'
        token_cache.mkdir()
        env = dict(os.environ, DENO_DIR=str(cache), XDG_CACHE_HOME=str(token_cache),
                   DENO_NO_PROMPT='1', DENO_NO_UPDATE_CHECK='1', FORCE_COLOR='false')
        _run([str(deno), 'install', '--allow-scripts=npm:canvas', '--frozen'],
             cwd=server, env=env, timeout=600)
        if not (server / 'node_modules').is_dir():
            raise RuntimeError('Pinned provider dependencies were not packaged')
        # Eagerly import native canvas and all script dependencies, without giving
        # the smoke check network permission or generating any token.
        result = _run([
            str(deno), 'run', '--cached-only', '--frozen', '--allow-env',
            f'--allow-ffi={server / "node_modules"}',
            f'--allow-read={token_cache},{server / "node_modules"}',
            f'--allow-write={token_cache}',
            str(server / 'src' / 'generate_once.ts'), '--version',
        ], cwd=server, env=env, timeout=60)
        if result.stdout.strip() != PROVIDER_VERSION:
            raise RuntimeError('Packaged provider version does not match its plugin')
        shutil.rmtree(token_cache)
        if destination.exists():
            shutil.rmtree(destination)
        # Move the contents, keeping TemporaryDirectory's cleanup root in place.
        destination.mkdir()
        for item in staging.iterdir():
            shutil.move(str(item), str(destination / item.name))
        # Validate the final paths too: Deno/native caches must survive the
        # relocation used when the deployment bundle is assembled.
        final_cache = staging / 'final-verification-cache'
        final_cache.mkdir()
        final_server = destination / 'server'
        final_env = dict(env, DENO_DIR=str(destination / 'deno_dir'), XDG_CACHE_HOME=str(final_cache))
        result = _run([
            str(deno), 'run', '--cached-only', '--frozen', '--allow-env',
            f'--allow-ffi={final_server / "node_modules"}',
            f'--allow-read={final_cache},{final_server / "node_modules"}',
            f'--allow-write={final_cache}',
            str(final_server / 'src' / 'generate_once.ts'), '--version',
        ], cwd=final_server, env=final_env, timeout=60)
        if result.stdout.strip() != PROVIDER_VERSION:
            raise RuntimeError('Relocated provider version does not match its plugin')
    return destination / 'server'
