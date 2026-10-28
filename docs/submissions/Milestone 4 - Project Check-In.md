# Project Check-In

Readable mirror of `Milestone 4 - Project Check-In.pdf` (built from the `.tex` beside it), Milestone 4, due Wednesday, October 28, one submission per group. Written as plain numbered answers with lists in place of tables, so it pastes into the Canvas text box.

**Team:** Karissa Kocourek and Daniel Escalante

**1. What is your in-class presentation date?**

Wednesday, November 18, 2026. The interactive demo has to be functionally complete by then; it is not required to be camera-ready until the December 2 deadline.

**2. How confident are you that your project will be completed on time?**

Moderately confident: 3 out of 5 today. The design is finished, and our decision to generate the dataset with the Claude API removes the largest build risk, a hand-built discrete-event simulator. Confidence is not higher because no pipeline code exists yet and the sourced policy table is still outstanding, which leaves 21 days to build, test and rehearse a working demo.

We expect to reach 4 out of 5 if two things hold on November 3: a validated pilot dataset exists, and Karissa's first-pass policy table is in. If either slips, we cut in a fixed order: the gradient-boosted challenger model, then the robustness checks, then the depth of the location-estimate evaluation. We keep the working core, which is the risk model, the live board and the specimen timeline.

**3. Overall, where does your AI system stand today? What is working? What isn't?**

The design is complete and the core mathematics runs, but nothing that depends on data is built yet. No model has been fitted or evaluated, so we have no performance numbers.

- Problem framing, architecture, data model: **done.** Milestones 1 to 3, `docs/architecture.md`, public repository github.com/scalinity/Tracer.
- Case that public data cannot be used: **done.** MIMIC-IV Demo check: 1,336 microbiology specimens stamped only at collection and at final result (median 80.1 hours apart), with no receipt, transport or setup events.
- Risk model (specimen status and arrival forecast): **scripted example only.** The closed-form mixture-survival model runs in our interactive system map with hand-set parameters. Nothing is fitted from data.
- Location estimate: **scripted example only.** A hidden semi-Markov forward filter over four transit states runs in the same map, with the same caveat.
- Synthetic dataset: **decided, not generated.** The Claude API generator is designed (see answer 6); the pilot run has not started.
- Specimen policy table (windows and citations): **outstanding.** Not yet in the shared project folder. The windows in use are labeled as assumptions.
- Search brief (LLM): **not started.** Design only.
- Pipeline, evaluation, live board: **not started.** The `src/` folders are empty.

**4. Describe any major challenges your team has faced.**

- **No public dataset covers the interval we monitor.** In the MIMIC-IV Demo, a specimen is stamped at collection and again when the final result is documented, and never in between. We tested using it to calibrate a simulator and rejected that: it is critical-care data (all 100 patients had an ICU stay, 38.3% of specimens are blood cultures, which we exclude), and more rows from the same hospital would not change the population. We therefore chose to generate our own synthetic dataset. The cost is that every timing parameter is an assumption, so we label each one and stress-test it.
- **The label depends on a clinical judgment.** A specimen is late only relative to a stability window and the event that stops the clock. Only Karissa can supply and cite those, so the policy table gates the target variable. The clock rule lives in configuration, so a change costs a re-run, not a rewrite.
- **A model can only explain what its state space contains.** Our first location model blamed silence on "lost" (0.64 lost against 0.35 at the tube station) until we added an explicit "stalled at station" state, which moved the same silence to 0.68 at the station and 0.31 lost.
- **Schedule.** Our time so far went to framing, architecture and the data feasibility check rather than code. Engineering is one person's serial path (data, fit, models, demo), so each late stage delays the next.

**5. Compare your current progress with the development timeline you proposed in the last milestone.**

Milestone 3 set five stages. Documentation and design are done. Engineering is about one to two weeks behind.

