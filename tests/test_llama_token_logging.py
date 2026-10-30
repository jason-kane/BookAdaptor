"""Report separate input and output token usage without contacting Llama."""
from pathlib import Path
import ast
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import llama_inference


class LlamaTokenLoggingTests(unittest.TestCase):
    def test_illustration_prompt_stays_a_string_through_the_gpu_queue(self):
        source = Path(__file__).resolve().parents[1] / 'src/artifact_editor/images/images.py'
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == 'generate_image_description')
        assignment = next(node for node in function.body
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'prompt'
                                  for target in node.targets))
        namespace = {'text': 'A pigeon sees a painted goblet.',
                     'context': 'Earlier narration.', 'metadata': {'setting': 'street'}}
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), str(source), 'exec'), namespace)
        # Redis serializes the producer's arguments before the GPU worker reads them.
        request = json.loads(json.dumps({'prompt': namespace['prompt']}))
        self.assertIsInstance(request['prompt'], str)
        client = MagicMock()
        client.post.return_value = llama_inference.httpx.Response(
            200, json={'choices': [{'message': {'content': 'An illustration.'}}]})
        with patch('llama_inference.httpx.Client') as factory:
            factory.return_value.__enter__.return_value = client
            self.assertEqual(llama_inference._request(request), 'An illustration.')
        content = client.post.call_args.kwargs['json']['messages'][1]['content']
        self.assertIsInstance(content, str)
        self.assertIn(namespace['text'], content)

    def test_invalid_prompt_is_rejected_before_http(self):
        for prompt in [[], ['first', 'second'], [{'type': 'text', 'text': 'wrong shape'}]]:
            with self.subTest(prompt=prompt), patch('llama_inference.httpx.Client') as factory:
                with self.assertRaisesRegex(TypeError, 'Llama prompt must be a string'):
                    llama_inference._request({'prompt': prompt})
                factory.assert_not_called()

    def test_already_queued_legacy_prompt_is_unwrapped(self):
        request = json.loads(json.dumps({'prompt': ('An illustration prompt.',)}))
        client = MagicMock()
        client.post.return_value = llama_inference.httpx.Response(
            200, json={'choices': [{'message': {'content': 'An illustration.'}}]})
        with patch('llama_inference.httpx.Client') as factory:
            factory.return_value.__enter__.return_value = client
            self.assertEqual(llama_inference._request(request), 'An illustration.')
        self.assertEqual(client.post.call_args.kwargs['json']['messages'][1]['content'],
                         'An illustration prompt.')

    def test_output_cap_and_input_usage_are_logged_separately(self):
        client = MagicMock()
        client.post.return_value.is_error = False
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

    def test_zero_thinking_budget_disables_reasoning_in_llama_api(self):
        client = MagicMock()
        client.post.return_value = llama_inference.httpx.Response(
            200, json={'choices': [{'message': {'content': 'A boy leaves.'}, 'finish_reason': 'stop'}]})
        with patch('llama_inference.httpx.Client') as factory:
            factory.return_value.__enter__.return_value = client
            llama_inference._request({'prompt': 'Describe the scene', 'thinking_budget_tokens': 0})
        self.assertEqual(client.post.call_args.kwargs['json']['reasoning_effort'], 'none')
        self.assertEqual(client.post.call_args.kwargs['json']['reasoning_format'], 'deepseek')
        self.assertNotIn('thinking_budget_tokens', client.post.call_args.kwargs['json'])

    def test_reasoning_only_response_is_not_reported_as_success(self):
        client = MagicMock()
        client.post.return_value = llama_inference.httpx.Response(200, json={
            'choices': [{'message': {'content': '', 'reasoning_content': 'Still reasoning...'},
                         'finish_reason': 'length'}]})
        with patch('llama_inference.httpx.Client') as factory:
            factory.return_value.__enter__.return_value = client
            with self.assertRaisesRegex(ValueError, 'empty final response'):
                llama_inference._request({'prompt': 'Describe the scene', 'max_tokens': 512})
