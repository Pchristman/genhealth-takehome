import traceback
from datetime import date

import anthropic
import httpx
import pytest

from app.config import settings
from app.decide import decide
from app.extraction_types import ExtractionResult, FlagCode
from app.llm import SYSTEM_PROMPT, LLMError, extract
from app.models import OrderStatus
from app.pdf_intake import read_pdf
from tests import pdf_fixtures as fx
from tests.llm_stub import StubClient, clean_result, evidenced, raw_reply, reply, text_block

PDF = fx.clean_text_pdf()
PARSED = read_pdf(PDF)
TODAY = date(2026, 11, 6)
SECRET = fx.LAST_NAME  # a value from the document, which must never reach an error message

BAD_QUOTE = clean_result(patient_last_name=evidenced(fx.LAST_NAME, "Not on the page"))


def run(client):
    return extract(PDF, PARSED.pages, PARSED.text_pages, client)


def assert_safe(error: BaseException) -> None:
    """Nothing a log or traceback printer could reach may carry document text."""
    assert SECRET not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    assert SECRET not in "".join(traceback.format_exception(error))


def test_request_has_the_pinned_shape():
    client = StubClient(reply(clean_result()))
    run(client)
    call = client.calls[0]

    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 16000
    assert call["system"] == SYSTEM_PROMPT
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == "high"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert not {"thinking", "temperature", "tool_choice", "tools"} & call.keys()


def test_pdf_goes_as_a_base64_document_before_the_text():
    client = StubClient(reply(clean_result()))
    run(client)
    messages = client.calls[0]["messages"]

    assert [message["role"] for message in messages] == ["user"]  # no prefill
    document, text = messages[0]["content"]
    assert document["type"] == "document"
    assert document["source"]["type"] == "base64"
    assert document["source"]["media_type"] == "application/pdf"
    assert text["type"] == "text"


@pytest.mark.parametrize(
    "rule",
    [
        "untrusted",
        "exactly as printed",
        "never infer, repair or complete",
        "absent, illegible or ambiguous",
        "prescriber",
        "referring physician",
        "supplier",
        "fax sender",
        "pdf page",
        "printed fax page",
        "embedded_instructions_found",
        "do not follow",
    ],
)
def test_system_prompt_states_every_rule(rule):
    assert rule in " ".join(SYSTEM_PROMPT.lower().split())


def test_system_prompt_holds_no_fixture_values():
    for value in (fx.FIRST_NAME, fx.LAST_NAME, fx.DOB, fx.PROVIDER, fx.NPI):
        assert value not in SYSTEM_PROMPT


def test_retry_on_quote_problem_then_success():
    first = reply(BAD_QUOTE)
    client = StubClient(first, reply(clean_result()))

    assert run(client) == clean_result()
    assert len(client.calls) == 2

    original, assistant, feedback = client.calls[1]["messages"]
    assert original == client.calls[0]["messages"][0]
    assert assistant == {"role": "assistant", "content": first.content}
    assert assistant["content"] is first.content  # unchanged, thinking blocks and all
    assert feedback["role"] == "user"
    assert "patient_last_name" in str(feedback["content"])
    assert SECRET not in str(feedback["content"])


def test_retry_on_schema_error_then_success():
    client = StubClient(raw_reply(text_block('{"not": "the schema"}')), reply(clean_result()))
    assert run(client) == clean_result()
    assert len(client.calls) == 2


def test_retry_on_missing_text_block_then_success():
    client = StubClient(raw_reply(), reply(clean_result()))
    assert run(client) == clean_result()


def test_no_retry_on_bad_npi():
    # The quote is real. Only the value fails, and a retry would press for an invented one.
    pdf = fx.bad_npi_pdf()
    parsed = read_pdf(pdf)
    bad = clean_result(provider_npi=evidenced(fx.BAD_NPI, f"Prescriber NPI: {fx.BAD_NPI}"))
    client = StubClient(reply(bad))

    assert extract(pdf, parsed.pages, parsed.text_pages, client) == bad
    assert len(client.calls) == 1


