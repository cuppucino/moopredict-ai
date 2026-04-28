import os
import time
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

    def connect(self) -> bool:
        """Initialize connection to OpenD."""
        try:
            logger.info(f"Connecting to Moomoo OpenD at {self.host}:{self.port}...")
            self.trd_ctx = OpenSecTradeContext(host=self.host, port=self.port)
            self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            
            # Simple check to see if we can get account list
            ret, data = self.trd_ctx.get_acc_list()
            if ret == RET_OK:
                logger.info("Successfully connected to Moomoo OpenD")
                self.is_connected = True
                self._initialize_account(data)
                return True
            else:
                logger.error(f"Connection failed: {data}")
                return False
        except Exception as e:
            logger.error(f"Unexpected error during connection: {e}")
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
        """Fetch account balance details."""
        if not self.trd_ctx or not self.acc_id:
            return None
        try:
            ret, data = self.trd_ctx.accinfo_query(acc_id=self.acc_id, trd_env=self.trd_env)
            if ret == RET_OK:
                # data is a DataFrame
                info = data.iloc[0]
                return {
                    "available_cash": float(info['cash']),
                    "total_assets": float(info['total_assets']),
                    "currency": info.get('currency', 'USD')
                }
            else:
                logger.error(f"Balance query failed: {data}")
                return None
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        """Fetch list of open positions."""
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
                return positions
            else:
                logger.error(f"Position query failed: {data}")
                return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def close(self):
        """Clean up connections."""
        if self.trd_ctx:
            self.trd_ctx.close()
        if self.quote_ctx:
            self.quote_ctx.close()
        logger.info("Moomoo connections closed.")

# Singleton instance
moomoo_service = MoomooService()
