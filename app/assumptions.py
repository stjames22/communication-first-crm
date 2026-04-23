from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path


ASSUMPTIONS_PATH = Path(__file__).resolve().parent / "data" / "pricing_assumptions.json"
LOAD_SPLIT_STRATEGIES = {"max_loads_first", "balanced_loads"}


def _decimal(value, default: str) -> Decimal:
    try:
        if value in (None, ""):
            return Decimal(default)
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _normalize_zip(zip_code: str | None) -> str:
    digits = "".join(ch for ch in str(zip_code or "") if ch.isdigit())
    return digits[:5]


@lru_cache
def load_pricing_assumptions() -> dict:
    data = json.loads(ASSUMPTIONS_PATH.read_text(encoding="utf-8"))
    normalized = {
        "distance_pricing_tiers": [],
        "zip_factors": [],
        "material_assumptions": [],
        "material_pricing_tables": {},
        "job_type_assumptions": [],
        "labor_defaults": {},
        "measurement_defaults": {},
    }

    for row in data.get("distance_pricing_tiers", []):
        normalized["distance_pricing_tiers"].append(
            {
                "label": str(row.get("label") or "").strip(),
                "min_miles": _decimal(row.get("min_miles"), "0"),
                "max_miles": None if row.get("max_miles") in (None, "") else _decimal(row.get("max_miles"), "0"),
                "blower_delivery_fee": _decimal(row.get("blower_delivery_fee"), "85"),
            }
        )

    for row in data.get("zip_factors", []):
        normalized["zip_factors"].append(
            {
                "zip_prefix": str(row.get("zip_prefix") or "").strip(),
                "multiplier": _decimal(row.get("multiplier"), "1.05"),
            }
        )

    for row in data.get("material_assumptions", []):
        normalized["material_assumptions"].append(
            {
                "material_type": str(row.get("material_type") or "").strip().lower(),
                "label": str(row.get("label") or "").strip(),
                "blower_compatible": bool(row.get("blower_compatible", False)),
                "cost_per_yard": _decimal(row.get("cost_per_yard"), "0"),
                "delivery_fee_per_yard": _decimal(row.get("delivery_fee_per_yard"), "4"),
                "default_selected": bool(row.get("default_selected", False)),
            }
        )

    material_pricing_tables = data.get("material_pricing_tables", {})
    if isinstance(material_pricing_tables, dict):
        for material_type, table in material_pricing_tables.items():
            key = str(material_type or "").strip().lower()
            if not key:
                continue
            prices: dict[str, Decimal] = {}
            if isinstance(table, dict):
                for yard_key, price in table.items():
                    yard_label = str(yard_key or "").strip()
                    if not yard_label:
                        continue
                    prices[yard_label] = _decimal(price, "0")
            normalized["material_pricing_tables"][key] = prices

    for row in data.get("job_type_assumptions", []):
        normalized["job_type_assumptions"].append(
            {
                "job_type": str(row.get("job_type") or "").strip().lower(),
                "label": str(row.get("label") or "").strip(),
                "base_hours": _decimal(row.get("base_hours"), "1.0"),
                "hours_per_1000_sqft": _decimal(row.get("hours_per_1000_sqft"), "0.40"),
                "material_per_1000_sqft": _decimal(row.get("material_per_1000_sqft"), "4"),
                "labor_rate": _decimal(row.get("labor_rate"), "76"),
                "base_fee": _decimal(row.get("base_fee"), "28"),
                "area_share": _decimal(row.get("area_share"), "0.60"),
            }
        )

    labor_defaults = data.get("labor_defaults", {})
    normalized["labor_defaults"] = {
        "blower_placement_rate_per_yard": _decimal(labor_defaults.get("blower_placement_rate_per_yard"), "24"),
        "blowing_service_fee": _decimal(labor_defaults.get("blowing_service_fee"), "12"),
        "obstacle_fee_per_obstacle": _decimal(labor_defaults.get("obstacle_fee_per_obstacle"), "12"),
        "gate_access_fee": _decimal(labor_defaults.get("gate_access_fee"), "18"),
        "haul_away_base_fee": _decimal(labor_defaults.get("haul_away_base_fee"), "45"),
    }
    measurement_defaults = data.get("measurement_defaults", {})
    normalized["measurement_defaults"] = {
        "material_depth_inches": _decimal(measurement_defaults.get("material_depth_inches"), "2"),
        "minimum_material_yards": _decimal(measurement_defaults.get("minimum_material_yards"), "0.25"),
        "quote_price_rounding_dollars": _decimal(measurement_defaults.get("quote_price_rounding_dollars"), "5"),
        "bulk_discount_threshold_1_yards": _decimal(measurement_defaults.get("bulk_discount_threshold_1_yards"), "10"),
        "bulk_discount_percent_1": _decimal(measurement_defaults.get("bulk_discount_percent_1"), "5"),
        "bulk_discount_threshold_2_yards": _decimal(measurement_defaults.get("bulk_discount_threshold_2_yards"), "20"),
        "bulk_discount_percent_2": _decimal(measurement_defaults.get("bulk_discount_percent_2"), "8"),
        "bulk_discount_threshold_3_yards": _decimal(measurement_defaults.get("bulk_discount_threshold_3_yards"), "30"),
        "bulk_discount_percent_3": _decimal(measurement_defaults.get("bulk_discount_percent_3"), "12"),
        "load_split_strategy": str(measurement_defaults.get("load_split_strategy") or "max_loads_first").strip().lower(),
    }
    if normalized["measurement_defaults"]["load_split_strategy"] not in LOAD_SPLIT_STRATEGIES:
        normalized["measurement_defaults"]["load_split_strategy"] = "max_loads_first"
    return normalized


