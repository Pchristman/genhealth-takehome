"""A stand-in for the Anthropic client, shaped like the one call app.llm makes."""

import copy
from types import SimpleNamespace

from app.extraction_types import Evidenced, ExtractionResult, Issue
from tests import pdf_fixtures as fx


def evidenced(
    value: str | None, quote: str | None, page: int | None = 1, issue: Issue = Issue.none
) -> Evidenced:
    return Evidenced(value=value, source_quote=quote, page=page, issue=issue)


def clean_result(**overrides) -> ExtractionResult:
    """What a perfect read of clean_text_pdf() returns. Override a field to break it."""
    fields = {
        "patient_first_name": evidenced(fx.FIRST_NAME, fx.FIRST_LINE),
        "patient_last_name": evidenced(fx.LAST_NAME, fx.LAST_LINE),
        "patient_dob": evidenced(fx.DOB, fx.DOB_LINE),
        "provider_name": evidenced(fx.PROVIDER, fx.PROVIDER_LINE),
        "provider_npi": evidenced(fx.NPI, fx.NPI_LINE),
        "multiple_patients": False,
        "embedded_instructions_found": False,
    }
    return ExtractionResult(**{**fields, **overrides})


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def raw_reply(*content, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, model="claude-opus-5-5")


def reply(result: ExtractionResult) -> SimpleNamespace:
    return raw_reply(text_block(result.model_dump_json()))


class StubMessages:
    def __init__(self, replies, on_call) -> None:
        self.replies = list(replies)
        self.on_call = on_call
        self.calls: list[dict] = []

    def create(self, **kwargs):
        # A snapshot: extract() adds to its messages list between attempts.
        self.calls.append({**kwargs, "messages": copy.copy(kwargs["messages"])})
        if self.on_call is not None:
            self.on_call()
        answer = self.replies.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class StubClient:
    def __init__(self, *replies, on_call=None) -> None:
        self.beta = SimpleNamespace(messages=StubMessages(replies, on_call))

    @property
    def calls(self) -> list[dict]:
        return self.beta.messages.calls
