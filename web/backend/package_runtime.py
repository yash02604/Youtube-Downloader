"""Build-only relocation of the pinned Deno wheel executable into the function."""
from pathlib import Path
import shutil


def package_runtime(destination=None):
    from deno import find_deno_bin
    source = Path(find_deno_bin())
    target = Path(destination) if destination else Path(__file__).parent / '_runtime' / 'deno'
    if not source.is_file():
        raise RuntimeError('Pinned web JavaScript runtime is unavailable at build time')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if not target.stat().st_mode & 0o111:
        target.chmod(target.stat().st_mode | 0o111)
    return target


if __name__ == '__main__':
    package_runtime()
    print('Packaged pinned web JavaScript runtime')
