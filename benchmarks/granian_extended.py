
"""
Stack 2: Granian + Raw ASGI + NautilusTrader + skfolio
Low-level ASGI implementation without framework overhead, served by Granian
"""

import asyncio
import json
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import pandas as pd
import numpy as np
from dataclasses import dataclass

# NautilusTrader imports
from nautilus_trader.trading.trader import Trader
from nautilus_trader.system.kernel import NautilusKernel
from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType

# skfolio imports
from skfolio import RiskMeasure
from skfolio.datasets import load_sp500_dataset
from skfolio.preprocessing import prices_to_returns
from skfolio.optimization import MeanRisk, ObjectiveFunction, HierarchicalRiskParity

# Simple dataclasses for request/response (no Pydantic)
@dataclass
class PortfolioRequest:
    symbols: list
    risk_measure: str = "variance"
    objective: str = "maximize_ratio"
    lookback_days: int = 252

@dataclass  
class TradeRequest:
    instrument_id: str
    side: str
    quantity: float
    time_in_force: str = "GTC"

# State
class TradingState:
    def __init__(self):
        self.kernel: Optional[NautilusKernel] = None
        self.trader: Optional[Trader] = None
        self.cache: Dict[str, Any] = {}
        self.initialized: bool = False

state = TradingState()

# ==================== ASGI APPLICATION ====================

async def app(scope: Dict, receive: Callable, send: Callable):
    """
    Raw ASGI application - no framework, direct protocol handling
    """
    assert scope['type'] == 'http'

    # Lazy init on first request
    if not state.initialized:
        await init()

    method = scope['method']
    path = scope['path']

    # Simple router
    handler = ROUTES.get((method, path))

    if not handler:
        # Try path with parameters
        handler = match_dynamic_route(method, path)

    if not handler:
        await send_response(send, 404, {"error": "Not found"})
        return

    # Read body
    body = await read_body(receive)

    try:
        # Parse JSON if present
        data = json.loads(body.decode()) if body else {}

        # Execute handler
        response = await handler(scope, data)
        await send_response(send, 200, response)

    except Exception as e:
        await send_response(send, 500, {"error": str(e)})

# ==================== ROUTING ====================

ROUTES: Dict[tuple, Callable] = {}

def route(method: str, path: str):
    def decorator(func: Callable):
        ROUTES[(method, path)] = func
        return func
    return decorator

def match_dynamic_route(method: str, path: str) -> Optional[Callable]:
    """Simple pattern matching for /resource/{id} paths"""
    parts = path.split('/')

    # Try /marketdata/{instrument_id}
    if len(parts) == 3 and parts[1] == 'marketdata':
        return get_market_data

    # Try /backtest/results/{strategy_name}
    if len(parts) == 4 and parts[1] == 'backtest' and parts[2] == 'results':
        return get_backtest_results

    return None

# ==================== REQUEST/RESPONSE UTILITIES ====================

async def read_body(receive: Callable) -> bytes:
    """Read request body from ASGI messages"""
    body = b''
    while True:
        message = await receive()
        if message['type'] == 'http.request':
            body += message.get('body', b'')
            if not message.get('more_body', False):
                break
    return body

async def send_response(send: Callable, status: int, data: Dict):
    """Send JSON response via ASGI"""
    body = json.dumps(data, default=str).encode()

    await send({
        'type': 'http.response.start',
        'status': status,
        'headers': [
            [b'content-type', b'application/json'],
            [b'content-length', str(len(body)).encode()],
        ],
    })
    await send({
        'type': 'http.response.body',
        'body': body,
    })

# ==================== HANDLERS ====================

@route("GET", "/health")
async def health_check(scope: Dict, data: Dict) -> Dict:
    return {
        'status': 'healthy',
        'initialized': state.initialized,
        'kernel_ready': state.kernel is not None,
        'trader_ready': state.trader is not None
    }

@route("POST", "/portfolio/optimize")
async def optimize_portfolio(scope: Dict, data: Dict) -> Dict:
    """
    Portfolio optimization using skfolio
    """
    request = PortfolioRequest(
        symbols=data.get('symbols', []),
        risk_measure=data.get('risk_measure', 'variance'),
        objective=data.get('objective', 'maximize_ratio'),
        lookback_days=data.get('lookback_days', 252)
    )

    # Load data
    prices = load_sp500_dataset()
    available = [s for s in request.symbols if s in prices.columns]

    if not available:
        return {"error": "No valid symbols"}

    prices = prices[available].dropna()
    X = prices_to_returns(prices)

    # Map string enums
    risk_map = {
        "variance": RiskMeasure.VARIANCE,
        "semivariance": RiskMeasure.SEMI_VARIANCE,
        "cvar": RiskMeasure.CVAR
    }
    obj_map = {
        "maximize_ratio": ObjectiveFunction.MAXIMIZE_RATIO,
        "minimize_risk": ObjectiveFunction.MINIMIZE_RISK
    }

    model = MeanRisk(
        objective_function=obj_map.get(request.objective, ObjectiveFunction.MAXIMIZE_RATIO),
        risk_measure=risk_map.get(request.risk_measure, RiskMeasure.VARIANCE),
    )

    model.fit(X)
    portfolio = model.predict(X)

    return {
        "weights": dict(zip(available, model.weights_.tolist())),
        "expected_return": float(portfolio.annualized_mean_return),
        "volatility": float(portfolio.annualized_volatility),
        "sharpe_ratio": float(portfolio.annualized_sharpe_ratio),
        "max_drawdown": float(portfolio.max_drawdown)
    }

