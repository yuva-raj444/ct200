from __future__ import annotations

import json
import re

import httpx

from .config import settings
from .schemas import TestCase


SYSTEM = """You are a medical-device QA test designer.

Generate concrete, executable QA test-case ideas ONLY from the supplied source text.
Do not invent requirements or values that are not present in the source.

Return ONLY valid JSON matching this structure:

{
  "test_cases": [
    {
      "title": "string",
      "preconditions": ["string"],
      "steps": ["string"],
      "expected_result": "string",
      "source_node_ids": [integer]
    }
  ]
}

Generate 3 to 5 distinct test cases.
Every test case must be specific and traceable to the supplied source_node_ids.
"""


def mock_generate(source: list[dict]) -> dict:
    first = source[0] if source else {"id": 0, "heading": "Source"}

    heading = first["heading"]
    node_id = first["id"]

    return {
        "test_cases": [
            {
                "title": f"Verify normal behavior for {heading}",
                "preconditions": [
                    "Device is available in a safe test setup."
                ],
                "steps": [
                    f"Execute the behavior described under {heading} "
                    "using valid inputs."
                ],
                "expected_result": (
                    "The device follows the stated behavior."
                ),
                "source_node_ids": [node_id],
            },
            {
                "title": f"Verify boundary behavior for {heading}",
                "preconditions": [
                    "Device is available in a safe test setup."
                ],
                "steps": [
                    f"Exercise the boundary or limit described under {heading}."
                ],
                "expected_result": (
                    "The device handles the stated boundary "
                    "according to the supplied document."
                ),
                "source_node_ids": [node_id],
            },
            {
                "title": f"Verify repeatability of {heading}",
                "preconditions": [
                    "Device is available and reset to the documented "
                    "starting state."
                ],
                "steps": [
                    f"Repeat the documented behavior under {heading} "
                    "using the same valid conditions."
                ],
                "expected_result": (
                    "The documented behavior is reproduced consistently."
                ),
                "source_node_ids": [node_id],
            },
        ]
    }


def extract_json(text: str) -> dict:
    """
    Extract JSON from an LLM response.

    Handles:
    - pure JSON
    - JSON surrounded by markdown/code fences
    - extra explanatory text surrounding the JSON
    """

    text = text.strip()

    # First try the response directly.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Remove markdown code fences if present.
    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find the outermost JSON object.
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")

    candidate = cleaned[start : end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM returned invalid JSON: {exc}"
        ) from exc


def validate_payload(
    payload: object,
    source_ids: set[int],
) -> list[TestCase]:
    """
    Validate the LLM response using the application's Pydantic schema.
    """

    cases = (
        payload.get("test_cases")
        if isinstance(payload, dict)
        else None
    )

    if not isinstance(cases, list) or not 3 <= len(cases) <= 5:
        raise ValueError(
            "LLM must return between 3 and 5 test_cases"
        )

    validated: list[TestCase] = []

    for case in cases:
        try:
            test_case = TestCase.model_validate(case)
        except Exception as exc:
            raise ValueError(
                f"Invalid test case structure: {exc}"
            ) from exc

        # Prevent the LLM from claiming traceability to unrelated nodes.
        if not set(test_case.source_node_ids).issubset(source_ids):
            raise ValueError(
                "Test case references a node outside the selection"
            )

        validated.append(test_case)

    return validated


async def generate(source: list[dict]) -> list[TestCase]:
    """
    Generate QA test cases from the selected CT-200 source nodes.

    Supports:
    - mock provider for automated tests
    - Gemini through Google's OpenAI-compatible API

    Gemini output is validated locally before being accepted.
    Invalid output gets one correction/retry attempt.
    """

    source_ids = {
        int(item["id"])
        for item in source
    }

    # ---------------------------------------------------------
    # MOCK PROVIDER
    # ---------------------------------------------------------

    if settings.llm_provider == "mock":
        return validate_payload(
            mock_generate(source),
            source_ids,
        )

    # ---------------------------------------------------------
    # PROVIDER VALIDATION
    # ---------------------------------------------------------

    if settings.llm_provider != "gemini":
        raise ValueError(
            f"Unsupported LLM_PROVIDER: {settings.llm_provider}"
        )

    if not settings.llm_api_key:
        raise ValueError(
            "LLM_API_KEY is required when LLM_PROVIDER=gemini"
        )

    # ---------------------------------------------------------
    # GEMINI REQUEST
    # ---------------------------------------------------------

    messages = [
        {
            "role": "system",
            "content": SYSTEM,
        },
        {
            "role": "user",
            "content": (
                "Generate the QA test cases from the following "
                "selected CT-200 source nodes.\n\n"
                + json.dumps(
                    source,
                    ensure_ascii=False,
                    indent=2,
                )
            ),
        },
    ]

    url = (
        settings.llm_base_url.rstrip("/")
        + "/chat/completions"
    )

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    request_body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {
            "type": "json_object"
        },
        "reasoning_effort": "minimal",
    }

    async with httpx.AsyncClient(timeout=60) as client:

        for attempt in range(2):

            try:
                response = await client.post(
                    url,
                    headers=headers,
                    json=request_body,
                )

            except httpx.RequestError as exc:
                raise ValueError(
                    f"Could not connect to Gemini API: {exc}"
                ) from exc

            # -------------------------------------------------
            # IMPORTANT:
            # Expose the real Gemini error instead of hiding it
            # behind the application's 502 response.
            # -------------------------------------------------

            if response.status_code >= 400:
                error_body = response.text[:2000]

                raise ValueError(
                    f"Gemini API returned "
                    f"{response.status_code}: "
                    f"{error_body}"
                )

            # -------------------------------------------------
            # Parse Gemini's OpenAI-compatible response
            # -------------------------------------------------

            try:
                response_json = response.json()

                content = (
                    response_json["choices"][0]["message"]["content"]
                )

            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Gemini returned an unexpected response format: "
                    f"{response.text[:2000]}"
                ) from exc

            # -------------------------------------------------
            # Validate generated JSON
            # -------------------------------------------------

            try:
                payload = extract_json(content)

                return validate_payload(
                    payload,
                    source_ids,
                )

            except Exception as exc:

                # First invalid response:
                # ask Gemini to correct it.

                if attempt == 0:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous response failed "
                                "application validation.\n\n"
                                f"Validation error: {exc}\n\n"
                                "Return ONLY corrected JSON matching "
                                "the required schema. "
                                "Do not include markdown or explanation."
                            ),
                        }
                    )

                    request_body["messages"] = messages
                    continue

                # Second failure:
                # stop rather than storing malformed output.

                raise ValueError(
                    "LLM output remained invalid after retry: "
                    f"{exc}"
                ) from exc

    raise ValueError("LLM generation failed")