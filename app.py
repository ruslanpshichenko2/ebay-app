import base64
import hashlib
import html
import math
import io
import json
import os
import re
import statistics
import time
from typing import Any, Optional
from urllib import error, parse, request

import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI
from PIL import Image

try:
    from pillow_heif import register_heif_opener
except ImportError:
    register_heif_opener = None


MODEL_NAME = "gpt-4.1"
MAX_IMAGES = 10
CONDITION_OPTIONS = ["New", "Like New", "Used - Good", "Used - Fair"]
CONDITION_TO_EBAY_CONDITION_ID = {
    "New": "1000",
    "Like New": "2750",
    "Used - Good": "5000",
    "Used - Fair": "6000",
}
FACEBOOK_MARKETPLACE_CREATE_URL = "https://www.facebook.com/marketplace/create/item"
LISTING_TYPE_OPTIONS = ["Both", "eBay Only", "Facebook Only"]
APP_DIR = os.path.dirname(os.path.abspath(__file__))

if register_heif_opener is not None:
    register_heif_opener()


def load_local_env_file(env_path: str) -> None:
    resolved_path = env_path
    if not os.path.isabs(resolved_path):
        resolved_path = os.path.join(APP_DIR, env_path)

    if not os.path.exists(resolved_path):
        return

    with open(resolved_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


def load_local_env_files() -> None:
    for env_path in ("keys.env", ".env"):
        load_local_env_file(env_path)


load_local_env_files()


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment.")
    return OpenAI(api_key=api_key)


def get_env_value(name: str, required: bool = True, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is not set in the environment.")
    return value or ""


def get_ebay_public_config() -> dict[str, str]:
    environment = get_env_value("EBAY_ENVIRONMENT", required=False, default="production").lower()
    api_domain = "api.sandbox.ebay.com" if environment == "sandbox" else "api.ebay.com"
    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET to fetch eBay active-listing prices."
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "api_domain": api_domain,
        "marketplace_id": get_env_value("EBAY_MARKETPLACE_ID", required=False, default="EBAY_US"),
    }


def image_to_data_url(uploaded_file: Any) -> str:
    image_bytes, mime_type = get_image_bytes_and_mime_type(uploaded_file)
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def is_heic_file(uploaded_file: Any) -> bool:
    file_name = (uploaded_file.name or "").lower()
    mime_type = (uploaded_file.type or "").lower()
    return file_name.endswith((".heic", ".heif")) or mime_type in {"image/heic", "image/heif"}


def convert_heic_to_jpeg_bytes(uploaded_file: Any) -> bytes:
    if register_heif_opener is None:
        raise RuntimeError(
            "HEIC support requires pillow-heif. Install it with: "
            "python3 -m pip install pillow pillow-heif"
        )

    uploaded_file.seek(0)
    with Image.open(uploaded_file) as image:
        converted = image.convert("RGB")
        output = io.BytesIO()
        converted.save(output, format="JPEG", quality=95)
        return output.getvalue()


def get_image_bytes_and_mime_type(uploaded_file: Any) -> tuple[bytes, str]:
    if is_heic_file(uploaded_file):
        return convert_heic_to_jpeg_bytes(uploaded_file), "image/jpeg"
    return uploaded_file.getvalue(), uploaded_file.type or "image/jpeg"


def get_preview_image(uploaded_file: Any) -> bytes:
    image_bytes, _ = get_image_bytes_and_mime_type(uploaded_file)
    return image_bytes


def render_image_previews(uploaded_files: list[Any]) -> None:
    preview_blocks: list[str] = []

    for uploaded_file in uploaded_files[:MAX_IMAGES]:
        image_bytes, mime_type = get_image_bytes_and_mime_type(uploaded_file)
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        preview_blocks.append(
            f"""
            <div class="preview-frame">
                <img src="data:{mime_type};base64,{encoded}" alt="Uploaded preview" />
            </div>
            """
        )

    st.markdown("".join(preview_blocks), unsafe_allow_html=True)


def format_price_display(price_text: str) -> str:
    cleaned = (price_text or "").strip()
    if not cleaned:
        return "N/A"
    if cleaned.startswith("$"):
        return cleaned

    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", cleaned)
    if match:
        return f"${match.group(0)}"
    return cleaned


def format_price_display_rounded(price_text: str) -> str:
    cleaned = (price_text or "").strip()
    if not cleaned:
        return "N/A"

    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", cleaned)
    if not match:
        return cleaned

    value = float(match.group(0).replace(",", ""))
    return format_usd_rounded(value)


def extract_part_number(additional_details: str) -> str:
    text = (additional_details or "").strip()
    if not text:
        return ""

    explicit_match = re.search(
        r"(?:^|[\s,;])(?:p\/n|pn|part\s*number)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9\-_/\.]{2,})",
        text,
        flags=re.IGNORECASE,
    )
    if explicit_match:
        return explicit_match.group(1).strip()[:100]

    single_token_match = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9\-_/\.]{3,})\s*", text)
    if single_token_match:
        return single_token_match.group(1).strip()[:100]

    return ""


def compose_ebay_search_query(listing_result: dict[str, Any], additional_details: str = "") -> str:
    pn = extract_part_number(additional_details)
    if pn:
        return pn

    model = " ".join(str(listing_result.get("model", "")).split()).strip()
    if model:
        return model[:100]

    product_name = " ".join(str(listing_result.get("product_name", "")).split()).strip()
    return product_name[:100]


