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
- Risk while silent, mixture survival with a defective mass: `riskUnreceived(t) = 1 − safe(t) / notReceived(t)`, with `notReceived = p + Σ wₖ Sₖ(t)` and `safe = Σ wₖ Σᵤ fₖ(u) G(W − u)` summed at u = t + 0.5, t + 1.5, … below W. Transit components: normal w 0.88, lognormal median 24 min, σ 0.50; stalled w 0.08, median 70 min, σ 0.60; never arrives p 0.04. Setup G: lognormal median 22 min, σ 0.40. Window W 120 min.
- Risk after the receipt scan: `riskReceived(t) = S_setup(W − t)`. After plating the outcome is known: 0 if plated inside the window, 1 if not (the original plot's `riskAt` shows 0 after plating because its plating at 87 min falls inside its 120 min window; this exhibit lets the window shrink below the plating minute, so it states the outcome).
- Location while silent: forward filter on a one-minute grid over four sub-legs with lognormal durations and exits, ages capped at 180 min, conditioned on no receipt scan: awaiting dispatch (median 6, σ 0.60; to tube 0.992, lost 0.008), in the tube (4, 0.25; to station 0.995, lost 0.005), at the station (11, 0.70; received 0.910, stalled 0.070, lost 0.020), stalled at the station (45, 0.80; received 0.850, lost 0.150). After the receipt scan the place is known: the lab, 100%.
- Alert rule: the first 5-minute tick at or after minute 0, and not after the receipt scan, at which the risk is at least the alert line (operating point, default 0.45).
- Readouts are evaluated at whole minutes (the time display rounds); the glow interpolates between minutes.
- The mixture's posterior weights while silent, `wₖ Sₖ(t) / notReceived` and `p / notReceived` (normal, late, never arrive), drive the loupe. They are the trip model's shares, not the location model's: at 55 min the loupe shows 29 of 100 never arriving while the lost bin holds 31%.
- Time: minutes since the collection scan. Playback runs at 3 min/s (1×) or 12 min/s (4×), labeled on the card.
- Override: `window.TRACER_PARAMS` or `?params=<same-origin JSON url>` replaces any parameter; invalid overrides are ignored.

Invariants for tests:
- Parity: the port agrees with the original functions, extracted from `visuals/architecture.html` and run under node, at every minute 0 to 130, to 1e-9 (risk while silent, risk after receipt, every leg and lost of the location posterior, the alert minute).
- Risk stays in [0, 1] for every window 60 to 240 and minute 0 to 130.
- The location posterior (four legs plus lost) sums to 1 at every minute, within 1e-12.
- P(lost) never decreases with silence.
- Lowering the alert line never moves the alert minute later; shrinking the window never moves it later.
- Lead time equals the window minus the alert minute; the default parameters alert at 55 min with 65 min of lead.
- Known values from architecture.md §3: risk 0.07 at minute 0 and 0.50 at 55; at 55 the station holds 0.68 and lost 0.31; risk 0.02 just after a receipt scan at 68.
- Not an invariant: risk is not strictly monotone. The original's midpoint sum makes it dip by at most 5e-6 between minutes 1 and 4; the tests bound the dip instead of claiming monotonicity.
- A start from address parameters gives the same readouts as reaching that state through the controls.

## Parameters → controls
| Parameter | Range, default, unit | HUD control | Scene control | Console control | Keys |
|---|---|---|---|---|---|
| Silence so far | 0 to 130, 55, min | time group: play, scrubber (ticks 0, 30, 60, 90, 120; amber marker at the alert minute), speeds 3 min/s and 12 min/s | drag the stopwatch hand | drag the marker along the risk chart | Space play, `,` `.` 1 min (Shift 5), Backspace back to 0 |
| Alert line (operating point) | 0.10 to 0.90, 0.45 | slider in percent, ticks 25, 45, 75 | drag the dashed alert line on the risk chart | the ALERT LINE knob | `[` `]` 1 point (Shift 5), `0` back to 45% |
| Stability window | 60 to 240, 120, min | slider, ticks 60, 120, 180, 240 | drag the coral marker on the stopwatch bezel | the WINDOW knob | `-` `=` 5 min (Shift 15) |
| Truth | Shown / Hidden, Shown | segmented | click the brass truth switch on the bench, in front of the lost bin | — (the switch sits on the bench, where the first view sees it) | T |
| View | Lab / Exploded / Studio | segmented | — | — | Shift+V, Shift+X |

## TRY presets
Each preset picks a scripted true fate (the model never sees it) and moves the clock to the moment that shows its idea.
| # | Name | State | Demonstrates | Caption text |
|---|---|---|---|---|
| 1 | On time | fate arrives (receipt 24 min, plated 43), silence 26 | the scan ends the silence | "The receipt scan at 24 min ended the silence: only the in-lab setup remains, and the risk fell to 0.0%." |
| 2 | Stalls | fate stalls (stalled at the station, receipt 68, plated 87), silence 55 | the alert buys time: architecture.md §3's specimen | "After 55 min of silence the risk passed the 45% line: Tracer alerts with 65 min of the window left, and looks first at the station (68%)." |
| 3 | Lost | fate lost (misrouted into the lost bin at 12 min, never scanned), silence 90 | the failure regime: lost becomes the best explanation | "No scan for 90 min: lost (60%) is now Tracer's best explanation, and the risk of missing the window is 93%." |
| 4 | Never sent | fate unsent (left in the ward's send sleeve), silence 55 | the surprise: the same silence, the same numbers, a different truth | "Tracer reads 50% and the station, exactly as for the stalled specimen: silence carries no clue that this one never left the ward." |

## Lesson
| # | Ask | Hint pill (part, text) | Arrange first | Done when (model state, hold) | Answer (live values) | Tour performs |
|---|---|---|---|---|---|---|
| 1 | "Run the clock and watch a specimen reach the scanner: press play, or drag the stopwatch hand." | stopwatch, above the dial: "Drag the hand to run the clock" | fate arrives, silence 0, truth shown, line 45%, window 120 | fate arrives and silence ≥ 26, 0.4 s | "The scan at {24 min} ended the silence: the risk fell from {12.2%} to {0.0%}." | silence 0 → 30 over 7 s |
| 2 | "A second specimen is on its way. Hide the truth: press T or flip the truth switch." | truth switch on the bench, above it: "Flip to hide the truth" | fate lost, silence 0, truth shown | truth hidden and dissolved, 0.3 s | "Now you see what the lab sees: one scan at the ward, and then nothing. The light is all Tracer knows." | truth hidden |
| 3 | "Run the clock until Tracer raises the alarm." | stopwatch, above the dial | — | alert fired, clock settled, 0.5 s | "After {55 min} of silence the risk reached {50.0%}, past the {45%} line: Tracer alerts with {65 min} left and looks first at the station ({68%})." (values at the alert minute) | silence → 58 over 7 s |
| 4 | "Lower the alert line to warn sooner: drag it on the chart, or turn the ALERT LINE knob." | ALERT LINE knob: "Turn to move the line" | playback paused | projected alert ≤ 45 min, line settled, 0.6 s | "At a {30%} line Tracer alerts at {45 min}: {75 min} of warning instead of {65 min}. A lower line warns sooner, and also for more specimens that would have made it. The line goes back to {45%}." | line 45% → 30% over 3 s |
| 5 | "Now give the specimen a shorter life: drag the coral mark on the stopwatch to shrink the window to 90 min or less." | above the stopwatch's bezel: "Drag the coral mark to shrink the window" | line back to 45% | window ≤ 90, settled, 0.6 s | "With a {90 min} window the alert comes at {45 min} and leaves {45 min} of warning, not {65 min}. **Silence becomes the alarm** once the chance of arriving in time falls below the line: sooner, when the window is short." | window 120 → 60 over 4 s |

The lesson starts at the first input after the opening: the tour's return home puts shot one's setup in place under the black, and the lesson's start state lands right after it, still under the black (the kit's `onHome`), so the bench comes up already at the first request. A preset or the lesson tool in that moment cancels it. The law is stated about the alert minute, which the tests show never moves later as the line falls or the window shrinks; the warning time itself moves in 5-minute steps and is not monotone in the window at 1-minute resolution, so the answers quote the visitor's own numbers instead of claiming it.

## Readouts (3)
| Label | Unit | Formula | Hot? | Color |
|---|---|---|---|---|
| MISS RISK | % | riskUnreceived(m) while silent, riskReceived(m) after the receipt scan, the known outcome after plating; m = round(silence) | yes | cyan |
| MOST LIKELY | % and place | the largest of ward, tube, station (incl. stalled), lost, lab, with its probability (whole percent above 9.5%) | no | white, coral when lost |
| WARNING LEFT | min | max(0, W − m), an em dash once plated (the clock has stopped); amber while a fired alert stands; the alert minute shows on the beacon's label, the chart, the card's amber marker and the caption | no | white, amber while alerted |

## World labels
| Part | Name | Live value format |
|---|---|---|
| Ward send sleeve | Ward | "0.1%" |
| Glass tube | Tube | "0.1%" |
| Station receiver | Station | "68%" (stalled share in the caption and bars) |
| Lost bin | Lost | "31%" (coral) |
| Receiving tray | Lab | "scan at 24 min" or "no scan yet" |
| Alert beacon | Alert | "at 55 min", "quiet", "cleared at 68 min" |
| Carrier (truth shown only) | Truth | "stalled at the station", "in the lost bin", "still at the ward", "in the tube", "scanned in the lab"; it hangs below the parts on a leader, so belief reads above and truth below |
| Exploded decks | What happened, What the lab sees, What Tracer infers | "the truth", "1 scan", "50.0% risk" |

Place labels stand still beside their vessels (they do not bob with the fill) and ride the columns' tops on the exploded deck.

## Diegetic displays, loupe, twin
Console screen 1, "Risk of missing the window": risk against silence 0 to 130 min, the history solid cyan up to now and the projection under continued silence dashed, the amber dashed alert line (draggable) with the alert minute annotated, coral shading past the window, receipt and plating ticks, a white marker at now (draggable along time). Console screen 2, "Where is it now": four bars (ward, tube, station split into arrived and stalled, lost) with percentages, the most likely bar in cyan; a cream TRUTH mark beside the true place while the truth is shown; "location known: lab" after the receipt. Stopwatch dial (0 to 240 min over 270°): white hand at the silence, a cyan arc for the elapsed silence, an amber tick at the alert minute, a coral bezel marker at the window's end. ALERT LINE and WINDOW knobs with amber value arcs; the brass truth switch stands on the bench in front of the lost bin. Hint pills sit above their parts, since the console's lower half falls below the first view's frame. Loupe "100 silent specimens", floating over the tube run with a leader down to it (the silence happens in the tube): a drawing of the mixture's posterior weights as 100 small carriers, normal trips cream, late violet, never arriving coral, rounded to whole carriers by largest remainder, with the counts written under them in the trip model's words ("normal", "late", "never arrive"; the vessels keep the location model's "station" and "lost"); once a scan has come it reads "scanned: no longer silent". The stopwatch's face sits in front of its well's cap, never in its plane, so the numerals stay whole under TRAA's jitter. The truth toggle itself is the twin: the real carrier beside Tracer's light.

