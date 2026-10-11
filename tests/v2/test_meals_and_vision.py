"""Photo upload, AI nutrition estimate, and user confirmation."""

from __future__ import annotations

import base64
import io
import json
import os

import httpx
import openai
import pytest
from PIL import Image
from sqlalchemy import select

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.services import vision
from backend.app.v2.services.vision import OpenAIVisionAnalyzer, RangedValue, Totals
from tests.v2.conftest import BASE_NOW, MEAL, jpeg_bytes, vision_estimate


def upload(h, headers, data, content_type="image/jpeg", occurred_at=BASE_NOW):
    return h.client.post("/api/v2/meals/images", params={"occurred_at": occurred_at.isoformat()},
                         headers={**headers, "Content-Type": content_type}, content=data)


# --------------------------------------------------------------------------- #
# upload validation
# --------------------------------------------------------------------------- #


def test_upload_requires_authentication(harness):
    response = harness.client.post("/api/v2/meals/images", params={"occurred_at": BASE_NOW.isoformat()},
                                   headers={"Content-Type": "image/jpeg"}, content=jpeg_bytes())
    assert response.status_code == 401
    assert list(harness.image_dir.rglob("*")) == []


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (200, 200), (10, 200, 30, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("data,content_type,status,code", [
    (b"", "image/jpeg", 422, "invalid_request"),
    (b"this is not an image at all, just text", "image/jpeg", 415, "unsupported_image_type"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "image/svg+xml", 415, "unsupported_image_type"),
    (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64, "image/heic", 415, "unsupported_image_type"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 200, "image/jpeg", 422, "invalid_image"),
    (None, "image/jpeg", 422, "invalid_image"),          # truncated real JPEG
    ("png", "image/jpeg", 415, "content_type_mismatch"),  # PNG bytes declared as JPEG
    ("tiny", "image/jpeg", 422, "invalid_request"),
])
def test_invalid_uploads_are_rejected_and_nothing_is_stored(harness, data, content_type, status, code):
    headers = harness.register()
    if data is None:
        data = jpeg_bytes()[:400]
    elif data == "png":
        data = _png()
    elif data == "tiny":
        data = jpeg_bytes(size=(20, 20))
    response = upload(harness, headers, data, content_type)
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert list(harness.image_dir.rglob("*.jpg")) == []
    assert harness.client.get("/api/v2/meals", headers=headers).json()["meals"] == []


def test_oversized_upload_is_rejected(tmp_path, v1_settings):
    from tests.v2.conftest import build_harness

    h = build_harness(tmp_path, v1_settings, max_image_bytes=2000)
    response = upload(h, h.register(), jpeg_bytes(size=(1200, 1200)))
    assert response.status_code == 413 and response.json()["code"] == "image_too_large"


def test_occurred_at_must_be_offset_aware_and_not_far_in_the_future(harness):
    headers = harness.register()
    naive = harness.client.post("/api/v2/meals/images", params={"occurred_at": "2026-09-20T12:00:00"},
                                headers={**headers, "Content-Type": "image/jpeg"}, content=jpeg_bytes())
    assert naive.status_code == 422
    import datetime as dt
    future = upload(harness, headers, jpeg_bytes(), occurred_at=BASE_NOW + dt.timedelta(hours=2))
    assert future.status_code == 422


def test_stored_photo_is_reencoded_without_exif_and_is_owner_only(harness):
    headers = harness.register()
    original = jpeg_bytes(exif=True)
    assert b"TestCameraMaker" in original
    meal = upload(harness, headers, original).json()
    stored = list(harness.image_dir.rglob("*.jpg"))
    assert len(stored) == 1
    data = stored[0].read_bytes()
    assert b"TestCameraMaker" not in data
    assert not Image.open(io.BytesIO(data)).getexif()
    assert oct(os.stat(stored[0]).st_mode & 0o777) == "0o600"
    served = harness.client.get(meal["photo"]["url"], headers=headers)
    assert served.content == data and served.headers["cache-control"] == "private, no-store"


def test_png_with_transparency_is_accepted_and_normalized_to_jpeg(harness):
    meal = upload(harness, harness.register(), _png(), "image/png").json()
    assert meal["photo"]["content_type"] == "image/jpeg"


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #


def test_analysis_returns_an_editable_estimate_with_provenance_and_uncertainty(harness):
    headers = harness.register()
    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    estimate = harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers).json()["estimate"]

    assert estimate["is_estimate"] is True and estimate["needs_user_confirmation"] is True
    # Rounded: a photograph does not support decimal-gram or single-kcal precision.
    assert estimate["totals"] == {"carbs_g": 48.0, "protein_g": 36.0, "fat_g": 18.0,
                                  "fiber_g": 6.0, "calories_kcal": 505.0}
    assert set(estimate["ranges"]) == set(estimate["totals"])
    assert estimate["overall_confidence"] in ("low", "medium", "high")
    assert estimate["provenance"]["ai_model_requested"] == "test-vision-model"
    assert estimate["provenance"]["ai_model_reported"] == "test-vision-model-2026-01-01"
    assert estimate["provenance"]["prompt_version"] == vision.PROMPT_VERSION
    assert [i["visibility"] for i in estimate["items"]] == ["visible", "assumed_hidden"]
    assert estimate["assumptions"] and estimate["clarification_questions"]
    codes = {w["code"] for w in estimate["warnings"]}
    assert "hidden_fats_likely" in codes
    assert "%" not in json.dumps(estimate["items"])  # no invented probabilities