def compose_query_variants(listing_result: dict[str, Any], additional_details: str = "") -> list[str]:
    pn = extract_part_number(additional_details)
    if pn:
        return [pn]

    model = " ".join(str(listing_result.get("model", "")).split()).strip()
    product_name = " ".join(str(listing_result.get("product_name", "")).split()).strip()

    candidates = [model, product_name]

    variants: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = " ".join(candidate.split()).strip()[:100]
        lowered = cleaned.lower()
        if cleaned and lowered not in seen:
            seen.add(lowered)
            variants.append(cleaned)

    return variants


def build_ebay_sold_search_url(listing_result: dict[str, Any], additional_details: str = "") -> str:
    query = compose_ebay_search_query(listing_result, additional_details=additional_details)
    if not query:
        return "https://www.ebay.com/sch/i.html?LH_Sold=1&LH_Complete=1"

    params = {
        "_nkw": query,
        "LH_Sold": "1",
        "LH_Complete": "1",
    }
    return f"https://www.ebay.com/sch/i.html?{parse.urlencode(params)}"


def get_relevance_tokens(listing_result: dict[str, Any], additional_details: str = "") -> set[str]:
    source_parts = [
        extract_part_number(additional_details),
        str(listing_result.get("brand", "")),
        str(listing_result.get("model", "")),
        str(listing_result.get("product_name", "")),
    ]
    stop_words = {
        "new", "used", "good", "fair", "like", "brand", "module", "unit", "item",
        "for", "and", "the", "with", "without", "in", "of", "to", "a", "an",
    }

    tokens: set[str] = set()
    for source_part in source_parts:
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-./]*", source_part):
            normalized = token.lower().strip(".-/")
            if len(normalized) < 3 or normalized in stop_words:
                continue
            tokens.add(normalized)

    return tokens


def filter_items_by_title_relevance(
    items: list[dict[str, Any]],
    relevance_tokens: set[str],
) -> list[dict[str, Any]]:
    if not relevance_tokens:
        return items

    filtered_items: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title", "")).lower()
        title_tokens = {
            token.strip(".-/")
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-./]*", title)
        }
        if relevance_tokens & title_tokens:
            filtered_items.append(item)

    return filtered_items


def build_comp_based_ebay_title(existing_title: str, comp_titles: list[str]) -> str:
    cleaned_existing = " ".join((existing_title or "").split()).strip()
    candidates = [" ".join(title.split()).strip() for title in comp_titles if title and title.strip()]
    if not candidates:
        return cleaned_existing

    token_counts: dict[str, int] = {}
    token_order: dict[str, int] = {}
    stop_words = {
        "new", "brand", "fast", "shipping", "sealed", "module", "communication",
        "for", "and", "the", "with", "pcs", "pc", "1pc", "1pcs",
    }

    for candidate in candidates:
        seen_in_title: set[str] = set()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-./]*", candidate):
            normalized = token.lower()
            if normalized in stop_words or len(normalized) <= 1:
                continue
            if normalized not in token_order:
                token_order[normalized] = len(token_order)
            if normalized not in seen_in_title:
                token_counts[normalized] = token_counts.get(normalized, 0) + 1
                seen_in_title.add(normalized)

    sorted_tokens = sorted(
        token_counts.items(),
        key=lambda item: (-item[1], token_order[item[0]]),
    )
    common_tokens = [token for token, _count in sorted_tokens[:8]]

    best_title = cleaned_existing
    best_score = -1
    for candidate in candidates:
        normalized_candidate = {
            token.lower() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-./]*", candidate)
        }
        score = sum(1 for token in common_tokens if token in normalized_candidate)
        if score > best_score:
            best_title = candidate
            best_score = score

    if len(best_title) > 80:
        best_title = best_title[:80].rstrip(" -,:;/")
        if " " in best_title:
            best_title = best_title.rsplit(" ", 1)[0]

    return best_title or cleaned_existing


def determine_listing_type(include_ebay: bool, include_facebook: bool) -> str:
    if include_ebay and include_facebook:
        return "Both"
    if include_ebay:
        return "eBay Only"
    if include_facebook:
        return "Facebook Only"
    return ""


