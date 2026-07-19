"""Core tests: ID generation, pipeline execution, configuration."""

import pytest
from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.core import IdGenerator, Pipeline, StageError, requirement_ids
from qaops.core import test_case_ids as tc_ids


class Payload(BaseModel):
    value: int


class AddOne:
    name = "add_one"

    def run(self, data: Payload) -> Payload:
        return Payload(value=data.value + 1)


class Explode:
    name = "explode"

    def run(self, data: Payload) -> Payload:
        msg = "boom"
        raise RuntimeError(msg)


class TestIdGenerator:
    def test_sequential_zero_padded_ids(self) -> None:
        gen = requirement_ids()
        assert gen.take(3) == ["REQ-001", "REQ-002", "REQ-003"]

    def test_prefixes_are_uppercased(self) -> None:
        assert IdGenerator("tc").next() == "TC-001"

    def test_widths_grow_past_padding(self) -> None:
        gen = IdGenerator("TC", start=999)
        assert gen.next() == "TC-999"
        assert gen.next() == "TC-1000"

    def test_rejects_non_alphabetic_prefix(self) -> None:
        with pytest.raises(ValueError, match="alphabetic"):
            IdGenerator("T C")

    def test_generated_ids_satisfy_model_validation(self) -> None:
        from qaops.models import Requirement

        gen = requirement_ids()
        req = Requirement(id=gen.next(), title="t", description="d")
        assert req.id == "REQ-001"

    def test_independent_generators_do_not_share_state(self) -> None:
        a, b = tc_ids(), tc_ids()
        a.next()
        assert b.next() == "TC-001"


class TestPipeline:
    def test_runs_stages_in_order(self) -> None:
        pipe = Pipeline([AddOne(), AddOne(), AddOne()])
        result = pipe.run(Payload(value=0))
        assert isinstance(result, Payload)
        assert result.value == 3

    def test_stage_names(self) -> None:
        pipe = Pipeline([AddOne(), Explode()])
        assert pipe.stage_names == ["add_one", "explode"]

    def test_wraps_failures_in_stage_error_with_stage_name(self) -> None:
        pipe = Pipeline([AddOne(), Explode()])
        with pytest.raises(StageError, match=r"\[explode\] boom"):
            pipe.run(Payload(value=0))

    def test_rejects_empty_pipeline(self) -> None:
        with pytest.raises(ValueError, match="at least one stage"):
            Pipeline([])


class TestSettings:
    def test_defaults(self) -> None:
        settings = QAOpsSettings()
        assert settings.provider == "anthropic"
        assert settings.llm_retries == 2
        assert "markdown" in settings.default_export_formats

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QAOPS_TEMPERATURE", "0.7")
        monkeypatch.setenv("QAOPS_PROMPT_VERSION", "v2")
        settings = QAOpsSettings()
        assert settings.temperature == 0.7
        assert settings.prompt_version == "v2"

    def test_rejects_unknown_provider(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            QAOpsSettings(provider="openai")

    def test_rejects_unknown_export_format(self) -> None:
        with pytest.raises(ValueError, match="Unknown export formats"):
            QAOpsSettings(default_export_formats=["pdf"])

    def test_rejects_out_of_range_temperature(self) -> None:
        with pytest.raises(ValueError):
            QAOpsSettings(temperature=1.5)
