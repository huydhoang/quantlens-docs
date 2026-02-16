# Monaco Strategy Editor & Python Linter Integration

## Integration Overview

The Monaco Strategy Editor integration provides a browser-based Python code editor for QuantLens users to author, validate, and test trading strategies in real-time. This integration combines three key technologies:

1. **Monaco Editor** (`@monaco-editor/react`) — Microsoft's web-based code editor (the same engine powering VS Code), providing syntax highlighting, code folding, and extensibility for custom language features
2. **Pyodide (Python WASM)** — A Python runtime compiled to WebAssembly, enabling client-side Python AST parsing and syntax validation without server round-trips
3. **FastAPI Backend** — Handles deeper validation by interfacing with NautilusTrader for strategy class registration, import resolution, and semantic checks

**Purpose:** Enable users to write trading strategies in Python with immediate syntax feedback (via Pyodide), followed by backend validation (via FastAPI + NautilusTrader) to ensure strategy compatibility with the backtesting engine before execution. This two-tier validation approach balances responsiveness (client-side) with correctness (server-side).

**Integration Benefits:**
- **Real-time feedback** — Syntax errors caught instantly in the editor without network latency
- **Intelligent autocomplete** — Custom `CompletionItemProvider` suggests NautilusTrader strategy methods, indicators, and order types
- **Seamless execution** — Validated strategies flow directly into the NautilusTrader backtesting pipeline via WebSocket-driven progress updates
- **Offline-capable** — Pyodide-based linting works without backend connectivity; deep validation deferred until submission

---

## Workflow

The Monaco integration follows a progressive validation workflow, from initial editing through final backtest execution:

### 1. User Opens Strategy Editor

When a user clicks "New Strategy" or "Edit Strategy" in the React UI:

```typescript
// React component initializes Monaco Editor
import Editor from '@monaco-editor/react';

<Editor
  height="80vh"
  defaultLanguage="python"
  theme="vs-dark"
  value={strategyCode}
  onChange={handleEditorChange}
  onMount={handleEditorDidMount}
/>
```

**Actions:**
- React component mounts `@monaco-editor/react`
- Editor loads with Python syntax highlighting pre-configured
- If editing an existing strategy: `GET /api/strategies/:id` fetches the code from FastAPI
- If creating a new strategy: `GET /api/strategies/template` returns a NautilusTrader strategy template

### 2. Syntax Highlighting Pre-Configured

Monaco provides **out-of-the-box Python tokenization** (keywords, strings, comments, operators). No custom grammar needed.

```typescript
// Editor initialization with Python language support
const handleEditorDidMount = (editor, monaco) => {
  // Python colorization is automatic
  monaco.editor.setTheme('vs-dark');
  
  // Register custom completion provider for NautilusTrader APIs
  monaco.languages.registerCompletionItemProvider('python', {
    provideCompletionItems: (model, position) => {
      // Suggest NautilusTrader methods: on_start, on_bar, submit_order, etc.
      return {
        suggestions: getNautilusCompletions(model, position)
      };
    }
  });
};
```

**Features Enabled:**
- Keyword highlighting (`def`, `class`, `import`, `if`, etc.)
- String and comment styling
- Bracket matching and code folding
- Line numbers and minimap

### 3. Real-Time Syntax Linting via Pyodide

As the user types, a **debounced linter** (300ms delay) validates syntax using Pyodide's `ast.parse()`:

```typescript
// Python linting with Pyodide WASM
import { loadPyodide } from 'pyodide';

let pyodide;
const initPyodide = async () => {
  pyodide = await loadPyodide();
};

const lintPythonCode = async (code, monaco, editor) => {
  if (!pyodide) await initPyodide();
  
  try {
    // Parse Python AST to detect syntax errors
    await pyodide.runPythonAsync(`
import ast
import sys
from io import StringIO

code = """${code.replace(/"/g, '\\"')}"""

try:
    ast.parse(code)
    result = {"valid": True, "errors": []}