@route("POST", "/portfolio/hierarchical")
async def hierarchical_portfolio(scope: Dict, data: Dict) -> Dict:
    """Hierarchical Risk Parity"""
    symbols = data.get('symbols', [])

    prices = load_sp500_dataset()
    available = [s for s in symbols if s in prices.columns]
    prices = prices[available].dropna()
    X = prices_to_returns(prices)

    model = HierarchicalRiskParity()
    model.fit(X)

    return {
        "weights": dict(zip(available, model.weights_.tolist())),
        "method": "Hierarchical Risk Parity"
    }

@route("GET", "/marketdata/{instrument_id}")
async def get_market_data(scope: Dict, data: Dict) -> Dict:
    """Fetch market data from NautilusTrader cache"""
    path = scope['path']
    instrument_id = path.split('/')[-1]

    if not state.trader:
        return {"error": "Trader not initialized"}

    try:
        instrument = InstrumentId.from_str(instrument_id)
        bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
        bars = state.trader.cache.bars(bar_type)

        result = [{
            'timestamp': str(bar.ts_event),
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': float(bar.volume)
        } for bar in bars]

        return {'instrument': instrument_id, 'count': len(result), 'data': result}

    except Exception as e:
        return {"error": str(e)}

@route("POST", "/trading/order")
async def submit_order(scope: Dict, data: Dict) -> Dict:
    """Submit order via NautilusTrader"""
    if not state.trader:
        return {"error": "Trader not initialized"}

    request = TradeRequest(
        instrument_id=data.get('instrument_id'),
        side=data.get('side', 'BUY'),
        quantity=data.get('quantity', 0.0)
    )

    try:
        instrument = InstrumentId.from_str(request.instrument_id)
        side = OrderSide.BUY if request.side.upper() == 'BUY' else OrderSide.SELL

        order = state.trader.create_order(
            instrument_id=instrument,
            side=side,
            quantity=request.quantity,
            time_in_force=TimeInForce.GTC
        )

        response = await state.trader.submit_order(order)

        return {
            "order_id": str(order.client_order_id),
            "status": "submitted",
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        return {"error": str(e)}

@route("GET", "/trading/positions")
async def get_positions(scope: Dict, data: Dict) -> Dict:
    """Get current positions"""
    if not state.trader:
        return {"error": "Trader not initialized"}

    positions = state.trader.cache.positions()

    return {
        "positions": [
            {
                "instrument": str(pos.instrument_id),
                "side": pos.side.name,
                "quantity": float(pos.quantity),
                "unrealized_pnl": float(pos.unrealized_pnl)
            }
            for pos in positions
        ],
        "total_pnl": sum(float(p.unrealized_pnl) for p in positions)
    }

@route("POST", "/backtest/run")
async def run_backtest(scope: Dict, data: Dict) -> Dict:
    """Start backtest (async)"""
    strategy_name = data.get('strategy_name', 'unknown')

    # Start in background
    asyncio.create_task(_backtest_task(strategy_name, data))

    return {
        "status": "started",
        "strategy": strategy_name
    }

async def _backtest_task(strategy_name: str, params: Dict):
    """Background backtest execution"""
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.config import BacktestEngineConfig

    try:
        bt_config = BacktestEngineConfig(
            trader_id="BACKTEST-001",
            logging=LoggingConfig(log_level="INFO")
        )

        engine = BacktestEngine(config=bt_config)
        results = engine.run()

        state.cache[f"backtest_{strategy_name}"] = {
            "returns": results.returns.tolist(),
            "sharpe": results.sharpe_ratio()
        }
    except Exception as e:
        state.cache[f"backtest_{strategy_name}"] = {"error": str(e)}

@route("GET", "/backtest/results/{strategy_name}")
async def get_backtest_results(scope: Dict, data: Dict) -> Dict:
    """Retrieve backtest results"""
    path = scope['path']
    strategy_name = path.split('/')[-1]
    key = f"backtest_{strategy_name}"

    if key not in state.cache:
        return {"error": "Results not found"}

    return state.cache[key]

# ==================== INITIALIZATION ====================

async def init():
    """Initialize NautilusTrader and skfolio (called lazily on first request)"""
    config = TradingNodeConfig(
        trader_id="GRANIAN-RAW-001",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            "BINANCE": BinanceDataClientConfig(
                account_type=BinanceAccountType.SPOT,
            )
        },
    )

    state.kernel = NautilusKernel(config=config)
    state.trader = Trader(kernel=state.kernel)
    state.initialized = True
    print("✅ Granian + Raw ASGI + NautilusTrader + skfolio initialized")

# Run with: granian --interface asgi main:app --host 0.0.0.0 --port 8000