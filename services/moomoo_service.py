import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from futu import *
from loguru import logger

class MoomooService:
    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        self.host = host
        self.port = port
        self.trd_ctx: Optional[OpenSecTradeContext] = None
        self.quote_ctx: Optional[OpenQuoteContext] = None
        self.acc_id: Optional[int] = None
        self.trd_env: TrdEnv = TrdEnv.REAL
        self.trd_market: TrdMarket = TrdMarket.HK
        self.is_connected: bool = False
        self._pos_cache = None
        self._pos_cache_expiry = datetime.now()
        self._bal_cache = None
        self._bal_cache_expiry = datetime.now()

    def connect(self) -> bool:
        """Initialize connection to OpenD with dynamic IP detection."""
        target_ips = [self.host, "127.0.0.1", "192.168.100.90", "192.168.0.33"]
        # Remove duplicates while preserving order
        target_ips = list(dict.fromkeys(target_ips))
        
        for ip in target_ips:
            try:
                logger.info(f"[Moomoo] Trying to connect to OpenD at {ip}:{self.port}...")
                self.trd_ctx = OpenSecTradeContext(host=ip, port=self.port)
                self.quote_ctx = OpenQuoteContext(host=ip, port=self.port)
                
                ret, data = self.trd_ctx.get_acc_list()
                if ret == RET_OK:
                    logger.success(f"[Moomoo] Connected successfully to {ip}")
                    self.host = ip
                    self.is_connected = True
                    self._initialize_account(data)
                    return True
                else:
                    logger.warning(f"[Moomoo] Connection to {ip} failed: {data}")
                    self.close() # Clean up failed contexts
            except Exception as e:
                logger.warning(f"[Moomoo] Unexpected error connecting to {ip}: {e}")
                
        logger.error("[Moomoo] Failed to connect to any target IP.")
        return False

    def _initialize_account(self, acc_list_df):
        """Identify and set the correct trading account."""
        try:
            # Filter for REAL account if possible, else SIMULATE
            real_acc = acc_list_df[acc_list_df['trd_env'] == 'REAL']
            sim_acc = acc_list_df[acc_list_df['trd_env'] == 'SIMULATE']

            if not real_acc.empty:
                target = real_acc.iloc[0]
                self.trd_env = TrdEnv.REAL
                logger.info("Real account detected.")
            elif not sim_acc.empty:
                target = sim_acc.iloc[0]
                self.trd_env = TrdEnv.SIMULATE
                logger.info("No real account found, using Simulation account.")
            else:
                logger.warning("No accounts found in Moomoo!")
                return

            self.acc_id = target['acc_id']
            # Map market string to Enum if necessary, default to HK
            market_str = target.get('trd_market_auth', 'HK')
            if 'US' in market_str:
                self.trd_market = TrdMarket.US
            else:
                self.trd_market = TrdMarket.HK
                
            logger.info(f"Account Initialized: ID={self.acc_id}, Env={self.trd_env}, Market={self.trd_market}")
        except Exception as e:
            logger.error(f"Error initializing account: {e}")

    def unlock(self, password: str) -> bool:
        """Unlock the trading session for live trading."""
        if not self.trd_ctx:
            return False
        try:
            ret, data = self.trd_ctx.unlock_trade(password)
            if ret == RET_OK:
                logger.info("Trading session unlocked.")
                return True
            else:
                logger.error(f"Unlock failed: {data}")
                return False
        except Exception as e:
            logger.error(f"Error during unlock: {e}")
            return False

    def get_balance(self) -> Optional[Dict]:
        """Fetch account balance details with 5-min cache."""
        if self._bal_cache and datetime.now() < self._bal_cache_expiry:
            return self._bal_cache

        if not self.trd_ctx or not self.acc_id:
            return None
        try:
            ret, data = self.trd_ctx.accinfo_query(acc_id=self.acc_id, trd_env=self.trd_env)
            if ret == RET_OK:
                # data is a DataFrame
                info = data.iloc[0]
                result = {
                    "available_cash": float(info['cash']),
                    "total_assets": float(info['total_assets']),
                    "currency": info.get('currency', 'USD')
                }
                self._bal_cache = result
                self._bal_cache_expiry = datetime.now() + timedelta(minutes=5)
                return result
            else:
                logger.error(f"Balance query failed: {data}")
                return None
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        """Fetch list of open positions with 5-min cache."""
        if self._pos_cache and datetime.now() < self._pos_cache_expiry:
            return self._pos_cache

        if not self.trd_ctx or not self.acc_id:
            return []
        try:
            ret, data = self.trd_ctx.position_list_query(acc_id=self.acc_id, trd_env=self.trd_env)
            if ret == RET_OK:
                positions = []
                for _, row in data.iterrows():
                    positions.append({
                        "symbol": row['code'],
                        "qty": float(row['qty']),
                        "avg_price": float(row['cost_price']),
                        "current_price": float(row['nominal_price']),
                        "pnl_percent": f"{float(row['pl_ratio']):.2f}%"
                    })
                self._pos_cache = positions
                self._pos_cache_expiry = datetime.now() + timedelta(minutes=5)
                return positions
            else:
                logger.error(f"Position query failed: {data}")
                return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "MARKET", price: float = 0.0) -> Dict:
        """Place a trade order."""
        if not self.trd_ctx or not self.acc_id:
            return {"success": False, "error": "Not connected to Moomoo"}

        try:
            # Map side to TrdSide enum
            trd_side = TrdSide.BUY if side.upper() == "BUY" else TrdSide.SELL
            
            # Map order type to OrderType enum
            ot = OrderType.MARKET
            if order_type.upper() == "LIMIT":
                ot = OrderType.NORMAL # Futu uses NORMAL for limit orders in some contexts
            
            logger.info(f"[Moomoo] Placing {side} order for {qty} {symbol}...")
            ret, data = self.trd_ctx.place_order(
                price=price,
                qty=qty,
                code=symbol,
                trd_side=trd_side,
                order_type=ot,
                trd_env=self.trd_env,
                acc_id=self.acc_id
            )
            
            if ret == RET_OK:
                order_id = data.iloc[0]['order_id']
                logger.info(f"[Moomoo] Order placed successfully! ID: {order_id}")
                return {"success": True, "order_id": str(order_id), "data": data.to_dict(orient='records')[0]}
            else:
                logger.error(f"[Moomoo] Order failed: {data}")
                return {"success": False, "error": str(data)}
        except Exception as e:
            logger.error(f"[Moomoo] Exception during order: {e}")
            return {"success": False, "error": str(e)}

    def get_trade_history(self, symbol: str = "", start_date: str = "", end_date: str = "") -> List[Dict]:
        """Fetch historical trade orders."""
        if not self.trd_ctx or not self.acc_id:
            return []
            
        try:
            # If no start date, default to last 30 days
            if not start_date:
                from datetime import datetime, timedelta
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            if not end_date:
                from datetime import datetime
                end_date = datetime.now().strftime("%Y-%m-%d")

            logger.info(f"[Moomoo] Querying history for {symbol or 'ALL'} from {start_date} to {end_date}...")
            ret, data = self.trd_ctx.history_order_list_query(
                code=symbol,
                start=start_date,
                end=end_date,
                trd_env=self.trd_env,
                acc_id=self.acc_id
            )
            
            if ret == RET_OK:
                orders = []
                for _, row in data.iterrows():
                    # Map to a clean dictionary
                    orders.append({
                        "order_id": str(row['order_id']),
                        "symbol": row['code'],
                        "side": "BUY" if row['trd_side'] == 'BUY' else "SELL",
                        "qty": float(row['qty']),
                        "price": float(row['dealt_avg_price'] or row['price']),
                        "status": row['order_status'],
                        "time": row['create_time'],
                        "order_type": row['order_type']
                    })
                return orders
            else:
                logger.error(f"[Moomoo] History query failed: {data}")
                return []
        except Exception as e:
            logger.error(f"[Moomoo] Exception during history query: {e}")
            return []

    def get_stock_quote(self, symbol: str) -> Dict:
        """Fetch real-time quote for a symbol with market snapshot fallback."""
        if not self.quote_ctx:
            if not self.connect():
                return {"success": False, "error": "Not connected"}

        if "." not in symbol:
            symbol = f"US.{symbol}"
            
        try:
            # 1. Try standard quote first
            self.quote_ctx.subscribe([symbol], [SubType.QUOTE])
            ret, data = self.quote_ctx.get_stock_quote([symbol])
            
            last_price = 0.0
            if ret == RET_OK:
                last_price = float(data.iloc[0]['last_price'])
            
            # 2. Fallback to Snapshot if price is 0.0 or failed
            if last_price <= 0.0:
                logger.info(f"[Moomoo] Quote returned 0.0 for {symbol}. Trying market snapshot...")
                ret, data = self.quote_ctx.get_market_snapshot([symbol])
                if ret == RET_OK:
                    last_price = float(data.iloc[0]['last_price'])
                else:
                    # Final attempt: get_cur_kline
                    ret, df = self.quote_ctx.get_cur_kline(symbol, 1, SubType.K_DAY)
                    if ret == RET_OK:
                        last_price = float(df['close'].iloc[-1])

            if last_price > 0:
                return {
                    "symbol": symbol,
                    "last_price": last_price,
                    "timestamp": datetime.now().isoformat()
                }
            
            return {"success": False, "error": f"Failed to get price for {symbol}"}
        except Exception as e:
            logger.error(f"[Moomoo] Quote exception for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def close(self):
        """Clean up connections."""
        if self.trd_ctx:
            self.trd_ctx.close()
        if self.quote_ctx:
            self.quote_ctx.close()
        logger.info("Moomoo connections closed.")

# Singleton instance
moomoo_service = MoomooService()
