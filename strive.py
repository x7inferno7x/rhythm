"""Read-only Strive integration. Token stays in macOS Keychain."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
SECRET = str(Path.home() / 'Applications/Ритм.app/Contents/MacOS/StriveSecret')
BASE = 'https://server.striveapp.ru'
MOSCOW = ZoneInfo('Europe/Moscow')
TOKEN = re.compile(r'^strive_at_[A-Za-z0-9_.-]{20,500}$')
ALLOWED = re.compile(r'^/(?:user/me|spaces|spaces/\d+/projects|projects/\d+/tasks)$')
DATE_COLUMN = re.compile(r'^(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?$')


class StriveError(Exception):
    pass


def get_token():
    result = subprocess.run([SECRET, 'get'], capture_output=True, timeout=15)
    if result.returncode:
        return None
    token = result.stdout.decode().strip()
    return token if TOKEN.fullmatch(token) else None


def get(path, token, params=None):
    if not ALLOWED.fullmatch(path) or not TOKEN.fullmatch(token):
        raise StriveError('Недопустимый запрос Strive')
    url = BASE + path + ('?' + urlencode(params) if params else '')
    # Use Apple's system TLS trust. Credential travels via stdin, never argv.
    config = 'header = "Authorization: Bearer ' + token + '"\nheader = "Accept: application/json"\n'
    for attempt in range(2):
        result = subprocess.run(['/usr/bin/curl', '--silent', '--show-error', '--request', 'GET', '--connect-timeout', '8', '--max-time', '25', '--max-filesize', '15000000', '--write-out', '\n%{http_code}', '--config', '-', url], input=config.encode(), capture_output=True, timeout=30)
        raw, _, code = result.stdout.rpartition(b'\n')
        if attempt == 0 and (result.returncode or code in (b'429', b'500', b'502', b'503', b'504')):
            time.sleep(1)
            continue
        break
    if result.returncode:
        raise StriveError('Strive временно недоступен. Проверьте интернет и повторите попытку.')
    raw, _, code = result.stdout.rpartition(b'\n')
    if code == b'401':
        raise StriveError('Strive отклонил токен. Проверьте его или создайте новый.')
    if code == b'403':
        raise StriveError('Strive запретил доступ. Проверьте доступность API в своём тарифе и права токена.')
    if code != b'200':
        raise StriveError('Strive вернул ошибку API (' + code.decode(errors='replace')[:3] + ').')
    try:
        return json.loads(raw)
    except ValueError:
        raise StriveError('Strive вернул неожиданный формат ответа.') from None


def list_response(value):
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise StriveError('Формат списка Strive отличается от документации.')
    return value


def install_token(token):
    token = token.strip()
    if not TOKEN.fullmatch(token):
        raise StriveError('Вставьте API-токен Strive целиком, без лишнего текста.')
    me = get('/user/me', token)
    if not isinstance(me, dict) or not isinstance(me.get('id'), int):
        raise StriveError('Не удалось подтвердить пользователя Strive.')
    list_response(get('/spaces', token))
    result = subprocess.run([SECRET, 'set'], input=token.encode(), capture_output=True, timeout=20)
    if result.returncode:
        raise StriveError('Не удалось сохранить токен в Связке ключей macOS.')
    # Non-secret state only. A failed API must not look like an empty task list.
    return snapshot(token)


def local_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            dt.date.fromisoformat(value)
            return value
        value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if value.tzinfo is None:
            return None
        return value.astimezone(MOSCOW).isoformat(timespec='minutes')
    except ValueError:
        return None


def column_dates(name, today=None):
    """A board day is explicitly in the current Moscow month, never inferred."""
    today = today or dt.datetime.now(MOSCOW).date()
    match = DATE_COLUMN.fullmatch(str(name).strip())
    if not match:
        return None
    first, last = int(match[1]), int(match[2] or match[1])
    if first > last:
        return None
    try:
        start = dt.date(today.year, today.month, first)
        end = dt.date(today.year, today.month, last)
    except ValueError:
        return None
    return {'column': str(name).strip(), 'from': start.isoformat(), 'to': end.isoformat()}


def task_columns(task, project, today=None):
    ids = set()
    if isinstance(task.get('stageId'), int):
        ids.add(task['stageId'])
    for stage in task.get('stages') or []:
        if isinstance(stage, dict) and stage.get('projectId', project['id']) == project['id']:
            ids.add(stage.get('stageId'))
        elif isinstance(stage, int):
            ids.add(stage)
    result = []
    for stage in project.get('stages') or []:
        if stage.get('id') in ids:
            parsed = column_dates(stage.get('name'), today)
            if parsed:
                result.append(parsed)
    return result


def normalize(task, project, user_id, today=None):
    if task.get('status') != 0:
        return None  # Official API: 0 in progress, 1 completed, 2 rejected.
    owners = task.get('responsibleUserId') or []
    if not isinstance(owners, list):
        owners = [owners]
    owners = [int(x.get('id') if isinstance(x, dict) else x) for x in owners if str(x.get('id') if isinstance(x, dict) else x).isdigit()]
    if owners and user_id not in owners:
        return None
    stages = task.get('stages') or []
    archive = project.get('archiveStageId')
    if archive and (task.get('stageId') == archive or (stages and all(isinstance(s, dict) and s.get('stageId') == archive for s in stages))):
        return None
    if task.get('deleted') is True or task.get('archived') is True:
        return None
    dates = task_columns(task, project, today)
    if not dates:
        return None
    title = str(task.get('name') or '').strip()
    if not title:
        return None
    importance = task.get('importance')
    priority = {-2: 'Заморожено', -1: 'Не важно', 0: 'Обычная', 1: 'Важно', 2: 'Критическая'}.get(importance, 'Не указана')
    seconds = task.get('planTime')
    return {'id': task.get('id'), 'title': title[:180], 'project': str(project.get('name') or '')[:80], 'columnDates': dates, 'deadline': local_date(task.get('dateEndWithoutTime')) or local_date(task.get('dateEnd')), 'earliestStart': local_date(task.get('dateBeginWithoutTime')) or local_date(task.get('dateBegin')), 'priority': priority, 'importance': importance, 'estimatedMinutes': round(seconds / 60, 1) if isinstance(seconds, (int, float)) and seconds > 0 else None, 'blocked': bool(task.get('blockReason')), 'frozen': importance == -2, 'assignedToMe': bool(owners)}


def snapshot(token=None, planning_day=None):
    from power import write_json
    fetched = dt.datetime.now(MOSCOW).isoformat(timespec='seconds')
    try:
        token = token or get_token()
        if not token:
            return {'status': 'not_configured', 'tasks': [], 'fetchedAt': fetched}
        me = get('/user/me', token)
        user_id = me.get('id')
        if not isinstance(user_id, int):
            raise StriveError('Не удалось определить пользователя Strive.')
        spaces = list_response(get('/spaces', token))
        partial = len(spaces) > 20
        projects = []
        for space in spaces[:20]:
            if isinstance(space.get('id'), int):
                projects.extend(list_response(get('/spaces/' + str(space['id']) + '/projects', token)))
        # The same project may appear in more than one space.
        projects = list({p['id']: p for p in projects if isinstance(p.get('id'), int)}.values())
        today = dt.datetime.now(MOSCOW).date()
        target = planning_day or today
        # No task reads at all for projects that contain no valid date columns.
        projects = [p for p in projects if any(column_dates(s.get('name'), today) for s in p.get('stages') or [])]
        partial |= len(projects) > 80
        def read(project):
            tasks = list_response(get('/projects/' + str(project['id']) + '/tasks', token, {'includeCompleted': 'false'}))
            return [result for task in tasks if (result := normalize(task, project, user_id, today))]
        selected = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for tasks in pool.map(read, projects[:80]):
                for task in tasks:
                    selected[task['id']] = task
        tasks = list(selected.values())
        # Prioritize deadlines. Unknown importance values are kept as raw data.
        def rank(task):
            windows = task['columnDates']
            on_day = any(w['from'] <= target.isoformat() <= w['to'] for w in windows)
            upcoming = any(w['from'] > target.isoformat() for w in windows)
            return (task['frozen'], 0 if on_day else 1 if upcoming else 2, min(w['from'] for w in windows), -(task['importance'] or 0), task['blocked'], str(task['id']))
        tasks.sort(key=rank)
        compact = []
        used = 0
        for task in tasks[:15]:
            item = {k: v for k, v in task.items() if k not in ('importance', 'assignedToMe') and v is not None and v is not False}
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > 4500:
                break
            compact.append(item)
            used += size
        result = {'status': 'ok', 'fetchedAt': fetched, 'totalActiveTasks': len(tasks), 'tasks': compact, 'truncated': partial or len(compact) < len(tasks), 'scope': 'Только числовые столбцы с датами текущего месяца: мои задачи и без исполнителя', 'columnMonth': today.strftime('%Y-%m'), 'selectedForDay': target.isoformat(), 'totalForDay': sum(any(w['from'] <= target.isoformat() <= w['to'] for w in t['columnDates']) for t in tasks), 'timezone': 'Europe/Moscow'}
        write_json(RUNTIME / 'strive-tasks-cache.json', result)
        write_json(RUNTIME / 'strive-status.json', {k: v for k, v in result.items() if k != 'tasks'})
        return result
    except (StriveError, subprocess.SubprocessError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, StriveError) else 'Не удалось прочитать Strive. Повторите запрос позже.'
        result = {'status': 'unavailable', 'tasks': [], 'fetchedAt': fetched, 'error': message}
        cache = RUNTIME / 'strive-tasks-cache.json'
        if cache.exists():
            saved = json.loads(cache.read_text())
            age = (dt.datetime.now(MOSCOW) - dt.datetime.fromisoformat(saved['fetchedAt'])).total_seconds()
            if 0 <= age < 86400 and saved.get('status') == 'ok' and saved.get('columnMonth') == dt.datetime.now(MOSCOW).strftime('%Y-%m'):
                result = {**saved, 'status': 'stale', 'error': message, 'cached': True}
        write_json(RUNTIME / 'strive-status.json', {k: v for k, v in result.items() if k != 'tasks'})
        return result


def status():
    path = RUNTIME / 'strive-status.json'
    return json.loads(path.read_text()) if path.exists() else {'status': 'not_configured'}
