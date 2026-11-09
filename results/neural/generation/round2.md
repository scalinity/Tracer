# Round 2 (O3): weak spots on validation

Synthetic data: laboratory weeks designed by language models and simulated by this project's code, with no clinical review. Every number below is a validation number from that simulator.

Read validation only: /blue/cai6734/daniel.escalante/tracer/code-n7b/results/neural/val/evaluation.json (set `val`, 772 scenarios, 4,093,666 scored specimens with a clock, 305,080 breaches with a tick), TracerNet's validation risk table (16 parts) and the observed store's validation split. No test set was read.

## How the slices were chosen (spec §4.12)

- Miss log loss: TracerNet's p_miss against the breach label at every tick up to the smaller of the window and 240 minutes, averaged per specimen, then over the slice's specimens (val_sel's definition, spec §7.7).
- Excess: that log loss minus the log loss of the slice's own base rate (the binary entropy of its breach share). Below zero, TracerNet beats the base rate in the slice.
- Ranked: slices with at least 30 breaches, by the loss ratio (TracerNet's log loss over the base rate's) times the slice's share of all validation breaches, highest first. Spec §4.12 writes excess times share; when every excess is below zero that would rank the smallest slices first, so the ratio is used: it orders a positive excess the same way and names where TracerNet adds least.
- Warning gap: TracerNet's median warning over all breaches (a miss counting as zero) minus the best baseline's, at 6/shift and 3-2-1 weights, from the evaluation's slices. Reported, not ranked on.

## The four slices

| Rank | Dimension | Slice | Breaches | Share of breaches | Base rate | TracerNet log loss | Base-rate log loss | Excess | Loss ratio x share | Warning gap | Planned as |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | route kind | courier | 242,011 | 0.7933 | 0.1691 | 0.1144 | 0.4545 | -0.3401 | 0.19968 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) | `{"transport_mix": "courier_dominant"}` |
| 4 | volume tercile | high | 203,577 | 0.6673 | 0.0765 | 0.0808 | 0.2702 | -0.1894 | 0.19949 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) | `{"size_band": "large"}` |
| 5 | route kind | tube | 50,714 | 0.1662 | 0.0225 | 0.0605 | 0.1075 | -0.0470 | 0.09353 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) | `{"transport_mix": "tube_dominant"}` |
| 6 | volume tercile | middle | 74,419 | 0.2439 | 0.0707 | 0.0815 | 0.2554 | -0.1739 | 0.07782 | +15.0 min (TracerNet 15.0, best baseline window_rule 0.0) | `{"size_band": "medium"}` |

Every ranked slice has an excess below zero: TracerNet beats each slice's base rate, which is why the ranking uses the loss ratio rather than the excess itself.

Ranked above a chosen slice but not plannable:

- Rank 1, window class `short (up to 2 h)` (loss ratio x share 0.36914): no brief factor sets a specimen's window.
- Rank 2, edge tag `none` (loss ratio x share 0.26634): a week without edge tags is not an edge-tag target.

## Every ranked slice

