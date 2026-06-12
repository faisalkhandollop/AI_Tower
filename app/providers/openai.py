"""
providers/openai.py - OpenAI provider.
"""

import asyncio
import os
from typing import Optional

TIMEOUT = 15
_client = None

def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    return _client


async def health_check() -> bool:
    try:
        client = _get_client()
        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=5,
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
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                ),
            ),
            timeout=TIMEOUT,
        )
        return {
            "response": response.choices[0].message.content,
            "provider": "openai",
            "model":    model,
            "tokens": {
                "prompt_tokens":     response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens":      response.usage.total_tokens,
            },
        }
    except asyncio.TimeoutError:
        raise Exception(f"OpenAI timeout: no response within {TIMEOUT}s")
    except Exception as e:
        raise Exception(f"OpenAI error: {str(e)}")
