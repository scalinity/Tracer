# The Tracer talk, November 18

The in-class presentation for CAI 5724: the run of show, the slides and every line we say. It runs 17:00, 2 minutes over the 15-minute slot by choice, so there is time to say what Tracer is, what it is built to fix, and how we built and tested it.

- **Slides:** `docs/talk/index.html`, published at <https://scalinity.github.io/Tracer/talk/>. It opens from disk too, with no server.
- **Script:** `docs/talk/script.html`, published at <https://scalinity.github.io/Tracer/talk/script.html>. It is this file as a reading page for rehearsals and the day: a talk clock that lights the current beat and runs with the slides' clock, a speaker filter (`script.html?who=karissa` opens on Karissa's lines) and a print view of one speaker's lines. It opens from disk too, and the presenter window links to it.
- **Demo:** <https://scalinity.github.io/Tracer/demo/>. The class uses it on their phones for about 8 and a half minutes of the 17.
- **Speakers:** Daniel Escalante and Karissa Kocourek. Karissa's lines are written out below in full, so she has nothing to prepare beyond reading them. If she cannot be there, Daniel reads them as written. The words are split about evenly between the two of us, and the talk changes speaker ten times, so neither voice runs long.

The numbers from the demo and its results come from synthetic data that had no clinical review, and the slides say so on each screen that shows one. The others are not synthetic: the 61.9% comes from a published audit, the 60-minute urine window and the 44 policy rows from Karissa's policy table and the guidelines it cites, and the 80.1 hours from the public MIMIC-IV demo extract. "Where the numbers come from" below gives the source of each.

## What the course asks for

From the revised guidelines (`docs/course/ai-design-project-guidelines.pdf`):

- About 15 minutes per team, in any format, covering three things: a brief discussion of the problem, an overview of the AI prototype and how it helps, and a hands-on class activity with the prototype.
- The interactive demo should take about 10 minutes and must be reachable at a public URL.
- The prototype does not have to be finished by the talk, but it has to work.

The rubric gives the talk 10 points: a clear narrative for a mixed audience (2), the rationale for the design (2), a correct understanding of how the system works (2), and class engagement and answers to questions (4). The demo's own 10 points include encouraging exploration with different inputs and settings (2). The run of show puts the class's hands on the demo early and keeps them there, because engagement carries the most points.

## Run of show

The beats come from the run of show in `visuals/plan.html`, whose lengths are weights the plan scales to any slot; here they run at their own lengths, 17:00 in all. Hands-on time is the board, Beat Tracer and the threshold slider: 8.5 minutes, plus the discussion in the questions.

| Clock | Slide | Beat | Who | On the screen | The room is |
|---|---|---|---|---|---|
| before 0:00 | 0 | Title | | Title slide; demo, 3D bench and the specimen link open in tabs behind it | settling in |
| 0:00 to 0:30 | 1 | The silent tube | Karissa | The 3D bench with the truth hidden | voting with hands |
| 0:30 to 1:30 | 2 | Why it matters | Karissa | Problem slide | listening |
| 1:30 to 2:00 | 3 | The missing data | Daniel | Two timestamps, 80.1 hours apart | listening |
| 2:00 to 2:45 | 4 | Meet Tracer | Daniel | What Tracer reads and what it gives the lab | listening |
| 2:45 to 3:30 | 5 | What Tracer is built to fix | Karissa | Today and with Tracer, side by side | listening |
| 3:30 to 4:15 | 6 | How Tracer works | Daniel | Three questions, then the 3D bench with the truth shown | listening |
| 4:15 to 7:15 | 7 | Board and timeline | Daniel, then Karissa | QR code, then one specimen's timeline on the projector | on phones |
| 7:15 to 11:45 | 8 | Beat Tracer | Daniel, then Karissa | Seeded QR code, then the game on the projector | playing |
| 11:45 to 12:45 | 9 | Why this design | Daniel | The threshold slider, then the numbers slide | dragging the slider |
| 12:45 to 13:30 | 10 | How we built it | Karissa, then Daniel | The data and the model, from briefs to the demo | listening |
| 13:30 to 14:15 | 11 | Limits and next steps | Karissa | Limits slide | listening |
| 14:15 to 17:00 | 12 | Questions | Both | Closing slide with the QR code and the repo | asking |

Minute by minute, for whoever keeps time:

