# EXHIBIT: The Silent Specimen

Kicker / Title: Tracer / The Silent Specimen
Lede: A specimen left the ward and no scan has come back. Run the clock, hide the truth, and watch Tracer judge how late is too late and where it is now.
Question: How long can a specimen stay silent before the silence itself is the alarm?
Set sentence: A pneumatic tube bench in a laboratory at dusk: a ward station, a glass tube run, a diverter over a receiver, a lost bin, a receiving scanner and a console, with Tracer's belief about a missing specimen glowing in the glass.
Loading line: Pressurizing the tube…
Favicon: an amber tube with a cyan glow pooled at its far end, on the navy tile (`public/favicon.svg`).

## Phenomenon and model
Hero (what glows): Tracer's inference drawn as light, cyan along the route and coral in the lost bin. The location posterior fills four glass vessels with light to a level equal to its probability (the ward's send sleeve, the station's receiver, the lost bin, and the lab's receiving tray after the receipt scan), and a smear of light inside the glass tube shows the share still in transit, spread by minutes spent in the tube. The light is conserved: it drains from vessel to vessel as silence continues. The alert beacon (an amber strip) lights when the risk crosses the alert line. The carrier (the truth) is a lit object, never an emitter. Practical emitters: the console strip, the scanner's scan line, the beacon.

Model: a port of the working model in `visuals/architecture.html` (architecture.md §5.2 and §5.3), every parameter an assumption (`source: "assumed"`).
- Risk while silent, mixture survival with a defective mass. The clock stops at the receipt scan, so the window is missed exactly when the scan has not come by W: `riskUnreceived(t) = notByWindow(t) / notReceived(t)`, and 1 from W on, with `notReceived = p + Σ wₖ Sₖ(t)` and `notByWindow = p + Σ wₖ Sₖ(W)`. Transit components: normal w 0.88, lognormal median 24 min, σ 0.50; stalled w 0.08, median 70 min, σ 0.60; never arrives p 0.04. Window W 120 min by default, the policy table's window for lavage and tissue at room temperature (the system map's own example is an unpreserved urine, whose policy window is 60 min).
- Risk after the receipt scan: the clock has stopped and the outcome is known, 0 if the scan came inside the window and 1 if not (`riskAfterReceipt(receivedAt, W)`). Nothing after the receipt scan, plating included, enters the model. The window can shrink below the scan minute, so a late scan reads as a miss.
- Location while silent: forward filter on a one-minute grid over four sub-legs with lognormal durations and exits, ages capped at 180 min, conditioned on no receipt scan: awaiting dispatch (median 6, σ 0.60; to tube 0.992, lost 0.008), in the tube (4, 0.25; to station 0.995, lost 0.005), at the station (11, 0.70; received 0.910, stalled 0.070, lost 0.020), stalled at the station (45, 0.80; received 0.850, lost 0.150). After the receipt scan the place is known: the lab, 100%.
- Alert rule: the first 5-minute tick at or after minute 0, and not after the receipt scan, at which the risk is at least the alert line (operating point, default 0.45).
- Readouts are evaluated at whole minutes (the time display rounds); the glow interpolates between minutes.
- The mixture's posterior weights while silent, `wₖ Sₖ(t) / notReceived` and `p / notReceived` (normal, late, never arrive), drive the loupe. They are the trip model's shares, not the location model's: at 60 min the loupe shows 34 of 100 never arriving while the lost bin holds 36%.
- Time: minutes since the collection scan. Playback runs at 3 min/s (1×) or 12 min/s (4×), labeled on the card.
- Override: `window.TRACER_PARAMS` or `?params=<same-origin JSON url>` replaces any parameter; invalid overrides are ignored.

Invariants for tests:
- Parity: the port agrees with the original functions, extracted from `visuals/architecture.html` and run under node, to 1e-9: the risk function at every minute 0 to 130 and at windows 60, 90, 150 and 240; the scenario series (the model while silent, then the known outcome after the receipt scan) over the page's 0 to 90 minutes; every leg and lost of the location posterior at every minute 0 to 130; and the alert minute across alert lines and windows. The original is told this exhibit's window (120 min) and receipt (68 min), since the page's own example is a 60 min window with a scan at 52 min.
- Risk stays in [0, 1] for every window 60 to 240 and minute 0 to 130.
- The location posterior (four legs plus lost) sums to 1 at every minute, within 1e-12.
- P(lost) never decreases with silence.
- Lowering the alert line never moves the alert minute later; shrinking the window never moves it later.
- Lead time equals the window minus the alert minute; the default parameters alert at 60 min with 60 min of lead.
- Known values: risk 0.055 at minute 0, 0.41 at 55 and 0.47 at 60; at 55 the station holds 0.68 and lost 0.31 (architecture.md §3, which the clock does not change); the receipt scan at 68 min stops the clock: the outcome is 0 inside a 120 min window and 1 inside a 60 min one.
- Risk never falls while the silence lasts, at any window (the numerator of the ratio is fixed and its denominator only shrinks), and a passed window is certain.
- A start from address parameters gives the same readouts as reaching that state through the controls.

