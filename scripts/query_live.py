"""Execute a reviewed read-only SQL file and save its observed result."""
import argparse
import json
from pathlib import Path
from app.adapters.zetaris import ZetarisClient
from app.config import Config
from app.zsql.guard import validate_read_only


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--reader", action="store_true")
    args = parser.parse_args()
    cfg = Config.load()
    sql = validate_read_only(args.file.read_text(encoding="utf-8"))
    identity = "reader" if args.reader else "builder"
    result = ZetarisClient(cfg, identity).run_sql(sql, limit=1000, timeout=180)
    output = {"sql": sql, "identity": identity, "columns": result.columns, "rows": result.rows,
              "truncated": result.truncated, "elapsed_ms": result.elapsed_ms}
    args.file.with_suffix(".result.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    main()
