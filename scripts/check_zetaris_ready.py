"""Read-only readiness check through Pipeless's configured Zetaris client."""
import json

from app.config import Config
from app.adapters.zetaris import ZetarisClient, StatementRunner
from app.tracing import Tracer


def main():
    cfg = Config.load()
    print(json.dumps({"user_agent_valid": cfg.user_agent_valid,
                      "builder_key_present": bool(cfg.zetaris_builder_key),
                      "reader_key_present": bool(cfg.zetaris_reader_key),
                      "recipe_repo_exists": cfg.repo_path.is_dir()}), flush=True)
    client = ZetarisClient(cfg, "builder")
    response = client._session.get(cfg.zetaris_rest_url.rstrip("/") + "/datasource/datasources",
                                   headers=client._headers(), timeout=15)
    print(f"Datasource endpoint HTTP {response.status_code}", flush=True)
    response.raise_for_status()
    runner = StatementRunner(client, cfg, Tracer("zetaris_readiness", cfg.log_dir), "ReadinessCheck")
    for sql in ["SELECT 1 AS one", "SHOW LIGHTNING DATABASES"]:
        result = runner.execute(sql, purpose="read-only readiness check", timeout=20)
        print(json.dumps({"sql": sql, "columns": result.columns, "rows": result.rows}), flush=True)


if __name__ == "__main__":
    main()
