"""Report separate input and output token usage without contacting Llama."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import llama_inference


class LlamaTokenLoggingTests(unittest.TestCase):
    def test_output_cap_and_input_usage_are_logged_separately(self):
        client = MagicMock()
        client.post.return_value.json.return_value = {
            'choices': [{'message': {'content': '{}'}, 'finish_reason': 'length'}],
            'usage': {'prompt_tokens': 987, 'completion_tokens': 1536, 'total_tokens': 2523},
        }
        with patch('llama_inference.httpx.Client') as factory, \
                self.assertLogs('llama_inference', level='INFO') as captured:
            factory.return_value.__enter__.return_value = client
            result = llama_inference._request({'prompt': 'test', 'max_tokens': 1536})
        self.assertEqual(result, '{}')
        self.assertEqual(client.post.call_args.kwargs['json']['max_tokens'], 1536)
        output = '\n'.join(captured.output)
        self.assertIn('input=987 output=1536', output)
        self.assertIn('max_output=1536 finish_reason=length', output)
