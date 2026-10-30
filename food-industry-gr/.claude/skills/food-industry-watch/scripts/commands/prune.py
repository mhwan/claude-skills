"""fiw.py prune — remove the article bodies and excerpts of a published run (and its export chunks)."""
from __future__ import annotations

from pathlib import Path

from lib import accum, cli, gates, layout, schema

from .ingest import IngestError, read_chunk


def register(sub):
    p = sub.add_parser('prune', help='remove article bodies and excerpts of a published run')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--chunks', nargs='+', default=[], metavar='DIR',
                   help='folders holding saved export chunks (tool results) to delete')
    p.add_argument('--apply', action='store_true', help='delete (default: print what would change)')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


def chunk_files(dirs) -> list:
    """Files directly in the given folders that are export chunks (identified by content, never by name)."""
    out = []
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            raise cli.UsageError(f'--chunks {d}: not a folder')
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            try:
                read_chunk(f)
            except (IngestError, OSError, UnicodeDecodeError):
                continue
            out.append(f)
    return out


def _stage_logs(rd) -> list:
    logs = layout.logs_dir(rd)
    return sorted(logs.glob('*.txt')) if logs.is_dir() else []


def _part_files(rd, name) -> list:
    parts = rd / 'work' / 'parts'
    return sorted(parts.glob(f'*/{name}')) if parts.is_dir() else []


def _todo_text(todo) -> tuple:
    """(bodies, flow excerpts) still in a fiw.todo/1 object."""
    groups = todo['issues'] + todo['market']
    return (sum(1 for g in groups for a in g['articles'] if a['body']),
            sum(1 for g in groups for f in g.get('flow_candidates', []) if f['excerpt']))


def _empty_todo(todo):
    for group in todo['issues'] + todo['market']:
        for a in group['articles']:
            a['body'] = ''
        for f in group.get('flow_candidates', []):
            f['excerpt'] = ''


def scan(rd, dirs) -> dict:
    """What still holds article text: bodies.json, todo bodies and excerpts, bigkinds excerpts, export chunks,
    the pipeline's stage logs (a failing stage may quote a body), and the summary parts (each part's bodies.md,
    its todo.json copy of the bodies and the facts.json quotes)."""
    found = {'bodies_file': int((rd / 'work' / 'bodies.json').is_file()), 'todo_bodies': 0, 'flow_excerpts': 0,
             'bigkinds_excerpts': 0, 'chunks': len(chunk_files(dirs)), 'stage_logs': len(_stage_logs(rd)),
             'part_bodies': len(_part_files(rd, 'bodies.md')), 'part_todo_bodies': 0, 'fact_quotes': 0}
    todo = gates.load_work(rd, 'todo', 'fiw.todo/1', required=False)
    if todo is not None:
        found['todo_bodies'], found['flow_excerpts'] = _todo_text(todo)
    for f in _part_files(rd, 'todo.json'):
        found['part_todo_bodies'] += sum(_todo_text(schema.load(f, 'fiw.todo/1')))
    for f in _part_files(rd, 'facts.json'):
        facts = schema.load(f, 'fiw.facts/1')
        found['fact_quotes'] += sum(1 for a in facts['articles'] for x in a.get('facts') or [] if x['quote'])
    env_path = layout.collect_dir(rd) / 'bigkinds.json'
    if env_path.is_file():
        env = schema.load(env_path, 'fiw.collect/1')
        found['bigkinds_excerpts'] = sum(1 for a in env['items'] if a['excerpt'])
    return found


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    runs = accum.load_state('runs.json')
    entry = next((r for r in (runs or {}).get('runs', []) if r['run_id'] == manifest['run_id']), None)
    if entry is None or not entry['report']:
        print(f'prune: refused — run {manifest["run_id"]} is not published; bodies are needed until publish')
        return cli.EXIT_FAIL
    before = scan(rd, args.chunks)
    if not args.apply:
        if args.json:
            cli.print_json({'exit': cli.EXIT_OK, 'applied': False, 'found': before})
        else:
            print('prune: would remove — ' + ', '.join(f'{k} {v}' for k, v in before.items()))
            print('prune: preview only — nothing changed (add --apply)')
        return cli.EXIT_OK

    bodies = rd / 'work' / 'bodies.json'
    if bodies.is_file():
        bodies.unlink()
    todo = gates.load_work(rd, 'todo', 'fiw.todo/1', required=False)
    if todo is not None:
        _empty_todo(todo)
        schema.dump(rd / 'work' / 'todo.json', todo, 'fiw.todo/1')
    for f in _part_files(rd, 'todo.json'):
        ptodo = schema.load(f, 'fiw.todo/1')
        if any(_todo_text(ptodo)):
            _empty_todo(ptodo)
            schema.dump(f, ptodo, 'fiw.todo/1')
    for f in _part_files(rd, 'facts.json'):
        facts = schema.load(f, 'fiw.facts/1')
        if any(x['quote'] for a in facts['articles'] for x in a.get('facts') or []):
            for a in facts['articles']:
                for x in a.get('facts') or []:
                    x['quote'] = ''           # the fact text is the writer's own words; the quote is the body's
            schema.dump(f, facts, 'fiw.facts/1')
    env_path = layout.collect_dir(rd) / 'bigkinds.json'
    if env_path.is_file():
        env = schema.load(env_path, 'fiw.collect/1')
        for a in env['items']:
            a['excerpt'] = ''
        schema.dump(env_path, env, 'fiw.collect/1')
    for f in chunk_files(args.chunks) + _stage_logs(rd) + _part_files(rd, 'bodies.md'):
        f.unlink()

    after = scan(rd, args.chunks)
    left = sum(after.values())
    code = cli.EXIT_FAIL if left else cli.EXIT_OK
    if args.json:
        cli.print_json({'exit': code, 'applied': True, 'removed': before, 'left': after})
    else:
        print('prune: removed — ' + ', '.join(f'{k} {v}' for k, v in before.items()))
        print('본문 0건' if not left else 'prune: still present — ' + ', '.join(f'{k} {v}' for k, v in after.items()))
        print(f'prune: exit {code}')
    return code