def build_user_prompt(condition: str, additional_details: str, listing_type: str) -> str:
    if listing_type == "eBay Only":
        marketplace_instruction = (
            "Generate the eBay listing fields normally. Return empty strings for the Facebook title and "
            "description."
        )
    elif listing_type == "Facebook Only":
        marketplace_instruction = (
            "Generate the Facebook Marketplace fields normally. Return empty strings, an empty bullet list, "
            "and empty condition details for the eBay section."
        )
    else:
        marketplace_instruction = "Generate both the eBay and Facebook Marketplace sections."

    return f"""
Analyze the uploaded product images and create selling data for a secondhand marketplace listing.

User-provided details:
- Condition: {condition}
- Additional details: {additional_details or "None provided"}
- Requested listing type: {listing_type}

Return valid JSON only using this schema:
{{
  "product_name": "string",
  "brand": "string",
  "model": "string",
  "category": "string",
  "key_features": ["feature 1", "feature 2"],
  "visible_condition_clues": ["clue 1", "clue 2"],
  "pricing": {{
    "quick_sale_price": "string",
    "market_price": "string",
    "high_end_price": "string",
    "pricing_rationale": "string"
  }},
  "ebay": {{
    "title": "string",
    "structured_description": "string",
    "bullet_points": ["bullet 1", "bullet 2"],
    "condition_details": "string"
  }},
  "facebook_marketplace": {{
    "title": "string",
    "description": "string"
  }}
}}

Requirements:
- Identify the likely product name, brand, and model from visual evidence.
- Summarize key visible features clearly.
- Mention only condition clues that are visible in the images or supported by the user inputs.
- Provide realistic used-market pricing as a range represented by the three price tiers.
- {marketplace_instruction}
- eBay title should be optimized for search and stay close to 80 characters without keyword stuffing.
- eBay description should be structured and easy to scan.
- Facebook title should be shorter than the eBay title.
- Facebook description should sound casual and suitable for local pickup messaging.
- Do not invent accessories, specifications, or defects you cannot support.
""".strip()


def analyze_listing(
    client: OpenAI,
    uploaded_files: list[Any],
    condition: str,
    additional_details: str,
    listing_type: str,
) -> dict[str, Any]:
    input_content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": build_user_prompt(
                condition=condition,
                additional_details=additional_details,
                listing_type=listing_type,
            ),
        }
    ]

    for uploaded_file in uploaded_files[:MAX_IMAGES]:
        input_content.append(
            {
                "type": "input_image",
                "image_url": image_to_data_url(uploaded_file),
            }
        )

    response = client.responses.create(
        model=MODEL_NAME,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are an expert resale assistant for eBay and Facebook Marketplace. "
                            "Analyze product photos carefully, reason conservatively, and return JSON only."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": input_content,
            },
        ],
    )

    raw_text = response.output_text.strip()
    return json.loads(raw_text)


def ebay_request(
    method: str,
    url: str,
    headers: dict[str, str],
    data: Optional[bytes] = None,
) -> tuple[int, str]:
    req = request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req) as response:
            return response.getcode(), response.read().decode("utf-8")
    except error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"eBay API error {exc.code}: {error_body}") from exc


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of an empty list.")
    if len(values) == 1:
        return values[0]

    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    if lower_index == upper_index:
        return ordered[lower_index]

    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def format_usd(value: float) -> str:
    return f"${value:,.2f}"


def format_usd_rounded(value: float) -> str:
    return f"${math.ceil(value):,}"


