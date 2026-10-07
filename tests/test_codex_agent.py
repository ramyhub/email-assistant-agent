import json
import queue
import unittest
from pathlib import Path
from unittest.mock import patch

from email_assistant.codex_agent import Conversation, INSTRUCTIONS, STE_SKILL

THREAD = 'thread-1'


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.output = queue.Queue()
        self.returncode = None
        self.stdout = iter(self._lines())
        self.stdin = self.Input(self)

    def _lines(self):
        while True:
            line = self.output.get()
            if line is None:
                return
            yield json.dumps(line)

    def respond(self, message):
        if message.get('method') == 'initialized':
            return
        method = message.get('method')
        request_id = message.get('id')
        if method == 'initialize':
            result = {}
        elif method in ('thread/start', 'thread/resume'):
            result = {'thread': {'id': THREAD}}
        elif method == 'turn/start':
            self.output.put({'id': request_id, 'result': {'turn': {'id': 'turn-1'}}})
            self.output.put({'method': 'item/completed', 'params': {
                'threadId': THREAD, 'item': {'type': 'agentMessage', 'text': 'A clear reply.'}}})
            self.output.put({'method': 'turn/completed', 'params': {
                'threadId': THREAD, 'turn': {'id': 'turn-1', 'status': 'completed'}}})
            return
        else:
            return
        self.output.put({'id': request_id, 'result': result})

    class Input:
        def __init__(self, process):
            self.process = process

        def write(self, line):
            self.process.respond(json.loads(line))

        def flush(self):
            pass

        def close(self):
            self.process.output.put(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode


class CodexAgentTests(unittest.TestCase):
    def test_skill_is_loaded_into_codex_workspace_and_user_turns(self):
        saved = []
        process = None

        def popen(command, **kwargs):
            nonlocal process
            process = FakeProcess(command, **kwargs)
            return process

        with patch('email_assistant.codex_agent.shutil.which', return_value='/usr/bin/codex'):
            conversation = Conversation({'email': 'test@example.edu', 'client_id': 'app'},
                                        save_thread_id=saved.append, popen=popen)
        root = Path(process.kwargs['cwd'])
        try:
            self.assertIn('Simplified Technical English', INSTRUCTIONS)
            self.assertEqual((root / '.agents/skills/simplified-technical-english/SKILL.md').read_text(),
                             STE_SKILL)
            self.assertIn('user-facing Telegram', (root / 'AGENTS.md').read_text())
            self.assertEqual(saved, [THREAD])
            self.assertEqual(conversation.respond('Summarize this.'), 'A clear reply.')
            self.assertIn('create_reply_draft', ' '.join(process.command))
        finally:
            conversation.close()

    def test_resume_uses_saved_thread(self):
        process = None

        def popen(command, **kwargs):
            nonlocal process
            process = FakeProcess(command, **kwargs)
            return process

        with patch('email_assistant.codex_agent.shutil.which', return_value='/usr/bin/codex'):
            conversation = Conversation({'email': 'test@example.edu', 'client_id': 'app'},
                                        saved_thread_id=THREAD, popen=popen)
        try:
            self.assertEqual(conversation.thread_id, THREAD)
            self.assertEqual(conversation.respond('Continue.'), 'A clear reply.')
        finally:
            conversation.close()


if __name__ == '__main__':
    unittest.main()
