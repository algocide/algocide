# Overnight review 2026-09-26-dryrun-synthetic

> WARNING: 1176 of 1176 outcomes are SYNTHETIC: this report is a plumbing check, not evidence

## Calibration

* resolved directional decisions: 797 of 1176 outcomes (1200 decisions logged)
* hit rate: 0.471
* Brier: 0.314 (reference 0.250); skill: -0.255

| confidence bin | n | mean confidence | observed frequency |
|---|---|---|---|
| [0.5, 0.6) | 157 | 0.553 | 0.567 |
| [0.6, 0.7) | 139 | 0.651 | 0.468 |
| [0.7, 0.8) | 501 | 0.746 | 0.441 |
| [0.8, 0.9) | 0 | n/a | n/a |
| [0.9, 1.0) | 0 | n/a | n/a |

### By source

* rule: n=797 Brier=0.314

### By regime

* trending: n=297 Brier=0.319
* mean_reverting: n=500 Brier=0.310

## Trading

* fills: 0; closed trades: 0
* realised: 0 USD; fees: 0 USD; net: 0 USD
* mean win / mean loss: n/a / n/a
* equity: 10000.00 -> 10000.00; max drawdown 0.00%
* kill events: 0

## Proposals (apply only with --approve; risk limits are never proposed)

```json
{
 "date": "2026-09-26-dryrun-synthetic",
 "policy_updates": {
  "min_confidence": 0.85
 },
 "reasons": [
  "overconfident bucket(s): [0.6,0.7) obs 0.47 n=139, [0.7,0.8) obs 0.44 n=501",
  "do not arm live: need Brier skill > 0 on >= 200 real resolved decisions and net P&L > 0 after fees"
 ],
 "rule_calibration": {
  "0.50": 0.567,
  "0.60": 0.468,
  "0.70": 0.441
 },
 "do_not_arm_live": true
}
```
