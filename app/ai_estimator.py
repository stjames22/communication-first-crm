from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

from .assumptions import measurement_defaults, normalize_blower_material, zip_factor_for_code
from .ai_photo_analysis import analyze_uploaded_images, measurement_entries_to_bed_groups, normalize_measurement_entries
from .pricing_engine import calculate_detected_pricing


TERRAIN_FACTORS = {
    "flat": Decimal("1.00"),
    "mixed": Decimal("1.10"),
    "sloped": Decimal("1.20"),
    "hilly": Decimal("1.32"),
    "wooded": Decimal("1.40"),
}
REQUIRED_ANGLES = ("front", "back", "left", "right")
PRIMARY_JOB_TYPE_BY_MATERIAL = {
    "soil": "topsoil_install",
    "compost": "compost_refresh",
    "mulch": "mulch_refresh",
}
SUPPORTED_SERVICE_JOB_TYPES = {
    "flower_bed_refresh",
    "topsoil_install",
    "mulch_refresh",
    "compost_refresh",
}
SERVICE_JOB_LABELS = {
    "flower_bed_refresh": "Flower Bed Refresh",
    "topsoil_install": "Topsoil Install",
    "mulch_refresh": "Mulch Refresh",
    "compost_refresh": "Compost Refresh",
}

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


