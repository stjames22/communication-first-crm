from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Optional

from .assumptions import measurement_defaults
from .settings import get_settings, refresh_settings, runtime_openai_api_key
from .storage import storage

TASK_PATTERNS = {
    "flower_bed_refresh": {
        "keywords": {
            "bed",
            "beds",
            "flower",
            "flowers",
            "flowerbed",
            "flowerbeds",
            "planter",
            "planters",
            "garden",
            "mulch",
            "topsoil",
            "soil",
            "compost",
            "box",
            "boxes",
        },
        "label": "Flower Bed Refresh",
    },
    "leaf_cleanup": {
        "keywords": {"leaf", "leaves", "debris", "cleanup", "yard", "blower", "front", "back", "driveway"},
        "label": "Leaf Cleanup",
    },
    "hedge_trim": {
        "keywords": {"hedge", "trim", "shrub", "bush", "fence", "row", "topiary"},
        "label": "Hedge Trim",
    },
    "brush_removal": {
        "keywords": {"brush", "blackberry", "branch", "branches", "removal", "overgrowth", "limb", "brushy"},
        "label": "Brush Removal",
    },
}
IGNORED_TOKENS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "heif", "img", "image"}
FLOWER_BED_KEYWORDS = {
    "bed",
    "beds",
    "flower",
    "flowers",
    "flowerbed",
    "flowerbeds",
    "planter",
    "planters",
    "garden",
    "box",
    "boxes",
}
YARD_KEYWORDS = {"yard", "lawn", "front", "back", "backyard", "frontyard", "driveway"}
MEASUREMENT_REFERENCE_KEYWORDS = {
    "measure",
    "measurement",
    "measurements",
    "dimension",
    "dimensions",
    "dims",
    "note",
    "notes",
    "sheet",
    "estimate",
    "estimates",
    "calc",
    "calculation",
    "screenshot",
}

FRACTION_REPLACEMENTS = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅐": "1/7",
    "⅑": "1/9",
    "⅒": "1/10",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

INCH_PATTERN = re.compile(
    r"(?<![\d/])(\d{1,3}(?:\.\d+)?(?:\s+\d/\d)?|\d{1,3}/\d)\s*(?:\"|inches?\b|inch\b|in\b)?",
    re.IGNORECASE,
)
MEASUREMENT_VALUE_PART = (
    r"(?:\d{1,4}(?:\.\d+)?(?:\s+\d/\d)?|\d{1,4}/\d)"
    r"(?:\s*(?:ft|feet|foot|'))?"
    r"(?:\s+(?:\d{1,3}(?:\.\d+)?(?:\s+\d/\d)?|\d{1,3}/\d)\s*(?:in|inch|inches|\"))?"
)
MEASUREMENT_PAIR_PATTERN = re.compile(
    rf"(?<!\d)({MEASUREMENT_VALUE_PART})\s*(?:x|×|by)\s*({MEASUREMENT_VALUE_PART})(?!\d)",
    re.IGNORECASE,
)
YARD_PATTERN = re.compile(
    rf"(?<!\d)({MEASUREMENT_VALUE_PART})\s*(?:yds?|yards?)\b",
    re.IGNORECASE,
)
MEASUREMENT_TOKEN_PATTERN = re.compile(MEASUREMENT_VALUE_PART, re.IGNORECASE)

SWIFT_OCR_SCRIPT = r"""
import Foundation
import Vision
import ImageIO

func emit(path: String, text: String, error: String) {
    let payload: [String: String] = ["path": path, "text": text, "error": error]
    guard let data = try? JSONSerialization.data(withJSONObject: payload, options: []),
          let line = String(data: data, encoding: .utf8) else {
        return
    }
    print(line)
}

for path in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        emit(path: path, text: "", error: "decode_failed")
        continue
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
        let text = (request.results ?? [])
            .compactMap { $0.topCandidates(1).first?.string }
            .joined(separator: "\n")
        emit(path: path, text: text, error: "")
    } catch {
        emit(path: path, text: "", error: "ocr_failed")
    }
}
"""

settings = get_settings()
logger = logging.getLogger("growthsignal")
OPENAI_NETWORK_ERROR_MAP = {
    "dns": "openai_dns_failed",
    "timeout": "openai_request_timed_out",
    "tls": "openai_tls_failed",
    "refused": "openai_connection_refused",
    "reset": "openai_connection_reset",
    "request": "openai_request_failed",
}
OPENAI_DNS_FAILURE_MARKERS = (
    "nodename nor servname provided",
    "name or service not known",
    "temporary failure in name resolution",
    "failure in name resolution",
    "getaddrinfo failed",
    "no address associated with hostname",
)
HANDWRITTEN_MEASUREMENT_PARSE_PROMPT = """
You are extracting handwritten landscaping measurements from a note photo.

Task:
Read the image and extract every handwritten measurement pair that represents dimensions in the format length x width.

Important:
- This is a BarkBoys landscaping worksheet note.
- The image may contain logos, printed text, business info, stray marks, and handwritten words.
- Ignore branding, printed footer text, addresses, phone numbers, and non-measurement notes unless they help interpret a measurement.
- Focus on handwritten numeric dimension pairs only.

Extraction rules:
1. Return only true dimension pairs written as two numbers that mean length x width.
2. Normalize all pairs into numeric fields:
   - "91x22" -> { "length": 91, "width": 22 }
   - "25 x 30" -> { "length": 25, "width": 30 }
3. Accept messy handwriting and imperfect spacing.
4. Accept large values if they appear real, such as:
   - 15x230
   - 100x7
   - 110x12
5. Do not discard a row just because it looks unusual.
6. Ignore standalone notes that are not dimension pairs, for example:
   - "Starbucks"
   - "4 yds"
   - isolated numbers without an x-pair
7. If a pair is ambiguous but still most likely a valid measurement, include it.
8. Preserve the approximate reading order from top to bottom, left to right.
9. Do not invent missing pairs.
10. Do not explain your reasoning.

Return JSON only in this exact shape:

{
  "rows": [
    {
      "raw": "91x22",
      "length": 91,
      "width": 22
    }
  ]
}
""".strip()
HANDWRITTEN_MEASUREMENT_PARSE_RETRY_PROMPT = """
Read this handwritten landscaping note again and extract ALL dimension pairs.
Be tolerant of messy handwriting.
Include unusual but plausible pairs like 15x230, 100x7, and 110x12.
Return JSON only in the same schema.
""".strip()
HANDWRITTEN_MEASUREMENT_PARSE_SCHEMA = {
    "type": "json_schema",
    "name": "handwritten_measurement_rows",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "raw": {"type": "string"},
                        "length": {"type": "number"},
                        "width": {"type": "number"},
                    },
                    "required": ["raw", "length", "width"],
                },
            },
        },
        "required": ["rows"],
    },
}


def _classify_openai_network_error(error: object) -> str:
    text = str(error or "").lower()
    if any(marker in text for marker in OPENAI_DNS_FAILURE_MARKERS):
        return OPENAI_NETWORK_ERROR_MAP["dns"]
    if "timed out" in text:
        return OPENAI_NETWORK_ERROR_MAP["timeout"]
    if any(token in text for token in ("certificate verify failed", "ssl", "tls", "wrong version number", "alert handshake failure")):
        return OPENAI_NETWORK_ERROR_MAP["tls"]
    if "connection refused" in text:
        return OPENAI_NETWORK_ERROR_MAP["refused"]
    if any(token in text for token in ("connection reset", "eof occurred in violation of protocol", "unexpected eof")):
        return OPENAI_NETWORK_ERROR_MAP["reset"]
    return OPENAI_NETWORK_ERROR_MAP["request"]


def _tokenize(text: Optional[str]) -> set[str]:
    raw = str(text or "").lower().replace("-", " ").replace("_", " ").replace(".", " ")
    cleaned = {token.strip(".,:;!?()[]{}") for token in raw.split() if token.strip(".,:;!?()[]{}")}
    return {token for token in cleaned if any(ch.isalpha() for ch in token) and token not in IGNORED_TOKENS}


def _normalize_fraction_text(text: str) -> str:
    normalized = str(text or "")
    for symbol, replacement in FRACTION_REPLACEMENTS.items():
        normalized = re.sub(rf"(\d){re.escape(symbol)}", rf"\1 {replacement}", normalized)
        normalized = normalized.replace(symbol, replacement)
    return normalized


def _to_decimal(value: str) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _parse_measurement_token(token: str) -> Optional[Decimal]:
    token = str(token or "").strip()
    if not token:
        return None

    if " " in token and "/" in token:
        # Mixed number, e.g. "4 1/2"
        whole, frac = token.split(" ", 1)
        whole_val = _to_decimal(whole)
        if whole_val is None:
            return None
        return whole_val + (_parse_measurement_token(frac) or Decimal("0"))

    if "/" in token:
        num_str, den_str = token.split("/", 1)
        num = _to_decimal(num_str)
        den = _to_decimal(den_str)
        if num is None or den in (None, Decimal("0")):
            return None
        return num / den

    return _to_decimal(token)


def _extract_measurements_in(text: str) -> list[Decimal]:
    normalized = _normalize_fraction_text(str(text or ""))
    has_explicit_units = bool(re.search(r"(\"|inches?\b|inch\b|in\b)", normalized, re.IGNORECASE))
    values: list[Decimal] = []
    fallback_candidates: list[Decimal] = []

    for match in INCH_PATTERN.finditer(normalized):
        raw = match.group(1)
        value = _parse_measurement_token(raw)
        if value is None:
            continue
        if value <= 0 or value > 240:
            continue

        # Keep explicit inch values immediately; otherwise hold as a fallback candidate.
        span_text = normalized[max(0, match.start() - 1): min(len(normalized), match.end() + 4)]
        if has_explicit_units or "\"" in span_text:
            values.append(value)
        else:
            fallback_candidates.append(value)

    if not values and fallback_candidates:
        # OCR sometimes drops inch marks. Keep plausible dimensions when no large numbers dominate.
        plausible = [value for value in fallback_candidates if Decimal("1.0") <= value <= Decimal("96.0")]
        if plausible and len(plausible) <= 8:
            values.extend(plausible)

    deduped = sorted({value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for value in values})
    return deduped


