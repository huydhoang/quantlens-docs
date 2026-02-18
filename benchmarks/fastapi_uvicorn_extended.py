
"""
Stack 4: FastAPI + Uvicorn + NautilusTrader + skfolio
FastAPI framework served by Uvicorn ASGI server
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List
from datetime import datetime
import pandas as pd
import numpy as np

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

# Pydantic Models
class PortfolioRequest(BaseModel):
    symbols: List[str] = Field(..., example=["AAPL", "MSFT", "GOOGL"])
    risk_measure: str = "variance"
    objective: str = "maximize_ratio"
    lookback_days: int = 252

class TradeRequest(BaseModel):
    instrument_id: str
    side: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: float = Field(..., gt=0)
    time_in_force: str = "GTC"

class BacktestRequest(BaseModel):
    strategy_name: str
    instrument_id: str
    start_time: datetime
    end_time: datetime
    bar_type: str = "1-MINUTE-LAST"

# State
class TradingState:
    def __init__(self):
        self.kernel: Optional[NautilusKernel] = None
        self.trader: Optional[Trader] = None
        self.cache: Dict[str, Any] = {}

state = TradingState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup: Initialize Nautilus Kernel
    config = TradingNodeConfig(
        trader_id='UVICORN-FASTAPI-001',
        logging=LoggingConfig(log_level='INFO'),
        data_clients={
            'BINANCE': BinanceDataClientConfig(
                account_type=BinanceAccountType.SPOT,
            )
        },
    )
    state.kernel = NautilusKernel(config=config)
    state.trader = Trader(kernel=state.kernel)
    print('✅ FastAPI + Uvicorn + NautilusTrader + skfolio initialized')
    yield
    if state.kernel:
        await state.kernel.cleanup()
    print('🛑 Shutdown complete')

app = FastAPI(
    title='FastAPI + Uvicorn Trading API',
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency injection for common checks
def get_trader():
    if not state.trader:
        raise HTTPException(status_code=503, detail="Trading system not initialized")
    return state.trader


@app.get('/health')
async def health_check():
    return {
        'status': 'healthy',
        'initialized': state.kernel is not None,
        'kernel_ready': state.kernel is not None,
        'trader_ready': state.trader is not None
    }

# Portfolio endpoints
@app.post('/portfolio/optimize')
async def optimize_portfolio(request: PortfolioRequest):
    """Optimize portfolio using skfolio"""
    try:
        prices = load_sp500_dataset()
        available = [s for s in request.symbols if s in prices.columns]

        if not available:
            raise HTTPException(status_code=400, detail="No valid symbols")

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
            objective_function=obj_map.get(request.objective, ObjectiveFunction.MAXIMIZE_RATIO),
            risk_measure=risk_map.get(request.risk_measure, RiskMeasure.VARIANCE),
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post('/portfolio/hierarchical')
async def hierarchical_portfolio(request: PortfolioRequest):
    """Hierarchical Risk Parity optimization"""
    prices = load_sp500_dataset()
    available = [s for s in request.symbols if s in prices.columns]
    prices = prices[available].dropna()
    X = prices_to_returns(prices)

    model = HierarchicalRiskParity()
    model.fit(X)

    return {
        'weights': dict(zip(available, model.weights_.tolist())),
        'method': 'Hierarchical Risk Parity'
    }

# Market data endpoints
@app.get('/marketdata/{instrument_id}')
async def get_market_data(
    instrument_id: str,
    data_type: str = Query('bars'),
    trader: Trader = Depends(get_trader)
):
    """Fetch market data from NautilusTrader cache"""
    try:
        instrument = InstrumentId.from_str(instrument_id)

        if data_type == 'bars':
            bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
            bars = trader.cache.bars(bar_type)

            result = [{
                'timestamp': str(bar.ts_event),
                'open': float(bar.open),
                'high': float(bar.high),
                'low': float(bar.low),
                'close': float(bar.close),
                'volume': float(bar.volume)
            } for bar in bars]

            return {'instrument': instrument_id, 'count': len(result), 'data': result}

        elif data_type == 'quotes':
            quote = trader.cache.quote_tick(instrument)
            if not quote:
                raise HTTPException(status_code=404, detail="No quote data available")
            return {
                'instrument': instrument_id,
                'bid': float(quote.bid_price),
                'ask': float(quote.ask_price),
                'timestamp': str(quote.ts_event)
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Trading endpoints
@app.post('/trading/order')
async def submit_order(
    request: TradeRequest,
    trader: Trader = Depends(get_trader)
):
    """Submit order via NautilusTrader"""
    try:
        instrument = InstrumentId.from_str(request.instrument_id)
        side = OrderSide.BUY if request.side == 'BUY' else OrderSide.SELL

        order = trader.create_order(
            instrument_id=instrument,
            side=side,
            quantity=request.quantity,
            time_in_force=TimeInForce.GTC
        )

        await trader.submit_order(order)

        return {
            'order_id': str(order.client_order_id),
            'status': 'submitted',
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/trading/positions')
async def get_positions(trader: Trader = Depends(get_trader)):
    """Get current positions"""
    positions = trader.cache.positions()
    return {
        'positions': [{
            'instrument': str(pos.instrument_id),
            'side': pos.side.name,
            'quantity': float(pos.quantity),
            'unrealized_pnl': float(pos.unrealized_pnl)
        } for pos in positions],
        'total_pnl': sum(float(p.unrealized_pnl) for p in positions)
    }

# Backtest endpoints
@app.post('/backtest/run')
async def run_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks
):
    """Run backtest asynchronously"""
    async def backtest_task():
        from nautilus_trader.backtest.engine import BacktestEngine
        from nautilus_trader.backtest.config import BacktestEngineConfig

        try:
            bt_config = BacktestEngineConfig(
                trader_id=f'BACKTEST-{request.strategy_name}',
                logging=LoggingConfig(log_level='INFO')
            )
            engine = BacktestEngine(config=bt_config)
            results = engine.run()

            state.cache[f'backtest_{request.strategy_name}'] = {
                'returns': results.returns.tolist(),
                'sharpe': results.sharpe_ratio()
            }
        except Exception as e:
            state.cache[f'backtest_{request.strategy_name}'] = {'error': str(e)}

    background_tasks.add_task(backtest_task)
    return {'status': 'started', 'strategy': request.strategy_name}

@app.get('/backtest/results/{strategy_name}')
async def get_backtest_results(strategy_name: str):
    """Get backtest results"""
    key = f'backtest_{strategy_name}'
    if key not in state.cache:
        raise HTTPException(status_code=404, detail='Results not found')
    return state.cache[key]

# WebSocket for real-time data
@app.websocket('/ws/market-data/{instrument_id}')
async def market_data_websocket(websocket: WebSocket, instrument_id: str):
    """WebSocket streaming via NautilusTrader cache"""
    if not state.trader:
        await websocket.close(code=1011, reason="Trader not initialized")
        return

    await websocket.accept()
    instrument = InstrumentId.from_str(instrument_id)

    try:
        while True:
            quote = state.trader.cache.quote_tick(instrument)
            if quote:
                await websocket.send_json({
                    'instrument': instrument_id,
                    'bid': float(quote.bid_price),
                    'ask': float(quote.ask_price),
                    'timestamp': str(quote.ts_event)
                })
            await asyncio.sleep(0.1)
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --loop uvloop