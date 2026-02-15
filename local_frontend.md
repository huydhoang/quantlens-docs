# Local Desktop Frontend: Tech Stack Decision

## Decision Summary

**Tauri + Vite + React + TanStack Query/Router** is the frontend stack for QuantLens's local desktop application. This replaces the previous TanStack Start plan. For a local desktop app handling real-time financial data via FastAPI + WebSocket, this stack offers the best balance of performance, WebSocket handling, and developer experience — without the overhead of SSR-oriented frameworks.

---

## Context

QuantLens is a **local-first** desktop application for alpha research, strategy backtesting, and portfolio optimization. The frontend must support:

1. **Real-time data streaming** — WebSocket connections for backtest progress and market data
2. **Data-intensive dashboards** — Charts, equity curves, trade history, portfolio analytics
3. **Code editing** — Monaco Editor for Python strategy authoring
4. **Local execution** — Runs on the user's machine alongside a FastAPI backend; no SEO, no server-side rendering needed
5. **Lightweight distribution** — Small bundle size and low memory footprint for a desktop app

The previous plan used **TanStack Start** (a full-stack SSR framework). Upon evaluation, its server-side rendering capabilities are unnecessary for a local desktop app and add complexity without benefit.

---

## Candidates Evaluated

### Desktop Shell: Tauri vs Electron

| Metric | Tauri | Electron | Notes |
|--------|-------|----------|-------|
| **Bundle Size** | ~2–10 MB | ~80–150 MB | Critical for distribution |
| **Memory Usage** | 30–50 MB idle | 150–300 MB idle | Essential for data-intensive apps |
| **Startup Time** | 0.3–1s | 1–3s | Better UX for frequent restarts |
| **Security** | Capability-based (locked by default) | Node.js access (open by default) | Tauri's Rust backend is safer |
| **WebSocket Support** | Native via Rust or frontend | Node.js `ws` library | Both work well |

**Verdict:** Tauri wins decisively. The 10x smaller bundle and 5x lower memory footprint matter enormously for a trading app that runs locally and processes large datasets.

### Frontend Framework: Vite SPA vs TanStack Start vs Next.js vs Astro

| Feature | Vite + React SPA | TanStack Start | Next.js | Astro |
|---------|-----------------|----------------|---------|-------|
| **Architecture** | Client-side SPA | Full-stack (SSR/SSG) | Full-stack (SSR/RSC) | Content-first (Islands) |
| **Best for** | Desktop apps, SPAs | SSR web apps | SEO, server-rendered apps | Static content sites |
| **Bundle size** | Smallest (client-only) | Larger (hydration code) | Larger (RSC, caching) | Optimized for content |
| **Desktop integration** | Perfect fit | Overkill | Over-engineered | Wrong paradigm |
| **WebSocket handling** | Native, straightforward | May need workarounds | Requires workarounds in App Router | Not designed for this |
| **Dev server** | Vite-native HMR | Vite-powered | Turbopack | Vite-powered |
| **Complexity** | Low, explicit | Medium | High (caching, RSC boundaries) | Medium (islands) |

**Verdict:** For a local desktop app, SSR capabilities are unnecessary — SEO and TTFB don't matter. A Vite-based SPA gives faster dev server (Vite HMR), smaller bundle (no server hydration code), simpler mental model, and direct WebSocket management without framework abstraction.

- **TanStack Start** shines for web apps needing SSR but adds complexity without benefit for a desktop use case.
- **Next.js** is over-engineered for a local app — RSC, PPR, and Vercel optimizations are not needed.
- **Astro** is designed for content-heavy websites (blogs, docs), not data-intensive dashboards.

### Build Tool: Vite vs Rspack

| Aspect | Vite | Rspack | Impact for Tauri |
|--------|------|--------|------------------|
| **Dev Server Model** | Native ESM, on-demand | Webpack-compatible bundler | Vite's ESM is faster for SPAs |
| **Production Build** | Rollup (420ms) | Rust-based (595ms) | Vite faster for production |
| **HMR Speed** | ~136ms | ~125ms | Both fast enough |
| **Tauri Integration** | Official plugin (`vite-plugin-tauri`) | No official Tauri plugin | Vite has first-class support |
| **Config** | Zero-config for React | Webpack-like config required | Vite is simpler |
| **Plugin Ecosystem** | 1,200+ plugins, 73k GitHub stars | ~200 plugins, 9k stars | Vite is more mature |

