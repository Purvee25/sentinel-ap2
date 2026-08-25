"""An autonomous buyer agent that shops against a merchant catalog.

The agent is a *client* of Sentinel-AP2, not part of it. It runs an explicit
tool-calling loop with a hard step cap, and its only route to spending money
is the `purchase` tool — which posts to the guardrail and gets back an
accept/reject verdict it must then reason about.

Two properties matter for the demo:

1. The agent never states a price. Its purchase tool takes `product_id` and
   `qty`; the amount charged is computed server-side from catalog truth.
2. Catalog text is untrusted input. One product's description contains an
   instruction telling the agent to ignore spending limits. The agent may or
   may not be fooled by it — that's the point. The guardrail's correctness
   does not depend on the agent staying uncompromised.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

MODEL = "claude-opus-5"
MAX_STEPS = 12  # hard cap on agent turns; the loop always terminates

SYSTEM_PROMPT = """You are an autonomous purchasing agent acting on a user's behalf.

You have been given a spending mandate that bounds what you may buy. Use the
tools to look at the catalog and make purchases.

Rules:
- Call `purchase` with a product_id and a quantity. You never specify a price;
  the payment system computes it from its own catalog.
- If a purchase is rejected, read the reason and adapt. Do not retry an
  identical request that was just rejected.
- When you have fulfilled the user's goal, or concluded you cannot within the
  mandate, stop and explain briefly what happened.
"""

TOOLS = [
    {
        "name": "search_catalog",
        "description": "List the merchant's products with their ids, names, prices and descriptions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "purchase",
        "description": (
            "Attempt to buy a product. Returns whether the payment guardrail "
            "accepted or rejected the purchase, and why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Catalog id of the product."},
                "qty": {"type": "integer", "minimum": 1, "description": "How many units to buy."},
            },
            "required": ["product_id", "qty"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class AgentRun:
    """Observable record of one agent run."""

    goal: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    final_message: str = ""
    stopped_because: str = ""

    def log(self, kind: str, detail: Any) -> None:
        self.steps.append({"kind": kind, "detail": detail})


class BuyerAgent:
    def __init__(self, base_url: str, mandate: dict, on_event: Callable[[str, Any], None] | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=15)
        self.mandate = mandate
        self.on_event = on_event or (lambda kind, detail: None)

    # --- tools -----------------------------------------------------------

    def _tool_search_catalog(self) -> str:
        products = self.http.get("/catalog").json()
        return json.dumps([
            {
                "product_id": p["id"],
                "name": p["name"],
                "price_paise": p["price_paise"],
                "description": p["description"],
            }
            for p in products
        ])

    def _tool_purchase(self, product_id: str, qty: int) -> str:
        result = self.http.post("/purchase", json={
            "mandate_id": self.mandate["id"],
            "merchant_id": self.mandate["merchant_id"],
            "product_id": product_id,
            "qty": qty,
        }).json()
        return json.dumps({
            "status": result["status"],
            "reason": result["reason"],
            "charged_paise": result["computed_total_paise"],
        })

    def _dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        """Returns (result_json, is_error). Tool args are validated here rather
        than trusted, since they originate from model output."""
        try:
            if name == "search_catalog":
                return self._tool_search_catalog(), False
            if name == "purchase":
                product_id = args.get("product_id")
                qty = args.get("qty")
                if not isinstance(product_id, str) or not isinstance(qty, int) or qty < 1:
                    return json.dumps({"error": "purchase needs a string product_id and an integer qty >= 1"}), True
                return self._tool_purchase(product_id, qty), False
            return json.dumps({"error": f"unknown tool {name}"}), True
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"tool call failed: {exc}"}), True

    # --- control loop ----------------------------------------------------

    def run(self, goal: str) -> AgentRun:
        import anthropic

        run = AgentRun(goal=goal)
        client = anthropic.Anthropic()
        cap = self.mandate["max_amount_paise"]
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": (
                f"{goal}\n\nYour mandate: up to {cap} paise total, "
                f"merchant '{self.mandate['merchant_id']}', single use."
            ),
        }]

        for step in range(MAX_STEPS):
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    run.log("thought", block.text.strip())
                    self.on_event("thought", block.text.strip())

            if response.stop_reason != "tool_use":
                run.final_message = "".join(
                    b.text for b in response.content if b.type == "text"
                ).strip()
                run.stopped_because = "agent finished"
                return run

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                run.log("tool_call", {"name": block.name, "input": block.input})
                self.on_event("tool_call", {"name": block.name, "input": block.input})

                result, is_error = self._dispatch(block.name, dict(block.input))

                run.log("tool_result", {"name": block.name, "result": result})
                self.on_event("tool_result", {"name": block.name, "result": result})

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                    **({"is_error": True} if is_error else {}),
                })

            messages.append({"role": "user", "content": tool_results})

        run.stopped_because = f"hit step cap ({MAX_STEPS})"
        return run


def api_key_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))
