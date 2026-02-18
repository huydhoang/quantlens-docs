
"""
Stack 3: Gunicorn + Uvicorn Workers + NautilusTrader + skfolio
Multi-process raw ASGI using Gunicorn with Uvicorn worker class
"""

import asyncio
import json
import os
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

# Multi-process state management
# NOTE: Each worker process has isolated state - no shared memory
class ProcessState:
    """Per-process state - each Gunicorn worker has its own instance"""
    def __init__(self):
        self.kernel: Optional[NautilusKernel] = None
        self.trader: Optional[Trader] = None
        self.cache: Dict[str, Any] = {}
        self.worker_id = os.getpid()
        self.initialized = False

state = ProcessState()

# ASGI Application
async def app(scope: Dict, receive: Callable, send: Callable):
    assert scope['type'] == 'http'

    method = scope['method']
    path = scope['path']

    # Initialize per-process if needed
    if not state.initialized:
        await init_process()

    handler = ROUTES.get((method, path))
    if not handler:
        handler = match_dynamic_route(method, path)

    if not handler:
        await send_json(send, 404, {'error': 'Not found', 'worker_pid': state.worker_id})
        return

    body = await read_body(receive)
    data = json.loads(body.decode()) if body else {}

    try:
        result = await handler(scope, data)
        result['worker_pid'] = state.worker_id  # Debug: show which worker handled request
        await send_json(send, 200, result)
    except Exception as e:
        await send_json(send, 500, {'error': str(e), 'worker_pid': state.worker_id})

# Utilities
async def read_body(receive):
    body = b''
    while True:
        msg = await receive()
        if msg['type'] == 'http.request':
            body += msg.get('body', b'')
            if not msg.get('more_body'):
                break
    return body

async def send_json(send, status: int, data: Dict):
    body = json.dumps(data, default=str).encode()
    await send({
        'type': 'http.response.start',
        'status': status,
        'headers': [
            [b'content-type', b'application/json'],
            [b'content-length', str(len(body)).encode()],
        ],
    })
    await send({'type': 'http.response.body', 'body': body})

# Per-process initialization
async def init_process():
    """Initialize NautilusTrader for this worker process"""
    config = TradingNodeConfig(
        trader_id=f'GUNICORN-WORKER-{state.worker_id}',
        logging=LoggingConfig(log_level='INFO'),
        data_clients={
            'BINANCE': BinanceDataClientConfig(
                account_type=BinanceAccountType.SPOT,
            )
        },
    )
    state.kernel = NautilusKernel(config=config)
    state.trader = Trader(kernel=state.kernel)
    state.initialized = True
    print(f'✅ Worker {state.worker_id} initialized with NautilusTrader + skfolio')

# Routes
ROUTES: Dict[tuple, Callable] = {}

def route(method: str, path: str):
    def decorator(func):
        ROUTES[(method, path)] = func
        return func
    return decorator

def match_dynamic_route(method: str, path: str):
    parts = path.split('/')
    if len(parts) == 3 and parts[1] == 'marketdata':
        return get_market_data
    if len(parts) == 4 and parts[1] == 'backtest' and parts[2] == 'results':
        return get_backtest_results
    return None

# Handlers
@route('POST', '/portfolio/optimize')
async def optimize_portfolio(scope, data):
    """Portfolio optimization using skfolio"""
    symbols = data.get('symbols', [])
    risk_measure = data.get('risk_measure', 'variance')
    objective = data.get('objective', 'maximize_ratio')

    prices = load_sp500_dataset()
    available = [s for s in symbols if s in prices.columns]

    if not available:
        return {'error': 'No valid symbols'}

    prices = prices[available].dropna()
    X = prices_to_returns(prices)

    risk_map = {
        'variance': RiskMeasure.VARIANCE,
        'semivariance': RiskMeasure.SEMI_VARIANCE,
        'cvar': RiskMeasure.CVAR
    }
    obj_map = {
        'maximize_ratio': ObjectiveFunction.MAXIMIZE_RATIO,
        'minimize_risk': ObjectiveFunction.MINIMIZE_RISK
    }

    model = MeanRisk(
        objective_function=obj_map.get(objective, ObjectiveFunction.MAXIMIZE_RATIO),
        risk_measure=risk_map.get(risk_measure, RiskMeasure.VARIANCE),
    )

    model.fit(X)
    portfolio = model.predict(X)

    return {
        'weights': dict(zip(available, model.weights_.tolist())),
        'expected_return': float(portfolio.annualized_mean),
        'volatility': float(portfolio.annualized_standard_deviation),
        'sharpe_ratio': float(portfolio.annualized_sharpe_ratio),
        'max_drawdown': float(portfolio.max_drawdown)
    }

