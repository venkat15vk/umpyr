from decimal import Decimal

from umpyr_ontology import Facts, Item, Order, Ticket, field_paths, validate_path
from umpyr_ontology.catalog import catalog_markdown
from umpyr_ontology.export_schema import export


def test_catalog_contains_core_paths():
    paths = field_paths()
    for p in [
        "order.total",
        "item.is_final_sale",
        "shipment.days_since_delivery",
        "claim.has_photos",
        "flight.cause",
        "bag.minutes_to_carousel",
        "booking.is_refundable_fare",
        "case.outcome.amount",
    ]:
        assert p in paths, p


def test_validate_path_handles_list_elements_and_rejects_unknown():
    assert validate_path("order.items.price")
    assert validate_path("order.items.0.price")
    assert validate_path("booking.flights.delay_minutes")
    assert not validate_path("order.zendesk_tag")
    assert not validate_path("vendor.sku")
    assert not validate_path("order.items.colour")


def test_facts_dict_strips_none_and_keeps_decimals():
    facts = Facts(order=Order(order_id="R1", total=Decimal("99.50"), items=[Item(sku="A", price=Decimal("10"))]))
    d = facts.to_facts_dict()
    assert d["order"]["total"] == Decimal("99.50")
    assert "placed_at" not in d["order"]
    assert d["order"]["items"][0]["price"] == Decimal("10")
    assert d["claim"] == {}


def test_ticket_from_template_row():
    t = Ticket(
        ticket_id="T-1",
        created_at="2026-01-02T10:00:00Z",
        channel="email",
        subject="Damaged router bit",
        thread=[{"role": "customer", "text": "It arrived broken"}],
        agent_actions=["request_photos", "issue_replacement"],
        resolution="replacement",
        amount="29.99",
        order_id="R-100",
    )
    assert t.amount == Decimal("29.99")
    assert t.channel == "email"


def test_catalog_markdown_and_schema_export(tmp_path):
    md = catalog_markdown()
    assert "- order.total (decimal)" in md
    assert "flight.cause (enum) one of: controllable" in md
    files = export(tmp_path)
    assert (tmp_path / "Facts.schema.json").exists()
    assert (tmp_path / "field_catalog.json").exists()
    assert len(files) == 12
