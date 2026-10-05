from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import REPO_ROOT_ENV_FILE, Settings


def test_env_file_resolves_to_repository_root():
    repo_root = Path(__file__).resolve().parents[2]
    assert repo_root / ".env" == REPO_ROOT_ENV_FILE
    assert Settings.model_config["env_file"] == REPO_ROOT_ENV_FILE


def test_root_env_file_is_read_independent_of_cwd(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ENVIRONMENT=STAGING\nDEBUG=true\nPOSTGRES_USER=ignored\n", encoding="utf-8")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.chdir(tmp_path / "..")

    settings = Settings(_env_file=env_file)

    assert settings.environment == "STAGING"
    assert settings.debug is True


@pytest.mark.parametrize("environment", ["DEV", "STAGING", "PROD"])
def test_configured_cursor_key_requires_32_bytes(environment):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment=environment, cursor_signing_key="short")


def test_production_requires_shared_secret_and_redacts_it(monkeypatch):
    monkeypatch.delenv("CURSOR_SIGNING_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="PROD")
    secret = "cursor-secret-for-all-api-workers-32-bytes"
    config = Settings(_env_file=None, environment="PROD", cursor_signing_key=secret)
    assert config.cursor_signing_key.get_secret_value() == secret
    assert secret not in repr(config)
