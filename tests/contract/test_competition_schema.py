import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.competition_contract import (
    CompetitionResponse,
    validate_request,
    validate_response_for_request,
)
from app.domain.time import Interval, TimeOrigin

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/competition"


def official_response():
    return json.loads((FIXTURES / "official_response.json").read_text(encoding="utf-8"))


def test_official_structure_roundtrip():
    value = CompetitionResponse.model_validate(official_response())
    assert set(value.model_dump()) == {
        "overview",
        "cookingTimeline",
        "ingredientsSummary",
        "detailTimeline",
        "recipeDetail",
    }
    assert CompetitionResponse.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"id": "a", "name": "菜"}]},
        [{"id": True, "name": "菜"}],
        [{"id": "a", "name": "菜", "session_id": "invented"}],
        [{"id": "a", "name": "菜"}, {"id": "a", "name": "菜"}],
        [{"id": "a", "name": "别名"}],
        [{"id": 1, "name": "菜"}],
    ],
)
def test_bad_request(payload):
    with pytest.raises((ValueError, ValidationError)):
        validate_request(payload, {"a": "菜"})


def test_duplicate_names_and_explicit_numeric_mapping():
    assert (
        len(
            validate_request(
                [{"id": "a", "name": "麻辣对虾"}, {"id": "b", "name": "麻辣对虾"}],
                {"a": "麻辣对虾", "b": "麻辣对虾"},
            )
        )
        == 2
    )
    assert (
        validate_request([{"id": 1001, "name": "菜"}], {"a": "菜"}, id_mapping={1001: "a"})[0].id
        == 1001
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrapper",
        "extra",
        "invalid_time",
        "out_of_range",
        "sort",
        "missing_parameters",
        "zero_preheat",
        "wrong_recipe",
        "int_parameter_string",
        "step_parameter_number",
        "duplicate_ingredient",
    ],
)
def test_bad_response(mutation):
    value = official_response()
    if mutation == "wrapper":
        value = {"data": value}
    elif mutation == "extra":
        value["debug"] = {}
    elif mutation == "invalid_time":
        value["overview"]["finishTime"] = "25:00"
    elif mutation == "out_of_range":
        value["detailTimeline"][0]["timeInterval"] = "0-999"
    elif mutation == "sort":
        value["detailTimeline"].reverse()
    elif mutation == "missing_parameters":
        value["detailTimeline"][2].pop("parameters")
    elif mutation == "zero_preheat":
        value["detailTimeline"][3]["timeInterval"] = "10-10"
    elif mutation == "wrong_recipe":
        value["detailTimeline"][2]["parameters"][0]["recipeName"] = "不存在"
    elif mutation == "int_parameter_string":
        value["detailTimeline"][2]["parameters"][0]["time"] = "8"
    elif mutation == "step_parameter_number":
        value["recipeDetail"][0]["cookingSteps"][2]["cookingParameters"]["time"] = 20
    else:
        value["ingredientsSummary"][0]["list"].append(
            copy.deepcopy(value["ingredientsSummary"][0]["list"][0])
        )
    with pytest.raises(ValidationError):
        CompetitionResponse.model_validate(value)


def test_cross_day_preserves_full_duration_and_missing_device_parameters():
    payload = official_response()
    payload["overview"].update(finishTime="00:30", timeSpent="1480")
    for timeline in payload["cookingTimeline"]:
        timeline.update(startTime="23:50", endTime="00:30", timeSpent="1480")
    response = CompetitionResponse.model_validate(payload)
    request = validate_request(
        [{"id": "a", "name": "照烧鸡腿"}, {"id": "b", "name": "清炒西兰花"}],
        {"a": "照烧鸡腿", "b": "清炒西兰花"},
    )
    origin = TimeOrigin(start_at=datetime.fromisoformat("2026-09-22T23:50:00+08:00"))
    intervals = (Interval(start_sec=0, end_sec=88800),) * 2
    validate_response_for_request(
        response, request, origin=origin, recipe_intervals=intervals, device_steps=((0, 2), (1, 1))
    )
    payload["recipeDetail"][0]["cookingSteps"][2].pop("cookingParameters")
    with pytest.raises(ValueError, match="设备"):
        validate_response_for_request(
            CompetitionResponse.model_validate(payload),
            request,
            origin=origin,
            recipe_intervals=intervals,
            device_steps=((0, 2), (1, 1)),
        )


def test_official_example_has_known_physical_issue_not_treated_as_schedule_evidence():
    metadata = json.loads((FIXTURES / "source_manifest.json").read_text(encoding="utf-8"))
    assert metadata["source_kind"] == "OFFICIAL_EXAMPLE"
    assert metadata["usable_as_schedule_correctness_evidence"] is False
    assert len(metadata["open_questions"]) == 14
