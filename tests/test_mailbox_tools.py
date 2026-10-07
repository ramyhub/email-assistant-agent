import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse
from email_assistant.mailbox_tools import MailboxTools, create_server


class MailboxToolTests(unittest.TestCase):
    def test_today_filters_and_pagination_disclose_coverage(self):
        graph = Mock()
        graph.get.return_value = {'value': [{'id': 'one', 'body': {'content': 'x' * 5000}}],
            '@odata.nextLink': 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=30'}
        tools = MailboxTools(graph)
        result = tools.list_messages(received_after='2026-10-07T00:00:00-05:00',
            received_before='2026-10-08T00:00:00-05:00', include_bodies=True, limit=30)
        query = parse_qs(urlparse(graph.get.call_args.args[0]).query)
        self.assertEqual(query['$filter'], ['receivedDateTime ge 2026-10-07T05:00:00Z and receivedDateTime lt 2026-10-08T05:00:00Z'])
        self.assertIn('sentDateTime', query['$select'][0])
        self.assertTrue(result['messages'][0]['truncated'])
        self.assertEqual(len(result['messages'][0]['body']), 4000)
        tools.list_messages(next_link=result['next_link'])
        self.assertEqual(graph.get.call_args.args[0], result['next_link'])

    def test_search_mailbox_and_encoded_message_ids(self):
        graph = Mock()
        graph.get.return_value = {'value': []}
        tools = MailboxTools(graph)
        tools.list_messages(search='from:professor', folder='all')
        self.assertEqual(urlparse(graph.get.call_args.args[0]).path, '/v1.0/me/messages')
        graph.get.return_value = {'body': {'content': 'a' * 16000 + 'end'}}
        result = tools.read_message('a/b+c', body_offset=16000)
        self.assertIn('/messages/a%2Fb%2Bc?', graph.get.call_args.args[0])
        self.assertIn('sentDateTime', parse_qs(urlparse(graph.get.call_args.args[0]).query)['$select'][0])
        self.assertEqual(result['body'], 'end')
        self.assertEqual(result['body_length'], 16003)

    def test_invalid_inputs_never_reach_graph(self):
        graph = Mock()
        tools = MailboxTools(graph)
        for kwargs in ({'next_link': 'https://example.com/'}, {'next_link': 'https://graph.microsoft.com/v1.0/me/contacts'},
                       {'limit': 1000}, {'folder': 'trash'}, {'received_after': '2026-10-07'},
                       {'search': 'hello', 'received_after': '2026-10-07T00:00:00Z'}):
            with self.assertRaises(ValueError):
                tools.list_messages(**kwargs)
        graph.get.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install project dependencies to run MCP integration tests.')
class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_transport_initializes_and_reads_without_credentials(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'fixture.py'
            source_root = str(Path(__file__).resolve().parents[1] / 'src')
            source = (
                'import sys\n'
                f'sys.path.insert(0, {source_root!r})\n'
                'from email_assistant.mailbox_tools import create_server\n'
                'class Graph:\n'
                '    def get(self, url):\n'
                '        return {"body": {"content": "Due Friday"}, "subject": "Deadline"}\n'
                'create_server(Graph()).run(transport="stdio")\n'
            )
            script.write_text(source)
            async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(script)])) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual(len(listing.tools), 5)
                    result = await session.call_tool('read_message', {'message_id': 'one'})
                    self.assertFalse(result.isError)
                    self.assertEqual(json.loads(result.content[0].text)['body'], 'Due Friday')

    async def test_discovery_exposes_read_and_draft_tools_and_calls_graph(self):
        graph = Mock()
        graph.get.return_value = {'value': [{'id': 'one', 'subject': 'Deadline'}]}
        server = create_server(graph)
        tools = await server.list_tools()
        self.assertEqual({tool.name for tool in tools}, {'list_messages', 'read_message', 'mailbox_status',
                                                        'create_draft', 'create_reply_draft'})
        self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools
                            if tool.name in {'list_messages', 'read_message', 'mailbox_status'}))
        self.assertTrue(all(not tool.annotations.readOnlyHint for tool in tools
                            if tool.name in {'create_draft', 'create_reply_draft'}))
        self.assertTrue(all(not tool.annotations.destructiveHint for tool in tools))
        result = await server.call_tool('list_messages', {'folder': 'all', 'search': 'from:professor'})
        # FastMCP returns content plus structured data for annotated dict results.
        content = result[0] if isinstance(result, tuple) else result
        payload = json.loads(content[0].text)
        self.assertEqual(payload['messages'][0]['subject'], 'Deadline')
        self.assertEqual(graph.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