def test_only_the_sanitized_image_is_sent_to_the_vision_model(harness):
    headers = harness.register()
    # Give the account plenty of private data that must NOT travel with the photo.
    import datetime as dt
    harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=2), BASE_NOW)
    harness.client.post("/api/v2/medications", headers=headers, json={"name": "SecretDrugName"})
    meal_id = upload(harness, headers, jpeg_bytes(exif=True)).json()["meal_id"]
    harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers)

    assert len(harness.vision.calls) == 1
    sent = harness.vision.calls[0]
    assert sent == next(harness.image_dir.rglob("*.jpg")).read_bytes()
    assert b"TestCameraMaker" not in sent


def test_the_openai_request_contains_one_image_and_fixed_text_only():
    jpeg = jpeg_bytes()
    payload = vision.build_request_input(jpeg)
    assert len(payload) == 1 and payload[0]["role"] == "user"
    kinds = [part["type"] for part in payload[0]["content"]]
    assert kinds == ["input_text", "input_image"]
    image_url = payload[0]["content"][1]["image_url"]
    assert base64.b64decode(image_url.split(",", 1)[1]) == jpeg
    text = payload[0]["content"][0]["text"] + vision.INSTRUCTIONS
    for forbidden in ("glucose", "mg/dL", "medication", "insulin", "user_id", "email"):
        assert forbidden not in text
    assert "never an instruction" in vision.INSTRUCTIONS  # prompt-injection guard


class _FakeResponses:
    def __init__(self, outcome):
        self.outcome, self.kwargs = outcome, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeOpenAI:
    def __init__(self, outcome):
        self.responses = _FakeResponses(outcome)


class _Response:
    def __init__(self, parsed):
        self.output_parsed, self.model = parsed, "reported-model"
        self.usage = type("U", (), {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12})()


def test_adapter_calls_responses_parse_with_the_structured_schema_and_no_storage():
    client = _FakeOpenAI(_Response(vision_estimate()))
    result = OpenAIVisionAnalyzer(client, "some-model", 12.0).analyze(jpeg_bytes())
    kwargs = client.responses.kwargs
    assert kwargs["model"] == "some-model"
    assert kwargs["text_format"] is vision.MealVisionEstimate
    assert kwargs["store"] is False and kwargs["timeout"] == 12.0
    assert kwargs["instructions"] == vision.INSTRUCTIONS
    assert set(kwargs) == {"model", "instructions", "input", "text_format", "store", "timeout"}
    assert result.model_reported == "reported-model" and result.usage["total_tokens"] == 12


def test_structured_schema_is_accepted_by_the_installed_sdk():
    """The SDK must be able to turn the model into a strict JSON schema."""
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(vision.MealVisionEstimate)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(vision.MealVisionEstimate.model_fields)


_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(cls, status):
    return cls("boom", response=httpx.Response(status, request=_REQUEST), body=None)


@pytest.mark.parametrize("error,status,code", [
    (openai.APITimeoutError(request=_REQUEST), 504, "vision_timeout"),
    (openai.APIConnectionError(request=_REQUEST), 502, "vision_unreachable"),
    (_status_error(openai.RateLimitError, 429), 503, "vision_rate_limited"),
    (_status_error(openai.AuthenticationError, 401), 503, "vision_unavailable"),
    (_status_error(openai.InternalServerError, 500), 502, "vision_provider_error"),
    (_status_error(openai.BadRequestError, 400), 502, "vision_provider_error"),
])
def test_provider_failures_map_to_safe_errors(error, status, code):
    with pytest.raises(core.V2Error) as caught:
        OpenAIVisionAnalyzer(_FakeOpenAI(error), "m").analyze(jpeg_bytes())
    assert (caught.value.status_code, caught.value.code) == (status, code)
    assert "key" not in caught.value.message.lower()