## Parameters → controls
| Parameter | Range, default, unit | HUD control | Scene control | Console control | Keys |
|---|---|---|---|---|---|
| Silence so far | 0 to 130, 62, min | time group: play, scrubber (ticks 0, 30, 60, 90, 120; amber marker at the alert minute), speeds 3 min/s and 12 min/s | drag the stopwatch hand | drag the marker along the risk chart | Space play, `,` `.` 1 min (Shift 5), Backspace back to 0 |
| Alert line (operating point) | 0.10 to 0.90, 0.45 | slider in percent, ticks 25, 45, 75 | drag the dashed alert line on the risk chart | the ALERT LINE knob | `[` `]` 1 point (Shift 5), `0` back to 45% |
| Stability window | 60 to 240, 120, min | slider, ticks 60, 120, 180, 240 | drag the coral marker on the stopwatch bezel | the WINDOW knob | `-` `=` 5 min (Shift 15) |
| Truth | Shown / Hidden, Shown | segmented | click the brass truth switch on the bench, in front of the lost bin | — (the switch sits on the bench, where the first view sees it) | T |
| View | Lab / Exploded / Studio | segmented | — | — | Shift+V, Shift+X |

## TRY presets
Each preset picks a scripted true fate (the model never sees it) and moves the clock to the moment that shows its idea.
| # | Name | State | Demonstrates | Caption text |
|---|---|---|---|---|
| 1 | On time | fate arrives (receipt 24 min), silence 26 | the scan stops the clock | "The receipt scan at 24 min stopped the clock inside the 120 min window: the risk fell from 9.9% to 0.0%." |
| 2 | Stalls | fate stalls (stalled at the station, receipt 68), silence 62 | the alert buys time: architecture.md §3's specimen | "No scan for 62 min. The risk reached the 45% line at 60 min, so Tracer alerted with 60 min to spare; it looks first at the station (62%)." |
| 3 | Lost | fate lost (misrouted into the lost bin at 12 min, never scanned), silence 90 | the failure regime: lost becomes the best explanation | "No scan for 90 min. The risk reached the 45% line at 60 min, so Tracer alerted with 60 min to spare; lost is now the best explanation (60%)." |
| 4 | Never sent | fate unsent (left in the ward's send sleeve), silence 62 | the surprise: the same silence, the same numbers, a different truth | "The same sentence as for the stalled specimen, the same 49.5% risk and the same station: silence carries no clue that this one never left the ward." |

## Lesson
| # | Ask | Hint pill (part, text) | Arrange first | Done when (model state, hold) | Answer (live values) | Tour performs |
|---|---|---|---|---|---|---|
| 1 | "Run the clock and watch a specimen reach the scanner: press play, or drag the stopwatch hand." | stopwatch, above the dial: "Drag the hand to run the clock" | fate arrives, silence 0, truth shown, line 45%, window 120 | fate arrives and silence ≥ 26, 0.4 s | "The scan at {24 min} stopped the clock: the risk fell from {9.9%} to {0.0%}." | silence 0 → 30 over 7 s |
| 2 | "A second specimen is on its way. Hide the truth: press T or flip the truth switch." | truth switch on the bench, above it: "Flip to hide the truth" | fate lost, silence 0, truth shown | truth hidden and dissolved, 0.3 s | "Now you see what the lab sees: one scan at the ward, and then nothing. The light is all Tracer knows." | truth hidden |
| 3 | "Run the clock until Tracer raises the alarm." | stopwatch, above the dial | — | alert fired, clock settled, 0.5 s | "After {60 min} of silence the risk reached {47.1%}, past the {45%} line: Tracer alerts with {60 min} left and looks first at the station ({64%})." (values at the alert minute) | silence → 63 over 7 s |
| 4 | "Lower the alert line to warn sooner: drag it on the chart, or turn the ALERT LINE knob." | ALERT LINE knob: "Turn to move the line" | playback paused | projected alert ≤ 50 min, line settled, 0.6 s | "At a {30%} line Tracer alerts at {50 min}: {70 min} of warning instead of {60 min}. A lower line warns sooner, and also for more specimens that would have made it. The line goes back to {45%}." | line 45% → 30% over 3 s |
| 5 | "Now give the specimen a shorter life: drag the coral mark on the stopwatch to shrink the window to 90 min or less." | above the stopwatch's bezel: "Drag the coral mark to shrink the window" | line back to 45% | window ≤ 90, settled, 0.6 s | "With a {90 min} window the alert comes at {55 min} and leaves {35 min} of warning, not {60 min}. **Silence becomes the alarm** once the chance of arriving in time falls below the line: sooner, when the window is short." | window 120 → 60 over 4 s |

The lesson starts at the first input after the opening: the tour's return home puts shot one's setup in place under the black, and the lesson's start state lands right after it, still under the black (the kit's `onHome`), so the bench comes up already at the first request. A preset or the lesson tool in that moment cancels it. The law is stated about the alert minute, which the tests show never moves later as the line falls or the window shrinks; the warning time itself moves in 5-minute steps and is not monotone in the window at 1-minute resolution, so the answers quote the visitor's own numbers instead of claiming it.

