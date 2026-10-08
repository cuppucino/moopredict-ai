from threading import Event
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pandas as pd
import pytest

from services import moomoo_service as module


@pytest.fixture
def service(monkeypatch):
    svc = module.MoomooService(host="127.0.0.1")
    monkeypatch.setattr(svc, "discover_opend_ip", lambda: ["127.0.0.1"])
    monkeypatch.setattr(svc, "_port_reachable", lambda *args: True)
    monkeypatch.setattr(svc, "_initialize_account", Mock())
    monkeypatch.setattr(module, "CONNECT_WAIT_SECONDS", 0.05, raising=False)
    trade, quote = Mock(), Mock()
    trade.get_acc_list.return_value = (module.RET_OK, pd.DataFrame())
    monkeypatch.setattr(module, "OpenSecTradeContext", Mock(return_value=trade))
    monkeypatch.setattr(module, "OpenQuoteContext", Mock(return_value=quote))
    return svc


def test_healthy_connect_reuses_existing_contexts(service):
    assert service.connect()
    original = (service.trd_ctx, service.quote_ctx)
    assert service.connect()
    assert (service.trd_ctx, service.quote_ctx) == original
    assert module.OpenSecTradeContext.call_count == 1
    assert module.OpenQuoteContext.call_count == 1
    original[0].set_sync_query_connect_timeout.assert_called_once()
    original[1].set_sync_query_connect_timeout.assert_called_once()


def test_partial_initialization_failure_closes_unpublished_context(service):
    trade = module.OpenSecTradeContext.return_value
    module.OpenQuoteContext.side_effect = RuntimeError("initialization failed")
    assert not service.connect()
    trade.close.assert_called_once()
    assert service.trd_ctx is None and service.quote_ctx is None


def test_reconnect_disposes_stale_contexts_before_replacing_them(service):
    old_trade, old_quote = Mock(), Mock()
    service.trd_ctx, service.quote_ctx = old_trade, old_quote
    old_trade.close.side_effect = RuntimeError("one cleanup failed")
    assert service.connect()
    old_trade.close.assert_called_once()
    old_quote.close.assert_called_once()
    assert service.is_connected


def test_close_detaches_both_contexts_even_when_one_cleanup_fails(service):
    assert service.connect()
    trade, quote = service.trd_ctx, service.quote_ctx
    trade.close.side_effect = RuntimeError("close failed")
    service.close()
    quote.close.assert_called_once()
    assert service.trd_ctx is None and service.quote_ctx is None
    assert not service.is_connected


def test_hung_constructor_is_single_owned_attempt_and_close_fences_late_result(service):
    started, release = Event(), Event()
    trade = Mock()
    trade.get_acc_list.return_value = (module.RET_OK, pd.DataFrame())

    def constructor(**kwargs):
        started.set()
        assert release.wait(2), "test must release the simulated constructor"
        return trade

    module.OpenSecTradeContext.side_effect = constructor
    try:
        assert not service.connect()
        assert started.is_set()
        assert not service.connect()
        assert module.OpenSecTradeContext.call_count == 1
        service.close()
    finally:
        release.set()
        attempt = getattr(service, "_connection_attempt", None)
        if attempt:
            attempt.join(1)
    assert not service.is_connected
    assert service.trd_ctx is None and service.quote_ctx is None
    trade.close.assert_called_once()


def test_failed_account_query_closes_both_and_allows_retry(service):
    trade, quote = module.OpenSecTradeContext.return_value, module.OpenQuoteContext.return_value
    trade.get_acc_list.return_value = (module.RET_ERROR, "network interrupted")
    assert not service.connect()
    trade.close.assert_called_once()
    quote.close.assert_called_once()
    trade.get_acc_list.return_value = (module.RET_OK, pd.DataFrame())
    assert service.connect()


def test_concurrent_callers_share_attempt_that_can_finish_after_timeout(service):
    release = Event()
    trade = Mock()
    trade.get_acc_list.return_value = (module.RET_OK, pd.DataFrame())

    def constructor(**kwargs):
        assert release.wait(2), "test must release the simulated constructor"
        return trade

    module.OpenSecTradeContext.side_effect = constructor
    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            results = list(callers.map(lambda _: service.connect(), range(4)))
        assert results == [False] * 4
        assert module.OpenSecTradeContext.call_count == 1
    finally:
        release.set()
        service._connection_attempt.join(1)
    assert service.connect()
    assert module.OpenSecTradeContext.call_count == 1
    assert service.trd_ctx is trade
    trade.close.assert_not_called()