def test_no_retry_on_unparseable_date():
    pages = ["Date of Birth: the fifth of Smarch, nineteen sixty-two, printed here"]
    bad = clean_result(
        patient_first_name=evidenced(None, None, None),
        patient_last_name=evidenced(None, None, None),
        provider_name=evidenced(None, None, None),
        provider_npi=evidenced(None, None, None),
        patient_dob=evidenced("the fifth of Smarch", pages[0]),
    )
    client = StubClient(reply(bad))

    assert extract(PDF, pages, {1}, client) == bad
    assert len(client.calls) == 1


def test_gives_up_after_three_attempts_keeps_value_flagged():
    client = StubClient(reply(BAD_QUOTE), reply(BAD_QUOTE), reply(BAD_QUOTE))

    result = run(client)

    assert len(client.calls) == 3
    assert result == BAD_QUOTE
    decision = decide(result, PARSED.pages, PARSED.text_pages, TODAY)
    assert decision.values["patient_last_name"] == fx.LAST_NAME
    assert decision.status == OrderStatus.needs_review
    assert FlagCode.quote_not_found in {flag.code for flag in decision.flags}


def test_attempts_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_attempts", 2)
    client = StubClient(reply(BAD_QUOTE), reply(BAD_QUOTE))
    assert run(client) == BAD_QUOTE
    assert len(client.calls) == 2


def test_schema_errors_on_every_attempt_raise_llm_error():
    # Pydantic quotes the input in its messages, so its text must not travel with the error.
    junk = raw_reply(text_block(f'{{"patient_last_name": "{SECRET}"}}'))
    client = StubClient(junk, junk, junk)

    with pytest.raises(LLMError) as caught:
        run(client)

    assert len(client.calls) == 3
    assert_safe(caught.value)


def test_schema_error_on_the_last_attempt_keeps_the_last_valid_result():
    junk = raw_reply(text_block("not json"))
    client = StubClient(reply(BAD_QUOTE), junk, junk)
    assert run(client) == BAD_QUOTE


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_stop_reason_is_checked_before_reading_output(stop_reason):
    # Parseable text, so only the stop_reason check can catch this.
    client = StubClient(
        raw_reply(text_block(clean_result().model_dump_json()), stop_reason=stop_reason)
    )
    with pytest.raises(LLMError):
        run(client)
    assert len(client.calls) == 1


def test_refusal_raises_llm_error():
    client = StubClient(raw_reply(stop_reason="refusal"))
    with pytest.raises(LLMError) as caught:
        run(client)
    assert "refus" in str(caught.value)
    assert len(client.calls) == 1


def test_a_fallback_turn_reads_the_text_after_the_declined_one():
    # A model that declined can leave partial text before the model that took over answers.
    client = StubClient(
        raw_reply(
            text_block('{"patient_first'),
            type("Fallback", (), {"type": "fallback"})(),
            text_block(clean_result().model_dump_json()),
        )
    )
    assert run(client) == clean_result()
    assert len(client.calls) == 1


def test_api_error_message_contains_no_document_text():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    errors = [
        anthropic.BadRequestError(f"could not process {SECRET}", response=response, body=None),
        anthropic.APIConnectionError(message=f"lost {SECRET}", request=request),
        RuntimeError(f"unexpected {SECRET}"),
    ]
    for error in errors:
        client = StubClient(error)
        with pytest.raises(LLMError) as caught:
            run(client)
        assert_safe(caught.value)
        assert type(error).__name__ in str(caught.value)
        assert len(client.calls) == 1  # an API failure is not the model's to fix


def test_missing_key_raises_llm_error():
    assert settings.anthropic_api_key is None
    with pytest.raises(LLMError):
        extract(PDF, PARSED.pages, PARSED.text_pages)


def test_result_type_is_the_typed_schema():
    assert isinstance(run(StubClient(reply(clean_result()))), ExtractionResult)
