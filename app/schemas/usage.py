from pydantic import BaseModel
from typing import Literal, Optional


# Called by Lokesh's gateway
class UsageLogCreate(BaseModel):
    prompt:        str
    response:      str
    provider:      str
    model:         str
    complexity:    Literal["low", "medium", "high"]
    input_tokens:  int
    output_tokens: int
    user_id:       Optional[str] = None    # ← FIX: was missing, Lokesh's user_id was being silently dropped
    session_id:    Optional[str] = None    # ← for session tracking


class UsageReportResponse(BaseModel):
    total_requests:      int
    total_input_tokens:  int
    total_output_tokens: int
    total_cost:          float