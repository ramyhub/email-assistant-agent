import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from codex_chat import CodexBrain, Conversation


def action(kind, number=0, reply='', query=''):
    return {'action': kind, 'query': query, 'number': number, 'reply': reply}


class ChatTests(unittest.TestCase):
    def test_reads_before_summarizing(self):
        assistant = Mock()
        assistant.results = []
        assistant.graph.get.side_effect = [
            {'value': [{'id': 'abc', 'subject': 'Deadline'}]},
            {'subject': 'Deadline', 'body': {'content': 'Due Friday'}}]
        brain = Mock()
        brain.plan.side_effect = [action('inbox'), action('read', 1), action('answer', reply='Due Friday.')]
        conversation = Conversation(assistant, brain)
        self.assertEqual(conversation.respond('What is due?'), 'Due Friday.')
        context = brain.plan.call_args[0][0]
        self.assertEqual(context['observations'][1]['email']['body'], 'Due Friday')
        self.assertEqual(len(conversation.history), 2)

    def test_today_review_fetches_bodies_in_one_step(self):
        assistant = Mock()
        assistant.results = []
        assistant.graph.get.return_value = {'value': [
            {'id': 'today-email', 'subject': 'Please respond', 'body': {'content': 'Confirm by tonight.'}}
        ], '@odata.nextLink': 'more'}
        brain = Mock()
        brain.plan.side_effect = [action('today'), action('answer', reply='Confirm by tonight.')]
        reply = Conversation(assistant, brain).respond('Anything important today I need to respond to?')
        self.assertEqual(reply, 'Confirm by tonight.')
        self.assertEqual(assistant.graph.get.call_count, 1)
        observation = brain.plan.call_args[0][0]['observations'][0]
        self.assertTrue(observation['more_messages'])
        self.assertEqual(observation['emails'][0]['body'], 'Confirm by tonight.')
        self.assertIn('%24filter=', assistant.graph.get.call_args[0][0])

    def test_limit_returns_partial_assessment(self):
        assistant = Mock()
        assistant.results = [{'id': 'one'}]
        assistant.graph.get.side_effect = lambda url: {'body': {'content': 'Please reply.'}}
        brain = Mock()
        brain.plan.side_effect = [action('read', 1)] * 6 + [action('answer', reply='Review incomplete: one email asks for a reply.')]
        conversation = Conversation(assistant, brain)
        reply = conversation.respond('Review my email')
        self.assertIn('Review incomplete', reply)
        self.assertTrue(brain.plan.call_args[0][0]['tools_exhausted'])
        self.assertEqual(conversation.history[-1]['text'], reply)

    def test_no_write_action_is_accepted(self):
        def runner(command, **kwargs):
            self.assertEqual(command[command.index('--model') + 1], 'gpt-6.1-sol')
            self.assertIn('model_reasoning_effort="medium"', command)
            path = Path(command[command.index('--output-last-message') + 1])
            path.write_text(json.dumps(action('send')))
            return Mock(returncode=0)
        brain = CodexBrain(runner=runner)
        with self.assertRaises(RuntimeError):
            brain.plan({'user_request': 'Send it'})

    def test_email_read_bound_and_step_limit(self):
        assistant = Mock()
        assistant.results = [{'id': 'abc'}]
        assistant.graph.get.side_effect = lambda url: {'body': {'content': 'x' * 20000}}
        brain = Mock()
        brain.plan.return_value = action('read', 1)
        reply = Conversation(assistant, brain).respond('Read it')
        self.assertIn('not completed', reply)
        self.assertEqual(brain.plan.call_count, 7)
        observed = brain.plan.call_args[0][0]['observations'][0]['email']
        self.assertEqual(len(observed['body']), 16000)
        self.assertTrue(observed['truncated'])


if __name__ == '__main__':
    unittest.main()
