from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = "ct200_demo"


def post_file(client: httpx.Client, path: Path):
    with path.open("rb") as handle:
        response = client.post(
            f"{BASE}/v1/ingest",
            params={"document_name": DOCUMENT},
            files={"file": (path.name, handle, "application/pdf")},
        )
    response.raise_for_status()
    return response.json()


def main() -> None:
    try:
        with httpx.Client(timeout=60) as client:
            health = client.get(f"{BASE}/health")
            health.raise_for_status()

            v1 = post_file(client, ROOT / "data" / "ct200_manual_v1.pdf")
            v1_id = v1["version_id"]
            document_id = v1["document_id"]
            print(f"v1 ingested: document={document_id}, version={v1['version_number']}, nodes={v1['node_count']}")

            hits = client.get(
                f"{BASE}/v1/search",
                params={"document_id": document_id, "version": 1, "q": "Battery Life"},
            )
            hits.raise_for_status()
            results = hits.json()
            if not results:
                raise RuntimeError("Battery Life node was not found in v1")
            node_id = results[0]["id"]
            print(f"selected v1 node: {node_id}")

            selection = client.post(
                f"{BASE}/v1/selections",
                json={"name": "Battery QA Demo", "document_id": document_id, "node_ids": [node_id]},
            )
            selection.raise_for_status()
            selection_id = selection.json()["id"]
            print(f"selection created: {selection_id}")

            generation = client.post(
                f"{BASE}/v1/generations",
                json={"selection_id": selection_id},
            )
            if generation.status_code >= 400:
                print("GENERATION ERROR:")
                print(generation.text)
                raise RuntimeError(
                 f"Generation failed with HTTP {generation.status_code}"
    )

                generation.raise_for_status()
 
            first_generation = generation.json()
            if first_generation["stale"]:
                raise RuntimeError("generation should be fresh before v2 ingestion")
            if not 3 <= len(first_generation["test_cases"]) <= 5:
                raise RuntimeError("generation did not produce 3-5 test cases")
            print(f"generation created: {first_generation['id']} (stale={first_generation['stale']})")

            v2 = post_file(client, ROOT / "data" / "ct200_manual_v2.pdf")
            print(f"v2 ingested: version={v2['version_number']}, nodes={v2['node_count']}")

            comparison = client.get(f"{BASE}/v1/nodes/{node_id}/compare")
            comparison.raise_for_status()
            comparison = comparison.json()
            if not comparison["changed"]:
                raise RuntimeError("expected the selected battery node to change in v2")
            print(f"comparison: changed={comparison['changed']} status={comparison['status']}")

            retrieved = client.get(
                f"{BASE}/v1/generations",
                params={"selection_id": selection_id},
            )
            retrieved.raise_for_status()
            generations = retrieved.json()
            if not generations or not generations[0]["stale"]:
                raise RuntimeError("expected the old generation to be stale after v2 ingestion")
            print(f"retrieval: stale={generations[0]['stale']}")

            print("END-TO-END DEMO PASSED")
            print(f"v1 version id: {v1_id}")
    except httpx.ConnectError:
        print("API is not running. Start it with: uvicorn app.main:app --reload", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