| Rank | Dimension | Slice | Specimens | Breaches | Share | Base rate | Excess | Loss ratio x share | Warning gap |
|---|---|---|---|---|---|---|---|---|---|
| 1 | window class | short (up to 2 h) | 1,558,132 | 293,726 | 0.9628 | 0.1885 | -0.2985 | 0.36914 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 2 | edge tag | none | 3,673,427 | 265,996 | 0.8719 | 0.0724 | -0.1805 | 0.26634 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 3 | route kind | courier | 1,430,931 | 242,011 | 0.7933 | 0.1691 | -0.3401 | 0.19968 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 4 | volume tercile | high | 2,660,567 | 203,577 | 0.6673 | 0.0765 | -0.1894 | 0.19949 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 5 | route kind | tube | 2,256,642 | 50,714 | 0.1662 | 0.0225 | -0.0470 | 0.09353 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 6 | volume tercile | middle | 1,053,098 | 74,419 | 0.2439 | 0.0707 | -0.1739 | 0.07782 | +15.0 min (TracerNet 15.0, best baseline window_rule 0.0) |
| 7 | archetype by stratum | safety_net_urban / stressed | 378,263 | 38,595 | 0.1265 | 0.1020 | -0.2373 | 0.03539 | n/a (the evaluation slices archetype and stratum separately) |
| 8 | archetype by stratum | academic_medical_center / stressed | 317,526 | 31,763 | 0.1041 | 0.1000 | -0.2312 | 0.03007 | n/a (the evaluation slices archetype and stratum separately) |
| 9 | volume tercile | low | 371,290 | 27,084 | 0.0888 | 0.0729 | -0.1781 | 0.02824 | +15.0 min (TracerNet 55.0, best baseline analytic_local 40.0) |
| 10 | route kind | walk_in | 397,382 | 12,355 | 0.0405 | 0.0311 | -0.0597 | 0.02305 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 11 | window class | long (a day or more) | 2,526,823 | 11,354 | 0.0372 | 0.0045 | -0.0120 | 0.02169 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 12 | archetype by stratum | cancer_center / stressed | 293,672 | 20,681 | 0.0678 | 0.0704 | -0.1737 | 0.02156 | n/a (the evaluation slices archetype and stratum separately) |
| 13 | archetype by stratum | safety_net_urban / disrupted | 235,076 | 19,322 | 0.0633 | 0.0822 | -0.1946 | 0.01994 | n/a (the evaluation slices archetype and stratum separately) |
| 14 | archetype by stratum | childrens_hospital / stressed | 223,273 | 19,674 | 0.0645 | 0.0881 | -0.2065 | 0.01982 | n/a (the evaluation slices archetype and stratum separately) |
| 15 | archetype by stratum | community_hospital / stressed | 221,125 | 19,546 | 0.0641 | 0.0884 | -0.2081 | 0.01945 | n/a (the evaluation slices archetype and stratum separately) |
| 16 | archetype by stratum | safety_net_urban / normal | 387,743 | 17,574 | 0.0576 | 0.0453 | -0.1238 | 0.01896 | n/a (the evaluation slices archetype and stratum separately) |
| 17 | archetype by stratum | academic_medical_center / disrupted | 165,385 | 14,756 | 0.0484 | 0.0892 | -0.2046 | 0.01546 | n/a (the evaluation slices archetype and stratum separately) |
| 18 | archetype by stratum | academic_medical_center / edge | 122,861 | 13,943 | 0.0457 | 0.1135 | -0.2506 | 0.01332 | n/a (the evaluation slices archetype and stratum separately) |
| 19 | archetype by stratum | cancer_center / disrupted | 167,317 | 10,500 | 0.0344 | 0.0628 | -0.1529 | 0.01198 | n/a (the evaluation slices archetype and stratum separately) |
| 20 | archetype by stratum | cancer_center / normal | 191,957 | 8,689 | 0.0285 | 0.0453 | -0.1214 | 0.00973 | n/a (the evaluation slices archetype and stratum separately) |
| 21 | archetype by stratum | childrens_hospital / normal | 186,617 | 10,256 | 0.0336 | 0.0550 | -0.1515 | 0.00970 | n/a (the evaluation slices archetype and stratum separately) |
| 22 | archetype by stratum | childrens_hospital / disrupted | 126,059 | 10,065 | 0.0330 | 0.0798 | -0.1967 | 0.00968 | n/a (the evaluation slices archetype and stratum separately) |
| 23 | archetype by stratum | academic_medical_center / normal | 233,522 | 8,485 | 0.0278 | 0.0363 | -0.1019 | 0.00965 | n/a (the evaluation slices archetype and stratum separately) |
| 24 | archetype by stratum | safety_net_urban / edge | 82,024 | 9,023 | 0.0296 | 0.1100 | -0.2457 | 0.00860 | n/a (the evaluation slices archetype and stratum separately) |
| 25 | edge tag | courier_capacity_crunch | 57,014 | 8,638 | 0.0283 | 0.1515 | -0.2999 | 0.00835 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 26 | archetype by stratum | community_hospital / disrupted | 84,049 | 8,533 | 0.0280 | 0.1015 | -0.2314 | 0.00826 | n/a (the evaluation slices archetype and stratum separately) |
| 27 | archetype by stratum | surgical_specialty / stressed | 80,541 | 7,094 | 0.0233 | 0.0881 | -0.1986 | 0.00776 | n/a (the evaluation slices archetype and stratum separately) |
| 28 | archetype by stratum | community_hospital / normal | 157,270 | 6,969 | 0.0228 | 0.0443 | -0.1258 | 0.00701 | n/a (the evaluation slices archetype and stratum separately) |
| 29 | archetype by stratum | childrens_hospital / edge | 71,540 | 5,779 | 0.0189 | 0.0808 | -0.1911 | 0.00604 | n/a (the evaluation slices archetype and stratum separately) |
| 30 | edge tag | weekend_courier_no_show | 38,349 | 6,648 | 0.0218 | 0.1734 | -0.3430 | 0.00559 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 31 | archetype by stratum | cancer_center / edge | 63,683 | 4,098 | 0.0134 | 0.0643 | -0.1477 | 0.00513 | n/a (the evaluation slices archetype and stratum separately) |
| 32 | edge tag | misroute_long_search | 52,403 | 4,335 | 0.0142 | 0.0827 | -0.1898 | 0.00476 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 33 | edge tag | instrument_staff_drain | 40,636 | 4,595 | 0.0151 | 0.1131 | -0.2447 | 0.00462 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 34 | archetype by stratum | surgical_specialty / normal | 86,440 | 3,292 | 0.0108 | 0.0381 | -0.0969 | 0.00433 | n/a (the evaluation slices archetype and stratum separately) |
| 35 | edge tag | compound_failure | 45,557 | 4,086 | 0.0134 | 0.0897 | -0.2050 | 0.00429 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 36 | edge tag | batching_night_unit | 47,574 | 4,040 | 0.0132 | 0.0849 | -0.1969 | 0.00427 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 37 | archetype by stratum | critical_access_rural / stressed | 43,956 | 4,344 | 0.0142 | 0.0988 | -0.2375 | 0.00375 | n/a (the evaluation slices archetype and stratum separately) |
| 38 | edge tag | night_station_neglect | 47,524 | 3,099 | 0.0102 | 0.0652 | -0.1526 | 0.00373 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 39 | edge tag | holiday_skeleton | 36,291 | 3,487 | 0.0114 | 0.0961 | -0.2153 | 0.00365 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 40 | edge tag | unnoticed_outage | 44,962 | 3,353 | 0.0110 | 0.0746 | -0.1801 | 0.00353 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 41 | archetype by stratum | community_hospital / edge | 39,933 | 3,237 | 0.0106 | 0.0811 | -0.1887 | 0.00350 | n/a (the evaluation slices archetype and stratum separately) |
| 42 | edge tag | offsite_road_closure | 37,982 | 3,859 | 0.0126 | 0.1016 | -0.2396 | 0.00343 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 43 | edge tag | stat_heavy_icu_night | 48,959 | 2,856 | 0.0094 | 0.0583 | -0.1450 | 0.00326 | +20.0 min (TracerNet 30.0, best baseline analytic_local 10.0) |
| 44 | edge tag | misroute_storm | 21,280 | 3,251 | 0.0107 | 0.1528 | -0.2992 | 0.00320 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 45 | edge tag | mass_casualty_surge | 37,180 | 3,160 | 0.0104 | 0.0850 | -0.2051 | 0.00305 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 46 | edge tag | slow_degradation | 29,268 | 2,668 | 0.0087 | 0.0912 | -0.1991 | 0.00304 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 47 | edge tag | chronic_non_dispatch_unit | 33,941 | 2,340 | 0.0077 | 0.0689 | -0.1525 | 0.00301 | +5.0 min (TracerNet 25.0, best baseline analytic_local 20.0) |
| 48 | archetype by stratum | surgical_specialty / disrupted | 35,333 | 2,277 | 0.0075 | 0.0644 | -0.1455 | 0.00292 | n/a (the evaluation slices archetype and stratum separately) |
| 49 | archetype by stratum | surgical_specialty / edge | 19,969 | 2,218 | 0.0073 | 0.1111 | -0.2392 | 0.00228 | n/a (the evaluation slices archetype and stratum separately) |
| 50 | archetype by stratum | critical_access_rural / disrupted | 26,681 | 2,232 | 0.0073 | 0.0837 | -0.1991 | 0.00225 | n/a (the evaluation slices archetype and stratum separately) |
| 51 | disruption type | station_unattended | 4,781 | 992 | 0.0033 | 0.2075 | -0.1599 | 0.00223 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 52 | edge tag | outage_at_shift_change | 20,692 | 1,931 | 0.0063 | 0.0933 | -0.2196 | 0.00185 | +20.0 min (TracerNet 35.0, best baseline window_rule 15.0) |
| 53 | disruption type | instrument_down | 25,118 | 1,921 | 0.0063 | 0.0765 | -0.1932 | 0.00179 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 54 | archetype by stratum | critical_access_rural / normal | 31,622 | 1,349 | 0.0044 | 0.0427 | -0.1125 | 0.00160 | n/a (the evaluation slices archetype and stratum separately) |
| 55 | edge tag | outage_during_peak | 18,599 | 1,572 | 0.0052 | 0.0845 | -0.2010 | 0.00158 | +0.0 min (TracerNet 0.0, best baseline window_rule 0.0) |
| 56 | disruption type | receiving_short_staffed | 13,651 | 1,678 | 0.0055 | 0.1229 | -0.2691 | 0.00153 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 57 | disruption type | courier_missed_rounds | 1,740 | 901 | 0.0030 | 0.5178 | -0.4542 | 0.00102 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 58 | disruption type | tube_line_outage | 1,878 | 382 | 0.0013 | 0.2034 | -0.1482 | 0.00088 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 59 | archetype by stratum | critical_access_rural / edge | 11,518 | 786 | 0.0026 | 0.0682 | -0.1676 | 0.00084 | n/a (the evaluation slices archetype and stratum separately) |
| 60 | disruption type | unit_batching_hold | 1,120 | 302 | 0.0010 | 0.2696 | -0.2935 | 0.00049 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 61 | disruption type | unit_surge | 6,057 | 341 | 0.0011 | 0.0563 | -0.1344 | 0.00042 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 62 | disruption type | courier_delayed | 1,683 | 542 | 0.0018 | 0.3220 | -0.4796 | 0.00042 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 63 | disruption type | tube_line_degraded | 2,736 | 117 | 0.0004 | 0.0428 | -0.0735 | 0.00022 | n/a (the evaluation slices disruption as present or absent, not by type) |
| 64 | disruption type | offsite_route_closed | 234 | 76 | 0.0002 | 0.3248 | -0.5841 | 0.00002 | n/a (the evaluation slices disruption as present or absent, not by type) |

