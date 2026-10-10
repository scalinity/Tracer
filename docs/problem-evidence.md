# Problem evidence

Measured and cited support for the problem statement, written to be dropped into the final report's
Introduction (Week 9). Prose here is report-ready; keep the register accordingly.

---

## The interval is absent from the schema, not merely unmeasured

*Report-ready, 193 words.*

> The pre-analytical interval is not merely under-measured in existing records; it is absent from their
> schemas. MIMIC-IV, among the most widely used publicly available electronic health record datasets,
> timestamps a microbiology specimen twice: at collection (`charttime`) and when its final result is
> documented (`storetime`). No receipt, accessioning, or setup event exists between them, and no transport
> record of any kind. In the open-access demonstration extract (Johnson et al., 2023), the median interval
> between those two timestamps is 80.1 hours across the 1,291 specimens carrying both, and 0.15 percent
> close within two hours — a span dominated by incubation rather than transport.
>
> That extract covers 100 patients and supports no epidemiological claim about delay or loss. Its relevance
> is structural, not statistical: the fields that would record a specimen's journey are not present in the
> data model, so no sample size recovers them. Retrospective records can establish that a specimen was
> collected and that a result eventually appeared. They cannot establish where it was in between, or that
> one never arrived. A system intended to intercept those failures must therefore reason forward from what
> should have happened by now, rather than learn them from history.

**Citation.** Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023). MIMIC-IV
Clinical Database Demo (version 2.2). PhysioNet. https://doi.org/10.13026/dp1f-ex47. Open Data Commons Open
Database License v1.0. PhysioNet additionally requests the platform citation: Pollard, T., Moody, B. E.,
Lehman, L., et al. (2026). PhysioNet as a global platform for biomedical research. *Nature Health.*
https://doi.org/10.1038/s44360-026-00096-z

**Provenance of the figures.** Computed in `notebooks/mimic_demo_calibration_check.ipynb`; full working and
the by-specimen-type breakdown in `docs/mimic-demo-findings.md`. Figures are specimen-level: the source
table holds one row per organism-antibiotic result, so row counts overstate the specimen count roughly
twofold.

**Scope discipline.** The claim this paragraph makes is that the events are missing from the data model.
That is a property of the schema and holds identically in the full credentialed MIMIC-IV. The paragraph
makes no claim about how often specimens are delayed or lost, which 100 patients cannot support and which
remains this project's open evidentiary gap — see the note in
`docs/submissions/week03-problem-statement.docx`, which identifies the IFCC quality-indicator literature as
the lead worth chasing for a loss rate.

---

## Already cited in the Week 3 submission

Carried here so the Introduction draft has one place to look. Both were verified in the source when the
problem statement was written.

- **Pre-analytical errors dominate.** Carraro P, Plebani M. Errors in a Stat Laboratory: Types and
  Frequencies 10 Years Later. *Clin Chem.* 2007;53(7):1338–42. 160 confirmed errors in 51,746 analyses at a
  single centre: 61.9% pre-analytical, 15% analytical, 23.1% post-analytical. Single-centre; attribute it to
  that audit rather than presenting it as a universal rate.
- **Specimen integrity degrades on a clock that differs by type.** Miller JM, Binnicker MJ, Campbell S,
  et al. A Guide to Utilization of the Microbiology Laboratory for Diagnosis of Infectious Diseases: 2018
  Update by IDSA and ASM. *Clin Infect Dis.* 2018;67(6):e1–e94. Verified directly: CSF transported at room
  temperature immediately and "do not refrigerate"; boric acid as the transport medium for urines. The
  commonly quoted two-hour room-temperature urine limit traces to CLSI GP16, which has not been read
  directly and is not cited as though it had.
