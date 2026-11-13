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

### Oct 30 · S1 · Check the API key and the credits (this pull request)

- **Agent:** checked what the repo and the key can show. A free token-count call returned 200, so the key works. `.env` holds only the one variable, and the Messages API gives no credit balance or expiry, so the agent recorded in the plan that neither can be confirmed from here, with the expiry Daniel gave (30 days, about Nov 29) beside it.
- **Daniel:** told the agent the key expires in 30 days and that no Console limit is set, and asked on Oct 30 for S1 to be closed this way if the balance cannot be read. Daniel to confirm the balance and expiry in the Console.
- **Went wrong:** the first check failed in the agent's own snippet (`load_dotenv()` needs an explicit path outside a file); no key was printed.
- **Assumed:** the 30-day expiry runs from the day Daniel added the key (Oct 30), so about Nov 29. Daniel to confirm.

### Oct 30 · V1 · Move the system map and the 3D exhibit to the receipt clock (this pull request)

- **Agent:** changed the system map page so the clock stops at receipt (no set-up step after it, no "Plated, clock stops" label, the policy's 60-minute window for unpreserved urine), simplified the risk code to the matching closed form, and moved the 3D exhibit's model, fixture, help text, captions, console screens and lab deck to the same rule. Extended the parity tests to every state, rebuilt the exhibit's `dist/`, and captured the exhibit in WebGPU Chrome (60 frames per second, no console errors) and looked at the changed screens. Closed gap 6 in `visuals/README.md` and fixed three sentences on the Milestone 2 page.
- **Daniel:** asked for V1 in the plan with its steps. His Oct 28 note asks for visual builds to run on the larger model; this change was made in the current session as a model change with a visual check, not a redesign. Daniel to confirm he is content with that or wants a second look.
- **Went wrong:** the exhibit's tests had assumed the page's old defaults, so the original had to be told the exhibit's window and receipt. The rearranged risk formula moved seven calibration numbers in `results/evaluation.json` by about 1e-16, which the committed-numbers test caught, so the file was regenerated (the alert list is identical). A location test sliced the page by the text of its old constant and broke when the page changed.
- **Assumed:** the exhibit keeps its 120-minute default window (the policy's window for lavage and tissue) while the page uses 60 for its urine. The page plots 90 minutes instead of 130. The page's scenario has the scan at 52 minutes, after the alert at 40 and before the window closes at 60. The exhibit's Stalls and Never-sent presets moved from 55 to 62 minutes so the alert has fired, and the lesson's alert-line step now needs an alert at 50 minutes or sooner. The Milestone 2 page mirrors a submitted milestone; its three sentences were changed because no page may stop the clock at plating (Daniel to confirm).

### Oct 30 · W6 · Bring the design documents up to date (this pull request)

- **Agent:** rewrote the parts of the architecture document and the README that treated a simulator as the event source (the event-source section, the component and library tables, the data-flow diagram, section 6, the build order and the divergences) to describe the Claude-generated dataset, with its counts, cost and the missing clinical review. Updated the worked example to the model's current numbers, put the commands that really run in the README (each run before it was written down), added a note to the older spec and the MIMIC findings, and added a test that holds the document's counts, windows, fault rates and example numbers to the code and the manifest. Updated the local agent instructions.
- **Daniel:** the plan card set the scope (sections 1.1, 6 and 12, the README, and the local instruction files).
- **Went wrong:** the first plan covered only the three named sections, but a search found the simulator named as the event source in the component table, the diagram, the build order and the divergences too, so the done line could not hold; all were rewritten. The worked example's numbers were stale after V1 changed the model.
- **Assumed:** sections 7 (the API) and 4.3 and 4.4 (the twin and alert records) keep their Week 4 design, since the bundle exporter and the demo (D11 to P7) will update them when they are built. The older spec and the MIMIC findings got a note instead of a rewrite, because they are dated records. The doc's worked example keeps the lavage specimen with a 120-minute window and takes its numbers from the exhibit's model.

### Oct 30 · M7 · Write the headline paragraph (this pull request)

- **Agent:** wrote `results/README.md` from the midterm snapshot: one headline paragraph, a table of the three alert budgets and a list of the files. The window-aware baseline's result sits right beside the headline number, and every caveat is attached to the number it qualifies. Added a test that checks each number in the paragraph and the table against `results/mvp-nov8.json`.
- **Daniel:** set the bar in advance (at least 5 more minutes of median warning at the same alerts per shift) and asked on Oct 30 that the window-aware result sit beside the headline and not only in the caveats. Sharing the paragraph with Karissa is Daniel's step, and nothing was sent.
- **Went wrong:** nothing failed. The paragraph is long because the caveats are part of the claim; Daniel to confirm he wants it that way.
- **Assumed:** the paragraph states the bar on median warning over all breaches (a miss counts as zero), the same measure the snapshot records. It leads with the default weights 3, 2, 1 because they are the plan's decision, and gives the equal-weights figure in the same sentence.

### Oct 30 · D11 and D12 · Export the demo bundle with its metrics and schema (this pull request)

- **Agent:** wrote `src/api/export.py`, which turns the held-out runs into `docs/demo/bundle.json`: each specimen's events, risk and location probabilities, the alert moment of three methods, and the truth, plus the fitted parameters, the policy, the urgency weights, and a grid of 20 operating points for the plan's baseline, a window-aware baseline and the risk model. Wrote the JSON Schema and 37 tests, and moved the architecture document's interface section from a planned server to the bundle that exists.
- **Daniel:** the plan cards set the contents (events, risk and location every 5 minutes, the alert moment, the truth, the parameters and the policy hash, a grid of 20 thresholds, a schema and 10 candidates), and his run order asked for the grid to cover both baselines and the model.
- **Went wrong:** the first test run failed on one specimen about 1e-12 from its alert threshold, because the risk had been rounded to 10 decimals and the rounding crossed the line. Risk is now stored at full precision. The first showcase picks had day-long windows the timeline and the 3D view cannot show, and one "clear catch" was only an alert at minute 5, so the rules now prefer short windows and require some silence first.
- **Assumed:** the grid is by alert budget (0.5 to 14 per shift), because that is the fair comparison between methods and each knob is set the way the evaluation sets it. The bundle holds all six held-out runs and stops the risk and places at 240 minutes (the risk runs on to the strictest alert if that comes later). Showcase candidates are chosen by fixed rules, not by hand, and `heroes` is empty until G3. The bundle is 3.3 MB, about 480 KB over the wire, which is fine for a phone and was not checked on one.

### Oct 30 · M9 · Write the search briefs, a template and a Claude version (this pull request)

- **Agent:** wrote `src/api/search_briefs.py` and ran the job. It reads each alert's inputs from the bundle (specimen type, minutes of silence, risk, the most likely places, the expected arrival, minutes left), writes a fixed-template brief from them, and has `claude-sonnet-5-5` write the other through one Batch call, with a validator, one retry, a spend guard, and a seeded blind key. Added the expected arrival (the time until half of the specimens still to come have arrived) to the risk model for the input. Did the dry run first ($1.10 estimated for 806 alerts, $2.39 already spent, so inside the guard), tested the prompt on five alerts, fixed it, and ran the batch: 806 of 806 accepted, $0.93.
- **Daniel:** set the guard (a dry run first, stop over $2 for a job or $10 in all), asked for both versions to be kept and labeled for a blind comparison later, and said to send nothing to Karissa. Nothing was sent.
- **Went wrong:** the API rejected `thinking: {"type": "disabled"}` on Sonnet 5.5 during the free token count, so no money was lost; the API skill's notes give `between_tools` instead. The first five-alert test showed briefs of 62 to 69 words against the 60-word limit and a "90 percent" that came from an input key's name, so the prompt was tightened. Two briefs failed the checks in the batch and passed on the retry. The first template listed the same tube station twice, once as stalled, so the inputs now merge the two.
- **Assumed:** the recipe was changed in four ways: it says the brief goes to whoever the alert is routed to (the role is in the input) instead of the receiving technologist, it asks for at most 50 words so a brief stays under 60, it carries one short example, and the input's expected arrival is a median and a latest likely time. The job covers all 806 alerts in the bundle, not about 300. The Claude and template briefs say nearly the same thing in different words, so whether the language model adds anything is for the blind comparison to say. The risk, places and roles come from the bundle and `alerts.csv` and agree with them.

### Oct 30 · G3 · Write the showcase stories (this pull request)

- **Agent:** wrote `src/api/heroes.py`: for each of the ten candidate specimens in the bundle it builds the facts (the scans seen up to receipt, what Tracer and the plan's rule did, the true path in plain words) and has `claude-sonnet-5-5` write a story, a takeaway and an audience question, with a validator, two retries and the spend guard. Did the dry run first ($0.07 estimated, $3.33 already spent), ran the job, and merged the stories into the bundle through `docs/demo/heroes.json` so the bundle still regenerates from code.
- **Daniel:** the plan card set the recipe and the count, and his run order set the spend guard. The stories are for the talk; which to tell is his call.
- **Went wrong:** the job stopped twice on a story that stated a number not literally in the facts, once a difference of two stated minutes (30 minus 20) and once a duration (43 minus 29). The agent patched the validator once and the patch did not apply, so a third run still used the strict rule, then fixed it properly: durations are in the facts and a difference of two stated numbers is accepted. The failed runs cost about $0.14 of the $0.20 the job took in all. One story also mentioned plating, so the facts now stop at receipt, and the job was rerun.
- **Assumed:** a difference of two stated numbers is a derived fact and not an invented one. The recipe's "past tense, no names" is checked only for names, by a rule that flags capitalized words, so past tense is read by a person. The candidates were picked by rules and not by hand, and Daniel can swap any of them (the stories would then need rewriting).

### Oct 30 · A2 · Run the sensitivity analysis (this pull request)

- **Agent:** wrote `src/pipeline/sensitivity.py`, which reweights the held-out specimens for other arrival volumes, hours of the day and specimen mixes, and scores the plan's baseline, the window-aware baseline and the risk model with the knobs fixed and again retuned to 6 alerts per shift in each scenario. Wrote 14 tests, the results section, and the summary in the plan. The exporter's knob counters now take optional weights.
- **Daniel:** the plan lists the sensitivity runs for after November 18, and his run order pulled them ahead. He did not choose the scenarios.
- **Went wrong:** the first share test expected 1.5 times as many urine specimens to raise their share by 1.5 times, which is not what renormalizing does (the share goes from about 39 to 49 percent), so the test and the descriptions were corrected. Nothing ran against the Claude API.
- **Assumed:** reweighting stands in for generating new data, which keeps each specimen's behavior fixed and so cannot show congestion (the generator does not model it either). The scenarios are one set of choices: volume at 0.5, 0.75, 1.5 and 2 times, three hourly profiles, and three mixes (more urine, more swabs, more urgent specimens). The model uses the default weights 3, 2, 1. The gradient-boosted challenger was not run; it waits until after November 18.
- **Found:** the arrival profile does not move the result. Volume does: at the same alert budget the model's median warning falls from 55 to 35 minutes at twice the volume, and its lead over the window-aware baseline exists only at the original volume. That belongs in the report's limits.

### Oct 30 · P2 and P3 · Build the demo page's board and specimen timeline (this pull request)

- **Agent:** an Opus subagent, briefed with absolute paths and the plan cards, wrote `docs/demo/index.html` (one file, vanilla JavaScript, no request beyond the two data files) and `tests/test_demo_page.py`. The page ranks the outstanding specimens by expected harm with the plan's rule beside the model, opens a timeline with a scrubber, and recomputes the risk in the browser. The parent reviewed the diff, re-ran the tests on current `main` and looked at the page at phone and laptop widths.
- **Daniel:** the plan cards set what the page shows, the project's color tokens set how it looks, and the run order put it ahead of schedule. He has not seen the page. The footer naming both authors is the agent's addition: Daniel to confirm.
- **Went wrong:** with no limit on the board, specimens whose window had closed piled up to 10 rows at noon on late days, so they now stay ranked for one shift and then move to a list. The screenshot review found colliding chart labels, verdict text breaking into columns, numbers wrapping mid-value and windows shown in hours, and fixed them. A label on the chart (Tracer's alert line) still touches the curve.
- **Assumed:** the default moment is the first five-minute clock, scanned over the 42 days in date order, with at least four specimens outstanding and a tier 1 specimen that Tracer alerts on, the plan's rule has not, and the specimen misses its window. The board lists one day's run at a time, since runs are separate simulated weeks. The page refuses to score if the model's policy hash differs from the policy's. Both search briefs are shown labelled by writer and marked as not the blind comparison. The quoted median warnings are the headline point's. A browser check on a real phone, Firefox, WebKit and a screen reader has not run (P7).

### Oct 30 · P4, P5 and P6 · Add the threshold control, Beat Tracer and the 3D link (this pull request)

- **Agent:** an Opus subagent, briefed with the plan cards and the page's exported names, extended `docs/demo/index.html` in three commits: a threshold slider over the bundle's grid with a stability-window control that recomputes risk in the browser (P4), Beat Tracer with a seeded round generator and an activity card (P5), and a View in 3D link with a byte copy of the exhibit in `docs/demo/exhibit/` (P6). It wrote 17 tests. The parent re-ran the suite on current `main` (565 passed) and looked at the new sections at phone and laptop widths.
- **Daniel:** the plan cards and the Beat Tracer notes set the three features. He has not played the game or seen the page. The game's rules for choosing and scoring rounds are the agent's: Daniel to confirm.
- **Went wrong:** the brief gave the headline grid index as 2 and the bundle says 12, which the agent caught by reading it. Screenshot review found a clipped axis label, colliding chart labels, tall feed rows on a phone and a verdict line running off the edge, all fixed. A rule against any "plating" wording could not hold for the built exhibit, whose help text says plating is outside the clock, so that check is scoped to the sentence that says so. The playtest with three people did not run.
- **Assumed:** the window control sets one window for every specimen, and with it set both methods are recomputed at the same thresholds, so alerts per shift drift from the budget and the page says so. A round's miss is a specimen that missed a window of at most 120 minutes; the other five arrived in time from the same run. Time runs at 3 minutes a second. Tracer's score in a round is its stored alert at the headline setting, zero when there is none. Before the truth is shown, the 3D link's fate comes only from the scans shown so far, and the link carries `truth=0`. The link opens in a new tab.
- **Found:** some window settings favor the plan's rule (125 minutes for every specimen at 5.5 alerts per shift gives Tracer 85 minutes of median warning and the rule 95), and in seed 18's rounds Tracer alerted on 12 of the 15 specimens that arrived in time. Both belong in the report's limits.

### Oct 30 · P7 · Publish the demo on GitHub Pages (this pull request)

- **Agent:** turned on GitHub Pages for `main`, folder `/docs`, through the GitHub API (first from this branch, to check the public address before merging, then switched to `main`), and added `docs/.nojekyll`. Wrote `src/api/qr.py` and its tests, a test that loads the page in Chromium, Firefox and WebKit at phone and laptop widths under the `/Tracer/` path, and a test that no key reaches the repo or the site. Checked the deployed files against the repo by hash and decoded the QR code from the public site.
- **Daniel:** the plan card set the steps, and his run order put Pages and the browser checks ahead of schedule and parked the phone test on his checklist. He did not choose the address or the QR code's size.
- **Went wrong:** the first engine test read the window control's label before the page's next animation frame had applied it, and failed in all three engines; the page was right and the test now waits for the change. The public site's root returns 404, because only `/demo/` has a page.
- **Assumed:** the address is the project site's, <https://scalinity.github.io/Tracer/demo/>. Publishing `docs/` makes everything in it reachable by address: the notes, the submissions and the tracked guidelines PDF, all already public in the repository. The browser checks ran against a local copy served under `/Tracer/`, and the public site was checked with curl, not loaded in a browser. A real phone, a screen reader and Beat Tracer with three people have not been run.

### Oct 30 · A4 (clean-clone test) · Run the README from a fresh clone (this pull request)

- **Agent:** cloned the repository from GitHub into a new folder, installed `requirements.txt` into a new venv and ran the README's commands in order, then wrote `scripts/clean_clone.sh` to repeat it and fixed what the first run found. The script fails on a test failure or on any committed output that differs from what the commands write again.
- **Daniel:** the run order pulled the clean-clone test ahead of schedule. He did not choose what it checks.
- **Went wrong:** the README ran `pytest` before the store existed, so 4 tests failed and 44 errored; the store now comes first. A fresh install gets DuckDB 2.0, which removed `fetchone()` on a relation, and five test queries failed on it (the product code was not affected); they now go through `execute()`. The script's first version broke on a Python path with spaces in it.
- **Assumed:** a fresh clone installs the newest versions that `requirements.txt` allows, so the test is only as good as that day's releases and should be rerun before the freeze and the final prototype. The key-value test skips without `.env`, as intended. The README and demo polish parts of A4 are not done.
- **Found:** with the fixes, every committed result, the demo bundle and the QR code regenerate byte for byte on a clean clone, and 575 tests pass.

### Oct 30 · Plan · Bring the plan up to date after the hands-off build (this pull request)

- **Agent:** checked the plan against what landed on `main` (pull requests 23 to 36, the `v0.1-mvp` tag, `results/`, the demo page and this log), marked the run order complete, added the open placeholders on the demo page and the two findings for the limits beat, and updated the spend, the risks and the footer. Republished the shared plan page with the exhibit's rebuilt `dist/` from V1.
- **Daniel:** asked for the plan to show the state the build reached.
- **Went wrong:** nothing. The public demo address could not be reached from the agent's session, so the live-site claims rest on the P7 checks recorded above.
- **Assumed:** "Why it is built this way" is left without an owner, as the build found it, rather than given a task.

### Oct 30 · Why · Write the "Why it is built this way" section of the demo (this pull request)

- **Agent:** read the demo page, `docs/architecture.md`, `results/README.md`, this log and the course guidelines, and drafted ten short points on why each part of Tracer is built the way it is, each tied to a claim the repository already makes. Replaced the one-line placeholder on `docs/demo/index.html` with them, in the Limits section's list style, and updated the two plan sentences that said no task owned the section.
- **Daniel:** asked for the section, read the full draft and approved it for the public page as written.
- **Went wrong:** the approved draft said Tracer does not flag a delay "between receipt and plating", and the page test that keeps any plating wording off the page caught it, so the line now says "at the bench, after receipt", the architecture notes' own words. On the agent's Linux machine three regeneration tests (evaluation, bundle, location) differ from the committed files in the last digits of a few floats, on `main` as well as on this branch; the page tests and the plan check pass.
- **Assumed:** the section names Karissa in the point on where the clock stops, as the page already credits her, and leaves out the search briefs to stay short. It quotes no number the results do not hold; the stalled-state story is the location estimate's from §13.1 of the architecture notes.

### Oct 30 · Exhibit · Show the pause icon on the 3D exhibit's play button (this pull request)

- **Agent:** found that the pause icon was two open lines while the play button only fills its icon and draws no stroke, so pressing play left the button blank. Redrew the pause icon as two filled bars, rebuilt the exhibit and copied the new `dist/` into `docs/demo/exhibit/`.
- **Daniel:** spotted the blank button on the live exhibit and sent screenshots of it.
- **Went wrong:** nothing in the fix. No browser could run in the agent's session, so the new icon was checked in the built bundle and by the type check and unit tests, not on screen.
- **Assumed:** filled bars, rather than a stroke on the button, keep the play and pause icons drawn the same way.

### Nov 5 · T1 · Draft the talk, its slides and the run of show (this pull request)

- **Agent:** read the revised course guidelines, the plan's run of show, the demo page, `results/README.md`, the showcase stories and `docs/problem-evidence.md`, and wrote `docs/talk/`: a minute-by-minute run of show for the 15-minute slot, every line each speaker says with stage directions, ten prepared answers, and a source for every number. Added a static slide deck, `docs/talk/index.html`, with a presenter window and a talk clock, published by GitHub Pages beside the demo, a second QR code for Beat Tracer's shared rounds (`?seed=18`), and `tests/test_talk.py`, which keeps the slides' notes and the script word for word and the run of show at 15 minutes.
- **Daniel:** asked for the talk, the slides and the run of show, merged to main. He rehearses it and changes any line before November 18.
- **Went wrong:** the slide links first pointed at `../demo/`, which opens a folder listing from disk; the new link test caught it and they now name `index.html`. The demo's problem section is still a placeholder, so the problem slide carries those facts itself. The automated review found four real problems, all fixed before merge: a presenter window opened mid-talk reset the clock, the bench link started the opening tour so one press of T showed the truth instead of hiding it (now `?truth=0`), the offline demo needs a local server because the browser blocks its data files from disk, and the game cannot end after two rounds, so the fallback now uses the round result screens. No browser could open the public site from the agent's session.
- **Assumed:** the beats, speakers and lengths are the plan's run of show at 15 minutes, unchanged. Karissa's three beats are written out in full from sources already in the repository, so nothing is asked of her, and Daniel reads them if she is away. The line that a missing specimen is often noticed only when someone calls for the result is drawn from the project spec's account of who is affected, and is hers to change in rehearsal. About 130 words a minute is the pace the timings assume.

### Nov 5 · T2 · Add the script page and rebuild the deck for a real screen (this pull request)

- **Agent:** loaded the deck in headless Chrome at 1920 by 1080 and 1280 by 800 and found the Beat Tracer slide's button running into its footer, its address broken mid-token, and captions and the synthetic-data caveat at 17 to 19 px on the smaller screen. Rebuilt `docs/talk/index.html` on one grid and one type scale whose smallest step is 26 px at 1280 by 800, opening dark whatever the system setting, with a run-of-show rail in place of the all-caps labels, the QR codes beside their instructions, the numbers slide redrawn on a 0 to 60 minute axis with the gains from the window (+15) and the tier weights (+10) marked, speaker colours in the presenter window, and a crossfade between slides. An Opus subagent wrote `scripts/build_talk_script.py`, which builds `docs/talk/script.html` from `docs/talk/README.md`: a reading page with a talk clock that lights the current beat, a speaker filter (`?who=karissa`) that reduces the other speaker's turns to cues, and a print view of one speaker's lines. `tests/test_talk.py` fails if the page and the README disagree, and `tests/test_talk_browser.py` checks the fit, the type size, the caveat and the L, N, S and P keys in Chrome. Installed BoardUI's agent skill (MIT) for every coding agent on the laptop; BoardUI is a React kit that cannot run in a static page, so only its method carried over: colours through tokens, one composite type scale, its motion timings.
- **Daniel:** asked for the script page, the deck check and redesign, the BoardUI skill, and a merged pull request with no agent attribution. Beats, speakers, timings and spoken lines were his to keep and are unchanged.
- **Went wrong:** an independent Codex review (GPT-6.1 Sol) found no critical issue and two gaps in the drift tests, both fixed: the page dropped the run of show's "On the screen" column, so changing that column changed nothing and every test passed, and the prepared answers' text was never compared with the README, so a generator that emptied them passed once the page was rebuilt. Mutations of both now fail the tests. The deck's fit-to-screen loop never fired, because `scrollHeight` ignored the slides' bottom padding; it now measures against the padding edge. The rail's hands-on segments first shared a class name with the hands-on slide layout and stretched across the footer, caught on the first screenshot.
- **Assumed:** the caveat stays on the slides that show the demo or its numbers (0, 5, 6, 7 and 9). The cited 61.9% and the MIMIC 80.1 hours on slides 2 and 3 are not synthetic and are not labelled as if they were. The type scale follows the classic 12, 14, 16, 18, 24, 36, 60 point steps in place of the plan's 1.3 ratio, so the numbers slide and the facts fit without shrinking. The script page's clock runs on its own and does not follow the deck's, because the deck's channel messages carry a slide number and a page that posted would move the slides.

### Nov 5 · T2 · Shorten two lines, say which numbers are synthetic, and share one talk clock (this pull request)

- **Agent:** cut "and sometimes longer on broad antibiotics while the team waits for a result" from Karissa's slide 2, which was 137 words for a 60-second beat and is now 125, and split Daniel's slide 7 sentence about the window rule and equal weights in two, in the README and the slides' notes together. Reworded the README's line that every number in the talk is synthetic: the demo's numbers are, while the 61.9%, the 60-minute urine window, the 44 policy rows and the 80.1 hours come from a published audit, the policy table and its guidelines, and MIMIC-IV. Linked the script page's clock to the slides': the slides, the presenter window and the script page now share one clock over the channel the slides already used, with pause, and `tests/test_talk_browser.py` checks it in Chrome.
- **Daniel:** asked for all four changes, which the previous pull request had listed as suggestions.
- **Went wrong:** an independent Codex review (GPT-6.1 Sol) of the shared clock found three problems, all fixed and tested. A slides window opened before the change would have crashed on the script page's clock-only message, so the shared clock moved to a channel of its own. A malformed slide number or clock could throw, so both pages now check every field. A window that had just opened could answer with its blank clock and reset a running talk, so the slides now send their clock only once they know it. Run against the previous commit, the new browser test fails, with the blank clock sent and four page errors.
- **Assumed:** the script page sends only the clock and never a slide number, so it cannot move the slides, and it answers a window that opens later only while its clock runs, so a page opened mid-talk never resets the talk. Karissa's slide 2 line changed at Daniel's request; she has not seen the change.

### Nov 5 · T2 · Introduce Tracer and what it is built to fix, and split the talk evenly (this pull request)

- **Agent:** added two beats to the talk and rebalanced who speaks. "Meet Tracer" (Daniel, 45 seconds) introduces it in plain terms, as package tracking with no scans along the way, with what it reads and what it gives the lab every five minutes. "What Tracer is built to fix" (Karissa, 45 seconds) sets today against Tracer, from the project spec's table of what labs use now: silent status screens, a missing specimen found when someone calls, one time limit for every specimen, and tracking tags that need readers in every hallway, then the aim: fewer repeat collections, quicker results, less time chasing. "How Tracer works" lost the 15 seconds that the new beat now says. Karissa now walks the specimen on the projector and gives the closing line, so the words split 526 to 520 and the speaker changes eight times. The slides, the README, the script page, the plan's run of show and the tests changed together, and a test keeps the split within 45 to 55 percent.
- **Daniel:** asked for an introduction, a plain-language explanation, the gaps it fixes and why it is useful as an application; chose to run 16:15 rather than cut the questions or the hands-on time; gave the new beat to Karissa and asked for a 50/50 split with frequent handoffs.
- **Went wrong:** the browser test measured exactly ten slides, so the two new ones would not have been checked; it now counts the slides on the page. On the first screenshot, "Where each one probably is" wrapped and pushed the two columns of the new slide out of line; it now says "likely", which is also the spoken word.
- **Assumed:** the new beats state what Tracer is built to do, not what it has shown in a laboratory, so they carry no synthetic numbers; the measured lead stays on the numbers slide with its caveat. If the instructor holds the team to 15:00, every beat stays and the questions get 1:30 instead of 2:45. Karissa has not seen her new lines; Daniel goes over them with her on Sunday. An independent Codex review (GPT-6.1 Sol) found one warning, fixed: the benefits first read as established outcomes, and now read as the aim.

### Nov 6 · T2 · Show how Tracer works and how it was built, in pictures (this pull request)

- **Agent:** added a slide on how Tracer was built and tested, in two rows. The data row runs from briefs drawn by code (12,000 specimens with planted faults), to Claude writing each scan trail and its hidden true path, to the nine-check validator (11,967 kept and frozen, $2.16). The model row runs from a three-part survival model fit on 24 runs (normal trip times by type, hour and priority; trips that run far longer but still arrive; specimens that never arrive), to testing on the 6 runs it never saw, to the demo. A dashed line shows the hidden truth going only to the test. Karissa says the data half and Daniel the model half, so the words stay even (584 to 572) and the speaker changes ten times; the talk runs 17:00. Gave the three questions on "How Tracer works" a picture each (silence past the usual arrival, the likely places sized by probability for the walkthrough specimen at minute 20, the time left before the window closes), drew the hand vote on the opening slide, gave "Meet Tracer" an icon per item and centred its Tracer label between the two lists, balanced the line breaks of every heading, and hid the controls and the pointer after 2 seconds without mouse movement. Checked every slide at 1920 by 1080 and 1280 by 800 in dark and in light.
- **Daniel:** asked for the Tracer label moved left, a visual check of every slide, more visuals so the class understands the system, and a slide on the training pipeline: the model, the data and how it was trained.
- **Went wrong:** on the first check the pipeline's step numbers ran into the longest title, and its "frozen dataset" label ran past the right margin; both moved. An independent Codex review (GPT-6.1 Sol) found four problems, all fixed: Daniel's line called the model's long-delay share "how often one stalls", but that share describes the tail of very long trips, not the stalls planted in the data (plan task M3); the slide did not name the model; "100 misses" could read as 100 specimens Tracer missed, when it caught all 100 late ones at the headline setting; and "No public data has these scans" said more than the search showed. The tighter caption first made the slide shrink at 1920 by 1080, because a narrower margin makes the figure taller, so the gap between its rows shrank instead. The opening slide's rail said the talk runs 17:00, which would have shown the room the overrun; it is blank there now.
- **Assumed:** the pipeline slide goes after the numbers slide, as how those numbers were made, because Karissa's debrief hands straight to Daniel's threshold slider. The place sizes on "How Tracer works" come from the walkthrough specimen's model output at minute 20 (unit 0.25, tube 0.15, station 0.52), drawn without numbers. If the instructor holds the team to 15:00, the pipeline slide is skipped and the questions get 1:30.

### Nov 8 · T2 · Add an explainer page and Karissa's lines (this pull request)

- **Agent:** added `docs/explained/index.html`, one self-contained page served at /Tracer/explained/: the problem, the four parts as a diagram with a plain explanation, an analogy and a line to say on stage for each, what each person built, the ten assumed policy rows, a replay of SP-r27-000368 from the demo bundle, a calculator that runs the risk formula on the fitted parameters in `results/interval_params.json`, a glossary, likely questions starting from the script's prepared answers, and the limits. It says plainly that Tracer is a small statistical model with a threshold rule, not a trained black box, and that Claude predicts nothing. Added `docs/talk/karissa-lines.md`, a copy of Karissa's lines, and a README link. The page's risk formula reproduces the bundle's stored risk for the walkthrough specimen within 0.00005, the bundle's rounding, and the page loads with no console errors at 1280 and 390 pixels wide.
- **Daniel:** asked for an explainer for the Nov 8 rehearsal that traces every number to the repo, made visual and interactive, published on Pages with Karissa's lines so she can open everything on her own laptop.
- **Went wrong:** the first phone capture looked clipped, but that was headless Chrome's minimum window width; at a true 390 pixels nothing overflowed, though chart labels shrank to about 8 pixels, so the hero labels moved out of the SVG and the two charts keep 600 pixels and scroll sideways on a phone. The page first called the model's long-delay share "stalled", the slip the Nov 6 review caught on the slide; it now says trips that run far longer but still arrive.
- **Assumed:** links on the page are absolute, so the same file works from Pages, from disk and from the shared Drive folder. `karissa-lines.md` is a copy that no test holds to `docs/talk/README.md`, so a later script change has to be copied by hand. The task tag is T2, the talk.

### Nov 8 · T2 · Address the review of the explainer page (this pull request)

- **Agent:** ran a two-reviewer review (a debugger and an auditor, both on Fable) of the explainer, Karissa's lines and the Windows guide, and fixed every finding. The risk lab's first alert now starts at minute 5, Tracer's first scoring tick. Prepared answer 5, the demo's design note and the page now call the model's long-delay share trips that run far longer, not stalls, and `script.html` is rebuilt. `tests/test_explained.py` holds the page's parameters, threshold, replay series and curve, and its headline figures, to their source files, and `tests/test_talk.py` holds `karissa-lines.md` to the script. The page takes the demo's colour grammar and the sibling pages' description and icon, drops three dead selectors, tells a screen reader only about changes of state, cites the held-out runs' file, and says the window-aware rule ties at every other volume tested. This log now lists the Sonnet jobs and the full spend. The Windows guide, which is not in the repo, no longer passes `-Force` to `Expand-Archive` and binds the local server to 127.0.0.1.
- **Daniel:** asked for a two-agent review and then a fix for every finding.
- **Went wrong:** the new figure test failed on its first run. The page said 29.6% of specimens were planted still out at 30 minutes, but the source value is 0.2955, which sits on a rounding boundary that float formatting rounds down; the page now quotes 29.55% against 29.97%. The recolour check found the risk lab's last axis label clipped ("180 mi"). Room-temperature body fluid gets 112 of its 150 alerts at minute 5: its starting risk of about 8% times weight 2 already passes the 0.137 threshold, so for that group the alert says little about silence. It is not changed here, since the weights and the budget are assumptions.
- **Assumed:** squash merge, as for the earlier pull requests. The log's spend total now counts J3 and J4, about $3.36 in all.

### Nov 8 · T2 · Add a short, card-by-card explainer (this pull request)

- **Agent:** added `docs/explained/short/index.html`, served at /Tracer/explained/short/: the explainer condensed to twelve cards for two people to step through together, one idea per card, each marked with whose part of the talk it is. It keeps the replay of SP-r27-000368, draws Karissa's 44 policy rows as 34 sourced and 10 assumed squares, and ends with tap-to-reveal practice questions. Its figures come from the full explainer, which `tests/test_explained.py` holds to the repo.
- **Daniel:** asked for a far shorter version to go through with Karissa, and for it to reach her through the Drive guide.
- **Went wrong:** headless captures showed the opening line undrawn and the progress rail grey; seeking the animation with the Web Animations API showed it ends fully drawn, so the captures were taken mid-animation. The line is now visible by default and only drawn in by the animation.
- **Assumed:** no drift test covers the short page; its figures are a subset of the full page's, so a change there has to be copied by hand.

### Nov 8 · N0 · Commit the neural-model spec, guard the new keys and verify OpenAI prices (this pull request)

- **Agent:** committed `docs/neural/spec.md`, the source of truth for the v2 scenario data and the TracerNet network, and started `docs/neural/build-log.md`, a running diary of the build. `.env.example` names `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY`. `tests/test_no_key_in_site.py` now matches OpenAI and OpenRouter key shapes, keeps OpenAI's and TypeSafe's API hosts and OpenRouter's API path out of `docs/`, and checks each key's value against every tracked file (10 more cases). `config/openai_prices.yaml` records the Standard, Batch and Flex rates of `gpt-6.1-terra`, `gpt-6.1-sol` and `gpt-6-luna` from OpenAI's pricing page, read twice independently and matching the spec. `config/scenario.example.json` is the spec's worked example, written first as the contract the parallel schema and simulator tasks build against. The plan gains tasks N0 to N11, N0 to N9 under Gate C; the plan check passes.
- **Daniel:** wrote the spec and the brief: hard OpenAI and decision-model caps, one pull request per task, hands-off with assumptions labeled, and HiPerGator available for training.
- **Went wrong:** nothing failed. A docs check found three differences from the spec, recorded for the client task: a one-off prompt is billed at the cache-write rate under OpenAI's default caching, Flex's capacity error has no machine-readable code, and exhausted credit arrives as `credit_balance_exhausted`.
- **Assumed:** disruptions with no line, route, unit or instrument target use the ids `lab_station` and `receiving`; a `mixed` unit's non-tube share goes by its courier route if it has one, else on foot; one shift clock from `receiving.shift_pattern` governs every shift-indexed field, and under `7-19` evening values are unused; N10 and N11 join no gate. torch has a wheel for the venv's Python 3.14, so it will go into `requirements.txt` and no Python 3.13 venv is needed.

### Nov 8 · Plan · Bring the published Nov 8 rework into the repository's plan (this pull request)

- **Agent:** the first republish of the plan after N0 was refused because the published page held work main did not: the November 8 rework section, tasks R1 to R7 (spec and build the network, then rebuild the 3D bench, demo and QR pages on it, decide on Beat Tracer, cut the talk), a rewritten API section and the freeze gate's new list. `visuals/plan.html` is now rebuilt from the published page exactly, with tasks N0 to N11 added after R7, N0 to N9 under Gate C, and one step on R2 pointing to its breakdown in N0 to N11. A diff against the published page shows only those three changes. The plan check passes with 67 tasks.
- **Daniel:** published the rework from another session on Nov 8; it had not been committed.
- **Went wrong:** N0 had been written on main's older plan, so publishing it as it stood would have erased the rework. The publish step's own check caught it, and nothing was overwritten.
- **Assumed:** the published page is the newer plan and wins over main's copy. R2 stays as the umbrella task and is done when N8 and N9 are. The plan's slack goes negative at Gates C and F because the N tasks add Daniel's review hours; the hours are the spec's and are left as they are.

### Nov 8 · N1 · Add the scenario schema, planner, prompt, validators and signatures (this pull request)

- **Agent:** a subagent (Claude Sonnet 5.5) built `src/scenarios/` in its own worktree from a self-contained brief; the parent session reviewed, tested and merged it. The full schema (`config/scenario.schema.json`) holds every bound of spec §4.3 and `schema.api_schema()` strips it to the keywords OpenAI's strict mode accepts (117 properties, depth 6, well inside the documented limits). `planner.plan` draws seeded briefs over stratum, archetype, size, transport, staffing and failure focus, each with plain `must_include` sentences paired with code predicates; the main run's out-of-distribution cells are 5.07% `multi_site_system` and 2.98% `scanner_downtime`, and the pilot and round 2 have none. `prompt.py` writes an 8,200-token developer message (field meanings, the simulator's use of each, bounds, the worked example with an instruction not to copy it) and the brief last. `validate.py` applies schema, semantics, names, plausibility (an injected check, wired to the simulator later), brief adherence and near duplicates in order, and returns the first failing layer's errors for a repair. `signature.py` computes the 32-number signature, near duplicates, families, coverage, dispersion and the collapse check. 402 tests; the full suite passes (1,011).
- **Daniel:** set the task through the spec; no review yet.
- **Went wrong:** v1's titled-name pattern matched "technologist. On" across a sentence break and would have rejected the worked example, so layer 3 uses a stricter pattern with the same intent. The developer message was checked for the 36 denylisted institution names before merging and holds none.
- **Assumed:** the factor shares not given in the spec (archetype, size, transport, staffing, focus per stratum) and the archetype and transport compatibility rules, all in `config/scenario_planner.yaml`; `unit_batching` is the one mild chronic focus a normal week may carry; staffing and transport predicates are ratios (for example, reduced nights means night staff at most half of day staff); the signature groups disruption counts into tube, courier and laboratory to stay at 32 numbers; the schema allows 0 staff in a shift the shift pattern does not use, and the semantic layer requires at least one in every shift it does; one developer message serves every job and model, so one cached prefix covers the whole run.

### Nov 8 · N2 · Add the OpenAI client, spend ledger, runner and pipeline command line (this pull request)

- **Agent:** a subagent (Claude Opus 5.5, at the highest effort because the code guards a hard cap on real money) built the client, ledger, price loader, runner and `python -m src.neural.pipeline` in its own worktree, with no network access; the parent session wired in the N1 modules, reviewed and merged it. The ledger (`data/openai/ledger.sqlite`, mirrored to `ledger.jsonl`) reserves each request's worst case inside one `BEGIN IMMEDIATE` transaction and refuses unless the account stays within $76.43 less the $2.43 margin and the job within its cap; a timeout keeps its full reservation as spent, a Flex capacity error is voided, and amounts are whole micro-dollars rounded up. The client sends the Responses API's strict JSON-schema format on the Flex tier with no temperature and never logs a key or header; the runner stores every raw response before parsing, never resends a cached one, gives one repair per brief, and moves an item to Batch after six Flex refusals. The O1 dry run builds the 72 real pilot requests and prices them at $2.03 expected and $5.05 worst case, sending nothing. 94 tests; to check them, the subagent broke 18 guards one at a time and the tests caught 17, and the 18th now has its own test.
- **Daniel:** set the caps and the order through the spec; no review yet.
- **Went wrong:** the runner read the validator's layer as a bare name, but N1 reports it as "6 diversity", so a near duplicate would have been sent for a repair it could never pass. The parent's wiring now names each layer for the runner, with a test. The job configuration first gave the pilot a total of 24 briefs, which the planner split by the main run's shares (5, 2, 8, 9 per stratum); the spec's pilot is 6 per stratum, and the configuration now leaves the count to the planner.
- **Assumed:** near duplicates (validation layer 6) are judged at freeze time in item-id order rather than as each response arrives, so which of two survives never depends on timing; plausibility comes from the simulator and generation refuses to run without it. The Standard tier is sent as `default`, as the docs name it. Cache writes are priced from the usage object's own count when it reports one. A pilot item id is `O1-<brief>.<arm>-<attempt>`. Backoff after a Flex refusal is 40 seconds doubling, about 20 minutes over six tries. Concurrency starts at 8 and rises to at most 32 while the token limit allows.

### Nov 8 · N3 · Add the laboratory simulator, v2 validator, plausibility checks and v1 world (this pull request)

- **Agent:** a subagent (Claude Opus 5.5) built `src/sim/` in its own worktree; the parent session ran an integration check, sent three fixes back, and merged the result. A heap-ordered discrete-event engine turns a scenario into specimen records in the v1 shape plus a `v2` block, with queues at the receiving desk, station emptying, courier rounds with capacity, staffing by shift and the eleven disruption types; each specimen's own random draws are tied to its arrival candidate, so adding or removing a disruption leaves every other specimen's draws unchanged. It runs at about 0.05 seconds per 1,000 specimens against a 2-second target. The v1 world passes v1's nine-check validator on every trail, matches every v1 leg (Kolmogorov-Smirnov p from 0.25 to 0.82 at 5,000 draws) and lands its breach share at 4.42%, inside the 99% interval around v1's 522 of 12,000. 93 tests; the full suite passes (1,198).
- **Daniel:** set the task through the spec; no review yet.
- **Went wrong:** the parent fed N1's code-random scenarios through the engine and found three problems. A unit with a dispatch median of 2 minutes and a 90th percentile of 170 left specimens at the unit past the 7,200-minute horizon, which produced invalid trails and dropped whole replicates; by the spec's definition such a specimen is never received, and it now ends open-ended with a `horizon_cut` flag. The plausibility check simulated two days and so never saw a disruption planned later in the week; it now simulates the full week (0.11 seconds). The KPI names did not match what the scenario signature reads, which would have counted them as zero; both sides are now held to one set of names by a test. After the fixes, 120 replicates of 60 random scenarios have no invalid trail.
- **Assumed:** the plausibility check covers the full scored week rather than the spec's two days; a cut specimen keeps only the faults that happened within its first 7,200 minutes; a carrier caught by a tube outage is stuck until it ends and then completes its transit; a courier round visits every unit on its route at once and takes the oldest specimens first up to capacity; the on-call runner has no capacity limit and never loses a specimen; a night shift takes the staffing of the day it starts on; scanner downtime defers scans to one scanner working through the backlog after the window. Each is listed in full in the pull request.

### Nov 8 · N4 · Run the OpenAI pilot (O1) and its gate (this pull request)

- **Agent:** ran the O1 dry run, then the pilot: the same 24 briefs (6 per stratum) to `gpt-6-luna` at low effort, `gpt-6.1-terra` at low and `gpt-6.1-sol` at medium, on the Flex tier through the ledger. Added `src/scenarios/gate.py` and `python -m src.neural.pipeline gate --job O1`, which scores G1 to G6 per model from the pilot's items and applies the model rule; `results/neural/generation/pilot.md` and `.json` hold the result. 69 of 72 accepted after at most one repair, $0.6003 spent of the $3.00 cap. All three models pass every gate; Terra writes every stratum of the main run, which the configuration now plans at 6,101 briefs by the spec's count formula. Tests for the model rule and for a repair request through the real planner and prompt.
- **Daniel:** set the pilot design and its gate through the spec; no review yet.
- **Went wrong:** the first run stopped after 45 seconds: the runner passes a repair's reasons as a list and the prompt expected a string, a path the test fakes accepted. The 12 requests in flight had all completed and their responses were stored before the stop, so the resume settled them at their real cost and sent nothing twice. G3 as first written ("adherent among accepted") is always 100%, because acceptance requires adherence; it now measures the share of first-attempt responses that passed layers 1 to 4 and met their brief. The edge rule first read overall adherence and chose Luna; it now reads the edge briefs, where Luna met 5 of 6.
- **Assumed:** G3's definition above, and the edge rule reading G3 on edge briefs only (6 per model, so one miss is 83%); G4 counts near duplicates within each model's own accepted set, with delta fitted on all 69 accepted pilot scenarios; the main run's count follows the formula, floor($44 x 0.95 / $0.00715 per accepted), giving 5,846 accepted, about 2.6 times the spec's planning figure, because the measured cost is half the estimate.

### Nov 8 · N2 · Address the independent review of the spend path (this pull request)

- **Agent:** before the $44 main run, the parent session sent the ledger, client, runner and pipeline to an independent read-only reviewer from another vendor (GPT-6.1 Sol at the highest effort, through NativePeers), which found four critical defects, six warnings and one note, each with a probe. A subagent (Claude Opus 5.5, highest effort) fixed all of them, each with a test that reproduced it first; a deliberate break of each fix was caught 21 times of 21. Every reservation is now at Standard rates, so a request billed at a tier other than the one requested is still covered (the reviewer's probe had taken O1 from $2.94 to $3.02 past its $3.00 cap). Input is bounded by the request's UTF-8 bytes plus framing, which a byte-level tokenizer cannot exceed, instead of a character estimate. One classifier decides every synchronous and Batch outcome: only outcomes proven unbilled are voided; a 5xx, a timeout, an uncertain Batch creation or a non-expired Batch error keeps its reservation and is never re-sent. A resumed reservation is topped up to the current worst case before it is used. Batch serves stored responses first and resumes cleanly after a crash. `generate` sends only the dry run's checked requests and their repairs. The token-counting option is removed. The spec's §4.5 to §4.7, §12.3, §13 and Appendix B now state these rules. 122 tests in the four spend-path files; the full suite passes (1,226).
- **Daniel:** asked for cross-vendor review of safety changes in his standing instructions; no review of this change yet.
- **Went wrong:** the original tests encoded their author's assumptions (that Flex is billed at Flex, that a 5xx is unbilled), which is why 94 tests and a mutation check had missed what the reviewer found in minutes.
- **Assumed:** a new stop rule ends a job when 5 of the last 10 synchronous answers are unusable, so an outage cannot use the cap up on charges that may never have happened; a synchronous error's usage is priced at Standard rates, since an error names no tier; other 4xx codes (402, 410) are settled as unknown and stop the job; pending items outside the checked request set stay pending. The worst case of a typical O2 request is $0.138 (46,464 input tokens by the byte bound plus 16,000 output, at Standard rates), about 4.4 times the pilot's billed input, so about $4.40 is held at 32 workers.

### Nov 8 · N2 · Close the re-review's findings on the spend path (this pull request)

- **Agent:** the reviewer (GPT-6.1 Sol, read-only) re-checked #55 while O2 ran: the four critical findings are closed and no path past a cap or to a duplicate send remains; it raised two new warnings and left one partly open. Fixed here, each with a test that fails without the fix: F012, the outage stop counted a billed refusal or truncated reply (HTTP 200) as a usable answer, so a run of them could not stop the job; it now counts the answer's own outcome, and a configuration that fails validation still counts as usable. F013, a crash between a Batch item's `reserved` and `in_flight` steps stranded its hold; every member reaches `in_flight` before the create call, so a member still `reserved` proves the chunk was never submitted, and `collect` now ends it as unsent and the requests go once. F005: the spec and the code now call the framing allowance (64 tokens per request, 16 per message) an assumption, with the measurement behind it; the byte part of the bound remains a guarantee. 1,230 tests pass.
- **Daniel:** no review yet.
- **Went wrong:** the re-review's own test run could not start in its read-only sandbox, so its dispositions rest on reading the code; the tests here were run locally.
- **Assumed:** a refusal, a truncated reply or a body that is not a configuration is an unusable answer; a configuration that fails a validation layer is a usable one.

### Nov 9 · N5 · Run the main scenario job (O2) and freeze the v2 library (this pull request)

- **Agent:** re-verified prices, ran O2 (6,101 briefs to `gpt-6.1-terra` at low effort, Flex, through the ledger): 6,023 accepted, $43.8659 of the $44.00 cap, 26 requests refused at the cap. Added `src/scenarios/freeze.py` and the `freeze` and `simulate` commands, and froze 6,071 scenarios into `datasets/scenarios-v2.jsonl.gz` with its manifest and splits (train 3,668, validation 772, test 782, out-of-distribution test 849; 5,396 families). Moved the pilot gate's and the simulation's simulator calls out of `src/neural/` so the isolation test needs no exemption, made a long stage record its real finish time, and recorded a non-binding secondary analysis before any real result.
- **Daniel:** no review yet.
- **Went wrong:** the first freeze re-validated every scenario on one core and was stopped before it wrote anything; it now uses worker processes (3.4 minutes). A progress estimate told Daniel finishing every request would cost $6.70 more; it had counted repairs already sent as unsent, and the real figure was 26 requests. Stage markers stamped a long stage's start and finish with one time. OpenAI's usage page trailed the ledger by $1.20 just after the run, with the same lag in tokens; twenty minutes later it read $44.98 against the ledger's $44.47, within limits.
- **Assumed:** the near-duplicate distance is refit on the library itself (half its median nearest-neighbor distance, 1.495), because the pilot's distance would have flagged about half of O2 and chained the rest into one family; families are union-find components at 1.5 times that distance; splits are 70/15/15 by a salted hash within (archetype, stratum) cells; the count follows the spec's formula, about 2.6 times its planning figure because measured cost was half the estimate; `test_ood` results will also be reported on the 482 scenarios that carry the out-of-distribution feature, since 367 of its 849 travel with their families.

### Nov 9 · N6 · Add store v2, the features, the baselines and the evaluation harness (this pull request)

- **Agent:** two agents built store v2 (observed and truth in separate files), the observed-only feature builder, TracerNet's network, bins, losses and calibration, the training samplers, the v2 baselines and the evaluation driver; the parent integrated them, added the `store` and `evaluate` commands and TracerNet's scoring step, rewrote two slow layers so CPU scoring is about 13 times faster, built the real store (48.9 million specimens), and scored the baselines on validation. GPT-6.1 Sol reviewed the evaluation's integrity (four critical findings, four warnings); an Opus agent fixed all eight with tests that fail on the old code, and Sol re-reviewed the fixes.
- **Daniel:** no review yet.
- **Went wrong:** the first integrity review found that TracerNet could score a test set before the lock, that the lock could be satisfied by different inputs, that manifests were trusted unchecked, and that tuning could exceed its alert budget; none had run on real data, and the re-review's five narrower warnings were fixed the same way. The first real validation run found a local refit whose risk was not a number (now a logged fallback to the global fit). Running the evaluation and a training benchmark on the Mac exhausted its memory and it had to be restarted; heavy work moved to HiPerGator, where one evaluation ran out of memory at 160 GB before a 256 GB rerun finished. Four exact-regeneration tests fail on HiPerGator's processors (three of them on main as well) and pass on the Mac.
- **Assumed:** a knob that cannot meet the budget alerts on nothing (spec §8.3 read strictly), and the count of such scenarios is reported per method and budget (261 of 772 validation scenarios at 6 alerts per shift for the window rule and the equal weights); a local refit whose risk is not a number counts as a failed refit; `legacy_v1` keeps `evaluate.py`'s knob for exact parity; risks within 1e-5 of [0, 1] are clipped, anything further refused; the predictor ships with this task as the harness's scoring interface, and its training and ensemble code with N7.

### Nov 9 · N7 · Train, search and select TracerNet, and build artifact v1 (this pull request)

- **Agent:** agents wrote the training loop, the search, rung selection, the ensemble and calibration; the parent integrated them and ran everything on HiPerGator: the speed benchmarks, a 24-trial search on a B200, the four-rung ladder on B200 GPUs and burst CPU nodes, selection with its warning check, artifact v1 and the validation evaluation. The parent fixed the epoch planning (4c44858) and the evaluation's risk reads, worker threads and progress reporting (42caffc). GPT-6.1 Sol reviewed 42caffc and found nothing that changes a score, but one warning (a lookup holding only specimens received before their first tick raised an error instead of returning empty series) and one suggestion (a failed batch counted toward progress); both are fixed, the first with a test that fails on the old code. Before any test result, the parent also fixed two non-binding analyses for a possible paper in spec §8.11 (d99d010).
- **Daniel:** asked for a live view of running jobs in the local monitor and whether the work could support a paper; no review of this pull request yet.
- **Went wrong:** the start-up speed check underestimated feature throughput about twentyfold, so the ladder's epochs ran 31 seconds instead of 10 minutes (fixed for the final ensemble). The search's first launch ran out of memory. The first selection run re-read a 484 MB risk table for every scenario group with about 30 workers fighting over 64-thread pools, then hung after memory kills; the second started all 63 workers at once and crossed 450 GB; the artifact's first try ran calibration on one core. Each was found in the job's own readings, cancelled or resubmitted, and fixed in code or the job script.
- **Assumed:** the ladder stands with short epochs because all four rungs trained under the same rule; the warning check passes when both medians are 0 (C is not more than 5 minutes below A, read literally); artifact v1 is a one-member, provisional artifact that N8 replaces; ablation A8 is cut, since it would train TracerNet on the Claude-written v1 trails and Anthropic's Usage Policy forbids training on outputs without prior authorization; `results/neural/runs.csv` leaves out the four speed benchmarks.

### Nov 10 · N8 · Run round two, train the final ensemble, score the test sets under the lock, and run the ablations (this pull request)

- **Agent:** the parent ran round two (O3 on terra-low, $17.71 of $18.00; the freeze to 8,142 scenarios), rebuilt store v2 on HiPerGator, trained the three-seed rung C ensemble on B200 GPUs, calibrated it, scored validation in 11 CPU shards, took the test lock and scored `test`, `test_ood`, `legacy_v1` and `control_v1world`, and ran and collected ablations A1, A2, A5 and A6. GPT-6.1 Sol reviewed every change to the lock and the scoring path before the test look (three rounds: three critical and two warning findings, all fixed with regressions); a Sonnet agent drafted the build-log entries from the progress notes and the parent checked them against the result files.
- **Daniel:** asked whether the results looked promising and for an orientation message for a Grok bot; no review of this pull request yet.
- **Went wrong:** the lock hashed `test_ood`'s reported subset into the configuration both test sets share, so the second set would have needed a relock (found and fixed before any look); Sol then found that a stale test run survived a subset-only relock, that lock writes were not serialized across nodes, and that a sampled risk table was not bound to its run (all fixed before the look). The GPU groups were full for hours; the training schedule was unstable at the searched learning rate; a member trained on an AMD node could not join GPU-trained members (feature tables differ in the last bit by processor); the first `test` look ran out of memory after scoring and its evaluation was rerun on the recorded risk table; most ablation arms ran out of memory until each evaluation worker was given about 11 GB.
- **Assumed:** each member keeps its best validation epoch despite the unstable schedule (spec §7.7 as written); ablation arms are scored on one fixed salted 25% of validation and test scenarios, the same for every arm, and their compute (73.7 hours) is reported over the 6-hour box; CPU inference is bit-reproducible per processor type, so every evaluation ran on AMD Rome nodes; the control store holds the v1-world scenario in its own store's `val` split; `legacy_v1`'s TracerNet risk comes from `Predictor.score` on the v1 store because the risk-table builder reads only v2 stores; the negative control's leakage flag is reported as the rule states it, with the global-fit comparison beside it; A3 and A4 fall to the time box and A8 is cut by Anthropic's Usage Policy.

### Nov 10 · N9 · Explain TracerNet's alerts, add the predictor, the commands and the demo export, and check the explanations on every test alert (this pull request)

- **Agent:** Opus subagents built the explanations, the predictor, the export and the schemas (Nov 9), then the `explain` and `export` commands, the README section and the demo file (Nov 10), from self-contained briefs; the parent ran every test-set step on HiPerGator as a logged repeat of the locked look, and reviewed and committed each change. GPT-6.1 Sol reviewed the explanation and export code (one critical and three warning findings) and the `explain` command (three warnings); all were fixed with regressions and verified.
- **Daniel:** no review of this pull request yet.
- **Went wrong:** the export read v2 specimens and truth labels before the lock check (found by review before any run); the first explanation smoke run failed because the stored risk table lacks each alert's tier and window; the full run neared its 250 GB and moved to a 450 GB node, so the run log holds five repeats from this work; the deletion check fails over every test alert (68.2% against 70%).
- **Assumed:** integrated-gradient steps double past the spec's 64 to 128, up to 1,024, until the median alert is complete within 1% (256 on test); closed-window alerts carry one window factor with no attribution and are left out of the checks; each test alert's tier and window come from the observed store, never the truth; the demo's explanations come from `legacy_v1`; `legacy_v1`'s and `control_v1world`'s risk tables are built in the pipeline's `evaluate` dispatch, since `neural_eval.py` must not change while the lock records its blob.

### Nov 10 · N10 · Add the decision layer and run X1 to X3 (this pull request)

- **Agent:** an Opus subagent built `src/decide/`, `src/pipeline/dispatch_eval.py`, the `decide` command and report section 8 in its own worktree from self-contained briefs, and fixed each review round; the parent checked the prices, ran every spending step on the Mac through the ledger and every data-reading step on HiPerGator, chose the prompt round on validation, took the decision layer's single test look and wrote the results. GPT-6.1 Sol reviewed the spend path, key handling, isolation and the lock in five rounds, then the final commits in a sixth (fifteen findings, none critical, all fixed; each code finding with a regression shown to fail on its parent).
- **Daniel:** accepted editor access to the plan page, whose tick database stays closed to Claude Code across organizations, and waived the ledger-versus-usage-page check; no review of this pull request yet.
- **Went wrong:** the first fix for test-store reads left the risk table hashed outside the mutex and added a `table=` path that skipped the predictor's lock check, so two more rounds followed. The whole-scenario sample drew 14 laboratories and whole runs drew 43 before alerts were capped per run (two discarded dry runs, about 46 minutes of cluster time, no spend), and commit `42ec5e1` claimed whole runs spread the sample over hundreds of scenarios. Round two's wording moved Drex Max off the receiving-queue action even for specimens truly there.
- **Assumed:** X1 labels the whole library (8,142 scenarios, not the planned 3,000) because the checked price fitted its cap; X2 samples runs in a seeded order with at most 10 alerts of each; test uses the prompt round with the higher validation role accuracy; X2 does not run on `legacy_v1` or the demo set, whose v1 store records no route, unit kind or roster, so the demo carries no dispatch; X4 is not built (cut list item 2, and the adoption rule failed); the reservation's framing allowance holds because the pilot measured billed input at most a third of the byte bound; the counterfactual times are `source: assumed`.

### Nov 11 · N11 · Close out the neural work: the acceptance audit, the single command and the final checks (this pull request)

- **Agent:** two Opus subagents worked in the N11 worktree from self-contained briefs, one building `pipeline run --from-frozen` and one drafting the §17 audit; the parent ran the key scans with the real `.env`, the rebuild and both modes of the command on HiPerGator, corrected the spec text and the teaming log, decided the audit's open verdicts, and wrote the appendix's final form.
- **Daniel:** accepted editor access to the plan page, whose tick database stays closed to Claude Code across organizations, and asked whether HiPerGator needed a new sign-in (it did not); no review of this pull request yet.
- **Went wrong:** the spec described a 2-day plausibility simulation and calibration over every validation tick, both unlike the code, and the teaming log lacked O3's row (all fixed here); the GPU retrain waited about four hours on both groups' limits; a first plan to run the `--evaluate-committed` mode at 63 workers would have risked running out of memory in evaluation, so it ran at 47; the explanation checks still fail on deletion.
- **Assumed:** item 18 is read on validation, with the three fields every rerun changes (store path, store manifest hash, timing) carried over before the byte comparison, since the test sets cannot be rescored after the decision layer's relock; the gated metrics (the ensemble's `val_sel`, pooled AUROC, AUPRC and Brier at 30 and 60 minutes) were fixed before the retrain's result; the committed result files write the cluster's folder prefix as `$T`; items 2, 3 and 11 stay partly held rather than editing the frozen manifest or rescoring a test set.

### Nov 11 · Report review · Correct the report's wording and labels after an independent review (this pull request)

- **Agent:** applied the review's thirteen fixes in the report's generator (`src/neural/report.py`) and its written inputs (`results/neural/acceptance.md`, `limits.md`, a new `caveats.md`, and the A3 and A4 labels in `ablations.json` and `src/neural/ablations.py`), with five new report tests, then re-rendered `REPORT.md` from the committed results with the local ledger mirror: no rerun, no new result and no test-set access. It brought the README's verdict and test-lock lines in line, and a Sonnet subagent repaired the local build monitor, which had frozen on N7 (outside the repository).
- **Daniel:** sent the review's findings as a list of fixes with the cleanup to do and the files to leave alone; no review of this pull request yet.
- **Went wrong:** the report said TracerNet "ties overall" though it leads on capped mean warning, AUROC and Brier; it showed O1's acceptance as 287.5% (69 accepted over 24 briefs, each sent to three models), printed the lock's two times in the wrong order, and called appendix items 14 and 18 held when each held only in part. Its first paragraph never said why every R1 median was 0, and its R2 line gave only the equal-weights deficit.
- **Assumed:** the review's 6.3 breaches per shift is 6.4 from the committed counts (316,182 breaches in 49,266 shifts); moving items 14 and 18 to partly held gives 14 held, 1 failed and 6 partly held, not the 15 and 5 the review named; the "Read with the verdict" list is a written input like `limits.md`, since the val_sel spikes are recorded only in the training logs; the "not run" lines for §8.11's sensitivity sets and face-validity rating close report section 5, where §8.10 item 5 puts them.

### Nov 11 · Report review · Close a second review's text fixes and clean the repository (this pull request)

- **Agent:** made six text-only fixes from a second independent review. It added a correction line to the build log's 6:06 PM entry for the 1:18 AM entry's "not from leakage", and restored spec §17's own wording for appendix item 12's criterion, with the hash lock in its verdict. It labeled item 20's evidence by date and refreshed its counts through #64, and named the local ledger mirror wherever the report's byte-for-byte rendering is claimed (report section 11, the build log, this log and #64's description). It wrote "R1's three baselines at 3-2-1 weights" in the opening paragraph and the README, and added a test that ties `caveats.md` to its committed sources, shown to fail on an altered copy. It then stopped the stalled monitor subagent and removed the last branches.
- **Daniel:** sent the second review's findings and the cleanup to a clean working tree; no review of this pull request yet.
- **Went wrong:** the first revision paraphrased item 12's criterion instead of quoting the spec, left item 20's November 10 counts unlabeled, said no check was rerun while item 21 read PR metadata, and said the page renders from the committed results alone although section 9's spend table reads the local ledger mirror. The monitor subagent sat for 45 minutes on an `ls` that this shell runs as `eza`.
- **Assumed:** the ledger mirror stays local, since `data/` is never committed, so the reproduction claims name it instead; the test checks the leakage line of `caveats.md` as well as the three lines the review named; the val_sel spike line stays unchecked, having no committed source.

### Nov 11 · Report review · Name the commit range behind each count in appendix item 20 (this pull request)

- **Agent:** made the one optional text fix a third independent review found (no critical issues or warnings): item 20's two "16 commits" bullets now name their ranges, checked with `git rev-list` (#49 to #62 plus two N11 branch commits at `ac94969`; #49 to #64 at `d087870`). It re-rendered the report and recorded the closed neural work in the local `AGENTS.md`.
- **Daniel:** sent the review's result and the close-out list; no review of this pull request yet.
- **Went wrong:** the second revision's new bullet repeated "16 commits" beside the November 10 one without naming either range, so the same count read as the same commits.
- **Assumed:** the November 10 range is read from `ac94969`, the commit the audit names, which still exists in this repository.

### Nov 13 · Rework · A self-playing 3D demo of three specimens (#67)

- **Agent:** built `docs/demo/play/`, a plain three.js page that plays on open and loops through three held-out specimens (on time, stalled at the lab's tube station, lost in the tube), with one caption at a time, a minutes-since-it-left clock, a timeline with the deadline and each method's alert, both models' chance of a miss, and halos for where Tracer thinks the specimen is. Its data file comes from the committed `bundle.json` and `neural.json` through `python -m src.api.autoplay`; nothing is recomputed. three.js 0.186.1 is vendored so the page makes no outside request. Tests tie the data file to its sources and check the captions under node.
- **Daniel:** asked for the 3D demo to play by itself, be easy for a first-time viewer and look good, and not to use the exhibit-3d skill, after the November 8 session with Karissa; no review of this pull request yet.
- **Went wrong:** no browser could run in the build session, so the page was not seen rendered before merging; it was checked by syntax, by loading every vendored module under node and by the story tests.
- **Assumed:** the three specimens are picked to show the three situations, not to favor a method (in the stalled one TracerNet alerts last); the fixed window rule is called "Fixed timer" on screen; alerts are the headline budget of 6 per 8-hour shift at 3-2-1 weights.

### Nov 13 · Rework · The self-playing demo moves onto the original bench (#68 to #70)

- **Agent:** rebuilt `docs/demo/play/` on the Silent Specimen bench's own scene (`visuals/exhibit-3d`, new `play/index.html` and `src/play.ts`, built with `npm run build:play`): the same lab, glass tube, lighting, glow and console, with every control, the lesson and the loupe taken out. Four specimens play in turn (on time, stalled, lost, never sent) on the bench's scripted fates; the clock slows near each moment that matters and holds under one caption; a card names each specimen and gives its verdict. Every number comes from the bench's own model at that minute. PR 67's plain page, its data script and its tests are removed.
- **Daniel:** said the original bench was beautiful and only too demanding to use, and asked for the self-playing version to keep its look.
- **Went wrong:** PR 67's page was built from scratch with a plainer look; the cloud browser has no WebGL, so neither page could be seen rendered there.
- **Assumed:** the demo shows the analytic Tracer model the bench was built on; TracerNet's numbers stay in the deck.

### Nov 13 · Rework · A shorter phone page, and the 3D caption off the dials (this pull request)

- **Agent:** cut the phone demo page to one line per idea: a one-line tagline and a "synthetic data, no clinical review" badge at the top, the class activity as three one-line steps and two short questions, one-line ledes for the board and the threshold, three short rules for Beat Tracer. The column key, the game's fine print, and the honesty note, the design reasons and the limits now open from folds, word for word, so nothing true was dropped. Karissa's problem paragraph stays on the page as a hidden slot until she writes it. On wide screens the self-playing 3D demo now puts its caption in a column under the risk card, with the bench framed to its left, so the caption no longer covers the console's dials.
- **Daniel:** asked for front-end pages that are short ("nobody wants to read a wall of text"); the caption over the dials was a weak spot found when checking the demo in his Chrome.
- **Went wrong:** nothing yet; the browser checks in the test suite skip here because Playwright isn't installed, so the page is checked in Daniel's Chrome after merging.
- **Assumed:** Beat Tracer stays, as the class's hands-on activity; whether to cut it for time is Daniel's call with the deck.

## The Claude API jobs

The dataset jobs (D4 to J2) call `claude-haiku-5-5` at low effort with thinking off. The demo's specimen stories (J3) and search briefs (J4) call `claude-sonnet-5-5`, the model for text a person reads. The reply must match a JSON schema, so replies parse and retries stay rare. Bulk jobs run through the Batch API at half price, and every call is logged to `data/claude/usage.jsonl` (kept local). The order for each job is a token count, a pilot, then the batch, and a job's estimate must be under the plan's gate before it starts.

| Job | What it wrote | Requests | Cost | Result |
|---|---|---|---|---|
| D4 check | Two calls to check that the cached prefix is read | 2 | $0.0012 | The second call read 3,953 cached tokens |
| J1 pilot (seed 0) | Event trails for 200 specimens | 204 | $0.0728 | 198 accepted, 2 dropped by the validator |
| J1b reruns | The pilot again after the note fix, and 190 specimens with every fault | 400 | $0.1350 | 199 of 200 and 188 of 190 accepted |
| J2 smoke | 10 specimens through the Batch API | 10 | $0.0019 | All 10 accepted |
| J2 full | 12,000 specimens in 30 seeded runs of 400 | 12,077 | $2.1612 | 11,967 accepted, 33 dropped |
| J3 stories | Stories for the demo's 10 showcase specimens (`docs/demo/heroes.json`) | 10, plus 1 retry | $0.0518 | 10 stories |
| J4 search briefs | A search brief for each of the 806 alerts (`docs/demo/briefs.json`) | 806, plus 2 retries | $0.9317 | 804 on the first try, 2 after a retry, none fell back to the template |

Total about $3.36 of the $50 soft limit ($2.37 for the dataset, $0.98 for the demo's text). The manifest (`datasets/manifest.json`) records the model, the prompt hash, the seed, the counts and the cost of the full job.

**What each job wrote.** For each brief the seeded code had already fixed the specimen, its planted fault and every timing. The model wrote the observed scan events and the hidden true path for that brief, and nothing else. Code owns the statistics, since language-model timings have thin tails and cannot be re-drawn.

**What the validator caught.**

- In the first pilot it rejected 2 of 200: a time-arithmetic slip and a trail with a third note.
- In the full job, 33 specimens failed twice and were dropped. A specimen can fail more than one check: check 5 (segment lengths) 28 times, check 7 (receipt time) 15, check 4 (truth segments) 3, check 1 (schema) once. 99.4% were accepted on the first attempt and 99.7% after one retry. The drops lean toward long delays (5.9% of specimens with a stage of 120 minutes or more, against 0.0% under 60), which the manifest reports.
- It did not catch notes that gave away the hidden path in the first pilot (all 6 stalled and misrouted trails). A person reading the sample found that, and check 8 was added.

**Not done.** A clinical review of twenty pilot timelines (K2) was skipped at Daniel's request, and the manifest says so. Any result built on the dataset says that it is synthetic and had no clinical review.

## The OpenAI API jobs

The v2 scenario jobs (docs/neural/spec.md §4) call OpenAI's Responses API with Structured Outputs on the Flex tier, through a ledger (`data/openai/ledger.sqlite`, kept local) that reserves every request's worst case before it is sent: $76.43 in all, of which $2.43 is never reserved, with a cap per job. Prices are checked on OpenAI's pricing page into `config/openai_prices.yaml` before each spending stage.

| Job | What it wrote | Requests | Cost | Result |
|---|---|---|---|---|
| O1 pilot | One laboratory week for each of 24 briefs, on each of three models | 79 (72 plus 7 repairs) | $0.6003 of $3.00 | 69 accepted, 3 dropped; gate passed, Terra chosen for every stratum |
| O2 main run | One laboratory week for each of 6,101 briefs, `gpt-6.1-terra` at low effort | 7,038 (6,100 plus 938 repairs) | $43.8659 of $44.00 | 6,023 accepted (98.7% of briefs), 52 dropped, 26 left unsent at the cap |
| O3 round two | One laboratory week for each of 2,378 briefs aimed at validation's weak spots, `gpt-6.1-terra` at low effort | 2,733 (2,378 plus 355 repairs) | $17.7077 of $18.00 | 2,367 accepted (99.5% of briefs), 11 dropped; the library froze at 8,142 scenarios |


## The decision-model jobs

Jobs X1 to X3 (`docs/neural/spec.md` §11) call OpenRouter's Decisions API with `nace-ai/drex-max` (resolved `nace-ai/drex-max-20261016`), zero-shot, through the same ledger's `decide` account ($8.00, separate from OpenAI's), each request reserving its worst case at the undiscounted price. Prices were checked on the model pages on Nov 10.

| Job | What it decided | Requests | Cost | Result |
|---|---|---|---|---|
| X1 | Plausibility, dominant failure and edge tags for 8,142 library scenarios and 300 code-made controls | 8,443 (a 320-call pilot, then 8,123) | $0.6961 of $1.00 | Control AUROC 0.980; 726 scenarios to a review list, none deleted |
| X2 | Where to look, what to do and whom to call per alert: validation rounds one and two, then test | 30,044 (5,001, 5,006 and 20,037) | $2.1933 of $4.50 | Adoption rule fails on test (role accuracy 61.2% against R2's 69.1%); R2 dispatches |
| X3 | Whether a specimen misses its window, without TracerNet's output | 4,003 | $0.1003 of $0.75 | AUROC 0.669 against TracerNet's 0.763 |
| X4 | Not built (cut list item 2) | 0 | $0 of $1.75 | Not needed once the adoption rule failed |

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
