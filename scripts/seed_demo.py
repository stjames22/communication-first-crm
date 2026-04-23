from app.db import SessionLocal
from app.models import Job, Quote, QuoteItem
from app.quote_pricing import calculate_quote
from app.schemas import QuoteItemInput


def run() -> None:
    db = SessionLocal()
    try:
        existing_quotes = db.query(Quote).count()
        if existing_quotes > 0:
            print("Demo seed skipped: quotes already exist.")
            return

        items = [
            QuoteItemInput(
                name="Mowing",
                quantity=5000,
                unit="sq ft",
                base_price=25,
                per_unit_price=0.01,
                min_charge=55,
            ),
            QuoteItemInput(
                name="Edging",
                quantity=120,
                unit="linear ft",
                base_price=15,
                per_unit_price=0.12,
                min_charge=35,
            ),
        ]

        pricing = calculate_quote(
            items=items,
            frequency="weekly",
            tax_rate=8.25,
            zone_modifier_percent=5,
        )

        job = Job(
            customer_name="Demo Customer",
            phone="555-0100",
            address="123 Test Ln",
            notes="Seeded demo quote",
            source="Demo",
        )
        db.add(job)
        db.flush()

        quote = Quote(
            job_id=job.id,
            frequency="weekly",
            tax_rate=pricing["tax_rate"],
            zone_modifier_percent=5,
            frequency_discount_percent=pricing["frequency_discount_percent"],
            subtotal=pricing["subtotal"],
            zone_adjustment=pricing["zone_adjustment"],
            discount_amount=pricing["discount_amount"],
            tax_amount=pricing["tax_amount"],
            total=pricing["total"],
        )
        db.add(quote)
        db.flush()

        for item in pricing["items"]:
            db.add(
                QuoteItem(
                    quote_id=quote.id,
                    name=item["name"],
                    quantity=item["quantity"],
                    unit=item["unit"],
                    base_price=item["base_price"],
                    per_unit_price=item["per_unit_price"],
                    min_charge=item["min_charge"],
                    line_total=item["line_total"],
                )
            )

        db.commit()
        print(f"Demo seed complete: quote #{quote.id} created.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
