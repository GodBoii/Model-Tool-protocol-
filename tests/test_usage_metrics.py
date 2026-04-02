from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.providers.common import extract_usage_metrics


class UsageMetricsTests(unittest.TestCase):
    def test_openai_like_usage_with_reasoning_and_prompt_cache(self) -> None:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=45,
                total_tokens=165,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=11),
                prompt_tokens_details=SimpleNamespace(cached_tokens=30, cache_write_tokens=8),
            )
        )

        metrics = extract_usage_metrics(response)
        self.assertEqual(metrics["input_tokens"], 120)
        self.assertEqual(metrics["output_tokens"], 45)
        self.assertEqual(metrics["total_tokens"], 165)
        self.assertEqual(metrics["reasoning_tokens"], 11)
        self.assertEqual(metrics["cached_input_tokens"], 30)
        self.assertEqual(metrics["cache_write_tokens"], 8)

    def test_anthropic_usage_with_cache_fields(self) -> None:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=250,
                cache_creation_input_tokens=400,
                cache_read_input_tokens=120,
            )
        )

        metrics = extract_usage_metrics(response)
        self.assertEqual(metrics["input_tokens"], 1000)
        self.assertEqual(metrics["output_tokens"], 250)
        self.assertEqual(metrics["total_tokens"], 1250)
        self.assertEqual(metrics["cache_creation_input_tokens"], 400)
        self.assertEqual(metrics["cache_read_input_tokens"], 120)

    def test_gemini_usage_metadata_with_thinking_and_tool_use_tokens(self) -> None:
        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=60,
                candidates_token_count=20,
                total_token_count=95,
                thoughts_token_count=10,
                tool_use_prompt_token_count=5,
                cached_content_token_count=12,
            )
        )

        metrics = extract_usage_metrics(response)
        self.assertEqual(metrics["input_tokens"], 60)
        self.assertEqual(metrics["output_tokens"], 20)
        self.assertEqual(metrics["total_tokens"], 95)
        self.assertEqual(metrics["reasoning_tokens"], 10)
        self.assertEqual(metrics["tool_use_prompt_tokens"], 5)
        self.assertEqual(metrics["cached_input_tokens"], 12)

    def test_camel_case_usage_keys_are_supported(self) -> None:
        response = {
            "usageMetadata": {
                "promptTokenCount": 50,
                "responseTokenCount": 25,
                "totalTokenCount": 75,
                "thoughtsTokenCount": 7,
            }
        }

        metrics = extract_usage_metrics(response)
        self.assertEqual(metrics["input_tokens"], 50)
        self.assertEqual(metrics["output_tokens"], 25)
        self.assertEqual(metrics["total_tokens"], 75)
        self.assertEqual(metrics["reasoning_tokens"], 7)


if __name__ == "__main__":
    unittest.main()

