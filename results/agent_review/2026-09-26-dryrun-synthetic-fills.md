# Overnight review 2026-09-26-dryrun-synthetic-fills

> WARNING: 2976 of 2976 outcomes are SYNTHETIC: this report is a plumbing check, not evidence

## Calibration

* resolved directional decisions: 2069 of 2976 outcomes (3000 decisions logged)
* hit rate: 0.493
* Brier: 0.354 (reference 0.250); skill: -0.416

| confidence bin | n | mean confidence | observed frequency |
|---|---|---|---|
| [0.5, 0.6) | 373 | 0.551 | 0.509 |
| [0.6, 0.7) | 377 | 0.650 | 0.454 |
| [0.7, 0.8) | 314 | 0.749 | 0.455 |
| [0.8, 0.9) | 309 | 0.853 | 0.531 |
| [0.9, 1.0) | 696 | 0.957 | 0.507 |

### By source

* rule: n=2069 Brier=0.354

### By regime

* trending: n=783 Brier=0.404
* mean_reverting: n=1284 Brier=0.323
* high_vol: n=2 Brier=0.303

## Trading

* fills: 142; closed trades: 71
* realised: 13.94 USD; fees: 63.70 USD; net: -49.77 USD
* mean win / mean loss: 6.05 / 1.51
* equity: 9999.45 -> 9950.28; max drawdown 0.78%
* kill events: 0

## Proposals (apply only with --approve; risk limits are never proposed)

```json
{
 "date": "2026-09-26-dryrun-synthetic-fills",
 "policy_updates": {
  "min_confidence": 0.85,
  "payoff_ratio": 4.016
 },
 "reasons": [
  "overconfident bucket(s): [0.6,0.7) obs 0.45 n=377, [0.7,0.8) obs 0.46 n=314, [0.8,0.9) obs 0.53 n=309, [0.9,1.0) obs 0.51 n=696",
  "payoff_ratio measured from 71 closed trades",
  "do not arm live: need Brier skill > 0 on >= 200 real resolved decisions and net P&L > 0 after fees"
 ],
 "rule_calibration": {
  "0.50": 0.509,
  "0.60": 0.454,
  "0.70": 0.455,
  "0.80": 0.531,
  "0.90": 0.507
 },
 "do_not_arm_live": true
}
```
