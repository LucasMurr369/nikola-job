#!/usr/bin/env python
"""Detached launcher — starts NIKOLA in a new process group so it survives terminal session cleanup."""
import subprocess, sys, os

os.chdir(r'C:\Users\mrluk\nikola')
log_path = r'C:\Users\mrluk\nikola\nikola_live.log'

# Try to remove old log (may be locked by zombie handle from killed process)
try:
    os.remove(log_path)
except Exception:
    pass

# If still locked, use alternate name
try:
    log = open(log_path, 'w')
except PermissionError:
    log_path = r'C:\Users\mrluk\nikola\nikola_go.log'
    log = open(log_path, 'w')

p = subprocess.Popen(
    [r'C:\Users\mrluk\AppData\Local\Programs\Python\Python312\python.exe',
     '-u', 'nikola.py', '--live', '--cycles', '9999'],
    stdout=log,
    stderr=subprocess.STDOUT,
    cwd=r'C:\Users\mrluk\nikola',
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    close_fds=True,
)

log.write(f'\n[launcher] NIKOLA started: PID={p.pid}, PGID={p.pid}, log={log_path}\n')
log.flush()
print(f'NIKOLA launched: PID={p.pid} (detached via CREATE_NEW_PROCESS_GROUP)')
log.close()
sys.exit(0)
