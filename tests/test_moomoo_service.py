import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from services.moomoo_service import MoomooService

@pytest.fixture
def moomoo_service():
    return MoomooService()

def test_initialize_account_real(moomoo_service):
    # Mock account list dataframe
    df = pd.DataFrame([
        {'acc_id': 123, 'trd_env': 'REAL', 'trd_market_auth': 'HK'},
        {'acc_id': 456, 'trd_env': 'SIMULATE', 'trd_market_auth': 'US'}
    ])
    
    moomoo_service._initialize_account(df)
    
    assert moomoo_service.acc_id == 123
    assert moomoo_service.trd_env.value == 'REAL'
    assert moomoo_service.trd_market.value == 'HK'

def test_initialize_account_sim(moomoo_service):
    # Mock account list dataframe with only sim
    df = pd.DataFrame([
        {'acc_id': 456, 'trd_env': 'SIMULATE', 'trd_market_auth': 'US'}
    ])
    
    moomoo_service._initialize_account(df)
    
    assert moomoo_service.acc_id == 456
    assert moomoo_service.trd_env.value == 'SIMULATE'
    assert moomoo_service.trd_market.value == 'US'

@patch('services.moomoo_service.OpenSecTradeContext')
def test_connect_failure(mock_ctx, moomoo_service):
    # Mock connection failure
    mock_ctx.return_value.get_acc_list.return_value = (-1, "Connection Error")
    
    result = moomoo_service.connect()
    
    assert result is False
    assert moomoo_service.is_connected is False
