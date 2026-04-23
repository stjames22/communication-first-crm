from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

from .assumptions import job_type_assumption, zip_factor_for_code


def _decimal(value, default: Decimal) -> Decimal:
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _hours(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _normalize_zip(zip_code: Optional[str]) -> str:
    digits = "".join(ch for ch in str(zip_code or "") if ch.isdigit())
    return digits[:5]


def _zip_factor(zip_code: Optional[str]) -> Decimal:
    return zip_factor_for_code(zip_code, default="1.05")


def _job_config(job_type: Optional[str]) -> dict:
    return job_type_assumption(job_type)


def calculate_suggested_price(area_sqft, job_type: str, zip_code: Optional[str]) -> dict:
    area = max(_decimal(area_sqft, Decimal("2500")), Decimal("8"))
    job_key = str(job_type or "general_cleanup").strip().lower() or "general_cleanup"
    config = _job_config(job_key)
    regional_factor = _zip_factor(zip_code)
    area_units = area / Decimal("1000")

    labor_hours = _hours((config["base_hours"] + (area_units * config["hours_per_1000_sqft"])) * regional_factor)
    material_cost = _money((config["material_per_1000_sqft"] * area_units + Decimal("8")) * regional_factor)
    suggested_price = _money((labor_hours * config["labor_rate"] * regional_factor) + material_cost + (config["base_fee"] * regional_factor))

    return {
        "job_type": job_key,
        "label": config["label"],
        "area_sqft": area,
        "labor_hours": labor_hours,
        "material_cost": material_cost,
        "suggested_price": suggested_price,
        "zip_code": _normalize_zip(zip_code),
    }


def calculate_detected_pricing(area_sqft, job_types: Iterable[str], zip_code: Optional[str]) -> dict:
    area = max(_decimal(area_sqft, Decimal("2500")), Decimal("8"))
    normalized_job_types = []
    for job_type in job_types or []:
        key = str(job_type or "").strip().lower()
        if not key:
            continue
        if key not in normalized_job_types:
            normalized_job_types.append(key)
    if not normalized_job_types:
        normalized_job_types = ["general_cleanup"]

    task_breakdown = []
    total_labor_hours = Decimal("0")
    total_material_cost = Decimal("0")
    total_suggested_price = Decimal("0")

    multi_task = len(normalized_job_types) > 1
    for job_type in normalized_job_types:
        config = _job_config(job_type)
        effective_area = area * (config["area_share"] if multi_task else Decimal("1"))
        result = calculate_suggested_price(effective_area, job_type, zip_code)
        task_breakdown.append(result)
        total_labor_hours += result["labor_hours"]
        total_material_cost += result["material_cost"]
        total_suggested_price += result["suggested_price"]

    return {
        "area_sqft": area,
        "primary_job_type": task_breakdown[0]["job_type"],
        "labor_hours": _hours(total_labor_hours),
        "material_cost": _money(total_material_cost),
        "suggested_price": _money(total_suggested_price),
        "task_breakdown": task_breakdown,
        "zip_code": _normalize_zip(zip_code),
    }
