# The Tracer talk, December 9

The in-class presentation for CAI 5724: the run of show, the slides, Daniel's lines and Karissa's outline. It runs 15:00, the length of the slot, with ten slides, each one idea a first-time viewer gets in about five seconds, and seven minutes of the class's hands on the demo.

- **Slides:** `docs/talk/index.html`, published at <https://scalinity.github.io/Tracer/talk/>. It opens from disk too, with no server.
- **Script:** `docs/talk/script.html`, published at <https://scalinity.github.io/Tracer/talk/script.html>. It is this file as a reading page for rehearsals and the day: a talk clock that lights the current beat and runs with the slides' clock, a speaker filter (`script.html?who=karissa` opens on Karissa's sections) and a print view of one speaker's part. It opens from disk too, and the presenter window links to it.
- **Demo:** <https://scalinity.github.io/Tracer/demo/>, the phone page. The class uses it on their phones for 7 of the 15 minutes: the 3D demo from its top section, Beat Tracer, and the warning section.
- **Speakers:** Daniel Escalante and Karissa Kocourek. Daniel's lines are written out below. Karissa's sections are outlined instead: the points to cover, in order, and about how long, and she writes her own words from them. `docs/talk/karissa-outline.md` holds her part on its own. If she cannot be there, Daniel covers her points. The speaking time is split about evenly: about 410 words for Karissa over slides 1, 2, 5, 6, 8 and 9, and about 355 for Daniel over slides 3, 4, 6 and 7, about 54 and 46 percent. The talk changes speaker six times.

Every slide carries a headline of eight words or fewer and one picture; the detail is in the notes below. The numbers from the demo and its results come from synthetic data that had no clinical review, and each slide that shows the demo or one of its numbers says so on screen. The others are not synthetic: the 60-minute urine window and the 44 policy rows come from Karissa's policy table and the guidelines it cites, and the 62% from a published audit. "Where the numbers come from" below gives the source of each.

## What the course asks for

From the revised guidelines (`docs/course/ai-design-project-guidelines.pdf`):

- About 15 minutes per team, in any format, covering three things: a brief discussion of the problem, an overview of the AI prototype and how it helps, and a hands-on class activity with the prototype.
- The interactive demo should take about 10 minutes and must be reachable at a public URL.
- The prototype does not have to be finished by the talk, but it has to work.

The rubric gives the talk 10 points: a clear narrative for a mixed audience (2), the rationale for the design (2), a correct understanding of how the system works (2), and class engagement and answers to questions (4). The demo's own 10 points include encouraging exploration with different inputs and settings (2). The run of show puts the class's hands on the demo early and keeps them there, because engagement carries the most points.

## Run of show

The story runs in this order: the problem, what Tracer does, watch it work, try it, does it work, its limits, questions. Hands-on time is the phone page, Beat Tracer and the warning taps: 7 minutes, plus the discussion in the questions.

| Clock | Slide | Beat | Who | On the screen | The room is |
|---|---|---|---|---|---|
| before 0:00 | 0 | Title | | Title slide; the 3D demo, the phone page and Beat Tracer open in tabs behind it | settling in |
| 0:00 to 1:00 | 1 | A specimen goes silent | Karissa | The hand vote: a collection scan, then silence, marked at 15 minutes, 30 minutes and 1 hour | voting with hands |
| 1:00 to 1:45 | 2 | Why it matters | Karissa | The 60-minute window for unpreserved urine, drawn as a bar that runs out | listening |
| 1:45 to 2:45 | 3 | What Tracer does | Daniel | Three questions, every 5 minutes: how late, where, who should look | listening |
| 2:45 to 3:30 | 4 | Watch it work | Daniel | The 3D demo playing the stalled tube inside the slide | watching |
| 3:30 to 6:00 | 5 | Try it on your phone | Karissa | QR code for the phone page and the four specimens it plays | on phones |
| 6:00 to 9:30 | 6 | Beat Tracer | Daniel, then Karissa | Seeded QR code and the one-line rule, then the game on the projector | playing |
| 9:30 to 10:30 | 7 | Does it work | Daniel | One chart: minutes of warning for TracerNet, our survival model and a simple deadline rule | tapping 1 and 6 alerts |
| 10:30 to 11:30 | 8 | Limits | Karissa | Three limits: synthetic data, blind to leaks and labels, a real lab log next | listening |
| 11:30 to 15:00 | 9 | Questions | Both | The phone page's QR code, the short explainer and the repository | asking |

