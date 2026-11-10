#!/usr/bin/env python3
"""Loopback-only planner chat. No cloud inference, no event writes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import datetime as dt
import json
import mimetypes
import os
import sqlite3
import subprocess
import threading
import urllib.request
import urllib.parse
import uuid
import sys
import time
import re

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT.parent / 'runtime'
DB = RUNTIME / 'chat.sqlite'
GATE = threading.Semaphore(1)
CONFIG = None
SERVER = None
CLOSING = threading.Event()
sys.path.insert(0, str(ROOT.parent))
from power import engine
import strive
import mail_digest


def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def init():
    global CONFIG
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    CONFIG = json.loads((RUNTIME / 'ui-config.json').read_text())
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,status TEXT,created TEXT);
        CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id,id);''')
        db.execute("UPDATE messages SET status='done' WHERE status='pending' AND content<>''")
        db.execute("UPDATE messages SET status='error',content='Приложение было перезапущено. Отправьте запрос ещё раз.' WHERE status='pending'")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def n8n_query(js):
    result = subprocess.run([__import__('power').DOCKER, 'exec', 'rhythm-n8n-1', 'node', '-e', js, CONFIG['plan_workflow_id']], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('n8n unavailable')
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


def plans():
    cache = RUNTIME / 'plans-cache.json'
    return json.loads(cache.read_text()) if cache.exists() else []


def close_when_finished():
    while True:
        with connect() as db:
            busy = db.execute("SELECT 1 FROM messages WHERE status='pending' LIMIT 1").fetchone()
        if not busy:
            break
        time.sleep(1)
    time.sleep(0.5)
    SERVER.shutdown()


def watch_native_window():
    parent = os.getppid()
    result = subprocess.run(['/bin/ps', '-p', str(parent), '-o', 'comm='], capture_output=True, text=True)
    if Path(result.stdout.strip()).name != 'Rhythm':
        return
    while os.getppid() == parent and not CLOSING.is_set():
        time.sleep(5)
    if not CLOSING.is_set():
        CLOSING.set()
        close_when_finished()


def generate(chat_id, message_id, text):
    with GATE:
        try:
            target = dt.datetime.now(strive.MOSCOW).date()
            if re.search(r'\bзавтра\b', text, re.IGNORECASE):
                target += dt.timedelta(days=1)
            strive_data = strive.snapshot(planning_day=target)
            mail_requested = bool(re.search(r'план|почт|письм', text, re.IGNORECASE))
            with engine():
                mail_snapshot = mail_digest.collect_all() if mail_requested else None
                with connect() as db:
                    previous = db.execute("SELECT role,content FROM messages WHERE chat_id=? AND id<? AND status='done' ORDER BY id DESC LIMIT 4", (chat_id, message_id - 1)).fetchall()
                history = [{'role': row['role'], 'content': row['content'][-1200:]} for row in reversed(previous)]
                payload = json.dumps({'chatInput': text, 'sessionId': 'planner-ui-' + chat_id, 'history': history, 'strive': strive_data}, ensure_ascii=False).encode()
                req = urllib.request.Request(CONFIG['chat_webhook'], data=payload, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=660) as response:
                    body = json.load(response)
                if isinstance(body, list):
                    body = body[0]
                output = body.get('output') if isinstance(body, dict) else None
                if not isinstance(output, str) or not output.strip():
                    raise ValueError('Empty model response')
                if mail_snapshot:
                    output += '\n\n' + mail_digest.render(mail_snapshot)
                with connect() as db:
                    db.execute('UPDATE messages SET content=? WHERE id=?', (output, message_id))
                if mail_snapshot:
                    mail_digest.commit(mail_snapshot)
            status = 'done'
        except Exception:
            output = 'Не удалось получить ответ. Проверьте, что Docker и Ollama работают и есть интернет для календаря, затем отправьте запрос ещё раз.'
            status = 'error'
        with connect() as db:
            db.execute('UPDATE messages SET content=?,status=? WHERE id=?', (output, status, message_id))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, kind='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def safe_host(self):
        return self.headers.get('Host') in ('127.0.0.1:8787', 'localhost:8787')

    def do_GET(self):
        if not self.safe_host():
            return self.reply(403, {'error': 'Forbidden'})
        path = urllib.parse.urlparse(self.path)
        try:
            if path.path == '/api/integrations/mail':
                return self.reply(200, mail_digest.status())
            if path.path == '/api/integrations/strive':
                return self.reply(200, strive.status())
            if path.path == '/api/power':
                state = RUNTIME / 'power-state.json'
                return self.reply(200, json.loads(state.read_text()) if state.exists() else {'phase': 'idle'})
            if path.path == '/api/chats':
                with connect() as db:
                    rows = db.execute('SELECT c.*,MAX(m.id) AS recent FROM chats c LEFT JOIN messages m ON c.id=m.chat_id GROUP BY c.id ORDER BY recent DESC,c.created DESC').fetchall()
                return self.reply(200, [dict(r) for r in rows])
            if path.path == '/api/messages':
                chat_id = urllib.parse.parse_qs(path.query).get('chat', [''])[0]
                with connect() as db:
                    rows = db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id', (chat_id,)).fetchall()
                return self.reply(200, [dict(r) for r in rows])
            if path.path == '/api/plans':
                return self.reply(200, plans())
            allowed = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/vendor/marked.js': 'vendor/marked.js', '/vendor/marked.mjs': 'vendor/marked.mjs', '/vendor/purify.js': 'vendor/purify.js'}
            if path.path not in allowed:
                return self.reply(404, {'error': 'Not found'})
            file = ROOT / allowed[path.path]
            kind = 'text/javascript' if file.suffix in ('.js', '.mjs') else mimetypes.guess_type(file.name)[0] or 'text/plain'
            self.reply(200, file.read_bytes(), kind + '; charset=utf-8')
        except Exception:
            self.reply(503, {'error': 'Не удалось загрузить данные. Проверьте, что Docker и n8n запущены.'})

    def do_POST(self):
        if not self.safe_host() or self.headers.get('X-Planner-Client') != 'local' or self.headers.get('Origin') not in (None, 'http://127.0.0.1:8787', 'http://localhost:8787'):
            return self.reply(403, {'error': 'Forbidden'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > 40000:
                return self.reply(413, {'error': 'Сообщение слишком длинное.'})
            body = json.loads(self.rfile.read(length) or b'{}')
            if self.path == '/api/shutdown':
                if not CLOSING.is_set():
                    CLOSING.set()
                    threading.Thread(target=close_when_finished, daemon=True).start()
                return self.reply(200, {'closing': True})
            if CLOSING.is_set():
                return self.reply(503, {'error': 'Приложение завершает работу. Откройте Ритм заново.'})
            if self.path == '/api/integrations/mail':
                try:
                    return self.reply(200, mail_digest.install(str(body.get('provider', '')), str(body.get('address', '')).strip(), str(body.get('password', '')).strip()))
                except ValueError as error:
                    return self.reply(400, {'error': str(error)})
            if self.path == '/api/integrations/strive':
                try:
                    result = strive.install_token(str(body.get('token', '')))
                    return self.reply(200, {k: v for k, v in result.items() if k != 'tasks'})
                except strive.StriveError as error:
                    return self.reply(400, {'error': str(error)})
            if self.path == '/api/chats':
                chat_id = str(uuid.uuid4())
                with connect() as db:
                    db.execute('INSERT INTO chats VALUES(?,?,?)', (chat_id, 'Новый разговор', now()))
                return self.reply(200, {'id': chat_id})
            if self.path == '/api/send':
                text, chat_id = str(body.get('text', '')).strip(), body.get('chat_id')
                if not text or len(text) > 8000:
                    return self.reply(400, {'error': 'Введите сообщение до 8000 символов.'})
                with connect() as db:
                    if not db.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone():
                        return self.reply(404, {'error': 'Разговор не найден.'})
                    if db.execute("SELECT 1 FROM messages WHERE chat_id=? AND status='pending'", (chat_id,)).fetchone():
                        return self.reply(409, {'error': 'Дождитесь ответа на предыдущее сообщение.'})
                    db.execute("UPDATE chats SET title=? WHERE id=? AND title='Новый разговор'", (text[:52], chat_id))
                    db.execute('INSERT INTO messages(chat_id,role,content,status,created) VALUES(?,?,?,?,?)', (chat_id, 'user', text, 'done', now()))
                    message_id = db.execute('INSERT INTO messages(chat_id,role,content,status,created) VALUES(?,?,?,?,?)', (chat_id, 'assistant', '', 'pending', now())).lastrowid
                threading.Thread(target=generate, args=(chat_id, message_id, text), daemon=True).start()
                return self.reply(202, {'id': message_id})
            self.reply(404, {'error': 'Not found'})
        except Exception:
            self.reply(400, {'error': 'Не удалось отправить сообщение. Попробуйте ещё раз.'})


if __name__ == '__main__':
    init()
    SERVER = ThreadingHTTPServer(('127.0.0.1', 8787), Handler)
    threading.Thread(target=watch_native_window, daemon=True).start()
    SERVER.serve_forever()
    SERVER.server_close()
