#!/usr/bin/env python3
"""Run the latest due Moscow plan once; retry failed runs after 15 minutes."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'runtime'
DOCKER = str(Path.home() / '.docker/bin/docker') if (Path.home() / '.docker/bin/docker').exists() else 'docker'
CONTAINER = 'rhythm-n8n-1'
MOSCOW = ZoneInfo('Europe/Moscow')


def due_slot(now):
    now = now.astimezone(MOSCOW)
    if now.hour >= 23:
        return now.replace(hour=23, minute=0, second=0, microsecond=0)
    if now.hour >= 7:
        return now.replace(hour=7, minute=0, second=0, microsecond=0)
    return (now - dt.timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)


def timestamp(value):
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def disposition(rows, slot, now):
    current = [r for r in rows if r.get('startedAt') and timestamp(r['startedAt']) >= slot]
    if any(r['status'] == 'success' for r in current):
        return 'done'
    if any(r['status'] in ('new', 'running', 'waiting') for r in current):
        return 'running'
    if any((now - timestamp(r.get('stoppedAt') or r['startedAt'])).total_seconds() < 900 for r in current):
        return 'retry_later'
    return 'due'


def command(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def healthy(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def log(message):
    print(dt.datetime.now(MOSCOW).isoformat(timespec='seconds'), message, flush=True)


def main():
    os.umask(0o077)
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    with (PRIVATE / 'wake.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        config = json.loads((PRIVATE / 'wake-config.json').read_text())
        now = dt.datetime.now(MOSCOW)
        slot = due_slot(now)
        statefile = PRIVATE / 'wake-state.json'
        state = json.loads(statefile.read_text()) if statefile.exists() else {}
        if state.get('completed_slot') == slot.isoformat():
            from power import recover_if_interrupted
            recover_if_interrupted()
            return
        if state.get('attempt_slot') == slot.isoformat() and now.timestamp() - state.get('attempt_at', 0) < 900:
            return
        from power import engine, query, request, cache_plans, write_json
        state.update(attempt_slot=slot.isoformat(), attempt_at=now.timestamp())
        write_json(statefile, state)
        from strive import snapshot
        target = now.date() + dt.timedelta(days=1 if now.hour >= 23 else 0)
        strive_data = snapshot(planning_day=target)
        with engine():
            rows = query('SELECT id,status,startedAt,stoppedAt FROM execution_entity WHERE workflowId=? ORDER BY id DESC LIMIT 100', config['workflow_id'])
            outcome = disposition(rows, slot, dt.datetime.now(MOSCOW))
            if outcome in ('running', 'retry_later'):
                return
            if outcome != 'done':
                import mail_digest
                mail_snapshot = mail_digest.collect_all()
                result = request(config['webhook_url'], {'strive': strive_data}, timeout=660)
                if isinstance(result, list):
                    result = result[0]
                if not isinstance(result, dict) or not isinstance(result.get('output'), str):
                    raise RuntimeError('Plan generation failed')
                mail_section = mail_digest.render(mail_snapshot)
                generated = cache_plans()
                latest = next((p for p in generated if p['status'] == 'success'), None)
                if latest is None or str(latest['id']) in {str(r['id']) for r in rows}:
                    raise RuntimeError('New report not persisted')
                mail_digest.save_report(latest['id'], mail_section, mail_snapshot)
                cache_plans()
            if outcome == 'done':
                import mail_digest
                generated = cache_plans()
                latest = next((p for p in generated if p['status'] == 'success'), None)
                if latest and str(latest['id']) not in mail_digest.load(mail_digest.REPORTS):
                    mail_snapshot = mail_digest.collect_all()
                    mail_digest.save_report(latest['id'], mail_digest.render(mail_snapshot), mail_snapshot)
                    cache_plans()
            state['completed_slot'] = slot.isoformat()
            write_json(statefile, state)
            log('План за ' + slot.isoformat() + ' готов; выключаем тяжёлые сервисы.')



if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never log event data, connection URLs, or credentials.
        log('Проверка отложена: ' + type(error).__name__)
