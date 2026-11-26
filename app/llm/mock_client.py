from __future__ import annotations

import re

from app.llm.base import LLMClient
from app.models import GeneratedSQL


class MockLLMClient(LLMClient):
    def generate_sql(
        self,
        request: str,
        schema: dict,
        error_context: str | None = None,
    ) -> GeneratedSQL:
        text = request.lower().strip()

        if error_context:
            if "age" in error_context.lower() and "does not exist" in error_context:
                return GeneratedSQL(
                    query="SELECT full_name, email FROM customers",
                    parameters={},
                    explanation="Corrected: column 'age' does not exist; returning full_name and email.",
                )
            if "orders_archive" in error_context.lower():
                return GeneratedSQL(
                    query="SELECT * FROM orders ORDER BY created_at DESC LIMIT 10",
                    parameters={},
                    explanation="Corrected: using the existing orders table.",
                )

        match = re.search(r"(?:latest|recent)\s+(\d+)\s+orders", text)
        if match:
            limit = int(match.group(1))
            return GeneratedSQL(
                query=(
                    "SELECT * FROM orders "
                    "ORDER BY created_at DESC "
                    f"LIMIT {limit}"
                ),
                parameters={},
                explanation=f"Retrieve the {limit} most recent orders.",
            )

        if "registered" in text and ("last month" in text or "past month" in text):
            return GeneratedSQL(
                query=(
                    "SELECT * FROM customers "
                    "WHERE created_at >= NOW() - INTERVAL '1 month'"
                ),
                parameters={},
                explanation="Find customers who registered during the last month.",
            )

        if "how many orders" in text and "each customer" in text:
            return GeneratedSQL(
                query=(
                    "SELECT customer_id, COUNT(*) AS order_count "
                    "FROM orders GROUP BY customer_id"
                ),
                parameters={},
                explanation="Count orders per customer.",
            )

        match = re.search(r"change (?:the )?price of product (\d+) to (\d+)", text)
        if match:
            product_id = int(match.group(1))
            price = int(match.group(2))
            return GeneratedSQL(
                query=(
                    "UPDATE products SET price = :price "
                    "WHERE id = :product_id"
                ),
                parameters={"price": price, "product_id": product_id},
                explanation=f"Update the price of product {product_id} to {price}.",
            )

        match = re.search(r"delete customer (\d+)", text)
        if match:
            customer_id = int(match.group(1))
            return GeneratedSQL(
                query="DELETE FROM customers WHERE id = :id",
                parameters={"id": customer_id},
                explanation=f"Delete customer {customer_id}.",
            )

        return GeneratedSQL(
            query="",
            parameters={},
            explanation=(
                "The mock model only understands a few fixed sentences, e.g. "
                "'Show me the latest 10 orders' or 'change the price of product 5 to 250'. "
                "Set LLM_PROVIDER=ollama or openai in .env for real questions."
            ),
        )
