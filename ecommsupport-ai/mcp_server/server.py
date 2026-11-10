"""NovaShop MCP server: structured data tools over MySQL.

Run it standalone (stdio transport, the way the agent launches it):

    python -m mcp_server.server

Security model
--------------
* Only three narrow, parameterised tools are exposed. There is no SQL,
  text-to-SQL or "run query" tool, by design.
* Every argument is validated against a strict pattern *before* it reaches
  the database, and every statement uses bound parameters.
* `customer_id` is a tool argument, but the agent never lets the LLM fill it:
  the orchestrator injects it from the authenticated session (see
  agent/mcp_client.py). An order that belongs to someone else is reported
  as "not found", so the tool cannot be used to probe other customers'
  orders.
* Outputs leave out email, phone and street address.
* The server connects with a least-privilege MySQL user (see db/schema.sql).
"""

from __future__ import annotations

import logging
import re
import secrets
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from mcp_server import db

# stdout is the MCP transport, so logs must go to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s mcp %(levelname)s %(message)s")
log = logging.getLogger("novashop.mcp")

ORDER_ID_RE = re.compile(r"^ORD-\d{4,8}$")
PRODUCT_ID_RE = re.compile(r"^NS-[A-Z]{3}-\d{3}$")
CUSTOMER_ID_RE = re.compile(r"^CUST-\d{3,6}$")
# Free-text product search: letters, digits, spaces and a little punctuation only.
SEARCH_RE = re.compile(r"^[\w\s\-\.'+&/]{2,60}$", re.UNICODE)

WARRANTY_RATES = {"none": Decimal("0"), "novacare": Decimal("0.10"), "novacare_plus": Decimal("0.18")}
MAX_QTY_PER_ORDER = 5

mcp = MCPServer(
    name="novashop-db",
    instructions="Parameterised read/write tools for NovaShop products and orders. No raw SQL.",
)


# --------------------------------------------------------------------------- helpers
def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _norm_order_id(order_id: str) -> str | None:
    oid = (order_id or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"\d{4,8}", oid):  # customer typed just "1001"
        oid = f"ORD-{oid}"
    if re.fullmatch(r"ORD\d{4,8}", oid):  # "ORD1001"
        oid = f"ORD-{oid[3:]}"
    return oid if ORDER_ID_RE.fullmatch(oid) else None


def _valid_customer(customer_id: str) -> bool:
    if not CUSTOMER_ID_RE.fullmatch(customer_id or ""):
        return False
    return db.query_one("SELECT 1 AS ok FROM customers WHERE customer_id = %s", (customer_id,)) is not None


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --------------------------------------------------------------------------- core logic
# Plain functions so they can be unit-tested without an MCP session.
def get_product_details_impl(product_id: str | None = None, search: str | None = None) -> dict[str, Any]:
    if product_id:
        pid = product_id.strip().upper()
        if not PRODUCT_ID_RE.fullmatch(pid):
            return _err("invalid_product_id", "Product IDs look like NS-LAP-001.")
        rows = db.query(
            "SELECT product_id, name, category, description, price_gbp, stock_qty, warranty_months "
            "FROM products WHERE product_id = %s AND is_active = TRUE",
            (pid,),
        )
    elif search:
        term = search.strip()
        if not SEARCH_RE.fullmatch(term):
            return _err("invalid_search", "Search with 2-60 plain characters, e.g. 'wireless keyboard'.")
        words = [w for w in re.split(r"\s+", term) if len(w) > 1][:5]
        # One bound LIKE parameter per word; all must match name or description.
        clauses = " AND ".join(["(name LIKE %s ESCAPE '\\\\' OR description LIKE %s ESCAPE '\\\\')"] * len(words))
        params: list[str] = []
        for w in words:
            p = f"%{_escape_like(w)}%"
            params += [p, p]
        rows = db.query(
            "SELECT product_id, name, category, description, price_gbp, stock_qty, warranty_months "
            f"FROM products WHERE is_active = TRUE AND {clauses} ORDER BY name LIMIT 5",
            tuple(params),
        )
        if not rows:  # relax to any-word match
            any_clause = " OR ".join(["name LIKE %s ESCAPE '\\\\' OR category LIKE %s ESCAPE '\\\\'"] * len(words))
            rows = db.query(
                "SELECT product_id, name, category, description, price_gbp, stock_qty, warranty_months "
                f"FROM products WHERE is_active = TRUE AND ({any_clause}) ORDER BY name LIMIT 5",
                tuple(params),
            )
    else:
        return _err("missing_argument", "Provide product_id or search.")

    if not rows:
        return _err("not_found", "No matching product.")
    products = []
    for r in rows:
        r = _jsonable(r)
        r["in_stock"] = r["stock_qty"] > 0
        r["stock_level"] = "out of stock" if r["stock_qty"] == 0 else ("low" if r["stock_qty"] < 10 else "good")
        del r["stock_qty"]  # exact inventory is internal
        products.append(r)
    return {"ok": True, "products": products}