1. Specimen policy and workflow specification (target Oct 16): **late.** The workflow map and failure modes are written. The sourced stability table is not delivered.
2. Simulated specimen dataset (target Oct 23): **late.** The approach changed to Claude API generation; the generator and pilot are not yet built.
3. Status and arrival forecast, minimum viable prototype (target Oct 30): **will be missed.** The model exists as running JavaScript with assumed parameters. The version fitted from data is not started.
4. Location estimate and search brief (target Nov 13): **at risk.** The location filter exists as running JavaScript on a scripted example. The search brief is not started.
5. Interactive demo tutorial (target Dec 2): **seed only.** Our system map (`visuals/architecture.html`) is the starting point. A working version is now needed by Nov 18.

**6. Describe any changes to your Development Roadmap from the previous milestone.**

Unchanged are the four modules, the Nov 13 target for location and search brief, and the Dec 2 and Dec 9 deadlines. The changes:

- **Data source.** The simulated dataset becomes a synthetic dataset generated with the Claude API. Seeded code draws each specimen's brief (type, tests, unit, hour, route, planted failure, timing targets); Claude writes the event trail and true location history for it; a validator checks timestamps, workflow order and the planted outcome; accepted output is frozen and committed. No public dataset has intermediate events, and building a discrete-event simulator would have cost weeks.
- **Smaller, staged dataset.** About 12,000 specimens instead of 50,000, since the models fit a few parameters per specimen group and cost now scales with size. A 200-specimen pilot, reviewed by Karissa, comes first.
- **Policy table split in two.** A provisional first pass (Nov 1) lets generation start; the cited version follows (Nov 8).
- **Minimum viable prototype redefined** as the thinnest end-to-end path (data, fitted risk model, alerts, board and timeline), moved from Oct 30 to Nov 8, with the location estimate and search brief added by Nov 13.
- **Demo pulled forward.** Functionally complete by Nov 16 for the Nov 18 presentation, camera-ready by Dec 2. It gains an audience activity and a 3D view of the specimen's journey, with a 2D fallback.

Revised dates: policy table first pass Nov 1 and cited Nov 8; dataset pilot Oct 31 and full set Nov 3; risk model and minimum viable prototype Nov 8; location estimate and search brief Nov 13; interactive demo functionally complete Nov 16, camera-ready Dec 2.

**7. List each dataset you are using, including links to any public datasets.**

