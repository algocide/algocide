"""Data sources. All read-only. `offline` = the committed panel; `hyperliquid` = public info endpoints;
`edgar` = SEC public files. Network calls are injected (`get`/`post` callables) so every parser is testable offline."""
