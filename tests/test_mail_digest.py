import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import mail_digest as mail

class MailTests(unittest.TestCase):
 def test_html_and_attachment(self):
  raw=b'Subject: Test\nFrom: sender@example.com\nMIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n<p>Hello</p><script>evil()</script><img src="https://example.com/track">'
  result=mail.message_text(raw)
  self.assertEqual(result['text'], 'Hello')
 def test_read_only_uid_window(self):
  client=MagicMock()
  client.response.side_effect=lambda key: (key,[b'1' if key=='UIDVALIDITY' else b'3'])
  client.uid.side_effect=[('OK',[b'2']),('OK',[b'2 (INTERNALDATE "10-Nov-2026 10:00:00 +0300" RFC822.SIZE 70)']),('OK',[(b'2',b'Subject: New\nFrom: a@example.com\nContent-Type: text/plain\n\nHello')])]
  state={'gmail':{'connected':True,'address':'a@example.com','uid':1,'validity':'1','reportedAt':'2026-11-10T04:00:00+00:00'}}
  with patch.object(mail,'load',return_value=state),patch.object(mail,'secret',return_value={}),patch.object(mail,'login',return_value=client):
   result=mail.collect('gmail')
  self.assertEqual(result['status'],'ok'); self.assertEqual(result['checkpoint']['uid'],2)
  self.assertIn('BODY.PEEK',client.uid.call_args_list[-1].args[-1]);client.logout.assert_called_once()
 def test_login_examines_not_select_writable(self):
  client=MagicMock();client.select.return_value=('OK',[b'0'])
  with patch.object(mail.imaplib,'IMAP4_SSL',return_value=client): mail.login('gmail',{'address':'a','password':'b'})
  client.select.assert_called_once_with('INBOX',readonly=True)
 def test_commit_failure_and_account_change(self):
  state={'gmail':{'address':'a','uid':1},'yandex':{'address':'b','uid':2}}
  snap={'gmail':{'status':'ok','address':'a','summaryFailed':True,'checkpoint':{'uid':5}},'yandex':{'status':'ok','address':'other','checkpoint':{'uid':9}}}
  with patch.object(mail,'load',return_value=state),patch.object(mail,'write_json') as write:
   mail.commit(snap)
  self.assertEqual(write.call_args.args[1]['gmail']['uid'],1);self.assertEqual(state['yandex']['uid'],2)
 def test_summary_failure_keeps_checkpoint(self):
  snap={'gmail':{'status':'ok','since':'2026-11-10T04:00:00+00:00','messages':[{'subject':'Hi','sender':'A','text':'Hi','partial':False}],'more':False}}
  with patch.object(mail,'request',side_effect=TimeoutError): result=mail.render(snap)
  self.assertTrue(snap['gmail']['summaryFailed']);self.assertIn('повторится',result)

if __name__=='__main__': unittest.main()
