import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.

from clashroyale_agent.api.sse import format_sse_data, split_stream_chunks


class ApiSseHelperTests(unittest.TestCase):
    def test_format_sse_data_serializes_unicode_payload(self):
        self.assertEqual(format_sse_data({"text": "当前环境"}), 'data: {"text": "当前环境"}\n\n')

    def test_split_stream_chunks_preserves_order_and_size(self):
        self.assertEqual(list(split_stream_chunks("abcdef", chunk_size=2)), ["ab", "cd", "ef"])


if __name__ == "__main__":
    unittest.main()
