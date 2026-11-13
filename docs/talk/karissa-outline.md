# Karissa's outline

Your four sections of the Tracer talk on December 9, in the order they come. Under each slide are the points to cover, in order; the words are yours. The note in brackets says about how long it runs, and the times are the clock on the slides. The full run of show, with Daniel's lines around yours, is in `docs/talk/README.md` and at <https://scalinity.github.io/Tracer/talk/script.html?who=karissa>.

## 1. Opening and the problem

*Slides 1 and 2, 0:00 to 1:15.* You open the talk. Get the room to feel the silent tube, then say why it matters.

### Slide 1 · The silent tube · 0:00 to 0:30 · Karissa

1. A tube has left the ward: nothing beeps, nothing is logged, and nobody knows yet if it is late.

2. The hand vote: who would start to worry at 15 minutes, at 30, at an hour?

3. Their number is the guess Tracer has to make, for every specimen, all day.

[About 50 words. Pause for each show of hands.]

### Slide 2 · Why it matters · 0:30 to 1:15 · Karissa

1. Most lab errors happen before testing: about 62% in one stat laboratory's audit.

2. A microbiology specimen is on a clock: unpreserved urine has about an hour at room temperature, and spinal fluid must come straight to the lab and never be refrigerated.

3. A missed window often means rejection and a new collection: another needle stick or lumbar puncture, and hours of delay.

4. Between collection and receipt the lab has no record, so a missing specimen is noticed only when someone calls for the result.

[About 95 words.]

## 2. What Tracer is built to fix

*Slide 5, 2:30 to 3:00.* After Daniel introduces Tracer, you say what it changes in the lab.

### Slide 5 · What Tracer is built to fix · 2:30 to 3:00 · Karissa

1. Today a missing specimen stays silent until someone calls for the result.

2. One fixed time limit is too late for urine and too early for a swab.

3. Tracking tags would need a tag on every tube and a reader in every hallway.

4. Tracer closes those gaps with the scans the lab already records.

5. The aim: fewer repeat collections, quicker results for the patient, and less time chasing for the lab.

[About 65 words.]

## 3. Hosting the phone activity

*Slides 7 and 8, 3:45 to 6:15 and about 9:50 to 10:45.* You run the room's hands-on time: you get the phones out and walk one specimen, and after Daniel starts the game you lead the debrief.

### Slide 7 · Board and timeline · 3:45 to 6:15 · Karissa · hands-on

1. Phones out: scan the code or type the address.

2. What the board is: every specimen collected and not yet received, ranked by expected harm.

3. What to do: tap a row, drag the scrubber slowly to the right, and watch the risk climb, where Tracer thinks it is, and when the alert fires and who gets it.

4. On the projector, one specimen: a stat tissue specimen from the emergency department that sat at the lab's tube station for almost an hour.

5. Tracer alerted at minute 20 with 100 minutes of its window left, put it most likely at the tube station, and it was received in time.

6. Their turn: tap Show the truth and see where their own specimen really was.

[About 120 words. Wait about 20 seconds after point 1. After point 3, give the room about a minute on their own, then Daniel switches the projector to the specimen tab. The rest of the time is the room's.]

### Slide 8 · Beat Tracer · 6:15 to 10:45 · Daniel, then Karissa · hands-on

1. The end screen puts their warning next to Tracer's, on the same rounds.

2. Hands up if you beat Tracer.

3. What made you call it? Take one or two answers.

4. The idea: the silence itself was telling them something.

5. Some early callers picked the wrong one, and that trade is what Daniel shows next.

[About 65 words plus the answers. Start when most end screens are up. About a minute.]

## 4. The data, the limits and the close

*Slides 10, 12 and 13, from 11:45.* You say how we made the data, give the honest limits, and close before questions.

### Slide 10 · How we built it · 11:45 to 12:30 · Karissa, then Daniel

1. No public data has these scans, so we made our own.

2. Code draws 12,000 specimens from assumed timings and the policy table.

3. Claude writes each one's scans and its hidden true path, and a validator keeps the ones that pass nine checks.

[About 45 words.]

### Slide 12 · Limits and next steps · 13:00 to 13:45 · Karissa

1. Tracer watches only time and place, so it cannot see a mislabeled or leaking specimen.

2. Every specimen is synthetic, with no clinical review of the data or the model.

3. The policy table behind the stability windows: 10 of its 44 rows still have no published source, so they are marked as assumptions.

4. Tracer's lead depends on the setting: at twice the volume, the simple window rule matches it.

5. The next step: test it on a real laboratory log with receipt scans.

[About 90 words.]

### Slide 13 · Questions · 13:45 to 15:00 · Both

1. That's Tracer.

2. The demo stays up at this address, and the code, the data and how we made it are on GitHub.

3. Invite their questions.

[About 30 words. Then the questions below. Stop at 15:00.]

## Questions you may take

If one of these comes up, the points to make. Either of us can take any question.

- **What would someone actually do with an alert?** Points: go to the place Tracer names, like the tube station or the collecting unit, and look for it; who gets which alert is our assumption, and a real lab would set it.
- **Why does the clock stop at receipt?** Points: where a window ends is a clinical judgement; receipt in the lab is the standard measure, and it is what the policy table uses; a delay at the bench after receipt is outside what Tracer watches.
- **Is any real patient or hospital data in this?** Points: no; a public 100-patient MIMIC-IV extract was checked and set aside, because it describes intensive care, not a general lab; no workplace data is used anywhere.
