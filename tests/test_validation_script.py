import httpx

from scripts.test_mercadolibre_api import (
    PROBE_CATEGORIES, SITE_ID, category_from_tree, extract_attribute,
    first_picture, probe, review_count, select_category,
)


def test_site_and_three_probe_categories_are_chilean():
    assert SITE_ID == "MLC"
    assert len(PROBE_CATEGORIES) >= 3
    assert all(category.startswith("MLC") for category in PROBE_CATEGORIES)


def test_select_category_by_id_and_default():
    categories = [{"id": "MLC123"}, {"id": "MLC456"}]
    assert select_category(categories, "MLC456") == categories[1]
    assert select_category(categories, None) == categories[0]


def test_resource_helpers():
    item = {"attributes": [{"id": "BRAND", "value_name": "Example"}],
            "pictures": [{"secure_url": "https://example.test/image.jpg"}]}
    assert extract_attribute(item, "BRAND") == "Example"
    assert extract_attribute(item, "MODEL") is None
    assert first_picture(item) == "https://example.test/image.jpg"


def test_review_count():
    assert review_count({"paging": {"total": 9}, "rating_levels": {"five": 3}}) == 9
    assert review_count({"rating_levels": {"five": 3, "four": 2}}) == 5
    assert review_count({}) is None


def test_category_lookup_walks_tree():
    tree = [{"id": "MLC1", "children_categories": [{"id": "MLC2", "name": "Leaf"}]}]
    assert category_from_tree(tree, "MLC2")["name"] == "Leaf"
    assert category_from_tree(tree, "missing") is None
    downloaded_tree = {"MLC2": {"id": "MLC2", "name": "Downloaded leaf"}}
    assert category_from_tree(downloaded_tree, "MLC2")["name"] == "Downloaded leaf"


def test_probe_records_success_and_http_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        status = 200 if request.url.path == "/ok" else 401
        return httpx.Response(status, json={"status": status}, request=request)
    with httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler)) as client:
        success, failure = probe(client, "/ok"), probe(client, "/private")
    assert success.works and success.data == {"status": 200}
    assert not failure.works and failure.status_code == 401