| Minute | What should be happening |
|---|---|
| 0 | Karissa opens on the 3D bench. The hand vote. |
| 1 | Karissa on the problem slide. Daniel on the missing data at 1:30. |
| 2 | Daniel introduces Tracer at 2:00. Karissa on what it is built to fix at 2:45. |
| 3 | Daniel on the three questions and the 3D bench at 3:30. |
| 4 | QR code up at about 4:15. Phones out. Everyone on the board by about 4:35. |
| 5 | The room is scrubbing a timeline. Karissa walks one specimen on the projector. |
| 6 | "Show the truth" on their own specimen. |
| 7 | Seeded QR code up at 7:15. Daniel explains Beat Tracer in 30 seconds. Round 1 by about 7:55. |
| 8 | Round 1 or 2. |
| 9 | Round 2 or 3. |
| 10 | Round 3 ends. End screens up by about 10:50. |
| 11 | Karissa: who beat Tracer, and what made you call it. Daniel on the threshold slider at 11:45. |
| 12 | Daniel on the numbers. At 12:45 Karissa on how we built the data, then Daniel on the model. |
| 13 | Karissa on the limits at 13:30. |
| 14 | Karissa's closing line at about 14:15, then questions. |
| 15 | Questions. |
| 16 | Questions. Stop at 17:00 even mid-question, and offer to talk after. |

If the hands-on part runs long, cut in this order: Karissa's projector walkthrough on slide 7 (the room keeps exploring alone), then the numbers slide in beat 9 (say the one headline sentence and stay on the slider). The game shows its end screen only after round 3, so if the room is still on round 2 at 10:45, Karissa asks for hands from the round result screens instead: who called it before Tracer did. Never cut the board, the game or the limits. If the instructor holds us to 15:00, skip slide 10 (how we built it) and give the questions 1:30 instead of 2:45.

## Before the talk

Open these on the presenting laptop, each in its own tab, in this order:

1. The slides: <https://scalinity.github.io/Tracer/talk/>. Press **F** for full screen. Press **P** for the presenter window, with these notes and the clock, on the laptop screen.
2. The 3D bench with the truth hidden: <https://scalinity.github.io/Tracer/demo/exhibit/?truth=0>. This address skips the opening tour and starts with the carrier hidden; **T** shows it on slide 6.
3. The demo board: <https://scalinity.github.io/Tracer/demo/>.
4. The specimen for slide 7: <https://scalinity.github.io/Tracer/demo/#SP-r27-000368>. The address opens that specimen's timeline.
5. Beat Tracer with the room's rounds: <https://scalinity.github.io/Tracer/demo/?seed=18#beat>.

Slide keys: right arrow, space or click for next; left arrow for back; **F** full screen; **P** presenter window; **N** notes on this screen (for rehearsal, not for the projector); **S** starts or resets the talk clock; the number keys 0 to 9 jump to the first ten slides, and End to the last. The presenter window and the slides follow each other when both are open from the same address. The script page shares their talk clock when it is open in the same browser too: **S** on the slides or the script page starts or resets it everywhere, and Pause on the script page pauses it everywhere. From disk, use **N** instead.

The backups are the plan's: the five-minute screen recording, `?lite=1` on the demo if the 3D view is blank, the phone hotspot, and an offline copy of `docs/` on the laptop. The slides open straight from disk. The demo does not, because the browser blocks it from reading its data files there, so serve the copy first and open <http://localhost:8000/demo/>:

```sh
cd docs && python3 -m http.server 8000
```

## Slides and speaker notes

Words in brackets are stage directions, not lines. The word counts assume about 130 words a minute, a classroom pace with pauses.

### Slide 0 · Title · on screen before the clock starts

**Screen:** Tracer. Predictive specimen tracking for the clinical microbiology laboratory. Daniel Escalante and Karissa Kocourek, CAI 5724, University of Florida. Synthetic data, no clinical review.

**Notes:** Nothing is said. Switch to the 3D bench tab with the truth hidden when the instructor hands over.

### Slide 1 · The silent tube · 0:00 to 0:30 · Karissa

**Screen:** The 3D bench, with the truth hidden. The slide behind it reads "A tube leaves the ward.", draws the hand vote as a timeline marked at 15 minutes, 30 minutes and 1 hour, and holds the bench's link.

**Karissa:**

A tube leaves the ward. Nothing beeps, and nothing gets logged. Nobody knows yet if it's late. So, raise your hand if you'd start to worry after fifteen minutes. Thirty? An hour? Hold on to that number, because that's the guess Tracer has to make, for every specimen, all day.

[About 50 words. Pause for each show of hands.]

### Slide 2 · Why it matters · 0:30 to 1:30 · Karissa

