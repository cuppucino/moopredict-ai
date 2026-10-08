import os
import time
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Thread
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from futu import *
from loguru import logger

# ─── LIVE TRADING HARD KILL-SWITCH (2026-07-29) ───────────────────────────────
# This is a PAPER-PREDICTION system. Live order placement is disabled at the code
# level so NO webhook command, bug, or DB-flag flip can ever fire a real trade on
# the brokerage account. Re-enabling requires a deliberate edit here (not a runtime
# toggle) plus the existing SystemState.real_trading_unlocked gate.
LIVE_TRADING_ENABLED = False

# SDK constructors retry indefinitely even when OpenD accepts TCP but rejects
# the SDK handshake. Bound callers' wait and share one owned attempt instead.
CONNECT_WAIT_SECONDS = 8.0
QUERY_CONNECT_TIMEOUT_SECONDS = 5.0


class MoomooService:
    def __init__(self, host: str = None, port: int = None):
        load_dotenv()
        self.host = host or os.getenv("MOOMOO_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("MOOMOO_PORT", 11111))
        self.trd_ctx: Optional[OpenSecTradeContext] = None
        self.quote_ctx: Optional[OpenQuoteContext] = None
        self.acc_id: Optional[int] = None
        self.trd_env: TrdEnv = TrdEnv.REAL
        self.trd_market: TrdMarket = TrdMarket.HK
        self.is_connected: bool = False
        self._connection_lock = Lock()
        self._connection_attempt: Optional[Thread] = None
        self._connection_epoch = 0
        self._closing = False
        self._pos_cache = None
        self._pos_cache_expiry = datetime.now()
        self._bal_cache = None
        self._bal_cache_expiry = datetime.now()
        self._price_cache = {}

    def _scan_ip(self, ip: str) -> Optional[str]:
        """Check if port is open on a given IP."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((ip, self.port)) == 0:
                    return ip
        except:
            pass
        return None

    def _get_local_ip(self) -> str:
        """Get the local IP address of this machine."""
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except Exception as e:
            logger.warning(f"[Moomoo] Failed to get local IP: {e}")
            return "127.0.0.1"

    def discover_opend_ip(self) -> List[str]:
        """Automatically find potential OpenD IPs on the local network."""
        candidates = [self.host, "127.0.0.1"]
        
        # Add local subnet if on a common private range
        try:
            local_ip = self._get_local_ip()
            if local_ip.startswith(("192.168.", "10.", "172.")):
                prefix = ".".join(local_ip.split(".")[:-1])
                # Scan common endings first
                for i in [1, 90, 100, 33]: # Common static IPs
                    candidates.append(f"{prefix}.{i}")
                
                # Multi-threaded fast scan of the entire /24 subnet
                logger.info(f"[Moomoo] Auto-scanning subnet {prefix}.0/24 for OpenD...")
                ips_to_scan = [f"{prefix}.{i}" for i in range(1, 255)]
                with ThreadPoolExecutor(max_workers=50) as executor:
                    results = list(executor.map(self._scan_ip, ips_to_scan))
                    found_ips = [r for r in results if r]
                    candidates.extend(found_ips)
        except Exception as e:
            logger.warning(f"[Moomoo] Subnet discovery failed: {e}")

        # Unique IPs while preserving order
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _port_reachable(ip: str, port: int, timeout_s: float = 3.0) -> bool:
        """Cheap TCP pre-check. The futu SDK's context constructors can block
        indefinitely against a dead/half-open OpenD — on 2026-08-17 a single such
        hang inside focus_job (max_instances=1) silently killed the prediction
        pipeline for 4 trading days. A refused/filtered port fails here in <=3s."""
        import socket
        try:
            with socket.create_connection((ip, port), timeout=timeout_s):
                return True
        except OSError:
            return False

    def connect(self) -> bool:
        """Reuse healthy contexts or wait briefly for one shared connection attempt.

        The SDK cannot cancel a constructor that is retrying its handshake. Keep
        that attempt owned until it ends; repeated callers must not start more.
        """
        with self._connection_lock:
            if self.is_connected:
                return True
            if self._closing:
                return False
            attempt = self._connection_attempt
            if attempt is None or not attempt.is_alive():
                attempt = Thread(
                    target=self._connect_attempt,
                    args=(self._connection_epoch,),
                    name="moomoo-connect",
                    daemon=True,
                )
                self._connection_attempt = attempt
                attempt.start()

        attempt.join(timeout=CONNECT_WAIT_SECONDS)
        with self._connection_lock:
            connected = self.is_connected
        if not connected and attempt.is_alive():
            logger.warning("[Moomoo] Connection still pending; reusing the existing attempt on retry.")
        return connected

    @staticmethod
    def _dispose_contexts(*contexts):
        """Attempt every cleanup, including when one SDK close raises."""
        for context in contexts:
            if context is not None:
                try:
                    context.close()
                except Exception as exc:
                    logger.warning(f"[Moomoo] Context cleanup failed ({type(exc).__name__}).")

    def _attempt_current(self, epoch: int) -> bool:
        with self._connection_lock:
            return epoch == self._connection_epoch and not self._closing

    def _connect_attempt(self, epoch: int):
        """Own construction and cleanup; publish only a complete, current pair."""
        with self._connection_lock:
            if epoch != self._connection_epoch or self._closing:
                return
            stale = (self.trd_ctx, self.quote_ctx)
            self.trd_ctx = self.quote_ctx = None
            self.is_connected = False
        self._dispose_contexts(*stale)

        try:
            target_ips = self.discover_opend_ip()
        except Exception as exc:
            logger.warning(f"[Moomoo] Discovery failed ({type(exc).__name__}).")
            return

        for ip in target_ips:
            trade = quote = None
            try:
                if not self._attempt_current(epoch):
                    return
                if not self._port_reachable(ip, self.port):
                    logger.warning(f"[Moomoo] OpenD port {ip}:{self.port} unreachable — skipping "
                                   f"(is OpenD running and logged in?)")
                    continue
                logger.info(f"[Moomoo] Trying to connect to OpenD at {ip}:{self.port}...")
                trade = OpenSecTradeContext(host=ip, port=self.port)
                trade.set_sync_query_connect_timeout(QUERY_CONNECT_TIMEOUT_SECONDS)
                if not self._attempt_current(epoch):
                    return
                quote = OpenQuoteContext(host=ip, port=self.port)
                quote.set_sync_query_connect_timeout(QUERY_CONNECT_TIMEOUT_SECONDS)
                if not self._attempt_current(epoch):
                    return

                ret, data = trade.get_acc_list()
                if ret == RET_OK:
                    with self._connection_lock:
                        if epoch != self._connection_epoch or self._closing:
                            return
                        self._initialize_account(data)
                        self.host = ip
                        self.trd_ctx, self.quote_ctx = trade, quote
                        self.is_connected = True
                        trade = quote = None  # Ownership transferred to the service.
                    logger.success(f"[Moomoo] Connected successfully to {ip}")
                    return
                else:
                    logger.warning(f"[Moomoo] Account query failed for {ip}; closing this attempt.")
            except Exception as exc:
                logger.warning(f"[Moomoo] Connection to {ip} failed ({type(exc).__name__}).")
            finally:
                self._dispose_contexts(trade, quote)
                
        logger.error("[Moomoo] Failed to connect to any target IP. Please check if OpenD is running.")

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
            # Map market string to Enum, checking environment variable first
            env_market = os.getenv("MOOMOO_MARKET")
            if env_market:
                env_market_upper = env_market.upper()
                if env_market_upper == "US":
                    self.trd_market = TrdMarket.US
                elif env_market_upper == "HK":
                    self.trd_market = TrdMarket.HK
                else:
                    self.trd_market = TrdMarket.HK
            else:
                # Fallback to the account's authorized markets.
                # The Futu API returns the column as 'trdmarket_auth'.
                market_auth = target.get('trdmarket_auth')
                if market_auth is None:
                    market_auth = target.get('trd_market_auth', 'HK')
                
                # Check if US is in the authorized markets
                if isinstance(market_auth, list):
                    if 'US' in market_auth:
                        self.trd_market = TrdMarket.US
                    else:
                        self.trd_market = TrdMarket.HK
                else:
                    if 'US' in str(market_auth):
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
                
                # Register freshness
                from services.data_freshness import freshness_registry
                freshness_registry.register_update("moomoo_balance")
                
                return result
            else:
                logger.error(f"Balance query failed: {data}")
                self.is_connected = False
                return None
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            self.is_connected = False
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
                
                # Register freshness
                from services.data_freshness import freshness_registry
                freshness_registry.register_update("moomoo_positions")
                
                return positions
            else:
                logger.error(f"Position query failed: {data}")
                self.is_connected = False
                return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            self.is_connected = False
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "MARKET", price: float = 0.0) -> Dict:
        """Place a trade order. HARD-DISABLED — paper-prediction system only."""
        if not LIVE_TRADING_ENABLED:
            logger.warning(f"[Moomoo] place_order REFUSED for {symbol} — live trading hard-disabled (paper only)")
            return {"success": False, "error": "live_trading_disabled"}
        if not self.trd_ctx or not self.acc_id:
            return {"success": False, "error": "Not connected to Moomoo"}

        # Gate on SystemState.real_trading_unlocked
        from core.database import SessionLocal, SystemState
        db = SessionLocal()
        try:
            state = db.query(SystemState).filter(SystemState.key == "real_trading_unlocked").first()
            if not state or not state.real_trading_unlocked:
                logger.warning(
                    f"[Moomoo] REAL TRADE BLOCKED for {symbol}: "
                    f"real_trading_unlocked=False (paper-trading mode)"
                )
                return {"success": False, "error": "real_trading_locked"}
        except Exception as e:
            logger.error(f"[Moomoo] Error checking real_trading_unlocked gate: {e}")
            return {"success": False, "error": "real_trading_locked"}
        finally:
            db.close()

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
                    try:
                        # Map to a clean dictionary with extended fields
                        orders.append({
                            "order_id": str(row['order_id']),
                            "symbol": row['code'],
                            "side": "BUY" if row['trd_side'] == 'BUY' else "SELL",
                            "qty": float(row['qty']),
                            "price": float(row['dealt_avg_price'] or row['price']),  # DEPRECATED: Use order_price or dealt_avg_price
                            "status": row['order_status'],
                            "time": row['create_time'],
                            "order_type": row['order_type'],
                            "order_price": float(row['price']),          # intended price
                            "dealt_avg_price": float(row['dealt_avg_price']),  # actual fill
                            "dealt_qty": float(row['dealt_qty']),
                            "create_time": row['create_time'],           # order placed
                            "updated_time": row['updated_time'],         # filled / final state
                            "order_type_raw": str(row['order_type'])
                        })
                    except Exception as parse_error:
                        logger.error(f"[Moomoo] Error parsing trade history row: {parse_error}")
                return orders
            else:
                logger.error(f"[Moomoo] History query failed: {data}")
                return []
        except Exception as e:
            logger.error(f"[Moomoo] Exception during history query: {e}")
            return []

    def get_stock_quote(self, symbol: str) -> Dict:
        """Fetch real-time quote for a symbol with market snapshot and yfinance fallback."""
        from services.data_freshness import freshness_registry
        
        if self.quote_ctx:
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
                    freshness_registry.register_update("moomoo_quote", symbol)
                    self._price_cache[symbol] = {
                        "price": last_price,
                        "as_of": datetime.utcnow()
                    }
                    return {
                        "symbol": symbol,
                        "last_price": last_price,
                        "timestamp": datetime.now().isoformat(),
                        "source": "moomoo"
                    }
                else:
                    self.is_connected = False
            except Exception as e:
                logger.error(f"[Moomoo] Quote exception for {symbol}: {e}")
                self.is_connected = False

        # Tier 4: yfinance fallback (delayed but better than nothing)
        def _fetch_yfinance():
            import yfinance as yf
            yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
            logger.info(f"[Moomoo] Attempting yfinance fallback for {yf_symbol}...")
            ticker = yf.Ticker(yf_symbol)
            
            # Try fast price lookup first
            last_price = ticker.fast_info.get('last_price', 0.0)
            
            if last_price <= 0:
                # Fallback to history
                hist = ticker.history(period="1d")
                if not hist.empty:
                    last_price = float(hist['Close'].iloc[-1])
            return last_price

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_fetch_yfinance)
                last_price = future.result(timeout=5.0)
            
            if last_price > 0:
                freshness_registry.register_update("yfinance_fallback", symbol)
                logger.warning(f"[Moomoo] Using yfinance fallback for {symbol}: ${last_price}")
                self._price_cache[symbol] = {
                    "price": last_price,
                    "as_of": datetime.utcnow()
                }
                return {
                    "symbol": symbol,
                    "last_price": last_price,
                    "timestamp": datetime.now().isoformat(),
                    "source": "yfinance"
                }
        except TimeoutError:
            logger.error(f"[Moomoo] yfinance fallback timed out (5.0s limit) for {symbol}")
        except Exception as ye:
            logger.error(f"[Moomoo] yfinance fallback failed for {symbol}: {ye}")

        # Tier 5: In-memory cache fallback
        if symbol in self._price_cache:
            cache_entry = self._price_cache[symbol]
            cached_price = cache_entry["price"]
            as_of = cache_entry["as_of"]
            age_seconds = (datetime.utcnow() - as_of).total_seconds()
            stale = age_seconds > 300.0  # 5 minutes
            
            logger.warning(f"[Moomoo] Using cached price for {symbol}: ${cached_price} (Age: {age_seconds:.1f}s, Stale: {stale})")
            return {
                "symbol": symbol,
                "last_price": cached_price,
                "timestamp": as_of.isoformat(),
                "source": "cache",
                "stale": stale
            }
            
        return {"success": False, "last_price": None, "error": "quote_timeout", "stale": False}

    def get_volume(self, symbol: str) -> Dict:
        """Fetch volume, gap, and snapshot data for a symbol with yfinance fallback."""
        if not symbol:
            return {"success": False, "error": "Empty symbol"}

        if "." not in symbol:
            symbol = f"US.{symbol}"

        # 1. Try Moomoo OpenD first
        if self.quote_ctx:
            try:
                self.quote_ctx.subscribe([symbol], [SubType.QUOTE])
                ret, data = self.quote_ctx.get_market_snapshot([symbol])
                if ret == RET_OK and not data.empty:
                    info = data.iloc[0]
                    prev_close = float(info.get('prev_close_price', 0.0))
                    open_price = float(info.get('open_price', 0.0))
                    volume_ratio = float(info.get('volume_ratio', 1.0))
                    intraday_high = float(info.get('high_price', 0.0))
                    intraday_low = float(info.get('low_price', 0.0))
                    
                    # Determine volume signal based on ratio
                    volume_signal = "NORMAL"
                    if volume_ratio > 2.0:
                        volume_signal = "HIGH"
                    elif volume_ratio < 0.5:
                        volume_signal = "LOW"
                        
                    return {
                        "success": True,
                        "previous_close": prev_close,
                        "open_price": open_price,
                        "volume_ratio": volume_ratio,
                        "volume_signal": volume_signal,
                        "intraday_high": intraday_high,
                        "intraday_low": intraday_low,
                        "source": "moomoo"
                    }
            except Exception as e:
                logger.error(f"[Moomoo] Error fetching volume snapshot for {symbol}: {e}")

        # 2. Fallback to yfinance if not connected or snapshot failed
        try:
            import yfinance as yf
            yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
            logger.info(f"[Moomoo] get_volume yfinance fallback for {yf_symbol}...")
            ticker = yf.Ticker(yf_symbol)
            
            # Fetch 1d history to get open, high, low, prev close
            hist = ticker.history(period="1d")
            if not hist.empty:
                info = hist.iloc[-1]
                open_price = float(info.get('Open', 0.0))
                intraday_high = float(info.get('High', 0.0))
                intraday_low = float(info.get('Low', 0.0))
                
                # Fetch fast info or fallback for previous close
                prev_close = float(ticker.fast_info.get('previousClose', open_price))
                if prev_close <= 0:
                    prev_close = open_price
                
                return {
                    "success": True,
                    "previous_close": prev_close,
                    "open_price": open_price,
                    "volume_ratio": 1.0,
                    "volume_signal": "NORMAL",
                    "intraday_high": intraday_high,
                    "intraday_low": intraday_low,
                    "source": "yfinance"
                }
        except Exception as ye:
            logger.error(f"[Moomoo] get_volume yfinance fallback failed for {symbol}: {ye}")

        return {
            "success": False,
            "volume_ratio": 1.0,
            "volume_signal": "NORMAL",
            "previous_close": 0.0,
            "open_price": 0.0,
            "intraday_high": 0.0,
            "intraday_low": 0.0,
            "error": f"Failed to fetch volume data for {symbol}"
        }

    def auto_reconnect(self, max_attempts: int = 3):
        """Wraps connect() with retry logic."""
        for i in range(max_attempts):
            logger.info(f"[Moomoo] Auto-reconnect attempt {i+1}/{max_attempts}...")
            if self.connect():
                return True
            time.sleep(min(30, 5 * (2**i))) # Exponential backoff: 5, 10, 20s
        return False

    def close(self):
        """Detach contexts and invalidate any connection still being constructed."""
        with self._connection_lock:
            self._connection_epoch += 1
            self.is_connected = False
            if self._closing:
                return
            self._closing = True
            contexts = (self.trd_ctx, self.quote_ctx)
            self.trd_ctx = self.quote_ctx = None
        try:
            self._dispose_contexts(*contexts)
        finally:
            with self._connection_lock:
                self._closing = False
        logger.info("Moomoo connections closed.")

# Singleton instance
moomoo_service = MoomooService()
