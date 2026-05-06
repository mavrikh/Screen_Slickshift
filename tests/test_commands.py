from app import commands


def test_list_macros_skips_macros_for_other_platforms(monkeypatch) -> None:
    monkeypatch.setattr(
        commands,
        "load_macros",
        lambda: [
            {
                "id": "windows_only",
                "label": "Windows Only",
                "description": "",
                "platforms": ["windows"],
                "command": ["notepad.exe"],
            },
            {
                "id": "all_platforms",
                "label": "All Platforms",
                "description": "",
                "command": ["tool"],
            },
        ],
    )
    monkeypatch.setattr(commands, "platform_allows", lambda platforms: platforms is None)

    macros = commands.list_macros()

    assert [macro.id for macro in macros] == ["all_platforms"]
