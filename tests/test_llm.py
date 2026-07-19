"""LLM layer tests: models, mock client, structured output retry loop,
and prompt loader. Everything here runs offline (ADR-008)."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from qaops.core.errors import ConfigurationError
from qaops.llm import (
    LLMClient,
    LLMMessage,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMResponseFormatError,
    LLMUsage,
    MockLLMClient,
    PromptLoader,
    extract_json_payload,
    generate_structured,
)


class Animal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    legs: int


def make_request(prompt: str = "Describe a cat as JSON.") -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content=prompt)])


class TestLLMModels:
    def test_request_defaults(self) -> None:
        req = make_request()
        assert req.temperature == 0.2
        assert req.system == ""

    def test_request_requires_a_message(self) -> None:
        with pytest.raises(ValidationError):
            LLMRequest(messages=[])

    def test_request_rejects_out_of_range_temperature(self) -> None:
        with pytest.raises(ValidationError):
            LLMRequest(messages=[LLMMessage(role="user", content="x")], temperature=1.5)

    def test_with_feedback_appends_and_does_not_mutate(self) -> None:
        req = make_request()
        repaired = req.with_feedback('{"broken', "Expecting value")
        assert len(req.messages) == 1  # original untouched
        assert len(repaired.messages) == 3
        assert repaired.messages[1].role == "assistant"
        assert '{"broken' in repaired.messages[1].content
        assert "Expecting value" in repaired.messages[2].content

    def test_usage_total(self) -> None:
        assert LLMUsage(input_tokens=10, output_tokens=5).total_tokens == 15


class TestMockLLMClient:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(MockLLMClient(), LLMClient)

    def test_replays_script_in_order_and_records_requests(self) -> None:
        mock = MockLLMClient(["first", "second"])
        assert mock.complete(make_request("a")).text == "first"
        assert mock.complete(make_request("b")).text == "second"
        assert mock.call_count == 2
        assert mock.requests[1].messages[0].content == "b"

    def test_raises_scripted_exception(self) -> None:
        mock = MockLLMClient([LLMProviderError("mock", "rate limited")])
        with pytest.raises(LLMProviderError, match="rate limited"):
            mock.complete(make_request())

    def test_returns_full_response_objects_verbatim(self) -> None:
        resp = LLMResponse(
            text="hi",
            model="custom",
            usage=LLMUsage(input_tokens=3, output_tokens=1),
            stop_reason="max_tokens",
        )
        mock = MockLLMClient([resp])
        assert mock.complete(make_request()) is resp

    def test_exhausted_script_fails_loudly(self) -> None:
        mock = MockLLMClient(["only one"])
        mock.complete(make_request())
        with pytest.raises(LLMProviderError, match="exhausted"):
            mock.complete(make_request())


class TestExtractJsonPayload:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"name": "cat", "legs": 4}',
            '```json\n{"name": "cat", "legs": 4}\n```',
            '```\n{"name": "cat", "legs": 4}\n```',
            'Here is the JSON you asked for:\n{"name": "cat", "legs": 4}\nHope that helps!',
        ],
    )
    def test_extracts_object_from_common_wrappings(self, raw: str) -> None:
        assert extract_json_payload(raw) == '{"name": "cat", "legs": 4}'

    def test_extracts_arrays(self) -> None:
        assert extract_json_payload("Result: [1, 2, 3] done") == "[1, 2, 3]"

    def test_returns_stripped_text_when_no_json_found(self) -> None:
        assert extract_json_payload("  no json here  ") == "no json here"


class TestGenerateStructured:
    def test_success_on_first_attempt(self) -> None:
        mock = MockLLMClient(['{"name": "cat", "legs": 4}'])
        result = generate_structured(mock, make_request(), Animal)
        assert result == Animal(name="cat", legs=4)
        assert mock.call_count == 1

    def test_retry_sends_repair_feedback_then_succeeds(self) -> None:
        mock = MockLLMClient(["not json at all", '{"name": "cat", "legs": 4}'])
        result = generate_structured(mock, make_request(), Animal, retries=2)
        assert result.legs == 4
        assert mock.call_count == 2
        retry_messages = mock.requests[1].messages
        assert retry_messages[1].content == "not json at all"  # failed output echoed
        assert "ONLY valid JSON" in retry_messages[2].content

    def test_schema_violation_triggers_retry(self) -> None:
        # valid JSON, but extra="forbid" rejects the hallucinated field (ADR-003)
        mock = MockLLMClient(
            [
                '{"name": "cat", "legs": 4, "hallucinated": true}',
                '{"name": "cat", "legs": 4}',
            ]
        )
        result = generate_structured(mock, make_request(), Animal)
        assert result.name == "cat"
        assert mock.call_count == 2

    def test_exhausted_retries_raise_with_all_raw_responses(self, tmp_path: Path) -> None:
        mock = MockLLMClient(["bad1", "bad2", "bad3"])
        with pytest.raises(LLMResponseFormatError) as excinfo:
            generate_structured(
                mock, make_request(), Animal, retries=2, failure_dir=tmp_path / "failures"
            )
        err = excinfo.value
        assert err.attempts == 3
        assert err.raw_responses == ["bad1", "bad2", "bad3"]
        written = sorted(p.name for p in (tmp_path / "failures").iterdir())
        assert len(written) == 3
        assert all(name.startswith("Animal_") for name in written)

    def test_zero_retries_means_single_attempt(self) -> None:
        mock = MockLLMClient(["bad"])
        with pytest.raises(LLMResponseFormatError):
            generate_structured(mock, make_request(), Animal, retries=0)
        assert mock.call_count == 1

    def test_provider_errors_propagate_unchanged(self) -> None:
        mock = MockLLMClient([LLMProviderError("mock", "boom")])
        with pytest.raises(LLMProviderError, match="boom"):
            generate_structured(mock, make_request(), Animal)


PROMPT_BODY = 'Analyze the following requirement:\n$requirement\n\nExample output: {"id": 1}\n'


@pytest.fixture
def prompt_dir(tmp_path: Path) -> Path:
    (tmp_path / "analyzer_v1.md").write_text(PROMPT_BODY)
    (tmp_path / "analyzer_v2.md").write_text("v2: $requirement")
    return tmp_path


class TestPromptLoader:
    def test_load_returns_raw_template(self, prompt_dir: Path) -> None:
        loader = PromptLoader(prompt_dir)
        assert loader.load("analyzer") == PROMPT_BODY

    def test_render_substitutes_variables_and_preserves_json_braces(self, prompt_dir: Path) -> None:
        loader = PromptLoader(prompt_dir)
        rendered = loader.render("analyzer", requirement="Users can reset passwords.")
        assert "Users can reset passwords." in rendered
        assert '{"id": 1}' in rendered  # braces survive (ADR-010)

    def test_version_switch_selects_different_file(self, prompt_dir: Path) -> None:
        loader = PromptLoader(prompt_dir, version="v2")
        assert loader.render("analyzer", requirement="x") == "v2: x"

    def test_missing_template_lists_available(self, prompt_dir: Path) -> None:
        loader = PromptLoader(prompt_dir, version="v9")
        with pytest.raises(ConfigurationError, match="analyzer_v1.md"):
            loader.load("analyzer")

    def test_missing_variable_fails_loudly(self, prompt_dir: Path) -> None:
        loader = PromptLoader(prompt_dir)
        with pytest.raises(ConfigurationError, match="missing variables.*requirement"):
            loader.render("analyzer")

    def test_unknown_variable_fails_loudly(self, prompt_dir: Path) -> None:
        loader = PromptLoader(prompt_dir)
        with pytest.raises(ConfigurationError, match="unknown variables.*reqirement"):
            loader.render("analyzer", requirement="x", reqirement="typo")

    def test_invalid_version_rejected(self, prompt_dir: Path) -> None:
        with pytest.raises(ConfigurationError, match="Invalid prompt version"):
            PromptLoader(prompt_dir, version="../../etc")
