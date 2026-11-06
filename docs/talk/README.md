# The Tracer talk, November 18

The in-class presentation for CAI 5724: the run of show, the slides and every line we say, timed to 15 minutes.

- **Slides:** `docs/talk/index.html`, published at <https://scalinity.github.io/Tracer/talk/>. It opens from disk too, with no server.
- **Demo:** <https://scalinity.github.io/Tracer/demo/>. The class uses it on their phones for about 8 and a half minutes of the 15.
- **Speakers:** Daniel Escalante and Karissa Kocourek. Karissa's lines are written out below in full, so she has nothing to prepare beyond reading them. If she cannot be there, Daniel reads them as written.

Every number in the talk comes from synthetic data that had no clinical review, and the slides say so on each screen that shows one.

## What the course asks for

From the revised guidelines (`docs/course/ai-design-project-guidelines.pdf`):

- About 15 minutes per team, in any format, covering three things: a brief discussion of the problem, an overview of the AI prototype and how it helps, and a hands-on class activity with the prototype.
- The interactive demo should take about 10 minutes and must be reachable at a public URL.
- The prototype does not have to be finished by the talk, but it has to work.

The rubric gives the talk 10 points: a clear narrative for a mixed audience (2), the rationale for the design (2), a correct understanding of how the system works (2), and class engagement and answers to questions (4). The demo's own 10 points include encouraging exploration with different inputs and settings (2). The run of show puts the class's hands on the demo early and keeps them there, because engagement carries the most points.

## Run of show

The beats and their lengths are the run of show in `visuals/plan.html` at a 15-minute slot. Hands-on time is the board, Beat Tracer and the threshold slider: 8.5 minutes, plus the discussion in the questions.

| Clock | Slide | Beat | Who | On the screen | The room is |
|---|---|---|---|---|---|
| before 0:00 | 0 | Title | | Title slide; demo, 3D bench and the specimen link open in tabs behind it | settling in |
| 0:00 to 0:30 | 1 | The silent tube | Karissa | The 3D bench with the truth hidden | voting with hands |
| 0:30 to 1:30 | 2 | Why it matters | Karissa | Problem slide | listening |
| 1:30 to 2:00 | 3 | The missing data | Daniel | Two timestamps, 80.1 hours apart | listening |
| 2:00 to 3:00 | 4 | How Tracer works | Daniel | Three questions, then the 3D bench with the truth shown | listening |
| 3:00 to 6:00 | 5 | Board and timeline | Daniel | QR code, then one specimen's timeline on the projector | on phones |
| 6:00 to 10:30 | 6 | Beat Tracer | Daniel, then Karissa | Seeded QR code, then the game on the projector | playing |
| 10:30 to 11:30 | 7 | Why this design | Daniel | The threshold slider, then the numbers slide | dragging the slider |
| 11:30 to 12:15 | 8 | Limits and next steps | Karissa | Limits slide | listening |
| 12:15 to 15:00 | 9 | Questions | Both | Closing slide with the QR code and the repo | asking |

Minute by minute, for whoever keeps time:

| Minute | What should be happening |
|---|---|
| 0 | Karissa opens on the 3D bench. The hand vote. |
| 1 | Karissa on the problem slide. |
| 2 | Daniel on the missing data, then the three questions. |
| 3 | QR code up. Phones out. Everyone on the board by about 3:20. |
| 4 | The room is scrubbing a timeline. Daniel walks one specimen on the projector. |
| 5 | "Show the truth" on their own specimen. |
| 6 | Seeded QR code up. Daniel explains Beat Tracer in 30 seconds. Round 1 by 6:40. |
| 7 | Round 1 or 2. |
| 8 | Round 2 or 3. |
| 9 | Round 3 ends. End screens up. |
| 10 | Karissa: who beat Tracer, and what made you call it. |
| 11 | Daniel on the threshold slider, then the numbers. |
| 12 | Karissa on the limits. Daniel's closing line at about 12:15. |
| 13 | Questions. |
| 14 | Questions. Stop at 15:00 even mid-question, and offer to talk after. |

If the hands-on part runs long, cut in this order: the projector walkthrough on slide 5 (the room keeps exploring alone), then the numbers slide in beat 7 (say the one headline sentence and stay on the slider). The game shows its end screen only after round 3, so if the room is still on round 2 at 9:30, Karissa asks for hands from the round result screens instead: who called it before Tracer did. Never cut the board, the game or the limits.

## Before the talk

Open these on the presenting laptop, each in its own tab, in this order:

