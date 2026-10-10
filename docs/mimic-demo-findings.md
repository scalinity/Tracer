# MIMIC-IV demo — calibration go/no-go findings

**Question.** `docs/architecture.md` §1.1 proposed calibrating two simulator parameters against the
MIMIC-IV Clinical Database Demo: **specimen-type mix** and the **collection time-of-day profile**. Can it?

**Answer. No, on both.** Both parameters revert to their assumed values. The calibration step and its
evaluation arm are removed from the architecture rather than left standing as an aspiration.

**Source.** MIMIC-IV Clinical Database Demo v2.2, PhysioNet, Open Data Commons Open Database License,
`data/mimic-demo/` (gitignored, not redistributed). Every figure below is reproduced by
`notebooks/mimic_demo_calibration_check.ipynb`, which runs against the extract and carries its outputs.

---

## 1. Volume

| Measure | Value |
|---|---|
| Rows in `microbiologyevents` | 2,899 |
| **Distinct specimens** (`micro_specimen_id`) | **1,336** |
| Distinct patients | 97 (of the 100 in the extract) |
| Distinct admissions | 171 |
| Distinct `spec_type_desc` values | 35 |
| `chartdate` range | 2110-01-04 to 2202-01-10 (date-shifted per patient) |

The row count is roughly double the specimen count because `microbiologyevents` holds one row per
organism-antibiotic result. Counting rows would overstate the sample and weight the mix toward whatever
grew. Everything below is specimen-level.

**Null rates.** `charttime` is null on 183 of 2,899 rows, which is **45 of 1,336 specimens (3.4%)** —
those have `chartdate` only. `storetime` is null on 16 rows. Neither null rate is the problem here.

## 2. Specimen mix — **NO**

Top of the distribution, specimen-level:

| Specimen type | Specimens | % | Patients |
|---|---:|---:|---:|
| BLOOD CULTURE | 492 | 36.8 | 60 |
| URINE | 275 | 20.6 | 77 |
| MRSA SCREEN | 102 | 7.6 | 78 |
| STOOL | 65 | 4.9 | 28 |
| SEROLOGY/BLOOD | 53 | 4.0 | 19 |
| SPUTUM | 51 | 3.8 | 32 |
| Immunology (CMV) | 41 | 3.1 | 4 |
| SWAB | 40 | 3.0 | 19 |
| PERITONEAL FLUID | 25 | 1.9 | 7 |
| … 26 further types | ≤ 19 each | | |
| **CSF;SPINAL FLUID** | **8** | **0.6** | 6 |

**Sample size: fails.** Of 35 types, **27 have fewer than 30 specimens**, **18 have fewer than 10**, and 3
have exactly one. Only 8 types clear 30.

**Population: fails, and this is the heavier objection.** Mapping the extract onto the five types the
prototype models (architecture §6.1):

| Maps to | Specimens | % of all |
|---|---:|---:|
| Blood culture — **excluded from the prototype** | 512 | 38.3 |
| Not modelled at all (MRSA screen, stool, serology, immunology, catheter tips…) | 386 | 28.9 |
| Urine, container unknown | 275 | 20.6 |
| Lower respiratory (approx: sputum, BAL, washings) | 81 | 6.1 |
| Wound/swab (approx: swab, abscess, staph swab, foot, ear) | 74 | 5.5 |
| CSF | 8 | 0.6 |

Three findings sit in that table:

1. **The largest category is the one type the prototype deliberately excludes.** Blood cultures are 38.3%
   of this data and are out of scope because they are loaded onto an instrument rather than plated.
2. **67% of the extract is either excluded or unmodelled.** Renormalising over what remains gives urine
   62.8%, lower respiratory 18.5%, wound 16.9%, CSF 1.8% — but that is no longer an observed mix. It is our
   own scope decision, re-weighted, wearing a citation it has not earned.
3. **The urine split is invisible.** `spec_type_desc` is `URINE` with no container or preservative field.
   Preserved versus unpreserved urine is the pair that makes the policy table per-container rather than
   per-fluid (§6.1), and this data cannot distinguish them at any sample size.

Add that the target setting is a general clinical microbiology laboratory serving inpatient, emergency
**and outpatient** collection, whose mix is characteristically heavy in outpatient urine. This extract is
heavy in blood culture. It describes a different laboratory.

## 3. Collection time of day — **NO**

**Sample size: passes.** 1,291 specimens carry a `charttime`. All 24 hours are populated: minimum 20
specimens (07:00), maximum 89 (13:00), mean 53.8. Four hours fall below 30; none fall below 10. On count
alone this is a usable hourly profile, and it was the more promising of the two parameters.

**Population: fails.** The shape is specifically acute-care:

| Band | Specimens | % |
|---|---:|---:|
| 00–06 overnight | 253 | 19.6 |
| 07–17 day | 741 | 57.4 |
| 18–23 evening | 297 | 23.0 |

