from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path
from .engine import Engine, MODES
from .errors import FDTError
from .model import Twin
from .store import TwinStore
from .util import read_json, write_json


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog='fdt',description='KeyFin FDT numeric-only engine (offline)')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list-modes',help='5개 모드 확인')
    b=sub.add_parser('build',help='CSV로 사용자 Twin 생성')
    b.add_argument('--csv',action='append',required=True)
    b.add_argument('--db',required=True);b.add_argument('--snapshot');b.add_argument('--as-of');b.add_argument('--out')
    i=sub.add_parser('inspect',help='현재 상태/행동 모델/관계 확인')
    i.add_argument('--db',required=True);i.add_argument('--out')
    r=sub.add_parser('run',help='정형 request 또는 명시적 mode 실행')
    r.add_argument('--db',required=True)
    sel=r.add_mutually_exclusive_group(required=True)
    sel.add_argument('--request');sel.add_argument('--mode',choices=list(MODES))
    r.add_argument('--horizon-days',type=int);r.add_argument('--paths',type=int);r.add_argument('--seed',type=int);r.add_argument('--out')
    u=sub.add_parser('update',help='LIVE 이벤트 batch 갱신')
    u.add_argument('--db',required=True);u.add_argument('--events',required=True)
    u.add_argument('--expected-revision',type=int,required=True);u.add_argument('--out')
    return p


def main(argv: list[str] | None=None) -> int:
    args=parser().parse_args(argv)
    try:
        if args.command=='list-modes':
            result={'modes':MODES,'natural_language_router':False}
        elif args.command=='build':
            twin=Twin.from_csv(args.csv,snapshot=read_json(args.snapshot) if args.snapshot else None,as_of=args.as_of)
            TwinStore(Path(args.db).resolve()).create(twin); result=twin.inspect()
        elif args.command=='inspect':
            result=TwinStore(Path(args.db).resolve()).load().inspect()
        elif args.command=='run':
            request=read_json(args.request) if args.request else {'mode':args.mode}
            if not isinstance(request,dict): raise FDTError('INVALID_REQUEST','request는 JSON object여야 합니다.')
            for k in ('horizon_days','paths','seed'):
                if getattr(args,k) is not None: request[k]=getattr(args,k)
            result=Engine(TwinStore(Path(args.db).resolve()).load()).run(request)
        else:
            result=TwinStore(Path(args.db).resolve()).update(read_json(args.events),args.expected_revision).inspect()
        if getattr(args,'out',None):
            write_json(args.out,result)
            print(json.dumps({'status':'written','path':str(args.out)},ensure_ascii=False))
        else: print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 0
    except FDTError as e:
        print(json.dumps(e.as_dict(),ensure_ascii=False)); return 2
    except (OSError,sqlite3.Error) as e:
        print(json.dumps(FDTError('IO_ERROR',str(e)).as_dict(),ensure_ascii=False));return 2

if __name__=='__main__':
    raise SystemExit(main())
