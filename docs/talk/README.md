# The Tracer talk, December 9

The in-class presentation for CAI 5724: the run of show, the slides, Daniel's lines and Karissa's outline. It runs 15:00, the length of the slot, so there is time to say what Tracer is, what it is built to fix, and how we built and tested it, with seven minutes of the class's hands on the demo.

- **Slides:** `docs/talk/index.html`, published at <https://scalinity.github.io/Tracer/talk/>. It opens from disk too, with no server.
- **Script:** `docs/talk/script.html`, published at <https://scalinity.github.io/Tracer/talk/script.html>. It is this file as a reading page for rehearsals and the day: a talk clock that lights the current beat and runs with the slides' clock, a speaker filter (`script.html?who=karissa` opens on Karissa's sections) and a print view of one speaker's part. It opens from disk too, and the presenter window links to it.
- **Demo:** <https://scalinity.github.io/Tracer/demo/>. The class uses it on their phones for about 7 of the 15 minutes.
- **Speakers:** Daniel Escalante and Karissa Kocourek. Daniel's lines are written out below. Karissa's sections are outlined instead: the points to cover, in order, and about how long, and she writes her own words from them. `docs/talk/karissa-outline.md` holds her part on its own. If she cannot be there, Daniel covers her points. The speaking time is split about evenly between the two of us, and the talk changes speaker ten times, so neither voice runs long.

The numbers from the demo and its results come from synthetic data that had no clinical review, and the slides say so on each screen that shows one. The others are not synthetic: the 61.9% comes from a published audit, the 60-minute urine window and the 44 policy rows from Karissa's policy table and the guidelines it cites, and the 80.1 hours from the public MIMIC-IV demo extract. "Where the numbers come from" below gives the source of each.

## What the course asks for

From the revised guidelines (`docs/course/ai-design-project-guidelines.pdf`):

- About 15 minutes per team, in any format, covering three things: a brief discussion of the problem, an overview of the AI prototype and how it helps, and a hands-on class activity with the prototype.
- The interactive demo should take about 10 minutes and must be reachable at a public URL.
- The prototype does not have to be finished by the talk, but it has to work.

The rubric gives the talk 10 points: a clear narrative for a mixed audience (2), the rationale for the design (2), a correct understanding of how the system works (2), and class engagement and answers to questions (4). The demo's own 10 points include encouraging exploration with different inputs and settings (2). The run of show puts the class's hands on the demo early and keeps them there, because engagement carries the most points.

## Run of show

The beats come from the run of show in `visuals/plan.html`, cut to fit the 15-minute slot. Hands-on time is the board, Beat Tracer and the threshold slider: 7 minutes, plus the discussion in the questions.

| Clock | Slide | Beat | Who | On the screen | The room is |
|---|---|---|---|---|---|
| before 0:00 | 0 | Title | | Title slide; the 3D demo, the demo board and the specimen link open in tabs behind it | settling in |
| 0:00 to 0:30 | 1 | The silent tube | Karissa | The hand vote, drawn as a timeline marked at 15 minutes, 30 minutes and 1 hour | voting with hands |
| 0:30 to 1:15 | 2 | Why it matters | Karissa | Problem slide | listening |
| 1:15 to 1:45 | 3 | The missing data | Daniel | Two timestamps, 80.1 hours apart | listening |
| 1:45 to 2:30 | 4 | Meet Tracer | Daniel | What Tracer reads and what it gives the lab | listening |
| 2:30 to 3:00 | 5 | What Tracer is built to fix | Karissa | Today and with Tracer, side by side | listening |
| 3:00 to 3:45 | 6 | How Tracer works | Daniel | Three questions, then the self-playing 3D demo | watching |
| 3:45 to 6:15 | 7 | Board and timeline | Karissa | QR code, then one specimen's timeline on the projector | on phones |
| 6:15 to 9:45 | 8 | Beat Tracer | Daniel, then Karissa | Seeded QR code, then the game on the projector | playing |
| 9:45 to 10:45 | 9 | Why this design | Daniel | The threshold slider, then the numbers slide | dragging the slider |
| 10:45 to 11:30 | 10 | How we built it | Karissa, then Daniel | The data and the model, from briefs to the demo | listening |
| 11:30 to 12:00 | 11 | TracerNet wins | Daniel | TracerNet's warning beside the baselines on fresh data, and the verdict | listening |
| 12:00 to 12:45 | 12 | Limits and next steps | Karissa | Limits slide | listening |
| 12:45 to 15:00 | 13 | Questions | Both | Closing slide with the QR code and the repo | asking |

