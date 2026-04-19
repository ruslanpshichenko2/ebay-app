import base64
import html
import math
import io
import json
import os
import re
import statistics
import time
import uuid
import xml.etree.ElementTree as ET
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
EBAY_API_COMPATIBILITY_LEVEL = "1231"
EBAY_XML_NS = {"ebay": "urn:ebay:apis:eBLBaseComponents"}
EBAY_OAUTH_SCOPES = (
    "https://api.ebay.com/oauth/api_scope/sell.inventory "
    "https://api.ebay.com/oauth/api_scope/sell.account"
)
CONDITION_TO_EBAY_ENUM = {
    "New": "NEW",
    "Like New": "LIKE_NEW",
    "Used - Good": "USED_GOOD",
    "Used - Fair": "USED_ACCEPTABLE",
}
CONDITION_TO_COMPLETED_FILTER_ID = {
    "New": "1000",
    "Like New": "2750",
    "Used - Good": "5000",
    "Used - Fair": "6000",
}
EBAY_DRAFTS_URL = "https://www.ebay.com/sh/lst/drafts"
FACEBOOK_MARKETPLACE_CREATE_URL = "https://www.facebook.com/marketplace/create/item"
FINDING_API_VERSION = "1.13.0"
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


def get_ebay_config() -> dict[str, str]:
    environment = get_env_value("EBAY_ENVIRONMENT", required=False, default="production").lower()
    api_domain = "api.sandbox.ebay.com" if environment == "sandbox" else "api.ebay.com"
    finding_domain = "svcs.sandbox.ebay.com" if environment == "sandbox" else "svcs.ebay.com"

    return {
        "client_id": get_env_value("EBAY_CLIENT_ID"),
        "client_secret": get_env_value("EBAY_CLIENT_SECRET"),
        "refresh_token": get_env_value("EBAY_REFRESH_TOKEN"),
        "marketplace_id": get_env_value("EBAY_MARKETPLACE_ID", required=False, default="EBAY_US"),
        "content_language": get_env_value("EBAY_CONTENT_LANGUAGE", required=False, default="en-US"),
        "site_id": get_env_value("EBAY_SITE_ID", required=False, default="0"),
        "api_domain": api_domain,
        "finding_domain": finding_domain,
    }


def get_ebay_oauth_setup_config() -> dict[str, str]:
    environment = get_env_value("EBAY_ENVIRONMENT", required=False, default="production").lower()
    auth_domain = "auth.sandbox.ebay.com" if environment == "sandbox" else "auth.ebay.com"
    api_domain = "api.sandbox.ebay.com" if environment == "sandbox" else "api.ebay.com"

    return {
        "client_id": get_env_value("EBAY_CLIENT_ID"),
        "client_secret": get_env_value("EBAY_CLIENT_SECRET"),
        "runame": (
            os.getenv("EBAY_RUNAME")
            or os.getenv("EBAY_RU_NAME")
            or os.getenv("EBAY_REDIRECT_URI_NAME")
            or ""
        ).strip(),
        "auth_domain": auth_domain,
        "api_domain": api_domain,
    }


def get_ebay_public_config() -> dict[str, str]:
    environment = get_env_value("EBAY_ENVIRONMENT", required=False, default="production").lower()
    api_domain = "api.sandbox.ebay.com" if environment == "sandbox" else "api.ebay.com"
    client_id = os.getenv("EBAY_CLIENT_ID") or os.getenv("EBAY_APP_ID")
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


