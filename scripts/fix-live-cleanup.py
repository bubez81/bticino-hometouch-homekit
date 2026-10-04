#!/usr/bin/env python3
"""Narrow, backed-up correction of the installed IPC graceful-stop timeout."""
import ast
import shutil
import tempfile
from pathlib import Path

target = Path('/opt/bticino-sniffer/bticino_ipc.py')
old = target.read_text()
before = '_call_process.terminate()\n            try:\n                _call_process.wait(timeout=2)'
after = before.replace('timeout=2', 'timeout=8')
if after in old:
    print('ALREADY_APPLIED')
else:
    if old.count(before) != 1:
        raise SystemExit('Unexpected installed code; no changes made')
    updated = old.replace(before, after)
    ast.parse(updated)
    backup = Path(tempfile.mkdtemp(prefix='ipc-cleanup-', dir='/opt/bticino-sniffer/backups'))
    shutil.copy2(target, backup / target.name)
    target.write_text(updated)
    print(f'BACKUP={backup}')
    print('GRACEFUL_STOP_TIMEOUT=8')
