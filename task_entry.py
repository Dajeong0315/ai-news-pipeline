"""Write native UTF-8 logs without Windows PowerShell stderr conversion."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--channel',choices=['economy','youth'],required=True)
    parser.add_argument('mode',choices=['auto','cards','approved'])
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    log_dir=root/'logs';log_dir.mkdir(exist_ok=True)
    env=dict(os.environ,PYTHONUTF8='1',PYTHONUNBUFFERED='1')
    with (log_dir/f'{args.channel}-{args.mode}.log').open('ab') as log:
        return subprocess.call([sys.executable,str(root/'safe_runner.py'),'--channel',args.channel,args.mode],
                               stdout=log,stderr=subprocess.STDOUT,cwd=root,env=env)

if __name__=='__main__':sys.exit(main())
