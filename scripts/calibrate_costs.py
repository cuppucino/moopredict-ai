import sys
import os
import numpy as np
from datetime import datetime, timedelta
from loguru import logger

# Add root directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.moomoo_service import moomoo_service

def calibrate_costs():
    """
    Calibrates transaction costs based on historical Moomoo fill data.
    Only analyzes LIMIT and NORMAL orders for slippage.
    Includes regulatory fees (SEC + FINRA TAF) on sell orders.
    """
    logger.info("Starting Moomoo transaction cost calibration...")
    try:
        # Connect to Moomoo
        if not moomoo_service.connect():
            logger.error("Failed to connect to Moomoo. Please ensure OpenD is running.")
            return

        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        symbol = "US.MARA"
        logger.info(f"Fetching trade history for {symbol} starting from {start_date}...")
        
        trades = moomoo_service.get_trade_history(symbol=symbol, start_date=start_date)
        if not trades:
            logger.warning(f"No trade history found for {symbol} in the last 90 days.")
            return

        costs_bps = []
        limit_order_count = 0
        market_order_count = 0

        for trade in trades:
            try:
                order_price = trade.get("order_price")
                dealt_avg_price = trade.get("dealt_avg_price")
                dealt_qty = trade.get("dealt_qty", 0)
                order_type_raw = trade.get("order_type_raw", "").upper()
                side = trade.get("side", "").upper()

                # Filter out trades where dealt_qty is zero or price is missing/invalid
                if not order_price or not dealt_avg_price or order_price <= 0 or dealt_qty <= 0:
                    continue

                # Filter to only LIMIT and NORMAL orders for slippage calibration
                # Moomoo records normal limit orders as 'NORMAL' or 'LIMIT'
                if order_type_raw not in ["NORMAL", "LIMIT"]:
                    market_order_count += 1
                    continue
                
                limit_order_count += 1

                # Slippage: positive is adverse (paid more to buy, received less to sell)
                # sign = +1 for BUY, -1 for SELL
                sign = 1 if side == "BUY" else -1
                slippage_bps = sign * (dealt_avg_price - order_price) / order_price * 10000.0

                # Sells have regulatory fees (SEC fee: ~0.8 bps, FINRA TAF fee: ~$0.000166/share)
                # realized_cost = slippage + reg_fees
                reg_fees_bps = 0.0
                if side == "SELL":
                    # SEC fee (~0.8 bps)
                    reg_fees_bps += 0.8
                    # FINRA TAF ($0.000166/share mapped to bps based on dealt_avg_price)
                    if dealt_avg_price > 0:
                        taf_fee_per_share = 0.000166
                        reg_fees_bps += (taf_fee_per_share / dealt_avg_price) * 10000.0

                total_cost_bps = slippage_bps + reg_fees_bps
                costs_bps.append(total_cost_bps)
                
                logger.info(
                    f"Order ID: {trade.get('order_id')} | {side} | {order_type_raw} | Qty: {dealt_qty} | "
                    f"Order Price: {order_price:.4f} | Filled Price: {dealt_avg_price:.4f} | "
                    f"Slippage: {slippage_bps:.2f} bps | Reg Fees: {reg_fees_bps:.2f} bps | Total: {total_cost_bps:.2f} bps"
                )
            except Exception as trade_error:
                logger.error(f"Error parsing trade details: {trade_error}")

        # TODO: Add capability to capture bid/ask snapshot at order submit time for market orders.

        if not costs_bps:
            logger.warning(f"No valid LIMIT/NORMAL filled orders found for calibration. (Ignored {market_order_count} MARKET orders)")
            return

        # Compute percentiles of signed realized costs
        median_cost = np.percentile(costs_bps, 50)
        p75_cost = np.percentile(costs_bps, 75)
        p95_cost = np.percentile(costs_bps, 95)

        print("\n=== Transaction Cost Calibration Results ===")
        print(f"Ticker: {symbol}")
        print(f"Lookback: 90 days (since {start_date})")
        print(f"Total LIMIT/NORMAL analyzed fills: {len(costs_bps)}")
        print(f"Total MARKET orders bypassed:      {market_order_count}")
        print(f"Median Realized Cost: {median_cost:.2f} bps")
        print(f"p75 Realized Cost:    {p75_cost:.2f} bps")
        print(f"p95 Realized Cost:    {p95_cost:.2f} bps")
        print("============================================\n")

    except Exception as e:
        logger.error(f"Critical error in cost calibration: {e}")
    finally:
        moomoo_service.close()

if __name__ == "__main__":
    calibrate_costs()
