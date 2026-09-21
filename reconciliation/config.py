"""Operator-profile and environment configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .errors import ConfigurationError
from .models import OperatorProfile


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_profile(operator_key: str, config_root: Path | None = None) -> OperatorProfile:
    root = config_root or PROJECT_ROOT / "config" / "operators"
    path = root / f"{operator_key}.json"
    if not path.is_file():
        raise ConfigurationError(f"Unknown operator profile: {operator_key}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        discovery = raw["discovery"]
        profile = OperatorProfile(
            operator_key=raw["operator_key"],
            display_name=raw["display_name"],
            website_base_url=raw["website_base_url"].rstrip("/"),
            directory_path=raw["directory_path"],
            crm_api_base_url=raw["crm_api_base_url"].rstrip("/"),
            token_environment_variable=raw["token_environment_variable"],
            parent_account_name=raw["parent_account_name"],
            expected_parent_account_id=raw.get("expected_parent_account_id"),
            minimum_facilities=int(discovery["minimum_facilities"]),
            maximum_facilities=int(discovery["maximum_facilities"]),
            required_directory_pages=(
                int(discovery["required_directory_pages"])
                if discovery.get("required_directory_pages") is not None
                else None
            ),
            care_type_map=dict(raw["care_type_map"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid operator profile {path}: {exc}") from exc
    if profile.operator_key != operator_key:
        raise ConfigurationError("Profile filename and operator_key differ")
    if profile.minimum_facilities < 1 or profile.maximum_facilities < profile.minimum_facilities:
        raise ConfigurationError("Invalid discovery facility bounds")
    return profile


def get_required_token(profile: OperatorProfile) -> str:
    token = os.environ.get(profile.token_environment_variable, "").strip()
    if not token:
        raise ConfigurationError(
            f"Set {profile.token_environment_variable} in the environment; "
            "tokens are never stored in configuration or SQLite."
        )
    return token


def database_path() -> Path:
    default = PROJECT_ROOT / "data" / "reconciliation.sqlite3"
    return Path(os.environ.get("RECONCILIATION_DB", default))
