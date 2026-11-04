"""Channel-wide OS lock for scheduled generation and publishing on this PC."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', choices=['economy', 'youth'], required=True)
    parser.add_argument('mode', choices=['auto', 'cards', 'approved'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    os.chdir(root)
    (root/'logs').mkdir(exist_ok=True)
    with (root/'logs'/f'{args.channel}.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('Channel already running; skipping.')
            return 0
        env = dict(os.environ, CHANNEL=args.channel, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
        command = ['publish_instagram.py', '--approved-only'] if args.mode == 'approved' else ['run_daily.py', args.mode]
        return subprocess.call([sys.executable, *command], cwd=root, env=env)

if __name__ == '__main__':
    sys.exit(main())