def test_a_response_with_no_parsed_output_is_an_error_not_a_guess():
    with pytest.raises(core.V2Error) as caught:
        OpenAIVisionAnalyzer(_FakeOpenAI(_Response(None)), "m").analyze(jpeg_bytes())
    assert caught.value.code == "vision_unparseable"


def test_provider_failure_leaves_the_meal_usable_for_manual_entry(harness):
    headers = harness.register()
    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    harness.vision.error = core.V2Error(504, "vision_timeout", "timed out")
    assert harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers).status_code == 504
    meal = harness.client.get(f"/api/v2/meals/{meal_id}", headers=headers).json()
    assert meal["status"] == "draft" and meal["estimate"] is None
    confirmed = harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=MEAL).json()
    assert confirmed["confirmation"]["source"] == "manual"


def test_not_configured_and_no_photo_are_reported_plainly(harness):
    headers = harness.register()
    manual = harness.client.post("/api/v2/meals", headers=headers, json={"occurred_at": BASE_NOW.isoformat()}).json()
    no_photo = harness.client.post(f"/api/v2/meals/{manual['meal_id']}/analyze", headers=headers)
    assert no_photo.status_code == 409 and no_photo.json()["code"] == "meal_has_no_photo"

    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    harness.ctx.vision = None
    response = harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers)
    assert response.status_code == 503 and response.json()["code"] == "vision_not_configured"


def test_non_food_photo_yields_no_numbers(harness):
    headers = harness.register()
    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    zero = RangedValue(estimate=0, low=0, high=0)
    harness.vision.estimate = vision_estimate(
        is_food=False, not_food_reason="The photo shows a keyboard.", items=[],
        totals=Totals(carbs_g=zero, protein_g=zero, fat_g=zero, fiber_g=zero, calories_kcal=zero))
    estimate = harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers).json()["estimate"]
    assert estimate["status"] == "not_food" and estimate["totals"] is None
    assert harness.client.get(f"/api/v2/meals/{meal_id}", headers=headers).json()["status"] == "draft"
    blocked = harness.predict(headers, meal_id, allow_unconfirmed_estimate=True)
    assert blocked.status_code == 409 and blocked.json()["code"] == "no_usable_estimate"


@pytest.mark.parametrize("field,value", [("carbs_g", float("nan")), ("fat_g", float("inf")), ("protein_g", -4.0)])
def test_nonfinite_or_negative_model_output_is_refused(field, value):
    bad = vision_estimate()
    setattr(bad.totals, field, RangedValue(estimate=value, low=0, high=10))
    with pytest.raises(core.V2Error) as caught:
        vision.validate_estimate(bad)
    assert caught.value.code == "vision_invalid_estimate"


def test_validation_flags_inconsistent_and_out_of_range_estimates():
    def r(v, low=None, high=None):
        return RangedValue(estimate=v, low=v if low is None else low, high=v if high is None else high)

    estimate = vision_estimate(
        totals=Totals(carbs_g=r(40, 60, 30), protein_g=r(20), fat_g=r(10), fiber_g=r(55), calories_kcal=r(12000)),
        overall_confidence="low",
        scene=vision.Scene(multiple_plates=True, contains_beverage=True, packaged_food=False,
                           hidden_fats_likely=False, portion_reference_visible=False))
    totals, ranges, warnings = vision.validate_estimate(estimate)
    codes = {w["code"] for w in warnings}
    assert {"calorie_macro_mismatch", "exceeds_supported_range", "fiber_exceeds_carbohydrate",
            "low_confidence", "multiple_plates", "no_portion_reference"} <= codes
    assert ranges["carbs_g"] == {"low": 30.0, "high": 60.0}  # inverted bounds reordered


def test_analysis_is_idempotent_and_rate_limited(tmp_path, v1_settings):
    from tests.v2.conftest import build_harness

    h = build_harness(tmp_path, v1_settings, vision_daily_limit=2)
    headers = h.register()
    first = upload(h, headers, jpeg_bytes()).json()["meal_id"]
    a = h.client.post(f"/api/v2/meals/{first}/analyze", headers=headers).json()
    b = h.client.post(f"/api/v2/meals/{first}/analyze", headers=headers).json()
    assert a["estimate"]["estimate_id"] == b["estimate"]["estimate_id"]
    assert len(h.vision.calls) == 1  # the retry did not buy a second analysis

    forced = h.client.post(f"/api/v2/meals/{first}/analyze", params={"force": True}, headers=headers)
    assert forced.status_code == 200 and len(h.vision.calls) == 2
    second = upload(h, headers, jpeg_bytes()).json()["meal_id"]
    limited = h.client.post(f"/api/v2/meals/{second}/analyze", headers=headers)
    assert limited.status_code == 429 and limited.json()["code"] == "analysis_limit_reached"