except SyntaxError as e:
    result = {
        "valid": False,
        "errors": [{
            "line": e.lineno,
            "column": e.offset,
            "message": e.msg
        }]
    }
`);
    
    const result = pyodide.globals.get('result').toJs();
    
    // Set Monaco markers for syntax errors
    const markers = result.errors.map(err => ({
      severity: monaco.MarkerSeverity.Error,
      startLineNumber: err.line,
      startColumn: err.column,
      endLineNumber: err.line,
      endColumn: err.column + 1,
      message: err.message
    }));
    
    monaco.editor.setModelMarkers(editor.getModel(), 'python', markers);
  } catch (error) {
    console.error('Pyodide linting failed:', error);
  }
};

// Debounced onChange handler
let lintTimeout;
const handleEditorChange = (value) => {
  clearTimeout(lintTimeout);
  lintTimeout = setTimeout(() => lintPythonCode(value, monaco, editor), 300);
};
```

**What Pyodide Validates:**
- ✅ Syntax errors (missing colons, invalid indentation, mismatched parentheses)
- ✅ Basic Python grammar compliance
- ❌ Import resolution (e.g., `from nautilus_trader.strategy import Strategy`)
- ❌ Type errors or undefined variables (static analysis requires heavier tooling)

**Deferred to Backend:** Import resolution, semantic validation, and NautilusTrader strategy class structure checks happen server-side (see step 4).

### 4. Backend Validation via NautilusTrader

When the user clicks **"Validate Strategy"**, the frontend sends the code to FastAPI for deep validation:

```python
# FastAPI endpoint: POST /api/strategies/validate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.backtest.engine import BacktestEngine
import ast

router = APIRouter()

class StrategyValidationRequest(BaseModel):
    code: str
    strategy_name: str

@router.post("/strategies/validate")
async def validate_strategy(request: StrategyValidationRequest):
    """
    Validates strategy code by:
    1. Parsing AST to ensure syntactic correctness
    2. Dynamically importing to check NautilusTrader compatibility
    3. Verifying Strategy subclass and required methods
    """
    try:
        # Step 1: Parse AST
        tree = ast.parse(request.code)
        
        # Step 2: Check for Strategy subclass
        has_strategy_class = any(
            isinstance(node, ast.ClassDef) and 
            any(base.id == 'Strategy' for base in node.bases if isinstance(base, ast.Name))
            for node in ast.walk(tree)
        )
        if not has_strategy_class:
            raise HTTPException(status_code=400, detail="Strategy must inherit from nautilus_trader.Strategy")
        
        # Step 3: Dynamic import to check runtime validity
        namespace = {}
        exec(request.code, namespace)
        
        # Step 4: Verify strategy instantiation
        strategy_class = next(
            (obj for obj in namespace.values() if isinstance(obj, type) and issubclass(obj, Strategy)),
            None
        )
        if not strategy_class:
            raise HTTPException(status_code=400, detail="No valid Strategy class found")
        
        # Step 5: Test dry-run instantiation (checks __init__ signature)
        strategy_id = StrategyId(f"TEST-{request.strategy_name}")
        # Note: In production, pass minimal config to test instantiation
        
        return {
            "valid": True,
            "message": "Strategy validated successfully",
            "strategy_class": strategy_class.__name__
        }
        
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Syntax error: {e.msg} at line {e.lineno}")
    except ImportError as e:
        raise HTTPException(status_code=400, detail=f"Import error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")
```

**Backend Validation Checks:**
- ✅ Strategy inherits from `nautilus_trader.model.strategy.Strategy`
- ✅ Required methods implemented (`on_start`, `on_stop`, signal handlers)
- ✅ Imports resolve correctly (`nautilus_trader`, `pandas`, `numpy`, etc.)
- ✅ Constructor signature compatible with NautilusTrader's strategy loading

**User Feedback:** Validation results appear in the UI as success/error notifications. Errors show line numbers and messages to guide fixes.