## Views
Lab set: the house room at dusk; echo: pneumatic tube pipes running along the back wall and up into the ceiling, dark steel with brass couplings, out of focus. Exploded: three decks on the plinth, stacked by how much is known: Truth (the bench and the carrier) on the plinth, What the lab sees (a dark glass plate with the route etched and an amber pip at each scan that has happened) above it, What Tracer infers (the glow lifted off the hardware onto a plate as a map of light) on top; the decks separate 6 units apart. Studio framing: three-quarter from the left, the bench on warm paper.

## Cinematic shots (the opening, Lab only)
Shot 1 is atmosphere and shot 2 comes closer and lower; then a close-up per feature, performing the lesson; the payoff last. Shot 1's setup resets the bench (Stalls at 55 min, truth shown) for a seamless loop.
| # | Camera move | Hold | Focus | Setup (in the black) | Beats |
|---|---|---|---|---|---|
| 1 | wide, a 17° arc from the hero side | 9 s | the station receiver | Stalls, silence 55, truth shown | none |
| 2 | low along the bench from the ward end, the tube at eye level | 9 s | the tube | — | none |
| 3 | arc round the ward and the tube | 10 s | the tube | lesson step 1's arrangement | step 1: the clock runs 0 → 30, the carrier rides to the scanner |
| 4 | push along the tube to the receiver | 10 s | the receiver | lesson step 2's arrangement | steps 2 and 3: the truth hides, the clock runs to 58, the beacon lights |
| 5 | track along the console's screens | 10 s | the screens | — | step 4: the alert line drops to 30%, the alert marker slides to 45 |
| 6 | arc round the stopwatch | 9 s | the stopwatch | — | step 5: the window shrinks to 60 min |
| 7 | low, slow arc toward the lost bin: the payoff | 11 s | the lost bin | Lost, silence 60, line 45%, window 120, truth hidden | the clock runs to 120 over 8 s, the lost bin fills; the truth is shown at 7.2 s: the carrier lies in the bin |

