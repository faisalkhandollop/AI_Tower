from pydantic import BaseModel
from typing import List, Optional


class DashboardResponse(BaseModel):
    total_requests:      int
    total_input_tokens:  int
    total_output_tokens: int
    total_tokens:        int
    total_cost:          float
    avg_cost_per_request: float


class UserUsageStat(BaseModel):
    user_id:         str
    name:            str
    email:           str
    department:      Optional[str]
    role:            Optional[str]
    is_active:       bool
    total_requests:  int
    total_tokens:    int
    total_cost:      float
    quota_limit:     int
    quota_used:      int
    quota_remaining: int


class UserReportResponse(BaseModel):
    top_users:   List[UserUsageStat]
    heavy_users: List[UserUsageStat]


class ProviderStat(BaseModel):
    provider:       str
    total_requests: int
    total_tokens:   int
    total_cost:     float


class ModelStat(BaseModel):
    provider:       str
    model_name:     str
    total_requests: int
    total_tokens:   int
    input_tokens:   int
    output_tokens:  int
    total_cost:     float


class ModelReportResponse(BaseModel):
    by_provider: List[ProviderStat]
    by_model:    List[ModelStat]
