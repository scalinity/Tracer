// Integrity check for the plan's data and arithmetic. Run after editing tasks, gates, jobs or the rubric in plan.html:
//   node visuals/plan.check.cjs
// It loads only the pure <script id="core"> block, so no browser is needed.
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "plan.html"), "utf8");
const ctx = vm.createContext({});
vm.runInContext(html.match(/<script id="core">([\s\S]*?)<\/script>/)[1] +
  "\nthis.X={TASKS,AFTER,GATES,CUTS,JOBS,RUBRIC,schedule,jobCost,D0,START,PRESENT,taskDay};", ctx);
const X = ctx.X;
let bad = 0;
const fail = s => { bad++; console.log("FAIL", s); };
const ids = new Set(X.TASKS.map(t => t.id));
if (ids.size !== X.TASKS.length) fail("duplicate task ids");
const rubric = new Set(X.RUBRIC.flatMap(c => c.rows.map(r => r.id)));
for (const t of X.TASKS) {
  for (const n of t.needs || []) {
    if (!ids.has(n)) fail(`${t.id} needs unknown ${n}`);
    else if (X.taskDay(X.TASKS.find(x => x.id === n)) > X.taskDay(t)) fail(`${t.id} is due before ${n}, which it needs`);
  }
  for (const e of t.earns) if (!rubric.has(e)) fail(`${t.id} earns unknown rubric line ${e}`);
  const d = X.taskDay(t);
  if (d < X.START || d > X.PRESENT) fail(`${t.id} falls outside Oct 28 to Nov 18`);
  if (!t.steps.length || !t.done || !t.t) fail(`${t.id} is missing steps, a done line or a title`);
  if (t.api && !X.JOBS.find(j => j.id === t.api)) fail(`${t.id} names unknown Claude job ${t.api}`);
}
for (const a of X.AFTER) for (const e of a.earns) if (!rubric.has(e)) fail(`${a.id} earns unknown rubric line ${e}`);
for (const g of X.GATES) for (const id of g.tasks) {
  if (!ids.has(id)) fail(`gate ${g.id} lists unknown ${id}`);
  else if (X.taskDay(X.TASKS.find(x => x.id === id)) > X.D0(g.day[0], g.day[1])) fail(`gate ${g.id} lists ${id}, which is due after the gate`);
}
for (const c of X.CUTS) for (const [id, who] of Object.entries(c.saves)) {
  if (!ids.has(id)) { fail(`cut ${c.id} names unknown ${id}`); continue; }
  for (const [p, h] of Object.entries(who)) if (h > (X.TASKS.find(x => x.id === id).who[p] || 0)) fail(`cut ${c.id} saves more than ${id} holds for ${p}`);
}
for (const r of rubric) {
  const n = X.TASKS.filter(t => t.earns.includes(r)).length + X.AFTER.filter(a => a.earns.includes(r)).length;
  if (!n) fail(`rubric line ${r} has no task that earns it`);
}
const hours = p => X.TASKS.reduce((s, t) => s + (t.who[p] || 0), 0);
const sch = X.schedule({done: {}, cuts: {}, hours: {}, cap: {D: {wd: 3, we: 5}, K: {wd: 0.5, we: 1.5}}}, X.START);
console.log(`${X.TASKS.length} tasks, ${rubric.size} rubric lines. Hours: Daniel ${hours("D")}, Karissa ${hours("K")}.`);
console.log("Slack at default hours (3 and 5 for Daniel, 0.5 and 1.5 for Karissa):",
  X.GATES.map((g, i) => `${g.id} D ${sch.D.gates[i].slack.toFixed(1)} K ${sch.K.gates[i].slack.toFixed(1)}`).join("; "));
console.log("Claude jobs, core:", "$" + X.JOBS.filter(j => !j.opt).reduce((s, j) => s + X.jobCost(j), 0).toFixed(2));
console.log(bad ? `${bad} problems` : "plan data is consistent");
process.exit(bad ? 1 : 0);