def extract_first(value: Any, default: Any = None) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def fetch_active_listing_prices(
    listing_result: dict[str, Any],
    condition: str,
    additional_details: str = "",
    image_search_bytes: Optional[bytes] = None,
    force_image_search: bool = False,
) -> dict[str, Any]:
    config = get_ebay_public_config()
    access_token = get_ebay_application_token(config)
    query_variants = compose_query_variants(listing_result, additional_details=additional_details)
    if not query_variants and not image_search_bytes:
        raise RuntimeError("Could not build an eBay search query from the detected item.")
    primary_query = "image search" if force_image_search else (query_variants[0] if query_variants else "image search")

    image_cache_key = ""
    if image_search_bytes:
        image_cache_key = hashlib.sha256(image_search_bytes).hexdigest()[:16]
    cache_key = f"{primary_query.lower()}::{condition}::{image_cache_key}"
    cache_bucket = st.session_state.setdefault("ebay_active_pricing_cache", {})
    cached_entry = cache_bucket.get(cache_key)
    now = time.time()
    cache_ttl_seconds = 60 * 30

    if cached_entry and now - cached_entry["timestamp"] < cache_ttl_seconds:
        return cached_entry["data"]

    def search_active_items(
        query: str,
        include_condition_filter: bool,
        fixed_price_only: bool,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        if fixed_price_only:
            filters.append("buyingOptions:{FIXED_PRICE}")
        if include_condition_filter:
            filters.append(f"conditionIds:{{{CONDITION_TO_EBAY_CONDITION_ID[condition]}}}")

        params = {
            "q": query,
            "limit": "15",
            "sort": "price",
        }
        if filters:
            params["filter"] = ",".join(filters)

        query_string = parse.urlencode(params)
        url = f"https://{config['api_domain']}/buy/browse/v1/item_summary/search?{query_string}"

        try:
            _, body = ebay_request(
                method="GET",
                url=url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-EBAY-C-MARKETPLACE-ID": config["marketplace_id"],
                },
            )
        except RuntimeError as exc:
            exc_text = str(exc)
            if "RateLimiter" in exc_text or "429" in exc_text:
                if cached_entry:
                    return cached_entry["data"].get("raw_items", [])
                raise RuntimeError(
                    "eBay active-listing pricing is temporarily rate-limited. Please wait a few minutes and try again."
                ) from exc
            if "Buy APIs" in exc_text or "production is restricted" in exc_text.lower():
                raise RuntimeError(
                    "Your eBay keyset may not have production Browse API access yet."
                ) from exc
            raise

        data = json.loads(body)
        return data.get("itemSummaries", [])

    def search_active_items_by_image(
        image_bytes: bytes,
        include_condition_filter: bool,
        fixed_price_only: bool,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        if fixed_price_only:
            filters.append("buyingOptions:{FIXED_PRICE}")
        if include_condition_filter:
            filters.append(f"conditionIds:{{{CONDITION_TO_EBAY_CONDITION_ID[condition]}}}")

        params = {
            "limit": "15",
        }
        if filters:
            params["filter"] = ",".join(filters)

        query_string = parse.urlencode(params)
        url = f"https://{config['api_domain']}/buy/browse/v1/item_summary/search_by_image?{query_string}"
        payload = json.dumps(
            {
                "image": base64.b64encode(image_bytes).decode("utf-8"),
            }
        ).encode("utf-8")

        try:
            _, body = ebay_request(
                method="POST",
                url=url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "X-EBAY-C-MARKETPLACE-ID": config["marketplace_id"],
                },
                data=payload,
            )
        except RuntimeError as exc:
            exc_text = str(exc)
            if "RateLimiter" in exc_text or "429" in exc_text:
                if cached_entry:
                    return cached_entry["data"].get("raw_items", [])
                raise RuntimeError(
                    "eBay image-search pricing is temporarily rate-limited. Please wait a few minutes and try again."
                ) from exc
            if "sandbox" in exc_text.lower() and "not supported" in exc_text.lower():
                raise RuntimeError("eBay image search is not supported in Sandbox.")
            raise

        data = json.loads(body)
        return data.get("itemSummaries", [])

    search_attempts = [
        (True, True),
        (False, True),
        (True, False),
        (False, False),
    ]

    item_entries: list[dict[str, Any]] = []
    relaxed_condition_match = False
    broadened_query = False
    fixed_price_filter_relaxed = False
    matched_query = primary_query
    image_result_count = 0
    image_relevance_filtered = False
    min_relevant_image_results = 3
    has_part_number = bool(extract_part_number(additional_details))

    def try_image_search() -> bool:
        nonlocal broadened_query
        nonlocal fixed_price_filter_relaxed
        nonlocal image_relevance_filtered
        nonlocal image_result_count
        nonlocal item_entries
        nonlocal matched_query
        nonlocal relaxed_condition_match
        nonlocal used_image_search

        if not image_search_bytes or has_part_number:
            return False

        relevance_tokens = get_relevance_tokens(
            listing_result=listing_result,
            additional_details=additional_details,
        )
        for include_condition_filter, fixed_price_only in search_attempts:
            image_items = search_active_items_by_image(
                image_bytes=image_search_bytes,
                include_condition_filter=include_condition_filter,
                fixed_price_only=fixed_price_only,
            )
            image_result_count = len(image_items)
            filtered_image_items = filter_items_by_title_relevance(image_items, relevance_tokens)
            image_relevance_filtered = len(filtered_image_items) < image_result_count
            if filtered_image_items:
                image_items = filtered_image_items

            if image_items and len(image_items) >= min_relevant_image_results:
                item_entries = image_items
                used_image_search = True
                matched_query = "image search"
                relaxed_condition_match = not include_condition_filter
                fixed_price_filter_relaxed = not fixed_price_only
                broadened_query = bool(query_variants)
                return True

        return False

    used_image_search = False
    if force_image_search:
        try_image_search()

    if not item_entries and not force_image_search:
        for query_variant in query_variants:
            for include_condition_filter, fixed_price_only in search_attempts:
                item_entries = search_active_items(
                    query=query_variant,
                    include_condition_filter=include_condition_filter,
                    fixed_price_only=fixed_price_only,
                )
                if item_entries:
                    matched_query = query_variant
                    relaxed_condition_match = not include_condition_filter
                    fixed_price_filter_relaxed = not fixed_price_only
                    broadened_query = query_variant.lower() != primary_query.lower()
                    break
            if item_entries:
                break

    if not item_entries:
        try_image_search()

    prices: list[float] = []
    comp_rows: list[dict[str, str]] = []

    for item in item_entries:
        current_price = item.get("price") or {}
        item_price = float(current_price.get("value", 0) or 0)
        if item_price <= 0:
            continue

        prices.append(item_price)
        comp_rows.append(
            {
                "title": item.get("title", "Untitled listing"),
                "price": format_usd_rounded(item_price),
                "item_url": item.get("itemWebUrl", ""),
                "end_date": item.get("itemEndDate", ""),
            }
        )

    if not prices:
        if image_result_count and image_relevance_filtered:
            raise RuntimeError(
                "eBay image search returned results, but too few matched the detected product details."
            )
        raise RuntimeError("No active eBay listings were returned for this item.")

    recommended = {
        "quick_sale_price": format_usd_rounded(percentile(prices, 0.15)),
        "market_price": format_usd_rounded(statistics.median(prices)),
        "high_end_price": format_usd_rounded(percentile(prices, 0.75)),
        "sample_size": len(prices),
        "query": matched_query,
        "average_price": format_usd_rounded(sum(prices) / len(prices)),
        "comps": comp_rows[:8],
        "condition_filter_relaxed": relaxed_condition_match,
        "fixed_price_filter_relaxed": fixed_price_filter_relaxed,
        "used_image_search": used_image_search,
        "image_result_count": image_result_count,
        "image_relevance_filtered": image_relevance_filtered,
        "query_broadened": broadened_query,
        "raw_items": item_entries,
    }
    cache_bucket[cache_key] = {"timestamp": now, "data": recommended}
    return recommended