1. The slides: <https://scalinity.github.io/Tracer/talk/>. Press **F** for full screen. Press **P** for the presenter window, with these notes and the clock, on the laptop screen.
2. The 3D bench with the truth hidden: <https://scalinity.github.io/Tracer/demo/exhibit/?truth=0>. This address skips the opening tour and starts with the carrier hidden; **T** shows it on slide 4.
3. The demo board: <https://scalinity.github.io/Tracer/demo/>.
4. The specimen for slide 5: <https://scalinity.github.io/Tracer/demo/#SP-r27-000368>. The address opens that specimen's timeline.
5. Beat Tracer with the room's rounds: <https://scalinity.github.io/Tracer/demo/?seed=18#beat>.

Slide keys: right arrow, space or click for next; left arrow for back; **F** full screen; **P** presenter window; **N** notes on this screen (for rehearsal, not for the projector); **S** starts or resets the talk clock; the number keys 0 to 9 jump to a slide. The presenter window and the slides follow each other when both are open from the same address. From disk, use **N** instead.

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

**Screen:** The 3D bench, with the truth hidden. The slide behind it reads "A tube leaves the ward." and holds the bench's link.

**Karissa:**

A tube leaves the ward. Nothing beeps, and nothing gets logged. Nobody knows yet if it's late. So, raise your hand if you'd start to worry after fifteen minutes. Thirty? An hour? Hold on to that number, because that's the guess Tracer has to make, for every specimen, all day.

[About 50 words. Pause for each show of hands.]

### Slide 2 · Why it matters · 0:30 to 1:30 · Karissa

**Screen:** Three facts. 61.9% of errors in one stat laboratory's audit happened before analysis (Carraro and Plebani, Clin Chem 2007). Unpreserved urine at room temperature: a 60-minute window in our policy table, from the IDSA and ASM 2024 guide. A missed window usually means a new specimen: another needle stick or lumbar puncture, and hours of delay.

**Karissa:**

Most lab errors don't happen during testing. In one stat laboratory's audit, about sixty-two percent of errors happened before the specimen was ever analyzed. And a microbiology specimen is on a clock. Unpreserved urine at room temperature has about an hour before bacteria overgrow and the colony count stops meaning anything. Spinal fluid has to come straight to the lab, and it must not be refrigerated. When a specimen misses its window, it's often rejected and has to be collected again. For the patient, that's another needle stick or another lumbar puncture, hours of delay, and sometimes longer on broad antibiotics while the team waits for a result. And the lab has no record of where a specimen is between collection and receipt, so a missing one often isn't noticed until someone calls asking for the result.

[About 135 words.]

### Slide 3 · The missing data · 1:30 to 2:00 · Daniel

**Screen:** Two dots on a line, "Collected" and "Result filed", with nothing between them. Median 80.1 hours apart, across 1,291 specimens in the public MIMIC-IV demo extract. No receipt scan, no transport record.

**Daniel:**

So why not learn this from hospital data? The best-known public one, MIMIC-IV, stamps a microbiology specimen twice: when it's collected and when the result is filed. Those stamps are a median of eighty hours apart, with nothing in between. There's no field for the journey at all. So we generated our own data, and every number you'll see is an assumption we can change.

[About 65 words.]

### Slide 4 · How Tracer works · 2:00 to 3:00 · Daniel

**Screen:** "Silence is evidence." Three questions Tracer asks every 5 minutes about every specimen not yet received: How unusual is this much silence for this kind of specimen? Where is it most likely to be? Would an alert now still leave time to act? Then: an alert, routed to the role that can check that place. Switch to the 3D bench for the second half.

**Daniel:**

Here's the idea. Silence is evidence. Every five minutes, Tracer asks three questions about every specimen the lab hasn't received. How unusual is this much silence, for this kind of specimen? Where is it most likely to be right now: still on the unit, in the tube, or sitting at the tube station? And if we alert now, is there still time to act before its window closes? [Switch to the 3D bench.] This is a tube bench built from the same model. The light along the tube is what Tracer infers. [Press T to show the truth.] And this carrier is where the specimen really was. When the risk crosses a threshold, Tracer alerts the person who can check the likely place. It's a small model you can read, not a black box.

[About 125 words.]

### Slide 5 · Board and timeline · 3:00 to 6:00 · Daniel · hands-on

**Screen:** The QR code for the demo, the address, and three steps: tap a row, drag the scrubber, tap Show the truth. Then the projector switches to the specimen tab.

**Daniel:**

Phones out. Scan this code, or type the address. [Wait about 20 seconds, and watch the room.] You're on the board. Every row is a specimen that's been collected and not received yet, ranked by expected harm. Tap any row to open its timeline, then drag the scrubber slowly to the right. Watch three things: the risk climbing while no scan comes, where Tracer thinks it is, and the moment the alert fires and who it goes to.

