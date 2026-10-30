# Project Spec — CAI 5724 AI Design Project
> **Superseded where it disagrees by `docs/architecture.md` (§12).** This spec was written when the event source was planned as a discrete-event simulator. The event source is now a Claude-generated synthetic dataset (`docs/architecture.md` §6), and the dataset had no clinical review.

**Course:** CAI 5724, AI in Health Design Studio I
**Milestone:** Week 3 — Team formed, Problem Statement, AI Sketch (5% of project grade)
**Deadline:** Wednesday, September 9, 11:59 PM, Canvas. Both team members submit the same file.

---

## 1. Team

| Name | Email | Other Role(s) | Coding |
|---|---|---|---|
| Daniel Escalante | [FILL: @ufl.edu] | N/A | High |
| Karissa [FILL: last name] | [FILL: @ufl.edu] | Clinical Microbiology Laboratory — specimen processing | Low |

**Domain fit:** Both members come from microbiology. Karissa works inside a clinical microbiology
laboratory and processes patient specimens through the laboratory workflow, so she
holds direct operational knowledge of where specimens stall and why. Daniel holds a B.S. in
Microbiology and Cell Sciences with undergraduate research on *Porphyromonas gingivalis* in UF's
Department of Oral Biology, and builds iOS and web applications and LLM/agent systems. The team
covers both the clinical workflow reality and the engineering.

---

## 2. Working title

**Primary:** Tracer — Predictive Specimen Tracking and Loss Prevention Across the Clinical-to-Laboratory Handoff

**Alternates:**
- Closing the Blind Interval: Predicting Specimen Delay and Loss Between Collection and Accessioning
- Specimen Sentinel — Anticipatory Alerting for Pre-Analytical Specimen Failures

---

## 3. One-paragraph pitch

Between the moment a specimen is collected at the patient's side and the moment its result posts,
nobody can tell whether it is on track. Tracer learns, from real hospital records, how long that
journey normally takes for each specimen type, collecting unit, and hour of day. It then estimates
the growing chance that a specimen will miss its clinically relevant window and raises a warning
while there is still time to act, rather than after the window has closed. A secondary study asks
how much of the unobserved transport leg can be inferred from timing alone.

---

## 4. Problem domain — how the workflow actually breaks

**The normal path.** A clinician orders a test. A nurse or phlebotomist collects the specimen and
scans it at the bedside. The specimen travels by pneumatic tube, courier, or hand to the
laboratory. The laboratory scans it at receipt and accessions it. It then moves through
processing, planting, incubation, and reading.

**The blind interval.** The system records a collection event and a receipt event. It records
nothing in between. For a specimen moving between buildings or between sites, that gap can run
from minutes to hours, and during the gap the specimen has no observable state. When it never
arrives, the absence is usually discovered indirectly and late: a clinician calls asking about a
result, or a laboratory technologist notices the order has no matching tube.

**Why absence is hard to notice.** A missing specimen produces no event. Every monitoring
mechanism in the workflow is triggered by something happening, so the one failure mode that matters
most is the one that generates no signal at all. Detecting it requires reasoning about what should
have happened by now and has not.

**Time is clinical, not administrative.** Delay is not merely an inconvenience because specimen
integrity degrades on a biological clock that differs by specimen type. Urine for culture held at
room temperature has a short window before bacterial overgrowth renders colony counts
uninterpretable. Cerebrospinal fluid is urgent and must not be refrigerated. Anaerobic transport
tolerates delay but not cold exposure. Blood culture bottles lose diagnostic resolution the longer
they sit unloaded, because time-to-positivity is measured from loading and not from collection.
When a specimen breaches its window, the sample is often rejected and must be recollected. That
means another needle stick or another lumbar puncture, a delay of hours in diagnosis, and in some
cases prolonged empiric antibiotic therapy while the team waits for a result that no longer exists.

**Failure taxonomy to model.** Never collected; collected but never dispatched; misrouted in the
tube system; stalled at a receiving station; lost in transit between sites; mislabeled or
unlabeled; wrong container or wrong preservative; temperature excursion; arrived but not accessioned.

**Who is affected.** Patients, who absorb the recollection and the diagnostic delay. Nurses and
phlebotomists, who repeat collections. Laboratory staff, who field calls and chase specimens.
Clinicians, who make decisions without results. The laboratory's quality program, which has no
systematic record of why specimens fail because failures are usually captured anecdotally.

**Broader context.** The pre-analytical phase, meaning everything before the instrument reads the
sample, is where testing errors concentrate. In a hospital audit of 51,746 analyses, 160 confirmed
errors broke down as 61.9% pre-analytical, 15% analytical, and 23.1% post-analytical (Carraro P,
Plebani M. *Clin Chem.* 2007;53(7):1338–42). The analytical phase has been heavily automated and
instrumented by comparison.