def get_ebay_application_token(config: dict[str, str]) -> str:
    cache_key = f"{config['client_id']}::{config['api_domain']}"
    cache_bucket = st.session_state.setdefault("ebay_application_token_cache", {})
    cached_entry = cache_bucket.get(cache_key)
    now = time.time()

    if cached_entry and cached_entry["expires_at"] > now + 60:
        return cached_entry["token"]

    token_url = f"https://{config['api_domain']}/identity/v1/oauth2/token"
    credentials = f"{config['client_id']}:{config['client_secret']}".encode("utf-8")
    auth_header = base64.b64encode(credentials).decode("utf-8")
    payload = parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    ).encode("utf-8")

    _, body = ebay_request(
        method="POST",
        url=token_url,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=payload,
    )
    token_data = json.loads(body)
    access_token = token_data.get("access_token", "")
    expires_in = int(token_data.get("expires_in", 7200) or 7200)
    if not access_token:
        raise RuntimeError("eBay did not return an application access token for Browse API pricing.")

    cache_bucket[cache_key] = {
        "token": access_token,
        "expires_at": now + expires_in,
    }
    return access_token


def render_copy_button(label: str, text: str, key: str) -> None:
    safe_text = json.dumps(text)
    components.html(
        f"""
        <div style="margin: 0.25rem 0 0.75rem 0;">
          <button
            onclick='navigator.clipboard.writeText({safe_text}).then(() => {{
              const status = document.getElementById("status-{key}");
              status.innerText = "Copied";
              setTimeout(() => status.innerText = "", 1500);
            }})'
            style="
              background: #111827;
              color: white;
              border: none;
              border-radius: 8px;
              padding: 0.45rem 0.8rem;
              cursor: pointer;
              font-size: 0.9rem;
            "
          >
            {label}
          </button>
          <span id="status-{key}" style="margin-left: 0.5rem; color: #047857; font-size: 0.9rem;"></span>
        </div>
        """,
        height=48,
    )


def format_ebay_description(ebay_data: dict[str, Any]) -> str:
    sections = [ebay_data.get("structured_description", "").strip()]

    bullet_points = ebay_data.get("bullet_points", [])
    if bullet_points:
        bullets = "\n".join(f"- {item}" for item in bullet_points)
        sections.append(f"Highlights:\n{bullets}")

    condition_details = ebay_data.get("condition_details", "").strip()
    if condition_details:
        sections.append(f"Condition Details: {condition_details}")

    return "\n\n".join(section for section in sections if section)


def render_comp_table(
    title: str,
    comps: list[dict[str, str]],
    footer_link_url: str = "",
    footer_link_label: str = "",
) -> None:
    st.write(f"**{title}**")
    row_html: list[str] = []
    for comp in comps:
        comp_title = comp.get("title", "Untitled listing")
        item_url = comp.get("item_url", "")
        price = comp.get("price", "N/A")
        safe_title = html.escape(comp_title)
        safe_price = html.escape(format_price_display_rounded(price))
        if item_url:
            title_html = f'<a href="{html.escape(item_url)}" target="_blank">{safe_title}</a>'
        else:
            title_html = safe_title
        row_html.append(
            f"""
            <tr>
              <td class="comp-title-cell">{title_html}</td>
              <td class="comp-price-cell">{safe_price}</td>
            </tr>
            """
        )

    footer_html = ""
    if footer_link_url and footer_link_label:
        footer_html = f"""
        <div class="comp-footer-link">
          <a href="{html.escape(footer_link_url)}" target="_blank">{html.escape(footer_link_label)}</a>
        </div>
        """

    table_html = f"""
        <style>
          body {{
            margin: 0;
          }}
          .comp-table-wrap {{
            width: 100%;
            overflow-x: auto;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.02);
          }}
          .comp-table {{
            width: 100%;
            min-width: 0;
            border-collapse: collapse;
            table-layout: fixed;
            font-family: "SF Pro Display", "SF Pro Text", "Inter", -apple-system, BlinkMacSystemFont,
              "Segoe UI", sans-serif;
            color: #f5f8f6;
          }}
          .comp-table th,
          .comp-table td {{
            padding: 0.65rem 0.7rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            vertical-align: middle;
          }}
          .comp-table th {{
            text-align: left;
            color: #98a2a7;
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
          }}
          .comp-title-cell,
          .comp-title-cell a {{
            white-space: nowrap;
            color: #4ea1ff;
            text-decoration: underline;
            font-size: 0.65rem;
          }}
          .comp-price-cell {{
            white-space: nowrap;
            text-align: right;
            font-weight: 700;
            color: #f5f8f6;
            font-size: 0.65rem;
          }}
          .comp-footer-link {{
            margin-top: 0.95rem;
            font-family: "SF Pro Display", "SF Pro Text", "Inter", -apple-system, BlinkMacSystemFont,
              "Segoe UI", sans-serif;
            font-size: 0.82rem;
          }}
          .comp-footer-link a {{
            color: #4ea1ff;
            text-decoration: underline;
          }}
        </style>
        <div class="comp-table-wrap">
          <table class="comp-table">
            <colgroup>
              <col style="width: 90%;">
              <col style="width: 10%;">
            </colgroup>
            <thead>
              <tr>
                <th>Title</th>
                <th>Price</th>
              </tr>
            </thead>
            <tbody>
              {''.join(row_html)}
            </tbody>
          </table>
        </div>
        {footer_html}
        """
    row_count = max(len(comps), 1)
    footer_height = 36 if footer_html else 0
    components.html(table_html, height=46 + 34 * row_count + footer_height, scrolling=False)