def _raw_assumptions_json() -> dict:
    return json.loads(ASSUMPTIONS_PATH.read_text(encoding="utf-8"))


def _write_raw_assumptions_json(data: dict) -> None:
    ASSUMPTIONS_PATH.write_text(f"{json.dumps(data, indent=2)}\n", encoding="utf-8")
    load_pricing_assumptions.cache_clear()


def blower_material_options() -> list[dict]:
    assumptions = load_pricing_assumptions()
    return [row for row in assumptions["material_assumptions"] if row["blower_compatible"]]


def default_blower_material() -> str:
    for row in blower_material_options():
        if row["default_selected"]:
            return row["material_type"]
    return "mulch"


def normalize_blower_material(material_type: str | None) -> str:
    candidate = str(material_type or "").strip().lower()
    options = {row["material_type"] for row in blower_material_options()}
    return candidate if candidate in options else default_blower_material()


def material_assumption(material_type: str | None) -> dict:
    normalized = normalize_blower_material(material_type)
    for row in blower_material_options():
        if row["material_type"] == normalized:
            return row
    return blower_material_options()[0]


def zip_factor_for_code(zip_code: str | None, default: str = "1.05") -> Decimal:
    zip_prefix = _normalize_zip(zip_code)[:3]
    for row in load_pricing_assumptions()["zip_factors"]:
        if row["zip_prefix"] == zip_prefix:
            return row["multiplier"]
    return Decimal(default)


def job_type_assumption(job_type: str | None) -> dict:
    normalized = str(job_type or "").strip().lower() or "general_cleanup"
    for row in load_pricing_assumptions()["job_type_assumptions"]:
        if row["job_type"] == normalized:
            return row
    for row in load_pricing_assumptions()["job_type_assumptions"]:
        if row["job_type"] == "general_cleanup":
            return row
    return load_pricing_assumptions()["job_type_assumptions"][0]


def blower_delivery_fee(distance_miles) -> Decimal:
    miles = _decimal(distance_miles, "0")
    for tier in load_pricing_assumptions()["distance_pricing_tiers"]:
        max_miles = tier["max_miles"]
        if miles >= tier["min_miles"] and (max_miles is None or miles <= max_miles):
            return tier["blower_delivery_fee"]
    return load_pricing_assumptions()["distance_pricing_tiers"][-1]["blower_delivery_fee"]


