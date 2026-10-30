# AI Teaming Log

How Claude Code and the Claude API were used to build Tracer: what each did, what Daniel Escalante checked or changed, what went wrong, and which calls were made as labeled assumptions. The course asks each team to document and reflect on the contribution of AI agents, including the strategies that worked best. The final report's section on how AI assisted is written from this log.

Times are US Eastern. A pull request is cited by its number and the date it merged.

## Who did what

- **Daniel Escalante** builds everything technical. He sets the task, the rules and the limits, makes the decisions the repo cannot make, and owns what is submitted.
- **Karissa Kocourek** is a clinical microbiology scientist who writes no code. She wrote the policy table (windows, where the clock stops, citations). No agent work asks anything of her.
- **Claude Code** is the coding agent. It wrote the code, tests, configuration and documents in the pull requests below, and opened and squash-merged the pull requests of its own session: #3 to #16, #18, #20 and #21 (Claude Sonnet 5.5, as the session reports; Daniel to confirm). Pull requests #1 and #2 were opened in earlier sessions, and the session that wrote #3 merged #2. Pull requests #17, #19 and #22 came from a separate session, as their descriptions said. Daniel to confirm which model and what he asked for each of those.
- **The Claude API** wrote the event trails of the synthetic dataset (`claude-haiku-5-5`). See "The Claude API jobs" below.

## How to read an entry

Each entry has four lines. **Agent** is what Claude Code did. **Daniel** is what the history shows he decided, checked or changed. **Went wrong** is what failed and how it was caught. **Assumed** lists calls the agent made itself, or that were recorded as assumptions, which Daniel can overturn.

Every pull request was opened and merged through Daniel's GitHub login, so the history does not show whether he read a given diff. Where an entry cannot say, it says "Daniel to confirm".

## Working rules

Set by Daniel in the Oct 30 brief and later messages, and followed in every entry from #3 on:

- Commits are authored by Daniel, with no agent attribution in commits or pull request descriptions. This log is where the agent's part is recorded.
- The API key lives only in `.env`. No workplace or patient data goes in the repo.
- The clock stop, the policy table, the compiled policy and its sources are not changed by the agent. Problems are reported instead.
- One branch and one pull request per task or tight pair, squash-merged to main, with `pytest` and `node visuals/plan.check.cjs` passing first.
- A soft limit of $50 for the Claude API across the project, which the agent may go over only when a result needs it. On Oct 30 this became a spend guard: do a dry run before any job, and stop and ask if one job estimates over $2 or the total would pass $10.
- On Oct 30 Daniel made the build hands-off: the agent settles any call the repo, the plan or the course guidelines can settle, labels it as an assumption in the pull request and in this log, and keeps going. From then on every pull request adds its own entry here before it merges.

## Entries

### Before the pull requests, Oct 9 to Oct 28

The history holds the design documents, system map and milestone submissions (Oct 9), then Milestone 4, the November 18 plan page and its data check, and the 3D exhibit (Oct 28). The project's agent instructions record that the 3D bench was built with an exhibit skill (critic score 93 of 100) and that Daniel asked on Oct 28 for visual builds to run on the larger model.

- **Agent:** Daniel to confirm what Claude drafted and what he wrote for each of these.
- **Daniel:** wrote and submitted the milestones (Daniel to confirm the split).
- **Went wrong:** not recorded.
- **Assumed:** the plan's hour estimates are Daniel's and Claude's together (the plan page says so).

### Oct 29, 9:51 PM · #1 · Load Karissa's policy table and settle the clock at receipt

- **Agent:** compiled Karissa's exported policy table (44 rows) into `config/breach_policy.yaml` with a compiler, a loader that checks it against the workflow graph, and nine tests. Found public sources for three rows the sheet left unsourced, so 34 of 44 rows are sourced. Rewrote the sections of the architecture, plan and notes that assumed the clock stops at plating.
- **Daniel:** Karissa chose receipt as the clock stop for every row (her table and notes, per the description). Daniel to confirm what he checked.
- **Went wrong:** the system map page still drew a plating clock. It was logged as gap 6 in `visuals/README.md`.
- **Assumed:** the 10 rows with no public source stay marked `assumed: true`. The agent added sources only where the sheet gave none and never overrode one the sheet gave.

### Oct 30, 4:40 AM · #2 · Bring the November 18 plan up to date after the policy table

