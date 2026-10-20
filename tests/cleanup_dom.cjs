// Deterministic DOM/scroll harness: no browser timing or destructive Mole command.
const assert = require("node:assert/strict");
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.events = {};
    this.className = "";
    this.id = "";
    this.value = "";
    this.textWrites = 0;
    this.markupWrites = 0;
    this.scrollHeight = 1000;
    this.clientHeight = 200;
    this.top = 0;
  }
  appendChild(node) { node.parent = this; this.children.push(node); return node; }
  insertBefore(node, before) {
    this.children = this.children.filter(child => child !== node);
    this.children.splice(this.children.indexOf(before), 0, node);
    node.parent = this;
  }
  remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
  replaceChildren() { this.children = []; }
  set textContent(value) { this.value = value; this.textWrites++; this.children = []; }
  get textContent() { return this.value; }
  set innerHTML(value) { this.markup = value; this.markupWrites++; }
  set scrollTop(value) { this.top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }
  get scrollTop() { return this.top; }
  addEventListener(name, callback) { this.events[name] = callback; }
  querySelector(selector) {
    return walk(this).find(node => selector.startsWith(".")
      ? node.className.split(" ").includes(selector.slice(1)) : node.tagName.toLowerCase() === selector);
  }
}
function walk(node) { return node.children.flatMap(child => [child, ...walk(child)]); }
const body = new Element("body");
global.document = {
  body,
  createElement: tag => new Element(tag),
  getElementById: id => walk(body).find(node => node.id === id)
};
for (const id of ["items", "status-icon", "status", "message", "amount", "caption", "mode", "elapsed"]) {
  const node = body.appendChild(new Element("div")); node.id = id;
}
let frames = new Map(), next = 0;
global.requestAnimationFrame = fn => { frames.set(++next, fn); return next; };
global.cancelAnimationFrame = id => frames.delete(id);
function flush() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); }
global.window = {};
require("../src/cleanup.js");
const area = document.getElementById("items");
const view = {
  session: "run1", revision: 1, state: "running", status: "Cleaning", message: "Scanning",
  amount: "—", metric: "Tracked cleanup", caption: "Apps", tag: "Cleanup", elapsed: "Elapsed 0:01",
  fallback: "", truncated: "", sections: [{id: "0", title: "Apps", current: true,
    items: [{id: "0", text: '</script><img onerror="bad()"> 中文 \u2028', value: "1MB", error: false}]}]
};
window.updateCleanup(view); flush();
assert.equal(area.scrollTop, 800);
const section = document.getElementById("category-0");
const row = section.querySelector(".items").children[0];
const paragraph = row.querySelector("p");
const spinner = document.getElementById("status-icon");
const originalIconWrites = spinner.markupWrites;
const originalTextWrites = paragraph.textWrites;
area.events.wheel({deltaY: -1});
area.scrollTop = 300;
area.events.scroll();
area.scrollHeight = 1500;
view.sections[0].items.push({id: "1", text: "new item", value: "2MB"});
view.revision++;
window.updateCleanup(view); flush();
assert.equal(area.scrollTop, 300);
assert.equal(section.querySelector(".items").children[0], row);
assert.equal(paragraph.textWrites, originalTextWrites);
assert.equal(spinner.markupWrites, originalIconWrites);
for (let i = 0; i < 30; i++) { view.elapsed = `Elapsed ${i}`; window.updateCleanup(view); flush(); }
assert.equal(area.scrollTop, 300);
// A pending follow frame must also respect wheel-up before the scroll event.
area.events.wheel({deltaY: 1}); area.scrollTop = 1300; area.events.scroll();
area.scrollHeight = 1800; window.updateCleanup(view);
area.events.wheel({deltaY: -1}); area.scrollTop = 1200; flush();
assert.equal(area.scrollTop, 1200);
area.events.scroll();
area.events.wheel({deltaY: 1}); area.scrollTop = 1600; area.events.scroll();
area.scrollHeight = 2000; window.updateCleanup(view); flush();
assert.equal(area.scrollTop, 1800);
view.state = "completed"; window.updateCleanup(view); flush();
assert.equal(document.body.className, "completed");
assert.equal(section.querySelector(".items").children[0], row);
view.session = "run2"; view.sections = []; window.updateCleanup(view); flush();
assert.equal(document.getElementById("items"), area);
assert.equal(document.getElementById("category-0"), undefined);
console.log("DOM identity, text preservation, scroll pause/resume, and new sessions passed.");

view.command = "status";
view.cards = [{key:"cpu",title:"CPU",value:"12%",note:"Current usage",usage:12,rows:["Processor"]}];
window.updateCleanup(view); flush();
const cpuCard = document.getElementById("status-card-cpu");
const cpuValue = cpuCard.querySelector(".status-number");
const cpuRows = cpuCard.querySelector(".status-rows");
cpuRows.scrollTop = 50;
view.cards[0].value = "27%";
view.cards[0].usage = 27;
window.updateCleanup(view); flush();
assert.equal(document.getElementById("status-card-cpu"), cpuCard);
assert.equal(cpuCard.querySelector(".status-number"), cpuValue);
assert.equal(cpuValue.textContent,"27%");
assert.equal(cpuCard.querySelector(".status-fill").style.width,"27%");
assert.equal(cpuRows.scrollTop,50);
console.log("Status cards preserve nodes and scroll while updating measurements.");