Glass in the views: tube glass writes depth only along its edge band and discards its clear middle, so depth of field keeps the tube sharp at its own distance while the room seen through it keeps its blur. On Studio's paper a lit glass reflects the bright studio and vanishes, so every glass mesh has an unlit paper twin, gray at its edges, shown only in Studio (sets `glass` and `paper-glass`).

## Help sheet outline
h2 "When silence becomes the alarm"; What you are looking at; Why silence raises the risk; Where Tracer thinks it is; Run the clock, move the line, shrink the window; Hide the truth; misconception "Does an alert mean it is lost?"; What is exact and what is not (exact; every assumed parameter with its median, σ, weight or exits and the working model as its source; theatre; the risk after the receipt scan with its size; approximations with sizes); Keys; footnote: minutes since the collection scan, parameters assumed, address parameters. Every number in the sheet is read from the model or the fates' script when the schema is built (`buildSchema(params, model)`), so an override rewrites it; `src/hud/schema.test.ts` pins the defaults' values.

## Honesty list
Exact: the mixture-survival risk and the forward-filter posterior as implemented in the project's working model, ported line for line and checked against it at every minute 0 to 130 to 1e-9; the alert rule; the mixture weights in the loupe.  Approximated (with size): erf by Abramowitz and Stegun 7.1.26 (error under 1.5e-7); the risk integral as a midpoint sum on a one-minute grid (it dips by at most 5e-6 between minutes 1 and 4); the filter on a one-minute grid with ages capped at 180 min; readouts at whole minutes; the glow interpolated between minutes; the tube smear places minutes-in-tube at the median speed (the model has no positions inside a leg); vessel fill is proportional to probability.  Not real: every parameter is an assumption (nothing measured, nothing fitted, no institutional data); the four fates are scripted and the model never sees them; the tube layout is schematic and is not any hospital's system, and real tubes are opaque; time runs at 3 or 12 min a second (180× or 720×); the loupe is a drawing of the model's weights rounded to whole carriers, from the trip model, so its never-arrive count (29 at 55 min) need not match the lost bin (31%).  Shown as written: after the receipt scan the working model puts the whole setup ahead at every minute, so the risk climbs while the specimen waits in the lab (Stalls: 1.6% at 68 min, 13.8% at 86 min; counting the setup already done would give 2.3% at 86 min).