Minute by minute, for whoever keeps time:

| Minute | What should be happening |
|---|---|
| 0 | Karissa opens with the hand vote. |
| 1 | Karissa on the problem. Daniel on the missing data at 1:15. |
| 2 | Daniel introduces Tracer. Karissa on what it is built to fix at 2:30. |
| 3 | Daniel on the three questions at 3:00, then the 3D demo. |
| 4 | Karissa puts the QR code up at 3:45. Phones out. Everyone on the board by about 4:05. |
| 5 | The room is scrubbing a timeline. Karissa walks one specimen on the projector. |
| 6 | "Show the truth" on their own specimen. Seeded QR code up at 6:15. Daniel explains Beat Tracer in 30 seconds. |
| 7 | Round 1 under way by about 7:00. |
| 8 | Rounds 2 and 3. Score screens up by about 8:45. |
| 9 | Karissa: who beat Tracer, and what made you call it. Daniel on the threshold slider at 9:45. |
| 10 | Daniel on the numbers. At 10:45 Karissa on how we built the data, then Daniel on the model. |
| 11 | Daniel on TracerNet winning at 11:30. |
| 12 | Karissa on the limits at 12:00. Her close at about 12:45, then questions. |
| 13 | Questions. |
| 14 | Questions. Stop at 15:00 even mid-question, and offer to talk after. |

If the hands-on part runs long, cut in this order: Karissa's projector walkthrough on slide 7 (the room keeps exploring alone), then the numbers slide in beat 9 (say the one headline sentence and stay on the slider), then TracerNet winning on slide 11 (prepared answer 5 covers it). The game shows its end screen only after round 3, so if the room is still on round 2 at 8:45, Karissa asks for hands from the round result screens instead: who called it before Tracer did. Never cut the board or the limits.

Beat Tracer is the longest beat, 3:30 of the 15 minutes, and with it the questions get 2:15. Without it the questions get 5:45 and the hands-on time is the board and the slider, 3:30. It stays in until we decide at rehearsal.

## Before the talk

Open these on the presenting laptop, each in its own tab, in this order:

1. The slides: <https://scalinity.github.io/Tracer/talk/>. Press **F** for full screen. Press **P** for the presenter window, with these notes and the clock, on the laptop screen.
2. The self-playing 3D demo, on the stalled specimen: <https://scalinity.github.io/Tracer/demo/play/?scene=2>. It plays by itself, with nothing to press.
3. The demo board: <https://scalinity.github.io/Tracer/demo/>.
4. The specimen for slide 7: <https://scalinity.github.io/Tracer/demo/#SP-r27-000368>. The address opens that specimen's timeline.
5. Beat Tracer with the room's rounds: <https://scalinity.github.io/Tracer/demo/?seed=18#beat>.

Slide keys: right arrow, space or click for next; left arrow for back; **F** full screen; **P** presenter window; **N** notes on this screen (for rehearsal, not for the projector); **S** starts or resets the talk clock; the number keys 0 to 9 jump to the first ten slides, and End to the last. The presenter window and the slides follow each other when both are open from the same address. The script page shares their talk clock when it is open in the same browser too: **S** on the slides or the script page starts or resets it everywhere, and Pause on the script page pauses it everywhere. From disk, use **N** instead.

The backups are the plan's: the five-minute screen recording, `?lite=1` on the demo if the 3D view is blank, the phone hotspot, and an offline copy of `docs/` on the laptop. The slides open straight from disk. The demo does not, because the browser blocks it from reading its data files there, so serve the copy first and open <http://localhost:8000/demo/>:

```sh
cd docs && python3 -m http.server 8000
```

## Slides and speaker notes

Words in brackets are stage directions, not lines. Daniel's lines are written out. Karissa's sections are numbered points to cover in order, and she says them in her own words. The word counts assume about 130 words a minute, a classroom pace with pauses, and a word count on one of Karissa's sections is a guide to its length, not a script.

### Slide 0 · Title · on screen before the clock starts