That figure covers pre-analytical errors as a whole, a category dominated by tube-filling and
labeling problems, so it establishes where errors cluster without establishing how often specimens
are specifically delayed or lost in transit. Measuring the turnaround distribution directly from
MIMIC-IV is the Week 7 deliverable that answers that question with our own analysis instead of a
borrowed statistic.

---

## 5. What exists today and why it falls short

| Approach | What it does | Limitation |
|---|---|---|
| LIS status screens | Show a specimen's last recorded state | Reports only what has been scanned; silent during the blind interval |
| Static turnaround time rules | Flag a specimen once a fixed time threshold has elapsed | Fires after the window is already breached; one threshold ignores specimen type, site, and time of day |
| Retrospective QA reports | Summarize missed specimens monthly | Learns after the harm; cannot intervene |
| RFID and BLE tag tracking | Physical location telemetry | Capital cost, tag-per-specimen expense, infrastructure across every corridor; rarely feasible at scale |
| Phone calls and manual searches | Human chase after a clinician asks | Reactive, undocumented, labor-intensive |
| Existing AI in the laboratory | Concentrated in the analytical and post-analytical phases, such as image interpretation and result validation | Very little applied to specimen logistics and the pre-analytical phase |

**The gap.** The ideal system would know where every specimen is at all times and warn staff before
a specimen is lost or degraded. Hardware-based tracking buys that visibility with capital
expenditure that most laboratories will not approve. Nobody has seriously attempted to buy it with
inference instead — using the timestamps that already exist to estimate the unobserved state. That
is the opening this project occupies.

---

## 6. AI Sketch content — NON-TECHNICAL, this is what goes in the submission

> **HARD RULE:** The assignment forbids technical detail in this section. No model names, no
> algorithms, no libraries, no platforms, no datasets, no architecture. Describe behavior and
> value only. Everything in Section 7 below is for the Week 4 and 5 milestones and must not appear
> in this document.

Submitted Week 3, verbatim. This text is method-agnostic and survives changes to Section 7.

Tracer treats each specimen's journey as one continuous story. Today the laboratory record holds a
collection scan, a receipt scan, and a void between them. From the timing patterns the laboratory
already produces in ordinary work, Tracer builds a sense of what a normal journey looks like for
each kind of specimen, from each collection site, at each hour of the day. It knows that a routine
urine leaving a clinic on a weekday afternoon behaves nothing like a spinal fluid drawn in the
emergency department at two in the morning. Working from that learned sense of normal, it does three
things the laboratory cannot do today. It warns early, raising a flag while a specimen still has
time left in its window instead of announcing after the fact that the specimen is late. It makes an
informed estimate about the stretch nobody can see, so an alert can point to where the specimen most
likely is rather than only reporting that something is wrong. And it weighs each finding by how much
harm the failure would cause, so a rare and urgent specimen is escalated differently from a routine
one, and every alert reaches the person who can act on it. A specimen that was probably never
collected goes to the charge nurse. One stalled in transit goes to the receiving technologist. One
lost on a run between campuses goes to the courier coordinator.

Three groups use it. Specimen processors and technologists work from a live view of everything in
flight, ordered by risk. Charge nurses and collection staff receive targeted recovery alerts that
name a specific specimen and a specific place to look. Laboratory supervisors and quality staff draw
on the accumulating record of what failed and why. If it works, specimen loss becomes something
intercepted in progress rather than discovered afterward, and fewer patients face a second draw or a
second lumbar puncture for a sample that was already taken once. The laboratory also gains a factual
account of its own failure modes in place of anecdotes, which turns process improvement from opinion
into evidence. Existing tracking tools answer the question of where a specimen was last seen. Tracer
answers where it probably is right now and what is about to go wrong, and it does so without asking
the hospital to install any new tracking hardware.

---

## 7. Technical architecture — DO NOT include in this submission

For Week 4 (AI solution architecture) and Week 5 (infrastructure, models, specifications).

Two halves. The primary half learns and is evaluated on real data. The secondary half is a
simulation study of the one thing real data cannot show.

### 7.1 Specimen event timeline (MIMIC-IV)

One row per specimen, assembled from the MIMIC-IV `hosp` module. Confirm every column against the
current MIMIC-IV schema documentation before building.

| Source | Fields | Gives |
|---|---|---|
| `microbiologyevents` | `micro_specimen_id`, `chartdate`, `charttime`, `storedate`, `storetime`, `spec_type_desc`, `test_name`, `org_name` | Culture specimens, collection time, result-stored time, specimen type |
| `labevents` | `specimen_id`, `itemid`, `charttime`, `storetime`, `priority` | Chemistry and hematology specimens, STAT vs routine |
| `d_labitems` | `fluid`, `category`, `label` | Specimen category |
| `transfers` | `careunit`, `intime`, `outtime` | Collecting location at time of collection |
| `admissions` | `admission_type`, `admission_location` | Emergency vs inpatient context |

