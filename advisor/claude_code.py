"""Claude Code command hooks. Only allowlisted features and numbers persist."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

from .catalog import known_model
from .storage import connect, setting, set_setting, make_event, put


def session_key(data):
    # Session identifiers are not content; hash them to bound and sanitize keys.
    return 'claude-session-' + hashlib.sha256(data['session_id'].encode()).hexdigest()


def model_in(data):
    model = data.get('model')
    if isinstance(model, dict): model = model.get('id')
    return known_model(model, 'claude')


def rating_only(prompt):
    value = prompt.strip().lower()
    if value.startswith('/rate '): value = value[6:].strip()
    if not re.fullmatch(r'[+-][vouwpsc]*', value): return None
    from .labels import parse_rating
    return parse_rating(value)


def user_prompt(data):
    from .features import featurize
    from .policy import choose
    from .storage import events
    key = session_key(data)
    rating = rating_only(data['prompt'])
    with connect() as db:
        db.execute('PRAGMA busy_timeout=20')
        state = setting(db, key, {})
        if rating is not None:
            identity = state.get('last')
            raw = db.execute('SELECT row FROM events WHERE id=?', (identity,)).fetchone()
            if raw:
                event = json.loads(raw[0]); event.update(rating); put(db, event)
                state['rating'] = ('+' if rating['rating'] else '-') + ''.join(rating['tags'])
                set_setting(db, key, state)
            return {'decision':'block', 'reason':'rated' if raw else 'No completed turn to rate',
                    'systemMessage':'rated' if raw else 'No completed turn to rate', 'suppressOutput':True,
                    'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','suppressOriginalPrompt':True}}
        features = featurize(data['prompt'], fast=True)
        rows = events(db)
        option = choose(features, 'claude', exploration=0, rows=rows,
                        weights=setting(db,'personal',setting(db,'team',{})),
                        baseline=setting(db,'user_baseline',{}).get('weights',{}))
        actual = model_in(data)
        if actual == 'unknown': actual = state.get('model', 'unknown')
        effort = (data.get('effort') or {}).get('level', state.get('effort','unknown'))
        event = make_event(features, 'claude', actual, effort)
        # Byte offset and wall time bound the Stop parser to this turn only.
        try: offset = Path(data['transcript_path']).stat().st_size
        except (OSError, KeyError): offset = 0
        state.update(pending=event, offset=offset, started=time.time(),
                     recommendation={'model':option['id'],'effort':option['effort'],'predicted':option['predicted']})
        set_setting(db,key,state)
    return {'systemMessage':f"advisor: {option['id']}/{option['effort']} would do ({option['predicted']:.0%}) - you are on {actual}; rate after with + / - tags", 'suppressOutput':True}


def transcript_numbers(path, offset):
    totals = {}
    model = 'unknown'
    messages = {}
    tools, failures = set(), set()
    timestamps = []
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        for line in stream:
            try: row = json.loads(line)
            except (ValueError, UnicodeError): continue
            # Subagent transcripts are separate; only inspect main-thread messages.
            if row.get('isSidechain'): continue
            try: timestamps.append(datetime.fromisoformat(row['timestamp'].replace('Z','+00:00')).timestamp())
            except (KeyError, ValueError, TypeError): pass
            message = row.get('message', {})
            if not isinstance(message,dict): continue
            if row.get('type') == 'assistant':
                current = known_model(message.get('model'), 'claude')
                if current != 'unknown': model = current
                usage = message.get('usage',{})
                # Streaming snapshots share an API message ID. Keep the latest,
                # not a sum of duplicate snapshots of the same request.
                if usage: messages[message.get('id', row.get('uuid', len(messages)))] = usage
            content = message.get('content',[])
            if not isinstance(content,list): continue
            for block in content:
                if not isinstance(block,dict): continue
                if block.get('type') == 'tool_use': tools.add(block['id'])
                if block.get('type') == 'tool_result' and block.get('is_error'):
                    failures.add(block['tool_use_id'])
    fields = {'input_tokens':'input_tokens','output_tokens':'output_tokens',
              'cache_read_input_tokens':'cached_tokens','cache_creation_input_tokens':'cache_creation_tokens'}
    for usage in messages.values():
        for source, target in fields.items():
            value = usage.get(source)
            if type(value) in (int,float) and value >= 0: totals[target] = totals.get(target,0) + value
    totals.update(tool_calls=len(tools), tool_failures=len(failures))
    if timestamps: totals['latency_ms'] = max(0,(max(timestamps)-min(timestamps))*1000)
    return model, totals


def stop(data):
    from .policy import rebuild
    key = session_key(data)
    with connect() as db:
        state = setting(db,key,{})
        event = state.get('pending')
        if not event: return
        model, numbers = transcript_numbers(data['transcript_path'],state['offset'])
        if model != 'unknown': event['model'] = model
        effort = (data.get('effort') or {}).get('level')
        if effort in {'low','medium','high','xhigh','max'}: event['effort'] = effort
        event['indicators'].update(numbers)
        put(db,event)
        state.update(last=event['id'], model=event['model'], effort=event['effort'])
        state.pop('pending'); state.pop('offset'); state.pop('started')
        set_setting(db,key,state)
        rebuild(db)


def statusline(data):
    key = session_key(data)
    with connect() as db:
        state = setting(db,key,{})
        state['model'] = model_in(data)
        effort = (data.get('effort') or {}).get('level')
        if effort in {'low','medium','high','xhigh','max'}: state['effort'] = effort
        set_setting(db,key,state)
    rec = state.get('recommendation',{})
    return f"advisor: {rec.get('model','?')}/{rec.get('effort','?')} | on {state['model']} | rating {state.get('rating','?')}"


def hook_main(kind, worker=False):
    # Parent enforces a wall-clock budget even on locked DBs or optional runtimes.
    # Buffer all output: partial/error output can never leak to Claude Code.
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        result = user_prompt(data) if kind == 'user-prompt' else stop(data) if kind == 'stop' else statusline(data)
        if result is not None: print(result if isinstance(result,str) else json.dumps(result))
    except Exception:
        pass
    return 0


def executable_command():
    import shutil
    invoked = Path(sys.argv[0])
    executable = str(invoked) if invoked.name.lower() in {'advisor','advisor.exe'} and invoked.is_file() else shutil.which('advisor')
    if not executable:
        # Venv installs are often not on PATH: use the launcher beside the running interpreter (manager fix).
        beside = [Path(sys.executable).parent/n for n in ('advisor.exe','advisor')]
        executable = next((str(p) for p in beside if p.is_file()), None)
    if not executable: raise ValueError('Install advisor first so its executable is on PATH')
    path = str(Path(executable).resolve())
    # Claude Code command hooks use a shell (Git Bash on Windows).
    return shlex.quote(path.replace('\\','/'))


def settings_change(action, *, dry_run=False, status=False, path=None, command=None):
    import difflib
    path = Path(path) if path else Path.home()/'.claude'/'settings.json'
    before = path.read_text(encoding='utf-8') if path.exists() else '{}\n'
    settings = json.loads(before)
    command = command or executable_command()
    desired = {'UserPromptSubmit':command+' hook user-prompt', 'Stop':command+' hook stop'}
    hooks = settings.setdefault('hooks',{}) if action == 'install' else settings.get('hooks',{})
    def owned(value):
        # Recognize installations from another venv without touching unrelated hooks.
        try:
            parts = shlex.split(value or '')
            return len(parts)==3 and Path(parts[0].replace('\\','/')).name in {'advisor','advisor.exe'} and parts[1]=='hook' and parts[2] in {'user-prompt','stop'}
        except ValueError: return False

    for event, cmd in desired.items():
        entries = hooks.get(event,[])
        if action == 'install':
            if not any(h.get('command') == cmd for entry in entries for h in entry.get('hooks',[])):
                hooks.setdefault(event,[]).append({'hooks':[{'type':'command','command':cmd,'timeout':1}]})
        else:
            kept = []
            for entry in entries:
                remaining = [h for h in entry.get('hooks',[]) if not owned(h.get('command'))]
                if remaining == entry.get('hooks',[]): kept.append(entry)
                elif remaining: kept.append({**entry,'hooks':remaining})
            if event in hooks: hooks[event] = kept
    line = {'type':'command','command':command+' statusline'}
    if action == 'install' and status and 'statusLine' not in settings: settings['statusLine'] = line
    if action == 'uninstall' and settings.get('statusLine') == line: settings.pop('statusLine')
    after = json.dumps(settings,indent=2)+'\n'
    changed = json.loads(before) != settings
    if dry_run:
        print(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=str(path),tofile=str(path)+' (proposed)')) if changed else 'No changes.')
    elif changed:
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + str(time.time_ns())
            backup = path.with_name(path.name+'.advisor-bak-'+stamp)
            suffix = 0
            while True:
                candidate = backup if not suffix else backup.with_name(backup.name+'-'+str(suffix))
                try:
                    with candidate.open('x',encoding='utf-8') as stream: stream.write(before)
                    break
                except FileExistsError: suffix += 1
        temp = path.with_name(path.name+'.advisor-tmp-'+str(os.getpid()))
        try:
            temp.write_text(after,encoding='utf-8'); os.replace(temp,path)
        finally: temp.unlink(missing_ok=True)
        print('Advisor Claude Code settings updated.')
    return settings


def install_main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='advisor claude-code')
    parser.add_argument('action',choices=('install','uninstall'))
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--statusline',dest='status',action='store_true')
    args = parser.parse_args(argv)
    settings_change(**vars(args))
    return 0
