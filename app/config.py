from __future__ import annotations
import os
import re
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# "app_name user@domain.tld" -- a bare app name or no contact email gets the strict rate limit.
UA_PATTERN = re.compile(r"^\S+\s+[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_user_agent(value: str) -> bool:
    if not UA_PATTERN.fullmatch(value):
        return False
    address = value.split()[-1].lower()
    domain = address.rsplit("@", 1)[-1]
    return domain not in {"example.com", "example.org", "example.net"} and not domain.endswith((".example", ".invalid", ".test"))


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ConfigError(f"{name} is missing from .env")
    return value


@dataclass(frozen=True)
class Config:
    # Zetaris
    zetaris_rest_url: str
    zetaris_org_id: str
    zetaris_builder_key: str | None
    zetaris_reader_key: str | None
    user_agent: str
    user_agent_valid: bool
    zetaris_timeout_s: int
    query_limit: int
    statement_gap_s: float          # 0 when identified, 15 when not (docs)
    repo_path: Path
    # Nightshift
    llm_base_url: str
    llm_api_key: str
    llm_app_name: str
    model_fast: str
    model_deep: str
    # Runtime
    api_token: str | None
    approval_mode: str
    max_revisions: int
    max_llm_calls_per_run: int
    sample_mode: bool
    log_dir: Path
    data_dir: Path

    def __repr__(self) -> str:
        return (
            f"Config(zetaris_rest_url={self.zetaris_rest_url!r}, "
            f"zetaris_org_id={self.zetaris_org_id!r}, "
            f"zetaris_builder_key={'***' if self.zetaris_builder_key else None!r}, "
            f"zetaris_reader_key={'***' if self.zetaris_reader_key else None!r}, "
            f"user_agent={self.user_agent!r}, user_agent_valid={self.user_agent_valid!r}, "
            f"statement_gap_s={self.statement_gap_s!r}, "
            f"llm_base_url={self.llm_base_url!r}, "
            f"llm_api_key={'***' if self.llm_api_key else None!r}, "
            f"model_fast={self.model_fast!r}, model_deep={self.model_deep!r}, "
            f"approval_mode={self.approval_mode!r}, sample_mode={self.sample_mode!r})"
        )

    @staticmethod
    def load() -> "Config":
        ua = _env("ZETARIS_USER_AGENT", "", False) or ""
        ua_valid = valid_user_agent(ua)
        is_vercel = bool(os.getenv("VERCEL"))
        default_log = "/tmp/logs" if is_vercel else "logs"
        default_data = "/tmp/data" if is_vercel else "data"
        cfg = Config(
            zetaris_rest_url=_env("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0") or "http://localhost:8888/api/v1.0",
            zetaris_org_id=_env("ZETARIS_ORG_ID", "1") or "1",
            zetaris_builder_key=_env("ZETARIS_BUILDER_API_KEY"),
            zetaris_reader_key=_env("ZETARIS_READER_API_KEY"),
            user_agent=ua,
            user_agent_valid=ua_valid,
            zetaris_timeout_s=int(_env("ZETARIS_TIMEOUT_SECONDS", "120") or "120"),
            query_limit=int(_env("ZETARIS_QUERY_LIMIT", "1000") or "1000"),
            statement_gap_s=0.0 if ua_valid else 15.0,
            repo_path=Path(_env("ZETARIS_REPO_PATH", "../getting-started") or "../getting-started").expanduser(),
            llm_base_url=_env("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1") or "http://127.0.0.1:8088/v1",
            llm_api_key=_env("METERLESS_RUNTIME_TOKEN", "") or "",
            llm_app_name=_env("METERLESS_APP_NAME", "pipeless") or "pipeless",
            model_fast=_env("MODEL_FAST", "") or "",
            model_deep=_env("MODEL_DEEP", "") or "",
            api_token=_env("PIPELESS_API_TOKEN"),
            approval_mode=_env("APPROVAL_MODE", "auto_policy") or "auto_policy",
            max_revisions=int(_env("MAX_REVISIONS", "2") or "2"),
            max_llm_calls_per_run=int(_env("MAX_LLM_CALLS_PER_RUN", "40") or "40"),
            sample_mode=(_env("SAMPLE_MODE", "false") or "false").lower() == "true",
            log_dir=Path(_env("LOG_DIR", default_log) or default_log),
            data_dir=Path(_env("DATA_DIR", default_data) or default_data),
        )
        try:
            cfg.log_dir.mkdir(parents=True, exist_ok=True)
            (cfg.data_dir / "memory").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return cfg

    def require_builder(self) -> str:
        if not self.zetaris_builder_key:
            raise ConfigError("ZETARIS_BUILDER_API_KEY is missing from .env")
        return self.zetaris_builder_key

    def require_llm(self) -> None:
        if not self.llm_api_key:
            raise ConfigError("METERLESS_RUNTIME_TOKEN is missing from .env")
        if not (self.model_fast and self.model_deep):
            raise ConfigError("MODEL_FAST and MODEL_DEEP must be set (see scripts/check_nightshift.py)")
