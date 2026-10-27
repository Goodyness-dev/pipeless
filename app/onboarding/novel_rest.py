from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
import requests

from app.onboarding.ddl import (
    create_database,
    create_rest_table,
    cache_table,
    preview_select,
    create_container,
    create_view,
)
from app.zsql.classify import classify_statement, statement_target

ONBOARDER_FLATTEN_SYSTEM = """You are OnboarderAgent inside Pipeless.
You write ONE Spark SQL SELECT that flattens a REST JSON response registered in Zetaris as the table {db}.{raw}.
Rules (from Zetaris documentation):
- Arrays of structs: LATERAL VIEW explode(<array_field>) AS item, then plain dot access (item.field, item.sub.field).
- Do NOT use explode on something that is not an array; flat structs use plain dot access.
- Dynamic-key objects: from_json(to_json(<path>), 'map<string, struct<...>>') then explode, and apply that to EVERY dynamic-key object exploded in the same query.
- Backtick keys with mixed case, reserved words or hyphens: `entityName`, item.`end`, product.nutriments.`energy-kcal_100g`.
- JSON may encode numbers as strings: CAST(... AS DOUBLE/INT/BIGINT) when the column is numeric.
- Read the raw table once per query. Do not self-join an exploded view.
- Select explicit, readable, snake_case aliases. Include an id/key column if one exists.
Output JSON:
{{
  "select_sql": "...",
  "row_grain": "one row per ...",
  "count_path": "dot.path.to.the.array.used.in.explode",
  "columns": [{{"name": "", "type": ""}}],
  "notes": []
}}
Memories from earlier onboarding:
{memory_block}
"""


def fetch_sample(url: str, cfg: Any, max_bytes: int = 200_000) -> tuple[dict | list, int]:
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", url.split("/")[-1] or "source")
    if cfg.sample_mode:
        sample_file = Path("input_examples/samples") / f"{slug}.json"
        if sample_file.exists():
            data = json.loads(sample_file.read_text(encoding="utf-8"))
            return data, len(json.dumps(data))

    headers = {"User-Agent": cfg.user_agent} if cfg.user_agent else {}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data, len(resp.content)


def classify_shape(obj: Any) -> dict[str, Any]:
    """Detects 1 of 5 JSON shapes from Zetaris documentation."""
    if isinstance(obj, list):
        return {"shape": "top_level_array", "array_paths": [""], "dynamic_key_paths": [], "notes": ["top-level JSON array"]}

    if not isinstance(obj, dict):
        return {"shape": "primitive", "array_paths": [], "dynamic_key_paths": [], "notes": []}

    array_paths = []
    dynamic_paths = []

    for k, v in obj.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            array_paths.append(k)
        elif isinstance(v, dict):
            # Check dynamic key heuristic: dict with >= 5 uniform dict keys
            if len(v) >= 5 and all(isinstance(val, dict) for val in list(v.values())[:5]):
                dynamic_paths.append(k)
            else:
                for sub_k, sub_v in v.items():
                    if isinstance(sub_v, list) and sub_v and isinstance(sub_v[0], dict):
                        array_paths.append(f"{k}.{sub_k}")

    if dynamic_paths:
        return {"shape": "dynamic_key_object", "array_paths": array_paths, "dynamic_key_paths": dynamic_paths, "notes": ["Requires from_json(to_json())"]}
    if array_paths:
        return {"shape": "array_of_structs", "array_paths": array_paths, "dynamic_key_paths": [], "notes": ["Use LATERAL VIEW explode"]}
    return {"shape": "flat_struct", "array_paths": [], "dynamic_key_paths": [], "notes": ["Use plain dot access, no explode"]}


def plan_novel_source(src: Any, ctx: Any, memory_block: str, result: Any) -> list[dict[str, Any]]:
    slug = re.sub(r"[^a-zA-Z0-9_]", "", src.id.lower())
    run_sfx = ctx.run_id[:6]
    db_name = f"pl_{slug}_{run_sfx}"
    raw_tbl = f"pl_{slug}_raw"
    container = f"pl_{slug}_{run_sfx}_c"
    view_name = f"pl_{slug}_view"

    sample_obj, _ = fetch_sample(src.url, ctx.cfg)
    shape_info = classify_shape(sample_obj)

    sample_snippet = json.dumps(sample_obj)[:6000]
    sys_prompt = ONBOARDER_FLATTEN_SYSTEM.format(
        db=db_name,
        raw=raw_tbl,
        memory_block=memory_block,
    )
    user_prompt = (
        f"Generate flattening SELECT for source {src.url}\n"
        f"Detected shape: {json.dumps(shape_info)}\n"
        f"Raw Table Reference: FROM {db_name}.{raw_tbl}\n"
        f"Sample snippet (truncated):\n{sample_snippet}"
    )

    llm_res, _ = ctx.llm.chat_json(
        lane="fast",
        system=sys_prompt,
        user=user_prompt,
        agent_name="OnboarderAgent",
        temperature=0.1,
    )

    select_sql = llm_res.get("select_sql", f"SELECT * FROM {db_name}.{raw_tbl}")

    # Build sequence of DDL statements
    statements = [
        {"sql": "SHOW LIGHTNING DATABASES", "kind": "READ", "purpose": "check databases"},
        {"sql": create_database(db_name, src.description or f"Database for {src.id}"), "kind": "CREATE", "target": ("LIGHTNING_DATABASE", (db_name,)), "rate_limited": False},
        {"sql": create_rest_table(raw_tbl, db_name, src.url, ctx.cfg.user_agent), "kind": "CREATE", "target": ("REST_TABLE", (raw_tbl, db_name)), "rate_limited": True},
        {"sql": cache_table(db_name, raw_tbl), "kind": "CACHE", "target": ("CACHE_TABLE", (f"{db_name}.{raw_tbl}",)), "rate_limited": False},
        {"sql": preview_select(select_sql), "kind": "READ", "purpose": "validate view select", "is_novel_view": True, "select_sql": select_sql},
        {"sql": create_container(container), "kind": "CREATE", "target": ("CONTAINER", (container,)), "rate_limited": False},
        {"sql": create_view(view_name, container, select_sql), "kind": "CREATE", "target": ("VIEW", (view_name, container)), "rate_limited": False, "is_novel_view": True},
        {"sql": f"SELECT COUNT(*) FROM {container}.{view_name}", "kind": "READ", "purpose": "verify view row readability"},
    ]
    return statements