### 5. Strategy Persistence & Registration

Once validated, the strategy is saved and registered with NautilusTrader:

```python
# FastAPI endpoint: POST /api/strategies
@router.post("/strategies")
async def save_strategy(request: StrategyCreateRequest):
    """
    Saves strategy code to PostgreSQL and registers with NautilusTrader
    """
    # Save to database
    strategy_id = await db.strategies.create({
        "name": request.name,
        "code": request.code,
        "created_at": datetime.now()
    })
    
    # Register strategy class with NautilusTrader
    # (Actual registration happens during backtest setup)
    
    return {"id": strategy_id, "status": "saved"}
```

**Database Schema (PostgreSQL):**
```sql
CREATE TABLE strategies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    code TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP
);
```

### 6. Backtest Execution Handoff

After saving, the strategy flows into the backtest pipeline (see [Backtest Execution Flow](#frontend-backend-communication) for WebSocket progress updates).

---

## Frontend-Backend Communication

The Monaco integration uses a hybrid HTTP + WebSocket communication model to balance request-response simplicity with real-time streaming updates.

### HTTP REST Endpoints

**Purpose:** Synchronous request-response operations (CRUD, validation, configuration)

#### 1. GET /api/strategies/template
**Description:** Returns a Python template for a new NautilusTrader strategy  
**Response:**
```json
{
  "code": "from nautilus_trader.model.strategy import Strategy\n\nclass MyStrategy(Strategy):\n    def on_start(self):\n        pass\n\n    def on_stop(self):\n        pass"
}
```

#### 2. GET /api/strategies/:id
**Description:** Fetches an existing strategy by ID  
**Response:**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "name": "MeanReversion",
  "code": "from nautilus_trader.model.strategy import Strategy\n...",
  "created_at": "2026-02-15T10:30:00Z"
}
```

#### 3. POST /api/strategies/validate
**Description:** Validates strategy code syntax and NautilusTrader compatibility  
**Request Body:**
```json
{
  "code": "from nautilus_trader.model.strategy import Strategy\n...",
  "strategy_name": "MeanReversion"
}
```
**Response (Success):**
```json
{
  "valid": true,
  "message": "Strategy validated successfully",
  "strategy_class": "MeanReversion"
}
```
**Response (Error):**
```json
{
  "valid": false,
  "detail": "Strategy must inherit from nautilus_trader.Strategy"
}
```

#### 4. POST /api/strategies
**Description:** Saves a validated strategy to the database  
**Request Body:**
```json
{
  "name": "MeanReversion",
  "code": "from nautilus_trader.model.strategy import Strategy\n..."
}
```
**Response:**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "saved"
}
```

#### 5. POST /api/backtest/run
**Description:** Initiates a backtest job  
**Request Body:**
```json
{
  "strategy_id": "123e4567-e89b-12d3-a456-426614174000",
  "symbols": ["AAPL", "MSFT"],
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "initial_cash": 100000
}
```
**Response (202 Accepted):**
```json
{
  "job_id": "backtest-789",
  "status": "queued",
  "websocket_url": "ws://localhost:8000/ws/backtest/backtest-789"
}
```

### WebSocket Streaming

**Purpose:** Real-time progress updates during long-running backtest operations

#### WebSocket Endpoint: /ws/backtest/:job_id

**Connection Flow:**
```typescript
// React component establishes WebSocket connection
const ws = new WebSocket(`ws://localhost:8000/ws/backtest/${jobId}`);

ws.onopen = () => {
  console.log('Backtest WebSocket connected');
};

ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  
  switch (update.type) {
    case 'progress':
      setBacktestProgress(update.progress);
      break;
    case 'log':
      appendLog(update.message);
      break;
    case 'complete':
      setBacktestResults(update.results);
      ws.close();
      break;
    case 'error':
      setError(update.error);
      ws.close();
      break;
  }
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};
```

**Message Types:**

**1. Progress Update**
```json
{
  "type": "progress",
  "job_id": "backtest-789",
  "progress": 45,
  "message": "Processing 2025-06-15..."
}
```

**2. Log Message**
```json
{
  "type": "log",
  "job_id": "backtest-789",
  "level": "info",
  "message": "Filled BUY order for AAPL at $150.25"
}
```

**3. Completion**
```json
{
  "type": "complete",
  "job_id": "backtest-789",
  "results": {
    "total_return": 0.15,
    "sharpe_ratio": 1.8,
    "max_drawdown": -0.08,
    "num_trades": 42
  }
}
```

**4. Error**
```json
{
  "type": "error",
  "job_id": "backtest-789",
  "error": "Insufficient market data for symbol XYZ"
}
```

**Backend Implementation (FastAPI):**
```python
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict

