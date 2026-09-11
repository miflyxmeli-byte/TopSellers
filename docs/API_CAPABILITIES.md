# Mercado Libre Chile API capabilities

Validation dates: 2026-09-08 to 2026-09-10 (Chile). Site: `MLC`. Documentation sources: official Mercado Libre Developers pages only. Category calls were tested without OAuth; bestsellers and product details were subsequently validated with OAuth through the Railway service.

| Capability | Endpoint | Works? | OAuth? | Data available | Limitations |
|---|---|---:|---:|---|---|
| Site categories | `GET /sites/MLC/categories` | Live `403` | Current docs show bearer token | Root category IDs and names | Blocked without authorization by `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`. |
| Category detail/subcategories | `GET /categories/{category_id}` | Live `200` | Current docs show bearer token, but these probes worked without it | ID, name, hierarchy, children, counts, settings, attributes | Child categories are embedded in `children_categories`; `/categories/{id}/children` is not documented. |
| Complete category tree | `GET /sites/MLC/categories/all` | Live `200` | Not required in this probe | Full MLC tree keyed by category ID | Response is delivered gzip-encoded; clients such as HTTPX decode it automatically. |
| Bestsellers by category | `GET /highlights/MLC/category/{category_id}` | Live `200` with OAuth | Yes | Ranked `ITEM`, `PRODUCT`, or `USER_PRODUCT` identifiers | Only leaf categories may have rankings. `MLC1055` returned 10 current results, all `PRODUCT`. |
| Item detail | `GET /items/{item_id}` | Documented; not reachable from this unauthenticated ranking run | Yes in current docs | Item ID, title, seller ID, category, price, attributes, permalink and pictures | Some fields are visible only to the item owner. |
| Catalog product detail | `GET /products/{product_id}` | Live `200` for all 10 ranked products | Yes | Product ID, title/name, brand, model, attributes and pictures | The tested responses did not supply Item ID, price, seller ID, GTIN, or a useful permalink. A catalog product is not a marketplace offer. |
| Product-level sold quantity | `sold_quantity` in `GET /products/{product_id}` | Not returned for the 10 tested MLC products | Yes | Accumulated quantity when present in other documented examples | The field was absent for all current `MLC1055` bestsellers and cannot be used as a dependable MLC metric. It would not represent the quantity used to calculate ranking. |
| User Product detail | `GET /user-products/{user_product_id}` | Documented; not reachable from this run | Yes | Seller product/variation data | New highlights responses can contain this type; fields and visibility depend on authorization. |
| All marketplace listings associated with an arbitrary catalog product | No general endpoint confirmed in the official Chile documentation reviewed | Not demonstrated | Undetermined | Individual items expose `catalog_product_id`; product search locates catalog products | Do not assume or invent `/products/{id}/items`; this remains an explicit validation gap. |
| Product reviews | `GET /reviews/item/{item_id}` | Documented; not reachable from this run | Yes | `rating_average`, rating levels, paging and reviews | Requires an item ID; review availability varies by category/product. |
| Seller detail | `GET /users/{seller_id}` | Documented; not reachable from this run | Yes | Nickname and seller reputation data | Seller reputation is not product rating. |
| Retrospective bestseller ranking | No documented endpoint | No | N/A | None | **Historical bestseller rankings must be generated internally through periodic snapshots.** |

## Real category probes

The complete MLC tree downloaded on 2026-09-10 contained 12,174 categories:
32 roots, 1,604 intermediate categories and 10,570 leaves, with a maximum depth
of seven levels. Category detail is available for the complete tree. Highlights
can only be requested meaningfully for leaves, and not every leaf is guaranteed
to have a published bestseller list.

| Category | Result | Structure observed |
|---|---:|---|
| `MLC1000` — Electrónica, Audio y Video | `200` | Non-leaf; 16 children |
| `MLC1055` — Celulares y Smartphones | `200` | Leaf; no children |
| `MLC1648` — Computación | `200` | Non-leaf; 21 children |

Additional live results: `/sites/MLC/categories` returned `403` without OAuth; `/sites/MLC/categories/all` returned `200`; `/highlights/MLC/category/MLC1055` returned `401 unspecified_token` without OAuth and `200` after authorization.

## Authenticated bestseller result (`MLC1055`)

| Rank | Product | Product ID | Brand | Model |
|---:|---|---|---|---|
| 1 | Samsung Galaxy A07 LTE 128GB | `MLC62784825` | Samsung | Galaxy A07 LTE 128GB |
| 2 | Moto G86 Power 5G 8+256GB | `MLC66215014` | Motorola | G86 |
| 3 | Xiaomi POCO C85 128GB | `MLC54097181` | Xiaomi | POCO C85 |
| 4 | Motorola Moto G06 128GB | `MLC63043383` | Motorola | Moto G06 |
| 5 | Apple iPhone 17 256GB | `MLC1055308917` | Apple | iPhone 17 |
| 6 | Moto G06 256GB | `MLC55762050` | Motorola | Moto G06 |
| 7 | Apple iPhone 17 Pro Max 256GB | `MLC1055308605` | Apple | iPhone 17 Pro Max |
| 8 | Samsung Galaxy S26 Ultra 256GB | `MLC65503990` | Samsung | Galaxy S26 Ultra |
| 9 | Apple iPhone 17 Pro 256GB | `MLC1055308774` | Apple | iPhone 17 Pro |
| 10 | Samsung Galaxy A37 5G 128GB | `MLC67297951` | Samsung | A37 |

This is a current API snapshot captured on 2026-09-10 (Chile) and must not be interpreted as a historical or stable ranking.

## Requested bestseller fields

The highlights response itself contains only ranking position, ID, and type. The validation script branches by type and attempts the official detail resource. From those resources it extracts, when present: Product ID, Item ID, title, brand, model, GTIN/EAN, price, seller ID, permalink, and image. If an item ID is available, it attempts reviews; if a seller ID is available, it attempts seller detail. Missing data is shown as `-`, never inferred.

A real Product-level table can be produced with OAuth. Price, seller and Item ID were not present in the tested catalog product responses, so a fully offer-level table still requires an officially supported association from each ranked product to marketplace items.

The authenticated revalidation also checked `sold_quantity` and
`buy_box_winner` directly in all 10 product responses. Both were absent in every
case. Consequently, no Item ID, current price, available stock or accumulated
units sold could be derived through the documented product-to-winner chain for
this MLC snapshot.

## Historical ranking conclusion

The official API documents current top-20 highlights and lookups for the current position of a product or item. It does not document a date parameter or snapshot endpoint equivalent to “Top 10 on 2026-01-01.” Historical bestseller rankings must be generated internally through periodic snapshots.

## Technical conclusion

The proposed application is feasible for current category bestsellers. The complete MLC category tree is obtainable and the official highlights resource defines a real bestseller ranking. OAuth application setup and token refresh are mandatory for the central workflow. Once authorized, enrichment must treat `ITEM`, `PRODUCT`, and `USER_PRODUCT` separately and preserve missing values. Retrospective history cannot be reconstructed officially and must begin accumulating after the application starts taking scheduled snapshots.

## Official documentation reviewed

- [Categories and attributes](https://developers.mercadolibre.cl/es_cl/categorias-y-atributos)
- [Bestsellers in Mercado Libre](https://developers.mercadolibre.cl/mas-vendidos-en-mercado-libre)
- [Publish products / item detail](https://developers.mercadolibre.cl/es_cl/publica-productos)
- [Product reviews](https://developers.mercadolibre.cl/en_us/manage-project-apps/products-reviews)
- [Product search](https://developers.mercadolibre.cl/es_cl/buscador-de-productos)