Minute by minute, for whoever keeps time:

| Minute | What should be happening |
|---|---|
| 0 | Karissa opens with the hand vote. |
| 1 | Karissa on why it matters. Daniel on what Tracer does at 1:45. |
| 2 | Daniel's three questions. The 3D demo plays in the slide at 2:45. |
| 3 | Karissa puts the QR code up at 3:30. Phones out. |
| 4 | The room watches the four specimens on their phones. Karissa on what each one means in a lab. |
| 5 | The room keeps watching. Karissa asks which ones the lab would find in time today. |
| 6 | Seeded QR code up at 6:00. Daniel gives the rule in 30 seconds. |
| 7 | Rounds under way. Daniel walks the room. |
| 8 | Last rounds. Score screens up by about 8:30. |
| 9 | Karissa: who beat Tracer, and what made you call it. Daniel on the result at 9:30. |
| 10 | The room taps 1 and 6 alerts a shift. Karissa on the limits at 10:30. |
| 11 | Karissa's close at about 11:30, then questions. |
| 12 | Questions. |
| 13 | Questions. |
| 14 | Questions. Stop at 15:00 even mid-question, and offer to talk after. |

If the hands-on part runs long, cut in this order: the room's taps on slide 7 (Daniel says the one-shift and six-shift numbers over the chart), then Karissa's question to the room on slide 5 (point 4), then the second answer in the Beat Tracer debrief. If the room is still playing at 8:30, Karissa asks for hands from the round result screens instead: who called it before Tracer did. Never cut the result chart or the limits.

Beat Tracer is the longest beat, 3:30 of the 15 minutes, and with it the questions get 3:30. Without it the questions get 7:00 and the hands-on time is the phone page and the warning taps, 3:30. It stays in until we decide at rehearsal.

## Before the talk

Open these on the presenting laptop, each in its own tab, in this order:

1. The slides: <https://scalinity.github.io/Tracer/talk/>. Press **F** for full screen. Press **P** for the presenter window, with these notes and the clock, on the laptop screen.
2. The self-playing 3D demo, on the stalled specimen: <https://scalinity.github.io/Tracer/demo/play/?scene=2>. Slide 4 plays it inside the slide; this tab is the backup if the slide's frame stays blank.
3. The phone page the class opens: <https://scalinity.github.io/Tracer/demo/>.
4. Beat Tracer with the room's rounds: <https://scalinity.github.io/Tracer/demo/?seed=18#beat>.

Slide keys: right arrow, space or click for next; left arrow for back; **F** full screen; **P** presenter window; **N** notes on this screen (for rehearsal, not for the projector); **S** starts or resets the talk clock; the number keys 0 to 9 jump to each slide. The presenter window and the slides follow each other when both are open from the same address. The script page shares their talk clock when it is open in the same browser too: **S** on the slides or the script page starts or resets it everywhere, and Pause on the script page pauses it everywhere. From disk, use **N** instead.

The backups are the plan's: the five-minute screen recording, the phone hotspot, and an offline copy of `docs/` on the laptop. The slides open straight from disk, but the 3D demo inside slide 4 and the phone page need a server, so serve the copy first and open <http://localhost:8000/talk/>:

```sh
cd docs && python3 -m http.server 8000
```

## Slides and speaker notes

Words in brackets are stage directions, not lines. Daniel's lines are written out. Karissa's sections are numbered points to cover in order, and she says them in her own words. The word counts assume about 130 words a minute, a classroom pace with pauses, and a word count on one of Karissa's sections is a guide to its length, not a script.

### Slide 0 · Title · on screen before the clock starts

