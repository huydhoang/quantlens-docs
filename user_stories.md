# User Stories

## Quant

- As a quant, I need a **local-first application** for alpha research, strategy backtesting, and portfolio optimization that I can run entirely on my own machine with a single `docker compose up` — no cloud accounts, no managed services, no API keys required for core functionality.

- As a quant, I need a **fast, responsive UI** to backtest my strategies and a Monaco-based editor with support for Python syntax highlighting to edit them.

- As a quant, I need the NautilusTrader package for backtesting and trade execution to leverage its performance advantages thanks to a Rust core and event-driven processing, while maintaining excellent ecosystem compatibility with Python bindings.

- As a quant, I need the system to integrate efficiently with skfolio for ready-to-use portfolio optimization functions.

- As a quant, I need the system to ingest data from various API sources like Tiingo for EOD history, Finnhub for fundamental data and cross-validation of historical prices, Alpaca for near-real-time intraday data, and last but not least, bring-your-own data (custom datasets I upload via the app UI).

- As a quant, I want to **submit my backtesting results** to the QuantLens platform to showcase my strategies' real-world performance and track live deployment results alongside other quants.

## Developer

- As a developer, I need a system design specification in which each architectural decision is deeply analyzed with consideration for real-world workloads, potential bottlenecks, and practical trade-offs.

- As a developer, I need each integration in the tech stack to be verified against the relevant technical documentation from official sources and relevant bug reports and community tutorials on the web.

- As a developer, I need the entire local application to be **Dockerized** so that all services (backend, databases, Redis, workers) start with a single `docker compose up` command, ensuring consistent environments across development machines.
