#!/usr/bin/env python
import argparse, json, os, shutil, sys, time
from datetime import datetime, timezone
from pathlib import Path

from mojokernel.lsp_client import MojoLSPClient, completion_matches, completion_metadata, hover_text, offset_to_lsp_position, signature_text


def _utc_stamp(): return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

def _default_out(): return Path(__file__).resolve().parents[1] / 'meta' / f'lsp-explore-{_utc_stamp()}.json'

def _items(raw):
    if isinstance(raw, list): return len(raw)
    if isinstance(raw, dict) and isinstance(raw.get('items'), list): return len(raw['items'])
    return 0

def _completion_provider(capabilities):
    if not isinstance(capabilities, dict): return {}
    p = capabilities.get('completionProvider')
    if not isinstance(p, dict): return {}
    trigger = p.get('triggerCharacters') if isinstance(p.get('triggerCharacters'), list) else []
    return dict(triggerCharacters=trigger, resolveProvider=bool(p.get('resolveProvider')))

def _notification_summary(msgs):
    out = {}
    for msg in msgs:
        key = msg.get('method', '<no-method>') if isinstance(msg, dict) else '<non-dict>'
        out[key] = out.get(key, 0) + 1
    return out

def _diagnostic_messages(msgs):
    out = []
    for msg in msgs:
        if not isinstance(msg, dict) or msg.get('method') != 'textDocument/publishDiagnostics': continue
        ds = msg.get('params', {}).get('diagnostics')
        if not isinstance(ds, list): continue
        out += [d.get('message', '').strip() for d in ds if isinstance(d, dict) and isinstance(d.get('message'), str) and d.get('message').strip()]
    return out

def _cursor(marked):
    pos = marked.find('|')
    if pos < 0: raise ValueError("probe text must include '|' cursor marker")
    return marked[:pos] + marked[pos+1:], pos

def _wrap_in_fn(text, pos, fn_name='main'):
    marker = '__LSP_CURSOR__'
    marked = text[:pos] + marker + text[pos:]
    wrapped = f"fn {fn_name}():\n" + '\n'.join(f'    {o}' if o else '' for o in marked.split('\n'))
    wpos = wrapped.find(marker)
    return wrapped.replace(marker, ''), wpos

def _augment(case):
    if not case.get('ok'): return case
    raw = case.get('raw')
    if isinstance(raw, list) or (isinstance(raw, dict) and 'items' in raw):
        case['parsed_matches'] = completion_matches(raw)
        case['parsed_metadata'] = completion_metadata(raw, 0, 0)
        case['parsed_items_count'] = _items(raw)
    elif isinstance(raw, dict) and 'contents' in raw: case['parsed_hover_text'] = hover_text(raw)
    elif isinstance(raw, dict) and 'signatures' in raw: case['parsed_signature_text'] = signature_text(raw)
    return case

def _is_outdated(err): return '-32801' in err and 'outdated request' in err


def _run_case(name, fn, probe, retries=4, retry_delay=0.25):
    t0,attempts = time.time(),[]
    for i in range(retries + 1):
        try:
            raw = fn()
            out = dict(name=name, probe=probe, ok=True, elapsed_s=round(time.time() - t0, 3), raw=raw)
            if attempts: out['attempts'] = attempts
            return out
        except Exception as e:
            err = repr(e)
            attempts.append(dict(attempt=i+1, error=err))
            if i < retries and _is_outdated(err):
                time.sleep(retry_delay)
                continue
            return dict(name=name, probe=probe, ok=False, elapsed_s=round(time.time() - t0, 3), error=err, attempts=attempts)


