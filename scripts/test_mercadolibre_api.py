"""Validate official Mercado Libre Chile category and bestseller APIs."""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

SITE_ID = "MLC"
API_BASE_URL = "https://api.mercadolibre.com"
REQUEST_TIMEOUT = 20.0
PROBE_CATEGORIES = ("MLC1000", "MLC1055", "MLC1648")
LOGGER = logging.getLogger("mercadolibre_validation")


@dataclass(frozen=True)
class ProbeResult:
    path: str
    status_code: int | None
    data: Any = None
    error: str | None = None

    @property
    def works(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


def probe(client: httpx.Client, path: str) -> ProbeResult:
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        LOGGER.error("GET %s failed: %s", path, exc)
        return ProbeResult(path, None, error=str(exc))
    print(f"GET {response.request.url} -> {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        data = None
    if not response.is_success:
        message = response.text[:200].replace("\n", " ")
        LOGGER.warning("Endpoint unavailable: %s (%s)", path, message)
        return ProbeResult(path, response.status_code, data, message)
    return ProbeResult(path, response.status_code, data)


def select_category(categories: list[dict[str, Any]], requested_id: str | None) -> dict[str, Any]:
    if requested_id:
        for category in categories:
            if category.get("id") == requested_id:
                return category
        raise ValueError(f"Category {requested_id!r} was not found in {SITE_ID}.")
    if not categories:
        raise ValueError("Mercado Libre returned no categories.")
    return categories[0]


def extract_attribute(resource: dict[str, Any], attribute_id: str) -> str | None:
    for attribute in resource.get("attributes") or []:
        if attribute.get("id") == attribute_id:
            return attribute.get("value_name") or attribute.get("value_id")
    return None


def first_picture(resource: dict[str, Any]) -> str | None:
    pictures = resource.get("pictures") or []
    if pictures and isinstance(pictures[0], dict):
        return pictures[0].get("secure_url") or pictures[0].get("url")
    return resource.get("thumbnail")


def review_count(payload: dict[str, Any]) -> int | None:
    paging = payload.get("paging") or {}
    if isinstance(paging.get("total"), int):
        return paging["total"]
    counts = [v for v in (payload.get("rating_levels") or {}).values() if isinstance(v, int)]
    return sum(counts) if counts else None


def category_from_tree(tree: Any, category_id: str) -> dict[str, Any] | None:
    if isinstance(tree, dict) and category_id in tree:
        candidate = tree[category_id]
        return candidate if isinstance(candidate, dict) else None
    stack = list(tree.values()) if isinstance(tree, dict) else (list(tree) if isinstance(tree, list) else [tree])
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("id") == category_id:
                return node
            stack.extend(node.get("children_categories") or [])
    return None


def enrich_highlight(client: httpx.Client, entry: dict[str, Any]) -> dict[str, Any]:
    entry_id, entry_type = entry.get("id"), entry.get("type")
    paths = {"ITEM": f"/items/{entry_id}", "PRODUCT": f"/products/{entry_id}",
             "USER_PRODUCT": f"/user-products/{entry_id}"}
    result = probe(client, paths[entry_type]) if entry_type in paths else None
    detail = result.data if result and isinstance(result.data, dict) else {}
    item_id = detail.get("id") if entry_type == "ITEM" else detail.get("item_id")
    product_id = detail.get("id") if entry_type == "PRODUCT" else detail.get("catalog_product_id")
    seller_id = detail.get("seller_id")
    reviews: dict[str, Any] = {}
    seller: dict[str, Any] = {}
    if item_id:
        review_result = probe(client, f"/reviews/item/{item_id}")
        reviews = review_result.data if isinstance(review_result.data, dict) else {}
    if seller_id:
        seller_result = probe(client, f"/users/{seller_id}")
        seller = seller_result.data if isinstance(seller_result.data, dict) else {}
    return {"ranking": entry.get("position"), "product_id": product_id, "item_id": item_id,
            "title": detail.get("title") or detail.get("name"),
            "brand": extract_attribute(detail, "BRAND"), "model": extract_attribute(detail, "MODEL"),
            "gtin": extract_attribute(detail, "GTIN") or extract_attribute(detail, "EAN"),
            "price": detail.get("price"), "seller": seller.get("nickname"), "seller_id": seller_id,
            "rating": reviews.get("rating_average"), "reviews": review_count(reviews),
            "permalink": detail.get("permalink"), "image": first_picture(detail)}


def show_rankings(client: httpx.Client, payload: dict[str, Any]) -> None:
    print("\nRanking | Producto | Product ID | Item ID | Marca | Precio")
    for entry in payload.get("content") or []:
        row = enrich_highlight(client, entry)
        price = row["price"] if row["price"] is not None else "-"
        print(f"{row['ranking'] or '-'} | {row['title'] or entry.get('id') or '-'} | "
              f"{row['product_id'] or '-'} | {row['item_id'] or '-'} | {row['brand'] or '-'} | {price}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default="MLC1055", help="Leaf category used for highlights")
    args = parser.parse_args()
    load_dotenv()
    token = os.getenv("MELI_ACCESS_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=API_BASE_URL, headers=headers, timeout=REQUEST_TIMEOUT,
                      follow_redirects=True) as client:
        print(f"Site: {SITE_ID}; OAuth token: {'configured' if token else 'not configured'}")
        category_list = probe(client, f"/sites/{SITE_ID}/categories")
        full_tree = probe(client, f"/sites/{SITE_ID}/categories/all")
        print("\nCategory probes:")
        for category_id in PROBE_CATEGORIES:
            result = probe(client, f"/categories/{category_id}")
            if isinstance(result.data, dict):
                children = result.data.get("children_categories") or []
                print(f"  {category_id}: {result.data.get('name')} ({len(children)} children)")
        selected = category_from_tree(full_tree.data, args.category)
        if selected is None and isinstance(category_list.data, list):
            selected = select_category(category_list.data, args.category)
        if selected is None:
            result = probe(client, f"/categories/{args.category}")
            selected = result.data if isinstance(result.data, dict) else None
        if selected is None:
            LOGGER.error("Unable to resolve category %s", args.category)
            return 1
        print(f"\nHighlights category: {selected.get('id')} - {selected.get('name')}")
        highlights = probe(client, f"/highlights/{SITE_ID}/category/{args.category}")
        if highlights.works and isinstance(highlights.data, dict):
            show_rankings(client, highlights.data)
        elif highlights.status_code == 401 and not token:
            print("A valid OAuth token is required to retrieve the real ranking.")
        else:
            print("No ranking rows were returned; no values were fabricated.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
