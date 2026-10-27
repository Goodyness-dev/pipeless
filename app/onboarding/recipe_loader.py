from __future__ import annotations
from pathlib import Path
from app.config import Config
from app.onboarding.catalog import Recipe
from app.zsql.classify import classify_statement, statement_target
from app.zsql.splitter import split_statements

PLACEHOLDER = "YOUR_APP_NAME YOUR_CONTACT_EMAIL"


def load_statements(cfg: Config, recipe: Recipe) -> list[dict]:
    """Return ordered statements: {sql, kind, target, rate_limited, source_file}."""
    out: list[dict] = []
    for part in recipe.parts:
        file_path = cfg.repo_path / part.path
        if not file_path.exists():
            raise FileNotFoundError(f"Recipe file not found: {file_path}")

        text = file_path.read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            if not cfg.user_agent_valid:
                raise ValueError("ZETARIS_USER_AGENT must be 'app_name user@domain.tld' for this recipe")
            text = text.replace(PLACEHOLDER, cfg.user_agent)

        for sql in split_statements(text):
            target = statement_target(sql)
            label = target[0] if target else None
            if part.include_kinds and label not in part.include_kinds:
                continue

            kind = classify_statement(sql)
            if kind == "DESTRUCTIVE":
                raise ValueError(f"Recipe {recipe.id} contains a destructive statement: {sql[:60]}")

            out.append(
                {
                    "sql": sql,
                    "kind": kind,
                    "target": target,
                    "rate_limited": part.rate_limited,
                    "source_file": part.path,
                }
            )
            if part.stop_after_contains and part.stop_after_contains.lower() in sql.lower():
                break
    return out
