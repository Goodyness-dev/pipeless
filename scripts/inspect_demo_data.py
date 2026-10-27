"""Read-only inspection of installed samples; no upstream HTTP connector calls."""
import json
from app.config import Config
from app.adapters.zetaris import ZetarisClient, StatementRunner
from app.tracing import Tracer


def main():
    cfg = Config.load()
    tracer = Tracer("inspect_demo_data", cfg.log_dir)
    runner = StatementRunner(ZetarisClient(cfg, "builder"), cfg, tracer, "DemoPreflight")
    outputs = []
    for sql in ["SHOW LIGHTNING TABLES NY_TAXI", "SHOW LIGHTNING TABLES TPCDS_DB",
                "DESCRIBE DATASOURCE TABLE NY_TAXI.taxi_zones",
                "DESCRIBE DATASOURCE TABLE NY_TAXI.trips",
                "DESCRIBE DATASOURCE TABLE NY_TAXI.zones",
                "DESCRIBE DATASOURCE TABLE NY_TAXI.trip_payments",
                "SELECT COUNT(*) AS trip_rows, COUNT(DISTINCT trip_id) AS distinct_trip_ids FROM NY_TAXI.trips",
                "SELECT COUNT(*) AS payment_rows, COUNT(DISTINCT trip_id) AS distinct_trip_ids FROM NY_TAXI.trip_payments",
                "SELECT COUNT(DISTINCT t.trip_id) AS matched_trip_ids FROM NY_TAXI.trips t INNER JOIN NY_TAXI.trip_payments p ON t.trip_id = p.trip_id"]:
        result = runner.execute(sql, purpose="inspect installed sample metadata", limit=100, timeout=40)
        record = {"sql": sql, "columns": result.columns, "rows": result.rows}
        outputs.append(record)
        print(json.dumps(record), flush=True)
    (cfg.data_dir / "demo" / "installed-sample-metadata.json").write_text(json.dumps(outputs, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
