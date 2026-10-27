from types import SimpleNamespace
from unittest.mock import Mock

from app.adapters.zetaris import ZetarisClient


def test_column_headers_from_observed_local_rest_response(monkeypatch):
    response = Mock(ok=True, status_code=200, text='{"headers":["one"],"data":[["1"]]}')
    response.json.return_value = {"headers": ["one"], "data": [["1"]], "total": 1}
    session = Mock()
    session.post.return_value = response
    monkeypatch.setattr("app.adapters.zetaris.requests.Session", lambda: session)
    cfg = SimpleNamespace(
        zetaris_builder_key="test-key",
        zetaris_org_id="1",
        user_agent="",
        query_limit=10,
        zetaris_rest_url="http://localhost:8888/api/v1.0",
        zetaris_timeout_s=30,
    )

    result = ZetarisClient(cfg).run_sql("SELECT 1 AS one")

    assert result.columns == ["one"]
    assert result.rows == [["1"]]