Derived per specimen: collection hour, weekday, collecting unit, priority, specimen type, and
concurrent load, taken as the count of specimens collected in the same unit inside a surrounding
window.

**What this measures, precisely.** `storetime` is when the result was stored, not when the laboratory
received the specimen. The observable interval is therefore collection-to-result, and the transport
leg sits inside it rather than beside it. Every claim in the report is scoped to collection-to-result
and says so up front.

**Known data-quality wrinkles.** `charttime` is null on a subset of `microbiologyevents` rows where
only `chartdate` is populated. Those rows are either dropped or handled under a stated imputation
rule, and the choice is reported. MIMIC timestamps are date-shifted per patient but internally
consistent within a patient, so intervals are preserved; absolute calendar dates are never used.

### 7.2 Breach labels from the specimen policy table

The policy table defines, per specimen type: stability window, required transport condition, urgency
tier, and rejection criteria, each with a published source. That table turns a raw interval into a
label. A specimen whose collection-to-result time exceeds its window is a breach. This is the target
variable, and it does not exist without the clinical table.

### 7.3 Risk model

**Baseline, built first.** Per-stratum empirical quantiles over specimen type × collecting unit ×
hour bucket × priority. Alert when elapsed time passes the stratum's nth percentile. This is a real
baseline and the model has to beat it.

**Model.** Time-to-event over "will this specimen's result arrive inside its window," conditioned on
features known at collection plus elapsed time since collection, so the estimate updates continuously
instead of firing once. Alert when P(breach) crosses a threshold.

Evaluated against the baseline on held-out real specimens. If it does not beat the baseline, that is
the finding and it gets reported as one.

### 7.4 Operating point chosen by alert burden

The threshold is selected by alerts per shift at a clinically acceptable lead time, not by maximizing
a summary score. Alert fatigue is the dominant failure mode of hospital alerting systems, so alert
volume is a first-class design constraint.

### 7.5 Live board and specimen timeline

Web application. Specimens in flight ranked by expected harm, taken as P(breach) × urgency tier from
the policy table. Click a specimen for its timeline. Alerts route to a role through a lookup table:
never-collected to the charge nurse, stalled in transit to the receiving technologist, cross-site to
the courier coordinator.

### 7.6 Simulator (supporting)

Discrete-event model of the collection-to-laboratory workflow, with dwell-time distributions fitted
from MIMIC and stability limits from published standards. Fault injection across the failure taxonomy
in Section 4. Two jobs: it powers the public demo, since MIMIC cannot be redistributed, and it is the
substrate for 7.7.

Scope it deliberately small: one emergency department, one laboratory, one pneumatic tube system,
five specimen types chosen for contrast in stability window and urgency.

### 7.7 Location inference (exploratory, simulation only)

The workflow graph as a hidden Markov model, forward algorithm, maintaining a belief over the
specimen's unobserved position. Hour and staffing update the belief as evidence arrives. Conceptually
this is dead reckoning between fixes.

Reported as a simulation study, with the reason stated explicitly: intermediate transport events are
not recorded in MIMIC or in any public dataset, so there is no real ground truth to validate against.
The report states what instrumentation a real evaluation would require.

To keep this from being self-confirming, a sensitivity arm deliberately mis-specifies the model's
transition structure relative to the simulator, and the degradation is reported.

### 7.8 FHIR mapping

A table mapping the event model onto FHIR `Specimen` and `Task`, included in the report to show
portability. No FHIR server is built.

---

## 8. Data strategy

**Primary data.** MIMIC-IV, through PhysioNet credentialed access. Publicly available and
de-identified, which is exactly what the course guidelines require. Access needs CITI "Data or
Specimens Only Research" training plus PhysioNet approval, typically one to two weeks. This is the
critical path item and starts immediately.

**No workplace data.** No data from Karissa's workplace of any kind, in any artifact, including simulator
parameters. Karissa's input is restricted to published clinical standards, meaning stability windows,
transport conditions, and rejection criteria, plus qualitative workflow structure common to clinical
microbiology laboratories generally. Specimen volumes, dwell times, and failure frequencies come from
MIMIC or from the literature, with the source recorded for each. Encoding workplace numbers as
simulator constants would be sharing them, so the numbers do not come from the workplace.

**Redistribution.** The PhysioNet data use agreement prohibits redistributing MIMIC. Code, models,
and derived aggregate statistics go on GitHub; raw data does not, and the repository documents how to
obtain access. The course guidelines ask for exactly this. The public interactive demo therefore runs
on simulated data.