def get_ebay_publish_config() -> dict[str, str]:
    return {
        "category_id": get_env_value("EBAY_CATEGORY_ID"),
        "merchant_location_key": get_env_value("EBAY_MERCHANT_LOCATION_KEY"),
        "fulfillment_policy_id": get_env_value("EBAY_FULFILLMENT_POLICY_ID"),
        "payment_policy_id": get_env_value("EBAY_PAYMENT_POLICY_ID"),
        "return_policy_id": get_env_value("EBAY_RETURN_POLICY_ID"),
        "listing_duration": get_env_value("EBAY_LISTING_DURATION", required=False, default="GTC"),
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


def build_condition_description(
    visible_condition_clues: list[str],
    additional_details: str,
) -> str:
    parts: list[str] = []
    if visible_condition_clues:
        parts.append("Visible condition clues: " + "; ".join(visible_condition_clues))
    if additional_details:
        parts.append("Additional seller details: " + additional_details.strip())
    return " | ".join(part for part in parts if part).strip()[:1000]


def normalize_price_value(price_text: str) -> str:
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", price_text)
    if not match:
        raise RuntimeError(f"Could not parse a numeric eBay price from: {price_text}")
    return match.group(0).replace(",", "")


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


def build_price_options(pricing: dict[str, Any]) -> dict[str, str]:
    return {
        "Quick Sale": pricing.get("quick_sale_price", ""),
        "Market Price": pricing.get("market_price", ""),
        "High-End": pricing.get("high_end_price", ""),
    }


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
) -> dict[str, Any]:
    config = get_ebay_public_config()
    access_token = get_ebay_application_token(config)
    query_variants = compose_query_variants(listing_result, additional_details=additional_details)
    if not query_variants:
        raise RuntimeError("Could not build an eBay search query from the detected item.")
    primary_query = query_variants[0]

    cache_key = f"{primary_query.lower()}::{condition}"
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
            filters.append(f"conditionIds:{{{CONDITION_TO_COMPLETED_FILTER_ID[condition]}}}")

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
                "price": format_usd(item_price),
                "item_url": item.get("itemWebUrl", ""),
                "end_date": item.get("itemEndDate", ""),
            }
        )

    if not prices:
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
        "query_broadened": broadened_query,
        "raw_items": item_entries,
    }
    cache_bucket[cache_key] = {"timestamp": now, "data": recommended}
    return recommended


def get_ebay_user_token(config: dict[str, str]) -> str:
    token_url = f"https://{config['api_domain']}/identity/v1/oauth2/token"
    credentials = f"{config['client_id']}:{config['client_secret']}".encode("utf-8")
    auth_header = base64.b64encode(credentials).decode("utf-8")
    payload = parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": config["refresh_token"],
            "scope": EBAY_OAUTH_SCOPES,
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
    return token_data["access_token"]


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


def build_ebay_consent_url(config: dict[str, str]) -> str:
    if not config["runame"]:
        raise RuntimeError(
            "Set EBAY_RUNAME in .env after creating your eBay RuName/OAuth redirect configuration."
        )

    query = parse.urlencode(
        {
            "client_id": config["client_id"],
            "redirect_uri": config["runame"],
            "response_type": "code",
            "scope": EBAY_OAUTH_SCOPES,
        }
    )
    return f"https://{config['auth_domain']}/oauth2/authorize?{query}"


def extract_ebay_auth_code(auth_input: str) -> str:
    cleaned = (auth_input or "").strip()
    if not cleaned:
        raise RuntimeError("Paste the full eBay callback URL or the raw authorization code.")

    if "code=" in cleaned:
        parsed_url = parse.urlparse(cleaned)
        query_values = parse.parse_qs(parsed_url.query)
        code = query_values.get("code", [""])[0]
        if not code:
            raise RuntimeError("The callback URL did not include a code parameter.")
        return parse.unquote(code).strip()

    return parse.unquote(cleaned).strip()


def exchange_ebay_authorization_code(
    auth_input: str,
) -> dict[str, str]:
    config = get_ebay_oauth_setup_config()
    if not config["runame"]:
        raise RuntimeError(
            "EBAY_RUNAME is missing. Add the RuName value from eBay OAuth settings to your .env file."
        )

    token_url = f"https://{config['api_domain']}/identity/v1/oauth2/token"
    credentials = f"{config['client_id']}:{config['client_secret']}".encode("utf-8")
    auth_header = base64.b64encode(credentials).decode("utf-8")
    authorization_code = extract_ebay_auth_code(auth_input)
    payload = parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": config["runame"],
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
    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        raise RuntimeError("eBay did not return a refresh token from the authorization code exchange.")

    return {
        "refresh_token": refresh_token,
        "access_token": token_data.get("access_token", ""),
    }


def test_ebay_authentication() -> str:
    config = get_ebay_config()
    get_ebay_user_token(config)
    return "eBay authentication succeeded. Your client ID, client secret, and refresh token match."