def show_product_analysis_details(result: dict[str, Any]) -> None:
    st.subheader("Detected Product")
    st.write(f"**Product Name:** {result.get('product_name', 'N/A')}")
    st.write(f"**Brand:** {result.get('brand', 'N/A')}")
    st.write(f"**Model:** {result.get('model', 'N/A')}")
    st.write(f"**Category:** {result.get('category', 'N/A')}")

    st.subheader("Key Features")
    for feature in result.get("key_features", []):
        st.write(f"- {feature}")

    st.subheader("Visible Condition Clues")
    for clue in result.get("visible_condition_clues", []):
        st.write(f"- {clue}")


def show_results(result: dict[str, Any], listing_type: str) -> None:
    pricing = result.get("pricing", {})
    active_listing_pricing = result.get("ebay_active_listing_pricing")

    if not active_listing_pricing:
        st.subheader("Price Range")
        col1, col2, col3 = st.columns(3)
        col1.metric("Quick Sale", format_price_display(pricing.get("quick_sale_price", "")))
        col2.metric("Market Price", format_price_display(pricing.get("market_price", "")))
        col3.metric("High-End", format_price_display(pricing.get("high_end_price", "")))

    show_ebay = listing_type in {"Both", "eBay Only"}
    show_facebook = listing_type in {"Both", "Facebook Only"}
    if show_ebay and active_listing_pricing:
        st.subheader("eBay Active Listings Pricing")
        comp_col1, comp_col2, comp_col3 = st.columns(3)
        comp_col1.metric(
            "Quick Sale",
            format_price_display(active_listing_pricing.get("quick_sale_price", "")),
        )
        comp_col2.metric(
            "Market Price",
            format_price_display(active_listing_pricing.get("market_price", "")),
        )
        comp_col3.metric(
            "High-End",
            format_price_display(active_listing_pricing.get("high_end_price", "")),
        )
        st.caption(
            f"Based on {active_listing_pricing.get('sample_size', 0)} active eBay listings "
            f"for: {active_listing_pricing.get('query', 'N/A')}. "
            f"Average active listing price: {active_listing_pricing.get('average_price', 'N/A')}."
        )
        if active_listing_pricing.get("condition_filter_relaxed"):
            st.caption("Condition filter was relaxed to find enough active listings.")
        if active_listing_pricing.get("fixed_price_filter_relaxed"):
            st.caption("Fixed-price-only filtering was relaxed to include more live listings.")
        if active_listing_pricing.get("used_image_search"):
            st.caption("eBay image search was used for these comps.")
        if active_listing_pricing.get("image_relevance_filtered"):
            st.caption("Unrelated image-search matches were filtered out by title relevance.")
        if active_listing_pricing.get("query_broadened"):
            st.caption(
                f"Search query was broadened to: {active_listing_pricing.get('query', 'N/A')}."
            )
        sold_search_url = result.get("ebay_sold_search_url")
        if active_listing_pricing.get("comps"):
            render_comp_table(
                "Active Listing Comps",
                active_listing_pricing["comps"],
                footer_link_url=sold_search_url,
                footer_link_label="Open eBay sold/completed search",
            )
        elif sold_search_url:
            st.markdown(f"[Open eBay sold/completed search]({sold_search_url})")

    if show_ebay:
        ebay = result.get("ebay", {})
        comp_titles = [comp.get("title", "") for comp in active_listing_pricing.get("comps", [])] if active_listing_pricing else []
        ebay_title = build_comp_based_ebay_title(ebay.get("title", ""), comp_titles)
        ebay_description = format_ebay_description(ebay)

        st.subheader("eBay Listing")
        st.text_area("eBay Title", value=ebay_title, height=70, key="ebay_title")
        render_copy_button("Copy eBay Title", ebay_title, "ebay-title")
        st.text_area(
            "eBay Description",
            value=ebay_description,
            height=220,
            key="ebay_description",
        )
        render_copy_button("Copy eBay Description", ebay_description, "ebay-description")

    if show_facebook:
        if show_ebay:
            st.markdown(
                '<div style="height:1px;background:linear-gradient(90deg, rgba(0, 200, 5, 0.82), rgba(0, 200, 5, 0.06));margin:1.1rem 0 1.05rem 0;"></div>',
                unsafe_allow_html=True,
            )
        facebook = result.get("facebook_marketplace", {})
        st.subheader("Facebook Marketplace Listing")
        st.text_area(
            "Facebook Title",
            value=facebook.get("title", ""),
            height=70,
            key="facebook_title",
        )
        render_copy_button("Copy Facebook Title", facebook.get("title", ""), "facebook-title")
        st.text_area(
            "Facebook Description",
            value=facebook.get("description", ""),
            height=200,
            key="facebook_description",
        )
        render_copy_button(
            "Copy Facebook Description",
            facebook.get("description", ""),
            "facebook-description",
        )