Nearly a fifth of all collections happen between midnight and 06:00. Breaking the hourly curve down by
type makes the mechanism explicit: **the single largest blood-culture hour in the entire extract is 06:00**
(34 specimens, against 10–28 in every other hour), with a sustained 04:00–06:00 plateau. That is the
signature of inpatient pre-dawn draw rounds, when a ward or ICU collects for the morning results.

A general laboratory serving outpatient collection has close to the opposite shape: a near-empty night, a
sharp rise when clinics open, and a peak driven by outpatient draws in the morning. Importing this curve
would not make the simulator more realistic. It would make it more specifically an ICU, and would do so
while the report claimed the opposite — that the arrival profile was calibrated to observed data. That is
worse than an assumed value honestly labelled.

*One methodological note, since this section depends on it.* MIMIC-IV shifts dates per patient and is
documented as preserving time of day. The data corroborates that here rather than merely asserting it: a
shuffled clock would produce a flat hourly distribution, and what appears instead is a coherent
circadian pattern — a daytime plateau of 68–89 specimens per hour, a 20-specimen trough at 07:00, and a
blood-culture spike at exactly 06:00. Structure that specific does not survive randomisation, so the hours
are real. That makes the *shape* trustworthy, which is precisely what makes it disqualifying.

## 4. Collection-to-result envelope — confirms it is not a transport interval

`storetime` is when the final result was documented. Measured across 1,291 specimens:

| Statistic | Hours |
|---|---:|
| Minimum | 1.6 |
| 25th percentile | 37.3 |
| **Median** | **80.1** |
| 75th percentile | 140.6 |
| 90th percentile | 149.6 |
| Maximum | 1,817.0 |

Only **0.15% of specimens complete within 2 hours**, 3.5% within 8 hours, 10.2% within 24 hours. By type,
medians run from 18.5 h (stool) through 31.9 h (urine) and 139.7 h (blood culture) to 529.6 h
(CSF — n=8, and a reminder of what a tail estimate off eight specimens is worth).

This settles empirically what §1.1 previously argued from the schema: the collection-to-result interval in
these tables is dominated by incubation and is not the same quantity as the transport interval Tracer
models. The shortest interval in the whole extract, 1.6 hours, is longer than the assumed stability window
for unpreserved urine.

Unlike everything else in this document, **this finding is not a limitation of the extract — it is evidence
for the problem statement**, and it survives to the full credentialed dataset because it describes the
schema rather than the sample. It is written up for the report's Introduction in `docs/problem-evidence.md`.

## 5. Population, directly

| Check | Result |
|---|---|
| Patients with at least one ICU stay | **100 of 100** |
| Admissions via emergency room | 48.7% |
| Admission type `EW EMER.` | 37.8% |
| Specimens with no linked admission (ED-encounter or pre-admission) | 31.7% |

Every patient in the extract had an ICU stay. The demo README states the source directly: deidentified
records from Beth Israel Deaconess Medical Center, a subset of 100 patients. There is no outpatient
collection population in this data, and the 31.7% of specimens without a linked admission are emergency
encounters for the same critical-care cohort, not clinic draws.

## 6. Verdict and consequence

| Parameter | Sample size | Population | Decision |
|---|---|---|---|
| Specimen-type mix | Fail — 27 of 35 types under n=30 | Fail — 38% blood culture, excluded from scope; no container detail | **Revert to assumed** |
| Collection time-of-day | Pass — 1,291 specimens, all hours ≥ 20 | Fail — 19.6% overnight, 06:00 blood-culture peak, no outpatient population | **Revert to assumed** |

Applying §1.1's fallback rule, both parameters keep their assumed values, sourced from general laboratory
literature and marked inline as assumptions. The calibration fitter, the `[cal]` parameter markers, and the
calibrated-versus-assumed evaluation arm are removed from the architecture. Every simulator parameter is now
an assumption, and the report says so plainly rather than claiming a calibration that this data cannot
support.

**Would credentialed access to the full MIMIC-IV change the answer?** No. It fixes exactly one of the two
failures. The full dataset has roughly three orders of magnitude more microbiology, which would clear every
sample-size objection — but it is the same hospital and the same critical-care population, so the mix stays
blood-culture-dominated, the urine container detail still does not exist, and the hourly curve keeps its
inpatient draw-round shape. The obstacle is the population, and more rows of the same population do not
move it. Pursuing credentialing for this purpose would spend two weeks to arrive at the same verdict with
tighter confidence intervals around the wrong laboratory.

**What would actually settle it** is an hourly collection profile and specimen mix published by a general
clinical microbiology laboratory — a quality-indicator paper or a laboratory's published workload
statistics. That is a literature question, not a data-access question, and it is worth one search before
the simulator's arrival profile is finalised in Week 6.
