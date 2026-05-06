import logging

import pytest

from app import main


@pytest.mark.anyio
async def test_startup_does_not_log_pairing_token(monkeypatch, caplog, capsys) -> None:
    token = "secret-test-token"
    monkeypatch.setattr(main, "ensure_directories", lambda: None)
    monkeypatch.setattr(main, "get_or_create_pairing_token", lambda: token)

    with caplog.at_level(logging.INFO):
        await main.startup()

    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    console_text = capsys.readouterr().out

    assert token not in logged_text
    assert token in console_text