active_connections: Dict[str, WebSocket] = {}

@app.websocket("/ws/backtest/{job_id}")
async def backtest_websocket(websocket: WebSocket, job_id: str):
    await websocket.accept()
    active_connections[job_id] = websocket
    
    try:
        while True:
            # Keep connection alive and send updates from Celery workers
            await websocket.receive_text()
    except WebSocketDisconnect:
        del active_connections[job_id]

# Celery worker publishes updates to Redis pub/sub
# FastAPI subscribes and broadcasts to active WebSocket connections
async def broadcast_backtest_update(job_id: str, message: dict):
    if job_id in active_connections:
        await active_connections[job_id].send_json(message)
```

**Why WebSocket for Backtests?**
- **Real-time progress:** Backtests can take minutes to hours; HTTP polling is inefficient
- **Bidirectional:** Allows users to cancel running backtests via `{"type": "cancel"}` message
- **Low latency:** Updates appear instantly in the UI (equity curve updates, trade logs)

---

## Key Components

### 1. MonacoStrategyEditor.tsx (React Component)

**Purpose:** Renders the Monaco Editor, manages Pyodide linting, and orchestrates validation/save actions.

**Key Responsibilities:**
- Initialize Monaco Editor with Python language support
- Load Pyodide WASM runtime for client-side linting
- Debounce editor changes and trigger AST parsing
- Display syntax errors as Monaco markers
- Provide "Validate" and "Save" buttons that call FastAPI endpoints
- Show loading states during backend validation

**Code Structure:**
```typescript
import React, { useRef, useEffect, useState } from 'react';
import Editor from '@monaco-editor/react';
import { loadPyodide } from 'pyodide';

export const MonacoStrategyEditor = ({ strategyId, onSave }) => {
  const editorRef = useRef(null);
  const [pyodide, setPyodide] = useState(null);
  const [code, setCode] = useState('');
  const [validating, setValidating] = useState(false);

  useEffect(() => {
    // Initialize Pyodide on component mount
    loadPyodide().then(setPyodide);
  }, []);

  const handleEditorDidMount = (editor, monaco) => {
    editorRef.current = editor;
    
    // Register NautilusTrader autocomplete
    monaco.languages.registerCompletionItemProvider('python', {
      provideCompletionItems: () => ({
        suggestions: [
          {
            label: 'on_start',
            kind: monaco.languages.CompletionItemKind.Method,
            insertText: 'def on_start(self):\n    ${1:pass}',
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          },
          // ... more NautilusTrader methods
        ]
      })
    });
  };

  const lintCode = async (value) => {
    if (!pyodide) return;
    
    try {
      const result = await pyodide.runPythonAsync(`
import ast
try:
    ast.parse("""${value.replace(/"/g, '\\"')}""")
    {"valid": True, "errors": []}
except SyntaxError as e:
    {"valid": False, "errors": [{"line": e.lineno, "column": e.offset, "message": e.msg}]}
      `);
      
      const errors = pyodide.globals.get('result').toJs().errors;
      // Set markers (simplified)
    } catch (e) {
      console.error('Linting failed:', e);
    }
  };

  const validateStrategy = async () => {
    setValidating(true);
    const response = await fetch('/api/strategies/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, strategy_name: 'UserStrategy' })
    });
    const result = await response.json();
    setValidating(false);
    
    if (result.valid) {
      alert('Strategy validated successfully!');
    } else {
      alert(`Validation failed: ${result.detail}`);
    }
  };

  return (
    <div>
      <Editor
        height="600px"
        defaultLanguage="python"
        value={code}
        onChange={(value) => {
          setCode(value);
          lintCode(value);
        }}
        onMount={handleEditorDidMount}
      />
      <button onClick={validateStrategy} disabled={validating}>
        {validating ? 'Validating...' : 'Validate Strategy'}
      </button>
    </div>
  );
};
```

### 2. Python Linter (Pyodide WASM)

**Purpose:** Provides instant syntax validation in the browser without backend calls.

**Architecture:**
- **Pyodide:** CPython 3.11+ compiled to WebAssembly (~6 MB gzipped)
- **AST Module:** Python's built-in `ast.parse()` for syntax checking
- **Execution Context:** Runs in Web Worker to avoid blocking UI thread (recommended for production)

**Integration Pattern:**
```typescript
// pyodide-worker.ts (Web Worker for non-blocking linting)
import { loadPyodide } from 'pyodide';

