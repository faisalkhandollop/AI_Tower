from uuid import UUID
from typing import Optional

from sqlalchemy.orm import Session

from database.models import UsageLog, Model
from services.cost import calculate_cost


def log_usage(
    db: Session,
    user_id: UUID,
    model_id: UUID,
    request_id: UUID,
    prompt: str,
    response: str,
    complexity: str,
    input_tokens: int,
    output_tokens: int,
    session_id: Optional[str] = None,     # ← NEW — nullable, Lokesh passes this in
) -> dict:
    """
    Core logger function.
    Called after every AI request completes.

    Steps:
    1. Fetch model pricing from DB
    2. Calculate cost via calculate_cost()
    3. Insert UsageLog row (now includes session_id)
    4. Return final structured output

    Args:
        db:            SQLAlchemy session (injected)
        user_id:       UUID of the requesting user
        model_id:      UUID of the AI model used
        request_id:    UUID of the associated request
        prompt:        The original user prompt
        response:      The AI-generated response text
        complexity:    "simple" | "medium" | "complex" (from Sandesh's router)
        input_tokens:  Prompt token count
        output_tokens: Completion token count
        session_id:    Optional session_id string from Sandesh's workspace router
    """

    # 1. Fetch model
    model = db.query(Model).filter(
        Model.id == model_id
    ).first()

    if not model:
        raise ValueError(f"Model with id {model_id} not found")

    # 2. Calculate cost
    cost_data = calculate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_per_1k=float(model.input_cost_per_1k),
        output_cost_per_1k=float(model.output_cost_per_1k)
    )

    total_tokens = input_tokens + output_tokens

    # 3. Insert UsageLog (session_id wires this log to Sandesh's session)
    usage = UsageLog(
        user_id=user_id,
        model_id=model_id,
        request_id=request_id,
        session_id=session_id,             # ← NEW
        prompt=prompt,
        response=response,
        provider=model.provider,
        model=model.model_name,
        complexity=complexity,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost=cost_data["total_cost"]
    )

    db.add(usage)
    db.commit()
    db.refresh(usage)

    # 4. Return final structured output
    return {
        "prompt":         prompt,
        "complexity":     complexity,
        "provider":       model.provider,
        "model":          model.model_name,
        "response":       response,
        "input_tokens":   input_tokens,
        "output_tokens":  output_tokens,
        "cost":           cost_data["total_cost"],
        "session_id":     session_id,      # ← NEW — echoed back for confirmation
    }