- **Agent:** edited the plan's data only: K1 answered by the table, S2 and D1 reduced to what was left, two answered questions and the late-table risk removed, and a new task W7 to check the plan against the revised guidelines of Oct 28.
- **Daniel:** the Oct 30 brief began by telling the agent to merge this pull request. Daniel to confirm the rest.
- **Went wrong:** nothing recorded.
- **Assumed:** none recorded.

### Oct 30, 4:45 AM · #3 · Finish the project scaffold for the generator build (S2)

- **Agent:** listed the dependencies with lower bounds, made `src/pipeline`, `src/models` and `src/api` packages, ignored `.env` with an `.env.example` naming the variable, and added `pytest.ini`. Confirmed the baseline first: 9 tests passed.
- **Daniel:** gave the task order and rules in the brief. Daniel to confirm the diff review.
- **Went wrong:** a bare `pytest` could not import `src`. `pytest.ini` puts the repository root on the path.
- **Assumed:** none.

### Oct 30, 4:47 AM · #4 · Add the generator parameters and load them with the policy (D1)

- **Agent:** wrote `config/config.yaml` with every generator parameter (arrivals, specimen mix, units, priority, route split, leg times as a median and a 90th percentile, fault rates), each marked `source: assumed` with a one-line reason. The loader fails loudly on sums that are off, a mix entry naming no policy, a fault naming a missing state, or a non-positive median or spread (59 tests, one per failure). Added a `misrouted` state to the workflow graph.
- **Daniel:** the brief required every timing parameter to carry `source: assumed`. Daniel to confirm the diff review.
- **Went wrong:** nothing recorded.
- **Assumed:** every number in the config, and the `misrouted` state (marked ASSUMED in the graph). None is fitted or cited.

### Oct 30, 4:50 AM · #5 · Add the brief sampler with arrivals, mix, faults and the timing skeleton (D2 and D3)

- **Agent:** wrote `make_briefs`, a seeded sampler that draws each specimen's type, tests, unit, route, collection time, planted fault and every timing target, and labels it from the policy. 84 tests cover byte-identical output by SHA-256, the fault mix within 3 standard errors over 10,000 briefs, the hourly profile, and stage times that sum to the receipt time.
- **Daniel:** task and rules from the brief. Daniel to confirm the diff review.
- **Went wrong:** a test caught the timing code reading the stall delay under the wrong key (`stalled` instead of `stalled_at_station`).
- **Assumed:** a tube fault forces the route to tube, and a never-collected specimen is labeled a breach with no clock started.

### Oct 30, 4:55 AM · #6 · Add the trail prompt, request builder, cost gate and usage log (D4)

- **Agent:** loaded the Claude API skill before writing API code, then built the request for one brief: `claude-haiku-5-5`, low effort, thinking off, a JSON schema for the reply, the stable instructions first with the cache breakpoint and the brief last. Added a token count, a price estimate, a `--budget-usd` gate that aborts before anything is sent, and a usage log. 105 tests, all offline.
- **Daniel:** the brief set the order for every job (count tokens, pilot, then Batch) and the rule to stop before any paid call above the pilot.
- **Went wrong:** two slips in the tests (an index comparison, an and/or precedence), found and fixed before the merge.
- **Assumed:** the model, effort and caching choices follow the plan's recipes; the prices are the plan's list prices of Oct 6.

### Oct 30, 4:59 AM · #7 · Add the nine-check trail validator with one retry and a drop log (D5)

- **Agent:** wrote the nine checks from the plan's recipe, a single retry that adds the error text to the brief, and a drop log. Every check has a broken example and a test of why it fails; a trail built in code from 6,000 briefs passes for every fault and both routes; switching off any one check fails at least three tests.
- **Daniel:** task and rules from the brief. Daniel to confirm the diff review.
- **Went wrong:** a check test used a hardcoded station, one test had a vacuous `or True`, and the first list of first names for the personal-data check contained ordinary words (green, white, frank). All were found and fixed before the merge.
- **Assumed:** check 7 also holds the collection and receipt scans to within 2 minutes of the brief's timing, an addition to the recipe, because per-segment drift could otherwise add up to ten minutes.

### Oct 30, 5:09 AM · #8 · Add the pilot runner and the plain-language review sheet (D6)