**Screen:** Tracer. Predictive specimen tracking for the clinical microbiology laboratory. Daniel Escalante and Karissa Kocourek, CAI 5724, University of Florida. Synthetic data, no clinical review.

**Notes:** Nothing is said. Karissa starts when the instructor hands over.

### Slide 1 · The silent tube · 0:00 to 0:30 · Karissa

**Screen:** "A tube leaves the ward." The hand vote, drawn as a timeline marked at 15 minutes, 30 minutes and 1 hour.

**Karissa:**

1. A tube has left the ward: nothing beeps, nothing is logged, and nobody knows yet if it is late.

2. The hand vote: who would start to worry at 15 minutes, at 30, at an hour?

3. Their number is the guess Tracer has to make, for every specimen, all day.

[In her own words, about 50 words. Pause for each show of hands.]

### Slide 2 · Why it matters · 0:30 to 1:15 · Karissa

**Screen:** Three facts. 61.9% of errors in one stat laboratory's audit happened before analysis (Carraro and Plebani, Clin Chem 2007). Unpreserved urine at room temperature: a 60-minute window in our policy table, from the IDSA and ASM 2024 guide. A missed window usually means a new specimen: another needle stick or lumbar puncture, and hours of delay.

**Karissa:**

1. Most lab errors happen before testing: about 62% in one stat laboratory's audit.

2. A microbiology specimen is on a clock: unpreserved urine has about an hour at room temperature, and spinal fluid must come straight to the lab and never be refrigerated.

3. A missed window often means rejection and a new collection: another needle stick or lumbar puncture, and hours of delay.

4. Between collection and receipt the lab has no record, so a missing specimen is noticed only when someone calls for the result.

[In her own words, about 95 words.]

### Slide 3 · The missing data · 1:15 to 1:45 · Daniel

**Screen:** Two dots on a line, "Collected" and "Result filed", with nothing between them. Median 80.1 hours apart, across 1,291 specimens in the public MIMIC-IV demo extract. No receipt scan, no transport record.

**Daniel:**

So why not learn this from hospital data? The best-known public one, MIMIC-IV, stamps a microbiology specimen twice: when it's collected and when the result is filed. Those stamps are a median of eighty hours apart, with nothing in between. There's no field for the journey at all. So we generated our own data, and every number you'll see is an assumption we can change.

[About 65 words.]

### Slide 4 · Meet Tracer · 1:45 to 2:30 · Daniel

**Screen:** "An early warning for specimens in transit." Like package tracking with no scans along the way. What it reads: when it was collected, what kind it is and where from, and how long it can wait. What it gives the lab, every 5 minutes: who is at risk, ranked; where each one likely is; and who should go and look. No new hardware: only the scans the lab already records.

**Daniel:**

With that data, we built Tracer. Think of package tracking with no scans along the way. You know when it left, and how long trips like it usually take, so at first, silence means nothing. But the longer it stays quiet past normal, and the closer it gets to its own deadline, the more you should worry. Tracer does that for every specimen the lab is waiting on, every five minutes, using only the scans the lab already records. It ranks them by risk, says where each one most likely is, and alerts someone who can go look, while there's still time.

[About 100 words.]

### Slide 5 · What Tracer is built to fix · 2:30 to 3:00 · Karissa

**Screen:** Today and with Tracer, side by side. Today a missing specimen makes no noise; Tracer scores each one every 5 minutes. Today it is found when someone calls for it; Tracer alerts while there is still time. Today one time limit covers every specimen; Tracer judges each by its own window. Tracking tags would need readers in every hallway; Tracer uses only the scans the lab records. The aim: fewer repeat collections and quicker results for patients, and less time chasing specimens for the lab.

**Karissa:**

1. Today a missing specimen stays silent until someone calls for the result.

2. One fixed time limit is too late for urine and too early for a swab.

3. Tracking tags would need a tag on every tube and a reader in every hallway.

4. Tracer closes those gaps with the scans the lab already records.

5. The aim: fewer repeat collections, quicker results for the patient, and less time chasing for the lab.

[In her own words, about 65 words.]

### Slide 6 · How Tracer works · 3:00 to 3:45 · Daniel

