"""Report contract v3.1: fix backend provenance without editing candidate-2.

The report uses the frozen candidate-2 analysis. Its identity and validation are
separately versioned so original registrations and reports remain immutable.
"""
from copy import deepcopy
from pathlib import Path

import jsonschema
from research.validation import digest, stable_hash
from research.project_compatibility import check as historical_check
from . import backend_identity, research_v3_report as old, research_v3_contract as c


def methods():
    return {**c.methods(), **{f'extensions/{n}': digest(c.BASE / n) for n in ('backend_identity.py', 'research_v3_report_v2.py')}}


def schema(value):
    spec = deepcopy(c.spec('report'))
    definition = spec['$defs']['report']
    definition['properties']['contract'] = {'const': 'issuer-research-report-v3.1'}
    definition['properties']['inference_kind'] = {'enum': sorted(backend_identity.KINDS)}
    definition['properties']['backend_identity'] = {'type': 'object'}
    definition['required'].append('backend_identity')
    jsonschema.Draft202012Validator(spec, format_checker=jsonschema.FormatChecker()).validate(value)
    backend_identity.validate(value['backend_identity'])
    if value['inference_kind'] != value['backend_identity']['backend']:
        raise ValueError('REPORT_BACKEND_MISMATCH')


def build(*args, backend=None, **kwargs):
    before = getattr(backend, 'calls', 0)
    value, ai = old.build(*args, backend=backend, **kwargs)
    identity = backend_identity.capture(backend, calls_before=before)
    value.update(contract='issuer-research-report-v3.1', backend_identity=identity,
                 inference_kind=identity['backend'], method_hashes=methods())
    value['record_sha256'] = stable_hash({k: v for k, v in value.items() if k != 'record_sha256'})
    schema(value)
    return value, ai


def write(value, ai, output):
    schema(value)
    root = Path(output).resolve()
    if not root.is_relative_to((old.ROOT / 'reports/private').resolve()):
        raise ValueError('REPORT_PRIVATE_OUTPUT_REQUIRED')
    folder = root / value['run_id']
    folder.mkdir(parents=True, exist_ok=False)
    from .research_v3_eval import write_once
    write_once(folder / 'RESEARCH_V3_1.json', value)
    write_once(folder / 'SOURCE_INPUT.json', ai)
    rendered = old.render(value, ai)
    rendered += '\nBackend: ' + value['inference_kind'] + ' / ' + value['backend_identity']['execution_status'] + '\n'
    (folder / 'REPORT.ko.md').write_text(rendered, encoding='utf-8')
    if value['legacy_component']:
        (folder / 'LEGACY.ko.md').write_bytes((Path(value['legacy_component']['folder']) / 'REPORT.ko.md').read_bytes())
    write_once(folder / 'FILES.json', {p.name: digest(p) for p in folder.iterdir()})
    return folder


