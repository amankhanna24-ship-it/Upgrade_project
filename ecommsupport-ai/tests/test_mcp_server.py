import pytest

from mcp_server import server as s
from tests.conftest import needs_db

pytestmark = needs_db


@pytest.mark.parametrize(
    "payload",
    [
        "ORD-1001' OR '1'='1",
        "ORD-1001; DROP TABLE orders;--",
        "1 UNION SELECT email FROM customers",
        "ORD-%",
        "",
    ],
)
def test_order_id_injection_is_rejected(payload):
    r = s.check_order_status_impl(payload, "CUST-001")
    assert r["ok"] is False and r["error"] == "invalid_order_id"


def test_order_lookup_is_scoped_to_customer():
    assert s.check_order_status_impl("ORD-1003", "CUST-002")["ok"] is True
    # Same order from another account looks like it doesn't exist (no enumeration).
    other = s.check_order_status_impl("ORD-1003", "CUST-001")
    assert other == {"ok": False, "error": "not_found", "message": "No order ORD-1003 on this account."}


def test_order_id_normalisation():
    assert s.check_order_status_impl("ord 1001", "CUST-001")["order"]["order_id"] == "ORD-1001"
    assert s.check_order_status_impl("1001", "CUST-001")["ok"] is True


def test_order_output_has_no_pii():
    r = s.check_order_status_impl("ORD-1002", "CUST-001")
    text = str(r)
    assert "@" not in text and "+44" not in text and "Elm Street" not in text
    assert r["order"]["days_since_delivery"] == 45
    assert r["order"]["warranty_plan"] == "novacare"


@pytest.mark.parametrize("term", ["laptop%' --", "x", "a" * 80, "keyboard; DROP TABLE products"])
def test_product_search_rejects_bad_terms(term):
    assert s.get_product_details_impl(search=term)["ok"] is False


def test_product_search_and_like_escaping():
    r = s.get_product_details_impl(search="wireless keyboard")
    assert r["products"][0]["product_id"] == "NS-PER-001"
    assert "stock_qty" not in r["products"][0]
    assert s.get_product_details_impl(product_id="NS-LAP-001")["products"][0]["name"] == "NovaBook Pro 14"


def test_place_order_validation():
    assert s.place_order_impl("CUST-999", "NS-PER-001")["error"] == "unauthenticated"
    assert s.place_order_impl("CUST-001", "DROP TABLE")["error"] == "invalid_product_id"
    assert s.place_order_impl("CUST-001", "NS-PER-001", 50)["error"] == "invalid_quantity"
    assert s.place_order_impl("CUST-001", "NS-PER-001", 1, "gold")["error"] == "invalid_warranty_plan"
    assert s.place_order_impl("CUST-001", "NS-ACC-001")["error"] == "out_of_stock"


def test_place_order_is_transactional():
    before = s.db.query_one("SELECT stock_qty FROM products WHERE product_id = %s", ("NS-MON-001",))["stock_qty"]
    r = s.place_order_impl("CUST-004", "NS-MON-001", 1, "novacare_plus")
    assert r["ok"] and r["total_gbp"] == pytest.approx(349 * 1.18, abs=0.01)
    after = s.db.query_one("SELECT stock_qty FROM products WHERE product_id = %s", ("NS-MON-001",))["stock_qty"]
    assert after == before - 1
    placed = s.check_order_status_impl(r["order_id"], "CUST-004")
    assert placed["order"]["status"] == "processing" and placed["items"][0]["product_id"] == "NS-MON-001"
