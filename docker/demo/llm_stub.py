# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""An OpenAI-compatible chat endpoint for the smoke test: python3 llm_stub.py PORT.

Every request gets a submit_insight call that cites the first event id of the window, so the
agent's model path in the image (LiteLLM, hash-locked, over HTTP) runs without a key or the
internet. Nothing else: it is not a model."""

import http.server
import json
import re
import sys
import time


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = " ".join(m["content"] for m in body["messages"] if isinstance(m.get("content"), str))
        ids = re.findall(r"id=([0-9a-f-]{36})", text)
        insight = {
            "title": f"The stub model read {len(ids)} event(s)",
            "summary": "Smoke test of the model path: LiteLLM, the tool loop and the harness checks.",
            "assessment": "WARN",
            "hypotheses": [{"cause": "Nothing: this is a stub", "confidence": 0.1, "evidence": ids[:1]}],
            "recommendations": [],
        }
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "submit_insight", "arguments": json.dumps(insight)},
        }
        reply = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": len(text) // 4,
                "completion_tokens": 50,
                "total_tokens": len(text) // 4 + 50,
            },
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