**Screen:** Three facts. 61.9% of errors in one stat laboratory's audit happened before analysis (Carraro and Plebani, Clin Chem 2007). Unpreserved urine at room temperature: a 60-minute window in our policy table, from the IDSA and ASM 2024 guide. A missed window usually means a new specimen: another needle stick or lumbar puncture, and hours of delay.

**Karissa:**

Most lab errors don't happen during testing. In one stat laboratory's audit, about sixty-two percent of errors happened before the specimen was ever analyzed. And a microbiology specimen is on a clock. Unpreserved urine at room temperature has about an hour before bacteria overgrow and the colony count stops meaning anything. Spinal fluid has to come straight to the lab, and it must not be refrigerated. When a specimen misses its window, it's often rejected and has to be collected again. For the patient, that's another needle stick or another lumbar puncture, and hours of delay. And the lab has no record of where a specimen is between collection and receipt, so a missing one often isn't noticed until someone calls asking for the result.

[About 125 words.]

### Slide 3 · The missing data · 1:30 to 2:00 · Daniel

**Screen:** Two dots on a line, "Collected" and "Result filed", with nothing between them. Median 80.1 hours apart, across 1,291 specimens in the public MIMIC-IV demo extract. No receipt scan, no transport record.

**Daniel:**

So why not learn this from hospital data? The best-known public one, MIMIC-IV, stamps a microbiology specimen twice: when it's collected and when the result is filed. Those stamps are a median of eighty hours apart, with nothing in between. There's no field for the journey at all. So we generated our own data, and every number you'll see is an assumption we can change.

[About 65 words.]

### Slide 4 · Meet Tracer · 2:00 to 2:45 · Daniel

**Screen:** "An early warning for specimens in transit." Like package tracking with no scans along the way. What it reads: when it was collected, what kind it is and where from, and how long it can wait. What it gives the lab, every 5 minutes: who is at risk, ranked; where each one likely is; and who should go and look. No new hardware: only the scans the lab already records.

**Daniel:**

With that data, we built Tracer. Think of package tracking with no scans along the way. You know when it left, and how long trips like it usually take, so at first, silence means nothing. But the longer it stays quiet past normal, and the closer it gets to its own deadline, the more you should worry. Tracer does that for every specimen the lab is waiting on, every five minutes, using only the scans the lab already records. It ranks them by risk, says where each one most likely is, and alerts someone who can go look, while there's still time.

[About 100 words.]

### Slide 5 · What Tracer is built to fix · 2:45 to 3:30 · Karissa

**Screen:** Today and with Tracer, side by side. Today a missing specimen makes no noise; Tracer scores each one every 5 minutes. Today it is found when someone calls for it; Tracer alerts while there is still time. Today one time limit covers every specimen; Tracer judges each by its own window. Tracking tags would need readers in every hallway; Tracer uses only the scans the lab records. The aim: fewer repeat collections and quicker results for patients, and less time chasing specimens for the lab.

**Karissa:**

So what does that change in the lab? Today, the lab's screens only show what's been scanned, so a missing specimen stays silent until someone calls asking for the result. A fixed time limit is too late for urine and too early for a swab. And tracking tags would need a tag on every tube and a reader in every hallway. Tracer is built to close those gaps with data the lab already has. The aim is fewer repeat collections and quicker results for the patient, and less time chasing for the lab.

[About 90 words.]

### Slide 6 · How Tracer works · 3:30 to 4:15 · Daniel

**Screen:** "Silence is evidence." Every 5 minutes, for every specimen not yet received, three questions, each with a picture. How unusual is this silence: a dotted silence running past where trips like it usually arrive. Where is it most likely: the unit, the tube and the station, each sized by how likely it is. Is there still time to act: the time left between an alert and the window closing. Then: an alert, routed to the role that can check that place. Switch to the 3D bench for the second half.

**Daniel:**

So how does it know? Silence is evidence. Under the hood, Tracer asks three questions. How unusual is this much silence, for this kind of specimen? Where is it most likely to be right now: still on the unit, in the tube, or sitting at the tube station? And if we alert now, is there still time to act before its window closes? [Switch to the 3D bench.] This is a tube bench built from the same model. The light along the tube is what Tracer infers. [Press T to show the truth.] And this carrier is where the specimen really was. It's a small model you can read, not a black box.

[About 100 words.]

### Slide 7 · Board and timeline · 4:15 to 7:15 · Daniel, then Karissa · hands-on

**Screen:** The QR code for the demo, the address, and three steps: tap a row, drag the scrubber, tap Show the truth. Then the projector switches to the specimen tab.

**Daniel:**