def _dimension(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _yards(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _measurement_depth_inches() -> Decimal:
    defaults = measurement_defaults()
    return max(_decimal(defaults.get("material_depth_inches"), Decimal("2.0")), Decimal("0.25"))


def _minimum_material_yards() -> Decimal:
    defaults = measurement_defaults()
    return max(_decimal(defaults.get("minimum_material_yards"), Decimal("0.25")), Decimal("0"))


def _quote_price_rounding_dollars() -> Decimal:
    defaults = measurement_defaults()
    return max(_decimal(defaults.get("quote_price_rounding_dollars"), Decimal("5")), Decimal("0"))


def _round_price_for_quote(value: Decimal) -> Decimal:
    increment = _quote_price_rounding_dollars()
    if increment <= 0:
        return _money(value)
    rounded = (value / increment).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * increment
    return _money(rounded)


def _normalize_zip(zip_code: Optional[str]) -> str:
    digits = "".join(ch for ch in str(zip_code or "") if ch.isdigit())
    return digits[:5]


def _photo_keywords(uploaded_images: Iterable[dict]) -> set[str]:
    keywords = set()
    for image in uploaded_images or []:
        name = str(image.get("file_name") or "").lower()
        for token in ("front", "back", "left", "right", "driveway", "fence", "hedge", "debris", "gate"):
            if token in name:
                keywords.add(token)
    return keywords


def _infer_missing_angle_estimate(
    *,
    lot_size: Decimal,
    edge_length: Decimal,
    uploaded_images: Iterable[dict],
) -> dict:
    keywords = _photo_keywords(uploaded_images)
    available_angles = [angle for angle in REQUIRED_ANGLES if angle in keywords]
    missing_angles = [angle for angle in REQUIRED_ANGLES if angle not in keywords]

    # Assume rectangular footprint and infer one missing dimension from the other.
    side = lot_size.sqrt()
    width_seed = max(side * Decimal("1.10"), edge_length / Decimal("6"))
    depth_seed = max(lot_size / max(width_seed, Decimal("1")), side * Decimal("0.80"))

    has_front_or_back = "front" in keywords or "back" in keywords
    has_left_or_right = "left" in keywords or "right" in keywords

    confidence = Decimal("0.52")
    basis = "Limited angle coverage; dimensions inferred mainly from area."
    if has_front_or_back and has_left_or_right:
        confidence = Decimal("0.74")
        basis = "Front/back and side angle cues present; dimensions inferred from both photo orientation and area."
    elif has_front_or_back:
        confidence = Decimal("0.62")
        basis = "Front/back cues present; depth inferred from area."
    elif has_left_or_right:
        confidence = Decimal("0.62")
        basis = "Left/right cues present; width inferred from area."

    if len(available_angles) >= 3:
        confidence = max(confidence, Decimal("0.78"))

    estimated_width = _dimension(width_seed)
    estimated_depth = _dimension(depth_seed)

    return {
        "available_angles": available_angles,
        "missing_angles": missing_angles,
        "estimated_width_ft": estimated_width,
        "estimated_depth_ft": estimated_depth,
        "estimated_area_sqft": _dimension(estimated_width * estimated_depth),
        "confidence": float(confidence),
        "basis": basis,
    }


def _recommended_material_yards(area_sqft: Decimal, depth_inches: Optional[Decimal] = None) -> Decimal:
    depth = depth_inches if depth_inches is not None else _measurement_depth_inches()
    depth = max(depth, Decimal("0.25"))
    if area_sqft <= 0:
        return _minimum_material_yards()
    volume_yards = (area_sqft * (depth / Decimal("12"))) / Decimal("27")
    return _yards(max(volume_yards, _minimum_material_yards()))


def _photo_estimate_measurement_entry(
    *,
    missing_angle_estimate: dict,
    uploaded_images: Iterable[dict],
    selected_material_depth: Decimal,
) -> Optional[dict]:
    width_ft = max(_decimal((missing_angle_estimate or {}).get("estimated_width_ft"), Decimal("0")), Decimal("0"))
    depth_ft = max(_decimal((missing_angle_estimate or {}).get("estimated_depth_ft"), Decimal("0")), Decimal("0"))
    confidence = max(_decimal((missing_angle_estimate or {}).get("confidence"), Decimal("0")), Decimal("0"))
    if width_ft <= 0 or depth_ft <= 0:
        return None

    length_ft = max(width_ft, depth_ft)
    narrow_ft = min(width_ft, depth_ft)
    area_sqft = _dimension(length_ft * narrow_ft)
    source_images: list[str] = []
    source_asset_id = None
    source_filename = None
    for image in uploaded_images or []:
        name = str((image or {}).get("file_name") or "").strip()
        if name and name not in source_images:
            source_images.append(name)
        if source_asset_id is None and (image or {}).get("id") is not None:
            source_asset_id = (image or {}).get("id")
            source_filename = name or None

    return {
        "entry_type": "dimension_pair",
        "raw_text": "Photo estimate",
        "original_raw_text": "Photo estimate",
        "length_ft": float(length_ft),
        "width_ft": float(narrow_ft),
        "depth_in": float(selected_material_depth),
        "estimated_area_sqft": float(area_sqft),
        "estimated_material_yards": float(_recommended_material_yards(area_sqft, selected_material_depth)),
        "confidence": float(confidence),
        "source_images": source_images[:5],
        "source_asset_id": source_asset_id,
        "source_filename": source_filename,
        "source_type": "site-media",
        # Keep inferred photo rows visible in Measurement Review without silently
        # treating them as confirmed dimensions.
        "include": False,
        "needs_review": True,
        "notes": "Background estimate only — no parsed image attached yet.",
        "inferred_from_photo_estimate": True,
        "inference_basis": str((missing_angle_estimate or {}).get("basis") or "").strip(),
    }


def _crew_sentence(worker_count: int, duration_hours: Decimal) -> str:
    worker_word = "worker" if worker_count == 1 else "workers"
    return f"Estimated crew: {worker_count} {worker_word} for {duration_hours} hours."


def generate_crew_instructions(
    *,
    detected_tasks: Iterable[str],
    detected_zones: Optional[Iterable[dict]] = None,
    bed_groups: Optional[Iterable[dict]] = None,
    material_type: str,
    placement_method: str,
    uploaded_images: Iterable[dict],
    obstacle_count: int = 0,
    include_haulaway: bool = False,
    has_gates: bool = False,
    job_notes: Optional[str] = None,
    exclusions: Optional[str] = None,
    worker_count: int = 2,
    duration_hours: Decimal = Decimal("2.0"),
) -> str:
    keywords = _photo_keywords(uploaded_images)
    steps = []

    task_steps = {
        "leaf_cleanup": "Front yard leaf cleanup.",
        "hedge_trim": "Trim hedge lines and shape shrubs.",
        "brush_removal": "Remove brush piles and overgrowth.",
        "general_cleanup": "General yard cleanup.",
    }
    normalized_tasks = []
    for task in detected_tasks or []:
        task_key = str(task or "").strip().lower()
        if task_key and task_key not in normalized_tasks:
            normalized_tasks.append(task_key)
    if not normalized_tasks:
        normalized_tasks = ["general_cleanup"]

    for task in normalized_tasks:
        steps.append(task_steps.get(task, "General yard cleanup."))

    zone_labels = []
    for zone in detected_zones or []:
        label = str((zone or {}).get("label") or "").strip()
        if label:
            zone_labels.append(label)
    if zone_labels:
        steps.append(f"Zones: {', '.join(zone_labels)}.")

    bed_group_items = []
    for idx, bed in enumerate(bed_groups or [], start=1):
        label = str((bed or {}).get("label") or f"Bed {idx}").strip()
        if not label:
            continue
        area = _decimal((bed or {}).get("estimated_area_sqft"), Decimal("0"))
        material = _decimal((bed or {}).get("estimated_material_yards"), Decimal("0"))
        detail = label
        if area > 0:
            detail += f" (~{_dimension(area)} sq ft)"
        if material > 0:
            detail += f" (~{_yards(material)} cu yd)"
        bed_group_items.append(detail)
    if bed_group_items:
        steps.append(f"Combined bed scope: {', '.join(bed_group_items)}.")

    if "front" not in keywords and material_type:
        steps.append(f"Handle site material using {placement_method.replace('_', ' ')} access.")

    if "driveway" in keywords:
        steps.append("Remove debris along the driveway and keep edges clear.")
    elif include_haulaway:
        steps.append("Remove loose debris and haul away green waste.")

    if "hedge" in keywords or "hedge" in str(job_notes or "").lower():
        steps.append("Trim hedge lines before final cleanup.")
    elif "fence" in keywords:
        steps.append("Work carefully along the fence line and edge transitions.")

    if obstacle_count > 0:
        steps.append(f"Work around {obstacle_count} obstacle area(s).")

    if has_gates:
        steps.append("Use gate-safe delivery access and protect narrow entry points.")

    if exclusions:
        steps.append(f"Do not cover: {str(exclusions).strip()}.")

    if job_notes:
        note = str(job_notes).strip()
        if note:
            steps.append(f"Sales notes: {note}.")

    return " ".join(step.strip() for step in steps if step.strip())


def estimate_job(
    *,
    lot_size_sqft,
    edge_length,
    terrain_type: Optional[str],
    zip_code: Optional[str],
    uploaded_images: Iterable[dict],
    measurement_reference_images: Optional[Iterable[dict]] = None,
    job_notes: Optional[str] = None,
    exclusions: Optional[str] = None,
    material_type: str = "mulch",
    placement_method: str = "blown_in",
    obstacle_count: int = 0,
    include_haulaway: bool = False,
    has_gates: bool = False,
    primary_job_type_override: Optional[str] = None,
    confirmed_measurement_entries: Optional[Iterable[dict]] = None,
    material_depth_inches: Optional[Decimal] = None,
    allow_site_media_measurement_reference_detection: bool = True,
) -> dict:
    raw_lot_size = _decimal(lot_size_sqft, Decimal("0"))
    raw_edge = _decimal(edge_length, Decimal("0"))
    terrain = (terrain_type or "mixed").strip().lower()
    terrain_factor = TERRAIN_FACTORS.get(terrain, TERRAIN_FACTORS["mixed"])
    regional_factor = zip_factor_for_code(zip_code, default="1.05")

    photos = list(uploaded_images or [])
    measurement_reference_photos = list(measurement_reference_images or [])
    photo_count = max(len(photos), 1)
    photo_complexity = Decimal("1.00") + (Decimal(min(photo_count, 6)) * Decimal("0.02"))
    obstacle_factor = Decimal("1.00") + (Decimal(max(obstacle_count, 0)) * Decimal("0.04"))
    gate_factor = Decimal("1.05") if has_gates else Decimal("1.00")
    cleanup_factor = Decimal("1.10") if include_haulaway else Decimal("1.00")
    selected_material_depth = max(
        _decimal(material_depth_inches, _measurement_depth_inches()),
        Decimal("0.25"),
    )
    analysis = analyze_uploaded_images(
        photos,
        notes=job_notes,
        measurement_reference_images=measurement_reference_photos,
        allow_site_media_measurement_reference_detection=allow_site_media_measurement_reference_detection,
    )
    dimension_info = analysis.get("dimension_observations") or {}
    measurement_entries = analysis.get("measurement_entries") or []
    bed_groups = analysis.get("bed_groups") or []
    extraction_meta = analysis.get("extraction_meta") or {}
    measurement_parse = analysis.get("measurement_parse") or {}
    combined_bed_area_sqft = max(_decimal(analysis.get("combined_bed_area_sqft"), Decimal("0")), Decimal("0"))
    combined_bed_material_yards = max(
        _decimal(analysis.get("combined_bed_material_yards"), Decimal("0")),
        Decimal("0"),
    )
    measurement_reference_mode = bool(measurement_reference_photos) or bool(extraction_meta.get("measurement_reference_images_present"))
    explicit_material_yards = Decimal("0")
    if confirmed_measurement_entries is not None:
        measurement_entries = normalize_measurement_entries(confirmed_measurement_entries)
        confirmed_bed_groups, confirmed_area, confirmed_material, explicit_yards = measurement_entries_to_bed_groups(
            measurement_entries
        )
        if confirmed_bed_groups:
            bed_groups = confirmed_bed_groups
            combined_bed_area_sqft = max(_decimal(confirmed_area, Decimal("0")), Decimal("0"))
            combined_bed_material_yards = max(_decimal(confirmed_material, Decimal("0")), Decimal("0"))
        if explicit_yards is not None:
            explicit_material_yards = max(_decimal(explicit_yards, Decimal("0")), Decimal("0"))
    dimension_area = max(_decimal(dimension_info.get("estimated_area_sqft"), Decimal("0")), Decimal("0"))
    dimension_length_in = max(_decimal(dimension_info.get("length_in"), Decimal("0")), Decimal("0"))
    dimension_width_in = max(_decimal(dimension_info.get("width_in"), Decimal("0")), Decimal("0"))
    dimension_material_yards = max(_decimal(dimension_info.get("estimated_material_yards"), Decimal("0")), Decimal("0"))

    if confirmed_measurement_entries is None:
        if dimension_area > 0:
            dimension_material_yards = _recommended_material_yards(dimension_area, selected_material_depth)
            dimension_info["estimated_material_yards"] = dimension_material_yards
        if combined_bed_area_sqft > 0:
            combined_bed_material_yards = _recommended_material_yards(combined_bed_area_sqft, selected_material_depth)
        if measurement_entries:
            adjusted_entries = []
            for entry in measurement_entries:
                if str((entry or {}).get("entry_type") or "").strip().lower() == "dimension_pair":
                    adjusted = dict(entry)
                    area_sqft = max(_decimal(adjusted.get("estimated_area_sqft"), Decimal("0")), Decimal("0"))
                    if area_sqft > 0:
                        adjusted["estimated_material_yards"] = _recommended_material_yards(area_sqft, selected_material_depth)
                    adjusted_entries.append(adjusted)
                else:
                    adjusted_entries.append(entry)
            measurement_entries = adjusted_entries
        if bed_groups:
            adjusted_beds = []
            for bed in bed_groups:
                adjusted = dict(bed or {})
                area_sqft = max(_decimal(adjusted.get("estimated_area_sqft"), Decimal("0")), Decimal("0"))
                if area_sqft > 0:
                    adjusted["estimated_material_yards"] = _recommended_material_yards(area_sqft, selected_material_depth)
                adjusted_beds.append(adjusted)
            bed_groups = adjusted_beds

    if combined_bed_area_sqft <= 0 and bed_groups:
        combined_bed_area_sqft = Decimal(len(bed_groups)) * Decimal("24")
    if combined_bed_material_yards <= 0 and combined_bed_area_sqft > 0:
        combined_bed_material_yards = _recommended_material_yards(combined_bed_area_sqft, selected_material_depth)
    combined_length_in = Decimal("0")
    for bed in bed_groups:
        combined_length_in += max(_decimal((bed or {}).get("length_in"), Decimal("0")), Decimal("0"))

    if raw_lot_size > 0:
        lot_size = raw_lot_size
    elif combined_bed_area_sqft > 0:
        lot_size = combined_bed_area_sqft
    elif dimension_area > 0 and not measurement_reference_mode:
        lot_size = dimension_area
    else:
        lot_size = Decimal("4500") + (Decimal(len(photos)) * Decimal("350"))
    lot_size = max(lot_size, Decimal("8"))

    if raw_edge > 0:
        edge = raw_edge
    elif combined_length_in > 0:
        edge = (combined_length_in * Decimal("2")) / Decimal("12")
    elif bed_groups:
        edge = max(Decimal("12"), (lot_size.sqrt() * Decimal("4.0")).quantize(Decimal("0.01")))
    elif dimension_length_in > 0 and dimension_width_in > 0 and not measurement_reference_mode:
        edge = ((dimension_length_in + dimension_width_in) * Decimal("2")) / Decimal("12")
    elif dimension_length_in > 0 and not measurement_reference_mode:
        edge = (dimension_length_in * Decimal("2")) / Decimal("12")
    else:
        edge = max(Decimal("120"), (lot_size.sqrt() * Decimal("3.6")).quantize(Decimal("0.01")))
    edge = max(edge, Decimal("4"))

    raw_detected_tasks = [task for task in analysis.get("detected_tasks") or [] if isinstance(task, dict)]
    detected_tasks = [
        task for task in raw_detected_tasks if str(task.get("job_type") or "").strip().lower() in SUPPORTED_SERVICE_JOB_TYPES
    ]
    detected_job_types = [str(task["job_type"]).strip().lower() for task in detected_tasks]
    detected_zones = analysis.get("detected_zones") or []
    material_type = normalize_blower_material(material_type)

    inferred_material_job_type = PRIMARY_JOB_TYPE_BY_MATERIAL.get(material_type, "flower_bed_refresh")
    if bed_groups and inferred_material_job_type and inferred_material_job_type not in detected_job_types:
        detected_job_types.insert(0, inferred_material_job_type)

    override_job_type = str(primary_job_type_override or "").strip().lower()
    if override_job_type in SUPPORTED_SERVICE_JOB_TYPES:
        detected_job_types = [override_job_type] + [job_type for job_type in detected_job_types if job_type != override_job_type]

    if not detected_job_types:
        detected_job_types = [inferred_material_job_type]

    # Preserve order while deduping.
    deduped_job_types = []
    for job_type in detected_job_types:
        if job_type not in deduped_job_types:
            deduped_job_types.append(job_type)
    detected_job_types = deduped_job_types

    pricing = calculate_detected_pricing(lot_size, detected_job_types, zip_code)
    complexity_factor = terrain_factor * photo_complexity * obstacle_factor * gate_factor * cleanup_factor
    missing_angle_estimate = _infer_missing_angle_estimate(
        lot_size=lot_size,
        edge_length=edge,
        uploaded_images=photos,
    )
    has_dimension_rows = any(
        str((entry or {}).get("entry_type") or "").strip().lower() == "dimension_pair"
        for entry in measurement_entries
    )
    if measurement_reference_mode and not has_dimension_rows:
        dimension_info = {}
        dimension_area = Decimal("0")
        dimension_length_in = Decimal("0")
        dimension_width_in = Decimal("0")
        dimension_material_yards = Decimal("0")
        missing_angle_estimate = {
            "available_angles": [],
            "missing_angles": [],
            "estimated_width_ft": None,
            "estimated_depth_ft": None,
            "estimated_area_sqft": None,
            "confidence": 0.0,
            "basis": "",
        }
    labor_hours = max(_hours(pricing["labor_hours"] * complexity_factor), Decimal("1.2"))
    material_cost = _money(pricing["material_cost"] * max(regional_factor / Decimal("1.05"), Decimal("0.95")))
    suggested_price = _round_price_for_quote(pricing["suggested_price"] * complexity_factor)
    if explicit_material_yards > 0:
        recommended_material_yards = explicit_material_yards
    elif combined_bed_material_yards > 0:
        recommended_material_yards = combined_bed_material_yards
    elif measurement_reference_mode and not has_dimension_rows:
        recommended_material_yards = Decimal("0")
    else:
        recommended_material_yards = Decimal("0") if not has_dimension_rows else _recommended_material_yards(lot_size, selected_material_depth)

    worker_count = 1
    if labor_hours >= Decimal("2.6"):
        worker_count = 2
    if labor_hours >= Decimal("5.5"):
        worker_count = 3
    duration_hours = _hours(labor_hours / Decimal(worker_count))

    crew_instructions = generate_crew_instructions(
        detected_tasks=detected_job_types,
        detected_zones=detected_zones,
        bed_groups=bed_groups,
        material_type=material_type,
        placement_method=placement_method,
        uploaded_images=photos,
        obstacle_count=obstacle_count,
        include_haulaway=include_haulaway,
        has_gates=has_gates,
        job_notes=job_notes,
        exclusions=exclusions,
        worker_count=worker_count,
        duration_hours=duration_hours,
    )

    primary_job_type = str(pricing.get("primary_job_type") or inferred_material_job_type).strip().lower()
    if primary_job_type not in SUPPORTED_SERVICE_JOB_TYPES:
        primary_job_type = inferred_material_job_type

    detected_task_map = {}
    for task in detected_tasks:
        job_type = str(task.get("job_type") or "").strip().lower()
        if job_type not in SUPPORTED_SERVICE_JOB_TYPES:
            continue
        detected_task_map[job_type] = {
            "job_type": job_type,
            "label": str(task.get("label") or SERVICE_JOB_LABELS.get(job_type, job_type.replace("_", " ").title())),
            "confidence": task.get("confidence", 0.6),
            "evidence": task.get("evidence") or [],
        }

    if primary_job_type not in detected_task_map:
        detected_task_map[primary_job_type] = {
            "job_type": primary_job_type,
            "label": SERVICE_JOB_LABELS.get(primary_job_type, primary_job_type.replace("_", " ").title()),
            "confidence": 0.82,
            "evidence": ["material_selection"],
        }

    detected_tasks_output = [detected_task_map[job_type] for job_type in detected_job_types if job_type in detected_task_map]

    return {
        "estimated_labor_hours": labor_hours,
        "material_cost": material_cost,
        "equipment_cost": material_cost,
        "suggested_price": suggested_price,
        "recommended_crew_size": worker_count,
        "estimated_duration_hours": duration_hours,
        "terrain_type": terrain,
        "zip_code": _normalize_zip(zip_code),
        "crew_instructions": crew_instructions,
        "primary_job_type": primary_job_type,
        "detected_tasks": detected_tasks_output,
        "task_breakdown": pricing["task_breakdown"],
        "analysis_summary": analysis["summary"],
        "detected_zones": detected_zones,
        "zone_summary": analysis.get("zone_summary") or "",
        "measurement_entries": measurement_entries,
        "measurement_parse": measurement_parse,
        "bed_groups": bed_groups,
        "combined_bed_area_sqft": _dimension(combined_bed_area_sqft) if combined_bed_area_sqft > 0 else None,
        "combined_bed_material_yards": _yards(combined_bed_material_yards)
        if combined_bed_material_yards > 0
        else None,
        "area_sqft": lot_size,
        "edge_length_ft": _dimension(edge),
        "recommended_material_yards": recommended_material_yards,
        "measurement_defaults": {
            "material_depth_inches": selected_material_depth,
            "minimum_material_yards": _minimum_material_yards(),
            "quote_price_rounding_dollars": _quote_price_rounding_dollars(),
        },
        "dimension_observations": dimension_info,
        "extraction_meta": extraction_meta,
        "missing_angle_estimate": {
            "available_angles": missing_angle_estimate["available_angles"],
            "missing_angles": missing_angle_estimate["missing_angles"],
            "estimated_width_ft": missing_angle_estimate["estimated_width_ft"],
            "estimated_depth_ft": missing_angle_estimate["estimated_depth_ft"],
            "estimated_area_sqft": missing_angle_estimate["estimated_area_sqft"],
            "confidence": missing_angle_estimate["confidence"],
            "basis": missing_angle_estimate["basis"],
        },
    }