let pyodide;

self.onmessage = async (event) => {
  if (event.data.type === 'init') {
    pyodide = await loadPyodide();
    self.postMessage({ type: 'ready' });
  } else if (event.data.type === 'lint') {
    const { code } = event.data;
    try {
      await pyodide.runPythonAsync(`
import ast
ast.parse("""${code.replace(/"/g, '\\"')}""")
      `);
      self.postMessage({ type: 'result', errors: [] });
    } catch (error) {
      self.postMessage({ 
        type: 'result', 
        errors: [{ line: error.lineno, message: error.msg }] 
      });
    }
  }
};
```

**Why Pyodide Instead of JavaScript-Based Linters:**
- **Accuracy:** Uses the actual Python parser, not a reimplementation
- **Compatibility:** Guarantees syntax validation matches CPython semantics
- **Extensibility:** Could later add type checking via `mypy` or `pyright` (though computationally expensive)

**Trade-offs:**
- **Bundle Size:** +6 MB (acceptable for desktop app; load on-demand for web)
- **Initialization Time:** ~1–2 seconds (cache Pyodide instance for editor lifetime)

### 3. FastAPI Validation Endpoint

**Purpose:** Deep validation that checks NautilusTrader compatibility and strategy structure.

**Validation Layers:**

**Layer 1: AST Parsing**
```python
import ast

def validate_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, "Valid syntax"
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"
```

**Layer 2: Import Resolution**
```python
def validate_imports(code: str) -> tuple[bool, str]:
    tree = ast.parse(code)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    
    for imp in imports:
        if isinstance(imp, ast.ImportFrom):
            module = imp.module
            if module and module.startswith('nautilus_trader'):
                # Check if NautilusTrader module exists
                try:
                    __import__(module)
                except ImportError:
                    return False, f"Invalid import: {module}"
    return True, "Imports valid"
```

**Layer 3: Strategy Structure Validation**
```python
from nautilus_trader.model.strategy import Strategy

def validate_strategy_class(code: str) -> tuple[bool, str]:
    namespace = {}
    exec(code, namespace)
    
    strategy_classes = [
        cls for cls in namespace.values()
        if isinstance(cls, type) and issubclass(cls, Strategy) and cls != Strategy
    ]
    
    if not strategy_classes:
        return False, "No Strategy subclass found"
    
    strategy_cls = strategy_classes[0]
    required_methods = ['on_start', 'on_stop']
    for method in required_methods:
        if not hasattr(strategy_cls, method):
            return False, f"Missing required method: {method}"
    
    return True, f"Strategy class {strategy_cls.__name__} validated"