## Readouts (3)
| Label | Unit | Formula | Hot? | Color |
|---|---|---|---|---|
| MISS RISK | % | riskUnreceived(m) while silent, the known outcome (0% or 100%) after the receipt scan; m = round(silence) | yes | cyan |
| MOST LIKELY | % and place | the largest of ward, tube, station (incl. stalled), lost, lab, with its probability (whole percent above 9.5%) | no | white, coral when lost |
| WARNING LEFT | min | max(0, W − m), an em dash once received (the clock has stopped); amber while a fired alert stands; the alert minute shows on the beacon's label, the chart, the card's amber marker and the caption | no | white, amber while alerted |

## World labels
| Part | Name | Live value format |
|---|---|---|
| Ward send sleeve | Ward | "0.1%" |
| Glass tube | Tube | "0.1%" |
| Station receiver | Station | "64%" (stalled share in the caption and bars) |
| Lost bin | Lost | "31%" (coral) |
| Receiving tray | Lab | "scan at 24 min" or "no scan yet" |
| Alert beacon | Alert | "at 60 min", "quiet", "cleared at 68 min" |
| Carrier (truth shown only) | Truth | "stalled at the station", "in the lost bin", "still at the ward", "in the tube", "scanned in the lab"; it hangs below the parts on a leader, so belief reads above and truth below |
| Exploded decks | What happened, What the lab sees, What Tracer infers | "the truth", "1 scan", "50.0% risk" |

Place labels stand still beside their vessels (they do not bob with the fill) and ride the columns' tops on the exploded deck.

## Diegetic displays, loupe, twin
Console screen 1, "Risk of missing the window": risk against silence 0 to 130 min, the history solid cyan up to now and the projection under continued silence dashed, the amber dashed alert line (draggable) with the alert minute annotated, coral shading past the window, a receipt tick, a white marker at now (draggable along time). Console screen 2, "Where is it now": four bars (ward, tube, station split into arrived and stalled, lost) with percentages, the most likely bar in cyan; a cream TRUTH mark beside the true place while the truth is shown; "location known: lab" after the receipt. Stopwatch dial (0 to 240 min over 270°): white hand at the silence, a cyan arc for the elapsed silence, an amber tick at the alert minute, a coral bezel marker at the window's end. ALERT LINE and WINDOW knobs with amber value arcs; the brass truth switch stands on the bench in front of the lost bin. Hint pills sit above their parts, since the console's lower half falls below the first view's frame. Loupe "100 silent specimens", floating over the tube run with a leader down to it (the silence happens in the tube): a drawing of the mixture's posterior weights as 100 small carriers, normal trips cream, late violet, never arriving coral, rounded to whole carriers by largest remainder, with the counts written under them in the trip model's words ("normal", "late", "never arrive"; the vessels keep the location model's "station" and "lost"); once a scan has come it reads "scanned: no longer silent". The stopwatch's face sits in front of its well's cap, never in its plane, so the numerals stay whole under TRAA's jitter. The truth toggle itself is the twin: the real carrier beside Tracer's light.