def validate(folder):
    folder = Path(folder)
    value = c.read(folder / 'RESEARCH_V3_1.json')
    schema(value)
    if value['run_id'] != folder.name or value['method_hashes'] != methods() or value['record_sha256'] != stable_hash({k: v for k, v in value.items() if k != 'record_sha256'}):
        raise ValueError('REPORT_IDENTITY_OR_METHOD_CHANGED')
    origin = value['origin']
    historical_check(origin['baseline_path'], expected_sha256=origin['baseline_sha256'])
    ai = old.analysis_contract.export_ai(origin['input_folder'], clock=lambda: value['generated_at'])
    if ai != c.read(folder / 'SOURCE_INPUT.json') or digest(Path(origin['input_folder']) / 'AI_INPUT.json') != origin['input_sha256'] or value['identity'] != ai['identity'] or value['evidence_as_of'] != ai['as_of']:
        raise ValueError('REPORT_SOURCE_CHANGED')
    if value['model_calls'] != value['backend_identity']['request_attempts']:
        raise ValueError('REPORT_CALL_COUNTS_CHANGED')
    if not old.as_utc(ai['as_of']) <= old.as_utc(value['started_at']) <= old.as_utc(value['generated_at']):
        raise ValueError('REPORT_TIME_CHANGED')
    if value['configuration'] != value['analysis']['configuration'] or value['candidate_registration']['requested'] != (value['mode'] == 'v3_candidate'):
        raise ValueError('REPORT_CONFIGURATION_CHANGED')
    settings = c.runtime_settings(value['configuration'])
    if value['model'] != {k: settings[k] for k in value['model']}:
        raise ValueError('REPORT_MODEL_CHANGED')
    identity = value['backend_identity']
    if identity['execution_status'] == 'ACTUAL_MODEL_EXECUTED' and any(identity['model'][k] != value['model'][k] for k in identity['model']):
        raise ValueError('REPORT_BACKEND_MODEL_CHANGED')
    old.replay_analysis(ai, value['analysis'])
    if value['legacy_component'] != old.legacy_component(origin['baseline_path'], ai):
        raise ValueError('REPORT_LEGACY_CHANGED')
    expected_render = old.render(value, ai) + '\nBackend: ' + value['inference_kind'] + ' / ' + value['backend_identity']['execution_status'] + '\n'
    if (folder / 'REPORT.ko.md').read_text(encoding='utf-8') != expected_render:
        raise ValueError('REPORT_RENDER_CHANGED')
    files = c.read(folder / 'FILES.json')
    expected = {'RESEARCH_V3_1.json', 'SOURCE_INPUT.json', 'REPORT.ko.md'} | ({'LEGACY.ko.md'} if value['legacy_component'] else set())
    if set(files) != expected or set(p.name for p in folder.iterdir()) != expected | {'FILES.json'} or any(digest(folder / n) != sha for n, sha in files.items()):
        raise ValueError('REPORT_FILES_CHANGED')
    return value


def main(argv=None):
    import argparse
    import json
    from research.local_files import LockBusy
    from . import local_inference as cpu, gpu_inference as gpu
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--validate', type=Path)
    parser.add_argument('--backend', choices=['not_run', 'cpu', 'cuda'], default='not_run')
    parser.add_argument('--configuration', choices=list('ABCDE'), default='A')
    args = parser.parse_args(argv)
    if args.validate:
        value = validate(args.validate)
        print(json.dumps({'status': 'VALID', 'backend': value['inference_kind']}))
        return 0
    if not args.baseline or not args.output or not (args.input or args.ticker):
        parser.error('--baseline, --output and --input/--ticker are required')
    source = args.input or old.weighted_engine.resolve(args.ticker.upper())
    kwargs = {'configuration': args.configuration}
    try:
        if args.backend == 'not_run':
            value, ai = build(source, args.baseline, **kwargs)
        else:
            server = gpu.Server(configuration=args.configuration) if args.backend == 'cuda' else cpu.Server(settings=c.runtime_settings(args.configuration))
            with server:
                value, ai = build(source, args.baseline, backend=server, **kwargs)
    except (cpu.Deferred, OSError, LockBusy) as error:
        reason = str(error) if isinstance(error, cpu.Deferred) else 'LOCAL_RESOURCE_UNAVAILABLE'
        value, ai = build(source, args.baseline, inference_reason=reason, **kwargs)
        identity = value['backend_identity']
        identity.update(requested_backend=args.backend, reason=reason)
        identity['record_sha256'] = stable_hash({k: v for k, v in identity.items() if k != 'record_sha256'})
        value['record_sha256'] = stable_hash({k: v for k, v in value.items() if k != 'record_sha256'})
    folder = write(value, ai, args.output)
    validate(folder)
    print(json.dumps({'status': 'SHADOW_REPORT_SAVED', 'path': str(folder), 'backend': value['inference_kind'],
                      'execution': value['backend_identity']['execution_status'], 'model_calls': value['model_calls']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