- **Agent:** wrote the seeded job runner (dry run, required budget, resume, pass rates, real cost from the usage log) and a plain-language review sheet. Ran the pilot: 200 specimens, 198 accepted (98.0% on the first attempt, 99.0% after one retry), $0.0728 against an estimate of $0.0996. Both drops were validator catches, a time-arithmetic slip and a third note. Changed the plan's $25 Console limit to a $50 soft limit.
- **Daniel:** told the agent the key was added, that it expires in 30 days, that no Console limit was set, and to use a $50 soft limit and go over it only when a result needs it. The review sheet was not sent to Karissa.
- **Went wrong:** nothing in the run. The pilot's output hid a problem that was found next (see #9).
- **Assumed:** a 200-specimen pilot (the plan's size).

### Oct 30, 5:14 AM · #9 · Stop notes from revealing the hidden path (D7)

- **Agent:** read the pilot's trails and found that the note on every stalled and misrouted trail (6 of 6) described the hidden path, and that two had copied the agent's own worked example in the prompt. Most other notes only restated the brief (56 unique of 91). Limited a note in the prompt to what the person at that scan could see, rewrote the worked examples, and added validator check 8, which rejects a note naming a station, a stall, a delay, a loss or a re-send. Reran the pilot: 199 of 200 accepted (97.0% on the first attempt), no leaking notes, 11 notes (it was 91). A second pilot with 25 specimens of each fault, lost in tube included, accepted 188 of 190. The two reruns cost $0.13.
- **Daniel:** said to skip Karissa's review (K2) and keep working. The pilot was audited in code instead, so no plausibility count exists, and the manifest says no clinical review was done. On Oct 30 Daniel accepted the code audit in place of the count (the plan records it). The review sheet was not sent to Karissa.
- **Went wrong:** the validator did not catch the leak. Reading the sample did. The agent's own worked example caused two of the leaks.
- **Assumed:** a note should be absent unless the person at the scan could see something worth recording, a rule written by the agent.

### Oct 30, 5:25 AM · #10 · Add batch submission and collection with a freeze step (D8)

- **Agent:** added `run.py --batch`, which submits one batch per run and saves the ids, and `collect.py`, which gathers ended batches, validates, retries failures once and drops the rest, with `--freeze` to write the dataset and manifest. Tested against a fake client that speaks the Batch API. Ran a live smoke batch of 10 specimens (all 10 accepted; structured output and the 1-hour cache work in batch mode). The dry run for the full job priced at $2.98, under the plan's $5 gate.
- **Daniel:** the brief's order (count, pilot, Batch) and the budget gate. Daniel to confirm the diff review.
- **Went wrong:** `run.py --job` defaulted to J1, so the local batch ledger labeled the full run J1; the agent corrected the ledger to J2 before collecting. The collect and run commands first bound their default log paths when defined, which could have let a test write to the real usage log; they now take the path explicitly.
- **Assumed:** the half-price Batch API with a 1-hour cache for all bulk trails.

### Oct 30, 5:37 AM · #11 · Freeze the synthetic dataset: 11,967 specimens from 30 runs (D9)

- **Agent:** collected the 30 batches and froze `datasets/tracer-synthetic-v1.jsonl.gz` (1.3 MB) and `datasets/manifest.json`: 11,967 of 12,000 accepted, 33 dropped, 516 breaches (4.3%), $2.16. Checked the file against itself: the SHA-256 matches the manifest, ids are unique, every stored brief equals the one its seed regenerates, every trail passes today's validator, and none of the 729 notes names the hidden path.
- **Daniel:** the plan card and the brief's rules. Daniel to confirm the diff review.
- **Went wrong:** the model resists writing very long waits, so the drops are not random: 5.9% of specimens with a stage of 120 minutes or more were dropped, against 0.0% under 60 minutes. The manifest reports the counts and says so.
- **Assumed:** none. The manifest records that no clinical review was done.

### Oct 30, 5:40 AM · #12 · Load the dataset into separate observed and truth stores (M1)

- **Agent:** built two DuckDB files, one a model may read (11,967 specimens, 47,592 scans) and one holding the planted fault, true timings, breach label and hidden path (68,208 segments), opened only through `open_truth()`. Split the runs by run number: 0 to 23 for fitting, 24 to 29 held out (2,399 specimens, 121 breaches). A test scans every module under `src/models/` for anything that points at the truth.
- **Daniel:** the brief made the truth log an oracle that only the evaluation may read. Daniel to confirm the diff review.
- **Went wrong:** the scanner's own test caught a path it missed (`DATASET_PATH`), and the scanner was widened. The agent also wrote a wrong tick time (ten minutes ahead) for this task in the shared plan; it was corrected, and ticks now use the clock's output.
- **Assumed:** none. The PR notes the guard stops accidents and not a determined person.

### Oct 30, 5:43 AM · #13 · Fit the collection-to-receipt time distributions (M2)

- **Agent:** fitted a lognormal for normal transit to each of the 96 groups by maximum likelihood on the 24 fit runs, plus a pooled stalled component and a never-arrive share. 21 groups had fewer than 30 received specimens and took a parent's fit.
- **Daniel:** task and rules from the brief. Daniel to confirm the diff review.
- **Went wrong:** the command first failed printing a path outside the repo, fixed. The pooled stalled component came out at 5.4% of arrivals against about 3% planted, because normal transit, a sum of legs, is not one lognormal and the stalled component absorbs part of its tail.
- **Assumed:** the stalled component, its weight and the never-arrive share are pooled rather than fitted per group, a deviation from the plan card that follows the architecture, since a group has about two such events.

### Oct 30, 5:47 AM · #14 · Add the risk model and a parameter-recovery check (M3)

- **Agent:** wrote the closed-form risk and a recovery check against the generator's own distribution. Python and the real page, run through node, agree to better than 1e-6 on 50 states before the scan. From 30 minutes on, survival is within 3 standard errors at every checkpoint (worst 1.7).
- **Daniel:** task and rules from the brief. Daniel to confirm the diff review.
- **Went wrong:** a test expected a risk under 0.1 for a 1,440-minute window; the right value is the never-arrive share over the share still unreceived, so the test now computes it. The body of the distribution before 30 minutes is off by up to 0.028.
- **Assumed:** the agent left the page's `riskReceived` alone, because fixing it means changing the 3D exhibit's model and fixture in the same commit (a later task, V1).

### Oct 30, 5:53 AM · #15 · Compare the risk model with the baseline on the held-out runs (M4)

- **Agent:** wrote the evaluation: a quantile rule against the risk model on the 6 held-out runs at 1, 2, 4 and 6 alerts per shift, with calibration and bootstrap intervals. At 6 per shift both catch all 100 breaches and the median warning is 45 minutes for the model against 30 (difference +10 to +15, 95% bootstrap). Calibration at 30 minutes of silence: Brier 0.109 against 0.119 for the base rate.
- **Daniel:** read the result and its caveats and agreed (Oct 30 message). Daniel to confirm the diff review.
- **Went wrong:** the first version set each knob on the held-out runs, and the median warning among the caught rewarded a method that misses the hard cases. The agent fixed both: each knob is set on the fit runs from alert volume alone, and two measures count a miss as zero.
- **Assumed:** 6 alerts per shift is the provisional budget in the architecture; it is about a third of all specimens, so the headline is generous. Data and model share assumptions because both are ours, and the plan's baseline ignores the window.

### Oct 30, 11:35 AM · #16 · Update the plan for the Oct 30 state

- **Agent:** applied Daniel's plan edits to `visuals/plan.html` (the run-order panel, task V1, K2 skipped, the M5 triage defaults, the M6 hold, the M7 draft steps, the API spend text and the footer) after diffing the published copy against the file.
- **Daniel:** wrote those edits on the published plan and asked for them in the file.
- **Went wrong:** nothing recorded.
- **Assumed:** none.

### Oct 30, 11:42 AM · #17 · Add the revised course project guidelines

- **Agent:** made in a separate session: Daniel to confirm.
- **Daniel:** added the guidelines PDF, which the plan and the report are now checked against. Daniel to confirm the rest.
- **Went wrong:** the description carried a "Generated by Claude Code" footer. At Daniel's request it was removed from this description and from #19 and #22 (see the entry for this log's own pull request).
- **Assumed:** none recorded.

