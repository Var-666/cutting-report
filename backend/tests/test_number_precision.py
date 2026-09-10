from decimal import Decimal

from app.services.report_service import format_number


def test_report_numbers_are_rendered_with_one_decimal_place() -> None:
    assert format_number(Decimal("1234.56")) == "1,234.6"
    assert format_number(Decimal("8")) == "8.0"
    assert format_number(None) == "--"
