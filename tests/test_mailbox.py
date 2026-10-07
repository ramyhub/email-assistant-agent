import sqlite3
import unittest
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from email_assistant.config import load_config
from email_assistant.mailbox import matches, sync


class FakeGraph:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []
    def get(self, url):
        self.urls.append(url)
        return self.pages.pop(0)


class MailboxClientTests(unittest.TestCase):
    def test_environment_overrides_legacy_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text('{"email":"old@illinois.edu","poll_seconds":60}')
            env = {'MAIL_EMAIL': 'new@illinois.edu', 'POLL_SECONDS': '300',
                   'MAIL_KEYWORDS': 'deadline, Meeting'}
            with patch.dict(os.environ, env, clear=True), patch.dict('sys.modules', {
                    'dotenv': SimpleNamespace(load_dotenv=Mock())}):
                result = load_config(config, Path(directory) / '.env')
            self.assertEqual(result['email'], 'new@illinois.edu')
            self.assertEqual(result['poll_seconds'], 300)
            self.assertEqual(result['keywords'], ['deadline', 'Meeting'])

    def test_baseline_pagination_and_restart_deduplication(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE seen (id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)')
        events = []
        emit = lambda message: events.append(message['id'])
        sync(db, FakeGraph([
            {'value': [{'id': 'old'}], '@odata.nextLink': 'page2'},
            {'value': [{'id': 'older'}], '@odata.deltaLink': 'cursor1'}]), {}, emit)
        self.assertEqual(events, [])
        graph = FakeGraph([{'value': [{'id': 'old'}, {'id': 'new'},
            {'id': 'gone', '@removed': {}}], '@odata.deltaLink': 'cursor2'}])
        sync(db, graph, {}, emit)
        self.assertEqual(graph.urls, ['cursor1'])
        self.assertEqual(events, ['new'])
        sync(db, FakeGraph([{'value': [{'id': 'new'}], '@odata.deltaLink': 'cursor3'}]), {}, emit)
        self.assertEqual(events, ['new'])

    def test_filters(self):
        message = {'subject': 'Deadline tomorrow', 'from': {'emailAddress': {'address': 'Prof@illinois.edu'}}}
        self.assertTrue(matches(message, {'senders': ['prof@illinois.edu'], 'keywords': ['deadline']}))
        self.assertFalse(matches(message, {'senders': ['other@illinois.edu']}))
        self.assertFalse(matches(message, {'keywords': ['invoice']}))

    def test_failed_page_preserves_cursor_and_no_duplicate_alert(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE seen (id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)')
        db.execute("INSERT INTO state VALUES ('cursor', 'previous')")
        events = []
        emit = lambda message: events.append(message['id'])
        with self.assertRaises(IndexError):
            sync(db, FakeGraph([{'value': [{'id': 'new'}], '@odata.nextLink': 'page2'}]), {}, emit)
        self.assertEqual(db.execute('SELECT value FROM state').fetchone()[0], 'previous')
        sync(db, FakeGraph([{'value': [{'id': 'new'}], '@odata.deltaLink': 'next'}]), {}, emit)
        self.assertEqual(events, ['new'])


if __name__ == '__main__':
    unittest.main()
