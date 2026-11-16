"""Destination policy hooks; engine final-output validation remains authoritative."""
import ntpath
from pathlib import Path


class DesktopDestinationPolicy:
    def resolve(self, destination):
        return str(Path(destination).absolute())


class RootedDestinationPolicy:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve(self, destination='.'):
        if not isinstance(destination, str):
            raise ValueError('destination must be relative text')
        normalized = destination.replace('\\', '/')
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
        if (ntpath.splitdrive(destination)[0] or normalized.startswith('/')
                or ':' in normalized or any(p == '..' for p in normalized.split('/'))
                or any(p.split('.')[0].upper() in reserved for p in normalized.split('/'))
                or any(p.endswith((' ', '.')) for p in normalized.split('/') if p not in {'', '.'})
                or any(ord(c) < 32 for c in normalized)):
            raise ValueError('destination must stay inside the allowed root')
        resolved = (self.root / normalized).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError('destination resolves outside the allowed root')
        return str(resolved)