**Screen:** "Silence is evidence." Every 5 minutes, for every specimen not yet received, three questions, each with a picture. How unusual is this silence: a dotted silence running past where trips like it usually arrive. Where is it most likely: the unit, the tube and the station, each sized by how likely it is. Is there still time to act: the time left between an alert and the window closing. Then: an alert, routed to the role that can check that place. Switch to the self-playing 3D demo for the second half.

**Daniel:**

So how does it know? Silence is evidence. Every five minutes, Tracer asks three questions. How unusual is this much silence, for this kind of specimen? Where is it most likely to be right now: on the unit, in the tube, or at the tube station? And if we alert now, is there still time to act? [Switch to the 3D demo.] This is that, playing by itself. The glow along the tube is where Tracer thinks the specimen is. The carrier is where it really is. It's a small model you can read, not a black box.

[About 95 words.]

### Slide 7 · Board and timeline · 3:45 to 6:15 · Karissa · hands-on

**Screen:** The QR code for the demo, the address, and three steps: tap a row, drag the scrubber, tap Show the truth. Then the projector switches to the specimen tab.

**Karissa:**

1. Phones out: scan the code or type the address.

2. What the board is: every specimen collected and not yet received, ranked by expected harm.

3. What to do: tap a row, drag the scrubber slowly to the right, and watch the risk climb, where Tracer thinks it is, and when the alert fires and who gets it.

4. On the projector, one specimen: a stat tissue specimen from the emergency department that sat at the lab's tube station for almost an hour.

5. Tracer alerted at minute 20 with 100 minutes of its window left, put it most likely at the tube station, and it was received in time.

6. Their turn: tap Show the truth and see where their own specimen really was.

