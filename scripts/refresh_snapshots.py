"""Refresh today's configured snapshots through the protected production API."""

import os

import httpx


BASE_URL = os.getenv("TOPSELLERS_BASE_URL", "https://topsellers-production.up.railway.app")
CATEGORIES = tuple(filter(None, os.getenv("SNAPSHOT_CATEGORIES", "").split(",")))
API_KEY = os.getenv("SNAPSHOT_API_KEY", "")


def main() -> int:
    if not API_KEY or not CATEGORIES:
        raise SystemExit("SNAPSHOT_API_KEY or SNAPSHOT_CATEGORIES is not configured")
    with httpx.Client(timeout=120) as client:
        for category_id in CATEGORIES:
            response = client.post(
                f"{BASE_URL}/api/v1/snapshots/run",
                params={"category_id": category_id},
                headers={"X-Snapshot-Key": API_KEY},
            )
            response.raise_for_status()
            payload = response.json()
            print(category_id, payload["result_count"], "results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