**Screen:** Tracer, with a scan dot and a dotted line running off into silence. Tracer warns the lab when a specimen goes quiet. Daniel Escalante and Karissa Kocourek, CAI 5724, December 9.

**Notes:** Nothing is said. Karissa starts when the instructor hands over.

### Slide 1 · A specimen goes silent · 0:00 to 1:00 · Karissa

**Screen:** "A specimen goes silent." When would you start to worry? A collection scan, a dotted silence marked at 15 minutes, 30 minutes and 1 hour, and an empty circle where the receipt scan should be.

**Karissa:**

1. A specimen is scanned when it is collected and again when the lab receives it; in between, nothing is recorded.

2. The hand vote: if it has not arrived, who would start to worry at 15 minutes, at 30, at an hour?

3. Their answer is the guess Tracer makes for every specimen, every 5 minutes, all day.

[In her own words, about 60 words. Pause for each show of hands.]

### Slide 2 · Why it matters · 1:00 to 1:45 · Karissa

**Screen:** "Every specimen is on a clock." 60 minutes for unpreserved urine at room temperature, drawn as a bar that runs out at a red line. Missed often means collecting again. Source: the IDSA and ASM 2024 guide.

**Karissa:**

1. A microbiology specimen is on a clock: unpreserved urine has about an hour at room temperature before the colony count stops meaning anything.

2. Spinal fluid must come straight to the lab and never be refrigerated.

3. A missed window often means rejection and a new collection: another needle stick or lumbar puncture, and hours of delay.

4. Most lab errors happen before testing: about 62% in one stat laboratory's audit.

5. Today a missing specimen is noticed only when someone calls for the result.

[In her own words, about 85 words.]

### Slide 3 · What Tracer does · 1:45 to 2:45 · Daniel

**Screen:** "Every 5 minutes, Tracer asks three questions." Three pictures: how late is it (silence running past where trips like it usually arrive), where is it likely (three places, the likeliest drawn largest), who should go look (an alert bell).

**Daniel:**

Public hospital data stamps a specimen only twice, so we generated our own, and every number you'll see is synthetic. On that data we built Tracer. Every five minutes, for every specimen the lab is still waiting on, it asks three questions. How late is this one, compared with trips like it? Where is it most likely to be: still on the ward, in the tube system, or waiting at a tube station? And who should go and look? When the risk is high enough, Tracer alerts the person who can check that place, while there is still time. It needs no new hardware, only the scans the lab already records.

[About 110 words.]

### Slide 4 · Watch it work · 2:45 to 3:30 · Daniel

**Screen:** "Watch Tracer catch a stalled tube." The 3D demo plays the stalled specimen inside the slide, with a link to open it on its own.

**Daniel:**

Here is one specimen, playing by itself. Its tube stalls on the way, and its deadline is two hours. Watch the risk bar: how likely it is to miss its deadline. [Point at the bar as it climbs.] At twenty-five minutes of silence, Tracer raises the alert. That is ninety-five minutes before the deadline, plenty of time to go and find it. Here it is scanned in at about sixty-seven minutes, well inside its window. Every alert you see is TracerNet's own, stored ahead of time, on synthetic data.

