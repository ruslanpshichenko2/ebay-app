import base64
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


def get_ebay_public_config() -> dict[str, str]:
    environment = get_env_value("EBAY_ENVIRONMENT", required=False, default="production").lower()
    finding_domain = "svcs.sandbox.ebay.com" if environment == "sandbox" else "svcs.ebay.com"
    app_id = os.getenv("EBAY_APP_ID") or os.getenv("EBAY_CLIENT_ID")
    if not app_id:
        raise RuntimeError("Set EBAY_APP_ID or EBAY_CLIENT_ID to fetch eBay completed-listing prices.")

    return {
        "app_id": app_id,
        "site_id": get_env_value("EBAY_SITE_ID", required=False, default="0"),
        "finding_domain": finding_domain,
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


def compose_completed_listings_query(listing_result: dict[str, Any]) -> str:
    candidates = [
        listing_result.get("brand", ""),
        listing_result.get("model", ""),
        listing_result.get("product_name", ""),
    ]
    parts: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        cleaned = " ".join(str(candidate).split()).strip()
        lowered = cleaned.lower()
        if cleaned and lowered not in seen:
            seen.add(lowered)
            parts.append(cleaned)

    query = " ".join(parts)
    return query[:100].strip()


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


def extract_first(value: Any, default: Any = None) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def fetch_completed_listing_prices(
    listing_result: dict[str, Any],
    condition: str,
) -> dict[str, Any]:
    config = get_ebay_public_config()
    query = compose_completed_listings_query(listing_result)
    if not query:
        raise RuntimeError("Could not build an eBay search query from the detected item.")

    cache_key = f"{query.lower()}::{condition}"
    cache_bucket = st.session_state.setdefault("ebay_completed_pricing_cache", {})
    cached_entry = cache_bucket.get(cache_key)
    now = time.time()
    cache_ttl_seconds = 60 * 30

    if cached_entry and now - cached_entry["timestamp"] < cache_ttl_seconds:
        return cached_entry["data"]

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": FINDING_API_VERSION,
        "SECURITY-APPNAME": config["app_id"],
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "true",
        "keywords": query,
        "paginationInput.entriesPerPage": "15",
        "sortOrder": "EndTimeSoonest",
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "Condition",
        "itemFilter(1).value(0)": CONDITION_TO_COMPLETED_FILTER_ID[condition],
        "outputSelector(0)": "SellerInfo",
    }
    query_string = parse.urlencode(params)
    url = f"https://{config['finding_domain']}/services/search/FindingService/v1?{query_string}"

    try:
        _, body = ebay_request(
            method="GET",
            url=url,
            headers={"X-EBAY-SOA-GLOBAL-ID": "EBAY-US"},
        )
    except RuntimeError as exc:
        if "RateLimiter" in str(exc):
            if cached_entry:
                return cached_entry["data"]
            raise RuntimeError(
                "eBay pricing is temporarily rate-limited. Please wait a few minutes and try again."
            ) from exc
        raise

    data = json.loads(body)

    response = extract_first(data.get("findCompletedItemsResponse"), {})
    ack = extract_first(response.get("ack"), "")
    if ack not in {"Success", "Warning"}:
        errors = response.get("errorMessage", [])
        raise RuntimeError(f"Completed-listings request failed: {errors or 'Unknown error'}")

    search_result = extract_first(response.get("searchResult"), {})
    item_entries = search_result.get("item", [])
    prices: list[float] = []
    comp_rows: list[dict[str, str]] = []

    for item in item_entries:
        selling_status = extract_first(item.get("sellingStatus"), {})
        current_price = extract_first(selling_status.get("currentPrice"), {})
        shipping_info = extract_first(item.get("shippingInfo"), {})
        shipping_cost = extract_first(shipping_info.get("shippingServiceCost"), {})

        item_price = float(current_price.get("__value__", 0) or 0)
        shipping_price = float(shipping_cost.get("__value__", 0) or 0)
        total_price = item_price + shipping_price
        if total_price <= 0:
            continue

        prices.append(total_price)
        comp_rows.append(
            {
                "title": extract_first(item.get("title"), "Untitled listing"),
                "price": format_usd(total_price),
                "item_url": extract_first(item.get("viewItemURL"), ""),
                "sold_date": extract_first(item.get("listingInfo"), {}).get("endTime", ""),
            }
        )

    if not prices:
        raise RuntimeError("No sold completed listings were returned for this item.")

    recommended = {
        "quick_sale_price": format_usd(percentile(prices, 0.25)),
        "market_price": format_usd(statistics.median(prices)),
        "high_end_price": format_usd(percentile(prices, 0.75)),
        "sample_size": len(prices),
        "query": query,
        "average_price": format_usd(sum(prices) / len(prices)),
        "comps": comp_rows[:8],
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
    sku: str,
    description: str,
    price: str,
) -> str:
    payload = {
        "sku": sku,
        "marketplaceId": config["marketplace_id"],
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
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


def create_ebay_draft_listing(
    uploaded_files: list[Any],
    listing_result: dict[str, Any],
    condition: str,
    additional_details: str,
    selected_price_label: str,
) -> dict[str, str]:
    config = get_ebay_config()
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
    st.subheader("Price Range")
    col1, col2, col3 = st.columns(3)
    col1.metric("Quick Sale", format_price_display(pricing.get("quick_sale_price", "")))
    col2.metric("Market Price", format_price_display(pricing.get("market_price", "")))
    col3.metric("High-End", format_price_display(pricing.get("high_end_price", "")))

    show_ebay = listing_type in {"Both", "eBay Only"}
    show_facebook = listing_type in {"Both", "Facebook Only"}

    completed_listing_pricing = result.get("ebay_completed_listing_pricing")
    if show_ebay and completed_listing_pricing:
        st.subheader("eBay Completed Listings Pricing")
        comp_col1, comp_col2, comp_col3 = st.columns(3)
        comp_col1.metric(
            "Quick Sale",
            format_price_display(completed_listing_pricing.get("quick_sale_price", "")),
        )
        comp_col2.metric(
            "Market Price",
            format_price_display(completed_listing_pricing.get("market_price", "")),
        )
        comp_col3.metric(
            "High-End",
            format_price_display(completed_listing_pricing.get("high_end_price", "")),
        )
        st.caption(
            f"Based on {completed_listing_pricing.get('sample_size', 0)} sold completed listings "
            f"for: {completed_listing_pricing.get('query', 'N/A')}. "
            f"Average sold price: {completed_listing_pricing.get('average_price', 'N/A')}."
        )
        if completed_listing_pricing.get("comps"):
            st.write("**Recent Sold Comps**")
            for comp in completed_listing_pricing["comps"]:
                title = comp.get("title", "Untitled listing")
                price = comp.get("price", "N/A")
                item_url = comp.get("item_url", "")
                if item_url:
                    st.markdown(f"- [{title}]({item_url}) - {price}")
                else:
                    st.write(f"- {title} - {price}")

    if show_ebay:
        ebay = result.get("ebay", {})
        ebay_description = format_ebay_description(ebay)

        st.subheader("eBay Listing")
        st.text_area("eBay Title", value=ebay.get("title", ""), height=70, key="ebay_title")
        render_copy_button("Copy eBay Title", ebay.get("title", ""), "ebay-title")
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


def show_ebay_draft_tools(
    uploaded_files: list[Any],
    listing_result: dict[str, Any],
    condition: str,
    additional_details: str,
) -> None:
    st.subheader("eBay Unpublished Offer")
    st.caption(
        "This creates an unpublished eBay offer using your generated eBay copy and uploaded photos."
    )
    st.markdown(f"[Open eBay Seller Hub Drafts]({EBAY_DRAFTS_URL})")

    pricing = listing_result.get("pricing", {})
    price_options = build_price_options(pricing)
    available_labels = [label for label, value in price_options.items() if value]

    if not available_labels:
        st.warning("Generate pricing first before creating an eBay draft.")
        return

    selected_price_label = st.selectbox(
        "Draft price tier",
        options=available_labels,
        index=1 if "Market Price" in available_labels else 0,
        key="ebay_draft_price_tier",
    )

    st.caption(
        "Required environment variables: EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_REFRESH_TOKEN. "
        "Optional: EBAY_ENVIRONMENT, EBAY_MARKETPLACE_ID, EBAY_CONTENT_LANGUAGE, EBAY_SITE_ID."
    )
    st.info(
        "Important: eBay's Inventory API creates an unpublished offer. eBay does not clearly guarantee "
        "that these appear inside the Seller Hub Drafts screen."
    )

    auth_col, draft_col = st.columns(2)
    if auth_col.button("Test eBay Auth", width="stretch"):
        try:
            auth_message = test_ebay_authentication()
            st.session_state["ebay_auth_test_result"] = {"ok": True, "message": auth_message}
        except Exception as exc:
            st.session_state["ebay_auth_test_result"] = {"ok": False, "message": str(exc)}

    if draft_col.button("Create eBay Unpublished Offer", width="stretch"):
        try:
            with st.spinner("Uploading images to eBay and creating an unpublished offer..."):
                draft_result = create_ebay_draft_listing(
                    uploaded_files=uploaded_files,
                    listing_result=listing_result,
                    condition=condition,
                    additional_details=additional_details,
                    selected_price_label=selected_price_label,
                )
            st.session_state["ebay_draft_result"] = draft_result
        except Exception as exc:
            st.error(f"Unable to create the eBay draft: {exc}")

    auth_result = st.session_state.get("ebay_auth_test_result")
    if auth_result:
        if auth_result["ok"]:
            st.success(auth_result["message"])
        else:
            st.error(f"eBay auth test failed: {auth_result['message']}")

    draft_result = st.session_state.get("ebay_draft_result")
    if draft_result:
        st.success(
            "eBay unpublished offer created. "
            f"Offer ID: {draft_result['offer_id']} | SKU: {draft_result['sku']} | "
            f"Price: {draft_result['price']} ({draft_result['price_label']})"
        )


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
        status_text.write("Checking eBay completed listings...")
        try:
            result["ebay_completed_listing_pricing"] = fetch_completed_listing_prices(
                listing_result=result,
                condition=condition,
            )
        except Exception as exc:
            result["ebay_completed_listing_pricing_error"] = str(exc)
        progress_bar.progress(92)

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
        st.subheader("Inputs")
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
        additional_details = st.text_area("Notes / Defects", height=100)
        generate_clicked = st.button("Generate Listing", type="primary", width="stretch")

        if uploaded_files:
            st.write("**Image Preview**")
            render_image_previews(uploaded_files)

    with right_col:
        st.markdown('<div class="panel-label">Generated Outputs</div>', unsafe_allow_html=True)
        st.subheader("Outputs")

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
            completed_listing_pricing_error = saved_result.get("ebay_completed_listing_pricing_error")
            active_listing_type = saved_result.get("selected_listing_type", listing_type)
            if active_listing_type in {"Both", "eBay Only"} and completed_listing_pricing_error:
                st.warning(
                    "Completed-listings pricing could not be loaded from eBay: "
                    f"{completed_listing_pricing_error}"
                )
            show_results(saved_result, active_listing_type)
            if uploaded_files and active_listing_type in {"Both", "eBay Only"}:
                show_ebay_draft_tools(
                    uploaded_files=uploaded_files,
                    listing_result=saved_result,
                    condition=condition,
                    additional_details=additional_details,
                )
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