def upload_image_to_ebay(
    access_token: str,
    uploaded_file: Any,
    site_id: str,
    api_domain: str,
) -> str:
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    xml_payload = """<?xml version="1.0" encoding="utf-8"?>
<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <PictureName>{picture_name}</PictureName>
  <PictureSet>Supersize</PictureSet>
</UploadSiteHostedPicturesRequest>
""".format(picture_name=uploaded_file.name)

    binary, mime_type = get_image_bytes_and_mime_type(uploaded_file)
    picture_name = uploaded_file.name
    if is_heic_file(uploaded_file):
        picture_name = os.path.splitext(uploaded_file.name)[0] + ".jpg"

    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            b'Content-Disposition: form-data; name="XML Payload"\r\n\r\n',
            xml_payload.encode("utf-8"),
            b"\r\n",
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{picture_name}"; '
                f'filename="{picture_name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            binary,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )

    _, response_body = ebay_request(
        method="POST",
        url=f"https://{api_domain}/ws/api.dll",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
            "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_API_COMPATIBILITY_LEVEL,
            "X-EBAY-API-SITEID": site_id,
            "X-EBAY-API-IAF-TOKEN": access_token,
        },
        data=body,
    )

    root = ET.fromstring(response_body)
    ack = root.findtext("ebay:Ack", namespaces=EBAY_XML_NS)
    if ack not in {"Success", "Warning"}:
        short_message = root.findtext(".//ebay:ShortMessage", namespaces=EBAY_XML_NS) or "Unknown error"
        long_message = root.findtext(".//ebay:LongMessage", namespaces=EBAY_XML_NS) or ""
        raise RuntimeError(f"Image upload failed: {short_message} {long_message}".strip())

    full_url = root.findtext(".//ebay:FullURL", namespaces=EBAY_XML_NS)
    if not full_url:
        raise RuntimeError("eBay did not return a hosted image URL.")
    return full_url


def create_inventory_item(
    access_token: str,
    config: dict[str, str],
    sku: str,
    title: str,
    description: str,
    brand: str,
    condition: str,
    condition_description: str,
    image_urls: list[str],
) -> None:
    aspects: dict[str, list[str]] = {}
    if brand:
        aspects["Brand"] = [brand]

    payload: dict[str, Any] = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": condition,
        "product": {
            "title": title[:80],
            "description": description,
            "imageUrls": image_urls,
            "aspects": aspects,
        },
    }

    if brand:
        payload["product"]["brand"] = brand
    if condition_description and condition != "NEW":
        payload["conditionDescription"] = condition_description

    ebay_request(
        method="PUT",
        url=f"https://{config['api_domain']}/sell/inventory/v1/inventory_item/{parse.quote(sku)}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": config["content_language"],
        },
        data=json.dumps(payload).encode("utf-8"),
    )


def create_ebay_offer(
    access_token: str,
    config: dict[str, str],
    publish_config: dict[str, str],
    sku: str,
    description: str,
    price: str,
) -> str:
    payload = {
        "sku": sku,
        "marketplaceId": config["marketplace_id"],
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "categoryId": publish_config["category_id"],
        "merchantLocationKey": publish_config["merchant_location_key"],
        "listingPolicies": {
            "fulfillmentPolicyId": publish_config["fulfillment_policy_id"],
            "paymentPolicyId": publish_config["payment_policy_id"],
            "returnPolicyId": publish_config["return_policy_id"],
        },
        "listingDuration": publish_config["listing_duration"],
        "listingDescription": description,
        "pricingSummary": {
            "price": {
                "value": normalize_price_value(price),
                "currency": "USD",
            }
        },
    }

    _, body = ebay_request(
        method="POST",
        url=f"https://{config['api_domain']}/sell/inventory/v1/offer",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": config["content_language"],
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    response_data = json.loads(body)
    offer_id = response_data.get("offerId")
    if not offer_id:
        raise RuntimeError("eBay did not return an offerId for the draft offer.")
    return offer_id


def publish_ebay_offer(
    access_token: str,
    config: dict[str, str],
    offer_id: str,
) -> dict[str, Any]:
    _, body = ebay_request(
        method="POST",
        url=f"https://{config['api_domain']}/sell/inventory/v1/offer/{parse.quote(offer_id)}/publish",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": config["content_language"],
        },
        data=b"{}",
    )
    return json.loads(body) if body.strip() else {}


def get_ebay_inventory_locations(access_token: str, config: dict[str, str]) -> list[dict[str, Any]]:
    _, body = ebay_request(
        method="GET",
        url=f"https://{config['api_domain']}/sell/inventory/v1/location",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": config["content_language"],
        },
    )
    data = json.loads(body)
    return data.get("locations", [])


def get_ebay_policies(
    access_token: str,
    config: dict[str, str],
    policy_type: str,
) -> list[dict[str, Any]]:
    endpoint_map = {
        "fulfillment": "fulfillment_policy",
        "payment": "payment_policy",
        "return": "return_policy",
    }
    endpoint = endpoint_map[policy_type]
    _, body = ebay_request(
        method="GET",
        url=(
            f"https://{config['api_domain']}/sell/account/v1/{endpoint}?"
            f"marketplace_id={parse.quote(config['marketplace_id'])}"
        ),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": config["content_language"],
        },
    )
    data = json.loads(body)
    key_map = {
        "fulfillment": "fulfillmentPolicies",
        "payment": "paymentPolicies",
        "return": "returnPolicies",
    }
    return data.get(key_map[policy_type], [])


