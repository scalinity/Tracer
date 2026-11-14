/* Tracer's survival model in the browser: the analytic mixture-survival baseline, its alert rules, the
   ranking, the threshold control's recomputation and the 3D bench's link. It used to run live on the phone page
   (docs/demo/index.html); the page now shows TracerNet's stored results only and does not load this file.
   It is kept here, unchanged, because tests/test_demo_page.py runs it under node and holds it to the Python
   (src/models/risk.py, src/pipeline/evaluate.py, src/pipeline/triage.py, src/api/search_briefs.py).
   Beat Tracer's pure functions stay in the page's <script id="core"> block. */
/* Tracer's arithmetic, with no access to the page: the normal distribution, the risk of missing the stability
   window, the expected arrival, the two alert rules, expected harm and the ranking. The formulas are those of
   src/models/risk.py, src/pipeline/evaluate.py, src/pipeline/triage.py and src/api/search_briefs.py.
   tests/test_demo_page.py runs this block under node and holds it to the Python. */
var TracerSurvival = (function () {
  "use strict";
  var SQRT_PI = Math.sqrt(Math.PI);

  /* erfc to about 1e-14 relative on both tails. Below 1.5 it is 1 - erf(x), with erf from its series of
     positive terms, erf(x) = 2/sqrt(pi) exp(-x^2) sum 2^n x^(2n+1) / (1*3*...*(2n+1)), so nothing cancels
     inside the sum. From 1.5 on it is the continued fraction
     erfc(x) = exp(-x^2) / sqrt(pi) / (x + (1/2) / (x + 1 / (x + (3/2) / (x + ...)))),
     evaluated by the modified Lentz method. */
  function erfc(x) {
    if (x !== x) return NaN;
    if (x < 0) return 2 - erfc(-x);
    if (x < 1.5) {
      var x2 = x * x, term = x, sum = x;
      for (var n = 1; n < 100 && term > sum * 1e-17; n++) {
        term *= 2 * x2 / (2 * n + 1);
        sum += term;
      }
      return 1 - 2 / SQRT_PI * Math.exp(-x2) * sum;
    }
    if (x > 27) return 0;
    var tiny = 1e-300, f = x, c = x, d = 0;
    for (var k = 1; k < 300; k++) {
      var a = k / 2;
      d = x + a * d;
      d = 1 / (d === 0 ? tiny : d);
      c = x + a / c;
      if (c === 0) c = tiny;
      var delta = c * d;
      f *= delta;
      if (Math.abs(delta - 1) < 3e-16) break;
    }
    return Math.exp(-x * x) / SQRT_PI / f;
  }

  /* The standard normal CDF. */
  function Phi(z) { return 0.5 * erfc(-z / Math.SQRT2); }

  /* Lognormal survival P(T > t) for log-minutes with mean mu and standard deviation sigma. */
  function survival(t, mu, sigma) {
    return t <= 0 ? 1 : 0.5 * erfc((Math.log(t) - mu) / (sigma * Math.SQRT2));
  }

  /* The fitted mixture for a group: the group's normal transit, the pooled stall, and the share that never
     arrives (src/models/risk.py mixture_from). */
  function mixture(model, group) {
    var p = model.pooled, w = p.weights, g = model.groups[group];
    if (!g) throw new Error("the bundle has no fitted parameters for the group " + group);
    return { never: w.never, comps: [[w.normal, g.mu, g.sigma], [w.stalled, p.stalled_mu, p.stalled_sigma]] };
  }

  function notReceived(t, mix) {
    var s = mix.never;
    for (var i = 0; i < mix.comps.length; i++) s += mix.comps[i][0] * survival(t, mix.comps[i][1], mix.comps[i][2]);
    return s;
  }

  /* P(the window is missed | no receipt scan after t minutes). The clock stops at the receipt scan, so a
     specimen misses its window exactly when it is still unreceived when the window closes; from then on the
     miss is certain. */
  function risk(t, mix, windowMin) {
    return t >= windowMin ? 1 : notReceived(windowMin, mix) / notReceived(t, mix);
  }

  /* The risk at every scoring tick, with the running maximum that src/pipeline/evaluate.py applies. */
  function riskSeries(ticks, mix, windowMin) {
    var out = new Array(ticks.length), top = -Infinity;
    for (var i = 0; i < ticks.length; i++) {
      var r = risk(ticks[i], mix, windowMin);
      if (r > top) top = r;
      out[i] = top;
    }
    return out;
  }

  /* Minutes from now until a share q of the specimens still silent at t, among those that will arrive, have
     arrived: bisection as in src/models/risk.py arrival_quantile. A specimen that never arrives is not in
     this share; the risk counts it. */
  function arrivalQuantile(t, mix, q) {
    var c = mix.comps, at = [], out = 0, i;
    for (i = 0; i < c.length; i++) { at[i] = survival(t, c[i][1], c[i][2]); out += c[i][0] * at[i]; }
    if (!(out > 0)) return Infinity;
    function arrived(x) {
      var s = 0;
      for (var j = 0; j < c.length; j++) s += c[j][0] * (at[j] - survival(x, c[j][1], c[j][2]));
      return s / out;
    }
    var hi = Math.max(t, 1) * 2;
    while (arrived(hi) < q && hi < 1e12) hi *= 2;
    var lo = t;
    for (i = 0; i < 80; i++) {
      var mid = (lo + hi) / 2;
      if (arrived(mid) < q) lo = mid; else hi = mid;
    }
    return hi - t;
  }

  /* Scoring times: every tick while the window is open and the specimen is unreceived
     (src/pipeline/evaluate.py Spec.ticks). */
  function ticksFor(windowMin, receivedMin, tickMin) {
    var last = receivedMin == null ? windowMin : Math.min(windowMin, Math.ceil(receivedMin) - 1), out = [];
    for (var t = tickMin; t <= last; t += tickMin) out.push(t);
    return out;
  }

  /* Expected harm: the weight of the specimen's urgency tier times its risk. */
  function harm(weight, r) { return weight * r; }

  /* Tracer alerts at the first tick where expected harm reaches the threshold. */
  function modelAlert(series, ticks, weight, threshold) {
    for (var i = 0; i < series.length; i++) if (harm(weight, series[i]) >= threshold) return ticks[i];
    return null;
  }

  /* The plan's rule alerts at the first tick strictly after its group's time threshold. */
  function planAlert(ticks, thresholdMin) {
    for (var i = 0; i < ticks.length; i++) if (ticks[i] > thresholdMin) return ticks[i];
    return null;
  }

  /* Highest expected harm first. A specimen not yet scored has no harm and goes last. Ties go to the lower id,
     so the order never depends on the order the rows came in. */
  function rankByHarm(rows) {
    return rows.slice().sort(function (a, b) {
      var ha = a.harm == null ? -Infinity : a.harm, hb = b.harm == null ? -Infinity : b.harm;
      if (ha !== hb) return hb > ha ? 1 : -1;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  }

  /* Minutes since 1970-01-01 for an ISO local time, read as written: no time zone is applied. */
  function civilMinutes(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(iso);
    if (!m) throw new Error("not an ISO date: " + iso);
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)) / 60000;
  }

  /* The specimens that are collected and not yet received at `now` (minutes, as civilMinutes). Each spec
     carries _c, its collection minute. A specimen whose window closed carryMin or more minutes ago leaves the
     ranking for the `older` list. windowOf(spec), when given, is the window in force (the page's window
     control); without it each specimen keeps its own. */
  function outstanding(specs, now, carryMin, windowOf) {
    var live = [], older = [];
    for (var i = 0; i < specs.length; i++) {
      var s = specs[i], t = now - s._c;
      if (t < 0 || (s.received_min != null && t >= s.received_min)) continue;
      var closedFor = t - (windowOf ? windowOf(s) : s.window_min);
      (closedFor >= carryMin ? older : live).push({ spec: s, t: t, closed: closedFor >= 0 });
    }
    return { live: live, older: older };
  }

  /* One board row per outstanding specimen, scored at its latest tick and ranked by expected harm. Each spec
     carries _ticks. ctx: tickMin, threshold, seriesOf(spec), weightOf(spec), planThresholdOf(spec). */
  function boardRows(live, ctx) {
    return rankByHarm(live.map(function (o) {
      var s = o.spec, ticks = s._ticks, i = Math.min(Math.floor(o.t / ctx.tickMin), ticks.length) - 1;
      var series = ctx.seriesOf(s), w = ctx.weightOf(s);
      var tick = i >= 0 ? ticks[i] : null, r = i >= 0 ? series[i] : null;
      var m = modelAlert(series, ticks, w, ctx.threshold), p = planAlert(ticks, ctx.planThresholdOf(s));
      return { id: s.id, spec: s, t: o.t, closed: o.closed, tick: tick, index: i, risk: r,
               harm: r == null ? null : harm(w, r), modelAt: m, planAt: p,
               model: tick != null && m != null && m <= tick ? m : null,
               plan: tick != null && p != null && p <= tick ? p : null };
    }));
  }

  /* The seven places merged to six for a person looking: the lab's tube station and a stall there are one
     place, worded as a stall when the stall holds at least half of it (src/api/search_briefs.py). */
  function mergePlaces(row, order) {
    var p = {}, i;
    for (i = 0; i < order.length; i++) p[order[i]] = row[i];
    var stall = p.stalled_at_station || 0, station = (p.at_station || 0) + stall, out = [], best = null;
    for (i = 0; i < order.length; i++) {
      var k = order[i];
      if (k === "stalled_at_station") continue;
      var item = { key: k, value: k === "at_station" ? station : p[k] };
      out.push(item);
      if (best === null || item.value > best.value) best = item;
    }
    return { places: out, best: best.key, stalled: station > 0 && stall >= 0.5 * station };
  }

  /* The role an alert goes to: the charge nurse when the specimen is most likely still at its unit, otherwise
     by route (src/pipeline/triage.py role_for). Without a location row it goes by route. */
  function roleFor(routing, route, row, order) {
    if (!row) return routing[route];
    var best = 0;
    for (var i = 1; i < row.length; i++) if (row[i] > row[best]) best = i;
    return order[best] === "at_unit" ? routing.still_at_unit : routing[route];
  }

  /* ---------- the threshold control (plan task P4) ---------- */

  /* The window in force for every specimen: its own (windowMin null) or one window for all. Each spec gets
     _window and fresh _ticks, and its cached risk series is dropped, since both depend on the window. */
  function applyWindow(specs, windowMin, tickMin) {
    for (var i = 0; i < specs.length; i++) {
      var s = specs[i];
      s._window = windowMin == null ? s.window_min : windowMin;
      s._ticks = ticksFor(s._window, s.received_min, tickMin);
      s._series = null;
    }
  }

  /* A missed window, read from the receipt scan as src/pipeline/evaluate.py derives it: no receipt scan, or
     one after the window closed. */
  function isMiss(receivedMin, windowMin) { return receivedMin == null || receivedMin > windowMin; }

  /* The median as numpy computes it: the mean of the two middle values when the count is even. */
  function median(xs) {
    if (!xs.length) return null;
    var v = xs.slice().sort(function (a, b) { return a - b; }), h = v.length >> 1;
    return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2;
  }

  /* One method's results over a set of specimens (src/pipeline/evaluate.py score): alerts[i] is the alert
     minute for specs[i] or null. Warning is the window minus the alert minute; an alert at or after the close
     catches nothing, and the median over all missed windows counts a miss as zero. */
  function scoreAlerts(specs, alerts, windowOf, shifts) {
    var fired = 0, breaches = 0, warnings = [], falseAlerts = 0;
    for (var i = 0; i < specs.length; i++) {
      var s = specs[i], W = windowOf(s), a = alerts[i], miss = isMiss(s.received_min, W);
      if (a != null) { fired++; if (!miss) falseAlerts++; }
      if (miss) { breaches++; if (a != null && W - a > 0) warnings.push(W - a); }
    }
    var everyone = warnings.slice();
    while (everyone.length < breaches) everyone.push(0);
    return { alerts: fired, alerts_per_shift: fired / shifts, breaches: breaches, caught: warnings.length,
             median_warning_min: median(warnings), median_warning_all_breaches_min: median(everyone),
             false_alerts: falseAlerts };
  }

  /* Both methods at every operating point, recomputed from the specimens. ctx: points (how many), shifts,
     windowOf(s), ticksOf(s), seriesOf(s), weightOf(s), theta(i) (Tracer's harm threshold at point i) and
     planThreshold(s, i) (the plan's rule's minutes at point i). */
  function operatingGrid(specs, ctx) {
    var out = [];
    for (var i = 0; i < ctx.points; i++) {
      var th = ctx.theta(i), m = new Array(specs.length), p = new Array(specs.length);
      for (var j = 0; j < specs.length; j++) {
        var s = specs[j], ticks = ctx.ticksOf(s);
        m[j] = modelAlert(ctx.seriesOf(s), ticks, ctx.weightOf(s), th);
        p[j] = planAlert(ticks, ctx.planThreshold(s, i));
      }
      out.push({ model: scoreAlerts(specs, m, ctx.windowOf, ctx.shifts), plan: scoreAlerts(specs, p, ctx.windowOf, ctx.shifts) });
    }
    return out;
  }

  /* Minutes as the threshold sentence prints them: whole minutes, or one decimal for a median that falls
     between two. */
  function fmtWarn(v) { return v == null ? "no" : Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : v.toFixed(1); }

  /* The sentence under the threshold chart. model and plan carry median_warning_all_breaches_min and
     alerts_per_shift; windowMin is the one window set for every specimen, or null for their own. */
  function thresholdSentence(model, plan, windowMin) {
    function part(who, m) {
      return who + " warns " + fmtWarn(m.median_warning_all_breaches_min) + " min ahead and alerts " +
        m.alerts_per_shift.toFixed(1) + " times a shift";
    }
    return (windowMin == null ? "At this setting " : "At this setting, with every window set to " + windowMin + " min, ") +
      part("Tracer", model) + "; " + part("the plan's rule", plan) + ".";
  }

  /* ---------- the 3D view (plan task P6) ---------- */
  /* The 3D bench (docs/demo/exhibit/, visuals/exhibit-3d) scripts four fates: arrives (receipt scan at 24 min),
     stalls (receipt scan at 68 min), lost and unsent (no receipt scan). */
  var BENCH_RECEIPT = { arrives: 24, stalls: 68 };

  /* The bench's fate chosen only from the scans a visitor has seen by `minute`, for use before the truth is
     shown: still silent, stalls while its scripted receipt is still to come, else lost, which never scans;
     received, arrives for a receipt before 68 min, else stalls. */
  function observedFate(minute, receivedMin) {
    if (receivedMin == null || minute < receivedMin) return minute < BENCH_RECEIPT.stalls ? "stalls" : "lost";
    return receivedMin < BENCH_RECEIPT.stalls ? "arrives" : "stalls";
  }

  /* The bench's fate nearest a specimen's true path, for use once the truth is shown: lost in the tube is lost,
     never sent is unsent, a stall or a misroute (late, then found) stalls, and a trip with no transport fault
     arrives when its receipt came in time and stalls when it came late. */
  function trueFate(fault, receivedMin, windowMin) {
    if (fault === "lost_in_tube") return "lost";
    if (fault === "not_dispatched") return "unsent";
    if (fault === "stalled_at_station" || fault === "misrouted") return "stalls";
    if (receivedMin == null) return "lost";
    return receivedMin <= windowMin ? "arrives" : "stalls";
  }

  /* The bench's address query. o: fate, silence (minutes), window (minutes), alert (risk), truth (boolean). A
     value outside what the bench accepts (silence 0 to 130, window 60 to 240, alert 0.1 to 0.9) is left out
     rather than clamped, as the bench itself ignores it. */
  function exhibitQuery(o) {
    var q = ["fate=" + o.fate];
    if (o.silence != null && o.silence >= 0 && o.silence <= 130) q.push("silence=" + Math.round(o.silence));
    if (o.window != null && o.window >= 60 && o.window <= 240) q.push("window=" + Math.round(o.window));
    if (o.alert != null && o.alert >= 0.1 && o.alert <= 0.9) q.push("alert=" + (Math.round(o.alert * 100) / 100));
    q.push("truth=" + (o.truth ? 1 : 0));
    return "?" + q.join("&");
  }

  return { erfc: erfc, Phi: Phi, survival: survival, mixture: mixture, risk: risk, riskSeries: riskSeries,
           arrivalQuantile: arrivalQuantile, ticksFor: ticksFor, harm: harm, modelAlert: modelAlert,
           planAlert: planAlert, rankByHarm: rankByHarm, civilMinutes: civilMinutes, outstanding: outstanding,
           boardRows: boardRows, mergePlaces: mergePlaces, roleFor: roleFor,
           applyWindow: applyWindow, isMiss: isMiss, median: median, scoreAlerts: scoreAlerts,
           operatingGrid: operatingGrid, fmtWarn: fmtWarn, thresholdSentence: thresholdSentence,
           observedFate: observedFate, trueFate: trueFate, exhibitQuery: exhibitQuery };
})();
