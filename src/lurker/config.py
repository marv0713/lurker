from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def load_themes(path: str | Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    themes = data.get("themes", [])
    if not isinstance(themes, list) or not themes:
        raise ValueError("themes.yaml must contain a non-empty themes list")
    return themes


def load_markets(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)


def load_scoring(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)
