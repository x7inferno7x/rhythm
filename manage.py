#!/usr/bin/env python3
"""Explicit setup commands; no credentials are generated or shared."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from power import ROOT, RUNTIME, DOCKER, CONTAINER, start, stop, wait_ready

def docker(*args):
    subprocess.run([DOCKER, *args], check=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','stop','import','publish','schedule-on','schedule-off'])
    action=parser.parse_args().action
    if action=='start':
        start(); print('n8n включён для настройки: http://127.0.0.1:5678. После настройки выполните: python3 manage.py stop')
    elif action=='stop':
        stop()
    elif action=='import':
        start()
        path=RUNTIME/'workflows-ready.json'
        if not path.exists(): raise RuntimeError('Сначала выполните setup.py.')
        dest='/tmp/rhythm-workflows.json'
        docker('cp',str(path),CONTAINER+':'+dest)
        docker('exec','-u','root',CONTAINER,'chown','node:node',dest)
        docker('exec',CONTAINER,'n8n','import:workflow','--input='+dest)
        print('Назначьте свои подключения Ollama и CalDAV в обоих workflow через n8n.')
        print('После настройки: python3 manage.py publish, затем python3 manage.py stop')
    elif action=='publish':
        start()
        ui=json.loads((RUNTIME/'ui-config.json').read_text())
        wake=json.loads((RUNTIME/'wake-config.json').read_text())
        # Chat workflow ID is installed alongside the webhook path.
        workflows=json.loads((RUNTIME/'workflows-ready.json').read_text())
        for w in workflows: docker('exec',CONTAINER,'n8n','publish:workflow','--id='+w['id'])
        docker('compose','-f',str(ROOT/'compose.yaml'),'restart','n8n')
        wait_ready('http://127.0.0.1:5678/healthz',90)
        print('Workflow опубликованы. Проверьте чат, затем выключите сервисы командой manage.py stop.')
    else:
        label='ru.personal-ai-planner.catch-up'
        target=Path.home()/'Library/LaunchAgents'/(label+'.plist')
        domain='gui/'+str(os.getuid())
        if action=='schedule-on':
            if target.exists():raise RuntimeError('Такой LaunchAgent уже существует. Автоматическая замена отключена.')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(RUNTIME/'catch-up.plist',target);target.chmod(0o600)
            subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)
            print('Включены планы в 07:00 и 23:00 по Москве и запуск после сна.')
        else:
            subprocess.run(['launchctl','bootout',domain+'/'+label],check=False,capture_output=True)
            if target.exists():target.unlink()
            print('Расписание отключено.')

if __name__=='__main__':main()