def _measurement_token_to_feet(token: str) -> Optional[Decimal]:
    normalized = _normalize_fraction_text(str(token or "").strip().lower())
    if not normalized:
        return None

    feet_match = re.search(r"(.+?)(?:ft|feet|foot|')", normalized)
    inch_match = re.search(r"(.+?)(?:in|inch|inches|\")", normalized)

    feet_value = None
    inches_value = None

    if feet_match:
        feet_value = _parse_measurement_token(feet_match.group(1).strip())
        remainder = normalized[feet_match.end():].strip()
        if remainder:
            remainder = re.sub(r"(?:in|inch|inches|\")", "", remainder).strip()
            if remainder:
                inches_value = _parse_measurement_token(remainder)
    elif inch_match and not re.search(r"(?:ft|feet|foot|')", normalized):
        inches_value = _parse_measurement_token(inch_match.group(1).strip())
    else:
        compact = re.sub(r"[^0-9./\s-]", " ", normalized).strip()
        if compact:
            feet_value = _parse_measurement_token(compact)

    total_feet = Decimal("0")
    if feet_value is not None:
        total_feet += feet_value
    if inches_value is not None:
        total_feet += inches_value / Decimal("12")

    if total_feet <= 0:
        return None
    return total_feet.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _extract_measurement_entries(text: str, source_images: list[str]) -> list[dict]:
    normalized = _normalize_fraction_text(str(text or ""))
    entries: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    depth_inches = _to_decimal_number(measurement_defaults().get("material_depth_inches")) or Decimal("2.0")

    def append_dimension_pair(
        raw_left: str,
        raw_right: str,
        raw_text: str,
        confidence: Decimal,
    ) -> bool:
        left_ft = _measurement_token_to_feet(raw_left)
        right_ft = _measurement_token_to_feet(raw_right)
        if left_ft is None or right_ft is None:
            return False
        if left_ft <= 0 or right_ft <= 0:
            return False
        if left_ft > Decimal("1000") or right_ft > Decimal("1000"):
            return False

        key = (str(left_ft), str(right_ft))
        if key in seen_pairs:
            return False
        seen_pairs.add(key)

        area_sqft = (left_ft * right_ft).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        material_yards = (
            (area_sqft * (depth_inches / Decimal("12"))) / Decimal("27")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        entries.append(
            {
                "entry_type": "dimension_pair",
                "raw_text": raw_text.strip(),
                "length_ft": float(left_ft),
                "width_ft": float(right_ft),
                "estimated_area_sqft": float(area_sqft),
                "estimated_material_yards": float(material_yards),
                "confidence": float(confidence),
                "source_images": source_images[:5],
            }
        )
        return True

    for match in MEASUREMENT_PAIR_PATTERN.finditer(normalized):
        raw_left = match.group(1).strip()
        raw_right = match.group(2).strip()
        explicit_units = bool(re.search(r"(?:ft|feet|foot|'|in|inch|inches|\")", raw_left + raw_right, re.IGNORECASE))
        confidence = Decimal("0.89") if explicit_units else Decimal("0.83")
        append_dimension_pair(raw_left, raw_right, match.group(0), confidence)

    yard_entries: list[dict] = []
    seen_yards: set[str] = set()
    for match in YARD_PATTERN.finditer(normalized):
        value_ft = _measurement_token_to_feet(match.group(1))
        if value_ft is None:
            continue
        if value_ft <= 0 or value_ft > Decimal("500"):
            continue
        key = str(value_ft)
        if key in seen_yards:
            continue
        seen_yards.add(key)
        yard_entries.append(
            {
                "entry_type": "material_yards",
                "raw_text": match.group(0).strip(),
                "yards": float(value_ft),
                "confidence": 0.9,
                "source_images": source_images[:5],
            }
        )

    line_based_pairs: list[dict] = []
    line_candidates = re.split(r"[\r\n]+", normalized)
    if len(line_candidates) <= 1:
        line_candidates = re.split(r"\s{2,}", normalized)

    for line in line_candidates:
        line_text = str(line or "").strip()
        if not line_text:
            continue
        if YARD_PATTERN.search(line_text):
            continue
        if MEASUREMENT_PAIR_PATTERN.search(line_text):
            continue

        token_matches = list(MEASUREMENT_TOKEN_PATTERN.finditer(line_text))
        if len(token_matches) != 2:
            continue

        raw_left = token_matches[0].group(0).strip()
        raw_right = token_matches[1].group(0).strip()
        between = line_text[token_matches[0].end():token_matches[1].start()]
        if re.search(r"[A-Za-z]", between) and not re.fullmatch(r"\s*[xXvVbyBY\-–—]*\s*", between):
            continue

        if append_dimension_pair(raw_left, raw_right, line_text, Decimal("0.72")):
            line_based_pairs.append(entries[-1])

    for line in line_candidates:
        line_text = str(line or "").strip()
        if not line_text:
            continue
        if YARD_PATTERN.search(line_text):
            continue
        if MEASUREMENT_PAIR_PATTERN.search(line_text):
            continue

        token_matches = list(MEASUREMENT_TOKEN_PATTERN.finditer(line_text))
        if len(token_matches) < 4 or len(token_matches) % 2 != 0:
            continue

        # OCR sometimes collapses multiple rows into one dense numeric line such as
        # "91 22 25 30 30 14". In that case, recover adjacent pairs in order.
        stripped = re.sub(r"(?:ft|feet|foot|in|inch|inches|yd|yds|yards|x|×|by)", " ", line_text, flags=re.IGNORECASE)
        if re.search(r"[A-Za-z]{2,}", stripped):
            continue

        recovered_count = 0
        for left_match, right_match in zip(token_matches[::2], token_matches[1::2]):
            raw_left = left_match.group(0).strip()
            raw_right = right_match.group(0).strip()
            raw_pair = f"{raw_left} x {raw_right}"
            if append_dimension_pair(raw_left, raw_right, raw_pair, Decimal("0.68")):
                recovered_count += 1
        if recovered_count:
            continue

    standalone_tokens: list[str] = []
    for line in line_candidates:
        line_text = str(line or "").strip()
        if not line_text:
            continue
        if YARD_PATTERN.search(line_text):
            continue
        if MEASUREMENT_PAIR_PATTERN.search(line_text):
            continue
        token_matches = list(MEASUREMENT_TOKEN_PATTERN.finditer(line_text))
        if len(token_matches) != 1:
            continue
        stripped = re.sub(r"(?:ft|feet|foot|in|inch|inches|yd|yds|yards)", " ", line_text, flags=re.IGNORECASE)
        if re.search(r"[A-Za-z]{2,}", stripped):
            continue
        standalone_tokens.append(token_matches[0].group(0).strip())

    if len(standalone_tokens) >= 4 and len(standalone_tokens) % 2 == 0:
        for raw_left, raw_right in zip(standalone_tokens[::2], standalone_tokens[1::2]):
            raw_pair = f"{raw_left} x {raw_right}"
            append_dimension_pair(raw_left, raw_right, raw_pair, Decimal("0.64"))

    return entries + yard_entries


def _measurement_text_score(text: str) -> tuple[int, Decimal, int, int]:
    entries = _extract_measurement_entries(text, [])
    dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
    yard_entries = [entry for entry in entries if entry.get("entry_type") == "material_yards"]
    total_area = Decimal("0")
    for entry in dimension_entries:
        area = _to_decimal_number(entry.get("estimated_area_sqft")) or Decimal("0")
        if area > 0:
            total_area += area
    return (
        len(dimension_entries),
        total_area.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        len(yard_entries),
        len(str(text or "").strip()),
    )


def _ignorable_measurement_row(row: str) -> bool:
    lower = str(row or "").strip().lower()
    if not lower:
        return True
    ignorable_tokens = {
        "bark boys",
        "barkboys",
        "landscape",
        "landscaping",
        "salem",
        "starbuck",
        "starbucks",
        "cherry avenue",
        "www.",
    }
    return any(token in lower for token in ignorable_tokens)


def _measurement_parse_result(text: str, source_name: str, measurement_entries: list[dict]) -> dict:
    source_label = str(source_name or "").strip() or "uploaded_image"
    note_like = _should_treat_as_measurement_reference(source_label, text)
    dimension_entries = [
        entry for entry in normalize_measurement_entries(measurement_entries, source_images=[source_label])
        if str(entry.get("entry_type") or "").strip().lower() == "dimension_pair"
    ]
    yard_entries = [
        entry for entry in normalize_measurement_entries(measurement_entries, source_images=[source_label])
        if str(entry.get("entry_type") or "").strip().lower() == "material_yards"
    ]

    unclear_rows: list[dict] = []
    rows = [line.strip() for line in str(text or "").replace("\r", "\n").split("\n") if line.strip()]
    for row in rows:
        row_entries = _extract_measurement_entries(row, [source_label])
        if row_entries:
            continue
        if not re.search(r"\d", row):
            continue
        if _ignorable_measurement_row(row):
            continue
        unclear_rows.append(
            {
                "raw": row,
                "reason": "Contains numbers but did not parse cleanly as a rectangle or yard entry.",
            }
        )

    total_square_feet = Decimal("0")
    for entry in dimension_entries:
        total_square_feet += _to_decimal_number(entry.get("estimated_area_sqft")) or Decimal("0")
    computed_cubic_yards = (
        (total_square_feet * (Decimal("2") / Decimal("12"))) / Decimal("27")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total_square_feet > 0 else Decimal("0")
    manual_cubic_yards = Decimal("0")
    for entry in yard_entries:
        manual_cubic_yards += _to_decimal_number(entry.get("yards")) or Decimal("0")
    grand_total_cubic_yards = (computed_cubic_yards + manual_cubic_yards).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if note_like and (dimension_entries or yard_entries):
        classification = "exact_measurement_note"
        should_use_geometry_fallback = False
    elif note_like:
        classification = "failed_ocr_unreadable_note"
        should_use_geometry_fallback = False
    else:
        classification = "scene_photo_estimation"
        should_use_geometry_fallback = True

    rectangles = [
        {
            "kind": "rectangle",
            "raw": str(entry.get("raw_text") or "").strip(),
            "length": float(_to_decimal_number(entry.get("length_ft")) or Decimal("0")),
            "width": float(_to_decimal_number(entry.get("width_ft")) or Decimal("0")),
            "area_sqft": float(_to_decimal_number(entry.get("estimated_area_sqft")) or Decimal("0")),
            "confidence": float(_to_decimal_number(entry.get("confidence")) or Decimal("0")),
        }
        for entry in dimension_entries
    ]
    manual_yards = [
        {
            "kind": "manual_yards",
            "raw": str(entry.get("raw_text") or "").strip(),
            "cubic_yards": float(_to_decimal_number(entry.get("yards")) or Decimal("0")),
            "confidence": float(_to_decimal_number(entry.get("confidence")) or Decimal("0")),
        }
        for entry in yard_entries
    ]

    return {
        "classification": classification,
        "rectangles": rectangles,
        "manual_yards": manual_yards,
        "unclear_rows": unclear_rows,
        "total_square_feet": float(total_square_feet.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "computed_cubic_yards_at_2_in": float(computed_cubic_yards),
        "manual_cubic_yards": float(manual_cubic_yards.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "grand_total_cubic_yards": float(grand_total_cubic_yards),
        "should_use_geometry_fallback": should_use_geometry_fallback,
    }


def _source_asset_map(
    uploaded_images: Iterable[dict],
    measurement_reference_images: Iterable[dict],
) -> dict[str, dict]:
    source_map: dict[str, dict] = {}
    for image in uploaded_images or []:
        source_name = str((image or {}).get("file_name") or "").strip()
        if not source_name:
          continue
        source_map[source_name] = {
            "source_asset_id": (image or {}).get("id"),
            "source_filename": source_name,
            "source_type": "site-media",
        }
    for image in measurement_reference_images or []:
        source_name = str((image or {}).get("file_name") or "").strip()
        if not source_name:
          continue
        source_map[source_name] = {
            "source_asset_id": (image or {}).get("id"),
            "source_filename": source_name,
            "source_type": "measurement-note",
        }
    return source_map


def _should_treat_as_measurement_reference(source_name: str, ocr_text: str) -> bool:
    name_tokens = _tokenize(source_name)
    if name_tokens.intersection(MEASUREMENT_REFERENCE_KEYWORDS):
        return True

    dimension_count, _total_area, yard_count, _text_length = _measurement_text_score(ocr_text)
    if dimension_count >= 2:
        return True
    if dimension_count >= 1 and yard_count >= 1:
        return True
    return False


def _select_best_ocr_text(candidates: list[str]) -> str:
    cleaned = [str(text or "").strip() for text in candidates if str(text or "").strip()]
    if not cleaned:
        return ""
    return max(cleaned, key=_measurement_text_score)


def _ocr_preview_excerpt(text: str, *, line_limit: int = 12, char_limit: int = 900) -> str:
    cleaned_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not cleaned_lines:
        return ""
    excerpt = "\n".join(cleaned_lines[:line_limit]).strip()
    if len(excerpt) <= char_limit and len(cleaned_lines) <= line_limit:
        return excerpt
    trimmed = excerpt[:char_limit].rstrip()
    return f"{trimmed}..."


def _merge_ocr_measurement_entries(candidates: list[str], source_images: list[str]) -> list[dict]:
    cleaned = [str(text or "").strip() for text in candidates if str(text or "").strip()]
    if not cleaned:
        return []

    scored: list[tuple[int, str, tuple[int, Decimal, int, int]]] = [
        (idx, text, _measurement_text_score(text)) for idx, text in enumerate(cleaned)
    ]
    scored.sort(key=lambda item: item[2], reverse=True)
    best_index, best_text, best_score = scored[0]
    best_count, best_area, _, _ = best_score

    retained_texts: list[tuple[int, str]] = []
    for candidate_index, text, score in scored:
        count, area, _, _ = score
        count_close = count >= max(1, best_count - 3)
        area_close = best_area <= 0 or area >= (best_area * Decimal("0.45"))
        if text == best_text or count_close or area_close:
            retained_texts.append((candidate_index, text))

    best_entries = _extract_measurement_entries(best_text, source_images)
    merged_entries: list[dict] = []
    for entry in best_entries:
        item = dict(entry)
        item["ocr_candidate_hits"] = 1
        item["recovered_from_alternate"] = False
        merged_entries.append(item)

    def _dimension_signature(entry: dict) -> tuple[float, float]:
        left = float(entry.get("length_ft") or 0)
        right = float(entry.get("width_ft") or 0)
        ordered = tuple(sorted((left, right)))
        return ordered[0], ordered[1]

    def _is_near_existing_dimension(entry: dict, existing_entries: list[dict]) -> bool:
        left, right = _dimension_signature(entry)
        for existing in existing_entries:
            if existing.get("entry_type") != "dimension_pair":
                continue
            existing_left, existing_right = _dimension_signature(existing)
            if abs(left - existing_left) <= 1.0 and abs(right - existing_right) <= 1.0:
                return True
        return False

    def _append_sources(target: dict, incoming: dict) -> None:
        sources = list(target.get("source_images") or [])
        for source in list(incoming.get("source_images") or []):
            if source not in sources:
                sources.append(source)
        target["source_images"] = sources[:5]

    for candidate_index, text in retained_texts:
        if candidate_index == best_index:
            continue
        candidate_entries = _extract_measurement_entries(text, source_images)
        for entry in candidate_entries:
            if entry.get("entry_type") == "dimension_pair":
                if _is_near_existing_dimension(entry, merged_entries):
                    for existing in merged_entries:
                        if existing.get("entry_type") != "dimension_pair":
                            continue
                        if _is_near_existing_dimension(entry, [existing]):
                            _append_sources(existing, entry)
                            existing["confidence"] = round(
                                min(0.98, max(float(existing.get("confidence") or 0), float(entry.get("confidence") or 0)) + 0.02),
                                2,
                            )
                            existing["ocr_candidate_hits"] = int(existing.get("ocr_candidate_hits") or 1) + 1
                            break
                    continue

                area = float(entry.get("estimated_area_sqft") or 0)
                if area < 120:
                    continue
                recovered_entry = dict(entry)
                recovered_entry["ocr_candidate_hits"] = 1
                recovered_entry["recovered_from_alternate"] = True
                merged_entries.append(recovered_entry)
                continue

            yards = float(entry.get("yards") or 0)
            duplicate_yard = False
            for existing in merged_entries:
                if existing.get("entry_type") != "material_yards":
                    continue
                if abs(float(existing.get("yards") or 0) - yards) < 0.01:
                    duplicate_yard = True
                    _append_sources(existing, entry)
                    existing["confidence"] = round(
                        min(0.98, max(float(existing.get("confidence") or 0), float(entry.get("confidence") or 0)) + 0.02),
                        2,
                    )
                    existing["ocr_candidate_hits"] = int(existing.get("ocr_candidate_hits") or 1) + 1
                    break
            if not duplicate_yard:
                recovered_entry = dict(entry)
                recovered_entry["ocr_candidate_hits"] = 1
                recovered_entry["recovered_from_alternate"] = True
                merged_entries.append(recovered_entry)

    def _sort_key(entry: dict) -> tuple[int, float]:
        if entry.get("entry_type") == "dimension_pair":
            area = float(entry.get("estimated_area_sqft") or 0)
            return (0, -area)
        return (1, -(float(entry.get("yards") or 0)))

    merged_entries.sort(key=_sort_key)
    return merged_entries


def _ocr_candidate_paths(path: Path, temp_dir: Path) -> list[Path]:
    candidates = [path]
    sips = shutil.which("sips")
    if not sips:
        return candidates

    for angle in (90, 180, 270):
        rotated_path = temp_dir / f"{path.stem}_rot{angle}{path.suffix}"
        try:
            proc = subprocess.run(
                [sips, "-r", str(angle), str(path), "--out", str(rotated_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception:
            continue
        if proc.returncode == 0 and rotated_path.exists():
            candidates.append(rotated_path)
    return candidates


def normalize_measurement_entries(entries: Iterable[dict], source_images: Optional[list[str]] = None) -> list[dict]:
    normalized_entries: list[dict] = []
    seen_keys: set[tuple] = set()
    source_images = source_images or []

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry_type = str(entry.get("entry_type") or "dimension_pair").strip().lower()
        include = bool(entry.get("include", True))
        if not include:
            continue

        if entry_type == "dimension_pair":
            length_ft = _to_decimal_number(entry.get("length_ft"))
            width_ft = _to_decimal_number(entry.get("width_ft"))
            if length_ft is None or width_ft is None:
                continue
            if length_ft <= 0 or width_ft <= 0:
                continue
            area_sqft = (length_ft * width_ft).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            depth_inches = _to_decimal_number(entry.get("depth_in")) or (_to_decimal_number(measurement_defaults().get("material_depth_inches")) or Decimal("2.0"))
            material_yards = _to_decimal_number(entry.get("estimated_material_yards"))
            if material_yards is None or material_yards <= 0:
                material_yards = (
                    (area_sqft * (depth_inches / Decimal("12"))) / Decimal("27")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            confidence = _to_decimal_number(entry.get("confidence")) or Decimal("0.9")
            key = ("dimension_pair", str(length_ft), str(width_ft))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            normalized_entries.append(
                {
                    "entry_type": "dimension_pair",
                    "raw_text": str(entry.get("raw_text") or f"{length_ft}x{width_ft}").strip(),
                    "length_ft": float(length_ft),
                    "width_ft": float(width_ft),
                    "depth_in": float(depth_inches),
                    "estimated_area_sqft": float(area_sqft),
                    "estimated_material_yards": float(material_yards),
                    "confidence": float(confidence),
                    "source_images": list(entry.get("source_images") or source_images)[:5],
                    "source_asset_id": entry.get("source_asset_id"),
                    "source_filename": entry.get("source_filename"),
                    "source_type": entry.get("source_type"),
                    "needs_review": entry.get("needs_review"),
                    "notes": entry.get("notes"),
                    "inferred_from_photo_estimate": entry.get("inferred_from_photo_estimate") is True,
                    "inference_basis": str(entry.get("inference_basis") or "").strip(),
                }
            )
            continue

        if entry_type == "material_yards":
            yards = _to_decimal_number(entry.get("yards"))
            if yards is None or yards <= 0:
                continue
            key = ("material_yards", str(yards))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            normalized_entries.append(
                {
                    "entry_type": "material_yards",
                    "raw_text": str(entry.get("raw_text") or f"{yards} yds").strip(),
                    "yards": float(yards),
                    "confidence": float(_to_decimal_number(entry.get("confidence")) or Decimal("0.9")),
                    "source_images": list(entry.get("source_images") or source_images)[:5],
                    "source_asset_id": entry.get("source_asset_id"),
                    "source_filename": entry.get("source_filename"),
                    "source_type": entry.get("source_type"),
                    "needs_review": entry.get("needs_review"),
                    "notes": entry.get("notes"),
                }
            )

    return normalized_entries


def attach_measurement_entry_sources(
    entries: Iterable[dict],
    *,
    source_asset_map: Optional[dict[str, dict]] = None,
) -> list[dict]:
    source_asset_map = source_asset_map or {}
    annotated_entries: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        annotated = dict(entry)
        source_images = list(annotated.get("source_images") or [])
        if source_images:
            for source_name in source_images:
                source_meta = source_asset_map.get(str(source_name or "").strip())
                if not source_meta:
                    continue
                if not str(annotated.get("source_asset_id") or "").strip():
                    annotated["source_asset_id"] = source_meta.get("source_asset_id")
                if not str(annotated.get("source_filename") or "").strip():
                    annotated["source_filename"] = source_meta.get("source_filename")
                if not str(annotated.get("source_type") or "").strip():
                    annotated["source_type"] = source_meta.get("source_type")
                break
        annotated_entries.append(annotated)
    return annotated_entries


def _bed_groups_from_measurement_entries(entries: list[dict], source_images: list[str]) -> tuple[list[dict], Optional[Decimal], Optional[Decimal]]:
    dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
    if not dimension_entries:
        return [], None, None
    defaults = measurement_defaults()
    depth_inches = _to_decimal_number(defaults.get("material_depth_inches")) or Decimal("2.0")

    bed_groups: list[dict] = []
    total_area = Decimal("0")
    total_material = Decimal("0")

    for idx, entry in enumerate(dimension_entries, start=1):
        length_ft = _to_decimal_number(entry.get("length_ft"))
        width_ft = _to_decimal_number(entry.get("width_ft"))
        area_sqft = _to_decimal_number(entry.get("estimated_area_sqft"))
        material_yards = _to_decimal_number(entry.get("estimated_material_yards"))
        confidence = _to_decimal_number(entry.get("confidence")) or Decimal("0.82")
        if length_ft is None or width_ft is None or area_sqft is None:
            continue
        if material_yards is None:
            material_yards = (
                (area_sqft * (depth_inches / Decimal("12"))) / Decimal("27")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total_area += area_sqft
        total_material += material_yards
        bed_groups.append(
            {
                "bed_id": f"bed_{idx}",
                "zone_id": f"bed_{idx}",
                "zone_type": "flower_bed",
                "label": f"Bed {idx}",
                "image_count": 1,
                "source_images": (entry.get("source_images") or source_images or [])[:5],
                "source_entry": entry.get("raw_text"),
                "length_ft": _round_two(length_ft),
                "width_ft": _round_two(width_ft),
                "length_in": _round_two(length_ft * Decimal("12")),
                "width_in": _round_two(width_ft * Decimal("12")),
                "depth_in": _round_two(depth_inches),
                "estimated_area_sqft": _round_two(area_sqft),
                "estimated_material_yards": _round_two(material_yards),
                "confidence": float(min(confidence + Decimal("0.03"), Decimal("0.95"))),
            }
        )

    if not bed_groups:
        return [], None, None

    return (
        bed_groups,
        total_area.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        total_material.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


def measurement_entries_to_bed_groups(
    entries: Iterable[dict],
    source_images: Optional[list[str]] = None,
) -> tuple[list[dict], Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    normalized_entries = normalize_measurement_entries(entries, source_images=source_images)
    bed_groups, total_area, total_material = _bed_groups_from_measurement_entries(normalized_entries, source_images or [])
    explicit_material_yards = Decimal("0")
    explicit_count = 0
    for entry in normalized_entries:
        if entry.get("entry_type") != "material_yards":
            continue
        yards = _to_decimal_number(entry.get("yards"))
        if yards is None or yards <= 0:
            continue
        explicit_material_yards += yards
        explicit_count += 1
    explicit_total = explicit_material_yards.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if explicit_count else None
    return bed_groups, total_area, total_material, explicit_total


def _dimension_observations(values: list[Decimal], source: str, source_images: list[str]) -> Optional[dict]:
    if not values:
        return None

    ordered = sorted(values)
    length_in = ordered[-1]
    width_in: Optional[Decimal] = None
    depth_in: Optional[Decimal] = None

    remaining = ordered[:-1]
    if remaining:
        # Prefer realistic bed widths for material calculations.
        width_candidates = [value for value in remaining if Decimal("1.0") <= value <= Decimal("48.0")]
        if width_candidates:
            width_in = width_candidates[-1]
            others = [value for value in width_candidates[:-1] if Decimal("1.0") <= value <= Decimal("24.0")]
            if others:
                depth_in = others[-1]
        else:
            width_in = remaining[-1]

    area_sqft = None
    material_yards = None
    depth_inches = _to_decimal_number(measurement_defaults().get("material_depth_inches")) or Decimal("2.0")
    if width_in is not None:
        area_sqft = (length_in * width_in) / Decimal("144")
        material_yards = (area_sqft * (depth_inches / Decimal("12"))) / Decimal("27")
        area_sqft = area_sqft.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        material_yards = material_yards.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    confidence = Decimal("0.52")
    if len(ordered) >= 2:
        confidence += Decimal("0.12")
    if len(ordered) >= 3:
        confidence += Decimal("0.08")
    if source == "vision_ocr":
        confidence += Decimal("0.10")
    if any((value % 1) != 0 for value in ordered):
        confidence += Decimal("0.05")
    confidence = min(confidence, Decimal("0.92"))

    return {
        "source": source,
        "source_images": source_images[:5],
        "measurements_in": [float(value) for value in ordered],
        "length_in": float(length_in),
        "width_in": float(width_in) if width_in is not None else None,
        "depth_in": float(depth_in if depth_in is not None else depth_inches),
        "estimated_area_sqft": float(area_sqft) if area_sqft is not None else None,
        "estimated_material_yards": float(material_yards) if material_yards is not None else None,
        "confidence": float(confidence),
    }


def _median_decimal(values: list[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def _to_decimal_number(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _round_two(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _is_record_bed_candidate(record: dict) -> bool:
    tokens = record.get("tokens") or set()
    observation = record.get("observation") or {}
    has_bed_tokens = bool(tokens.intersection(FLOWER_BED_KEYWORDS))
    has_dimensions = _to_decimal_number(observation.get("length_in")) is not None or _to_decimal_number(
        observation.get("width_in")
    ) is not None
    return has_bed_tokens or has_dimensions


def _group_ref_dimensions(group: dict) -> tuple[Optional[Decimal], Optional[Decimal]]:
    return _median_decimal(group["lengths"]), _median_decimal(group["widths"])


def _is_same_group(group: dict, length: Optional[Decimal], width: Optional[Decimal]) -> bool:
    ref_length, ref_width = _group_ref_dimensions(group)
    checks = []
    if length is not None and ref_length is not None:
        tol = max(Decimal("3.0"), ref_length * Decimal("0.20"))
        checks.append(abs(length - ref_length) <= tol)
    if width is not None and ref_width is not None:
        tol = max(Decimal("1.5"), ref_width * Decimal("0.30"))
        checks.append(abs(width - ref_width) <= tol)
    if not checks:
        return False
    return all(checks)


def _group_distance_score(group: dict, length: Optional[Decimal], width: Optional[Decimal]) -> Decimal:
    ref_length, ref_width = _group_ref_dimensions(group)
    score = Decimal("0")
    if length is not None and ref_length is not None and ref_length > 0:
        score += abs(length - ref_length) / ref_length
    if width is not None and ref_width is not None and ref_width > 0:
        score += abs(width - ref_width) / ref_width
    return score


def _append_record_to_group(group: dict, record: dict) -> None:
    obs = record.get("observation") or {}
    length_in = _to_decimal_number(obs.get("length_in"))
    width_in = _to_decimal_number(obs.get("width_in"))
    depth_in = _to_decimal_number(obs.get("depth_in"))
    area_sqft = _to_decimal_number(obs.get("estimated_area_sqft"))
    material_yards = _to_decimal_number(obs.get("estimated_material_yards"))
    confidence = _to_decimal_number(obs.get("confidence"))

    if length_in is not None:
        group["lengths"].append(length_in)
    if width_in is not None:
        group["widths"].append(width_in)
    if depth_in is not None:
        group["depths"].append(depth_in)
    if area_sqft is not None and area_sqft > 0:
        group["areas"].append(area_sqft)
    if material_yards is not None and material_yards > 0:
        group["materials"].append(material_yards)
    if confidence is not None and confidence > 0:
        group["confidences"].append(confidence)

    group["records"].append(record)
    group["tokens"].update(record.get("tokens") or set())
    source_name = str(record.get("source_name") or "").strip()
    if source_name and source_name not in group["source_images"]:
        group["source_images"].append(source_name)


def _create_empty_group() -> dict:
    return {
        "records": [],
        "tokens": set(),
        "lengths": [],
        "widths": [],
        "depths": [],
        "areas": [],
        "materials": [],
        "confidences": [],
        "source_images": [],
    }


def _build_bed_groups(image_records: list[dict], note_tokens: set[str]) -> tuple[list[dict], Optional[Decimal], Optional[Decimal]]:
    defaults = measurement_defaults()
    depth_inches = _to_decimal_number(defaults.get("material_depth_inches")) or Decimal("2.0")
    measured_candidates: list[dict] = []
    token_only_candidates: list[dict] = []
    for record in image_records:
        if not _is_record_bed_candidate(record):
            continue
        obs = record.get("observation") or {}
        has_observed_dims = _to_decimal_number(obs.get("length_in")) is not None or _to_decimal_number(
            obs.get("width_in")
        ) is not None
        if has_observed_dims:
            measured_candidates.append(record)
        else:
            token_only_candidates.append(record)

    groups: list[dict] = []
    for record in measured_candidates:
        obs = record.get("observation") or {}
        length = _to_decimal_number(obs.get("length_in"))
        width = _to_decimal_number(obs.get("width_in"))
        best_idx = None
        best_score = None
        for idx, group in enumerate(groups):
            if not _is_same_group(group, length, width):
                continue
            score = _group_distance_score(group, length, width)
            if best_score is None or score < best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:
            groups.append(_create_empty_group())
            best_idx = len(groups) - 1
        _append_record_to_group(groups[best_idx], record)

    if groups:
        for record in token_only_candidates:
            tokens = record.get("tokens") or set()
            best_idx = 0
            best_overlap = -1
            for idx, group in enumerate(groups):
                overlap = len(tokens.intersection(group["tokens"]))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_idx = idx
            _append_record_to_group(groups[best_idx], record)
    else:
        fallback_count = len(token_only_candidates)
        if fallback_count == 0 and note_tokens.intersection(FLOWER_BED_KEYWORDS) and image_records:
            fallback_count = 1
        fallback_count = min(max(fallback_count, 0), 3)
        for idx in range(fallback_count):
            group = _create_empty_group()
            if idx < len(token_only_candidates):
                _append_record_to_group(group, token_only_candidates[idx])
            elif note_tokens.intersection(FLOWER_BED_KEYWORDS):
                for record in image_records[idx::max(fallback_count, 1)]:
                    _append_record_to_group(group, record)
            groups.append(group)

    def _group_sort_key(group: dict):
        area = _median_decimal(group["areas"]) or Decimal("0")
        length = _median_decimal(group["lengths"]) or Decimal("0")
        return (area, length, Decimal(len(group["records"])))

    groups.sort(key=_group_sort_key, reverse=True)

    bed_groups: list[dict] = []
    total_area = Decimal("0")
    total_material = Decimal("0")
    area_count = 0
    material_count = 0

    for idx, group in enumerate(groups, start=1):
        length_in = _median_decimal(group["lengths"])
        width_in = _median_decimal(group["widths"])
        depth_in = _median_decimal(group["depths"])
        area_sqft = _median_decimal(group["areas"])
        material_yards = _median_decimal(group["materials"])

        if area_sqft is None and length_in is not None and width_in is not None:
            area_sqft = ((length_in * width_in) / Decimal("144")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if material_yards is None and area_sqft is not None and area_sqft > 0:
            material_yards = (
                (area_sqft * (depth_inches / Decimal("12"))) / Decimal("27")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if area_sqft is not None and area_sqft > 0:
            total_area += area_sqft
            area_count += 1
        if material_yards is not None and material_yards > 0:
            total_material += material_yards
            material_count += 1

        if group["confidences"]:
            confidence = sum(group["confidences"]) / Decimal(len(group["confidences"]))
        else:
            confidence = Decimal("0.56")
        confidence += min(Decimal("0.08"), Decimal(len(group["records"])) * Decimal("0.02"))
        confidence = min(confidence, Decimal("0.92"))

        bed_groups.append(
            {
                "bed_id": f"bed_{idx}",
                "zone_id": f"bed_{idx}",
                "zone_type": "flower_bed",
                "label": f"Bed {idx}",
                "image_count": len(group["records"]),
                "source_images": group["source_images"][:5],
                "length_in": _round_two(length_in),
                "width_in": _round_two(width_in),
                "depth_in": _round_two(depth_in),
                "estimated_area_sqft": _round_two(area_sqft),
                "estimated_material_yards": _round_two(material_yards),
                "confidence": float(confidence.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            }
        )

    total_area_value = (
        total_area.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if area_count > 0 else None
    )
    total_material_value = (
        total_material.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if material_count > 0 else None
    )
    return bed_groups, total_area_value, total_material_value


def _detect_zones(
    image_tokens: list[tuple[str, set[str]]],
    note_tokens: set[str],
    bed_groups: Optional[list[dict]] = None,
) -> tuple[list[dict], str]:
    zones: list[dict] = []
    bed_groups = bed_groups or []
    bed_hits = 0
    yard_hit = False

    for _, tokens in image_tokens:
        if tokens.intersection(FLOWER_BED_KEYWORDS):
            bed_hits += 1
        if tokens.intersection(YARD_KEYWORDS):
            yard_hit = True

    if note_tokens.intersection(FLOWER_BED_KEYWORDS):
        bed_hits = max(bed_hits, 1)
    if note_tokens.intersection(YARD_KEYWORDS):
        yard_hit = True

    if bed_hits == 0 and len(image_tokens) >= 2 and not yard_hit:
        # Heuristic fallback for multi-bed uploads with no explicit words.
        bed_hits = min(2, len(image_tokens))

    if bed_groups:
        for bed in bed_groups:
            zone = {
                "zone_id": bed.get("zone_id") or bed.get("bed_id") or f"bed_{len(zones) + 1}",
                "zone_type": "flower_bed",
                "label": str(bed.get("label") or f"Bed {len(zones) + 1}"),
                "confidence": bed.get("confidence", 0.62),
            }
            if bed.get("estimated_area_sqft") is not None:
                zone["estimated_area_sqft"] = bed.get("estimated_area_sqft")
            if bed.get("estimated_material_yards") is not None:
                zone["estimated_material_yards"] = bed.get("estimated_material_yards")
            zones.append(zone)
    else:
        for idx in range(bed_hits):
            zones.append(
                {
                    "zone_id": f"bed_{idx + 1}",
                    "zone_type": "flower_bed",
                    "label": f"Bed {idx + 1}",
                    "confidence": 0.72 if idx < len(image_tokens) else 0.56,
                }
            )

    if yard_hit:
        zones.append(
            {
                "zone_id": "yard",
                "zone_type": "yard",
                "label": "Yard",
                "confidence": 0.74,
            }
        )

    if not zones and image_tokens:
        zones.append(
            {
                "zone_id": "yard",
                "zone_type": "yard",
                "label": "Yard",
                "confidence": 0.46,
            }
        )

    summary = ", ".join(zone["label"] for zone in zones)
    return zones, summary


def _decode_image_to_path(image: dict, temp_dir: Path, index: int) -> Optional[Path]:
    storage_path = str(image.get("storage_path") or "").strip()
    if storage_path:
        name = str(image.get("file_name") or f"upload_{index}.bin")
        path = storage.ensure_local_path(storage_path, temp_dir, file_name=name)
        if path is not None:
            return path

    encoded = image.get("data_base64")
    if not encoded:
        return None

    try:
        raw = base64.b64decode(str(encoded), validate=False)
    except Exception:
        return None

    if not raw:
        return None

    name = str(image.get("file_name") or f"upload_{index}.bin")
    suffix = Path(name).suffix or ".bin"
    target = temp_dir / f"media_{index:02d}{suffix}"
    try:
        target.write_bytes(raw)
        return target
    except Exception:
        return None


def _vision_ocr_text(paths: list[Path]) -> dict[str, str]:
    if not paths:
        return {}
    if os.name != "posix":
        return {}

    swift = shutil.which("swift")
    if not swift:
        return {}

    results: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="bb-swift-ocr-") as run_dir:
        run_path = Path(run_dir)
        script_path = run_path / "vision_ocr.swift"
        module_cache = run_path / "module-cache"
        build_dir = run_path / "build-cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        script_path.write_text(SWIFT_OCR_SCRIPT, encoding="utf-8")

        env = os.environ.copy()
        env["SWIFT_MODULE_CACHE_PATH"] = str(module_cache)
        env["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
        env["SWIFT_BUILD_DIR"] = str(build_dir)

        cmd = [swift, str(script_path), *[str(path) for path in paths]]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(20, 6 * len(paths)),
                env=env,
            )
        except Exception:
            return {}

        if proc.returncode != 0 and not proc.stdout:
            return {}

        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = str(payload.get("path") or "")
            text = str(payload.get("text") or "")
            if path and text:
                results[path] = text
    return results


def _image_to_data_url(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    suffix = path.suffix.lower()
    mime = "image/jpeg"
    if suffix == ".png":
        mime = "image/png"
    elif suffix == ".webp":
        mime = "image/webp"
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _extract_json_object(text: str) -> Optional[dict]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_openai_payload_object(response_obj: dict) -> Optional[dict]:
    parsed = response_obj.get("output_parsed")
    if isinstance(parsed, dict):
        return parsed

    output_text = response_obj.get("output_text")
    if isinstance(output_text, str):
        payload_obj = _extract_json_object(output_text)
        if payload_obj:
            return payload_obj

    texts: list[str] = []
    for item in response_obj.get("output", []) or []:
        for content in item.get("content", []) or []:
            payload_obj = content.get("json")
            if isinstance(payload_obj, dict):
                return payload_obj
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)

    return _extract_json_object("\n".join(texts))


def _sanitized_openai_payload(payload: dict) -> dict:
    def _sanitize(value):
        if isinstance(value, dict):
            sanitized = {}
            for key, inner in value.items():
                if key == "image_url" and isinstance(inner, str):
                    sanitized[key] = f"<redacted image_url len={len(inner)}>"
                else:
                    sanitized[key] = _sanitize(inner)
            return sanitized
        if isinstance(value, list):
            return [_sanitize(item) for item in value]
        return value

    return _sanitize(payload)


def _normalize_number_for_json(value: Decimal) -> int | float:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if normalized == normalized.to_integral_value():
        return int(normalized)
    return float(normalized)


def _normalize_handwritten_parse_rows(payload_obj: dict) -> tuple[list[dict], Optional[str]]:
    raw_rows = payload_obj.get("rows")
    if not isinstance(raw_rows, list):
        return [], "openai_missing_rows"

    rows: list[dict] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        length = _to_decimal_number(item.get("length"))
        width = _to_decimal_number(item.get("width"))
        if length is None or width is None:
            continue
        if length <= 0 or width <= 0:
            continue

        length_value = _normalize_number_for_json(length)
        width_value = _normalize_number_for_json(width)
        raw_text = str(item.get("raw") or f"{length_value}x{width_value}").strip()
        rows.append(
            {
                "raw": raw_text or f"{length_value}x{width_value}",
                "length": length_value,
                "width": width_value,
            }
        )

    return rows, None


def _openai_http_error_reason(status_code: int) -> str:
    if status_code in {401, 403}:
        return "openai_auth_failed"
    if status_code == 404:
        return "openai_model_unavailable"
    if status_code == 429:
        return "openai_rate_limited"
    return "openai_http_error"


def _content_type_for_inline_image(content_type: Optional[str], source_name: str) -> str:
    normalized = str(content_type or "").strip().lower()
    if normalized.startswith("image/"):
        return normalized
    suffix = Path(str(source_name or "")).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    if suffix in {".heic", ".heif"}:
        return "image/heic"
    return "image/jpeg"


def _inline_image_url_from_bytes(raw: bytes, content_type: Optional[str], source_name: str) -> Optional[str]:
    if not raw:
        return None
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:{_content_type_for_inline_image(content_type, source_name)};base64,{encoded}"


def _handwritten_measurement_parse_payload(
    *,
    image_content: dict,
    model: str,
    prompt: str,
) -> dict:
    return {
        "model": model,
        "store": False,
        "max_output_tokens": 1200,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    image_content,
                ],
            }
        ],
        "text": {
            "format": HANDWRITTEN_MEASUREMENT_PARSE_SCHEMA,
        },
    }


def _openai_error_info(
    *,
    code: str,
    error_type: str = "",
    message: str = "",
    details: str = "",
) -> dict[str, str]:
    return {
        "code": str(code or "").strip(),
        "error_type": str(error_type or code or "").strip(),
        "message": str(message or code or "").strip(),
        "details": str(details or "").strip(),
    }


def _coerce_openai_result(result) -> tuple[list[dict], Optional[str], Optional[dict]]:
    if isinstance(result, tuple):
        if len(result) >= 3:
            return result[0], result[1], result[2]
        if len(result) == 2:
            return result[0], result[1], None
    return [], "openai_request_failed", _openai_error_info(code="openai_request_failed")


def _openai_responses_payload_via_sdk(
    *,
    payload: dict,
    parser_branch: str,
    log_reference: str,
) -> tuple[Optional[dict], Optional[dict], Optional[str]]:
    api_key = runtime_openai_api_key()
    model = str(payload.get("model") or "").strip()
    logger.info(
        "measurement_parse_sdk model=%s sdk_path=%s parser_branch=%s exception=%s",
        model,
        "official_openai_sdk",
        parser_branch,
        "",
    )
    if not api_key:
        return None, _openai_error_info(code="openai_not_configured"), None

    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
    except Exception as exc:
        logger.exception(
            "measurement_parse_sdk model=%s sdk_path=%s parser_branch=%s exception=%s",
            model,
            "official_openai_sdk",
            parser_branch,
            str(exc),
        )
        return None, _openai_error_info(
            code="openai_sdk_not_installed",
            error_type=exc.__class__.__name__,
            message=str(exc),
        ), None

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(**payload)
        raw = response.model_dump_json()
        logger.info(
            "measurement_parse_sdk_response parser_branch=%s reference=%s raw=%s",
            parser_branch,
            log_reference,
            raw,
        )
        return response.model_dump(), None, raw
    except APITimeoutError as exc:
        logger.warning(
            "measurement_parse_sdk_timeout model=%s sdk_path=%s parser_branch=%s exception=%s",
            model,
            "official_openai_sdk",
            parser_branch,
            str(exc),
        )
        return None, _openai_error_info(
            code="openai_request_timed_out",
            error_type=exc.__class__.__name__,
            message=str(exc),
        ), None
    except APIConnectionError as exc:
        reason = _classify_openai_network_error(getattr(exc, "__cause__", None) or exc)
        detail_text = str(getattr(exc, "__cause__", None) or exc)
        logger.warning(
            "measurement_parse_sdk_connection_failure model=%s sdk_path=%s parser_branch=%s reason=%s exception=%s",
            model,
            "official_openai_sdk",
            parser_branch,
            reason,
            detail_text,
        )
        return None, _openai_error_info(
            code=reason,
            error_type=exc.__class__.__name__,
            message=detail_text,
            details=detail_text,
        ), None
    except APIStatusError as exc:
        response_text = ""
        response_obj = getattr(exc, "response", None)
        if response_obj is not None:
            try:
                response_text = getattr(response_obj, "text", "") or ""
            except Exception:
                response_text = ""
        logger.warning(
            "measurement_parse_sdk_http_failure model=%s sdk_path=%s parser_branch=%s status=%s response=%s",
            model,
            "official_openai_sdk",
            parser_branch,
            getattr(exc, "status_code", None),
            response_text,
        )
        return None, _openai_error_info(
            code=_openai_http_error_reason(int(getattr(exc, "status_code", 0) or 0)),
            error_type=exc.__class__.__name__,
            message=str(exc),
            details=response_text,
        ), None
    except Exception as exc:
        logger.exception(
            "measurement_parse_sdk model=%s sdk_path=%s parser_branch=%s exception=%s",
            model,
            "official_openai_sdk",
            parser_branch,
            str(exc),
        )
        return None, _openai_error_info(
            code="openai_request_failed",
            error_type=exc.__class__.__name__,
            message=str(exc),
        ), None


def _handwritten_measurement_rows_from_openai_payload(
    *,
    payload: dict,
    log_reference: str,
) -> tuple[list[dict], Optional[str], Optional[dict]]:
    api_key = runtime_openai_api_key()
    logger.info(
        "measurement_parse_request has_openai_key=%s parser_branch=%s",
        bool(api_key),
        "handwritten_measurement",
    )
    logger.info(
        "measurement_parse_request_start parser_branch=%s reference=%s model=%s payload=%s",
        "handwritten_measurement",
        log_reference,
        str(payload.get("model") or "").strip(),
        _sanitized_openai_payload(payload),
    )
    if not api_key:
        return [], "openai_not_configured", _openai_error_info(code="openai_not_configured")
    response_obj, error_info, raw = _openai_responses_payload_via_sdk(
        payload=payload,
        parser_branch="handwritten_measurement",
        log_reference=log_reference,
    )
    if error_info:
        return [], str(error_info.get("code") or "openai_request_failed"), error_info

    payload_obj = _extract_openai_payload_object(response_obj or {})
    if not payload_obj and raw:
        payload_obj = _extract_json_object(raw)

    if not payload_obj:
        logger.warning(
            "measurement_parse_payload_missing parser_branch=%s reference=%s raw=%s",
            "handwritten_measurement",
            log_reference,
            raw,
        )
        error_info = _openai_error_info(code="openai_missing_json", message="OpenAI returned no parseable JSON payload.")
        return [], "openai_missing_json", error_info

    rows, error = _normalize_handwritten_parse_rows(payload_obj)
    logger.info(
        "handwritten_parse_rows_count reference=%s rows=%s error=%s",
        log_reference,
        len(rows),
        error,
    )
    error_info = _openai_error_info(code=error, message=error) if error else None
    return rows, error, error_info


def parse_handwritten_measurement_rows_from_uploaded_image(
    *,
    image_bytes: bytes,
    source_name: str,
    content_type: Optional[str] = None,
    asset_reference: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    current_settings = refresh_settings()
    model = str(getattr(current_settings, "openai_vision_model", "gpt-4.1") or "gpt-4.1").strip() or "gpt-4.1"
    image_url = _inline_image_url_from_bytes(image_bytes, content_type, source_name)
    if not image_url:
        return [], "openai_invalid_response"

    payload = _handwritten_measurement_parse_payload(
        image_content={"type": "input_image", "image_url": image_url},
        model=model,
        prompt=HANDWRITTEN_MEASUREMENT_PARSE_PROMPT,
    )
    rows, error, _error_info = _handwritten_measurement_rows_from_openai_payload(
        payload=payload,
        log_reference=str(asset_reference or source_name or "uploaded_image").strip(),
    )
    if rows or error:
        return rows, error
    retry_payload = _handwritten_measurement_parse_payload(
        image_content={"type": "input_image", "image_url": image_url},
        model=model,
        prompt=HANDWRITTEN_MEASUREMENT_PARSE_RETRY_PROMPT,
    )
    rows, error, _error_info = _handwritten_measurement_rows_from_openai_payload(
        payload=retry_payload,
        log_reference=f"{str(asset_reference or source_name or 'uploaded_image').strip()}:retry",
    )
    return rows, error


def parse_handwritten_measurement_rows_from_image_url(image_url: str) -> tuple[list[dict], Optional[str]]:
    current_settings = refresh_settings()
    api_key = runtime_openai_api_key()
    if not api_key:
        return [], "openai_not_configured"

    model = str(getattr(current_settings, "openai_vision_model", "gpt-4.1") or "gpt-4.1").strip() or "gpt-4.1"
    payload = _handwritten_measurement_parse_payload(
        image_content={"type": "input_image", "image_url": str(image_url or "").strip()},
        model=model,
        prompt=HANDWRITTEN_MEASUREMENT_PARSE_PROMPT,
    )
    rows, error, _error_info = _handwritten_measurement_rows_from_openai_payload(
        payload=payload,
        log_reference=str(image_url or "").strip(),
    )
    if rows or error:
        return rows, error
    retry_payload = _handwritten_measurement_parse_payload(
        image_content={"type": "input_image", "image_url": str(image_url or "").strip()},
        model=model,
        prompt=HANDWRITTEN_MEASUREMENT_PARSE_RETRY_PROMPT,
    )
    rows, error, _error_info = _handwritten_measurement_rows_from_openai_payload(
        payload=retry_payload,
        log_reference=f"{str(image_url or '').strip()}:retry",
    )
    return rows, error


def _normalize_openai_measurement_rows(payload_obj: dict, source_name: str) -> tuple[list[dict], Optional[str]]:
    raw_rows = payload_obj.get("rows")
    if isinstance(raw_rows, list):
        parsed_entries: list[dict] = []
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            length_ft = item.get("length")
            width_ft = item.get("width")
            if length_ft in (None, "") or width_ft in (None, ""):
                continue
            raw_text = str(item.get("raw") or item.get("raw_text") or f"{length_ft}x{width_ft}").strip()
            parsed_entries.append(
                {
                    "entry_type": "dimension_pair",
                    "raw_text": raw_text or f"{length_ft}x{width_ft}",
                    "length_ft": length_ft,
                    "width_ft": width_ft,
                    "confidence": item.get("confidence", 0.98),
                    "notes": item.get("notes"),
                    "source_images": [source_name, "openai_vision"],
                }
            )
        return normalize_measurement_entries(parsed_entries, source_images=[source_name]), None

    measurement_lines = payload_obj.get("measurement_lines")
    if not isinstance(measurement_lines, list):
        return [], "openai_missing_measurement_lines"

    parsed_entries: list[dict] = []
    for item in measurement_lines:
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get("raw_text") or "").strip()
        length_ft = item.get("length_ft")
        width_ft = item.get("width_ft")
        if length_ft in (None, "") or width_ft in (None, ""):
            continue
        parsed_entries.append(
            {
                "entry_type": "dimension_pair",
                "raw_text": raw_text or f"{length_ft}x{width_ft}",
                "length_ft": length_ft,
                "width_ft": width_ft,
                "confidence": item.get("confidence", 0.98),
                "notes": item.get("notes"),
                "source_images": [source_name, "openai_vision"],
            }
        )
    return normalize_measurement_entries(parsed_entries, source_images=[source_name]), None


def _vision_measurement_prompt(retry: bool = False) -> str:
    if retry:
        return """
Read this handwritten landscaping note again and extract ALL dimension pairs.
Be tolerant of messy handwriting.
Include unusual but plausible pairs like 15x230, 100x7, and 110x12.
Return JSON only in the same schema.
""".strip()
    return """
You are extracting handwritten landscaping measurements from a note photo.

Task:
Read the image and extract every handwritten measurement pair that represents dimensions in the format length x width.

Important:
- This is a BarkBoys landscaping worksheet note.
- The image may contain logos, printed text, business info, stray marks, and handwritten words.
- Ignore branding, printed footer text, addresses, phone numbers, and non-measurement notes unless they help interpret a measurement.
- Focus on handwritten numeric dimension pairs only.

Extraction rules:
1. Return only true dimension pairs written as two numbers that mean length x width.
2. Normalize all pairs into numeric fields:
   - "91x22" -> { "length": 91, "width": 22 }
   - "25 x 30" -> { "length": 25, "width": 30 }
3. Accept messy handwriting and imperfect spacing.
4. Accept large values if they appear real, such as:
   - 15x230
   - 100x7
   - 110x12
5. Do not discard a row just because it looks unusual.
6. Ignore standalone notes that are not dimension pairs, for example:
   - "Starbucks"
   - "4 yds"
   - isolated numbers without an x-pair
7. If a pair is ambiguous but still most likely a valid measurement, include it.
8. Preserve the approximate reading order from top to bottom, left to right.
9. Do not invent missing pairs.
10. Do not explain your reasoning.

Return JSON only in this exact shape:

{
  "rows": [
    {
      "raw": "91x22",
      "length": 91,
      "width": 22
    }
  ]
}
""".strip()


def _openai_response_measurement_entries(input_parts: list[dict], source_name: str) -> tuple[list[dict], Optional[str], Optional[dict]]:
    current_settings = refresh_settings()
    api_key = runtime_openai_api_key()
    logger.info(
        "measurement_parse_request has_openai_key=%s parser_branch=%s",
        bool(api_key),
        "worksheet_measurement",
    )
    if not api_key or not input_parts:
        return [], "openai_not_configured", _openai_error_info(code="openai_not_configured")
    model = str(getattr(current_settings, "openai_vision_model", "gpt-4.1") or "gpt-4.1").strip() or "gpt-4.1"

    payload = {
        "model": model,
        "store": False,
        "max_output_tokens": 1200,
        "input": [
            {
                "role": "user",
                "content": input_parts,
            }
        ],
        "text": {
            "format": HANDWRITTEN_MEASUREMENT_PARSE_SCHEMA,
        },
    }
    logger.info(
        "measurement_parse_request_start parser_branch=%s source=%s model=%s payload=%s",
        "worksheet_measurement",
        str(source_name or "").strip(),
        str(payload.get("model") or "").strip(),
        _sanitized_openai_payload(payload),
    )
    response_obj, error_info, raw = _openai_responses_payload_via_sdk(
        payload=payload,
        parser_branch="worksheet_measurement",
        log_reference=str(source_name or "").strip(),
    )
    if error_info:
        return [], str(error_info.get("code") or "openai_request_failed"), error_info
    payload_obj = _extract_openai_payload_object(response_obj or {})
    if not payload_obj and raw:
        payload_obj = _extract_json_object(raw)
    if not payload_obj:
        error_info = _openai_error_info(code="openai_missing_json", message="OpenAI returned no parseable JSON payload.")
        return [], "openai_missing_json", error_info

    logger.info(
        "worksheet_measurement_payload_object source=%s payload=%s",
        str(source_name or "").strip(),
        payload_obj,
    )

    entries, error = _normalize_openai_measurement_rows(payload_obj, source_name)
    logger.info(
        "worksheet_measurement_rows_count source=%s rows=%s error=%s",
        str(source_name or "").strip(),
        len(entries),
        error,
    )
    error_info = _openai_error_info(code=error, message=error) if error else None
    return entries, error, error_info


def _openai_vision_measurement_entries(image_paths: list[Path], source_name: str) -> tuple[list[dict], Optional[str], Optional[dict]]:
    if not image_paths:
        return [], "openai_not_configured", _openai_error_info(code="openai_not_configured")

    def _input_parts(prompt_text: str) -> list[dict]:
        parts: list[dict] = [{"type": "input_text", "text": prompt_text}]
        for path in image_paths[:4]:
            data_url = _image_to_data_url(path)
            if data_url:
                parts.append({"type": "input_image", "image_url": data_url})
        return parts

    primary_entries, primary_error, primary_error_info = _openai_response_measurement_entries(
        _input_parts(_vision_measurement_prompt(False)),
        source_name,
    )
    if primary_entries:
        return primary_entries, None, None

    retry_entries, retry_error, retry_error_info = _openai_response_measurement_entries(
        _input_parts(_vision_measurement_prompt(True)),
        source_name,
    )
    if retry_entries:
        return retry_entries, None, None
    return [], retry_error or primary_error, retry_error_info or primary_error_info


def _openai_text_measurement_entries(ocr_text: str, source_name: str) -> tuple[list[dict], Optional[str], Optional[dict]]:
    cleaned = str(ocr_text or "").strip()
    if not cleaned:
        return [], "openai_missing_text", _openai_error_info(code="openai_missing_text")

    input_parts: list[dict] = [
        {
            "type": "input_text",
            "text": (
                "Extract all measurement pairs from this OCR text. "
                "Rules: format as length x width, include only numeric pairs, ignore words like yards, beds, and totals, "
                "normalize '91 x 22' to 91x22. "
                'Return JSON only: {"rows":[{"length":91,"width":22}]}'
            ),
        },
        {
            "type": "input_text",
            "text": cleaned,
        },
    ]

    return _openai_response_measurement_entries(input_parts, source_name)


def analyze_uploaded_images(
    uploaded_images: Iterable[dict],
    notes: Optional[str] = None,
    measurement_reference_images: Optional[Iterable[dict]] = None,
    allow_site_media_measurement_reference_detection: bool = True,
) -> dict:
    logger.info(
        "analyze_uploaded_images start uploaded_images=%s measurement_reference_images=%s allow_site_media_measurement_reference_detection=%s",
        [
            {
                "id": image.get("id"),
                "file_name": image.get("file_name"),
                "storage_path": image.get("storage_path"),
                "category": image.get("category"),
            }
            for image in (uploaded_images or [])
            if isinstance(image, dict)
        ],
        [
            {
                "id": image.get("id"),
                "file_name": image.get("file_name"),
                "storage_path": image.get("storage_path"),
                "category": image.get("category"),
            }
            for image in (measurement_reference_images or [])
            if isinstance(image, dict)
        ],
        allow_site_media_measurement_reference_detection,
    )
    current_settings = refresh_settings()
    allow_fallback_handwritten_measurement_ocr = bool(
        getattr(current_settings, "allow_fallback_handwritten_measurement_ocr", False)
    )
    evidence = {}
    combined_tokens = set()
    image_measurements: list[Decimal] = []
    image_measurement_sources: list[str] = []
    measurement_entries: list[dict] = []
    ocr_payloads: list[tuple[int, str, list[Path]]] = []
    measurement_reference_payloads: list[tuple[str, list[Path]]] = []
    text_from_ocr: list[str] = []
    measurement_parse_inputs: list[tuple[str, str, bool]] = []
    image_records: dict[int, dict] = {}
    extraction_meta = {
        "openai_configured": bool(runtime_openai_api_key()),
        "openai_used": False,
        "openai_error": None,
        "openai_error_message": "",
        "openai_error_type": "",
        "openai_error_details": "",
        "fallback_ocr_used": False,
        "trusted_measurements_available": False,
        "measurement_reference_images_present": False,
        "exact_measurement_parse_failed": False,
        "ocr_debug": [],
    }

    def _capture_openai_error(error_code: Optional[str], error_info: Optional[dict]) -> None:
        if error_code and extraction_meta.get("openai_error") is None:
            extraction_meta["openai_error"] = error_code
        if isinstance(error_info, dict):
            if not extraction_meta.get("openai_error_message"):
                extraction_meta["openai_error_message"] = str(error_info.get("message") or "").strip()
            if not extraction_meta.get("openai_error_type"):
                extraction_meta["openai_error_type"] = str(error_info.get("error_type") or "").strip()
            if not extraction_meta.get("openai_error_details"):
                extraction_meta["openai_error_details"] = str(error_info.get("details") or "").strip()
    source_asset_map = _source_asset_map(uploaded_images or [], measurement_reference_images or [])

    with tempfile.TemporaryDirectory(prefix="bb-media-ocr-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        for idx, image in enumerate(uploaded_images or []):
            file_name = str(image.get("file_name") or "")
            tokens = _tokenize(file_name)
            source_name = file_name or f"image_{idx + 1}"
            image_records[idx] = {
                "source_name": source_name,
                "tokens": set(tokens),
                "measurements": [],
                "has_ocr": False,
                "observation": None,
            }
            combined_tokens.update(tokens)
            for job_type, config in TASK_PATTERNS.items():
                matched = sorted(tokens.intersection(config["keywords"]))
                if matched:
                    bucket = evidence.setdefault(job_type, set())
                    bucket.update(matched)

            name_measurements = _extract_measurements_in(file_name)
            if name_measurements:
                image_measurements.extend(name_measurements)
                image_measurement_sources.append(source_name)
                image_records[idx]["measurements"].extend(name_measurements)

            image_path = _decode_image_to_path(image, temp_dir, idx)
            if image_path is not None:
                candidate_paths = _ocr_candidate_paths(image_path, temp_dir)
                logger.info(
                    "analyze_uploaded_images parser_invoked source=%s decoded_path=%s candidates=%s",
                    source_name,
                    str(image_path),
                    [str(path) for path in candidate_paths[:4]],
                )
                model_entries, model_error, model_error_info = _coerce_openai_result(
                    _openai_vision_measurement_entries(
                        candidate_paths[:4],
                        source_name or image_path.name,
                    )
                )
                logger.info(
                    "analyze_uploaded_images parser_result source=%s model_entries=%s model_error=%s",
                    source_name,
                    model_entries,
                    model_error,
                )
                if model_entries:
                    measurement_entries.extend(model_entries)
                    extraction_meta["openai_used"] = True
                elif model_error:
                    _capture_openai_error(model_error, model_error_info)
                ocr_payloads.append((idx, source_name or image_path.name, candidate_paths))

        for idx, image in enumerate(measurement_reference_images or [], start=1000):
            file_name = str(image.get("file_name") or "")
            source_name = file_name or f"measurement_reference_{idx - 999}"
            image_path = _decode_image_to_path(image, temp_dir, idx)
            if image_path is None:
                continue
            candidate_paths = _ocr_candidate_paths(image_path, temp_dir)
            model_entries, model_error, model_error_info = _coerce_openai_result(
                _openai_vision_measurement_entries(
                    candidate_paths[:4],
                    source_name or image_path.name,
                )
            )
            if model_entries:
                measurement_entries.extend(model_entries)
                extraction_meta["openai_used"] = True
            elif model_error:
                _capture_openai_error(model_error, model_error_info)
            extraction_meta["measurement_reference_images_present"] = True
            measurement_reference_payloads.append((source_name or image_path.name, candidate_paths))

        ocr_text_by_path = _vision_ocr_text(
            [path for _, _, paths in ocr_payloads for path in paths]
            + [path for _, paths in measurement_reference_payloads for path in paths]
        )
        for idx, source_name, paths in ocr_payloads:
            candidate_texts = [ocr_text_by_path.get(str(path), "") for path in paths]
            text = _select_best_ocr_text(candidate_texts)
            if not text:
                continue
            logger.info(
                "analyze_uploaded_images ocr_result source=%s preview=%s",
                source_name,
                _ocr_preview_excerpt(text),
            )
            treat_as_measurement_reference = bool(
                allow_site_media_measurement_reference_detection
                and _should_treat_as_measurement_reference(source_name, text)
            )
            if treat_as_measurement_reference:
                extraction_meta["measurement_reference_images_present"] = True
            else:
                text_from_ocr.append(text)
                tokens = _tokenize(text)
                if idx in image_records:
                    image_records[idx]["tokens"].update(tokens)
                    image_records[idx]["has_ocr"] = True
                combined_tokens.update(tokens)
                for job_type, config in TASK_PATTERNS.items():
                    matched = sorted(tokens.intersection(config["keywords"]))
                    if matched:
                        bucket = evidence.setdefault(job_type, set())
                        bucket.update(matched)

                measurements = _extract_measurements_in(text)
                if measurements:
                    image_measurements.extend(measurements)
                    image_measurement_sources.append(source_name)
                    if idx in image_records:
                        image_records[idx]["measurements"].extend(measurements)
            measurement_parse_inputs.append((source_name, text, treat_as_measurement_reference))
            extraction_meta["ocr_debug"].append(
                {
                    "source_name": source_name,
                    "preview_text": _ocr_preview_excerpt(text),
                    "measurement_reference_mode": treat_as_measurement_reference,
                    "dimension_row_candidates": _measurement_text_score(text)[0],
                }
            )
            if text.strip():
                extraction_meta["fallback_ocr_used"] = True
            best_text_score = _measurement_text_score(text)
            existing_dimension_count = sum(
                1
                for entry in measurement_entries
                if str((entry or {}).get("entry_type") or "").strip().lower() == "dimension_pair"
                and source_name in list((entry or {}).get("source_images") or [])
            )
            min_dimension_rows = 1 if treat_as_measurement_reference else 2
            if best_text_score[0] >= min_dimension_rows and existing_dimension_count < best_text_score[0]:
                text_model_entries, text_model_error, text_model_error_info = _coerce_openai_result(
                    _openai_text_measurement_entries(text, source_name)
                )
                if text_model_entries:
                    measurement_entries.extend(text_model_entries)
                    extraction_meta["openai_used"] = True
                elif text_model_error:
                    _capture_openai_error(text_model_error, text_model_error_info)
            recovered_entries = _merge_ocr_measurement_entries(candidate_texts, [source_name])
            recovered_dimension_count = sum(
                1 for entry in recovered_entries if str((entry or {}).get("entry_type") or "").strip().lower() == "dimension_pair"
            )
            min_recovered_rows = 1 if treat_as_measurement_reference else 2
            if allow_fallback_handwritten_measurement_ocr or recovered_dimension_count >= min_recovered_rows:
                measurement_entries.extend(recovered_entries)

        for source_name, paths in measurement_reference_payloads:
            candidate_texts = [ocr_text_by_path.get(str(path), "") for path in paths]
            text = _select_best_ocr_text(candidate_texts)
            if not text:
                continue
            measurement_parse_inputs.append((source_name, text, True))
            if text.strip():
                extraction_meta["fallback_ocr_used"] = True
            extraction_meta["ocr_debug"].append(
                {
                    "source_name": source_name,
                    "preview_text": _ocr_preview_excerpt(text),
                    "measurement_reference_mode": True,
                    "dimension_row_candidates": _measurement_text_score(text)[0],
                }
            )
            best_text_score = _measurement_text_score(text)
            existing_dimension_count = sum(
                1
                for entry in measurement_entries
                if str((entry or {}).get("entry_type") or "").strip().lower() == "dimension_pair"
                and source_name in list((entry or {}).get("source_images") or [])
            )
            if best_text_score[0] >= 1 and existing_dimension_count < best_text_score[0]:
                text_model_entries, text_model_error, text_model_error_info = _coerce_openai_result(
                    _openai_text_measurement_entries(text, source_name)
                )
                if text_model_entries:
                    measurement_entries.extend(text_model_entries)
                    extraction_meta["openai_used"] = True
                elif text_model_error:
                    _capture_openai_error(text_model_error, text_model_error_info)
            recovered_entries = _merge_ocr_measurement_entries(candidate_texts, [source_name])
            recovered_dimension_count = sum(
                1 for entry in recovered_entries if str((entry or {}).get("entry_type") or "").strip().lower() == "dimension_pair"
            )
            if allow_fallback_handwritten_measurement_ocr or recovered_dimension_count >= 1:
                measurement_entries.extend(recovered_entries)

    note_tokens = _tokenize(notes)
    combined_tokens.update(note_tokens)
    for job_type, config in TASK_PATTERNS.items():
        matched = sorted(note_tokens.intersection(config["keywords"]))
        if matched:
            bucket = evidence.setdefault(job_type, set())
            bucket.update(matched)

    detected_tasks = []
    for job_type, config in TASK_PATTERNS.items():
        matched = sorted(evidence.get(job_type, set()))
        if not matched:
            continue
        confidence = min(0.58 + (len(matched) * 0.1), 0.94)
        detected_tasks.append(
            {
                "job_type": job_type,
                "label": config["label"],
                "confidence": round(confidence, 2),
                "evidence": matched,
            }
        )

    if not detected_tasks:
        detected_tasks = [
            {
                "job_type": "general_cleanup",
                "label": "General Cleanup",
                "confidence": 0.42 if combined_tokens else 0.35,
                "evidence": sorted(list(combined_tokens))[:4],
            }
        ]

    detected_tasks.sort(key=lambda item: item["confidence"], reverse=True)
    primary = detected_tasks[0]["job_type"]
    dimension_observations = _dimension_observations(
        values=image_measurements,
        source="vision_ocr" if text_from_ocr else "file_metadata",
        source_images=image_measurement_sources,
    )
    ordered_image_records: list[dict] = []
    for idx in sorted(image_records):
        record = image_records[idx]
        unique_measurements = sorted(set(record.get("measurements") or []))
        if unique_measurements:
            record["observation"] = _dimension_observations(
                values=unique_measurements,
                source="vision_ocr" if record.get("has_ocr") else "file_metadata",
                source_images=[record.get("source_name") or f"image_{idx + 1}"],
            )
        ordered_image_records.append(record)

    note_measurement_entries = _extract_measurement_entries(str(notes or ""), [])
    if note_measurement_entries:
        measurement_entries.extend(note_measurement_entries)

    measurement_entries = attach_measurement_entry_sources(
        measurement_entries,
        source_asset_map=source_asset_map,
    )

    deduped_measurement_entries: list[dict] = []
    seen_measurement_keys: set[tuple] = set()
    for entry in measurement_entries:
        if entry.get("entry_type") == "dimension_pair":
            key = (
                entry.get("entry_type"),
                entry.get("length_ft"),
                entry.get("width_ft"),
            )
        else:
            key = (
                entry.get("entry_type"),
                entry.get("yards"),
            )
        if key in seen_measurement_keys:
            continue
        seen_measurement_keys.add(key)
        deduped_measurement_entries.append(entry)
    measurement_entries = deduped_measurement_entries
    extraction_meta["trusted_measurements_available"] = bool(measurement_entries)
    if extraction_meta["measurement_reference_images_present"] and not extraction_meta["trusted_measurements_available"]:
        extraction_meta["exact_measurement_parse_failed"] = True

    source_names = sorted(
        {
            str(image.get("file_name") or "").strip()
            for image in (uploaded_images or [])
            if str(image.get("file_name") or "").strip()
        }
        | {
            str(image.get("file_name") or "").strip()
            for image in (measurement_reference_images or [])
            if str(image.get("file_name") or "").strip()
        }
    )
    if not measurement_parse_inputs and source_names:
        for source_name in source_names:
            measurement_parse_inputs.append((source_name, "", extraction_meta["measurement_reference_images_present"]))

    parse_unclear_rows: list[dict] = []
    parse_classification = "scene_photo_estimation"
    should_use_geometry_fallback = True
    if measurement_parse_inputs:
        parse_results = [
            _measurement_parse_result(
                text,
                source_name,
                [
                    entry for entry in measurement_entries
                    if source_name in list((entry or {}).get("source_images") or [])
                ],
            )
            for source_name, text, _is_reference_mode in measurement_parse_inputs
        ]
        parse_unclear_rows = [
            {**row, "source_name": result_source}
            for (result_source, _text, _mode), result in zip(measurement_parse_inputs, parse_results)
            for row in (result.get("unclear_rows") or [])
        ]
        if any(result.get("classification") == "exact_measurement_note" for result in parse_results):
            parse_classification = "exact_measurement_note"
            should_use_geometry_fallback = False
        elif any(result.get("classification") == "failed_ocr_unreadable_note" for result in parse_results):
            parse_classification = "failed_ocr_unreadable_note"
            should_use_geometry_fallback = False
        else:
            parse_classification = "scene_photo_estimation"
            should_use_geometry_fallback = True
    elif extraction_meta["measurement_reference_images_present"]:
        parse_classification = "failed_ocr_unreadable_note"
        should_use_geometry_fallback = False

    total_square_feet = Decimal("0")
    manual_cubic_yards = Decimal("0")
    for entry in measurement_entries:
        entry_type = str(entry.get("entry_type") or "").strip().lower()
        if entry_type == "dimension_pair":
            total_square_feet += _to_decimal_number(entry.get("estimated_area_sqft")) or Decimal("0")
        elif entry_type == "material_yards":
            manual_cubic_yards += _to_decimal_number(entry.get("yards")) or Decimal("0")
    computed_cubic_yards = (
        (total_square_feet * (Decimal("2") / Decimal("12"))) / Decimal("27")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total_square_feet > 0 else Decimal("0")
    grand_total_cubic_yards = (computed_cubic_yards + manual_cubic_yards).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    measurement_parse = {
        "classification": parse_classification,
        "rectangles": [
            {
                "kind": "rectangle",
                "raw": str(entry.get("raw_text") or "").strip(),
                "length": float(_to_decimal_number(entry.get("length_ft")) or Decimal("0")),
                "width": float(_to_decimal_number(entry.get("width_ft")) or Decimal("0")),
                "area_sqft": float(_to_decimal_number(entry.get("estimated_area_sqft")) or Decimal("0")),
                "confidence": float(_to_decimal_number(entry.get("confidence")) or Decimal("0")),
            }
            for entry in measurement_entries
            if str(entry.get("entry_type") or "").strip().lower() == "dimension_pair"
        ],
        "manual_yards": [
            {
                "kind": "manual_yards",
                "raw": str(entry.get("raw_text") or "").strip(),
                "cubic_yards": float(_to_decimal_number(entry.get("yards")) or Decimal("0")),
                "confidence": float(_to_decimal_number(entry.get("confidence")) or Decimal("0")),
            }
            for entry in measurement_entries
            if str(entry.get("entry_type") or "").strip().lower() == "material_yards"
        ],
        "unclear_rows": parse_unclear_rows,
        "total_square_feet": float(total_square_feet.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "computed_cubic_yards_at_2_in": float(computed_cubic_yards),
        "manual_cubic_yards": float(manual_cubic_yards.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "grand_total_cubic_yards": float(grand_total_cubic_yards),
        "should_use_geometry_fallback": should_use_geometry_fallback,
    }
    extraction_meta["measurement_parse_classification"] = parse_classification
    extraction_meta["should_use_geometry_fallback"] = should_use_geometry_fallback
    extraction_meta["unclear_rows"] = parse_unclear_rows

    bed_groups, combined_bed_area, combined_bed_material = _bed_groups_from_measurement_entries(
        measurement_entries,
        image_measurement_sources,
    )
    if not bed_groups and (
        allow_fallback_handwritten_measurement_ocr or extraction_meta["trusted_measurements_available"]
    ):
        bed_groups, combined_bed_area, combined_bed_material = _build_bed_groups(ordered_image_records, note_tokens)
    if bed_groups and not any(task.get("job_type") == "flower_bed_refresh" for task in detected_tasks):
        detected_tasks.append(
            {
                "job_type": "flower_bed_refresh",
                "label": "Flower Bed Refresh",
                "confidence": 0.78,
                "evidence": ["bed_geometry"],
            }
        )
        detected_tasks.sort(key=lambda item: item["confidence"], reverse=True)
        primary = detected_tasks[0]["job_type"]

    zones, zone_summary = _detect_zones(
        [(record.get("source_name"), record.get("tokens") or set()) for record in ordered_image_records],
        note_tokens,
        bed_groups=bed_groups,
    )

    logger.info(
        "analyze_uploaded_images normalized_output classification=%s entries=%s openai_error=%s openai_used=%s fallback_ocr_used=%s",
        measurement_parse.get("classification"),
        measurement_entries,
        extraction_meta.get("openai_error"),
        extraction_meta.get("openai_used"),
        extraction_meta.get("fallback_ocr_used"),
    )

    return {
        "primary_job_type": primary,
        "detected_tasks": detected_tasks,
        "summary": ", ".join(task["label"] for task in detected_tasks),
        "dimension_observations": dimension_observations,
        "measurement_entries": measurement_entries,
        "bed_groups": bed_groups,
        "combined_bed_area_sqft": _round_two(combined_bed_area),
        "combined_bed_material_yards": _round_two(combined_bed_material),
        "detected_zones": zones,
        "zone_summary": zone_summary,
        "measurement_parse": measurement_parse,
        "extraction_meta": extraction_meta,
    }