[In her own words, about 120 words. Wait about 20 seconds after point 1. After point 3, give the room about a minute on their own, then Daniel switches the projector to the specimen tab. The rest of the time is the room's.]

### Slide 8 · Beat Tracer · 6:15 to 9:45 · Daniel, then Karissa · hands-on

**Screen:** The QR code for the seeded game, so everyone plays the same three rounds, and the rules in three lines: six specimens leave and one will be late; tap it as soon as you can tell; one guess a round. Then the projector shows the game.

**Daniel:**

Now you get to compete with it. Scan this second code. It gives everyone in the room the same three rounds. Six specimens leave, and one of them will be late. Watch the scans come in, and tap the late one as soon as you can tell. You get one guess. Tap too early and you might pick wrong. Wait too long, and you've given no warning. Press Start.

[About 70 words. The three rounds take about a minute and a half. Walk the room. If phones struggle, play one round on the projector and ask for a show of hands.]

**Karissa:**

1. The end screen puts their warning next to Tracer's, on the same rounds.

2. Hands up if you beat Tracer.

3. What made you call it? Take one or two answers.

4. The idea: the silence itself was telling them something.

5. Some early callers picked the wrong one, and that trade is what Daniel shows next.

[In her own words, about 65 words plus the answers. Start when most end screens are up. About a minute.]

### Slide 9 · Why this design · 9:45 to 10:45 · Daniel · hands-on

**Screen:** First the demo's "Set the threshold" section on the projector, while the room drags it on their phones. Then the numbers slide: median warning at 6 alerts per 8-hour shift on the held-out synthetic runs. The plan's time-limit rule 30 minutes, a rule that alerts at a fixed share of the window 45, Tracer 55 (45 with equal tier weights).

**Daniel:**

That trade is the threshold. Scroll down to Set the threshold and drag it both ways. Lower, and Tracer warns earlier but alerts on specimens that would have arrived anyway. Higher, fewer alerts, less warning. So we judge Tracer by minutes of warning at a number of alerts a shift can handle, not by accuracy. [Switch to the numbers.] At six alerts a shift, Tracer gave fifty-five minutes of median warning, against thirty for a simple time limit. But a rule that alerts once a set share of the window has passed gets forty-five. With equal weights, Tracer only ties it. Most of the gain comes from using the window.

[About 105 words, over the room dragging the slider.]

### Slide 10 · How we built it · 10:45 to 11:30 · Karissa, then Daniel

**Screen:** "How we built and tested Tracer," in two rows. The data: briefs drawn by code (12,000 specimens, faults planted on purpose), then Claude writes the scans (each trail and its hidden true path), then a validator checks them (nine checks, 11,967 kept and frozen, $2.16 in all). The model: a three-part survival model is fit on 24 runs (normal trip times by type, hour and priority; trips that run far longer but arrive; specimens that never arrive), it is tested on 6 runs it never saw (2,378 specimens, 100 missed windows), and its numbers go to the demo you used (two files, no server). The hidden truth goes only to the test, never to the model.

**Karissa:**

1. No public data has these scans, so we made our own.

2. Code draws 12,000 specimens from assumed timings and the policy table.

3. Claude writes each one's scans and its hidden true path, and a validator keeps the ones that pass nine checks.

[In her own words, about 45 words.]

**Daniel:**

Then a small survival model learns from twenty-four of the thirty runs: how long a normal trip takes, how often one runs far longer and still arrives, and how often one never does. We test it on the other six, and only that test sees the hidden truth.

[About 50 words.]

### Slide 11 · TracerNet wins · 11:30 to 12:00 · Daniel

**Screen:** "TracerNet now runs Tracer." 23 minutes of warning, on 776 lab weeks it had never seen. Average warning at 6 alerts per 8-hour shift, a miss counting as zero: the window rule 7.6 minutes, our survival model refit to those weeks 13.6, TracerNet 23.3. The rule was set before testing on fresh data; it passed, so the survival model is now a baseline.

**Daniel:**

We also trained a neural network, TracerNet. We wrote down the rule it had to pass, then tested it once, on 776 simulated lab weeks it had never seen. It warned twenty-three minutes ahead on average, against fourteen for our survival model. It passed. So TracerNet now runs Tracer, and the survival model is a baseline.

[About 55 words.]

### Slide 12 · Limits and next steps · 12:00 to 12:45 · Karissa

**Screen:** What Tracer cannot see: a mislabeled or leaking specimen. Every specimen is synthetic, with no clinical review. 10 of the 44 policy rows are still assumed. At twice the volume, a simple window rule matches it. Next step: a real laboratory log with receipt scans.

**Karissa:**

1. Tracer watches only time and place, so it cannot see a mislabeled or leaking specimen.

2. Every specimen is synthetic, with no clinical review of the data or the model.

3. The policy table behind the stability windows: 10 of its 44 rows still have no published source, so they are marked as assumptions.

4. Tracer's lead depends on the setting: at twice the volume, the simple window rule matches it.

5. The next step: test it on a real laboratory log with receipt scans.

[In her own words, about 90 words.]

### Slide 13 · Questions · 12:45 to 15:00 · Both

**Screen:** "Questions?" The demo's QR code, the demo address, the repository address, both names.

**Karissa:**

1. That's Tracer.

2. The demo stays up at this address, and the code, the data and how we made it are on GitHub.

3. Invite their questions.

[In her own words, about 30 words. Then the questions below. Stop at 15:00.]

## Prepared answers

The suggested speaker is in brackets. Either of us can take any of them. Daniel's answers are written out; Karissa's are points she answers in her own words. Keep each answer to two or three sentences, and say what we don't know.

1. **What if a scanner just misses a scan?** [Daniel] We didn't plant missed scans in the data, so that's a gap. A missed receipt scan would look like a late specimen, so Tracer would raise its risk and someone would check, which costs one alert.
2. **Why not just alert on everything after 30 minutes?** [Daniel] That's close to the simple rule we compared against. It ignores the window, and the windows in our table run from 60 minutes for unpreserved urine to days for a swab in transport medium, so one time limit is too late for some and far too early for others.
3. **What would someone actually do with an alert?** [Karissa] Points: go to the place Tracer names, like the tube station or the collecting unit, and look for it; who gets which alert is our assumption, and a real lab would set it.
4. **Isn't it circular to test on data you generated?** [Daniel] Partly, yes. The data and the model share our assumptions, so a good result shows the method behaves as designed, not that it works in a real lab. That's why the next step is a real log with receipt scans.
5. **Why a simple model and not deep learning?** [Daniel] We have both. TracerNet, a neural network, now runs Tracer: it passed a rule we wrote down before testing it once on fresh simulated weeks. The survival model stays as a baseline a lab scientist can read: how long a normal trip takes, how often one runs far longer and still arrives, how often it never does. It is also the model that runs live on your phone.
6. **How often is it right about where the specimen is?** [Daniel] At 30 minutes of silence, the true place is in its top three about 88% of the time and its first guess about 55%, on synthetic data. So an alert says something is off more reliably than it says where.
7. **How many false alarms?** [Daniel] Plenty, by design. At 6 alerts a shift, about half of TracerNet's alerts go to specimens that arrive in time, about three a shift. That's still the fewest per alert of any method we tested.
8. **Why does the clock stop at receipt?** [Karissa] Points: where a window ends is a clinical judgement; receipt in the lab is the standard measure, and it is what the policy table uses; a delay at the bench after receipt is outside what Tracer watches.
9. **Is any real patient or hospital data in this?** [Karissa] Points: no; a public 100-patient MIMIC-IV extract was checked and set aside, because it describes intensive care, not a general lab; no workplace data is used anywhere.
10. **How did you use AI to build it?** [Daniel] Claude Code, a coding agent, wrote the code and tests under rules I set, and I made the calls the repo couldn't. A small Claude model wrote the scan trails: 12,000 requested, 11,967 passed a nine-check validator, for about two dollars. It's all in the AI teaming log in the repo.

Questions to ask the room if it goes quiet:

- If you were the tech getting these alerts, how many a shift would you answer before you stopped looking?
- Which specimen would you want flagged first: a urine with an hour left, or a spinal fluid with two?

## Where the numbers come from

| Claim | Source in the repo |
|---|---|
| 61.9% pre-analytical; spinal fluid handling | `docs/problem-evidence.md` (Carraro and Plebani 2007; Miller et al., IDSA and ASM 2018) |
| Rejection, recollection and chasing specimens | `docs/project-spec.md`, the problem and who is affected |
| 60-minute window for unpreserved urine (IDSA and ASM 2024); 44 policy rows, 10 assumed | `config/breach_policy.yaml`, from Karissa's policy table |
| 80.1 hours across 1,291 specimens | `docs/problem-evidence.md`, `docs/mimic-demo-findings.md` |
| 55, 45 and 30 minutes at 6 alerts per shift; ties at equal weights | `results/README.md`, `results/mvp-nov8.json` |
| Twice the volume: the window rule ties | `results/README.md`, Sensitivity |
| Top-3 88% and top-1 55% at 30 minutes | `results/location-accuracy.json` |
| About half of TracerNet's alerts false, about three a shift, the fewest per alert | `docs/neural/results-v2.md`, G3 |
| 12,000 requested, 11,967 accepted, $2.16; 30 runs; nine checks | `datasets/manifest.json`, `src/generator/` |
| Fit on runs 0 to 23 (9,514 specimens); 6 held-out runs, 2,378 specimens, 100 breaches | `docs/demo/bundle.json` (model), `results/README.md` |
| TracerNet 23.3 minutes against 7.6 (window rule) and 13.6 (survival model refit locally) on 776 fresh lab weeks, capped mean warning at 6 alerts per shift and 3-2-1 weights; the rule it passed | `docs/neural/results-v2.md`; the rule in `docs/neural/preregistration-v2.md` |
| The specimen on slide 7 | `docs/demo/heroes.json`, `SP-r27-000368` |

## Keeping the slides and this file together

`docs/talk/index.html` carries the same speaker notes as this file, word for word, and `tests/test_talk.py` fails if they drift apart, if the slide times stop adding up to the run of show's 15:00, or if a link on the slides points at a file that isn't there. Change the notes here and on the slides in the same commit. `docs/talk/karissa-outline.md` carries Karissa's points too, and the same tests fail if it drifts from this file.

`docs/talk/script.html` is built from this file. After any change here, run `python scripts/build_talk_script.py` and commit the page with it; `tests/test_talk.py` fails if the page and this file differ. `tests/test_talk_browser.py` opens the slides in Chrome at 1920 by 1080 and 1280 by 800 and fails if a slide runs into its footer, shrinks its type to fit, or shows text too small to read from the back of a room, or if the N, S and P keys stop working.

The QR code for Beat Tracer is `docs/talk/qr-beat.png`, for <https://scalinity.github.io/Tracer/demo/?seed=18#beat>. To write it again:

```sh
python -c "from pathlib import Path; from src.api import qr; qr.write(Path('docs/talk/qr-beat.png'), url='https://scalinity.github.io/Tracer/demo/?seed=18#beat')"
```