## Views
Lab set: the house room at dusk; echo: pneumatic tube pipes running along the back wall and up into the ceiling, dark steel with brass couplings, out of focus. Exploded: three decks on the plinth, stacked by how much is known: Truth (the bench and the carrier) on the plinth, What the lab sees (a dark glass plate with the route etched and an amber pip at each scan that has happened) above it, What Tracer infers (the glow lifted off the hardware onto a plate as a map of light) on top; the decks separate 6 units apart. Studio framing: three-quarter from the left, the bench on warm paper.

## Cinematic shots (the opening, Lab only)
Shot 1 is atmosphere and shot 2 comes closer and lower; then a close-up per feature, performing the lesson; the payoff last. Shot 1's setup resets the bench (Stalls at 62 min, truth shown) for a seamless loop.
| # | Camera move | Hold | Focus | Setup (in the black) | Beats |
|---|---|---|---|---|---|
| 1 | wide, a 17° arc from the hero side | 9 s | the station receiver | Stalls, silence 62, truth shown | none |
| 2 | low along the bench from the ward end, the tube at eye level | 9 s | the tube | — | none |
| 3 | arc round the ward and the tube | 10 s | the tube | lesson step 1's arrangement | step 1: the clock runs 0 → 30, the carrier rides to the scanner |
| 4 | push along the tube to the receiver | 10 s | the receiver | lesson step 2's arrangement | steps 2 and 3: the truth hides, the clock runs to 63, the beacon lights |
| 5 | track along the console's screens | 10 s | the screens | — | step 4: the alert line drops to 30%, the alert marker slides to 50 |
| 6 | arc round the stopwatch | 9 s | the stopwatch | — | step 5: the window shrinks to 60 min |
| 7 | low, slow arc toward the lost bin: the payoff | 11 s | the lost bin | Lost, silence 60, line 45%, window 120, truth hidden | the clock runs to 120 over 8 s, the lost bin fills; the truth is shown at 7.2 s: the carrier lies in the bin |

Glass in the views: tube glass writes depth only along its edge band and discards its clear middle, so depth of field keeps the tube sharp at its own distance while the room seen through it keeps its blur. On Studio's paper a lit glass reflects the bright studio and vanishes, so every glass mesh has an unlit paper twin, gray at its edges, shown only in Studio (sets `glass` and `paper-glass`).

## Help sheet outline
h2 "When silence becomes the alarm"; What you are looking at; Why silence raises the risk; Where Tracer thinks it is; Run the clock, move the line, shrink the window; Hide the truth; misconception "Does an alert mean it is lost?"; What is exact and what is not (exact; every assumed parameter with its median, σ, weight or exits and the working model as its source; theatre; how the clock stops at the receipt scan; approximations with sizes); Keys; footnote: minutes since the collection scan, parameters assumed, address parameters. Every number in the sheet is read from the model or the fates' script when the schema is built (`buildSchema(params, model)`), so an override rewrites it; `src/hud/schema.test.ts` pins the defaults' values.

## Honesty list
Exact: the mixture-survival risk and the forward-filter posterior as implemented in the project's working model, ported line for line and checked against it to 1e-9: the risk function at every minute 0 to 130 and at several windows, the scenario series over the page's 0 to 90 minutes, the location posterior at every minute 0 to 130, and the alert rule; the mixture weights in the loupe.  Approximated (with size): erf by Abramowitz and Stegun 7.1.26 (error under 1.5e-7; the risk itself is a closed form with no further error); the filter on a one-minute grid with ages capped at 180 min; readouts at whole minutes; the glow interpolated between minutes; the tube smear places minutes-in-tube at the median speed (the model has no positions inside a leg); vessel fill is proportional to probability.  Not real: every parameter is an assumption (nothing measured, nothing fitted, no institutional data); the default window of 120 min is the policy table's for lavage and tissue and the visitor can move it from 60 to 240; the four fates are scripted and the model never sees them; the tube layout is schematic and is not any hospital's system, and real tubes are opaque; time runs at 3 or 12 min a second (180× or 720×); the loupe is a drawing of the model's weights rounded to whole carriers, from the trip model, so its never-arrive count (34 at 60 min) need not match the lost bin (36%).  The clock stops at the receipt scan, as the policy table says for every row, so a receipt after the window closed is a miss and nothing after the scan, plating included, is modeled.
