# Portfolio Optimization: Python Package Assessment

An evaluation of the leading Python portfolio optimization libraries — their architectures, feature sets, performance characteristics, and trade-offs — to inform QuantLens's technology selection.

**Decision**: Riskfolio-Lib as the primary package, with skfolio as a strong alternative for ML research workflows and enterprise support, and a finalytics fork as the path toward the performance frontier.

---

## Candidates

| Library | First Release | Philosophy | Backend |
|---------|--------------|------------|---------|
| **Riskfolio-Lib** | 2019 | Comprehensive quantitative strategic asset allocation | CVXPY + commercial solvers |
| **skfolio** | 2024 | Scikit-learn native portfolio optimization | CVXPY + scikit-learn |
| **PyPortfolioOpt** | 2018 | Accessible, modular, classical methods | CVXPY + scipy |
| **finalytics** | 2024 | Rust-powered high-performance analytics | Rust core (PyO3) |

---

## 1. Riskfolio-Lib — Most Comprehensive

**Architecture**: Single integrated framework built on CVXPY with a monolithic `Portfolio` class and parameter-driven configuration. Supports commercial solvers (MOSEK, GUROBI) for large-scale problems.

**Dependencies**: `cvxpy` ≥1.7.2, `scipy` ≥1.16.0, `clarabel` ≥0.11.1, `scs` ≥3.2.7, `pybind11` ≥2.10.1

**Key Strengths**:
- **24+ convex risk measures** — the widest coverage of any Python library, including Entropic VaR, Relativistic VaR, Tail Gini, Square Root Kurtosis
- **6 drawdown measures**: ADD, Ulcer Index, CDaR, EDaR, RLDaR, MDD
- **Hierarchical methods**: HRP, HERC (Hierarchical Equal Risk Contribution), NCO with 35 risk measures
- **Advanced constraints**: Graph-based, cardinality, mutually exclusive assets, tracking error, turnover
- **Factor models**: Built-in risk factor modeling with PCA and stepwise regression
- **Uncertainty sets**: Robust optimization for mean and covariance
- **Optimization paradigms**: Mean-Risk, Logarithmic Mean Risk (Kelly), Risk Parity, OWA, Worst Case

**Unique Features**:
- OWA (Ordered Weighted Averaging) optimization
- Relaxed Risk Parity
- Augmented Black-Litterman Bayesian model
- Excel/Jupyter reporting tools

**Limitations**:
- Steeper learning curve than PyPortfolioOpt or skfolio
- Single maintainer with consulting fee model for support
- Monolithic design — less composable than scikit-learn-style APIs
- No native ML pipeline integration

**Maturity**: v7.2.0 (2025), rapid feature expansion, good technical documentation with academic focus.

---

## 2. skfolio — Emerging Leader for ML Workflows

**Architecture**: Built natively on the scikit-learn API (`fit-predict-transform` paradigm). Modular design with estimators inheriting from `BaseEstimator`. Python 3.10+, BSD 3-clause license, backed by Skfolio Labs (enterprise support available).

**Key Strengths**:
- **Native ML integration**: Compatible with `GridSearchCV`, `Pipeline`, cross-validation out of the box
- **Advanced cross-validation**: Combinatorial Purged CV (CPCV), Walk Forward, Multiple Randomized CV
- **15+ risk measures**: Variance, CVaR, EVaR, CDaR, Ulcer Index, Gini Mean Difference, etc.
- **State-of-the-art estimators**: Gerber Covariance, Denoising, Detoning, Vine Copulas
- **Ensemble methods**: Stacking Optimization combining multiple estimators
- **Factor models**: Full integration with Black-Litterman, Entropy Pooling, Opinion Pooling

**Unique Features**:
- Synthetic data generation for stress testing via Vine Copulas
- NCO with parallelization
- Transaction costs and management fees modeling
- Cardinality constraints (integer programming)

**Limitations**:
- Fewer risk measures than Riskfolio-Lib (15+ vs 24+)
- Newer library — less battle-tested in production
- Requires Python 3.10+

**Maturity**: >95% test coverage, active development, arXiv paper (2025), enterprise backing.

---

## 3. PyPortfolioOpt — Most Accessible

**Architecture**: Scikit-learn inspired (but not strictly compatible) with clean separation of concerns — expected returns, risk models, objectives, and optimizers are independently swappable. Built on `cvxpy`, `scipy`, `pandas`, `numpy`.

**Key Strengths**:
- **Clean API**: Intuitive modular design, best-in-class documentation (ReadTheDocs, cookbook tutorials)
- **Classical methods**: Mean-Variance, Black-Litterman, Critical Line Algorithm (CLA), HRP
- **Shrinkage methods**: Ledoit-Wolf, Oracle Approximating, manual shrinkage
- **Discrete allocation**: Converts portfolio weights to actual share counts — unique among these libraries
- **Robustness**: Handles missing data and different price series lengths gracefully

**Limitations**:
- Limited risk measures (5-6: Std Dev, Semi-variance, CVaR, CDaR)
- No native ML integration or cross-validation
- Smaller feature set overall — no graph constraints, robust optimization, or factor models
- HRP only (no HERC or NCO)

**Maturity**: JOSS publication (2021), GC.OS sponsored, close to 100% pytest coverage, active community (Discord, LinkedIn). Stable API since 2018.

---

## 4. finalytics — Rust-Powered Performance Frontier

**Architecture**: Rust core with Python bindings via PyO3. Four core modules: Screener, Ticker, Tickers, Portfolio. Combines data retrieval, analysis, and optimization in a single high-performance interface.

**Key Strengths**:
- **Speed**: Rust backend offers 10-100x performance over pure Python for compute-bound operations
- **Unified interface**: Data retrieval + analysis + optimization in one library
- **Multi-asset**: Equities, crypto, benchmarks

