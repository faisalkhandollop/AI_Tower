def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_1k: float,
    output_cost_per_1k: float
) -> dict:
    """
    Calculate the cost of an AI request.

    Args:
        input_tokens:       Number of input/prompt tokens
        output_tokens:      Number of output/completion tokens
        input_cost_per_1k:  Cost per 1000 input tokens (from Model table)
        output_cost_per_1k: Cost per 1000 output tokens (from Model table)

    Returns:
        dict with input_cost, output_cost, total_cost
    """

    input_cost = (input_tokens / 1000) * input_cost_per_1k

    output_cost = (output_tokens / 1000) * output_cost_per_1k

    total_cost = input_cost + output_cost

    return {
        "input_cost": round(input_cost, 6),
        "output_cost": round(output_cost, 6),
        "total_cost": round(total_cost, 6)
    }