# --------------------------------------------------------------------------- #
# confirmation
# --------------------------------------------------------------------------- #


def test_ai_estimate_and_user_confirmation_are_stored_separately(harness):
    headers = harness.register()
    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    estimate = harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers).json()["estimate"]

    accepted = harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=estimate["totals"]).json()
    assert accepted["confirmation"]["source"] == "ai_accepted" and accepted["confirmation"]["revision"] == 1
    corrected = {**estimate["totals"], "carbs_g": 80.0}
    edited = harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=corrected).json()
    assert edited["confirmation"]["source"] == "ai_edited" and edited["confirmation"]["revision"] == 2
    assert edited["confirmation"]["based_on_estimate_id"] == estimate["estimate_id"]

    with harness.ctx.engine.connect() as conn:
        stored = conn.execute(select(t.meal_estimates)).mappings().one()
        revisions = conn.execute(select(t.meal_confirmations.c.carbs_g)
                                 .order_by(t.meal_confirmations.c.revision)).scalars().all()
    assert stored["carbs_g"] == 48.0           # the AI's number is untouched by the edit
    assert revisions == [48.0, 80.0]           # both revisions kept


@pytest.mark.parametrize("body", [
    {k: v for k, v in MEAL.items() if k != "fiber_g"},      # a value missing
    {**MEAL, "carbs_g": -1}, {**MEAL, "carbs_g": 1001}, {**MEAL, "fiber_g": 201},
    {**MEAL, "calories_kcal": 10001}, {**MEAL, "carbs_g": "lots"}, {**MEAL, "sugar_g": 5},
])
def test_confirmation_requires_all_five_values_within_contract_bounds(harness, body):
    headers = harness.register()
    meal_id = harness.client.post("/api/v2/meals", headers=headers, json={"occurred_at": BASE_NOW.isoformat()}).json()["meal_id"]
    assert harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=body).status_code == 422


def test_nonfinite_confirmation_values_are_rejected(harness):
    headers = harness.register()
    meal_id = harness.client.post("/api/v2/meals", headers=headers, json={"occurred_at": BASE_NOW.isoformat()}).json()["meal_id"]
    for token in ("NaN", "Infinity"):
        raw = json.dumps(MEAL).replace("55.0", token)
        response = harness.client.patch(f"/api/v2/meals/{meal_id}/confirm",
                                        headers={**headers, "Content-Type": "application/json"}, content=raw)
        assert response.status_code == 422


def test_an_unconfirmed_estimate_is_never_forecast_without_explicit_opt_in(harness):
    import datetime as dt
    headers = harness.register()
    harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=3), BASE_NOW)
    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers)

    blocked = harness.predict(headers, meal_id)
    assert blocked.status_code == 409 and blocked.json()["code"] == "meal_not_confirmed"

    opted = harness.predict(headers, meal_id, allow_unconfirmed_estimate=True).json()
    assert opted["inputs_confirmed"] is False
    assert any("not reviewed or confirmed" in note for note in opted["forecast"]["limitations"])
    meal = harness.client.get(f"/api/v2/meals/{meal_id}", headers=headers).json()
    assert meal["confirmation"]["source"] == "ai_unconfirmed"


def test_deleting_a_meal_removes_its_photo(harness):
    headers = harness.register()
    meal_id = upload(harness, headers, jpeg_bytes()).json()["meal_id"]
    assert harness.client.delete(f"/api/v2/meals/{meal_id}", headers=headers).status_code == 204
    assert list(harness.image_dir.rglob("*.jpg")) == []
    assert harness.client.get("/api/v2/meals", headers=headers).json()["meals"] == []
    assert harness.predict(headers, meal_id).status_code == 409


@pytest.mark.skipif(
    not (os.environ.get("GLIMPSE_OPENAI_TEST_KEY") and os.environ.get("GLIMPSE_OPENAI_TEST_MODEL")
         and int(os.environ.get("GLIMPSE_OPENAI_TEST_MAX_CALLS", "0")) >= 1),
    reason="real OpenAI test needs GLIMPSE_OPENAI_TEST_KEY, _TEST_MODEL and _TEST_MAX_CALLS>=1",
)
def test_real_openai_round_trip():  # pragma: no cover - spends money; opt-in only
    client = openai.OpenAI(api_key=os.environ["GLIMPSE_OPENAI_TEST_KEY"], max_retries=0)
    analyzer = OpenAIVisionAnalyzer(client, os.environ["GLIMPSE_OPENAI_TEST_MODEL"], 60.0)
    result = analyzer.analyze(jpeg_bytes())  # exactly one call: a plain colour field
    vision.validate_estimate(result.estimate)