def labor_defaults() -> dict:
    return load_pricing_assumptions()["labor_defaults"]


def measurement_defaults() -> dict:
    return load_pricing_assumptions()["measurement_defaults"]


def serialize_pricing_assumptions() -> dict:
    assumptions = load_pricing_assumptions()
    return {
        "distance_pricing_tiers": [
            {
                "label": row["label"],
                "min_miles": float(row["min_miles"]),
                "max_miles": None if row["max_miles"] is None else float(row["max_miles"]),
                "blower_delivery_fee": float(row["blower_delivery_fee"]),
            }
            for row in assumptions["distance_pricing_tiers"]
        ],
        "zip_factors": [
            {
                "zip_prefix": row["zip_prefix"],
                "multiplier": float(row["multiplier"]),
            }
            for row in assumptions["zip_factors"]
        ],
        "material_assumptions": [
            {
                "material_type": row["material_type"],
                "label": row["label"],
                "blower_compatible": row["blower_compatible"],
                "cost_per_yard": float(row["cost_per_yard"]),
                "delivery_fee_per_yard": float(row["delivery_fee_per_yard"]),
                "default_selected": row["default_selected"],
            }
            for row in assumptions["material_assumptions"]
        ],
        "material_pricing_tables": {
            material_type: {
                yard_key: float(price)
                for yard_key, price in table.items()
            }
            for material_type, table in assumptions["material_pricing_tables"].items()
        },
        "job_type_assumptions": [
            {
                "job_type": row["job_type"],
                "label": row["label"],
                "base_hours": float(row["base_hours"]),
                "hours_per_1000_sqft": float(row["hours_per_1000_sqft"]),
                "material_per_1000_sqft": float(row["material_per_1000_sqft"]),
                "labor_rate": float(row["labor_rate"]),
                "base_fee": float(row["base_fee"]),
                "area_share": float(row["area_share"]),
            }
            for row in assumptions["job_type_assumptions"]
        ],
        "labor_defaults": {
            key: float(value)
            for key, value in assumptions["labor_defaults"].items()
        },
        "measurement_defaults": {
            key: (float(value) if isinstance(value, Decimal) else value)
            for key, value in assumptions["measurement_defaults"].items()
        },
    }


def update_material_assumptions(material_rows: list[dict]) -> list[dict]:
    if not isinstance(material_rows, list):
        raise ValueError("materials payload must be a list")

    raw = _raw_assumptions_json()
    existing_rows = list(raw.get("material_assumptions") or [])
    row_by_key: dict[str, dict] = {}
    ordered_keys: list[str] = []

    for row in existing_rows:
        key = str(row.get("material_type") or "").strip().lower()
        if not key:
            continue
        if key not in row_by_key:
            ordered_keys.append(key)
        row_by_key[key] = dict(row)

    for row in material_rows:
        key = str(row.get("material_type") or "").strip().lower()
        if not key:
            continue

        current = row_by_key.get(key, {})
        label = str(row.get("label") or current.get("label") or key.replace("_", " ").title()).strip()
        blower_compatible = bool(row.get("blower_compatible", current.get("blower_compatible", True)))
        cost_per_yard = float(_decimal(row.get("cost_per_yard"), str(current.get("cost_per_yard", "0"))))
        delivery_fee_per_yard = float(
            _decimal(row.get("delivery_fee_per_yard"), str(current.get("delivery_fee_per_yard", "0")))
        )
        default_selected = bool(row.get("default_selected", current.get("default_selected", False)))

        normalized = {
            "material_type": key,
            "label": label or key.replace("_", " ").title(),
            "blower_compatible": blower_compatible,
            "cost_per_yard": cost_per_yard,
            "delivery_fee_per_yard": delivery_fee_per_yard,
            "default_selected": default_selected,
        }
        row_by_key[key] = normalized
        if key not in ordered_keys:
            ordered_keys.append(key)

    rebuilt = [row_by_key[key] for key in ordered_keys if key in row_by_key]
    defaults = [row for row in rebuilt if row.get("default_selected")]
    if not defaults and rebuilt:
        rebuilt[0]["default_selected"] = True
    elif len(defaults) > 1:
        keep_key = defaults[0]["material_type"]
        for row in rebuilt:
            row["default_selected"] = row["material_type"] == keep_key

    raw["material_assumptions"] = rebuilt
    _write_raw_assumptions_json(raw)
    return serialize_pricing_assumptions()["material_assumptions"]