class ExploreLSPClient(MojoLSPClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rpc_trace, self.server_messages, self.last_initialize_result = [], [], {}

    def _request(self, method, params, timeout=None):
        t0,rec = time.time(),dict(kind='request', method=method, params=params)
        try:
            res = super()._request(method, params, timeout=timeout)
            rec.update(ok=True, elapsed_s=round(time.time() - t0, 3), result=res)
            if method == 'initialize' and isinstance(res, dict): self.last_initialize_result = res
            return res
        except Exception as e:
            rec.update(ok=False, elapsed_s=round(time.time() - t0, 3), error=repr(e))
            raise
        finally: self.rpc_trace.append(rec)

    def _notify(self, method, params):
        t0,rec = time.time(),dict(kind='notify', method=method, params=params)
        try:
            out = super()._notify(method, params)
            rec.update(ok=True, elapsed_s=round(time.time() - t0, 3))
            return out
        except Exception as e:
            rec.update(ok=False, elapsed_s=round(time.time() - t0, 3), error=repr(e))
            raise
        finally: self.rpc_trace.append(rec)

    def _handle_message(self, msg):
        if 'id' not in msg or 'method' in msg: self.server_messages.append(msg)
        return super()._handle_message(msg)


def _completion_call(client, text, pos, context=None):
    if context is None: return lambda: client.complete(text, pos)
    def _call():
        client.update_document(text)
        line,char = offset_to_lsp_position(text, pos)
        params = dict(textDocument=dict(uri=client._doc_uri), position=dict(line=line, character=char), context=context)
        return client._request('textDocument/completion', params, timeout=client.request_timeout)
    return _call


def _run_suite(name, include_dirs, request_timeout, shutdown_timeout, doc_uri=None):
    suite = dict(name=name, startup=dict(ok=False), shutdown=dict(ok=False), cases=[], rpc_trace=[], server_messages=[])
    client = ExploreLSPClient(include_dirs=include_dirs, request_timeout=request_timeout, shutdown_timeout=shutdown_timeout)
    if doc_uri: client._doc_uri = doc_uri

    try:
        t0 = time.time()
        client.start()
        init = client.last_initialize_result if isinstance(client.last_initialize_result, dict) else {}
        caps = init.get('capabilities') if isinstance(init.get('capabilities'), dict) else {}
        suite['startup'] = dict(
            ok=True,
            elapsed_s=round(time.time() - t0, 3),
            pid=client.pid,
            cmd=client._build_cmd(),
            doc_uri=client._doc_uri,
            supports_did_change=client._supports_did_change,
            completion_provider=_completion_provider(caps),
            server_capabilities=caps,
        )

        def run(name, fn, probe):
            n0 = len(client.server_messages)
            out = _augment(_run_case(name, fn, probe))
            msgs = client.server_messages[n0:]
            out['message_summary'] = _notification_summary(msgs)
            out['diagnostic_messages'] = _diagnostic_messages(msgs)
            return out

        list_file_dot,list_file_dot_pos = _cursor('var list = [2, 3, 5]\nlist.|')
        list_file_pref,list_file_pref_pos = _cursor('var list = [2, 3, 5]\nlist.s|')
        list_main_dot,list_main_dot_pos = _wrap_in_fn(list_file_dot, list_file_dot_pos, 'main')
        list_main_pref,list_main_pref_pos = _wrap_in_fn(list_file_pref, list_file_pref_pos, 'main')
        list_fn_dot,list_fn_dot_pos = _wrap_in_fn(list_file_dot, list_file_dot_pos, '__cell_probe')
        list_fn_pref,list_fn_pref_pos = _wrap_in_fn(list_file_pref, list_file_pref_pos, '__cell_probe')

        str_file_dot,str_file_dot_pos = _cursor('var s = "aa"\ns.|')
        str_file_pref,str_file_pref_pos = _cursor('var s = "aa"\ns.up|')
        str_main_dot,str_main_dot_pos = _wrap_in_fn(str_file_dot, str_file_dot_pos, 'main')
        str_main_pref,str_main_pref_pos = _wrap_in_fn(str_file_pref, str_file_pref_pos, 'main')

        text_user = 'fn myadd(a: Int, b: Int) -> Int:\n    return a + b\nmya'
        ctx_dot = dict(triggerKind=2, triggerCharacter='.')

        specs = [
            ('complete:global:pri', 'pri', 3, None),
            ('complete:user-defined:mya', text_user, len(text_user), None),
            ('complete:list:file.dot@default', list_file_dot, list_file_dot_pos, None),
            ('complete:list:file.dot@trigger-dot', list_file_dot, list_file_dot_pos, ctx_dot),
            ('complete:list:file.prefix@default', list_file_pref, list_file_pref_pos, None),
            ('complete:list:main.dot@default', list_main_dot, list_main_dot_pos, None),
            ('complete:list:main.dot@default#2', list_main_dot, list_main_dot_pos, None),
            ('complete:list:main.dot@trigger-dot', list_main_dot, list_main_dot_pos, ctx_dot),
            ('complete:list:main.prefix@default', list_main_pref, list_main_pref_pos, None),
            ('complete:list:main.prefix@default#2', list_main_pref, list_main_pref_pos, None),
            ('complete:list:fn.dot@default', list_fn_dot, list_fn_dot_pos, None),
            ('complete:list:fn.prefix@default', list_fn_pref, list_fn_pref_pos, None),
            ('complete:str:file.dot@default', str_file_dot, str_file_dot_pos, None),
            ('complete:str:file.prefix@default', str_file_pref, str_file_pref_pos, None),
            ('complete:str:main.dot@default', str_main_dot, str_main_dot_pos, None),
            ('complete:str:main.dot@default#2', str_main_dot, str_main_dot_pos, None),
            ('complete:str:main.prefix@default', str_main_pref, str_main_pref_pos, None),
            ('complete:str:main.prefix@default#2', str_main_pref, str_main_pref_pos, None),
        ]
        suite['cases'] = [run(n, _completion_call(client, t, p, c), dict(text=t, cursor=p, context=c) if c else dict(text=t, cursor=p)) for n,t,p,c in specs]
        suite['cases'].append(run('hover:global:print', lambda: client.hover('print', 2), dict(text='print', cursor=2)))
        suite['cases'].append(run('signature:global:print(', lambda: client.signature_help('print(', 6), dict(text='print(', cursor=6)))
    except Exception as e: suite['startup'] = dict(ok=False, error=repr(e), doc_uri=doc_uri or '')
    finally:
        try:
            client.shutdown()
            suite['shutdown'] = dict(ok=True)
        except Exception as e: suite['shutdown'] = dict(ok=False, error=repr(e))
        suite['rpc_trace'] = client.rpc_trace
        suite['server_messages'] = client.server_messages
        suite['server_message_summary'] = _notification_summary(client.server_messages)
        suite['diagnostic_messages'] = _diagnostic_messages(client.server_messages)
        suite['stderr_tail'] = list(client._stderr_tail)

    ok = sum(1 for o in suite['cases'] if o.get('ok'))
    suite['summary'] = dict(total_cases=len(suite['cases']), ok_cases=ok, failed_cases=len(suite['cases']) - ok)
    return suite


def _diagnose(suites):
    def case_map(s): return {o.get('name'): o for o in s.get('cases', []) if isinstance(o, dict) and o.get('name')}
    def items(cases, name): return _items(cases.get(name, {}).get('raw'))
    def diags(cases, names): return [m for n in names for m in cases.get(n, {}).get('diagnostic_messages', [])]
    def has_scope(diag): return any(('file scope' in o) or ('global vars are not supported' in o) for o in diag)

    file_names = ('complete:list:file.dot@default', 'complete:list:file.dot@trigger-dot', 'complete:list:file.prefix@default', 'complete:str:file.dot@default', 'complete:str:file.prefix@default')
    main_names = ('complete:list:main.dot@default', 'complete:list:main.dot@trigger-dot', 'complete:list:main.prefix@default', 'complete:str:main.dot@default', 'complete:str:main.prefix@default')

    per = []
    for suite in suites:
        c = case_map(suite)
        file_diag,main_diag = diags(c, file_names),diags(c, main_names)
        per.append(dict(
            name=suite.get('name'),
            completion_provider=suite.get('startup', {}).get('completion_provider', {}),
            global_pri_items=items(c, 'complete:global:pri'),
            user_defined_items=items(c, 'complete:user-defined:mya'),
            list_file_dot_items=items(c, 'complete:list:file.dot@default'),
            list_file_dot_trigger_items=items(c, 'complete:list:file.dot@trigger-dot'),
            list_file_prefix_items=items(c, 'complete:list:file.prefix@default'),
            list_main_dot_items=items(c, 'complete:list:main.dot@default'),
            list_main_dot_trigger_items=items(c, 'complete:list:main.dot@trigger-dot'),
            list_main_prefix_items=items(c, 'complete:list:main.prefix@default'),
            list_fn_dot_items=items(c, 'complete:list:fn.dot@default'),
            list_fn_prefix_items=items(c, 'complete:list:fn.prefix@default'),
            str_file_dot_items=items(c, 'complete:str:file.dot@default'),
            str_file_prefix_items=items(c, 'complete:str:file.prefix@default'),
            str_main_dot_items=items(c, 'complete:str:main.dot@default'),
            str_main_prefix_items=items(c, 'complete:str:main.prefix@default'),
            file_scope_parse_errors=has_scope(file_diag),
            main_scope_parse_errors=has_scope(main_diag),
            file_scope_diagnostic_examples=sorted(set(file_diag))[:6],
            main_scope_diagnostic_examples=sorted(set(main_diag))[:6],
        ))

    dot_ctx_helps = any((o['list_file_dot_trigger_items'] > o['list_file_dot_items']) or (o['list_main_dot_trigger_items'] > o['list_main_dot_items']) for o in per)
    main_wrapper_helps = any((o['list_main_dot_items'] > o['list_file_dot_items']) or (o['list_main_prefix_items'] > o['list_file_prefix_items']) or (o['str_main_dot_items'] > o['str_file_dot_items']) or (o['str_main_prefix_items'] > o['str_file_prefix_items']) for o in per)
    fn_wrapper_helps = any((o['list_fn_dot_items'] > o['list_file_dot_items']) or (o['list_fn_prefix_items'] > o['list_file_prefix_items']) for o in per)

    real_uri_helps = False
    if len(per) >= 2:
        keys = ('list_file_dot_items', 'list_file_prefix_items', 'list_main_dot_items', 'list_main_prefix_items', 'list_fn_dot_items', 'list_fn_prefix_items', 'str_file_dot_items', 'str_file_prefix_items', 'str_main_dot_items', 'str_main_prefix_items')
        a,b = per[0],per[1]
        real_uri_helps = sum(b.get(k, 0) for k in keys) > sum(a.get(k, 0) for k in keys)

    file_scope_parse_errors_present = any(o.get('file_scope_parse_errors', False) for o in per)
    main_scope_parse_errors_present = any(o.get('main_scope_parse_errors', False) for o in per)

    likely = 'server_returns_empty_completion_items'
    if dot_ctx_helps: likely = 'missing_completion_context_in_request'
    elif main_wrapper_helps or fn_wrapper_helps: likely = 'function_scope_wrapper_enables_completion'
    elif file_scope_parse_errors_present and not main_scope_parse_errors_present: likely = 'lsp_parses_notebook_cells_as_file_scope'
    elif file_scope_parse_errors_present and main_scope_parse_errors_present: likely = 'lsp_returns_empty_items_even_in_function_scope'
    elif real_uri_helps: likely = 'virtual_document_uri_behavior'

    return dict(
        per_suite=per,
        dot_context_changes_results=dot_ctx_helps,
        main_wrapper_changes_results=main_wrapper_helps,
        fn_wrapper_changes_results=fn_wrapper_helps,
        real_uri_changes_results=real_uri_helps,
        file_scope_parse_errors_present=file_scope_parse_errors_present,
        main_scope_parse_errors_present=main_scope_parse_errors_present,
        main_wrapper_hypothesis_supported=main_wrapper_helps or fn_wrapper_helps,
        likely_cause=likely,
    )


def _write_report(out, data):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=False, default=str) + '\n')


