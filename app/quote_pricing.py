from decimal import Decimal, ROUND_HALF_UP


MONEY_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")
FREQUENCY_DISCOUNT_PERCENTS = {
    "one_time": Decimal("0"),
    "weekly": Decimal("15"),
    "biweekly": Decimal("10"),
    "monthly": Decimal("0"),
}


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def calculate_quote(items, frequency: str, tax_rate, zone_modifier_percent):
    tax_rate_dec = _to_decimal(tax_rate)
    zone_modifier_dec = _to_decimal(zone_modifier_percent)
    frequency_discount_percent = FREQUENCY_DISCOUNT_PERCENTS.get(frequency, Decimal("0"))

    calculated_items = []
    subtotal = Decimal("0")

    for item in items:
        quantity = _to_decimal(item.quantity)
        base_price = _to_decimal(item.base_price)
        per_unit_price = _to_decimal(item.per_unit_price)
        min_charge = _to_decimal(item.min_charge)
        line_raw = base_price + (per_unit_price * quantity)
        line_total = _money(max(min_charge, line_raw))

        calculated_items.append(
            {
                "name": item.name,
                "description": getattr(item, "description", None),
                "quantity": _money(quantity),
                "unit": item.unit,
                "base_price": _money(base_price),
                "per_unit_price": _money(per_unit_price),
                "min_charge": _money(min_charge),
                "line_total": line_total,
            }
        )
        subtotal += line_total

    subtotal = _money(subtotal)
    zone_adjustment = _money((subtotal * zone_modifier_dec) / HUNDRED)
    subtotal_after_zone = subtotal + zone_adjustment
    discount_amount = _money((subtotal_after_zone * frequency_discount_percent) / HUNDRED)
    taxable_subtotal = subtotal_after_zone - discount_amount
    tax_amount = _money((taxable_subtotal * tax_rate_dec) / HUNDRED)
    total = _money(taxable_subtotal + tax_amount)

    return {
        "items": calculated_items,
        "subtotal": subtotal,
        "zone_adjustment": zone_adjustment,
        "frequency_discount_percent": _money(frequency_discount_percent),
        "discount_amount": discount_amount,
        "tax_rate": _money(tax_rate_dec),
        "tax_amount": tax_amount,
        "total": total,
    }