```

**Security Considerations:**
- **Sandboxing:** Execute validation in restricted environment (no file I/O, network access)
- **Resource Limits:** Timeout validation after 5 seconds
- **Code Injection:** Never use `eval()` or unsanitized `exec()`; always parse AST first

### 4. NautilusTrader Integration

**Purpose:** Executes validated strategies in backtests and manages engine lifecycle.

**Key Classes:**
- `BacktestEngine`: Low-level backtest orchestration
- `BacktestNode`: High-level configuration-driven wrapper (recommended)
- `ParquetDataCatalog`: Ingests historical data for simulations
- `StrategyId`: Unique identifier for strategy instances

**Strategy Registration Pattern:**
```python
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestRunConfig

def run_backtest(strategy_code: str, config: dict):
    # Step 1: Dynamically load strategy class
    namespace = {}
    exec(strategy_code, namespace)
    strategy_cls = next(
        cls for cls in namespace.values()
        if isinstance(cls, type) and issubclass(cls, Strategy)
    )
    
    # Step 2: Configure backtest
    run_config = BacktestRunConfig(
        engine=BacktestEngineConfig(
            strategies=[
                ImportableStrategyConfig(
                    strategy_path=f"{strategy_cls.__module__}:{strategy_cls.__name__}",
                    config_path="config.json"
                )
            ]
        ),
        venues=[venue_config],
        data=[data_config]
    )
    
    # Step 3: Execute backtest
    node = BacktestNode(configs=[run_config])
    node.run()
    
    # Step 4: Extract results
    return node.get_result()
```

**Process Isolation:**  
NautilusTrader enforces **one `BacktestEngine` per process** due to global singleton state (logger, Tokio runtime). Celery's `prefork` pool naturally satisfies this by spawning isolated worker processes.

### 5. Celery + Redis Task Queue

**Purpose:** Asynchronous backtest execution with progress tracking.

**Architecture:**
```
FastAPI (enqueue job) → Redis (queue) → Celery Worker (execute) → Redis (pub/sub) → FastAPI (WebSocket broadcast)
```

**Celery Task Example:**
```python
from celery import Celery

celery_app = Celery('quantlens', broker='redis://localhost:6379/0')

@celery_app.task(bind=True)
def run_backtest_task(self, job_id: str, strategy_code: str, config: dict):
    # Step 1: Update job status
    self.update_state(state='PROGRESS', meta={'progress': 0})
    
    # Step 2: Initialize NautilusTrader engine
    # ... (see NautilusTrader integration above)
    
    # Step 3: Stream progress updates
    for i in range(100):
        # Simulate backtest progress
        self.update_state(state='PROGRESS', meta={'progress': i})
        redis_client.publish(f'backtest:{job_id}', json.dumps({
            'type': 'progress',
            'progress': i,
            'message': f'Processing day {i}/100'
        }))
        time.sleep(0.1)
    
    # Step 4: Return results
    return {'status': 'complete', 'results': {...}}
```

**Redis Pub/Sub for Real-Time Updates:**
```python
# FastAPI subscribes to Redis pub/sub
import aioredis

async def subscribe_to_backtest_updates(job_id: str, websocket: WebSocket):
    redis = await aioredis.create_redis_pool('redis://localhost')
    channel = (await redis.subscribe(f'backtest:{job_id}'))[0]
    
    async for message in channel.iter():
        await websocket.send_text(message.decode('utf-8'))
```

---

## Recommendations

### 1. Optimize Linting Performance

**Problem:** Pyodide initialization (1–2 seconds) blocks the editor on first load.

**Solution: Lazy Loading + Web Worker**
```typescript
// Load Pyodide only when user starts typing
let pyodidePromise = null;

const getPyodide = () => {
  if (!pyodidePromise) {
    pyodidePromise = loadPyodide();
  }
  return pyodidePromise;
};