## The O3 plan

Targets: the four slices at equal shares of the 60% of slice briefs, 25% general edge briefs and 15% for 12 cells and edge tags below their coverage targets in the library manifest (largest deficit first: `{"archetype": "community_hospital", "stratum": "normal"}` 389 of 407, `{"archetype": "academic_medical_center", "stratum": "normal"}` 314 of 326, `{"archetype": "safety_net_urban", "stratum": "normal"}` 355 of 366, `{"archetype": "critical_access_rural", "stratum": "normal"}` 194 of 204, `{"archetype": "surgical_specialty", "stratum": "normal"}` 235 of 245, `{"archetype": "childrens_hospital", "stratum": "normal"}` 236 of 244, `{"archetype": "cancer_center", "stratum": "normal"}` 240 of 244, `{"archetype": "critical_access_rural", "stratum": "stressed"}` 201 of 203, `{"archetype": "academic_medical_center", "stratum": "disrupted"}` 185 of 186, `{"archetype": "community_hospital", "stratum": "stressed"}` 406 of 407, `{"archetype": "safety_net_urban", "stratum": "stressed"}` 365 of 366, `{"edge_tag": "holiday_skeleton"}` 53 of 54).

Measured cost and acceptance per model arm:

- `sol-medium`: $0.017452 per accepted scenario, acceptance 1.0000 (pilot gate (results/neural/generation/pilot.json): $0.017452 per accepted, 24 accepted of 24 briefs).
- `terra-low`: $0.007283 per accepted scenario, acceptance 0.9872 (library manifest, job O2: $43.865879 spent, 6,023 accepted of 6,101 briefs).

Count (spec Appendix B, the formula O2's count used): the 600 briefs the planner makes at its default size give the strata's shares (disrupted 0.1317, edge 0.3217, normal 0.3033, stressed 0.2433), so the arms' shares (`sol-medium` 0.3217, `terra-low` 0.6783). Spend per brief $0.010491 and accepted per brief 0.9913 give $0.010583 per accepted scenario; accepted = floor($18.00 x 0.95 / $0.010583) = 1,615; briefs = ceil(1,615 / 0.9913) = 1,630.

The briefs `plan --job O3` will write (seed 301): 1,630 in all, coverage 244, edge 408, slice 978 by origin; disrupted 216, edge 524, normal 488, stressed 402 by stratum; 0 out of distribution.

## Spend

- O3 cap: $18.00. Expected spend of these briefs at the measured costs: $17.10. The ledger's cap stops spending first whatever the estimate.
- Spent before O3 (the library manifest's ledger record at the freeze): O1 $0.6003, O2 $43.8659; $44.4662 in all.
