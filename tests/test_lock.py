from __future__ import annotations
import threading
import time
from unittest.mock import MagicMock
from app.adapters.zetaris import StatementRunner, ZResult, ZetarisClient
from app.config import Config
from app.tracing import Tracer


def test_builder_lock_prevents_overlap(tmp_path):
    cfg = MagicMock(spec=Config)
    cfg.statement_gap_s = 0.05
    cfg.query_limit = 100
    cfg.zetaris_timeout_s = 10
    cfg.log_dir = tmp_path

    tracer = Tracer("test_lock", tmp_path)

    active_executions = []
    max_concurrent = 0
    lock = threading.Lock()

    def mock_run_sql(sql, limit=None, timeout=None):
        nonlocal max_concurrent
        with lock:
            active_executions.append(1)
            if len(active_executions) > max_concurrent:
                max_concurrent = len(active_executions)
        time.sleep(0.02)
        with lock:
            active_executions.pop()
        return ZResult(rows=[[1]], raw={}, elapsed_ms=20, limit=100, truncated=False)

    client1 = MagicMock(spec=ZetarisClient)
    client1.identity = "builder"
    client1.run_sql = mock_run_sql

    client2 = MagicMock(spec=ZetarisClient)
    client2.identity = "builder"
    client2.run_sql = mock_run_sql

    runner1 = StatementRunner(client1, cfg, tracer, "Agent1")
    runner2 = StatementRunner(client2, cfg, tracer, "Agent2")

    t1 = threading.Thread(target=lambda: runner1.execute("SELECT 1", purpose="t1"))
    t2 = threading.Thread(target=lambda: runner2.execute("SELECT 2", purpose="t2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert max_concurrent == 1, "Builder statements overlapped in execution!"