@route('POST', '/portfolio/hierarchical')
async def hierarchical_portfolio(scope, data):
    """Hierarchical Risk Parity"""
    symbols = data.get('symbols', [])
    prices = load_sp500_dataset()
    available = [s for s in symbols if s in prices.columns]
    prices = prices[available].dropna()
    X = prices_to_returns(prices)

    model = HierarchicalRiskParity()
    model.fit(X)

    return {
        'weights': dict(zip(available, model.weights_.tolist())),
        'method': 'Hierarchical Risk Parity'
    }

@route('GET', '/marketdata')
async def get_market_data(scope, data):
    """Fetch market data from NautilusTrader cache"""
    if not state.trader:
        return {'error': 'Trader not initialized'}

    instrument_id = scope['path'].split('/')[-1]

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
        return {'error': str(e)}

@route('POST', '/trading/order')
async def submit_order(scope, data):
    """Submit order via NautilusTrader"""
    if not state.trader:
        return {'error': 'Trader not initialized'}

    try:
        instrument = InstrumentId.from_str(data['instrument_id'])
        side = OrderSide.BUY if data['side'].upper() == 'BUY' else OrderSide.SELL

        order = state.trader.create_order(
            instrument_id=instrument,
            side=side,
            quantity=float(data['quantity']),
            time_in_force=TimeInForce.GTC
        )

        await state.trader.submit_order(order)

        return {
            'order_id': str(order.client_order_id),
            'status': 'submitted',
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {'error': str(e)}

@route('GET', '/trading/positions')
async def get_positions(scope, data):
    """Get current positions"""
    if not state.trader:
        return {'error': 'Trader not initialized'}

    positions = state.trader.cache.positions()
    return {
        'positions': [{
            'instrument': str(pos.instrument_id),
            'side': pos.side.name,
            'quantity': float(pos.quantity),
            'unrealized_pnl': float(pos.unrealized_pnl)
        } for pos in positions],
        'total_pnl': sum(float(p.unrealized_pnl) for p in positions)
    }

@route('POST', '/backtest/run')
async def run_backtest(scope, data):
    """Run backtest - NOTE: Each worker has isolated cache"""
    strategy_name = data.get('strategy_name', 'unknown')

    # Run in background task
    asyncio.create_task(_backtest_worker(strategy_name, data))

    return {
        'status': 'started',
        'strategy': strategy_name
    }

async def _backtest_worker(name: str, params: Dict):
    """Background backtest execution"""
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.config import BacktestEngineConfig

    try:
        engine_config = BacktestEngineConfig(
            trader_id=f'BACKTEST-{name}-{state.worker_id}',
            logging=LoggingConfig(log_level='INFO')
        )
        engine = BacktestEngine(config=engine_config)
        results = engine.run()

        state.cache[f'backtest_{name}'] = {
            'returns': results.returns.tolist(),
            'sharpe': results.sharpe_ratio()
        }
    except Exception as e:
        state.cache[f'backtest_{name}'] = {'error': str(e)}

async def get_backtest_results(scope, data):
    """Get backtest results from worker-local cache"""
    name = scope['path'].split('/')[-1]
    key = f'backtest_{name}'
    result = state.cache.get(key, {'error': 'Not found'})
    return result

@route('GET', '/health')
async def health_check(scope, data):
    """Health check endpoint"""
    return {
        'status': 'healthy',
        'initialized': state.initialized,
        'kernel_ready': state.kernel is not None,
        'trader_ready': state.trader is not None
    }

# Run with: gunicorn main:app -k uvicorn.workers.UvicornWorker -w 4 --bind 0.0.0.0:8000