import pandas as pd
import numpy as np

def compute_pf(signal: pd.Series, returns: pd.Series) -> float:
    """
    Computes the Profit Factor for a strategy.
    PF = (Gross Profit) / (Gross Loss)
    """
    trade_rets = signal * returns
    
    gross_profit = trade_rets[trade_rets > 0].sum()
    gross_loss = trade_rets[trade_rets < 0].abs().sum()
    
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
        
    return float(gross_profit / gross_loss)