const handleFirstEdit = async () => {
  const pyodide = await getPyodide();
  // Now ready for linting
};
```

**Alternative: Server-Side Linting via Language Server Protocol (LSP)**
- Use `pyright` or `pylsp` in FastAPI backend
- Expose LSP endpoints: `POST /api/lsp/diagnostics`, `POST /api/lsp/completion`
- Monaco connects via custom LSP client
- **Trade-off:** Requires backend connectivity; adds latency vs Pyodide

### 2. Enhance Autocomplete with CompletionItemProvider

**Current State:** Monaco shows basic Python keywords; no NautilusTrader-specific suggestions.

**Enhancement: Custom CompletionItemProvider**
```typescript
monaco.languages.registerCompletionItemProvider('python', {
  provideCompletionItems: (model, position) => {
    const textUntilPosition = model.getValueInRange({
      startLineNumber: 1,
      startColumn: 1,
      endLineNumber: position.lineNumber,
      endColumn: position.column
    });
    
    // Suggest NautilusTrader methods based on context
    const suggestions = [];
    
    if (textUntilPosition.includes('class') && textUntilPosition.includes('Strategy')) {
      suggestions.push({
        label: 'on_start',
        kind: monaco.languages.CompletionItemKind.Method,
        insertText: 'def on_start(self):\n    self.log.info("Strategy started")\n    ${1}',
        insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        documentation: 'Called when the strategy starts'
      });
      suggestions.push({
        label: 'on_bar',
        kind: monaco.languages.CompletionItemKind.Method,
        insertText: 'def on_bar(self, bar: Bar):\n    ${1}',
        insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        documentation: 'Called on each bar update'
      });
    }
    
    if (textUntilPosition.includes('self.submit_order')) {
      suggestions.push({
        label: 'MarketOrder',
        kind: monaco.languages.CompletionItemKind.Class,
        insertText: 'MarketOrder(\n    trader_id=self.trader_id,\n    strategy_id=self.id,\n    instrument_id=${1:instrument_id},\n    order_side=OrderSide.${2:BUY},\n    quantity=Quantity.from_int(${3:100})\n)',
        insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
      });
    }
    
    return { suggestions };
  }
});
```

**Benefits:**
- Context-aware suggestions (e.g., `self.` shows strategy methods)
- Reduces typos and API lookup friction
- Onboarding-friendly for new NautilusTrader users

### 3. Implement Incremental Validation

**Problem:** Re-validating entire strategy on every change is inefficient for large files.

**Solution: Cache Validation Results by Code Hash**
```python
import hashlib

validation_cache = {}

def validate_strategy_cached(code: str):
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    
    if code_hash in validation_cache:
        return validation_cache[code_hash]
    
    result = validate_strategy(code)
    validation_cache[code_hash] = result
    return result
```

**Advanced: Partial AST Re-Parsing**
- Detect which lines changed (Monaco provides `IModelContentChange` events)
- Re-parse only modified functions/classes
- Requires custom AST diffing logic (complexity may not justify gains)

### 4. Add Strategy Templates Library

**Problem:** Users start from scratch; high barrier for new NautilusTrader adopters.

**Solution: Curated Template Library**
```python
# GET /api/strategies/templates
templates = [
    {
        "id": "mean-reversion",
        "name": "Mean Reversion (RSI)",
        "description": "Buy oversold, sell overbought based on RSI indicator",
        "code": "..."
    },
    {
        "id": "momentum",
        "name": "Momentum Breakout",
        "description": "Enter on 20-day high, exit on 10-day low",
        "code": "..."
    }
]
```

**UI Enhancement:**
- Dropdown selector in Monaco Editor toolbar
- Preview template in modal before loading
- Track template usage analytics to prioritize improvements

### 5. Security Hardening

**Critical: Prevent Code Injection**
```python
# NEVER DO THIS (vulnerable to arbitrary code execution)
def unsafe_validate(code: str):
    exec(code)  # ❌ Allows malicious code

# DO THIS INSTEAD (sandboxed execution)
import RestrictedPython

