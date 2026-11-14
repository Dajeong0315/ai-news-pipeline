"""Channel-wide OS lock for scheduled generation and publishing on this PC."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', choices=['economy', 'youth'], required=True)
    parser.add_argument('mode', choices=['auto', 'cards', 'approved', 'nextday', 'today', 'digest'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    os.chdir(root)
    (root/'logs').mkdir(exist_ok=True)
    with (root/'logs'/f'{args.channel}.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        # 2026-11-14: 10분마다 도는 게시(approved)가 정각에 잠금을 먼저 잡으면 12:00·19:00 카드 만들기가 건너뛰어졌다.
        # 게시는 지금처럼 바로 건너뛰고, 카드 만들기(auto/cards)는 최대 10분 기다린다.
        wait_until = time.monotonic() + (600 if args.mode in ('auto', 'cards', 'nextday', 'today', 'digest') else 0)
        waited = False
        while True:
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= wait_until:
                    print('Channel already running; skipping.')
                    return 0
                if not waited:
                    print('Channel busy; waiting for lock (max 10 min)...', flush=True)
                waited = True
                time.sleep(5)
        env = dict(os.environ, CHANNEL=args.channel, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
        command = (['publish_instagram.py', '--approved-only'] if args.mode == 'approved'
                   else ['approval_digest.py'] if args.mode == 'digest'
                   else ['run_daily.py', 'nextday', 'today'] if args.mode == 'today' else ['run_daily.py', args.mode])
        return subprocess.call([sys.executable, *command], cwd=root, env=env)

if __name__ == '__main__':
    sys.exit(main())