def get_ebay_category_suggestions(query: str) -> list[dict[str, Any]]:
    public_config = get_ebay_public_config()
    access_token = get_ebay_application_token(public_config)
    query_string = parse.urlencode({"q": query})
    _, body = ebay_request(
        method="GET",
        url=f"https://{public_config['api_domain']}/commerce/taxonomy/v1/category_tree/0/get_category_suggestions?{query_string}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": public_config["marketplace_id"],
        },
    )
    data = json.loads(body)
    return data.get("categorySuggestions", [])


def create_ebay_draft_listing(
    uploaded_files: list[Any],
    listing_result: dict[str, Any],
    condition: str,
    additional_details: str,
    selected_price_label: str,
) -> dict[str, str]:
    config = get_ebay_config()
    publish_config = get_ebay_publish_config()
    access_token = get_ebay_user_token(config)

    hosted_image_urls = [
        upload_image_to_ebay(
            access_token=access_token,
            uploaded_file=uploaded_file,
            site_id=config["site_id"],
            api_domain=config["api_domain"],
        )
        for uploaded_file in uploaded_files[:MAX_IMAGES]
    ]

    ebay_data = listing_result.get("ebay", {})
    pricing = listing_result.get("pricing", {})
    price_options = build_price_options(pricing)
    selected_price = price_options.get(selected_price_label, "")
    if not selected_price:
        raise RuntimeError(f"No generated price available for {selected_price_label}.")

    sku = f"codex-{uuid.uuid4().hex[:12]}"
    description = format_ebay_description(ebay_data)
    condition_description = build_condition_description(
        visible_condition_clues=listing_result.get("visible_condition_clues", []),
        additional_details=additional_details,
    )

    create_inventory_item(
        access_token=access_token,
        config=config,
        sku=sku,
        title=ebay_data.get("title", listing_result.get("product_name", "Untitled item")),
        description=description,
        brand=listing_result.get("brand", ""),
        condition=CONDITION_TO_EBAY_ENUM[condition],
        condition_description=condition_description,
        image_urls=hosted_image_urls,
    )
    offer_id = create_ebay_offer(
        access_token=access_token,
        config=config,
        publish_config=publish_config,
        sku=sku,
        description=description,
        price=selected_price,
    )

    return {
        "offer_id": offer_id,
        "sku": sku,
        "price_label": selected_price_label,
        "price": selected_price,
    }


def create_and_publish_ebay_listing(
    uploaded_files: list[Any],
    listing_result: dict[str, Any],
    condition: str,
    additional_details: str,
    selected_price_label: str,
) -> dict[str, str]:
    draft_result = create_ebay_draft_listing(
        uploaded_files=uploaded_files,
        listing_result=listing_result,
        condition=condition,
        additional_details=additional_details,
        selected_price_label=selected_price_label,
    )

    config = get_ebay_config()
    access_token = get_ebay_user_token(config)
    publish_response = publish_ebay_offer(
        access_token=access_token,
        config=config,
        offer_id=draft_result["offer_id"],
    )

    listing_id = ""
    listing_ids = publish_response.get("listingId")
    if isinstance(listing_ids, str):
        listing_id = listing_ids
    elif isinstance(listing_ids, list) and listing_ids:
        listing_id = str(listing_ids[0])

    return {
        **draft_result,
        "listing_id": listing_id,
    }


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


def render_comp_table(title: str, comps: list[dict[str, str]]) -> None:
    st.write(f"**{title}**")
    row_html: list[str] = []
    for comp in comps:
        comp_title = comp.get("title", "Untitled listing")
        item_url = comp.get("item_url", "")
        price = comp.get("price", "N/A")
        safe_title = html.escape(comp_title)
        safe_price = html.escape(price)
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

    table_html = f"""
        <style>
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
        """
    row_count = max(len(comps), 1)
    components.html(table_html, height=58 + 48 * row_count, scrolling=False)


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
        if active_listing_pricing.get("query_broadened"):
            st.caption(
                f"Search query was broadened to: {active_listing_pricing.get('query', 'N/A')}."
            )
        if active_listing_pricing.get("comps"):
            render_comp_table("Active Listing Comps", active_listing_pricing["comps"])

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
        try:
            result["ebay_active_listing_pricing"] = fetch_active_listing_prices(
                listing_result=result,
                condition=condition,
                additional_details=additional_details,
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
        include_ebay = st.checkbox("eBay", value=True)
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