[Switch the projector to the specimen tab. Give the room about a minute on their own first.]

Here's one on the screen. A stat tissue specimen from the emergency department. It sat at the lab's tube station for almost an hour. Tracer alerted at minute twenty, with a hundred minutes of its window left, and put it most likely at the tube station. It was received inside its window. Now tap Show the truth on yours, and see where your specimen really was.

[About 135 words over three minutes, with the rest left for the room.]

### Slide 6 · Beat Tracer · 6:00 to 10:30 · Daniel, then Karissa · hands-on

**Screen:** The QR code for the seeded game, so everyone plays the same three rounds, and the rules in three lines: one of six specimens will miss its window; tap it and press Call it; a wrong call uses one of your three alerts. Then the projector shows the game.

**Daniel:**

Now you get to compete with it. Scan this second code. It gives everyone in the room the same three rounds. Six specimens leave collection, and one of them will miss its window. Watch the receipt scans come in, tap the one you think is in trouble, and press Call it. Call too early and you might be wrong, and a wrong call uses one of your three alerts. Call too late, and you've given no warning. Ready? Start round one.

[About 80 words. Three rounds take about three minutes. Walk the room. If phones struggle, play one round on the projector and ask for a show of hands.]

**Karissa:**

[When most end screens are up.] Okay, look at your end screen. It puts your warning next to Tracer's, on the same rounds. Raise your hand if you beat Tracer. [Pause.] What did you notice that made you call it? [Take one or two answers.] That's the whole idea. The silence itself was telling you something. And the people who called early, some of you called the wrong one. That trade is what Daniel's going to show you next.

[About 65 words, plus the answers. About a minute.]

### Slide 7 · Why this design · 10:30 to 11:30 · Daniel · hands-on

**Screen:** First the demo's "Set the threshold" section on the projector, while the room drags it on their phones. Then the numbers slide: median warning at 6 alerts per 8-hour shift on the held-out synthetic runs. The plan's time-limit rule 30 minutes, a rule that alerts at a fixed share of the window 45, Tracer 55 (45 with equal tier weights).

**Daniel:**

That trade is the threshold. Scroll down to Set the threshold and drag it both ways. Lower, and Tracer warns earlier but alerts on specimens that would have arrived anyway. Higher, fewer alerts, less warning. So we judge Tracer by minutes of warning at a number of alerts a shift can handle, not by accuracy. [Switch to the numbers.] At six alerts a shift, Tracer gave fifty-five minutes of median warning, against thirty for a simple time limit. But a rule that alerts once a set share of the window has passed gets forty-five, and with equal weights Tracer only ties it. Most of the gain comes from using the window.

[About 105 words, over the room dragging the slider.]

### Slide 8 · Limits and next steps · 11:30 to 12:15 · Karissa

**Screen:** What Tracer cannot see: a mislabeled or leaking specimen. Every specimen is synthetic, with no clinical review. 10 of the 44 policy rows are still assumed. At twice the volume, a simple window rule matches it. Next step: a real laboratory log with receipt scans.

**Karissa:**

Some honest limits. Tracer only watches time and place, so it can't see a mislabeled or leaking specimen. Every specimen you saw is synthetic, and there's been no clinical review of the data or the model. I wrote the policy table behind the stability windows, and ten of its forty-four rows still have no published source, so they're marked as assumptions. Tracer's lead also depends on the setting: at twice the volume, the simple window rule matches it. The real next step is to test it on a real laboratory log with receipt scans.

[About 95 words.]

### Slide 9 · Questions · 12:15 to 15:00 · Both

**Screen:** "Questions?" The demo's QR code, the demo address, the repository address, both names.

**Daniel:**

That's Tracer. The demo stays up at this address, and everything is on GitHub: the code, the data, and how we made it. We'd love your questions.

[About 30 words. Then the questions below. Stop at 15:00.]

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
| 12,000 requested, 11,967 accepted, $2.16 | `datasets/manifest.json` |
| The specimen on slide 5 | `docs/demo/heroes.json`, `SP-r27-000368` |

## Keeping the slides and this file together

`docs/talk/index.html` carries the same speaker lines as this file, word for word, and `tests/test_talk.py` fails if they drift apart, if the slide times stop adding up to 15 minutes, or if a link on the slides points at a file that isn't there. Change the lines here and on the slides in the same commit.

The QR code for Beat Tracer is `docs/talk/qr-beat.png`, for <https://scalinity.github.io/Tracer/demo/?seed=18#beat>. To write it again:

```sh
python -c "from pathlib import Path; from src.api import qr; qr.write(Path('docs/talk/qr-beat.png'), url='https://scalinity.github.io/Tracer/demo/?seed=18#beat')"
```
