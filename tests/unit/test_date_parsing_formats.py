from app.tools.file_parsers import _parse_date


def test_parse_date_supports_mmddyyyy_slash():
    assert _parse_date("02/28/2026") == "2026-02-28"


def test_parse_date_supports_mmddyyyy_dash():
    assert _parse_date("02-28-2026") == "2026-02-28"


def test_parse_date_supports_yyyy_mm_dd_slash():
    assert _parse_date("2026/02/28") == "2026-02-28"
