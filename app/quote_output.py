import io
import json
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from .storage import storage


MATERIAL_KEYWORDS = ("mulch", "rock", "soil", "sand", "gravel", "compost")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}


def _currency(value: Decimal) -> str:
    return f"${value:.2f}"


def _decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _format_quantity(value) -> str:
    qty = _decimal(value)
    if qty == qty.to_integral():
        return str(qty.quantize(Decimal("1")))
    return format(qty.normalize(), "f").rstrip("0").rstrip(".")


def _material_estimates(items: list[dict]) -> list[dict]:
    estimates = []
    for item in items:
        name = str(item.get("name") or "").strip()
        unit = str(item.get("unit") or "").strip() or "unit"
        lowered = name.lower()
        if not lowered:
            continue
        if "material" not in lowered and not any(keyword in lowered for keyword in MATERIAL_KEYWORDS):
            continue
        estimates.append(
            {
                "name": name,
                "quantity": _format_quantity(item.get("quantity")),
                "unit": unit,
            }
        )
    return estimates


def _media_paths(media: list[dict], allowed_kinds: set[str], temp_dir: Path) -> list[Path]:
    paths = []
    for index, item in enumerate(media or []):
        media_kind = str(item.get("media_kind") or "").lower()
        storage_path = str(item.get("storage_path") or "").strip()
        content_type = str(item.get("content_type") or "").lower()
        if media_kind not in allowed_kinds or not storage_path:
            continue
        file_name = str(item.get("file_name") or f"media_{index}")
        path = storage.ensure_local_path(storage_path, temp_dir, file_name=file_name)
        if path is None:
            continue
        if content_type.startswith("image/") or path.suffix.lower() in IMAGE_SUFFIXES:
            paths.append(path)
    return paths


def _photo_paths(media: list[dict], temp_dir: Path) -> list[Path]:
    return _media_paths(media, {"photo"}, temp_dir)


def _exclusion_photo_paths(media: list[dict], temp_dir: Path) -> list[Path]:
    return _media_paths(media, {"exclusion_photo"}, temp_dir)


def _combined_photo_paths(quote: dict, temp_dir: Path) -> list[Path]:
    seen = set()
    combined = []
    for path in _photo_paths((quote.get("job_photos") or []) + (quote.get("media") or []), temp_dir):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        combined.append(path)
    return combined


def _job_value(job, key: str, default=None):
    if isinstance(job, dict):
        return job.get(key, default)
    return getattr(job, key, default)


