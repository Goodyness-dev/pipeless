"""Bounded live JSON/SQL generation probe before the full demonstration."""
from app.config import Config
from app.adapters.meterless import NightshiftLLM
from app.tracing import Tracer
from app.zsql.guard import validate_read_only


def main():
    cfg = Config.load()
    llm = NightshiftLLM(cfg, Tracer("structured_generation_probe", cfg.log_dir), max_calls=2)
    result, response = llm.chat_json(lane="fast", agent_name="GenerationProbe",
        system="Write one read-only SQL SELECT using only NY_TAXI.taxi_zones. Its columns are LocationID, zone, borough.",
        user="Return JSON with sql, rationale and join_spec=null. List the first three LocationID and zone values, ordered by numeric LocationID. Keep it short.")
    sql = validate_read_only(result["sql"], allowed_tables={"NY_TAXI.taxi_zones"})
    print(f"PASS structured generation: {response.model_returned}; SQL guard accepted: {sql}", flush=True)


if __name__ == "__main__":
    main()
