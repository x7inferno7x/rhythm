#!/usr/bin/env python3
"""Prepare an independent installation; never downloads models or credentials."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import uuid

SOURCE = Path(__file__).resolve().parent

def materialize_workflows(folder, chat_id, daily_id, chat_hook, daily_hook):
    substitutions = {'RHYTHM_CHAT_WORKFLOW_ID': chat_id, 'RHYTHM_DAILY_WORKFLOW_ID': daily_id, 'RHYTHM_CHAT_WEBHOOK_PATH': chat_hook, 'RHYTHM_DAILY_WEBHOOK_PATH': daily_hook}
    result = []
    for name in ['chat', 'daily']:
        text = (folder / 'workflows' / (name + '.json')).read_text()
        for old, new in substitutions.items():
            text = text.replace(old, new)
        result.append(json.loads(text))
    return result

def preflight():
    if sys.version_info < (3, 10):
        raise RuntimeError('Нужен Python 3.10 или новее.')
    if sys.platform != 'darwin':
        raise RuntimeError('Приложение предназначено для macOS.')
    if not shutil.which('xcrun'):
        raise RuntimeError('Нужны Xcode Command Line Tools: установите их командой xcode-select --install.')
    check = subprocess.run(['xcrun', '--find', 'swiftc'], capture_output=True)
    if check.returncode:
        raise RuntimeError('Не найден компилятор Swift. Установите Xcode Command Line Tools.')
    if not (shutil.which('docker') or (Path.home() / '.docker/bin/docker').exists()):
        raise RuntimeError('Сначала установите Docker Desktop.')
    if not (shutil.which('ollama') or Path('/usr/local/bin/ollama').exists()):
        raise RuntimeError('Сначала установите Ollama. Модель устанавливается отдельно.')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, default=Path.home() / 'personal-ai-planner')
    parser.add_argument('--check', action='store_true', help='Проверить зависимости, ничего не устанавливая')
    args = parser.parse_args()
    preflight()
    if args.check:
        print('Зависимости найдены. Проверка не меняла систему и не загружала модель.')
        return
    target = args.destination.expanduser().resolve()
    app = Path.home() / 'Applications/Ритм.app'
    if target.exists() or app.exists():
        raise RuntimeError('Папка установки или Ритм.app уже существует. Для защиты данных установка остановлена.')
    os.umask(0o077)
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', 'runtime', '.git'))
    runtime = target / 'runtime'
    runtime.mkdir(mode=0o700)
    chat_id, daily_id = str(uuid.uuid4()), str(uuid.uuid4())
    chat_hook, daily_hook = 'rhythm-chat-' + uuid.uuid4().hex, 'rhythm-plan-' + uuid.uuid4().hex
    def save(name, value):
        path = runtime / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        path.chmod(0o600)
    save('ui-config.json', {'chat_webhook': 'http://127.0.0.1:5678/webhook/' + chat_hook, 'plan_workflow_id': daily_id})
    save('wake-config.json', {'workflow_id': daily_id, 'webhook_url': 'http://127.0.0.1:5678/webhook/' + daily_hook})
    save('workflows-ready.json', materialize_workflows(target, chat_id, daily_id, chat_hook, daily_hook))
    save('power-state.json', {'phase': 'idle', 'note': '', 'updated': 0})
    save('strive-status.json', {'status': 'not_configured'})
    save('plans-cache.json', [])
    build = runtime / 'build'
    build.mkdir()
    swift = (target / 'native/Rhythm.swift').read_text()
    def escaped(value):
        return str(value).replace('\\', '\\\\').replace('"', '\\"')
    swift = swift.replace('__RHYTHM_ROOT__', escaped(target)).replace('__PYTHON__', escaped(sys.executable))
    (build / 'Rhythm.swift').write_text(swift)
    (app / 'Contents/MacOS').mkdir(parents=True)
    (app / 'Contents/Resources').mkdir()
    subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-O', '-framework', 'Cocoa', '-framework', 'WebKit', str(build / 'Rhythm.swift'), '-o', str(app / 'Contents/MacOS/Rhythm')], check=True)
    subprocess.run(['xcrun', 'swiftc', '-O', '-framework', 'Security', str(target / 'native/StriveSecret.swift'), '-o', str(app / 'Contents/MacOS/StriveSecret')], check=True)
    subprocess.run(['xcrun', 'swiftc', '-O', '-framework', 'Security', str(target / 'native/MailSecret.swift'), '-o', str(app / 'Contents/MacOS/MailSecret')], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(app / 'Contents/MacOS/MailSecret')], check=True)
    shutil.copyfile(target / 'native/AppIcon.icns', app / 'Contents/Resources/AppIcon.icns')
    info = {'CFBundleName':'Ритм','CFBundleDisplayName':'Ритм','CFBundleIdentifier':'ru.personal-ai-planner.rhythm','CFBundleExecutable':'Rhythm','CFBundlePackageType':'APPL','CFBundleShortVersionString':'1.0','CFBundleVersion':'1','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},'CFBundleIconFile':'AppIcon'}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign', '--force', '--sign', '-', str(app / 'Contents/MacOS/StriveSecret')], check=True)
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    agent = {'Label':'ru.personal-ai-planner.catch-up','ProgramArguments':[sys.executable,str(target / 'wake-planner.py')],'RunAtLoad':True,'StartCalendarInterval':[{'Minute':m} for m in range(0,60,5)],'ProcessType':'Background','WorkingDirectory':str(target),'StandardOutPath':str(runtime / 'wake.log'),'StandardErrorPath':str(runtime / 'wake-error.log'),'EnvironmentVariables':{'PATH':os.environ.get('PATH','/usr/bin:/bin:/usr/sbin:/sbin'),'TZ':'Europe/Moscow'}}
    (runtime / 'catch-up.plist').write_bytes(plistlib.dumps(agent))
    print('Приложение создано:', app)
    print('Модель и аккаунты не установлены. Следующий шаг — настройка n8n по README.md.')
    print('Расписание ещё выключено. Включите его после настройки своих подключений.')

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print('Установка остановлена:', error, file=sys.stderr)
        sys.exit(1)