def _job_detected_tasks(job) -> list[dict]:
    if isinstance(job, dict):
        tasks = job.get("detected_tasks")
        if isinstance(tasks, list):
            return [item for item in tasks if isinstance(item, dict)]
        raw = job.get("detected_tasks_json")
    else:
        raw = getattr(job, "detected_tasks_json", None)
    if not raw:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _quote_date_string(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return datetime.utcnow().strftime("%Y-%m-%d")
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return text[:10]
    return datetime.utcnow().strftime("%Y-%m-%d")


def build_text_quote(
    quote_id: int,
    job,
    frequency: str,
    pricing: dict,
    items: list[dict],
    quote_date=None,
) -> str:
    detected_tasks = _job_detected_tasks(job)
    date_text = _quote_date_string(quote_date)
    lines = [
        f"Quote #{quote_id}",
        f"Date: {date_text}",
        f"Customer: {job.customer_name}",
        f"Phone: {job.phone or 'N/A'}",
        f"Email: {getattr(job, 'email', None) or 'N/A'}",
        f"Address: {job.address}",
    ]

    if job.source:
        lines.append(f"Source: {job.source}")
    if getattr(job, "sales_rep", None):
        lines.append(f"Sales Rep: {job.sales_rep}")
    if getattr(job, "zip_code", None):
        lines.append(f"ZIP Code: {job.zip_code}")
    if getattr(job, "area_sqft", None) is not None:
        lines.append(f"Area (sq ft): {_format_quantity(job.area_sqft)}")
    if getattr(job, "terrain_type", None):
        lines.append(f"Terrain Type: {job.terrain_type}")
    if getattr(job, "primary_job_type", None):
        lines.append(f"Primary Job Type: {str(job.primary_job_type).replace('_', ' ')}")
    if job.notes:
        lines.append(f"Notes: {job.notes}")
    if getattr(job, "exclusions", None):
        lines.append(f"Exclusion Areas: {job.exclusions}")
    if detected_tasks:
        lines.append(
            "Detected Tasks: "
            + ", ".join(str(item.get("label") or item.get("job_type") or "").replace("_", " ") for item in detected_tasks)
        )
    if getattr(job, "estimated_labor_hours", None) is not None:
        lines.append(f"Estimated Labor Hours: {_format_quantity(job.estimated_labor_hours)}")
    if getattr(job, "material_cost", None) is not None:
        lines.append(f"Material Cost: {_currency(_decimal(job.material_cost))}")
    if getattr(job, "equipment_cost", None) is not None:
        lines.append(f"Equipment Cost: {_currency(_decimal(job.equipment_cost))}")
    if getattr(job, "suggested_price", None) is not None:
        lines.append(f"AI Suggested Price: {_currency(_decimal(job.suggested_price))}")
    if getattr(job, "crew_instructions", None):
        lines.append(f"Crew Instructions: {job.crew_instructions}")

    lines.append("")
    lines.append("Services:")

    for index, item in enumerate(items, start=1):
        qty = item["quantity"]
        unit = item["unit"]
        lines.append(
            f"{index}. {item['name']} - {qty} {unit} @ {_currency(item['per_unit_price'])} + "
            f"base {_currency(item['base_price'])} (min {_currency(item['min_charge'])}) = "
            f"{_currency(item['line_total'])}"
        )
        if item.get("description"):
            lines.append(f"   Details: {item['description']}")

    material_estimates = _material_estimates(items)
    if material_estimates:
        lines.extend(["", "Estimated Material:"])
        for item in material_estimates:
            lines.append(f"- {item['name']}: {item['quantity']} {item['unit']}")

    lines.extend(
        [
            "",
            f"Subtotal: {_currency(pricing['subtotal'])}",
            f"Travel/Zone Adjustment ({pricing['zone_modifier_percent']}%): {_currency(pricing['zone_adjustment'])}",
            f"Frequency Discount ({frequency}, {pricing['frequency_discount_percent']}%): -{_currency(pricing['discount_amount'])}",
            f"Tax ({pricing['tax_rate']}%): {_currency(pricing['tax_amount'])}",
            f"Total: {_currency(pricing['total'])}",
        ]
    )

    return "\n".join(lines)


def build_quote_pdf(quote: dict) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("reportlab is required for PDF export") from exc

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.55 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="QuoteTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#1d6a3a"),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=14,
            textColor=colors.HexColor("#14241b"),
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="QuoteBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#14241b"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="QuoteMuted",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#56645d"),
        )
    )

    quote_id = quote.get("id")
    quote_date = quote.get("created_at")
    job = quote.get("job") or {}
    items = quote.get("items") or []
    media = quote.get("media") or []
    detected_tasks = _job_detected_tasks(job)
    material_estimates = _material_estimates(items)
    temp_dir_context = tempfile.TemporaryDirectory(prefix="bb-quote-media-")
    temp_dir = Path(temp_dir_context.name)
    photo_paths = _combined_photo_paths(quote, temp_dir)
    exclusion_photo_paths = _exclusion_photo_paths(media, temp_dir)

    story = [
        Paragraph("BarkBoys Sales Quote", styles["QuoteTitle"]),
        Paragraph(
            "Prepared by BarkBoys with estimated material, pricing, and site photos.",
            styles["QuoteMuted"],
        ),
        Spacer(1, 12),
    ]

    summary_rows = [
        [
            Paragraph("<b>Quote #</b>", styles["QuoteBody"]),
            Paragraph(escape(str(quote_id or "Draft")), styles["QuoteBody"]),
            Paragraph("<b>Date</b>", styles["QuoteBody"]),
            Paragraph(_quote_date_string(quote_date), styles["QuoteBody"]),
        ],
        [
            Paragraph("<b>Customer</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("customer_name") or "N/A")), styles["QuoteBody"]),
            Paragraph("<b>Phone</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("phone") or "N/A")), styles["QuoteBody"]),
        ],
        [
            Paragraph("<b>Email</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("email") or "N/A")), styles["QuoteBody"]),
            Paragraph("<b>Frequency</b>", styles["QuoteBody"]),
            Paragraph(escape(str(quote.get("frequency") or "one_time").replace("_", " ").title()), styles["QuoteBody"]),
        ],
        [
            Paragraph("<b>Address</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("address") or "N/A")), styles["QuoteBody"]),
            Paragraph("<b>Source</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("source") or "Sales Tool")), styles["QuoteBody"]),
        ],
        [
            Paragraph("<b>ZIP</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("zip_code") or "N/A")), styles["QuoteBody"]),
            Paragraph("<b>Terrain</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("terrain_type") or "Mixed").title()), styles["QuoteBody"]),
        ],
        [
            Paragraph("<b>Area</b>", styles["QuoteBody"]),
            Paragraph(
                escape(f"{_format_quantity(job.get('area_sqft'))} sq ft") if job.get("area_sqft") is not None else "N/A",
                styles["QuoteBody"],
            ),
            Paragraph("<b>Job Type</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("primary_job_type") or "general_cleanup").replace("_", " ").title()), styles["QuoteBody"]),
        ],
        [
            Paragraph("<b>Sales Rep</b>", styles["QuoteBody"]),
            Paragraph(escape(str(job.get("sales_rep") or "BarkBoys Sales")), styles["QuoteBody"]),
            Paragraph("<b>Prepared By</b>", styles["QuoteBody"]),
            Paragraph("BarkBoys Sales Team", styles["QuoteBody"]),
        ],
    ]
    summary_table = Table(summary_rows, colWidths=[0.9 * inch, 2.35 * inch, 0.9 * inch, 2.35 * inch], hAlign="LEFT")
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#d8e1dc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1dc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(summary_table)

    if job.get("notes"):
        story.extend(
            [
                Spacer(1, 10),
                Paragraph("Site Notes", styles["SectionHeading"]),
                Paragraph(escape(str(job.get("notes"))).replace("\n", "<br/>"), styles["QuoteBody"]),
            ]
        )

    if job.get("exclusions"):
        story.extend(
            [
                Spacer(1, 10),
                Paragraph("Exclusion Areas", styles["SectionHeading"]),
                Paragraph(escape(str(job.get("exclusions"))).replace("\n", "<br/>"), styles["QuoteBody"]),
            ]
        )

    if detected_tasks:
        task_lines = []
        for task in detected_tasks:
            label = str(task.get("label") or task.get("job_type") or "Task").replace("_", " ").title()
            confidence = task.get("confidence")
            if confidence is not None:
                label = f"{label} ({round(float(confidence) * 100)}%)"
            task_lines.append(label)
        story.extend(
            [
                Spacer(1, 10),
                Paragraph("Detected Tasks", styles["SectionHeading"]),
                Paragraph("<br/>".join(escape(line) for line in task_lines), styles["QuoteBody"]),
            ]
        )

    if job.get("crew_instructions") or job.get("estimated_labor_hours") is not None:
        crew_rows = []
        if job.get("estimated_labor_hours") is not None:
            crew_rows.append(
                f"Estimated labor hours: {_format_quantity(job.get('estimated_labor_hours'))}"
            )
        if job.get("material_cost") is not None:
            crew_rows.append(f"Material cost: {_currency(_decimal(job.get('material_cost')))}")
        if job.get("equipment_cost") is not None:
            crew_rows.append(f"Equipment cost: {_currency(_decimal(job.get('equipment_cost')))}")
        if job.get("suggested_price") is not None:
            crew_rows.append(f"AI suggested price: {_currency(_decimal(job.get('suggested_price')))}")
        story.extend(
            [
                Spacer(1, 10),
                Paragraph("Crew Plan", styles["SectionHeading"]),
                Paragraph("<br/>".join(escape(row) for row in crew_rows), styles["QuoteBody"]) if crew_rows else Spacer(1, 0),
                Paragraph(escape(str(job.get("crew_instructions") or "")).replace("\n", "<br/>"), styles["QuoteBody"]) if job.get("crew_instructions") else Spacer(1, 0),
            ]
        )

    story.extend([Spacer(1, 12), Paragraph("Estimated Services", styles["SectionHeading"])])

    item_rows = [[
        Paragraph("<b>Service</b>", styles["QuoteBody"]),
        Paragraph("<b>Qty</b>", styles["QuoteBody"]),
        Paragraph("<b>Unit</b>", styles["QuoteBody"]),
        Paragraph("<b>Pricing</b>", styles["QuoteBody"]),
        Paragraph("<b>Total</b>", styles["QuoteBody"]),
    ]]
    for item in items:
        unit = str(item.get("unit") or "unit")
        pricing_text = (
            f"Base {_currency(_decimal(item.get('base_price')))} + "
            f"{unit} {_currency(_decimal(item.get('per_unit_price')))} "
            f"(min {_currency(_decimal(item.get('min_charge')))})"
        )
        service_label = escape(str(item.get("name") or ""))
        if item.get("description"):
            service_label = (
                f"{service_label}<br/><font size='8' color='#56645d'>"
                f"{escape(str(item.get('description') or ''))}"
                f"</font>"
            )
        item_rows.append(
            [
                Paragraph(service_label, styles["QuoteBody"]),
                Paragraph(escape(_format_quantity(item.get("quantity"))), styles["QuoteBody"]),
                Paragraph(escape(unit), styles["QuoteBody"]),
                Paragraph(escape(pricing_text), styles["QuoteBody"]),
                Paragraph(_currency(_decimal(item.get("line_total"))), styles["QuoteBody"]),
            ]
        )

    items_table = Table(
        item_rows,
        colWidths=[2.15 * inch, 0.65 * inch, 0.7 * inch, 2.0 * inch, 0.85 * inch],
        repeatRows=1,
        hAlign="LEFT",
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f2ec")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#14241b")),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#d8e1dc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1dc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(items_table)

    if material_estimates:
        story.extend([Spacer(1, 12), Paragraph("Estimated Material", styles["SectionHeading"])])
        material_rows = [[
            Paragraph("<b>Material</b>", styles["QuoteBody"]),
            Paragraph("<b>Estimated Amount</b>", styles["QuoteBody"]),
        ]]
        for item in material_estimates:
            material_rows.append(
                [
                    Paragraph(escape(item["name"]), styles["QuoteBody"]),
                    Paragraph(escape(f"{item['quantity']} {item['unit']}"), styles["QuoteBody"]),
                ]
            )
        material_table = Table(material_rows, colWidths=[4.4 * inch, 1.95 * inch], hAlign="LEFT")
        material_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f8f6")),
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#d8e1dc")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1dc")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(material_table)

    story.extend([Spacer(1, 12), Paragraph("Pricing Summary", styles["SectionHeading"])])
    summary_values = [
        ("Subtotal", _currency(_decimal(quote.get("subtotal")))),
        (
            f"Travel/Zone Adjustment ({_format_quantity(quote.get('zone_modifier_percent'))}%)",
            _currency(_decimal(quote.get("zone_adjustment"))),
        ),
        (
            f"Frequency Discount ({str(quote.get('frequency') or 'one_time').replace('_', ' ').title()}, {_format_quantity(quote.get('frequency_discount_percent'))}%)",
            f"-{_currency(_decimal(quote.get('discount_amount')))}",
        ),
        (
            f"Tax ({_format_quantity(quote.get('tax_rate'))}%)",
            _currency(_decimal(quote.get("tax_amount"))),
        ),
        ("Total", _currency(_decimal(quote.get("total")))),
    ]
    pricing_rows = [
        [
            Paragraph(escape(label), styles["QuoteBody"]),
            Paragraph(f"<b>{escape(value)}</b>" if label == "Total" else escape(value), styles["QuoteBody"]),
        ]
        for label, value in summary_values
    ]
    pricing_table = Table(pricing_rows, colWidths=[4.85 * inch, 1.5 * inch], hAlign="LEFT")
    pricing_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#d8e1dc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1dc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("BACKGROUND", (0, len(pricing_rows) - 1), (-1, len(pricing_rows) - 1), colors.HexColor("#e9f2ec")),
            ]
        )
    )
    story.append(pricing_table)

    def _append_photo_grid(paths: list[Path], title: str, start_new_page: bool):
        if not paths:
            return False
        if start_new_page:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 12))
        story.append(Paragraph(title, styles["SectionHeading"]))
        photo_cells = []
        for photo_path in paths:
            try:
                reader = ImageReader(str(photo_path))
                width, height = reader.getSize()
            except Exception:
                continue

            scale = min((3.0 * inch) / width, (2.2 * inch) / height, 1)
            image = Image(str(photo_path), width=width * scale, height=height * scale)
            image.hAlign = "CENTER"
            caption = Paragraph(escape(photo_path.name), styles["QuoteMuted"])
            photo_cells.append([image, Spacer(1, 4), caption])

        photo_rows = []
        for index in range(0, len(photo_cells), 2):
            row = [photo_cells[index]]
            row.append(photo_cells[index + 1] if index + 1 < len(photo_cells) else "")
            photo_rows.append(row)

        if photo_rows:
            photo_table = Table(photo_rows, colWidths=[3.2 * inch, 3.2 * inch], hAlign="LEFT")
            photo_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#d8e1dc")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1dc")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ]
                )
            )
            story.append(photo_table)
            return True
        return False

    photo_page_started = _append_photo_grid(exclusion_photo_paths, "Exclusion Reference Photos", start_new_page=True)
    _append_photo_grid(photo_paths, "Site Photos", start_new_page=not photo_page_started)

    def draw_branding(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#d8e1dc"))
        canvas.setFillColor(colors.HexColor("#1d6a3a"))
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(doc.leftMargin, letter[1] - 0.4 * inch, "BarkBoys")
        canvas.setFillColor(colors.HexColor("#56645d"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(letter[0] - doc.rightMargin, letter[1] - 0.4 * inch, f"Sales Quote #{quote_id or 'Draft'}")
        canvas.line(doc.leftMargin, letter[1] - 0.48 * inch, letter[0] - doc.rightMargin, letter[1] - 0.48 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(doc.leftMargin, 0.35 * inch, "BarkBoys Sales Quote Tool")
        canvas.drawRightString(
            letter[0] - doc.rightMargin,
            0.35 * inch,
            f"Prepared {datetime.utcnow().strftime('%Y-%m-%d')}  |  Page {canvas.getPageNumber()}",
        )
        canvas.line(doc.leftMargin, 0.47 * inch, letter[0] - doc.rightMargin, 0.47 * inch)
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=draw_branding, onLaterPages=draw_branding)
        return buffer.getvalue()
    finally:
        temp_dir_context.cleanup()