**Verdict:** Rspack is a Webpack replacement, not a Vite competitor. Tauri has first-class Vite support with an official plugin. Rspack would require custom Tauri configuration and adds configuration complexity without benefit for a desktop SPA.

**When Rspack might make sense (not our case):**
- Massive codebases (10k+ components)
- Webpack migration (drop-in config replacement)
- Module Federation / micro-frontends

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Tauri (Rust Core)                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  WebView (Chromium)                    │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │              Vite + React SPA                    │  │  │
│  │  │  ┌─────────────┐  ┌─────────────────────────┐  │  │  │
│  │  │  │  TanStack   │  │      WebSocket          │  │  │  │
│  │  │  │   Query     │◄─┤    Connection Manager   │  │  │  │
│  │  │  │  (REST API) │  │   (Real-time updates)   │  │  │  │
│  │  │  └─────────────┘  └─────────────────────────┘  │  │  │
│  │  │  ┌─────────────────────────────────────────┐    │  │  │
│  │  │  │         TanStack Router                 │    │  │  │
│  │  │  │    (Type-safe routing)                  │    │  │  │
│  │  │  └─────────────────────────────────────────┘    │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │   FastAPI +      │
                    │   Uvicorn        │
                    │   (Localhost)    │
                    └──────────────────┘
```

### Stack Breakdown

```
Tauri v2
├── Vite 6/7 (or 8 with Rolldown)
│   ├── @vitejs/plugin-react (SWC-based, Fast Refresh)
│   ├── vite-plugin-tauri (official integration)
│   └── TanStack Router (file-based routing)
├── React 19
├── TanStack Query (REST + WebSocket integration)
└── TanStack Router (type-safe routing)
```

---

## Key Implementation Details

### WebSocket + TanStack Query Integration

TanStack Query handles REST (CRUD operations), while WebSocket provides real-time streaming. The two integrate via TanStack Query's cache:

```typescript
// Use TanStack Query for REST (CRUD operations)
const { data: strategies } = useQuery({
  queryKey: ['strategies'],
  queryFn: fetchStrategies
})

// WebSocket for real-time backtest progress
useEffect(() => {
  const ws = new WebSocket('ws://localhost:8000/ws/backtest')

  ws.onmessage = (event) => {
    const progress = JSON.parse(event.data)
    // Push directly into Query cache for unified state
    queryClient.setQueryData(['backtest', progress.id], progress)
  }

  return () => ws.close()
}, [])
```

This gives the best of both worlds: TanStack Query's caching/invalidation for REST endpoints, and direct WebSocket integration for streaming backtest progress and market data.

### Why This Resolves Previous Open Questions

The TanStack Start approach left several verification items unresolved:

| Previous Concern | Resolution with Tauri + Vite |
|-----------------|------------------------------|
| WebSocket support in TanStack Start | WebSocket is native in the browser — no framework abstraction needed |
| Deployment architecture (Vercel/Edge feasibility) | Not applicable — Tauri runs locally as a desktop app |
| TanStack Start stability (RC / v0) | Eliminated dependency on pre-release framework |
| SSR + Python backend complexity | No SSR — direct REST/WebSocket to FastAPI |

---

## Final Comparison

| Stack | Score | Why |
|-------|-------|-----|
| **Tauri + Vite + React + TanStack** | ⭐⭐⭐⭐⭐ | Optimal for local desktop, real-time data |
| TanStack Start + Docker | ⭐⭐⭐⭐ | Good but overkill without SSR needs |
| Next.js + Tauri | ⭐⭐⭐ | Unnecessary complexity |
| Electron + anything | ⭐⭐⭐ | Bloated, higher memory |
| Astro | ⭐⭐ | Wrong paradigm for data apps |

**Bottom line:** For a FastAPI-backed desktop trading app with WebSocket streaming, the leanest, most explicit stack wins. Tauri provides the efficient desktop shell, Vite gives the fastest dev experience, and TanStack Query/Router handle the data layer with type safety. Full-stack frameworks add overhead without benefit for local applications.
