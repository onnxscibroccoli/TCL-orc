"""JSON operator entrypoint; no implicit task execution or transaction broadcast."""
import argparse
import json
from pathlib import Path
import sqlite3
from .storage.db import StateStore


class JSONParser(argparse.ArgumentParser):
    def error(self,message):
        print(json.dumps({'event':'rejected','error':message},sort_keys=True))
        raise SystemExit(2)


def main(argv=None):
    parser=JSONParser(prog='tcl-cli')
    parser.add_argument('--state',type=Path,default=Path('.runtime/operator.sqlite'))
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('health');verify=commands.add_parser('verify');verify.add_argument('--expected-head')
    commands.add_parser('snapshot')
    apply=commands.add_parser('apply');apply.add_argument('--batch',type=Path,required=True);apply.add_argument('--expected-head',required=True)
    args=parser.parse_args(argv)
    try:
        with StateStore(args.state) as store:
            if args.command=='apply':
                if args.batch.stat().st_size>4*1024*1024:raise ValueError('batch file too large')
                head=store.apply(json.loads(args.batch.read_text()),args.expected_head)
                result={'event':'state_committed','head':head}
            elif args.command=='snapshot':
                # Verify and snapshot inside one read transaction using a dedicated API.
                result=store.verified_snapshot()
            else:result={'event':args.command,'status':'ok','head':store.verify(getattr(args,'expected_head',None))}
        print(json.dumps(result,sort_keys=True));return 0
    except (ValueError,TypeError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'event':'rejected','error':str(exc)},sort_keys=True));return 2