Phones out. Scan this code, or type the address. [Wait about 20 seconds, and watch the room.] You're on the board. Every row is a specimen that's been collected and not received yet, ranked by expected harm. Tap any row to open its timeline, then drag the scrubber slowly to the right. Watch three things: the risk climbing while no scan comes, where Tracer thinks it is, and the moment the alert fires and who it goes to.

[About 70 words. Then switch the projector to the specimen tab, and give the room about a minute on their own first.]

**Karissa:**

Here's one on the screen. A stat tissue specimen from the emergency department. It sat at the lab's tube station for almost an hour. Tracer alerted at minute twenty, with a hundred minutes of its window left, and put it most likely at the tube station. It was received inside its window. Now tap Show the truth on yours, and see where your specimen really was.

[About 65 words. The rest of the three minutes is the room's.]

### Slide 8 · Beat Tracer · 7:15 to 11:45 · Daniel, then Karissa · hands-on

**Screen:** The QR code for the seeded game, so everyone plays the same three rounds, and the rules in three lines: one of six specimens will miss its window; tap it and press Call it; a wrong call uses one of your three alerts. Then the projector shows the game.

**Daniel:**

Now you get to compete with it. Scan this second code. It gives everyone in the room the same three rounds. Six specimens leave collection, and one of them will miss its window. Watch the receipt scans come in, tap the one you think is in trouble, and press Call it. Call too early and you might be wrong, and a wrong call uses one of your three alerts. Call too late, and you've given no warning. Ready? Start round one.

[About 80 words. Three rounds take about three minutes. Walk the room. If phones struggle, play one round on the projector and ask for a show of hands.]

**Karissa:**

[When most end screens are up.] Okay, look at your end screen. It puts your warning next to Tracer's, on the same rounds. Raise your hand if you beat Tracer. [Pause.] What did you notice that made you call it? [Take one or two answers.] That's the whole idea. The silence itself was telling you something. And the people who called early, some of you called the wrong one. That trade is what Daniel's going to show you next.

[About 65 words, plus the answers. About a minute.]

### Slide 9 · Why this design · 11:45 to 12:45 · Daniel · hands-on

**Screen:** First the demo's "Set the threshold" section on the projector, while the room drags it on their phones. Then the numbers slide: median warning at 6 alerts per 8-hour shift on the held-out synthetic runs. The plan's time-limit rule 30 minutes, a rule that alerts at a fixed share of the window 45, Tracer 55 (45 with equal tier weights).

**Daniel:**

That trade is the threshold. Scroll down to Set the threshold and drag it both ways. Lower, and Tracer warns earlier but alerts on specimens that would have arrived anyway. Higher, fewer alerts, less warning. So we judge Tracer by minutes of warning at a number of alerts a shift can handle, not by accuracy. [Switch to the numbers.] At six alerts a shift, Tracer gave fifty-five minutes of median warning, against thirty for a simple time limit. But a rule that alerts once a set share of the window has passed gets forty-five. With equal weights, Tracer only ties it. Most of the gain comes from using the window.

[About 105 words, over the room dragging the slider.]

### Slide 10 · How we built it · 12:45 to 13:30 · Karissa, then Daniel

**Screen:** "How we built and tested Tracer," in two rows. The data: briefs drawn by code (12,000 specimens, faults planted on purpose), then Claude writes the scans (each trail and its hidden true path), then a validator checks them (nine checks, 11,967 kept and frozen, $2.16 in all). The model: a three-part survival model is fit on 24 runs (normal trip times by type, hour and priority; trips that run far longer but arrive; specimens that never arrive), it is tested on 6 runs it never saw (2,378 specimens, 100 missed windows), and its numbers go to the demo you used (two files, no server). The hidden truth goes only to the test, never to the model.

**Karissa:**

Here's how we built it. We couldn't find public data with these scans, so we wrote our own. Code draws twelve thousand specimens from assumed timings and my policy table. Claude writes each one's scans and its hidden true path, and a validator keeps the ones that pass nine checks.

[About 50 words.]

**Daniel:**

Then a small survival model learns from twenty-four of the thirty runs: how long a normal trip takes for each kind of specimen and time of day, how often a trip runs far longer and still arrives, and how often one never does. We test it on the other six, and only that test sees the hidden truth.

[About 60 words.]

### Slide 11 · Limits and next steps · 13:30 to 14:15 · Karissa

**Screen:** What Tracer cannot see: a mislabeled or leaking specimen. Every specimen is synthetic, with no clinical review. 10 of the 44 policy rows are still assumed. At twice the volume, a simple window rule matches it. Next step: a real laboratory log with receipt scans.

**Karissa:**

Some honest limits. Tracer only watches time and place, so it can't see a mislabeled or leaking specimen. Every specimen you saw is synthetic, and there's been no clinical review of the data or the model. I wrote the policy table behind the stability windows, and ten of its forty-four rows still have no published source, so they're marked as assumptions. Tracer's lead also depends on the setting: at twice the volume, the simple window rule matches it. The real next step is to test it on a real laboratory log with receipt scans.

[About 95 words.]

### Slide 12 · Questions · 14:15 to 17:00 · Both

**Screen:** "Questions?" The demo's QR code, the demo address, the repository address, both names.

**Karissa:**

That's Tracer. The demo stays up at this address, and everything is on GitHub: the code, the data, and how we made it. We'd love your questions.

[About 30 words. Then the questions below. Stop at 17:00.]

## Prepared answers

The suggested speaker is in brackets. Either of us can take any of them. Keep each answer to two or three sentences, and say what we don't know.

1. **What if a scanner just misses a scan?** [Daniel] We didn't plant missed scans in the data, so that's a gap. A missed receipt scan would look like a late specimen, so Tracer would raise its risk and someone would check, which costs one alert.
2. **Why not just alert on everything after 30 minutes?** [Daniel] That's close to the simple rule we compared against. It ignores the window, and the windows in our table run from 60 minutes for unpreserved urine to days for a swab in transport medium, so one time limit is too late for some and far too early for others.
3. **What would someone actually do with an alert?** [Karissa] Go to the place Tracer names, like the tube station or the collecting unit, and look for it. Who gets which alert is an assumption we made, and a real lab would set it.
4. **Isn't it circular to test on data you generated?** [Daniel] Partly, yes. The data and the model share our assumptions, so a good result shows the method behaves as designed, not that it works in a real lab. That's why the next step is a real log with receipt scans.
5. **Why a simple model and not deep learning?** [Daniel] A lab scientist can read its numbers: how long a normal trip takes, how often a specimen stalls and still arrives, how often it never does. A gradient-boosted challenger is planned and hasn't been run.
6. **How often is it right about where the specimen is?** [Daniel] At 30 minutes of silence, the true place is in its top three about 88% of the time and its first guess about 55%, on synthetic data. So an alert says something is off more reliably than it says where.
7. **How many false alarms?** [Daniel] Plenty, by design. At 6 alerts a shift about a third of all specimens get an alert, and most of them arrive in time. Its risk scores are only slightly sharper than guessing the average rate, so the gain is in timing, not in sorting.
8. **Why does the clock stop at receipt?** [Karissa] Where a window ends is a clinical judgement, and receipt in the lab is the standard measure. That's what the policy table uses, so a delay at the bench after receipt is outside what Tracer watches.
9. **Is any real patient or hospital data in this?** [Karissa] No. We checked a public 100-patient MIMIC-IV extract and set it aside because it describes intensive care, not a general lab. No workplace data is used anywhere.
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
| About a third of specimens alerted; Brier 0.109 against 0.119 | `results/README.md` |
| 12,000 requested, 11,967 accepted, $2.16; 30 runs; nine checks | `datasets/manifest.json`, `src/generator/` |
| Fit on runs 0 to 23 (9,514 specimens); 6 held-out runs, 2,378 specimens, 100 breaches | `docs/demo/bundle.json` (model), `results/README.md` |
| The specimen on slide 7 | `docs/demo/heroes.json`, `SP-r27-000368` |

## Keeping the slides and this file together

`docs/talk/index.html` carries the same speaker lines as this file, word for word, and `tests/test_talk.py` fails if they drift apart, if the slide times stop adding up to the run of show's 17:00, or if a link on the slides points at a file that isn't there. Change the lines here and on the slides in the same commit.

`docs/talk/script.html` is built from this file. After any change here, run `python scripts/build_talk_script.py` and commit the page with it; `tests/test_talk.py` fails if the page and this file differ. `tests/test_talk_browser.py` opens the slides in Chrome at 1920 by 1080 and 1280 by 800 and fails if a slide runs into its footer, shrinks its type to fit, or shows text too small to read from the back of a room, or if the N, S and P keys stop working.

The QR code for Beat Tracer is `docs/talk/qr-beat.png`, for <https://scalinity.github.io/Tracer/demo/?seed=18#beat>. To write it again:

```sh
python -c "from pathlib import Path; from src.api import qr; qr.write(Path('docs/talk/qr-beat.png'), url='https://scalinity.github.io/Tracer/demo/?seed=18#beat')"
```
