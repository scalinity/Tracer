/* ════════════════════════════════════════════════════════════
   1. Model. Illustrative parameters — assumptions, not measurements.
      Mirrors docs/architecture.md §5.2 (risk) and §5.3 (location).
   ════════════════════════════════════════════════════════════ */
const PARAM = {
  /* Collection → receipt, as a mixture: most specimens move normally, a few stall
     somewhere and arrive very late, and a few never arrive. Weights sum with
     lossFraction to 1. This is the "failure component" of architecture §5.2. */
  mix: [
    {w:0.88, m:24, s:0.50, label:"normal transit"},
    {w:0.08, m:70, s:0.60, label:"stalled, arrives late"}
  ],
  lossFraction:  0.04,                     // never arrives
  windowMin:     60,                       // stability window: the policy's, for unpreserved urine at room temperature
  tickMin:       5,                        // twin re-score cadence
  operatingPoint:0.45,                     // alert threshold on P(miss window)
  collectedAt:   2*60 + 14,                // 02:14 in minutes past midnight
  receivedAt:    52,                       // scenario: found after the alert; the clock stops here
  accessionedAt: 60,                       // later scans, after the clock has stopped
  platedAt:      71,
  /* Sub-legs inside the unobserved interval, with explicit durations and where each
     one can go next. "Stalled" is a real state rather than a long tail, because a
     model whose only explanation for silence is loss will always cry loss. */
  legs: [
    {name:"Awaiting dispatch", median:6,  sigma:0.60, exit:[["leg",1,0.992],["lost",0,0.008]]},
    {name:"In the tube",       median:4,  sigma:0.25, exit:[["leg",2,0.995],["lost",0,0.005]]},
    {name:"At the station",    median:11, sigma:0.70, exit:[["recv",0,0.910],["leg",3,0.070],["lost",0,0.020]]},
    {name:"Stalled",           median:45, sigma:0.80, exit:[["recv",0,0.850],["lost",0,0.150]]}
  ],
  /* Which leg each smear column shows. The stalled specimen is still physically at
     the station, so those two share a column. */
  smearOf: [[0],[1],[2,3]]
};

const erf = x => {                          // Abramowitz & Stegun 7.1.26
  const s = x < 0 ? -1 : 1; x = Math.abs(x);
  const t = 1/(1 + 0.3275911*x);
  const y = 1 - ((((1.061405429*t - 1.453152027)*t + 1.421413741)*t - 0.284496736)*t + 0.254829592)*t*Math.exp(-x*x);
  return s*y;
};
const normCdf = z => 0.5*(1 + erf(z/Math.SQRT2));
const lnCdf  = (t,m,s) => t <= 0 ? 0 : normCdf(Math.log(t/m)/s);
const lnSurv = (t,m,s) => 1 - lnCdf(t,m,s);
const lnPdf  = (t,m,s) => t <= 0 ? 0 : Math.exp(-Math.pow(Math.log(t/m),2)/(2*s*s))/(t*s*Math.sqrt(2*Math.PI));

/* P(window missed | no receipt scan yet at t minutes). The clock stops at the receipt scan, so the
   window is missed exactly when the scan has not come by W. Of the specimens still unreceived at t,
   the ones that miss it are those still unreceived at W: the never-arrivals and the slow tail. As the
   normal transit passes without a scan, the surviving mass is increasingly that failure component. */
function riskUnreceived(t){
  const {mix, lossFraction:pl, windowMin:W} = PARAM;
  if (t >= W) return 1;
  let notReceived = pl, notByWindow = pl;
  for (const c of mix){
    notReceived += c.w * lnSurv(t, c.m, c.s);
    notByWindow += c.w * lnSurv(W, c.m, c.s);
  }
  return notByWindow / notReceived;
}
/* After the receipt scan the clock has stopped and the outcome is known. */
const riskAt = t => t < PARAM.receivedAt ? riskUnreceived(t)
                  : PARAM.receivedAt <= PARAM.windowMin ? 0 : 1;

/* Location posterior: forward filter over sub-legs on a one-minute grid, conditioned
   on "no receipt scan yet". Absence of a scan is the input, not a special case. */
function locationSeries(maxT){
  const legs = PARAM.legs, N = legs.length, CAP = 180;
  let alpha = legs.map(() => new Float64Array(CAP));   // [leg][minutes in that leg]
  alpha[0][0] = 1;
  let lost = 0, arrived = 0;
  const out = [];
  const snapshot = () => {
    const mass = alpha.map(a => a.reduce((s,v) => s+v, 0));
    const live = mass.reduce((s,v) => s+v, 0) + lost;   // conditioned on not received
    out.push({ legs: mass.map(m => m/live), lost: lost/live });
  };
  snapshot();
  for (let t = 1; t <= maxT; t++){
    const next = legs.map(() => new Float64Array(CAP));
    for (let i = 0; i < N; i++){
      const {median:m, sigma:s, exit} = legs[i];
      for (let a = 0; a < CAP-1; a++){
        const mass = alpha[i][a];
        if (mass < 1e-12) continue;
        const surv = Math.max(lnSurv(a, m, s), 1e-9);
        const hazard = Math.min(Math.max((lnCdf(a+1,m,s) - lnCdf(a,m,s))/surv, 0), 1);
        const leaving = mass*hazard;
        next[i][a+1] += mass - leaving;
        for (const [kind, target, p] of exit){
          if (kind === "leg") next[target][0] += leaving*p;
          else if (kind === "lost") lost += leaving*p;
          else arrived += leaving*p;
        }
      }
    }
    alpha = next;
    snapshot();
  }
  return out;
}

/* ════════════════════════════════════════════════════════════
   2. Derived series and scenario events
   ════════════════════════════════════════════════════════════ */
const MAXT = 90;
const RISK = Array.from({length: MAXT+1}, (_,t) => riskAt(t));
const LOC  = locationSeries(MAXT);

/* The alert fires at the first five-minute tick past the operating point — the same
   rule the triage component applies, so this moment is computed, not chosen. */
let ALERT_T = null;
for (let t = 0; t <= PARAM.receivedAt; t += PARAM.tickMin){
  if (RISK[t] >= PARAM.operatingPoint){ ALERT_T = t; break; }
}