def show_facebook_draft_tools(
    listing_result: dict[str, Any],
    condition: str,
    additional_details: str,
) -> None:
    st.subheader("Facebook Draft")
    st.markdown(f"[Open Facebook Marketplace Create Listing]({FACEBOOK_MARKETPLACE_CREATE_URL})")


def generate_listing_with_progress(
    client: OpenAI,
    uploaded_files: list[Any],
    condition: str,
    additional_details: str,
    listing_type: str,
    force_ebay_image_search: bool = False,
) -> dict[str, Any]:
    progress_bar = st.progress(0)
    status_text = st.empty()

    status_text.write("Preparing images...")
    progress_bar.progress(15)

    status_text.write("Analyzing product details...")
    result = analyze_listing(
        client=client,
        uploaded_files=uploaded_files,
        condition=condition,
        additional_details=additional_details,
        listing_type=listing_type,
    )
    progress_bar.progress(72)

    if listing_type in {"Both", "eBay Only"}:
        status_text.write("Checking eBay active listings...")
        result["ebay_sold_search_url"] = build_ebay_sold_search_url(
            listing_result=result,
            additional_details=additional_details,
        )
        try:
            image_search_bytes = get_image_bytes_and_mime_type(uploaded_files[0])[0] if uploaded_files else None
            result["ebay_active_listing_pricing"] = fetch_active_listing_prices(
                listing_result=result,
                condition=condition,
                additional_details=additional_details,
                image_search_bytes=image_search_bytes,
                force_image_search=force_ebay_image_search,
            )
            result.pop("ebay_active_listing_pricing_error", None)
        except Exception as exc:
            result["ebay_active_listing_pricing_error"] = str(exc)
        progress_bar.progress(92)

        comp_titles = [
            comp.get("title", "")
            for comp in result.get("ebay_active_listing_pricing", {}).get("comps", [])
        ]
        if "ebay" in result:
            result["ebay"]["title"] = build_comp_based_ebay_title(
                result["ebay"].get("title", ""),
                comp_titles,
            )

    status_text.write("Finalizing listing output...")
    result["selected_listing_type"] = listing_type
    progress_bar.progress(100)
    status_text.empty()
    progress_bar.empty()
    return result


