from __future__ import annotations
from typing import Any
from app.adapters.zetaris import StatementRunner, ZetarisClient, ZetarisError
from app.agents.schemas import ModelInfo


class GrantService:
    def __init__(self, cfg):
        self.cfg = cfg

    def grant_read(self, onboarder, model: ModelInfo, role: str = "pipeless_readers") -> dict[str, Any]:
        """Attempt to grant read permissions to non-admin reader role."""
        stmt = {
            "usl_table": f"GRANT SELECT ON USL TABLE {model.fqn} TO ROLE '{role}'",
            "schemastore_view": f"GRANT SELECT ON VIEW TABLE {model.fqn} TO ROLE '{role}'",
            "lightning_table": f"GRANT SELECT ON TABLE {model.fqn} TO ROLE '{role}'",
        }.get(model.kind)

        if not stmt:
            return {"status": "not_attempted", "reason": f"no grant form for kind {model.kind}"}

        builder_runner = StatementRunner(
            ZetarisClient(self.cfg, "builder"),
            self.cfg,
            onboarder.ctx.tracer,
            "GrantService",
        )

        try:
            builder_runner.execute(stmt, purpose="grant read to reader role")

            # Probe if reader can actually read
            if self.cfg.zetaris_reader_key:
                reader_runner = StatementRunner(
                    ZetarisClient(self.cfg, "reader"),
                    self.cfg,
                    onboarder.ctx.tracer,
                    "GrantService",
                )
                check = reader_runner.execute(f"SELECT * FROM {model.fqn} LIMIT 1", purpose="verify reader access")
                status = "granted_and_verified" if check.rows is not None else "granted_unreadable"
            else:
                status = "granted_unverified_no_reader_key"

            return {"status": status, "statement": stmt, "role": role}
        except ZetarisError as e:
            return {
                "status": "failed",
                "error_kind": e.info.kind,
                "message": e.info.message[:200],
                "statement": stmt,
            }
        except Exception as exc:
            return {"status": "failed", "error": str(exc), "statement": stmt}