def update_material_pricing_tables(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("material_pricing_tables payload must be an object")

    raw = _raw_assumptions_json()
    current = raw.get("material_pricing_tables")
    row_by_key: dict[str, dict] = {}

    if isinstance(current, dict):
        for material_type, table in current.items():
            key = str(material_type or "").strip().lower()
            if not key or not isinstance(table, dict):
                continue
            row_by_key[key] = {str(yard_key): float(_decimal(price, "0")) for yard_key, price in table.items()}

    for material_type, table in payload.items():
        key = str(material_type or "").strip().lower()
        if not key:
            continue
        if not isinstance(table, dict):
            raise ValueError(f"{key} table must be an object")
        normalized_table: dict[str, float] = {}
        for yard_key, price in table.items():
            yard_label = str(yard_key or "").strip()
            if not yard_label:
                continue
            normalized_table[yard_label] = max(float(_decimal(price, "0")), 0.0)
        row_by_key[key] = normalized_table

    raw["material_pricing_tables"] = row_by_key
    _write_raw_assumptions_json(raw)
    return serialize_pricing_assumptions()["material_pricing_tables"]


def update_job_type_assumptions(job_rows: list[dict]) -> list[dict]:
    if not isinstance(job_rows, list):
        raise ValueError("job_types payload must be a list")

    raw = _raw_assumptions_json()
    existing_rows = list(raw.get("job_type_assumptions") or [])
    row_by_key: dict[str, dict] = {}
    ordered_keys: list[str] = []

    for row in existing_rows:
        key = str(row.get("job_type") or "").strip().lower()
        if not key:
            continue
        if key not in row_by_key:
            ordered_keys.append(key)
        row_by_key[key] = dict(row)

    for row in job_rows:
        key = str(row.get("job_type") or "").strip().lower()
        if not key:
            continue

        current = row_by_key.get(key, {})
        label = str(row.get("label") or current.get("label") or key.replace("_", " ").title()).strip()
        base_hours = max(float(_decimal(row.get("base_hours"), str(current.get("base_hours", "1.0")))), 0.0)
        hours_per_1000_sqft = max(
            float(_decimal(row.get("hours_per_1000_sqft"), str(current.get("hours_per_1000_sqft", "0.40")))),
            0.0,
        )
        material_per_1000_sqft = max(
            float(_decimal(row.get("material_per_1000_sqft"), str(current.get("material_per_1000_sqft", "4")))),
            0.0,
        )
        labor_rate = max(float(_decimal(row.get("labor_rate"), str(current.get("labor_rate", "76")))), 0.0)
        base_fee = max(float(_decimal(row.get("base_fee"), str(current.get("base_fee", "28")))), 0.0)
        area_share = float(_decimal(row.get("area_share"), str(current.get("area_share", "0.60"))))
        area_share = min(max(area_share, 0.0), 1.0)

        normalized = {
            "job_type": key,
            "label": label or key.replace("_", " ").title(),
            "base_hours": base_hours,
            "hours_per_1000_sqft": hours_per_1000_sqft,
            "material_per_1000_sqft": material_per_1000_sqft,
            "labor_rate": labor_rate,
            "base_fee": base_fee,
            "area_share": area_share,
        }
        row_by_key[key] = normalized
        if key not in ordered_keys:
            ordered_keys.append(key)

    rebuilt = [row_by_key[key] for key in ordered_keys if key in row_by_key]
    raw["job_type_assumptions"] = rebuilt
    _write_raw_assumptions_json(raw)
    return serialize_pricing_assumptions()["job_type_assumptions"]


def update_measurement_defaults(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("measurement_defaults payload must be an object")

    raw = _raw_assumptions_json()
    current = dict(raw.get("measurement_defaults") or {})

    material_depth_inches = max(
        float(_decimal(payload.get("material_depth_inches"), str(current.get("material_depth_inches", "2")))),
        0.25,
    )
    minimum_material_yards = max(
        float(_decimal(payload.get("minimum_material_yards"), str(current.get("minimum_material_yards", "0.25")))),
        0.0,
    )
    quote_price_rounding_dollars = max(
        float(_decimal(payload.get("quote_price_rounding_dollars"), str(current.get("quote_price_rounding_dollars", "5")))),
        0.0,
    )
    bulk_discount_threshold_1_yards = max(
        float(_decimal(payload.get("bulk_discount_threshold_1_yards"), str(current.get("bulk_discount_threshold_1_yards", "10")))),
        0.0,
    )
    bulk_discount_percent_1 = max(
        float(_decimal(payload.get("bulk_discount_percent_1"), str(current.get("bulk_discount_percent_1", "5")))),
        0.0,
    )
    bulk_discount_threshold_2_yards = max(
        float(_decimal(payload.get("bulk_discount_threshold_2_yards"), str(current.get("bulk_discount_threshold_2_yards", "20")))),
        0.0,
    )
    bulk_discount_percent_2 = max(
        float(_decimal(payload.get("bulk_discount_percent_2"), str(current.get("bulk_discount_percent_2", "8")))),
        0.0,
    )
    bulk_discount_threshold_3_yards = max(
        float(_decimal(payload.get("bulk_discount_threshold_3_yards"), str(current.get("bulk_discount_threshold_3_yards", "30")))),
        0.0,
    )
    bulk_discount_percent_3 = max(
        float(_decimal(payload.get("bulk_discount_percent_3"), str(current.get("bulk_discount_percent_3", "12")))),
        0.0,
    )
    load_split_strategy = str(payload.get("load_split_strategy", current.get("load_split_strategy", "max_loads_first")) or "max_loads_first").strip().lower()
    if load_split_strategy not in LOAD_SPLIT_STRATEGIES:
        raise ValueError("load_split_strategy must be one of: max_loads_first, balanced_loads")

    raw["measurement_defaults"] = {
        "material_depth_inches": material_depth_inches,
        "minimum_material_yards": minimum_material_yards,
        "quote_price_rounding_dollars": quote_price_rounding_dollars,
        "bulk_discount_threshold_1_yards": bulk_discount_threshold_1_yards,
        "bulk_discount_percent_1": bulk_discount_percent_1,
        "bulk_discount_threshold_2_yards": bulk_discount_threshold_2_yards,
        "bulk_discount_percent_2": bulk_discount_percent_2,
        "bulk_discount_threshold_3_yards": bulk_discount_threshold_3_yards,
        "bulk_discount_percent_3": bulk_discount_percent_3,
        "load_split_strategy": load_split_strategy,
    }
    _write_raw_assumptions_json(raw)
    return serialize_pricing_assumptions()["measurement_defaults"]


def update_labor_defaults(defaults: dict) -> dict:
    if not isinstance(defaults, dict):
        raise ValueError("labor_defaults payload must be an object")

    raw = _raw_assumptions_json()
    existing = dict(raw.get("labor_defaults") or {})
    allowed_keys = (
        "blower_placement_rate_per_yard",
        "blowing_service_fee",
        "obstacle_fee_per_obstacle",
        "gate_access_fee",
        "haul_away_base_fee",
    )

    for key in allowed_keys:
        if key in defaults:
            existing[key] = max(float(_decimal(defaults.get(key), str(existing.get(key, "0")))), 0.0)

    raw["labor_defaults"] = existing
    _write_raw_assumptions_json(raw)
    return serialize_pricing_assumptions()["labor_defaults"]