**Fallback.** If credentialing does not clear by Week 5, the simulator becomes the primary substrate
and the project runs as a simulation study end to end, with that limitation stated plainly.
eICU-CRD is a secondary option on the same credentialing path.

---

## 9. Evaluation plan

**On held-out real specimens (primary).**

- **Lead time.** Median minutes between alert and the actual window breach. Headline metric; a system
  that alerts after the breach has failed regardless of accuracy.
- **Discrimination and calibration** against the per-stratum empirical baseline from 7.3. The
  comparison against the baseline is the result, not the absolute number.
- **Alerts per shift** at the chosen operating point. The number that determines whether staff would
  tolerate the system in practice.
- **Subgroup performance** across specimen type, collecting unit, hour, and priority, to check the
  model is not carrying one stratum and failing the rest.

**In simulation (secondary, reported as such).**

- **Location accuracy.** Top-1 and top-3 accuracy of the estimated position against the simulator's
  true position, plus calibration of the belief.
- **Recoverability.** Fraction of injected failures that would have been recoverable at alert time.
- **Sensitivity to mis-specification.** Performance when the model's assumed transition structure
  deliberately disagrees with the simulator that generated the data.

---

## 10. Scope control

**In scope this semester:** MIMIC-IV specimen event timeline; specimen policy table and breach
labels; per-stratum empirical baseline; continuously updating risk model; operating point chosen by
alert burden; live risk board; per-specimen timeline view; reduced simulator for the public demo.

**Flagship differentiator:** early warning on real data, with lead time as the headline and an honest
comparison against the baseline.

**Stretch if time allows:** the location-inference simulation study; conformal arrival intervals.

**Explicitly out of scope:** RFID and BLE hardware; integration with any live hospital system; any
real patient or workplace data other than MIMIC-IV; process discovery; an agent layer; a cause
classifier; multi-site expansion beyond the single simulated pair.

**Risk to manage:** the failure mode of this project is still building a dashboard. Every team can
build a dashboard. The model beating a real baseline on real data is what no one else will have. If
it does not beat the baseline, the honest negative result plus the simulation study is the project,
and it gets written that way.

---

## 11. Division of labor

Karissa's coding level is Low, so her contributions are specified to be load-bearing without
requiring her to write code. Nothing here is decorative. The policy table generates the target
variable, and without it there is no supervised problem to solve.

**Karissa:** specimen policy table, with a published source cited for every row; failure taxonomy;
workflow map; urgency tiers; alert routing and role design; the definition of clinically actionable
lead time; clinical plausibility review of sampled model alerts; clinical framing in the report and
presentation. The policy table is a spreadsheet and Daniel ingests it.

She uses agent assistance to source and cross-check the published standards behind the policy table.
That work is logged as it happens and becomes material for the AI Teaming Log due Week 14.

**Daniel:** PhysioNet access; event pipeline and data model; baseline and risk model; evaluation
harness; simulator; location study; web application and interactive demo; GitHub repository and
environment.

**Shared:** problem framing, evaluation design, operating-point selection, report, presentation, AI
teaming log.

---

## 12. Milestone map

| Week | Date | Deliverable | Team focus |
|---|---|---|---|
| 3 | Sep 9 | Team formed, problem statement, AI pitch | Submitted. CITI training starts now |
| 4 | Sep 16 | AI solution architecture | Section 7. Credentialing in flight |
| 5 | Sep 23 | Infrastructure, models, technical specs | Data access confirmed; policy table drafted |
| 6 | Sep 30 | Project abstract | — |
| 7 | Oct 7 | Preliminary results and pivots | Real turnaround distributions; empirical baseline |
| 8 | Oct 14 | Minimum viable prototype | Risk model beating baseline + live board |
| 9-14 | Oct 21 - Nov 25 | Report section drafts | Simulator and location study in parallel |
| 15 | Dec 2 | Final prototype and interactive demo, GitHub | Demo runs on simulated data |
| 16 | Dec 9 | Final report, Canvas | — |

---

## 13. Open items for the team

1. **Start CITI training today.** Critical path. Both members credential; at minimum Daniel.
   Everything in Section 7.1 through 7.4 waits on this.
2. Karissa's UF email, last name, preferred name spelling, and exact job title.
3. Daniel's UF email.
4. Confirm Karissa's coding level. Recorded as Low; she moves it to Medium if that undersells her.
5. Confirm the five specimen types, chosen for contrast in stability window and urgency, and find a
   published source for each one's stability window and rejection criteria.
6. Confirm nothing from the workplace reaches any artifact, including simulator parameters and the
   policy table. Published standards only.
7. Choose the final project title.