def main():
    p = argparse.ArgumentParser(description='Explore Mojo LSP behavior and save a JSON report.')
    p.add_argument('--out', type=Path, default=_default_out(), help='Output JSON path (default: meta/lsp-explore-<timestamp>.json)')
    p.add_argument('--request-timeout', type=float, default=5.0, help='LSP request timeout in seconds')
    p.add_argument('--shutdown-timeout', type=float, default=1.0, help='LSP shutdown timeout in seconds')
    p.add_argument('-I', '--include-dir', action='append', default=[], help='Extra include dir passed to mojo-lsp-server')
    args = p.parse_args()

    report = dict(
        generated_utc=datetime.now(timezone.utc).isoformat(),
        cwd=str(Path.cwd()),
        python=sys.executable,
        which_mojo_lsp_server=shutil.which('mojo-lsp-server'),
        env=dict(MOJO_LSP_SERVER=os.environ.get('MOJO_LSP_SERVER', ''), MOJO_KERNEL_LSP=os.environ.get('MOJO_KERNEL_LSP', '')),
        client=dict(request_timeout=args.request_timeout, shutdown_timeout=args.shutdown_timeout, include_dirs=args.include_dir),
    )

    virtual = _run_suite('virtual-uri', args.include_dir, args.request_timeout, args.shutdown_timeout)
    real_doc = args.out.parent / f'_lsp-explore-session-{_utc_stamp()}.mojo'
    real_doc.parent.mkdir(parents=True, exist_ok=True)
    real_doc.write_text('// temporary exploration document\n')
    real = _run_suite('real-uri', args.include_dir, args.request_timeout, args.shutdown_timeout, doc_uri=real_doc.resolve().as_uri())

    report['suites'] = [virtual, real]
    report['diagnosis'] = _diagnose(report['suites'])
    report['summary'] = dict(total_suites=len(report['suites']), out=str(args.out), likely_cause=report['diagnosis']['likely_cause'])
    _write_report(args.out, report)
    print(f'Wrote {args.out}')
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__': main()
