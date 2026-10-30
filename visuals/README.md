# visuals/

`architecture.html` is the interactive system map: the specimen journey, the intelligence layer, and one
worked example. It opens from disk with no server and no build step. It is also the seed of the Interactive
Demo due Week 15, which the course grades partly on **using the team's real AI prototype in appropriate
context**. The gaps below are the distance between what the page shows today and that standard. They are
tracked here so they get closed rather than merely known.

## What is real

Computed from the architecture, not drawn:

- **The risk curve.** The mixture-survival model of `docs/architecture.md` §5.2, evaluated at each minute:
  a normal completion component, a stalled component, and a defective mass that never completes.
- **The location posterior.** A genuine hidden semi-Markov forward filter over four transit states with
  explicit durations and an absorbing lost state, conditioned on no receipt scan having arrived (§5.3).
- **The alert moment.** The first five-minute tick at which the risk crosses the operating point — the same
  rule §5.2.1 gives triage, not a chosen timestamp.
- **The lead time** shown on the alert card, derived from that crossing.

## What is illustrative, and when it closes

| # | Gap | Closes |
|---|---|---|
| 1 | **Every parameter is invented.** Transit medians, the 120-minute window, the 0.45 operating point, the 4% loss fraction. None are measured, and the windows are unvalidated pending Karissa's policy table. | Week 6 for fitted intervals; Week 7 for the operating point; the policy table whenever it lands |
| 2 | **The ending is scripted.** Receipt at 03:22 and plating at 03:41 are authored events. Nothing decided the specimen would be found — the recovery is narrative, not simulation output. | Week 12, when scenario bundles come from real runs |
| 3 | **It runs the analytic form, not a fitted model.** The page implements the mixture-survival equation in JavaScript with hand-set parameters. The prototype fits those parameters from an event log and the page does not. | Week 12, by loading `interval_params.json` from a real fit |
| 4 | **Dwell distributions are hardcoded**, not fitted, and the specimen ID, unit and type are made up. | Week 12, with the scenario bundle |
| 5 | **Build statuses are hand-maintained strings** in the page source, not read from anything. | Manual until the end; check before each demo, and republish the link below |
| 6 | **The clock stops at plating on the page.** The policy table stops every clock at receipt (`docs/architecture.md` §10), and its unpreserved urine window is 60 minutes, not 120. The page's setup step after receipt and its "Plated, clock stops" label are out of date. | Week 12, with the scenario bundle (plan tasks P2 and P3) |

## How they close

Week 12–13 replaces the hardcoded `PARAM` block with a **scenario bundle** — the JSON that
`GET /api/scenario/{id}` already returns (`docs/architecture.md` §7), exported from a real pipeline run and
committed as a static file. The page keeps its own rendering and its own interaction; only the numbers stop
being invented. Nothing about the layout has to change, which is why the bundle and the live endpoint share
a schema.

Until that lands, the page carries a visible notice saying so, and the notice comes off only when the list
above is empty.

## The published copy

`architecture.html` is also published as a private Claude artifact, because Google Drive serves an
`.html` file as source code rather than running it and a viewer needs a host that executes JavaScript.

That copy is derived, not canonical. This file is the one to edit; the published page is a snapshot of it
with the `<!DOCTYPE>`/`<head>`/`<body>` wrapper stripped (the host supplies its own), the `<title>`
shortened to `Tracer`, and `color-scheme: dark` added.

**It does not update itself.** Whenever the page changes — and it changes at least every time a build status
in the table above moves from *not started* to *in progress* — the link needs an explicit republish to that
same URL. Keep the URL: republishing without it creates a second artifact rather than updating this one.

This is a stopgap. The Week 15 demo has to be reachable by every student from the project's GitHub
repository, so the durable home for this page is GitHub Pages, and the published link retires when that
exists.
