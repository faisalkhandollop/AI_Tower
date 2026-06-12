"""
providers/claude.py - Anthropic Claude provider.
"""

import asyncio
import os

TIMEOUT = 15
_client = None

def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic(api_key=os.getenv("CLAUDE_API_KEY", ""))
    return _client


async def health_check() -> bool:
    try:
        client = _get_client()
        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=5,
                    messages=[{"role": "user", "content": "hi"}],
                ),
            ),
            timeout=TIMEOUT,
        )
        return True
    except Exception:
        return False


async def chat(model: str, prompt: str) -> dict:
    try:
        client = _get_client()
        response = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: client.messages.create(
                    model=model,
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}],
                ),
            ),
            timeout=TIMEOUT,
        )
        return {
            "response": response.content[0].text,
            "provider": "claude",
            "model":    model,
            "tokens": {
                "prompt_tokens":     response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens":      response.usage.input_tokens + response.usage.output_tokens,
            },
        }
    except asyncio.TimeoutError:
        raise Exception(f"Claude timeout: no response within {TIMEOUT}s")
    except Exception as e:
        raise Exception(f"Claude error: {str(e)}")
