"""Prove the real call against the real API, on the generated scan. Spends two calls.

The first turn is what every upload sends. The second appends `response.content` unchanged, which
is what a retry sends and the stub can't prove. Prints field names and pass/fail, never values.
"""

from app.extraction_types import FIELD_NAMES
from app.llm import build_messages, call_model, check_stop_reason, get_llm_client, read_output
from tests.pdf_fixtures import scan_pdf


def report(label: str, response) -> None:
    check_stop_reason(response)
    result = read_output(response)
    print(label, "stop_reason:", response.stop_reason, "model:", response.model)
    print(label, "block types:", [block.type for block in response.content])
    print(label, "schema:", "pass" if result is not None else "fail")
    if result is not None:
        for name in FIELD_NAMES:
            field = getattr(result, name)
            present = "pass" if field.value and field.source_quote else "fail"
            print(label, name, present, "page", field.page, "issue", field.issue.value)


def main() -> None:
    client = get_llm_client()
    if client is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set.")
    messages = build_messages(scan_pdf())

    first = call_model(client, messages, timeout=120)
    report("turn 1", first)

    messages.append({"role": "assistant", "content": first.content})
    messages.append({"role": "user", "content": "Send the same object again, unchanged."})
    report("turn 2", call_model(client, messages, timeout=120))


if __name__ == "__main__":
    main()
