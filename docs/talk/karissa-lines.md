# Karissa's lines

### Slide 1 · The silent tube · 0:00 to 0:30 · Karissa

**Karissa:**

A tube leaves the ward. Nothing beeps, and nothing gets logged. Nobody knows yet if it's late. So, raise your hand if you'd start to worry after fifteen minutes. Thirty? An hour? Hold on to that number, because that's the guess Tracer has to make, for every specimen, all day.

[About 50 words. Pause for each show of hands.]

### Slide 2 · Why it matters · 0:30 to 1:30 · Karissa

**Karissa:**

Most lab errors don't happen during testing. In one stat laboratory's audit, about sixty-two percent of errors happened before the specimen was ever analyzed. And a microbiology specimen is on a clock. Unpreserved urine at room temperature has about an hour before bacteria overgrow and the colony count stops meaning anything. Spinal fluid has to come straight to the lab, and it must not be refrigerated. When a specimen misses its window, it's often rejected and has to be collected again. For the patient, that's another needle stick or another lumbar puncture, and hours of delay. And the lab has no record of where a specimen is between collection and receipt, so a missing one often isn't noticed until someone calls asking for the result.

[About 125 words.]

### Slide 5 · What Tracer is built to fix · 2:45 to 3:30 · Karissa

**Karissa:**

So what does that change in the lab? Today, the lab's screens only show what's been scanned, so a missing specimen stays silent until someone calls asking for the result. A fixed time limit is too late for urine and too early for a swab. And tracking tags would need a tag on every tube and a reader in every hallway. Tracer is built to close those gaps with data the lab already has. The aim is fewer repeat collections and quicker results for the patient, and less time chasing for the lab.

[About 90 words.]

### Slide 7 · Board and timeline · 4:15 to 7:15 · Daniel, then Karissa · hands-on

**Karissa:**

Here's one on the screen. A stat tissue specimen from the emergency department. It sat at the lab's tube station for almost an hour. Tracer alerted at minute twenty, with a hundred minutes of its window left, and put it most likely at the tube station. It was received inside its window. Now tap Show the truth on yours, and see where your specimen really was.

[About 65 words. The rest of the three minutes is the room's.]

### Slide 8 · Beat Tracer · 7:15 to 11:45 · Daniel, then Karissa · hands-on

**Karissa:**

[When most end screens are up.] Okay, look at your end screen. It puts your warning next to Tracer's, on the same rounds. Raise your hand if you beat Tracer. [Pause.] What did you notice that made you call it? [Take one or two answers.] That's the whole idea. The silence itself was telling you something. And the people who called early, some of you called the wrong one. That trade is what Daniel's going to show you next.

[About 65 words, plus the answers. About a minute.]

### Slide 10 · How we built it · 12:45 to 13:30 · Karissa, then Daniel

**Karissa:**

Here's how we built it. We couldn't find public data with these scans, so we wrote our own. Code draws twelve thousand specimens from assumed timings and my policy table. Claude writes each one's scans and its hidden true path, and a validator keeps the ones that pass nine checks.

[About 50 words.]

### Slide 11 · Limits and next steps · 13:30 to 14:15 · Karissa

**Karissa:**

Some honest limits. Tracer only watches time and place, so it can't see a mislabeled or leaking specimen. Every specimen you saw is synthetic, and there's been no clinical review of the data or the model. I wrote the policy table behind the stability windows, and ten of its forty-four rows still have no published source, so they're marked as assumptions. Tracer's lead also depends on the setting: at twice the volume, the simple window rule matches it. The real next step is to test it on a real laboratory log with receipt scans.

[About 95 words.]

### Slide 12 · Questions · 14:15 to 17:00 · Both

**Karissa:**

That's Tracer. The demo stays up at this address, and everything is on GitHub: the code, the data, and how we made it. We'd love your questions.

[About 30 words. Then the questions below. Stop at 17:00.]

## Prepared answers

3. **What would someone actually do with an alert?** [Karissa] Go to the place Tracer names, like the tube station or the collecting unit, and look for it. Who gets which alert is an assumption we made, and a real lab would set it.

8. **Why does the clock stop at receipt?** [Karissa] Where a window ends is a clinical judgement, and receipt in the lab is the standard measure. That's what the policy table uses, so a delay at the bench after receipt is outside what Tracer watches.

9. **Is any real patient or hospital data in this?** [Karissa] No. We checked a public 100-patient MIMIC-IV extract and set it aside because it describes intensive care, not a general lab. No workplace data is used anywhere.
