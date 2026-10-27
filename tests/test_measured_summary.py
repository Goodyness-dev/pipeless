from unittest.mock import MagicMock
from app.agents.investigator import InvestigatorAgent
from app.agents.schemas import Draft, Plan


def test_summary_uses_measured_count_without_calling_a_model():
    ctx = MagicMock()
    agent = InvestigatorAgent(ctx)
    draft = Draft(sql='SELECT id FROM records', columns=['id'], rows=[[str(i)] for i in range(260)])
    summary = agent._summarize_rows(draft, Plan(objective='Audit', question='Audit', sources=[]))
    assert '260 rows' in summary
    assert 'require separate evidence' in summary
    ctx.llm.chat.assert_not_called()
