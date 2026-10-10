# AI Solution Architecture

Readable mirror of `Milestone 2 - AI Solution Architecture.docx`, Milestone 2, due Wednesday, September 30 at 11:59 PM, one submission per group. The template's instructions are omitted here; the `.docx` carries them.

**Team:** Karissa Kocourek and Daniel Escalante

## 1. AI Design Project Update (2-3 sentences)

Since Milestone 1, Karissa's experience at the bench shifted our focus from specimens that degrade during ordinary delay to specimens that are lost or stalled, because stability windows run from 24 hours to 7 days while transport takes minutes to hours. The stability clock runs from collection to plating. We also found that the unobserved interval includes the time a specimen waits on the laboratory counter before it is received.

## 2. AI System Objective (1-2 sentences)

Tracer detects specimens that are lost or stalled between collection and plating early, estimates where each one most likely is, and directs the right person to it before recollection becomes necessary. It closes the gap identified in Milestone 1, where the interval between collection and receipt is unobserved and a specimen that never arrives produces no signal until someone notices it is missing.

## 3. Form Factor (2-4 sentences)

Tracer is a background service that continuously reads specimen events from the laboratory information system and carrier events from the pneumatic tube system. It displays a live board at the specimen receiving area that ranks every outstanding specimen by urgency. It sends alerts directly to the receiving tech’s workstation. It also keeps a timeline view for each specimen that shows every scan, the time spent on each leg, and where the specimen most likely is now.

## 4. User Interaction (2-4 sentences)

The primary users are the receiving and plating assistants and techs, who are usually the first to notice a missing specimen today, with supervisors as the escalation tier and collecting units contacted when a specimen was never sent. Tracer runs in the background on every specimen and requires no input to start. When a specimen needs attention, it delivers an alert with a short search brief to the receiving tech and posts it to the live board. The tech then records where the specimen was found or that it was not found, which closes the alert.

## 5. AI System Context (4-6 sentences)

**Context before using AI system:** Today a missing specimen produces no signal, so it is noticed late and indirectly. Staff search for it by hand, escalate to a supervisor after several failed attempts, and, if the specimen is declared lost, trigger a hospital-wide lost-specimen alert that requires every department to search its area and empty stored pneumatic tubes.

**Context after using AI system:** Tracer flags an abnormally long dwell on a specific leg within minutes to hours, names the most likely location, and gives the time left before recollection would be required. Searches become targeted, most losses are resolved before a hospital-wide alert would be needed, and fewer patients need to be recollected. It runs on every specimen all the time, so no decision is needed to use it.

## 6. System I/O (3-6 sentences)

**Overall System Input:** A single run is one assessment of one specimen at one moment, triggered by a new scan or pneumatic tube event, or by a periodic check while the specimen is between scans. Each run takes the specimen type and the tests ordered, since an anaerobic culture shortens the window, together with the collection time and every later scan timestamp from the laboratory information system. It also takes carrier dispatch and arrival events from the pneumatic tube system, the current time, and the current receiving volume in the lab.

**Overall System Output:** The run returns the specimen’s status, its most likely location with ranked alternatives, the expected time of its next scan, and the time remaining before recollection would be required, all of which update the live board and the specimen’s timeline. When warranted, it also sends a prioritized alert with a short search brief to the receiving tech’s workstation and the live board, and the outcome the tech records is logged with that alert.

## 7. AI Modules (1-4 modules)

| AI Module Description | AI Task | Input Data | Output Data |
|---|---|---|---|
| Specimen status: judges whether a specimen between scans is on track, delayed, or likely lost or stalled | Tabular classification | Specimen type, ordered tests, event timestamps so far, time elapsed on current leg, time of day, current lab volume | Probability of on track, delayed, and lost or stalled |
| Arrival forecast: predicts when the next scan should occur | Tabular regression | Same inputs as specimen status | Expected time of next scan with a likely and an outer bound |
| Location estimate: infers where an unscanned specimen most likely is | Multiclass tabular classification | Event timestamps, tube-system carrier events, time elapsed, current lab volume | Probability over locations: in tube, on lab counter unreceived, in tube storage, misrouted to another station, never sent from collecting unit |
| Search brief: tells the tech where to look first | Text-to-text generation | Outputs of the three modules above, specimen details, time remaining before recollection | Short plain-language note naming where to look first, why, and how much time remains |

**Figure 1.** Single run of the deployed system for one specimen. Specimen event history feeds three AI modules, specimen status, arrival forecast and location estimate, which feed a non-AI rule-based triage step; triage feeds the search brief, an AI module, which produces the alert to the receiving tech. The image is embedded in the `.docx`.