### Oct 30, 11:44 AM · #18 · Add alert triage and the operating point (M5)

- **Agent:** alerts on expected harm (the risk times a weight for the specimen's urgency tier), with the threshold set on the fit runs to the per-shift budget; routing by transport route; a one-row-per-alert list for the held-out runs; and a window-aware baseline beside the plan's baseline. Scored the default weights 3, 2, 1 beside equal weights and 9, 3, 1, with alerts and warning by tier.
- **Daniel:** supplied the M5 decisions: the weights (3, 2, 1), the operating point (the longest median warning at 6 or fewer alerts per shift, set on the fit runs), the routing, and the window-aware baseline, with every value marked assumed in `config/triage.yaml`.
- **Went wrong:** the weights move the headline. At 6 alerts per shift the median warning over all 100 breaches is 55 minutes at 3, 2, 1 and 45 at equal weights, against 30 for the plan's baseline, because the extra warning goes to tiers 1 and 2 while tier 3 (61 of the 100 breaches) gets less (40 against 45). At equal weights the model only ties the window-aware baseline at 4 and 6 alerts per shift. The held-out volume at the 6-per-shift setting is 6.4. The agent put all of this in the description and the plan.
- **Assumed:** every weight and role (Daniel's defaults, marked assumed). A specimen the location filter places at its unit goes to the charge nurse.

### Oct 30, 11:46 AM · #19 · Bring the plan in line with the revised course guidelines

- **Agent:** made in a separate session: Daniel to confirm.
- **Daniel:** the description says the Results, Discussion and Responsible Deployment Plan drafts were removed, W8 added, the run of show given to the hands-on demo, and the report brief rewritten to the 2 to 4 pages and eight parts. Daniel to confirm.
- **Went wrong:** the same footer as #17, removed.
- **Assumed:** none recorded.

### Oct 30, 11:50 AM · #20 · Add the location filter and place-based routing (M8)

- **Agent:** ported the page's semi-Markov location filter to NumPy. With the page's own normal CDF the posterior matches the JavaScript to 2e-10 at every minute. Built the legs for the dataset's workflow from the fitted intervals, the assumed leg times and the fault rates, with a left-at-unit place beside lost, and routed alerts by the most likely place.
- **Daniel:** the plan card and the M5 routing rule (a specimen still at the unit goes to the charge nurse).
- **Went wrong:** the first parity test failed at 1.2e-6 against a bound of 1e-6. The cause was the page's polynomial error function (about 1.5e-7) amplified by the hazards, not a difference in logic. The agent proved it by swapping in the same polynomial, then tested both: under 1e-9 with the page's function and under 5e-6 with the exact one.
- **Assumed:** the split between legs, the scale and the mix of failures are assumptions the data shares, since the data was drawn with the same ones.

### Oct 30, 11:53 AM · #21 · Score the location filter against the hidden truth (M8b)

- **Agent:** placed every silent held-out specimen at 15, 30, 60 and 90 minutes of silence and compared with the truth. Top-1 is 49, 55, 39 and 44 percent against 34, 38, 30 and 39 percent for always naming the commonest place; top-3 is 97, 88, 84 and 80 percent. The probability of lost is within 2 points of what happened below 7 percent predicted, from only 13 specimens that were really lost.
- **Daniel:** the plan card. Daniel to confirm the diff review.
- **Went wrong:** a splice of the plan file dropped task V1, and `plan.check` caught it before the commit. The plot's two titles nearly overlapped and were shortened after rendering it.
- **Assumed:** the data was drawn with the structure and fault mix the filter assumes, so these numbers are a best case.

### Oct 30, 11:57 AM · #22 · Make the build hands-off in the plan

- **Agent:** made in a separate session: Daniel to confirm.
- **Daniel:** the description says M6 tags as soon as M5 merges, D7 records the accepted code audit, S1 and P7 become checks Claude Code runs itself, and the phone test, W1, W2 and the talk are parked on Daniel's checklist. The spend guard became the one hard stop.
- **Went wrong:** the same footer as #17, removed.
- **Assumed:** none recorded.

### Oct 30 · W8 · Start the AI Teaming Log (this pull request)

- **Agent:** wrote this log from the pull request descriptions, the commit history, the manifest and the usage log. Removed the "Generated by Claude Code" footer from the descriptions of #17, #19 and #22 at Daniel's request; a search found no other footer in the files, commit messages, pull request comments or issues.
- **Daniel:** asked for the log, its contents and the footer removal.
- **Went wrong:** the history does not show Daniel's part in most entries, so most say "Daniel to confirm". He should correct any entry that misstates his part.
- **Assumed:** the agent's account of each entry comes from the descriptions and its own record of the session, not from a second source.

### Oct 30 · M6 · Save the midterm snapshot and tag v0.1-mvp (this pull request)

- **Agent:** cloned `main` fresh into an empty folder and rebuilt everything from the frozen dataset: the two stores, the fit, the recovery check, the evaluation and the location scoring. Every result matched the committed one, and 385 tests passed in the clone. Then wrote `results/mvp-nov8.json` (the headline by alerts per shift, the pre-registered bar, nine caveats, the full results and where each came from), pinned its hash in a test, and tagged the merge commit `v0.1-mvp`.
- **Daniel:** decided on Oct 30 that tags happen on their own once their condition is met, and that the file stays untouched so the December 2 results can be compared against it.
- **Went wrong:** the first draft of the snapshot counted all 96 groups as having taken a parent's fit instead of 21, found by reading the output before it was saved.
- **Assumed:** the bar is judged on median warning over all breaches (a miss counts as zero), at the lower end of the 95% interval. The clean checkout reused the repo's existing Python environment instead of installing a new one. The tag sits on the merge commit that adds the file, whose code is the commit the evaluation ran on.

## The Claude API jobs

All jobs call `claude-haiku-5-5` at low effort with thinking off. The reply must match a JSON schema, so replies parse and retries stay rare. Bulk jobs run through the Batch API at half price, and every call is logged to `data/claude/usage.jsonl` (kept local). The order for each job is a token count, a pilot, then the batch, and a job's estimate must be under the plan's gate before it starts.

| Job | What it wrote | Requests | Cost | Result |
|---|---|---|---|---|
| D4 check | Two calls to check that the cached prefix is read | 2 | $0.0012 | The second call read 3,953 cached tokens |
| J1 pilot (seed 0) | Event trails for 200 specimens | 204 | $0.0728 | 198 accepted, 2 dropped by the validator |
| J1b reruns | The pilot again after the note fix, and 190 specimens with every fault | 400 | $0.1350 | 199 of 200 and 188 of 190 accepted |
| J2 smoke | 10 specimens through the Batch API | 10 | $0.0019 | All 10 accepted |
| J2 full | 12,000 specimens in 30 seeded runs of 400 | 12,077 | $2.1612 | 11,967 accepted, 33 dropped |

Total about $2.37 of the $50 soft limit. The manifest (`datasets/manifest.json`) records the model, the prompt hash, the seed, the counts and the cost of the full job.

**What each job wrote.** For each brief the seeded code had already fixed the specimen, its planted fault and every timing. The model wrote the observed scan events and the hidden true path for that brief, and nothing else. Code owns the statistics, since language-model timings have thin tails and cannot be re-drawn.

**What the validator caught.**

- In the first pilot it rejected 2 of 200: a time-arithmetic slip and a trail with a third note.
- In the full job, 33 specimens failed twice and were dropped. A specimen can fail more than one check: check 5 (segment lengths) 28 times, check 7 (receipt time) 15, check 4 (truth segments) 3, check 1 (schema) once. 99.4% were accepted on the first attempt and 99.7% after one retry. The drops lean toward long delays (5.9% of specimens with a stage of 120 minutes or more, against 0.0% under 60), which the manifest reports.
- It did not catch notes that gave away the hidden path in the first pilot (all 6 stalled and misrouted trails). A person reading the sample found that, and check 8 was added.

**Not done.** A clinical review of twenty pilot timelines (K2) was skipped at Daniel's request, and the manifest says so. Any result built on the dataset says that it is synthetic and had no clinical review.

## What has worked, and what to avoid

Drawn from the entries above. Daniel can add his own view.

- **Make the checks fail loudly and name the failure.** A loader or validator test per failure (#4, #7) found three of the agent's own mistakes before they spread.
- **Read the output, not only the counts.** The validator reported 198 of 200 accepted while every stalled trail leaked the answer (#9). Reading ten trails found it in minutes.
- **Keep the answer key in a different file, and test the wall.** The truth store is separate and a test fails if a model module can reach it (#12). The test itself had a gap, found by its own cases.
- **Tune on one set and report on another, and count misses.** The first evaluation did neither (#15), and the fix changed what the headline can claim.
- **Say what a result depends on.** The weights moved the headline and the window-aware baseline closed most of the gap (#18). Both are in the description, not only in the code.
- **Avoid copying your own examples into a prompt.** The model copied a worked example into the data (#9).
- **Avoid a quiet default.** `--job` defaulted to J1 (#10) and a default log path was bound too early; both were harmless only because the work was checked afterward.

## Next entries

Each pull request adds its own entry above this line before it merges, in the same four lines, newest last.
