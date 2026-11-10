"""Shared on-demand lifecycle for scheduled plans and interactive chat."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
DOCKER = str(Path.home() / '.docker/bin/docker') if (Path.home() / '.docker/bin/docker').exists() else 'docker'
OLLAMA = shutil.which('ollama') or '/usr/local/bin/ollama'
CONTAINER = 'rhythm-n8n-1'
MODEL = os.environ.get('RHYTHM_MODEL', 'gemma4:e4b')


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)


def phase(value, note=''):
    write_json(RUNTIME / 'power-state.json', {'phase': value, 'note': note, 'updated': time.time()})


def command(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def request(url, data=None, timeout=10):
    payload = None if data is None else json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def healthy(url):
    try:
        request(url, timeout=2)
        return True
    except Exception:
        return False


def wait_ready(url, seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if healthy(url):
            return
        time.sleep(1)
    raise TimeoutError('Service did not start')


def query(sql, workflow_id=None):
    # SQL is internal code; user strings are passed as bound arguments.
    js = "const{DatabaseSync}=require('node:sqlite');const d=new DatabaseSync('/home/node/.n8n/database.sqlite',{readOnly:true});const s=d.prepare(process.argv[1]);console.log(JSON.stringify(process.argv[2]?s.all(process.argv[2]):s.all()));"
    args = [DOCKER, 'exec', CONTAINER, 'node', '-e', js, sql]
    if workflow_id:
        args.append(workflow_id)
    result = command(args, timeout=15)
    if result.returncode:
        raise RuntimeError('Execution history unavailable')
    return json.loads(result.stdout)


def unflatten(nodes):
    memo = {}
    def ref(value):
        return resolve(int(value)) if isinstance(value, str) else value
    def resolve(index):
        if index in memo:
            return memo[index]
        node = nodes[index]
        if isinstance(node, dict):
            result = {}; memo[index] = result
            result.update({k: ref(v) for k, v in node.items()})
        elif isinstance(node, list):
            result = []; memo[index] = result
            result.extend(ref(v) for v in node)
        else:
            result = node; memo[index] = result
        return result
    return resolve(0)


def cache_plans():
    config = json.loads((RUNTIME / 'wake-config.json').read_text())
    rows = query('SELECT e.id,e.status,e.startedAt,d.data FROM execution_entity e LEFT JOIN execution_data d ON d.executionId=e.id WHERE e.workflowId=? ORDER BY e.id DESC LIMIT 30', config['workflow_id'])
    plans = []
    for row in rows:
        output = ''
        if row['status'] == 'success' and row.get('data'):
            try:
                data = unflatten(json.loads(row['data']))
                output = data['resultData']['runData']['AI Agent'][-1]['data']['main'][0][0]['json']['output']
            except (KeyError, IndexError, ValueError, TypeError):
                output = 'Текст плана недоступен в истории.'
        plans.append({'id': row['id'], 'date': row['startedAt'], 'status': row['status'], 'content': output})
    mail_file = RUNTIME / 'mail-reports.json'
    mail_reports = json.loads(mail_file.read_text()) if mail_file.exists() else {}
    for plan in plans:
        if str(plan['id']) in mail_reports:
            plan['content'] += '\n\n' + mail_reports[str(plan['id'])]
    write_json(RUNTIME / 'plans-cache.json', plans)
    return plans


def start():
    phase('starting', 'Запускаю сервисы только для этого запроса')
    if not healthy('http://127.0.0.1:11434/api/tags'):
        env = os.environ.copy()
        env.update(OLLAMA_HOST='0.0.0.0:11434', OLLAMA_KEEP_ALIVE='0')
        with (RUNTIME / 'ollama-on-demand.log').open('ab') as log:
            subprocess.Popen([OLLAMA, 'serve'], env=env, stdout=log, stderr=log, start_new_session=True)
        wait_ready('http://127.0.0.1:11434/api/tags', 30)
    if command([DOCKER, 'info', '--format', '{{.ServerVersion}}'], timeout=15).returncode:
        result = command([DOCKER, 'desktop', 'start', '--timeout', '120'], timeout=130)
        if result.returncode:
            raise RuntimeError('Docker Desktop could not start')
    result = command([DOCKER, 'compose', '-f', str(ROOT / 'compose.yaml'), 'up', '-d'], timeout=90)
    if result.returncode:
        raise RuntimeError('n8n could not start')
    wait_ready('http://127.0.0.1:5678/healthz', 90)
    # Give production webhook registration a moment after n8n's health endpoint.
    time.sleep(2)
    phase('working', 'Gemma готовит ответ; после запроса ресурсы освободятся')


def stop_ollama_if_unused():
    if not healthy('http://127.0.0.1:11434/api/tags'):
        return True
    request('http://127.0.0.1:11434/api/generate', {'model': MODEL, 'keep_alive': 0}, timeout=40)
    models = request('http://127.0.0.1:11434/api/ps').get('models', [])
    if models:
        return False  # Another model is in use; leave its server alone.
    # Stop the known Ollama menu app first so it cannot relaunch its server.
    rows = command(['/bin/ps', '-axo', 'pid=,comm=']).stdout.splitlines()
    for row in rows:
        parts = row.strip().split(None, 1)
        if len(parts) == 2 and parts[1] == '/Applications/Ollama.app/Contents/MacOS/Ollama':
            try:
                os.kill(int(parts[0]), signal.SIGTERM)
            except ProcessLookupError:
                pass
    listeners = command(['/usr/sbin/lsof', '-t', '-iTCP:11434', '-sTCP:LISTEN']).stdout.split()
    for pid in listeners:
        executable = command(['/bin/ps', '-p', pid, '-o', 'comm=']).stdout.strip()
        if executable in ('/Applications/Ollama.app/Contents/Resources/ollama', OLLAMA):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
    return True


def stop():
    phase('stopping', 'Выгружаю модель и выключаю Docker')
    notes = []
    try:
        # A run launched directly from the n8n editor must also finish first.
        if healthy('http://127.0.0.1:5678/healthz'):
            busy = query("SELECT id FROM execution_entity WHERE status IN ('new','running','waiting') LIMIT 1")
            until = time.monotonic() + 15
            while busy and time.monotonic() < until:
                time.sleep(1)
                busy = query("SELECT id FROM execution_entity WHERE status IN ('new','running','waiting') LIMIT 1")
            if busy:
                phase('deferred', 'В n8n выполняется другая задача; выключение отложено')
                return
            cache_plans()
    except Exception as error:
        notes.append('Cache: ' + type(error).__name__)
    try:
        if stop_ollama_if_unused() is False:
            notes.append('Ollama оставлена включённой: используется другая модель')
    except Exception as error:
        notes.append('Ollama: ' + type(error).__name__)
    try:
        if command([DOCKER, 'info', '--format', '{{.ServerVersion}}'], timeout=15).returncode == 0:
            result = command([DOCKER, 'compose', '-f', str(ROOT / 'compose.yaml'), 'stop', 'n8n'], timeout=45)
            if result.returncode:
                raise RuntimeError('n8n stop failed')
            listed = command([DOCKER, 'ps', '--format', '{{.Names}}'])
            if listed.returncode:
                raise RuntimeError('Cannot verify other containers')
            others = listed.stdout.strip()
            if not others:
                result = command([DOCKER, 'desktop', 'stop', '--timeout', '45'], timeout=55)
                if result.returncode:
                    raise RuntimeError('Docker stop failed')
            else:
                notes.append('Docker оставлен включённым: работают другие контейнеры')
    except Exception as error:
        notes.append('Docker: ' + type(error).__name__)
    phase('idle' if not notes else 'shared', '; '.join(notes))


def recover_if_interrupted():
    """Reap resources after an interrupted worker without disturbing a live one."""
    statefile = RUNTIME / 'power-state.json'
    if not statefile.exists():
        return
    state = json.loads(statefile.read_text())
    if state.get('phase') not in ('starting', 'working', 'stopping', 'deferred'):
        return
    with (RUNTIME / 'engine.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        stop()


@contextlib.contextmanager
def engine():
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME / 'engine.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            start()
            yield
        finally:
            stop()