[About 80 words. If the slide's demo stays blank, click Open the 3D demo or switch to its tab.]

### Slide 5 · Try it on your phone · 3:30 to 6:00 · Karissa · hands-on

**Screen:** "Your turn: phones out." The QR code for the phone page, its address, and the four specimens the 3D demo plays: on time, stalled, lost, never sent.

**Karissa:**

1. Phones out: scan the code or type the address, and tap Watch one go quiet to open the 3D demo.

2. It plays four specimens in turn: on time, stalled, lost and never sent.

3. What each one is in a real lab: a stalled tube sits somewhere on its way, a lost one never turns up, and one never sent is still on the ward.

4. Ask the room: which of the four would the lab find in time today?

5. Tracer alerted on the three late ones, 90 to 95 minutes before the deadline, and sent no alert for the one on time.

[In her own words, about 70 words. Wait about 20 seconds after point 1. The rest of the time is the room's.]

### Slide 6 · Beat Tracer · 6:00 to 9:30 · Daniel, then Karissa · hands-on

**Screen:** "Beat Tracer." The QR code for the seeded game, so everyone plays the same rounds, and the rule in one line: One will be late. Tap it before Tracer does. Then the projector shows the game.

**Daniel:**

Now you get to compete with it. Scan this second code. Everyone in the room gets the same rounds. Each round, six specimens leave, and one will be late. Tap it before Tracer does. You get one tap a round. Tap too early and you may pick the wrong one. Wait too long and you have given no warning. Go.

[About 60 words. The rounds take about a minute and a half. Walk the room. If phones struggle, play one round on the projector and ask for a show of hands.]

**Karissa:**

1. The end screen puts their warning next to Tracer's, on the same rounds.

2. Hands up if you beat Tracer.

3. What made you call it? Take one or two answers.

4. The idea: the silence itself was telling them something.

5. Some early callers picked the wrong one, and that trade is what Daniel shows next.

[In her own words, about 65 words plus the answers. Start when most end screens are up. About a minute.]

### Slide 7 · Does it work · 9:30 to 10:30 · Daniel · hands-on

**Screen:** "TracerNet gives the most warning." One chart: minutes of warning per late specimen, at 6 alerts a shift. TracerNet 23.3, our survival model 13.6, a simple deadline rule 7.6. 776 fresh synthetic lab weeks, scored once.

**Daniel:**

So does it work? We measured what a lab would feel: minutes of warning per late specimen, at six alerts a shift. TracerNet, our neural network, gave twenty-three minutes. Our survival model gave about fourteen, and a simple deadline rule under eight. We wrote down the rule TracerNet had to pass before these test weeks existed, then scored them once. [The room: on the phone page, tap How much warning?] Tap one alert a shift, then six. At one, TracerNet gives about three minutes. At six, twenty-three. More alerts buy more warning, but about half of them, about three a shift, go to specimens that arrive in time. That is the trade.

[About 105 words, over the room tapping.]

### Slide 8 · Limits · 10:30 to 11:30 · Karissa

**Screen:** "What Tracer cannot tell you." Three limits side by side: synthetic data, no clinical review; blind to leaks and wrong labels; next, a real lab's scan log.

**Karissa:**

1. Every specimen is synthetic, with no clinical review of the data or the model.

2. Tracer watches only time and place, so it cannot see a mislabeled or leaking specimen.

3. About half of TracerNet's alerts at 6 a shift go to specimens that arrive in time, about three a shift.

4. The policy table behind the windows: 10 of its 44 rows still have no published source, so they are marked as assumptions.

5. The next step: test it on a real laboratory's log with receipt scans.

[In her own words, about 100 words.]

### Slide 9 · Questions · 11:30 to 15:00 · Both

**Screen:** "Questions?" The phone page's QR code, and three addresses: try it (the phone page), read it (the short explainer), code (the repository).

**Karissa:**

1. That's Tracer: it warns the lab when a specimen goes quiet.

2. The demo stays up at this address, and the short explainer and the code are linked here too.

3. Invite their questions.

[In her own words, about 30 words. Then the questions below. Stop at 15:00.]

## Prepared answers

The suggested speaker is in brackets. Either of us can take any of them. Daniel's answers are written out; Karissa's are points she answers in her own words. Keep each answer to two or three sentences, and say what we don't know.

1. **What if a scanner just misses a scan?** [Daniel] We didn't plant missed scans in the data, so that's a gap. A missed receipt scan would look like a late specimen, so Tracer would raise its risk and someone would check, which costs one alert.
2. **Why not just alert on everything after 30 minutes?** [Daniel] That's close to the simple rule we compared against. It ignores the window, and the windows in our table run from 60 minutes for unpreserved urine to days for a swab in transport medium, so one time limit is too late for some and far too early for others.
3. **What would someone actually do with an alert?** [Karissa] Points: go to the place Tracer names, like the tube station or the collecting unit, and look for it; who gets which alert is our assumption, and a real lab would set it.
4. **Isn't it circular to test on data you generated?** [Daniel] Partly, yes. The data and the model share our assumptions, so a good result shows the method behaves as designed, not that it works in a real lab. That's why the next step is a real log with receipt scans.
5. **Why a simple model and not deep learning?** [Daniel] We have both. TracerNet, a neural network, now runs Tracer: it passed a rule we wrote down before testing it once on fresh simulated weeks. The survival model stays as a baseline a lab scientist can read: how long a normal trip takes, how often one runs far longer and still arrives, how often it never does. The demo shows TracerNet's alerts, worked out ahead of time, so nothing runs on your phone.
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
| About 62% (61.9%) of errors before analysis in one stat laboratory; spinal fluid handling | `docs/problem-evidence.md` (Carraro and Plebani 2007; Miller et al., IDSA and ASM 2018) |
| Rejection, recollection and chasing specimens | `docs/project-spec.md`, the problem and who is affected |
| 60-minute window for unpreserved urine (IDSA and ASM 2024); 44 policy rows, 10 assumed; windows from 60 minutes to days | `config/breach_policy.yaml`, from Karissa's policy table |
| A public 100-patient MIMIC-IV extract, all intensive care | `docs/mimic-demo-findings.md` |
| Every 5 minutes | `docs/neural/spec.md`, the clock tick |
| The stalled tube: alert at 25 minutes of silence, 95 minutes before its 120-minute deadline, scanned in at about 67 minutes; the other three: on time with no alert, lost with an alert 90 minutes ahead, never sent with an alert 95 minutes ahead | `visuals/exhibit-3d/src/model/tracernet-stories.json`, TracerNet's stored alerts |
| TracerNet 23.3 minutes against 13.6 (our survival model, refit to each lab week) and 7.6 (the simple deadline rule) on 776 fresh lab weeks, the average warning per late specimen at 6 alerts per 8-hour shift and 3-2-1 weights, a miss counting as zero and each warning capped at 2 hours; the rule it passed, written down before the fresh weeks existed; scored once | `docs/neural/results-v2.md`, `results/neural/test_fresh/evaluation.json`; the rule in `docs/neural/preregistration-v2.md` |
| TracerNet at 1 alert a shift: 3.1 minutes of warning | `results/neural/test_fresh/evaluation.json`, `operating_points` at 1 per shift, 3-2-1 weights |
| About half of TracerNet's alerts false, about three a shift (2.9), the fewest per alert | `docs/neural/results-v2.md`, G3 |
| Top-3 88% and top-1 55% at 30 minutes | `results/location-accuracy.json` |
| 12,000 requested, 11,967 accepted, $2.16; nine checks | `datasets/manifest.json`, `src/generator/` |

## Keeping the slides and this file together

`docs/talk/index.html` carries the same speaker notes as this file, word for word, and `tests/test_talk.py` fails if they drift apart, if the slide times stop adding up to the run of show's 15:00, if a link on the slides points at a file that isn't there, if a headline runs past eight words or a slide shows more than about 23, or if the result chart stops showing exactly 23.3, 13.6 and 7.6. Change the notes here and on the slides in the same commit. `docs/talk/karissa-outline.md` carries Karissa's points too, and the same tests fail if it drifts from this file.

`docs/talk/script.html` is built from this file. After any change here, run `python scripts/build_talk_script.py` and commit the page with it; `tests/test_talk.py` fails if the page and this file differ. `tests/test_talk_browser.py` opens the slides in Chrome at 1920 by 1080, 1280 by 800 and a 390 by 844 phone and fails if a slide runs into its footer or off the screen, shrinks its type to fit on the projector, or shows text too small to read from the back of a room, or if the N, S and P keys stop working.

The QR code for Beat Tracer is `docs/talk/qr-beat.png`, for <https://scalinity.github.io/Tracer/demo/?seed=18#beat>. To write it again:

```sh
python -c "from pathlib import Path; from src.api import qr; qr.write(Path('docs/talk/qr-beat.png'), url='https://scalinity.github.io/Tracer/demo/?seed=18#beat')"
```