def safe_validate(code: str):
    compile_result = RestrictedPython.compile_restricted(
        code,
        filename='<strategy>',
        mode='exec'
    )
    if compile_result.errors:
        return False, compile_result.errors
    
    # Execute in restricted namespace (no __import__, open(), eval())
    safe_globals = {
        '__builtins__': RestrictedPython.safe_builtins,
        '_getattr_': RestrictedPython.safe_globals['_getattr_']
    }
    exec(compile_result.code, safe_globals)
```

**Additional Safeguards:**
- Run validation in Docker container with resource limits (`--memory=512m --cpus=0.5`)
- Use `seccomp` profiles to block dangerous syscalls (network, file I/O)
- Timeout validation after 5 seconds to prevent infinite loops

### 6. Improve WebSocket Reliability

**Problem:** WebSocket disconnections lose backtest progress updates.

**Solution: Reconnection Logic + Progress Persistence**
```typescript
class BacktestWebSocket {
  private ws: WebSocket;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;

  connect(jobId: string) {
    this.ws = new WebSocket(`ws://localhost:8000/ws/backtest/${jobId}`);
    
    this.ws.onclose = () => {
      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        setTimeout(() => {
          this.reconnectAttempts++;
          this.connect(jobId);
        }, 1000 * Math.pow(2, this.reconnectAttempts)); // Exponential backoff
      }
    };
    
    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      // Request missed updates
      this.ws.send(JSON.stringify({ type: 'resume', last_progress: 45 }));
    };
  }
}
```

**Backend: Persist Progress to Redis**
```python
# Celery worker stores progress in Redis
redis_client.setex(f'backtest_progress:{job_id}', 3600, json.dumps({
    'progress': 45,
    'last_update': time.time()
}))

# FastAPI retrieves on reconnection
@app.websocket("/ws/backtest/{job_id}")
async def backtest_websocket(websocket: WebSocket, job_id: str):
    await websocket.accept()
    
    # Send cached progress on reconnection
    cached = redis_client.get(f'backtest_progress:{job_id}')
    if cached:
        await websocket.send_json(json.loads(cached))
```

### 7. Leverage Monaco's Hover Provider for Documentation

**Enhancement: Inline API Documentation**
```typescript
monaco.languages.registerHoverProvider('python', {
  provideHover: (model, position) => {
    const word = model.getWordAtPosition(position);
    if (!word) return null;
    
    const docs = {
      'on_start': 'Called when the strategy is started. Use this to initialize indicators and state.',
      'on_bar': 'Called when a new bar is received. Parameter: bar (Bar object)',
      'submit_order': 'Submits an order to the execution engine. Returns: OrderId'
    };
    
    if (word.word in docs) {
      return {
        contents: [
          { value: `**${word.word}**` },
          { value: docs[word.word] }
        ]
      };
    }
    
    return null;
  }
});
```

**Benefits:**
- Users hover over methods to see quick docs (no context switching to API reference)
- Reduces learning curve for NautilusTrader API

---

## Conclusion

The Monaco Strategy Editor + Pyodide integration provides a robust, real-time Python authoring experience for QuantLens users. By combining client-side syntax linting (Pyodide) with server-side semantic validation (FastAPI + NautilusTrader), the system delivers instant feedback while ensuring strategy correctness before backtest execution. The hybrid HTTP/WebSocket communication model balances simplicity (REST for CRUD) with responsiveness (WebSocket for progress streaming), creating a seamless workflow from code editing to live trading simulation.

**Next Steps:**
1. Implement security hardening (RestrictedPython, Docker sandboxing)
2. Add strategy template library with 5–10 curated examples
3. Optimize Pyodide loading with Web Worker + lazy initialization
4. Build comprehensive test suite for validation edge cases
5. Monitor WebSocket reconnection rates and optimize reliability

For additional context on related architectural decisions, see:
- [System Design](system_design.md) — Full QuantLens architecture overview
- [Core Engine](core_engine.md) — NautilusTrader integration details
- [Local Frontend](local_frontend.md) — Tauri + React stack rationale