**Limitations**:
- Newest and least mature library in this assessment
- Limited documentation and smaller community
- Single maintainer
- Feature set not comparable to Riskfolio-Lib or skfolio
- Basic risk measures only

**Best For**: Performance-critical applications where Rust's speed advantage outweighs ecosystem maturity. A fork of this library could serve as the basis for a high-performance optimization core.

---

## Comparative Matrix

| Feature | Riskfolio-Lib | skfolio | PyPortfolioOpt | finalytics |
|---------|---------------|---------|----------------|------------|
| **Risk Measures** | **24+** | 15+ | 5-6 | Basic |
| **API Design** | Parameter-driven | scikit-learn native | Modular | Functional |
| **ML Integration** | Limited | **Native** | None | None |
| **Cross-Validation** | Basic | **CPCV, Walk Forward** | None | None |
| **Hierarchical Methods** | **HRP, HERC, NCO** | HRP, HERC, NCO | HRP only | None |
| **Advanced Constraints** | **Graph, cardinality, mutual exclusion** | Cardinality | Limited | None |
| **Factor Models** | **PCA, stepwise regression** | Black-Litterman, Entropy Pooling | Basic | None |
| **Robust Optimization** | **Uncertainty sets** | Limited | None | None |
| **Speed** | Moderate | Moderate | Moderate | **Fast (Rust)** |
| **Ease of Use** | Medium | High | **High** | Medium |
| **Academic Rigor** | **High** | **High** | Medium | Low |
| **Enterprise Support** | No | **Available** | No | No |
| **Test Coverage** | Comprehensive | >95% | ~100% | Limited |

---

## Performance & Computational Architecture

All CVXPY-based libraries (Riskfolio-Lib, skfolio, PyPortfolioOpt) share a fundamental bottleneck: CVXPY's high-level abstraction layer adds significant overhead — up to 50x slower than direct solver calls in some benchmarks.

| Aspect | Riskfolio-Lib | skfolio | PyPortfolioOpt | finalytics |
|--------|---------------|---------|----------------|------------|
| Small portfolios (<50 assets) | Fast enough | Fast enough | Fast enough | Fast |
| Large portfolios (>500 assets) | Moderate (commercial solvers) | Moderate | Slow | Fast |
| Hierarchical methods | Fast | Fast | Fast | N/A |
| Integer constraints | Slow (MIP) | Slow (MIP) | N/A | N/A |

Riskfolio-Lib has an edge at scale through MOSEK/GUROBI support. For truly performance-critical workloads, a Rust core (as in finalytics) or direct C++ solver bindings bypass the CVXPY bottleneck entirely.

### High-Performance Path

For production systems requiring sub-millisecond optimization or large-scale Monte Carlo:

```
┌─────────────────────────────────────┐
│  Python Interface                   │
│  (Riskfolio-Lib / skfolio API)      │
├─────────────────────────────────────┤
│  Cython/Rust Shim Layer             │
│  (Data preprocessing, validation)   │
├─────────────────────────────────────┤
│  C++/Rust Core Optimizer            │
│  (MOSEK, direct OSQP/ECOS/SCS)      │
├─────────────────────────────────────┤
│  GPU Acceleration (Optional)        │
│  (NVIDIA cuOPT for large-scale)     │
└─────────────────────────────────────┘
```

Currently no mature open-source Rust portfolio optimization library matches Riskfolio-Lib's feature richness — this is the gap a finalytics fork could fill over time.

---

## Decision

### Primary: Riskfolio-Lib

Riskfolio-Lib is the primary choice for QuantLens portfolio optimization due to its unmatched breadth of risk measures (24+), advanced constraint support (graph-based, cardinality, mutual exclusion), robust optimization with uncertainty sets, and full factor modeling capabilities. It covers the widest range of academic and practitioner use cases in a single library.

**Trade-offs accepted**:
- Steeper learning curve (mitigated by good technical docs)
- Single maintainer risk (mitigated by CVXPY-based architecture — models are portable)
- CVXPY performance ceiling (mitigated by commercial solver support and the high-performance path below)

### Alternative: skfolio (Research & Enterprise)

skfolio is the preferred alternative when ML pipeline integration, reproducible research workflows, or enterprise support are required. Its native scikit-learn compatibility makes it ideal for hyperparameter tuning, cross-validation, and model stacking — capabilities Riskfolio-Lib lacks.

**Use when**:
- Building ML-driven allocation strategies (GridSearchCV, Pipeline)
- Requiring Combinatorial Purged Cross-Validation or Walk Forward analysis
- Needing enterprise support (Skfolio Labs)
- Working with ensemble/stacking optimization methods

### Alternative: finalytics Fork (Performance Frontier)

A fork of finalytics provides the path toward Rust-powered optimization for performance-critical workloads. The 10-100x speed advantage over pure Python makes it the right foundation for high-frequency or large-universe scenarios.

**Use when**:
- Optimization latency is a hard constraint
- Processing large asset universes (>500 instruments) at high frequency
- Building Rust-native quant infrastructure

**Caveat**: This requires investment in extending finalytics' limited feature set. The fork strategy allows building toward Riskfolio-Lib-equivalent features on a Rust core over time.

### Not Selected: PyPortfolioOpt

PyPortfolioOpt has the best engineering practices (near-100% test coverage, JOSS publication, stable API, active community) and the cleanest API for classical methods. However, its limited risk measure coverage (5-6) and lack of advanced constraints, factor models, or ML integration make it insufficient for QuantLens's requirements. It remains an excellent educational and prototyping tool but is superseded by Riskfolio-Lib and skfolio for production and research use cases respectively.
