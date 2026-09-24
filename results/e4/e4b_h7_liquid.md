# E4b (post-hoc) — H7 premium reversion in liquid vs illiquid xyz names

Top-15 by median OI (USD): xyz:SP500 ($412.8M), xyz:SKHX ($362.2M), xyz:GOLD ($332.2M), xyz:XYZ100 ($226.0M), xyz:CL ($181.4M), xyz:SKHY ($168.9M), xyz:SPCX ($166.0M), xyz:BRENTOIL ($162.8M), xyz:MU ($140.2M), xyz:SNDK ($138.4M), xyz:NVDA ($132.4M), xyz:SILVER ($131.3M), xyz:GOOGL ($101.8M), xyz:DRAM ($81.7M), xyz:INTC ($69.8M)


## top15_liquid: 951 events, mean |p0| 33.3 bps

| session | n | mean abs p0 bps | reversion 1h [CI] | reversion 6h [CI] | reversion 24h |
|---|---|---|---|---|---|
| external | 315 | 43.2 | 20.4 [12.4, 29.7] | 37.4 [24.1, 53.6] | 41.4 [25.2, 62.1] |
| overnight | 139 | 30.0 | 12.0 [6.8, 18.3] | 19.6 [10.6, 31.6] | 20.6 [14.0, 29.1] |
| weekend | 497 | 28.0 | 14.4 [10.9, 18.2] | 27.0 [22.6, 32.3] | 28.1 [22.5, 34.7] |
| all | 951 | 33.3 | 16.0 [12.5, 20.6] | 29.4 [23.7, 36.3] | 31.4 [25.2, 39.3] |

External-session events by |p0| bucket (bps): <20: n=171, |p0|=11, rev1h=4.4, rev6h=8.1; 20-40: n=40, |p0|=28, rev1h=13.9, rev6h=22.7; 40-80: n=41, |p0|=59, rev1h=16.8, rev6h=49.4; >80: n=63, |p0|=131, rev1h=70.0, rev6h=118.3

## bottom30_illiquid: 605 events, mean |p0| 69.0 bps

| session | n | mean abs p0 bps | reversion 1h [CI] | reversion 6h [CI] | reversion 24h |
|---|---|---|---|---|---|
| external | 234 | 80.8 | 28.7 [21.6, 37.2] | 68.8 [52.0, 88.7] | 73.4 [55.5, 93.0] |
| overnight | 179 | 70.5 | 24.8 [17.5, 33.6] | 59.1 [41.8, 80.5] | 69.4 [47.9, 94.6] |
| weekend | 192 | 53.1 | 19.5 [12.8, 27.5] | 41.7 [31.4, 54.1] | 51.5 [39.2, 65.9] |
| all | 605 | 69.0 | 24.6 [19.9, 29.8] | 57.3 [46.3, 68.2] | 65.2 [53.1, 77.4] |

External-session events by |p0| bucket (bps): <20: n=28, |p0|=13, rev1h=3.8, rev6h=11.2; 20-40: n=61, |p0|=29, rev1h=17.1, rev6h=27.7; 40-80: n=48, |p0|=57, rev1h=19.2, rev6h=46.2; >80: n=97, |p0|=145, rev1h=47.8, rev6h=121.3

## all: 4260 events, mean |p0| 38.0 bps

| session | n | mean abs p0 bps | reversion 1h [CI] | reversion 6h [CI] | reversion 24h |
|---|---|---|---|---|---|
| external | 1238 | 46.2 | 19.1 [15.4, 23.1] | 39.3 [32.2, 47.2] | 43.0 [34.8, 51.1] |
| overnight | 871 | 44.1 | 18.3 [15.4, 21.3] | 35.9 [29.5, 43.2] | 39.1 [32.4, 47.3] |
| weekend | 2151 | 30.8 | 13.5 [11.9, 15.1] | 28.0 [25.9, 30.5] | 30.5 [27.9, 33.2] |
| all | 4260 | 38.0 | 16.1 [14.5, 17.7] | 32.9 [29.9, 35.8] | 35.8 [32.6, 39.3] |

External-session events by |p0| bucket (bps): <20: n=564, |p0|=12, rev1h=3.8, rev6h=8.0; 20-40: n=244, |p0|=28, rev1h=12.8, rev6h=22.5; 40-80: n=189, |p0|=58, rev1h=21.7, rev6h=49.6; >80: n=241, |p0|=136, rev1h=59.4, rev6h=120.4
## Realistic timing: enter at t+1 (after the extreme hour is known), reversion measured from t+1

| subset | condition | session | n | mean |p(t+1)| bps | rev 1h [CI] | rev 3h [CI] | rev 6h [CI] |
|---|---|---|---|---|---|---|---|
| top15_liquid | all events | external | 306 | 25.7 | 10.7 [6.2, 15.5] | 17.1 [9.1, 26.7] | 18.4 [10.9, 26.9] |
| top15_liquid | all events | overnight | 167 | 21.3 | 7.7 [3.2, 12.3] | 9.9 [5.5, 15.0] | 10.2 [4.4, 17.2] |
| top15_liquid | all events | weekend | 478 | 16.6 | 10.1 [7.9, 12.6] | 14.9 [11.6, 18.5] | 15.4 [12.2, 18.8] |
| top15_liquid | extreme persists at t+1 | external | 105 | 43.1 | 15.7 [7.3, 26.3] | 26.6 [9.7, 51.4] | 31.2 [17.0, 50.5] |
| top15_liquid | extreme persists at t+1 | overnight | 75 | 30.5 | 13.0 [5.5, 22.6] | 13.7 [7.1, 21.0] | 16.5 [8.3, 26.6] |
| top15_liquid | extreme persists at t+1 | weekend | 148 | 33.0 | 19.9 [14.4, 26.5] | 29.2 [22.2, 36.4] | 30.9 [25.3, 37.2] |
| all | all events | external | 1208 | 29.7 | 12.7 [9.9, 15.6] | 19.2 [15.4, 23.5] | 21.7 [17.7, 25.9] |
| all | all events | overnight | 1037 | 26.5 | 10.7 [8.6, 13.2] | 16.9 [14.0, 20.2] | 18.8 [14.9, 22.9] |
| all | all events | weekend | 2015 | 20.4 | 8.7 [7.6, 9.9] | 15.2 [13.5, 16.7] | 17.5 [15.6, 19.4] |
| all | extreme persists at t+1 | external | 399 | 51.4 | 20.6 [14.4, 27.3] | 32.0 [22.6, 41.6] | 39.3 [29.4, 50.3] |
| all | extreme persists at t+1 | overnight | 372 | 42.0 | 17.4 [13.0, 22.1] | 27.6 [20.4, 36.1] | 32.2 [23.5, 42.5] |
| all | extreme persists at t+1 | weekend | 715 | 36.4 | 14.5 [12.4, 16.7] | 25.5 [22.8, 28.4] | 32.4 [29.0, 36.1] |