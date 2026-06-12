"""
providers/openrouter.py - OpenRouter provider.
"""

import asyncio
import os
import requests

TIMEOUT = 12


async def health_check() -> bool:
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}"},
                    json={
                        "model": "mistralai/mistral-7b-instruct",
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 5,
                    },
                    timeout=TIMEOUT,
                ),
            ),
            timeout=TIMEOUT,
        )
        return True
    except Exception:
        return False


async def chat(model: str, prompt: str) -> dict:
    try:
        response = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=TIMEOUT,
                ),
            ),
            timeout=TIMEOUT,
        )
        data = response.json()
        usage = data.get("usage", {})
        return {
            "response": data["choices"][0]["message"]["content"],
            "provider": "openrouter",
            "model":    model,
            "tokens": {
                "prompt_tokens":     usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens":      usage.get("total_tokens", 0),
            },
        }
    except asyncio.TimeoutError:
        raise Exception(f"OpenRouter timeout: no response within {TIMEOUT}s")
    except Exception as e:
        raise Exception(f"OpenRouter error: {str(e)}")
