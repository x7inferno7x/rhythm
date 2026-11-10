import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch, Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import power
spec=importlib.util.spec_from_file_location('wake',ROOT/'wake-planner.py')
wake=importlib.util.module_from_spec(spec);spec.loader.exec_module(wake)

class PowerTests(unittest.TestCase):
    def test_cleanup_after_generation_error(self):
        with patch.object(power,'start') as start, patch.object(power,'stop') as stop:
            with self.assertRaises(ValueError):
                with power.engine():
                    raise ValueError('test')
            start.assert_called_once();stop.assert_called_once()

    def test_cleanup_after_startup_error(self):
        with patch.object(power,'start',side_effect=TimeoutError),patch.object(power,'stop') as stop:
            with self.assertRaises(TimeoutError):
                with power.engine():
                    self.fail('Should not enter failed service')
            stop.assert_called_once()

    def test_other_containers_keep_docker_running(self):
        def command(args,**kwargs):
            output='another-project' if 'ps' in args else ''
            return subprocess.CompletedProcess(args,0,output,'')
        with patch.object(power,'healthy',return_value=False),patch.object(power,'stop_ollama_if_unused'),patch.object(power,'phase') as phase,patch.object(power,'command',side_effect=command) as cmd:
            power.stop()
            self.assertFalse(any('desktop' in c.args[0] and 'stop' in c.args[0] for c in cmd.call_args_list))
            self.assertEqual(phase.call_args.args[0],'shared')

    def test_other_n8n_execution_is_preserved(self):
        with patch.object(power,'healthy',return_value=True),patch.object(power,'query',return_value=[{'id':1}]),patch.object(power.time,'monotonic',side_effect=[0,100]),patch.object(power,'command') as command,patch.object(power,'phase') as phase:
            power.stop();command.assert_not_called();self.assertEqual(phase.call_args.args[0],'deferred')

    def test_other_loaded_model_is_preserved(self):
        def request(url,*args,**kwargs):
            return {'models':[{'name':'other-model'}]} if url.endswith('/api/ps') else {}
        with patch.object(power,'healthy',return_value=True),patch.object(power,'request',side_effect=request) as req,patch.object(power,'command') as command:
            power.stop_ollama_if_unused();command.assert_not_called()
            self.assertEqual(req.call_args_list[0].args[1]['keep_alive'],0)

    def test_own_ollama_listener_is_stopped(self):
        def command(args,**kwargs):
            if args[0]=='/usr/sbin/lsof':output='12345'
            elif '-p' in args:output=power.OLLAMA
            else:output=''
            return subprocess.CompletedProcess(args,0,output,'')
        with patch.object(power,'healthy',return_value=True),patch.object(power,'request',return_value={'models':[]}),patch.object(power,'command',side_effect=command),patch.object(power.os,'kill') as kill:
            self.assertTrue(power.stop_ollama_if_unused())
            kill.assert_called_once_with(12345,power.signal.SIGTERM)

    def test_schedule_boundaries_and_midnight(self):
        import datetime as d
        for now,want in [('2026-11-07T06:59:00+03:00','2026-11-06T23:00:00+03:00'),('2026-11-07T07:00:00+03:00','2026-11-07T07:00:00+03:00'),('2026-11-07T22:59:00+03:00','2026-11-07T07:00:00+03:00'),('2026-11-07T23:00:00+03:00','2026-11-07T23:00:00+03:00'),('2026-11-08T01:00:00+03:00','2026-11-07T23:00:00+03:00')]:
            with self.subTest(now=now):self.assertEqual(wake.due_slot(d.datetime.fromisoformat(now)).isoformat(),want)

    def test_completed_plan_suppresses_start(self):
        import datetime as d
        now=d.datetime.fromisoformat('2026-11-07T10:00:00+03:00')
        self.assertEqual(wake.disposition([{'status':'success','startedAt':'2026-11-07 04:01:00'}],wake.due_slot(now),now),'done')

if __name__=='__main__':unittest.main()
