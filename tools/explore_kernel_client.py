#!/usr/bin/env python
import argparse, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty

import jupyter_client


def _utc_stamp(): return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _default_out():
    root = Path(__file__).resolve().parents[1]
    return root / 'meta' / f'kernel-client-explore-{_utc_stamp()}.json'


def _brief_iopub(msg):
    return dict(msg_type=msg.get('msg_type'), content=msg.get('content'), parent_msg_id=msg.get('parent_header', {}).get('msg_id'))


def _collect_iopub_for(kc, msg_id, timeout=20):
    deadline = time.time() + timeout
    out = []
    while time.time() < deadline:
        try: msg = kc.get_iopub_msg(timeout=1)
        except Empty: continue
        if msg.get('parent_header', {}).get('msg_id') != msg_id: continue
        out.append(_brief_iopub(msg))
        if msg.get('msg_type') == 'status' and msg.get('content', {}).get('execution_state') == 'idle': break
    return out


def _request_content(fn):
    try: return dict(ok=True, content=fn()['content'])
    except Exception as e: return dict(ok=False, error=repr(e))


def _execute_case(kc, name, code, timeout):
    t0 = time.time()
    try:
        msg_id = kc.execute(code, reply=False, allow_stdin=False, store_history=False, stop_on_error=False)
        shell = kc.get_shell_msg(timeout=timeout)
        iopub = _collect_iopub_for(kc, msg_id, timeout=timeout)
        return dict(name=name, ok=True, elapsed_s=round(time.time() - t0, 3), code=code, msg_id=msg_id, shell_content=shell.get('content', {}), iopub=iopub)
    except Exception as e: return dict(name=name, ok=False, elapsed_s=round(time.time() - t0, 3), code=code, error=repr(e))


def _write_report(out, data):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=False, default=str) + '\n')


def _run_complete_case(kc, code, cursor_pos, timeout, tag=''):
    t0 = time.time()
    row = dict(code=code, cursor_pos=cursor_pos, tag=tag)
    row.update(_request_content(lambda c=code, p=cursor_pos: kc.complete(c, p, reply=True, timeout=timeout)))
    row['elapsed_s'] = round(time.time() - t0, 3)
    c = row.get('content', {})
    row['matches_count'] = len(c.get('matches', [])) if isinstance(c, dict) else 0
    return row


def main():
    p = argparse.ArgumentParser(description='Explore jupyter_client <-> Mojo kernel behavior and save a JSON report.')
    p.add_argument('--out', type=Path, default=_default_out(), help='Output JSON path (default: meta/kernel-client-explore-<timestamp>.json)')
    p.add_argument('--kernel-name', default='mojo', help='Kernel name for KernelManager')
    p.add_argument('--timeout', type=float, default=20.0, help='Reply/message timeout in seconds')
    args = p.parse_args()

    report = dict(
        generated_utc=datetime.now(timezone.utc).isoformat(),
        cwd=str(Path.cwd()),
        python=sys.executable,
        env=dict(
        MOJO_KERNEL_ENGINE=os.environ.get('MOJO_KERNEL_ENGINE', ''),
        MOJO_KERNEL_LSP=os.environ.get('MOJO_KERNEL_LSP', ''),
        MOJO_LSP_SERVER=os.environ.get('MOJO_LSP_SERVER', ''),
    ),
        startup=dict(ok=False),
        kernel_info={},
        execute_cases=[],
        completion_cases=[],
        inspect_cases=[],
        is_complete_cases=[],
    )

    km = jupyter_client.KernelManager(kernel_name=args.kernel_name)
    kc = None
    try:
        t0 = time.time()
        km.start_kernel()
        kc = km.client()
        kc.start_channels()
        kc.wait_for_ready(timeout=args.timeout)
        report['startup'] = dict(ok=True, elapsed_s=round(time.time() - t0, 3))

        report['kernel_info'] = _request_content(lambda: kc.kernel_info(reply=True, timeout=args.timeout))

        exec_cases = [
            ('execute:print', 'print(42)'),
            ('execute:state-define', 'var _explore_v = 99'),
            ('execute:state-define-str', 'var a = "aa"'),
            ('execute:state-read', 'print(_explore_v)'),
            ('execute:error', 'print(_explore_missing_symbol_xyz)'),
            ('execute:multiline', 'fn _explore_sq(n: Int) -> Int:\n    return n*n\nprint(_explore_sq(5))'),
            ('execute:empty', ''),
        ]
        report['execute_cases'] = [_execute_case(kc, name, code, args.timeout) for name,code in exec_cases]

        comp_cases = [('pri', 3), ('print', 5), ('print(', 6), ('_explore', 8)]
        for code,cursor_pos in comp_cases:
            report['completion_cases'].append(_run_complete_case(kc, code, cursor_pos, args.timeout, tag='baseline'))

        repeated = [
            ('a.', 2, 'repeat-dot-1'),
            ('a.', 2, 'repeat-dot-2'),
            ('a.', 2, 'repeat-dot-3'),
            ('a.up', 4, 'repeat-prefix-1'),
            ('a.up', 4, 'repeat-prefix-2'),
            ('pri', 3, 'repeat-pri-after-dot'),
            ('a.', 2, 'repeat-dot-4'),
        ]
        for code,cursor_pos,tag in repeated:
            report['completion_cases'].append(_run_complete_case(kc, code, cursor_pos, args.timeout, tag=tag))

        inspect_cases = [('print(', 6), ('_explore_sq(', 12), ('_explore_v', 10)]
        for code,cursor_pos in inspect_cases:
            row = dict(code=code, cursor_pos=cursor_pos)
            row.update(_request_content(lambda c=code, p=cursor_pos: kc.inspect(c, p, detail_level=0, reply=True, timeout=args.timeout)))
            report['inspect_cases'].append(row)

        for code in ['', 'var x = 1', 'if True:']:
            row = dict(code=code)
            row.update(_request_content(lambda c=code: kc.is_complete(c, reply=True, timeout=args.timeout)))
            report['is_complete_cases'].append(row)
    except Exception as e: report['startup'] = dict(ok=False, error=repr(e))
    finally:
        try:
            if kc:
                try: kc.stop_channels()
                except Exception: pass
            km.shutdown_kernel(now=True)
            report['shutdown'] = dict(ok=True)
        except Exception as e: report['shutdown'] = dict(ok=False, error=repr(e))

    ok_exec = sum(1 for o in report['execute_cases'] if o.get('ok'))
    report['summary'] = dict(
        execute_cases=len(report['execute_cases']),
        execute_ok=ok_exec,
        completion_cases=len(report['completion_cases']),
        inspect_cases=len(report['inspect_cases']),
        is_complete_cases=len(report['is_complete_cases']),
        out=str(args.out),
    )
    _write_report(args.out, report)
    print(f"Wrote {args.out}")
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__': main()