def check_order_status_impl(order_id: str, customer_id: str) -> dict[str, Any]:
    oid = _norm_order_id(order_id)
    if not oid:
        return _err("invalid_order_id", "Order numbers look like ORD-1001.")
    if not _valid_customer(customer_id):
        return _err("unauthenticated", "No verified customer on this session.")
    order = db.query_one(
        "SELECT order_id, status, warranty_plan, carrier, tracking_number, total_gbp, "
        "placed_at, shipped_at, delivered_at, estimated_delivery "
        "FROM orders WHERE order_id = %s AND customer_id = %s",
        (oid, customer_id),
    )
    if not order:
        # Same answer whether the order doesn't exist or belongs to someone else.
        return _err("not_found", f"No order {oid} on this account.")
    items = db.query(
        "SELECT oi.product_id, p.name, p.category, oi.quantity, oi.unit_price_gbp, p.warranty_months "
        "FROM order_items oi JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = %s",
        (oid,),
    )
    order = _jsonable(order)
    if order.get("delivered_at"):
        delivered = datetime.fromisoformat(order["delivered_at"]).date()
        order["days_since_delivery"] = (date.today() - delivered).days
    return {"ok": True, "order": order, "items": [_jsonable(i) for i in items]}


def place_order_impl(
    customer_id: str, product_id: str, quantity: int = 1, warranty_plan: str = "none"
) -> dict[str, Any]:
    if not _valid_customer(customer_id):
        return _err("unauthenticated", "No verified customer on this session.")
    pid = (product_id or "").strip().upper()
    if not PRODUCT_ID_RE.fullmatch(pid):
        return _err("invalid_product_id", "Product IDs look like NS-LAP-001. Look the product up first.")
    if not isinstance(quantity, int) or not 1 <= quantity <= MAX_QTY_PER_ORDER:
        return _err("invalid_quantity", f"Quantity must be between 1 and {MAX_QTY_PER_ORDER}.")
    if warranty_plan not in WARRANTY_RATES:
        return _err("invalid_warranty_plan", "warranty_plan must be none, novacare or novacare_plus.")

    with db.transaction() as cur:
        cur.execute(
            "SELECT product_id, name, price_gbp, stock_qty FROM products "
            "WHERE product_id = %s AND is_active = TRUE FOR UPDATE",
            (pid,),
        )
        product = cur.fetchone()
        if not product:
            return _err("not_found", f"No active product {pid}.")
        if product["stock_qty"] < quantity:
            return _err("out_of_stock", f"{product['name']} has insufficient stock.")

        unit = Decimal(product["price_gbp"])
        cover = (unit * WARRANTY_RATES[warranty_plan]).quantize(Decimal("0.01"), ROUND_HALF_UP)
        total = (unit + cover) * quantity
        for _ in range(5):
            order_id = f"ORD-{secrets.randbelow(900000) + 100000}"
            cur.execute("SELECT 1 FROM orders WHERE order_id = %s", (order_id,))
            if not cur.fetchone():
                break
        cur.execute(
            "INSERT INTO orders (order_id, customer_id, status, warranty_plan, total_gbp, estimated_delivery) "
            "VALUES (%s, %s, 'processing', %s, %s, CURDATE() + INTERVAL 3 DAY)",
            (order_id, customer_id, warranty_plan, total),
        )
        cur.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price_gbp) VALUES (%s, %s, %s, %s)",
            (order_id, pid, quantity, unit),
        )
        cur.execute("UPDATE products SET stock_qty = stock_qty - %s WHERE product_id = %s", (quantity, pid))

    log.info("order placed order_id=%s product=%s qty=%s", order_id, pid, quantity)
    return {
        "ok": True,
        "order_id": order_id,
        "product": product["name"],
        "quantity": quantity,
        "warranty_plan": warranty_plan,
        "total_gbp": float(total),
        "status": "processing",
    }


def get_customer_profile_impl(customer_id: str) -> dict[str, Any]:
    if not CUSTOMER_ID_RE.fullmatch(customer_id or ""):
        return _err("unauthenticated", "No verified customer on this session.")
    row = db.query_one("SELECT full_name, loyalty_tier FROM customers WHERE customer_id = %s", (customer_id,))
    if not row:
        return _err("unauthenticated", "No verified customer on this session.")
    return {"ok": True, "first_name": row["full_name"].split()[0], "loyalty_tier": row["loyalty_tier"]}


# --------------------------------------------------------------------------- MCP tools
@mcp.tool(
    name="get_product_details",
    description=(
        "Look up NovaShop products: name, description, price (GBP), stock availability and manufacturer "
        "warranty length. Pass product_id (e.g. NS-AUD-001) if known, otherwise a short search phrase "
        "such as 'wireless keyboard'."
    ),
)
def get_product_details(product_id: str | None = None, search: str | None = None) -> dict[str, Any]:
    return get_product_details_impl(product_id, search)


@mcp.tool(
    name="check_order_status",
    description=(
        "Get status, carrier, tracking, delivery dates, warranty plan and items for one of the signed-in "
        "customer's orders. order_id looks like ORD-1001."
    ),
)
def check_order_status(order_id: str, customer_id: str) -> dict[str, Any]:
    return check_order_status_impl(order_id, customer_id)


@mcp.tool(
    name="place_order",
    description=(
        "Place an order for the signed-in customer. Only call after the customer has explicitly confirmed "
        "the product, quantity and price. product_id must come from get_product_details. "
        "warranty_plan is none, novacare or novacare_plus."
    ),
)
def place_order(
    customer_id: str,
    product_id: str,
    quantity: int = 1,
    warranty_plan: Literal["none", "novacare", "novacare_plus"] = "none",
) -> dict[str, Any]:
    return place_order_impl(customer_id, product_id, quantity, warranty_plan)


@mcp.tool(
    name="get_customer_profile",
    description="Session bootstrap only: first name and loyalty tier of the signed-in customer.",
)
def get_customer_profile(customer_id: str) -> dict[str, Any]:
    return get_customer_profile_impl(customer_id)


if __name__ == "__main__":
    mcp.run("stdio")
