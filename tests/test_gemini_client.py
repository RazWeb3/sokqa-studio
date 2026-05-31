from app.services.gemini_client import parse_json_response


def test_parse_plain_json_response() -> None:
    assert parse_json_response('{"ok": true}') == {"ok": True}


def test_parse_fenced_json_response() -> None:
    assert parse_json_response('```json\n{"ok": true}\n```') == {"ok": True}
