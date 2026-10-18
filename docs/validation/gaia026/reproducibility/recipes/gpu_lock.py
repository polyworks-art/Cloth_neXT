"""Cooperative GPU lock between project sessions: .hydro-test-tmp/GPU_LOCK.

python gpu_lock.py acquire "<purpose>"  (waits while another holder exists)
python gpu_lock.py release
"""
import json, sys, time, datetime, os
from pathlib import Path
LOCK = Path(__file__).resolve().parents[5]/'.hydro-test-tmp/GPU_LOCK'
OWNER = 'fast-contact thread session'
COOLDOWN = LOCK.with_name('GPU_LOCK.fast-contact-released')
if sys.argv[1] == 'acquire':
    # Yield: after our own release, wait 120 s before re-acquiring so a waiting
    # session (polling every 30 s) gets the slot between our scenes.
    try:
        remaining = 120 - (time.time() - COOLDOWN.stat().st_mtime)
        if remaining > 0:
            print(f'cooldown {remaining:.0f} s', flush=True); time.sleep(remaining)
    except OSError:
        pass
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            print('waiting for', LOCK.read_text(errors='ignore').strip()[:200], flush=True); time.sleep(30); continue
        os.write(fd, json.dumps(dict(session=OWNER, purpose=sys.argv[2], started=datetime.datetime.now().isoformat())).encode()); os.close(fd)
        print('acquired', flush=True); break
elif sys.argv[1] == 'release':
    data = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    if data.get('session') == OWNER: LOCK.unlink(); COOLDOWN.write_text(str(time.time())); print('released')
    else: print('not ours, untouched:', data)
