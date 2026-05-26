"""
MIT License

Copyright (c) 2023 neurotrader888 (Original implementation)
Copyright (c) 2026 moopredict-ai (Modifications for session gap segmentation and volume permutation)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import numpy as np
import pandas as pd
from typing import List, Union

def get_permutation(
    ohlc: Union[pd.DataFrame, List[pd.DataFrame]], start_index: int = 0, seed=None
):
    assert start_index >= 0

    np.random.seed(seed)

    if isinstance(ohlc, list):
        time_index = ohlc[0].index
        for mkt in ohlc:
            assert np.all(time_index == mkt.index), "Indexes do not match"
        n_markets = len(ohlc)
    else:
        n_markets = 1
        time_index = ohlc.index
        ohlc = [ohlc]

    n_bars = len(ohlc[0])

    perm_index = start_index + 1
    perm_n = n_bars - perm_index

    # Index 0: open, 1: high, 2: low, 3: close, 4: volume
    start_bar = np.empty((n_markets, 5))
    relative_open = np.empty((n_markets, perm_n))
    relative_high = np.empty((n_markets, perm_n))
    relative_low = np.empty((n_markets, perm_n))
    relative_close = np.empty((n_markets, perm_n))
    volumes = np.empty((n_markets, perm_n))

    for mkt_i, reg_bars in enumerate(ohlc):
        # We need volume, but we don't log-transform it
        log_bars = np.log(reg_bars[['open', 'high', 'low', 'close']])
        
        vols = reg_bars['volume'].to_numpy() if 'volume' in reg_bars.columns else np.zeros(n_bars)

        # Get start bar
        start_bar[mkt_i, :4] = log_bars.iloc[start_index].to_numpy()
        start_bar[mkt_i, 4] = vols[start_index]

        # Open relative to last close
        r_o = (log_bars['open'] - log_bars['close'].shift()).to_numpy()
        
        # Get prices relative to this bars open
        r_h = (log_bars['high'] - log_bars['open']).to_numpy()
        r_l = (log_bars['low'] - log_bars['open']).to_numpy()
        r_c = (log_bars['close'] - log_bars['open']).to_numpy()

        relative_open[mkt_i] = r_o[perm_index:]
        relative_high[mkt_i] = r_h[perm_index:]
        relative_low[mkt_i] = r_l[perm_index:]
        relative_close[mkt_i] = r_c[perm_index:]
        volumes[mkt_i] = vols[perm_index:]

    idx = np.arange(perm_n)

    # Shuffle intrabar relative values (high/low/close) AND volume
    perm1 = np.random.permutation(idx)
    relative_high = relative_high[:, perm1]
    relative_low = relative_low[:, perm1]
    relative_close = relative_close[:, perm1]
    volumes = volumes[:, perm1]

    # --- Session Boundary Aware Gap Shuffling ---
    dt_series = ohlc[0].index.to_series().dt.date
    # A bar is a session open if its date is different from the previous bar's date.
    is_session_open = (dt_series != dt_series.shift(1)).to_numpy()
    
    session_open_mask = is_session_open[perm_index:]
    session_open_indices = np.where(session_open_mask)[0]
    intraday_indices = np.where(~session_open_mask)[0]
    
    # Shuffle each pool separately
    shuffled_session_open = np.random.permutation(session_open_indices)
    shuffled_intraday = np.random.permutation(intraday_indices)
    
    # Reconstruct the shuffled relative_open (gaps)
    permuted_relative_open = np.empty_like(relative_open)
    for mkt_i in range(n_markets):
        permuted_relative_open[mkt_i, session_open_indices] = relative_open[mkt_i, shuffled_session_open]
        permuted_relative_open[mkt_i, intraday_indices] = relative_open[mkt_i, shuffled_intraday]

    # Create permutation from relative prices
    perm_ohlc = []
    for mkt_i, reg_bars in enumerate(ohlc):
        perm_bars = np.zeros((n_bars, 5))

        # Copy over real data before start index 
        log_bars = np.log(reg_bars[['open', 'high', 'low', 'close']]).to_numpy().copy()
        perm_bars[:start_index, :4] = log_bars[:start_index]
        if 'volume' in reg_bars.columns:
            perm_bars[:start_index, 4] = reg_bars['volume'].to_numpy()[:start_index]
        
        # Copy start bar
        perm_bars[start_index] = start_bar[mkt_i]

        for i in range(perm_index, n_bars):
            k = i - perm_index
            perm_bars[i, 0] = perm_bars[i - 1, 3] + permuted_relative_open[mkt_i][k]
            perm_bars[i, 1] = perm_bars[i, 0] + relative_high[mkt_i][k]
            perm_bars[i, 2] = perm_bars[i, 0] + relative_low[mkt_i][k]
            perm_bars[i, 3] = perm_bars[i, 0] + relative_close[mkt_i][k]
            perm_bars[i, 4] = volumes[mkt_i][k]

        exp_prices = np.exp(perm_bars[:, :4])
        vols_col = perm_bars[:, 4].reshape(-1, 1)
        
        perm_bars_combined = np.hstack([exp_prices, vols_col])
        perm_bars_df = pd.DataFrame(perm_bars_combined, index=time_index, columns=['open', 'high', 'low', 'close', 'volume'])

        perm_ohlc.append(perm_bars_df)

    if n_markets > 1:
        return perm_ohlc
    else:
        return perm_ohlc[0]