1. **Tracer Synthetic Specimen Event Dataset** (our own; generated with the Claude API). Event histories for five specimen types (urine, body fluid, bronchoalveolar lavage, culture swab, tissue), each with its ordered tests. Each record has scan events from collection to plating, the true location over time (held out, used only for evaluation) and outcome labels, with injected failures: lost in tube storage, stalled on the counter, misrouted, never sent, and content failures rejected at accessioning. Planned size: a 200-specimen pilot, then about 12,000 specimens in 30 seeded runs of 7 days. All timing parameters are assumed and labeled; no patient or institutional data is used. Search briefs for each alert are generated with Claude and stored with it. Used to fit and test the modules and to power the demo. No external link: the generator, prompts, validators and frozen dataset (with a manifest of model, prompts and seeds) will be published in https://github.com/scalinity/Tracer.
2. **Specimen Stability Reference Table.** Stability window and transport condition for each specimen type and test, one row each, with a citation, compiled from the IDSA and ASM Guide to Utilization of the Microbiology Laboratory (https://www.idsociety.org/practice-guideline/laboratory-diagnosis-of-infectious-diseases/) and, for swabs, the eSwab manufacturer instructions (https://www.copanusa.com/wp-content/uploads/2024/03/HPC030R05_PI-ESWAB-USA-2023.03.pdf). Sets the time remaining before recollection and parameterizes the generator.
3. **MIMIC-IV Clinical Database Demo v2.2** (https://physionet.org/content/mimic-iv-demo/2.2/). 100 critical care patients, 1,336 microbiology specimens. Not used to train any module. It is evidence that existing hospital data does not capture the interval Tracer monitors.

**8. List the major steps remaining to complete your project.**

1. **Policy table** (Karissa). First pass for five specimen types with window, transport and where the clock stops by Nov 1; cited version, alert routing and tolerable alerts per shift by Nov 8.
2. **Dataset** (Daniel; Karissa reviews 20 timelines). Build the brief sampler, Claude prompt and validator; validated pilot by Oct 31; full dataset frozen by Nov 3.
3. **Models and evaluation** (Daniel). Fit the interval distributions, build the risk model, a simple time-threshold baseline and the evaluation (median warning time, alerts per shift, catch rate, calibration) on held-out runs by Nov 8.
4. **Alerts, board and timeline** (Daniel; Karissa reviews sampled alerts) by Nov 10.
5. **Location estimate and search brief** (Daniel; Karissa reviews 20 briefs for clinical accuracy) by Nov 13, with the Claude brief compared against a fixed-template brief.
6. **Interactive demo** (Daniel builds; Karissa writes the clinical background and workflow narrative). Export results, build the tutorial, audience activity and 3D view, deploy to GitHub Pages. Functionally complete Nov 16; rehearsal Nov 16 to 17.
7. **Present** on Nov 18.
8. **Finish** the camera-ready prototype and demo by Dec 2 and the final report by Dec 9, drafting report sections alongside the build.

**9. Describe your plan for the in-class interactive demo. What platform will you use? Where will it run? How will students access it?**

**Platform.** A static interactive web page (HTML, CSS and JavaScript, no server) in github.com/scalinity/Tracer, hosted on GitHub Pages. The Python prototype runs offline to fit the models and export results; the page reads those results and the fitted parameters and re-computes risk and alerts live in the browser, so the demo uses the real prototype rather than a mock-up.

**Where it runs and how students reach it.** In each student's own browser, on a phone or laptop, from a public link and QR code shown in class and posted on Canvas. No HiPerGator, no hosted server, no install, no login and no API key. We chose this over Streamlit or Jupyter because a server is one more thing to fail when a whole class connects at once. Search briefs are generated with Claude ahead of time and stored with the results, so the demo makes no live API calls. The same files open from disk, and we will keep a recorded walkthrough as a backup.

**What students can do** (about 10 minutes): watch a live board rank simulated specimens by risk; scrub a timeline to see risk, likely location and expected arrival change while a specimen is silent; move the alert threshold and see warning time against alerts per shift for Tracer and the baseline; try to call a missing specimen before Tracer does; and view the specimen's journey in 3D.

**10. List any remaining uncertainties, questions, or unresolved challenges.**

- **Will generated data be realistic enough?** We do not yet know how often Claude's trails will pass the validator, or whether they will be too uniform or have too thin a tail of late specimens. The pilot answers this before we spend more. Results will show that the models recover what we encode, not real-world performance, which is why we plan sensitivity analysis and will say so in the report.
- **Where the clock stops.** Our working rule is collection to plating, from Karissa's bench knowledge. It defines the target variable, so it must be confirmed in the cited policy table. A change means re-running every fit.
- **Alert budget and routing.** Six alerts per shift is our assumption; the number a laboratory would tolerate, and who acts on each failure type, are still Karissa's to confirm.
- **Scope and devices.** Four modules in about three weeks is tight (cut order in answer 2). The 3D view depends on browser graphics support, so the 2D demo is the fallback.
- **API credits and cost.** Generation runs on promotional credits and should cost well under $25; we will set a hard spending limit and confirm the credits do not expire before November 18.
- **Questions for the instructor.** (a) Does a static GitHub Pages page count as a publicly accessible interactive web application? (b) How long is each presentation slot? (c) What level of provenance detail do you expect in the final report for AI-generated synthetic data (we will record model, prompts, seeds and validation)? (d) Do the weekly draft milestones (Results on Nov 4, Discussion on Nov 11) still apply?
