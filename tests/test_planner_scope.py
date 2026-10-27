from unittest.mock import MagicMock
from app.agents.planner import PlannerAgent
from app.api.schemas import RunRequest
from app.agents.schemas import SourceSpec


def test_planning_preserves_the_callers_question_and_sources():
    ctx = MagicMock()
    ctx.memory = None
    ctx.llm.chat_json.return_value = ({'objective': 'Audit', 'question': 'Shortened question', 'sources': []}, None)
    request = RunRequest(request_id='scope-test', user_query='Return left labels and preserve every key', sources=[
        SourceSpec(id='samples', kind='catalog', recipe_id='ny_taxi_zone_audit')])
    plan = PlannerAgent(ctx).plan(request)
    assert plan.question == request.user_query
    assert plan.sources == request.sources