def main() -> None:
    st.set_page_config(
        page_title="eBay & Facebook Marketplace Listing Generator",
        page_icon="🛍️",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        :root {
            --rh-bg: #0b0d0e;
            --rh-panel: #121517;
            --rh-panel-2: #171b1d;
            --rh-border: rgba(255, 255, 255, 0.08);
            --rh-text: #f5f8f6;
            --rh-muted: #98a2a7;
            --rh-green: #00c805;
            --rh-green-soft: rgba(0, 200, 5, 0.14);
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(0, 200, 5, 0.11), transparent 24%),
                radial-gradient(circle at top right, rgba(0, 200, 5, 0.07), transparent 18%),
                linear-gradient(180deg, #0d1011 0%, #080909 100%);
            color: var(--rh-text);
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
            max-width: 1240px;
        }
        h1 {
            font-size: 2.45rem !important;
            font-weight: 700 !important;
            letter-spacing: -0.03em;
            margin-bottom: 0.35rem !important;
        }
        html, body, [class*="css"] {
            font-family: "SF Pro Display", "SF Pro Text", "Inter", -apple-system, BlinkMacSystemFont,
                "Segoe UI", sans-serif;
        }
        .panel-label {
            color: var(--rh-muted);
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.8rem;
        }
        div[data-testid="column"] > div {
            background: linear-gradient(180deg, rgba(23, 27, 29, 0.94), rgba(18, 21, 23, 0.96));
            border: 1px solid var(--rh-border);
            border-radius: 22px;
            padding: 1.25rem 1.2rem 1.4rem 1.2rem;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.28);
            height: 100%;
        }
        [data-testid="stFileUploaderFile"] {
            display: none;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] small {
            display: none;
        }
        div[data-testid="stFileUploader"] section {
            background: rgba(255, 255, 255, 0.025);
            border: 1px dashed rgba(255, 255, 255, 0.14);
            border-radius: 18px;
        }
        div[data-testid="stSelectbox"],
        div[data-testid="stTextArea"],
        div[data-testid="stFileUploader"] {
            margin-bottom: 0.85rem;
        }
        div[data-testid="stCheckbox"] {
            margin-bottom: 0.15rem;
        }
        div[data-testid="stButton"] {
            margin-top: 0.5rem;
        }
        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            background: linear-gradient(180deg, #14d91b 0%, #00c805 100%);
            color: #041106;
            border: none;
            border-radius: 14px;
            font-weight: 700;
            box-shadow: 0 10px 24px rgba(0, 200, 5, 0.24);
        }
        div[data-testid="stButton"] button:hover,
        div[data-testid="stDownloadButton"] button:hover {
            background: linear-gradient(180deg, #20e827 0%, #05d30a 100%);
        }
        div[data-testid="stButton"] button[kind="secondary"] {
            background: rgba(255, 255, 255, 0.05);
            color: var(--rh-text);
            border: 1px solid var(--rh-border);
            box-shadow: none;
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="base-input"] > div,
        textarea {
            background: rgba(255, 255, 255, 0.04) !important;
            border-color: rgba(255, 255, 255, 0.08) !important;
            border-radius: 14px !important;
            color: var(--rh-text) !important;
        }
        label, .stCheckbox label, p, span, div {
            color: var(--rh-text);
        }
        small, .stCaption {
            color: var(--rh-muted) !important;
        }
        div[data-testid="stMarkdownContainer"] h3 {
            font-size: 1.05rem;
            font-weight: 700;
            letter-spacing: -0.01em;
        }
        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.035);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 0.95rem 0.9rem;
        }
        div[data-testid="stMetric"] label {
            color: var(--rh-muted) !important;
            font-size: 0.8rem !important;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        div[data-testid="stMetricValue"] {
            color: var(--rh-text) !important;
        }
        div[data-testid="stExpander"] {
            border: 1px solid var(--rh-border);
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.025);
        }
        div[data-testid="stAlert"] {
            border-radius: 14px;
        }
        img {
            border-radius: 18px;
        }
        .preview-frame {
            margin-bottom: 0.85rem;
            border-radius: 18px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.03);
        }
        .preview-frame img {
            display: block;
            width: 100%;
            height: auto;
            border-radius: 0;
        }
        .comp-table-wrap {
            width: 100%;
            overflow-x: auto;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.02);
        }
        .comp-table {
            width: 100%;
            min-width: 980px;
            border-collapse: collapse;
            table-layout: fixed;
        }
        .comp-table th,
        .comp-table td {
            padding: 0.8rem 0.9rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            vertical-align: middle;
        }
        .comp-table th {
            text-align: left;
            color: var(--rh-muted);
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
        }
        .comp-title-cell,
        .comp-title-cell a {
            white-space: nowrap;
        }
        .comp-price-cell {
            white-space: nowrap;
            text-align: right;
            font-weight: 700;
        }
        .header-rule {
            height: 1px;
            border: none;
            background: linear-gradient(90deg, rgba(0, 200, 5, 0.6), rgba(0, 200, 5, 0));
            margin: 0.2rem 0 1.2rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("eBay & Facebook Marketplace Listing Generator")
    st.markdown('<div class="header-rule"></div>', unsafe_allow_html=True)

    left_col, right_col = st.columns([1, 1.2], gap="large")

    with left_col:
        st.markdown('<div class="panel-label">Listing Inputs</div>', unsafe_allow_html=True)
        uploaded_files = st.file_uploader(
            "Upload up to 10 product images",
            type=["png", "jpg", "jpeg", "webp", "heic", "heif"],
            accept_multiple_files=True,
        )

        if uploaded_files and len(uploaded_files) > MAX_IMAGES:
            st.warning(f"Please upload no more than {MAX_IMAGES} images.")

        condition = st.selectbox("Condition", CONDITION_OPTIONS)
        st.write("**Listing Platform**")
        ebay_option_col, ebay_image_option_col = st.columns([1, 1.4])
        with ebay_option_col:
            include_ebay = st.checkbox("eBay", value=True)
        with ebay_image_option_col:
            force_ebay_image_search = st.checkbox(
                "Search eBay by first image",
                value=False,
                disabled=not include_ebay,
            )
        include_facebook = st.checkbox("Facebook Marketplace", value=True)
        listing_type = determine_listing_type(include_ebay, include_facebook)
        additional_details = st.text_area("PN / Notes / Defects", height=100)
        generate_clicked = st.button("Generate Listing", type="primary", width="stretch")

        if uploaded_files:
            st.write("**Image Preview**")
            render_image_previews(uploaded_files)

    with right_col:
        st.markdown('<div class="panel-label">Generated Outputs</div>', unsafe_allow_html=True)

        if generate_clicked:
            if not uploaded_files:
                st.error("Upload at least one product image to generate a listing.")
                return

            if not listing_type:
                st.error("Select at least one listing platform to generate.")
                return

            if len(uploaded_files) > MAX_IMAGES:
                st.error(f"Only up to {MAX_IMAGES} images are supported.")
                return

            try:
                client = get_openai_client()
                result = generate_listing_with_progress(
                    client=client,
                    uploaded_files=uploaded_files,
                    condition=condition,
                    additional_details=additional_details,
                    listing_type=listing_type,
                    force_ebay_image_search=force_ebay_image_search,
                )
                st.session_state["listing_result"] = result
            except json.JSONDecodeError:
                st.error("The model returned an unexpected response. Please try again.")
            except Exception as exc:
                st.error(f"Unable to generate the listing: {exc}")

        saved_result = st.session_state.get("listing_result")
        if saved_result:
            active_listing_type = saved_result.get("selected_listing_type", listing_type)

            active_listing_pricing_error = saved_result.get("ebay_active_listing_pricing_error")
            if active_listing_type in {"Both", "eBay Only"} and active_listing_pricing_error:
                st.warning(
                    "Active-listing pricing could not be loaded from eBay: "
                    f"{active_listing_pricing_error}"
                )
            show_results(saved_result, active_listing_type)
            if active_listing_type in {"Both", "Facebook Only"}:
                show_facebook_draft_tools(
                    listing_result=saved_result,
                    condition=condition,
                    additional_details=additional_details,
                )
        else:
            st.info("Your generated product analysis and listing copy will appear here.")


if __name__ == "__main__":
    main()
