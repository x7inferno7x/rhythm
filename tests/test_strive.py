import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import strive

class StriveTests(unittest.TestCase):
    project={'id':7,'name':'Учёба','archiveStageId':99,'stages':[{'id':20,'name':'7'}]}
    def task(self,**changes):
        item={'id':1,'name':'Задание','status':0,'responsibleUserId':[5],'importance':1,'stageId':20}
        item.update(changes);return item
    def test_numeric_column_dates_and_ranges(self):
        today=dt.date(2026,11,7)
        self.assertEqual(strive.column_dates('6',today)['from'],'2026-11-06')
        self.assertEqual(strive.column_dates('9–10',today)['to'],'2026-11-10')
        self.assertEqual(strive.column_dates('9 - 10',today)['from'],'2026-11-09')
        for value in ['Готово','Задачи 6','0','32','31','10-9','6.11']:
            self.assertIsNone(strive.column_dates(value,today),value)
    def test_non_numeric_columns_excluded(self):
        p={'id':7,'name':'Учёба','stages':[{'id':20,'name':'В работе'}]}
        self.assertIsNone(strive.normalize(self.task(),p,5))
    def test_task_stages_resolve_numeric_column(self):
        t=self.task(stageId=None,stages=[{'projectId':7,'stageId':20}])
        self.assertEqual(strive.normalize(t,self.project,5,dt.date(2026,11,7))['columnDates'][0]['from'],'2026-11-07')
    def test_excludes_completed_and_rejected(self):
        for status in [1,2,3,None]:
            self.assertIsNone(strive.normalize(self.task(status=status),self.project,5))
    def test_excludes_tasks_assigned_to_other_people(self):
        self.assertIsNone(strive.normalize(self.task(responsibleUserId=[6]),self.project,5))
        self.assertIsNotNone(strive.normalize(self.task(responsibleUserId=[]),self.project,5))
    def test_archive_filter(self):
        self.assertIsNone(strive.normalize(self.task(stages=[{'stageId':99}]),self.project,5))
        self.assertIsNone(strive.normalize(self.task(stageId=99),self.project,5))
    def test_dates_and_duration(self):
        task=strive.normalize(self.task(dateEnd='2026-11-07T22:30:00Z',planTime=5400),self.project,5)
        self.assertEqual(task['deadline'],'2026-11-08T01:30+03:00');self.assertEqual(task['estimatedMinutes'],90)
        task=strive.normalize(self.task(dateEndWithoutTime='2026-11-07'),self.project,5)
        self.assertEqual(task['deadline'],'2026-11-07')
        self.assertIsNone(strive.local_date('2026-11-07T10:00:00'))
    def test_priority_and_frozen_flag(self):
        task=strive.normalize(self.task(importance=-2),self.project,5)
        self.assertTrue(task['frozen']);self.assertEqual(task['priority'],'Заморожено')
    def test_write_paths_are_blocked(self):
        with patch.object(strive.subprocess,'run') as run:
            with self.assertRaises(strive.StriveError):strive.get('/tasks/5/change-task-status','strive_at_dummy_token_for_unit_tests_only')
            run.assert_not_called()
    def test_token_not_in_process_arguments(self):
        token='strive_at_dummy_token_for_unit_tests_only'
        with patch.object(strive.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'[]\n200',b'')) as run:
            self.assertEqual(strive.get('/spaces',token),[])
            self.assertNotIn(token,str(run.call_args.args[0]));self.assertIn(token.encode(),run.call_args.kwargs['input'])
            self.assertIn('GET',run.call_args.args[0]);self.assertNotIn('--location',run.call_args.args[0])
    def test_http_error_cannot_echo_secret(self):
        token='strive_at_dummy_token_for_unit_tests_only'
        with patch.object(strive.subprocess,'run',return_value=subprocess.CompletedProcess([],0,token.encode()+b'\n403',b'')):
            with self.assertRaises(strive.StriveError) as error:strive.get('/spaces',token)
            self.assertNotIn(token,str(error.exception))
    def test_not_configured_is_not_empty_success(self):
        with patch.object(strive,'get_token',return_value=None):
            self.assertEqual(strive.snapshot()['status'],'not_configured')
    def test_invalid_collection_is_error(self):
        with self.assertRaises(strive.StriveError):strive.list_response({'count':0})

if __name__=='__main__':unittest.main()
