import logging
import json
import time
import hashlib
import hmac
from typing import Dict, List, Tuple, Optional
import requests
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

class KrakenAPILite:
    """Minimal Kraken REST API wrapper for v12 trading."""

    def __init__(self, api_key: str, api_secret: str, api_url: str = "https://api.kraken.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_url = api_url
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "kraken-bot-v12"})

    def _get_nonce(self) -> str:
        """Generate unique nonce."""
        return str(int(time.time() * 1000))

    def _sign_request(self, endpoint: str, data: Dict, nonce: str) -> Dict:
        """Sign request with Kraken signature."""
        import base64
        data['nonce'] = nonce
        postdata = urlencode(data)
        path = f"/0/private/{endpoint}"
        message = (postdata).encode()
        message_hash = hashlib.sha256(message).digest()
        sign_data = path.encode() + message_hash
        secret_bytes = base64.b64decode(self.api_secret)
        signature = hmac.new(secret_bytes, sign_data, hashlib.sha512)
        return {
            "API-Sign": base64.b64encode(signature.digest()).decode(),
            "API-Key": self.api_key
        }

    def _private_request(self, endpoint: str, data: Dict) -> Dict:
        """Execute private API request."""
        try:
            nonce = self._get_nonce()
            headers = self._sign_request(endpoint, data, nonce)
            url = f"{self.api_url}/0/private/{endpoint}"
            response = self.session.post(url, data=data, headers=headers, timeout=10)
            result = response.json()

            if result.get('error'):
                logger.error(f"Kraken API error ({endpoint}): {result['error']}")
                return {"success": False, "error": result['error']}

            return {"success": True, "data": result.get('result', {})}
        except requests.exceptions.RequestException as e:
            logger.error(f"Kraken API request failed ({endpoint}): {e}")
            return {"success": False, "error": str(e)}

    def _public_request(self, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """Execute public API request."""
        try:
            url = f"{self.api_url}/0/public/{endpoint}"
            response = self.session.get(url, params=data or {}, timeout=10)
            result = response.json()

            if result.get('error'):
                logger.error(f"Kraken API error ({endpoint}): {result['error']}")
                return {"success": False, "error": result['error']}

            return {"success": True, "data": result.get('result', {})}
        except requests.exceptions.RequestException as e:
            logger.error(f"Kraken API request failed ({endpoint}): {e}")
            return {"success": False, "error": str(e)}

    def get_balance(self, asset: str = None) -> Dict[str, float]:
        """Get account balance for asset(s)."""
        result = self._private_request("Balance", {})
        if not result['success']:
            return {}

        balances = result.get('data', {})
        if asset:
            asset_normalized = f"X{asset}" if not asset.startswith(('X', 'Z')) else asset
            return {asset: float(balances.get(asset_normalized, 0))}
        return {k: float(v) for k, v in balances.items()}

    def get_open_orders(self, pair: str = None) -> List[Dict]:
        """Get open orders."""
        result = self._private_request("OpenOrders", {})
        if not result['success']:
            return []

        orders = result.get('data', {})
        if pair:
            return [o for o in orders.values() if pair in o.get('info', {}).get('pair', '')]
        return list(orders.values())

    def place_order(self, pair: str, side: str, amount: float,
                   limit_price: Optional[float] = None, order_type: str = "market") -> Dict:
        """Place order on Kraken."""
        data = {
            "pair": pair,
            "type": "buy" if side.lower() == "buy" else "sell",
            "ordertype": order_type if limit_price else "market",
            "volume": str(amount)
        }

        if limit_price:
            data["price"] = str(limit_price)

        result = self._private_request("AddOrder", data)
        if result['success']:
            order_data = result.get('data', {})
            return {
                "success": True,
                "txid": order_data.get('txid', [''])[0],
                "descr": order_data.get('descr', {})
            }
        return {"success": False, "error": result.get('error', 'Unknown error')}

    def cancel_order(self, txid: str) -> bool:
        """Cancel order by txid."""
        result = self._private_request("CancelOrder", {"txid": txid})
        return result['success']

    def get_closed_orders(self, pair: str = None, days: int = 30) -> List[Dict]:
        """Get closed orders."""
        data = {"closetime": "both"}
        result = self._private_request("ClosedOrders", data)
        if not result['success']:
            return []

        orders = result.get('data', {})
        if pair:
            return [o for o in orders.values() if pair in o.get('info', {}).get('pair', '')]
        return list(orders.values())

    def calculate_fees(self, pair: str, amount: float, side: str) -> float:
        """Calculate round-trip fee (0.42% assumed for Kraken Starter)."""
        base_fee = 0.0026  # 0.26% per side
        round_trip = base_fee * 2
        return amount * round_trip

    def get_ticker(self, pair: str) -> Dict:
        """Get current ticker data."""
        result = self._public_request("Ticker", {"pair": pair})
        if result['success']:
            ticker_data = result['data'].get(pair, {})
            return {
                "bid": float(ticker_data.get('b', [0])[0]),
                "ask": float(ticker_data.get('a', [0])[0]),
                "last": float(ticker_data.get('c', [0])[0]),
                "volume": float(ticker_data.get('v', [0])[0]),
                "timestamp": int(time.time())
            }
        return {}

    def get_ohlc(self, pair: str, interval: int = 60) -> List[Dict]:
        """Get OHLC data (interval in minutes)."""
        result = self._public_request("OHLC", {"pair": pair, "interval": interval})
        if not result['success']:
            return []

        ohlc_data = result['data'].get(pair, [])
        candles = []
        for candle in ohlc_data:
            candles.append({
                "time": int(candle[0]),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "vwap": float(candle[5]),
                "volume": float(candle[6]),
                "count": int(candle[7])
            })
        return candles

    def get_spread(self, pair: str) -> Dict:
        """Get bid/ask spread."""
        result = self._public_request("Spread", {"pair": pair})
        if result['success']:
            spread_data = result['data'].get(pair, [])
            if spread_data:
                latest = spread_data[-1]
                return {
                    "time": int(latest[0]),
                    "bid": float(latest[1]),
                    "ask": float(latest[2])
                }
        return {}


class KrakenOrderExecutor:
    """Executor for atomic order placement + tracking."""

    def __init__(self, kraken_api: KrakenAPILite):
        self.api = kraken_api
        self.pending_orders = {}  # txid -> order_data

    def execute_limit_order(self, pair: str, side: str, amount: float,
                           limit_price: float, max_slippage: float = 0.05) -> Tuple[bool, str]:
        """Execute limit order with optional slippage fallback."""
        result = self.api.place_order(pair, side, amount, limit_price, order_type="limit")

        if result['success']:
            txid = result['txid']
            self.pending_orders[txid] = {
                "pair": pair,
                "side": side,
                "amount": amount,
                "price": limit_price,
                "created_at": time.time()
            }
            return True, txid
        else:
            logger.warning(f"Limit order failed ({pair}): {result.get('error')}")
            return False, result.get('error', 'Unknown error')

    def cancel_pending_order(self, txid: str) -> bool:
        """Cancel order and remove from tracking."""
        success = self.api.cancel_order(txid)
        if success and txid in self.pending_orders:
            del self.pending_orders[txid]
        return success

    def check_order_status(self, txid: str) -> Dict:
        """Check if order filled."""
        orders = self.api.get_closed_orders()
        for order in orders:
            if order.get('txid') == txid:
                return {
                    "filled": True,
                    "amount": float(order['vol']),
                    "price": float(order['price']),
                    "fee": float(order['fee'])
                }
        return {"filled": False}
