"""
providers/gemini.py - Google Gemini provider.

Wraps google-generativeai in asyncio executor with timeout and health check.
"""

import asyncio
import os

import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))

TIMEOUT = 15  # seconds


async def health_check() -> bool:
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: genai.GenerativeModel("gemini-pro").generate_content(
                    "hi",
                    generation_config=genai.GenerationConfig(max_output_tokens=5),
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
                lambda: genai.GenerativeModel(model).generate_content(prompt),
            ),
            timeout=TIMEOUT,
        )

        # Extract token counts when available
        usage = getattr(response, "usage_metadata", None)
        input_tokens  = getattr(usage, "prompt_token_count",     0) if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0

        return {
            "response": response.text,
            "provider": "gemini",
            "model":    model,
            "tokens": {
                "prompt_tokens":     input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens":      input_tokens + output_tokens,
            },
        }
    except asyncio.TimeoutError:
        raise Exception(f"Gemini timeout: no response within {TIMEOUT}s")
    except Exception as e:
        raise Exception(f"Gemini error: {str(e)}")
