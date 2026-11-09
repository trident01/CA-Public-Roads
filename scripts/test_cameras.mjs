#!/usr/bin/env node
// Behavioral tests for the shared camera controller; no remote media is fetched.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source = readFileSync(new URL("../assets/cameras.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../assets/cameras.css", import.meta.url), "utf8");
const credit = "ALERTCalifornia | UC San Diego";

function camera(id = 1, attributes = {}, geometry = {x: -117.5, y: 34}) {
  return {attributes: {OBJECTID: id, cameraName: `Camera ${id}`, cameraURL: `https://cameras.alertcalifornia.org/?id=Axis-Test${id}`,
    imageURL: `https://cameras.alertcalifornia.org/public-camera-data/Axis-Test${id}/latest-frame.jpg`,
    isOnline: "online", isActive: "active", siteId: `site-${id}`, viewTime: "2026-11-09 07:16:59-08:00", ...attributes}, geometry};
}

function fixture({zoom = 12, rows = [camera()], mode = "vector", header = null} = {}) {
  const imageRequests = [], elements = [], requests = [], statuses = [], timers = new Map(), listeners = new Map();
  let nextTimer = 0, clock = 1_800_000_000_000;
  class Element {
    constructor(tag) {this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.events = new Map(); this.hidden = false; this.className = ""; this.textContent = ""; this.innerHTML = ""; elements.push(this);}
    appendChild(child) {this.children.push(child); child.parent = this; return child;}
    append(...children) {children.forEach((child) => this.appendChild(child));}
    remove() {if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); this.removed = true;}
    setAttribute(key, value) {this.attributes[key] = value;}
    addEventListener(event, callback) {this.events.set(event, callback);}
    dispatch(event, value) {this.events.get(event)?.(value);}
    set src(value) {this._src = value; if (this.tagName === "IMG") imageRequests.push(value);}
    get src() {return this._src;}
    get classList() {return {add: (value) => {this.className += ` ${value}`;}};}
  }
  const map = {
    zoom, bounds: {west: -119, south: 33, east: -116, north: 35}, markers: new Set(),
    getZoom() {return this.zoom;},
    getBounds() {return {getWest: () => this.bounds.west, getSouth: () => this.bounds.south, getEast: () => this.bounds.east, getNorth: () => this.bounds.north};},
    getContainer: () => ({clientWidth: 1400, clientHeight: 1000, getBoundingClientRect: () => ({left: 0, top: 0, width: 1400, height: 1000})}),
    project([lng, lat]) {return {x: (lng - this.bounds.west) / (this.bounds.east - this.bounds.west) * 1400, y: (this.bounds.north - lat) / (this.bounds.north - this.bounds.south) * 1000};},
    latLngToContainerPoint([lat, lng]) {return this.project([lng, lat]);},
    on(event, callback) {if (!listeners.has(event)) listeners.set(event, new Set()); listeners.get(event).add(callback);},
    off(event, callback) {listeners.get(event)?.delete(callback);},
    emit(event) {for (const callback of listeners.get(event) || []) callback();},
    removeLayer(marker) {this.markers.delete(marker);},
  };
  class Marker {
    constructor(options) {this.element = options.element; this.element?.setAttribute("aria-label", "Map marker");}
    setLngLat(point) {this.point = point; return this;}
    addTo(target) {this.map = target; target.markers.add(this); return this;}
    remove() {this.map.markers.delete(this);}
  }
  const reply = (data) => ({ok: true, json: async () => data});
  const defaultFetch = async (url) => {
    const params = new URL(url).searchParams;
    if (params.get("returnIdsOnly") === "true") return reply({objectIds: rows.map((row) => row.attributes.OBJECTID)});
    const ids = params.get("objectIds").split(",").map(Number);
    return reply({features: rows.filter((row) => ids.includes(row.attributes.OBJECTID))});
  };
  let fetchImplementation = defaultFetch;
  const context = vm.createContext({
    URL, URLSearchParams, AbortController,
    Date: class extends Date {static now() {return clock;}},
    document: {createElement: (tag) => new Element(tag), querySelector: () => header ? {getBoundingClientRect: () => header} : null},
    fetch: (url, options) => {requests.push({url, options}); return fetchImplementation(url, options);},
    setTimeout(callback, delay) {timers.set(++nextTimer, {callback, delay}); return nextTimer;},
    clearTimeout(id) {timers.delete(id);},
    maplibregl: {Marker}, L: {divIcon: (options) => options, marker(point, options) {
      const marker = new Marker({element: options.icon.html}); marker.point = point; return marker;
    }},
  });
  vm.runInContext(source, context, {filename: "assets/cameras.js"});
  const controller = context.initializeCameras({map, mode, enabled: true, onStatus: (message) => statuses.push(message)});
  const flush = async () => {
    const jobs = [...timers].filter(([, timer]) => timer.delay <= 350);
    await Promise.all(jobs.map(([id, timer]) => {timers.delete(id); return timer.callback();}));
  };
  return {map, controller, flush, elements, requests, imageRequests, statuses, timers, listeners,
    setFetch(value) {fetchImplementation = value;}, defaultFetch, reply, advance(ms) {clock += ms;},
    links: () => [...map.markers].map((marker) => marker.element),
    images: () => elements.filter((element) => element.tagName === "IMG" && !element.removed),
  };
}

test("both renderers show overview icons from zoom 6 and previews from zoom 11", async () => {
  for (const mode of ["classic", "vector"]) {
    const f = fixture({zoom: 5, mode}); await f.flush();
    assert.equal(f.requests.length, 0); assert.equal(f.map.markers.size, 0);
    for (const zoom of [6, 9, 10]) {
      f.map.zoom = zoom; f.map.emit("zoomend"); await f.flush();
      assert.equal(f.map.markers.size, 1); assert.equal(f.imageRequests.length, 0);
      assert.match(f.links()[0].className, /alert-camera-icon/);
    }
    f.map.zoom = 11; f.map.emit("zoomend"); await f.flush();
    assert.equal(f.map.markers.size, 1); assert.equal(f.imageRequests.length, 1);
    assert.match(f.links()[0].className, /alert-camera-card/);
    f.map.zoom = 10; f.map.emit("zoomend"); await f.flush();
    assert.equal(f.map.markers.size, 1); assert.equal(f.imageRequests.length, 1);
    assert.match(f.links()[0].className, /alert-camera-icon/);
    f.map.zoom = 5; f.map.emit("zoom");
    assert.equal(f.map.markers.size, 0);
    f.controller.destroy();
  }
});

test("complete 50-record pages are fetched before rendering and image cards are capped", async () => {
  const rows = Array.from({length: 61}, (_, index) => camera(index + 1, {}, {x: -118.8 + Math.floor(index / 8) * 0.35, y: 33.1 + index % 8 * 0.23}));
  const f = fixture({rows}); await f.flush();
  const pages = f.requests.filter(({url}) => new URL(url).searchParams.has("objectIds"));
  assert.equal(pages.length, 2);
  assert.deepEqual(pages.map(({url}) => new URL(url).searchParams.get("objectIds").split(",").length), [50, 11]);
  assert.ok(f.imageRequests.length > 0 && f.imageRequests.length <= 8);
  assert.ok(f.map.markers.size <= 64);
  assert.equal(f.statuses.length, 0);
  for (const {url} of f.requests) assert.match(url, /^https:\/\/services8\.arcgis\.com\//);
});

test("truncated or mismatched pages fail explicitly instead of displaying partial data", async () => {
  for (const page of [{features: [], exceededTransferLimit: true}, {features: []}, {features: [camera(2)]}]) {
    const f = fixture();
    f.setFetch(async (url) => new URL(url).searchParams.has("returnIdsOnly") ? f.reply({objectIds: [1]}) : f.reply(page));
    await f.flush(); assert.equal(f.map.markers.size, 0); assert.match(f.statuses.at(-1), /unavailable/);
  }
});

test("cards preserve full imagery, visible credit, history links and accessible names", async () => {
  const f = fixture(); await f.flush();
  const link = f.links()[0], image = f.images()[0];
  assert.equal(link.href, "https://cameras.alertcalifornia.org/?id=Axis-Test1");
  assert.equal(link.target, "_blank"); assert.match(link.rel, /noopener/);
  assert.match(link.attributes["aria-label"], /Camera 1.*history/);
  assert.ok(f.elements.some((element) => element.textContent === credit));
  assert.ok(f.elements.some((element) => element.textContent.startsWith("View: ")));
  assert.equal(image.loading, "eager", "hidden lazy images never trigger their load handler");
  assert.match(css, /object-fit:contain/);
  let stopped = false, prevented = false;
  link.dispatch("click", {stopPropagation() {stopped = true;}, preventDefault() {prevented = true;}});
  assert.equal(stopped, true); assert.equal(prevented, false, "normal targetblank navigation is preserved");
  image.dispatch("load"); assert.equal(image.hidden, false);
});

test("timezone-less source timestamps stay explicit instead of assuming the viewer's zone", async () => {
  const f = fixture({rows: [camera(1, {viewTime: "2026-11-09 07:16:59"})]}); await f.flush();
  assert.match(f.links()[0].title, /2026-11-09 07:16:59 \(time zone not supplied\)/);
  const label = f.elements.find((element) => element.className === "alert-camera-time");
  assert.equal(label.textContent, "View: 07:16 · zone not supplied");
  assert.equal(label.title, "2026-11-09 07:16:59 (time zone not supplied)");
  const missing = fixture({rows: [camera(1, {viewTime: "invalid"})]}); await missing.flush();
  assert.ok(missing.elements.some((element) => element.textContent === "Time not reported"));
});

test("offline and failed previews show placeholders without broken images", async () => {
  const offline = fixture({rows: [camera(1, {isOnline: "offline"})]}); await offline.flush();
  assert.equal(offline.imageRequests.length, 0);
  assert.ok(offline.elements.some((element) => element.textContent === "Camera offline"));
  const f = fixture(); await f.flush();
  const image = f.images()[0]; image.dispatch("error");
  assert.equal(image.removed, true);
  assert.ok(f.elements.some((element) => element.textContent === "Preview unavailable" && !element.hidden));
});

test("source text and URLs cannot insert markup, off-domain images or arbitrary navigation", async () => {
  const malicious = '<img src=x onerror="alert(1)">';
  const rows = [camera(1, {cameraName: malicious, imageURL: "https://evil.test/frame.jpg"}),
    camera(2, {cameraURL: "javascript:alert(1)"}, {x: -117, y: 34}),
    camera(3, {cameraURL: "https://cameras.alertcalifornia.org.evil.test/?id=Axis-X"}, {x: -118, y: 34})];
  const f = fixture({rows}); await f.flush();
  assert.equal(f.map.markers.size, 1); assert.equal(f.imageRequests.length, 0);
  assert.ok(f.elements.some((element) => element.textContent === malicious));
  assert.ok(f.elements.every((element) => !element.innerHTML.includes(malicious)));
  assert.equal(new URL(f.links()[0].href).hostname, "cameras.alertcalifornia.org");
});

test("colocated cameras choose a fresh online view and show the extra-view count", async () => {
  const f = fixture({rows: [camera(1, {isOnline: "offline"}), camera(2)]}); await f.flush();
  assert.equal(f.map.markers.size, 1); assert.equal(f.imageRequests.length, 1);
  assert.match(f.links()[0].href, /Axis-Test2/);
  assert.ok(f.elements.some((element) => element.textContent === "+1"));
});

test("cards near screen edges or behind the search header become compact icons", async () => {
  const top = fixture({rows: [camera(1, {}, {x: -117.5, y: 34.95})]}); await top.flush();
  assert.equal(top.imageRequests.length, 0); assert.match(top.links()[0].className, /alert-camera-icon/);
  const header = fixture({rows: [camera(1, {}, {x: -118.5, y: 34.5})], header: {left: 0, top: 0, right: 500, bottom: 300}});
  await header.flush(); assert.equal(header.imageRequests.length, 0); assert.match(header.links()[0].className, /alert-camera-icon/);
});

test("cached containing viewports do not repeat queries or reload unchanged images", async () => {
  const f = fixture(); await f.flush();
  const requests = f.requests.length, images = f.imageRequests.length, link = f.links()[0];
  f.map.emit("movestart"); assert.equal(link.hidden, true);
  f.map.emit("moveend"); await f.flush();
  assert.equal(f.requests.length, requests); assert.equal(f.imageRequests.length, images);
  assert.equal(f.links()[0], link); assert.equal(link.hidden, false);
  f.advance(5 * 60 * 1000 + 1); f.map.emit("moveend"); await f.flush();
  assert.equal(f.requests.length, requests + 2);
});

test("invalid, outside-California or world-wrapped bounds never trigger statewide queries", async () => {
  for (const bounds of [
    {west: -120, east: -120, south: 33, north: 34}, {west: NaN, east: -116, south: 33, north: 34},
    {west: -80, east: -79, south: 33, north: 34}, {west: 235, east: 238, south: 33, north: 34},
    {west: -180, east: 180, south: -90, north: 90}, {west: 170, east: -170, south: 33, north: 34},
  ]) {
    const f = fixture(); f.map.bounds = bounds; await f.flush();
    assert.equal(f.requests.length, 0, JSON.stringify(bounds)); assert.equal(f.map.markers.size, 0);
  }
  const f = fixture(); f.map.bounds = {west: -125, east: -124.5, south: 41.8, north: 42.3}; await f.flush();
  const envelope = new URL(f.requests[0].url).searchParams.get("geometry").split(",").map(Number);
  assert.ok(envelope[0] >= -124.6 && envelope[3] <= 42.1);
});

test("disabled/destroyed layers abort pending queries and cannot be resurrected by late responses", async () => {
  for (const action of ["disable", "destroy"]) {
    const f = fixture(); let resolve;
    const pendingReply = new Promise((done) => {resolve = done;});
    let first = true;
    f.setFetch((url, options) => {if (first) {first = false; return pendingReply;} return f.defaultFetch(url, options);});
    const pending = f.flush();
    if (action === "disable") f.controller.setEnabled(false); else f.controller.destroy();
    assert.equal(f.requests[0].options.signal.aborted, true);
    resolve(f.reply({objectIds: [1]})); await pending;
    assert.equal(f.map.markers.size, 0); assert.equal(f.imageRequests.length, 0);
    if (action === "destroy") assert.ok([...f.listeners.values()].every((set) => set.size === 0));
  }
});

test("successful retry clears a prior camera failure note", async () => {
  const f = fixture(); f.setFetch(async () => ({ok: false, status: 503})); await f.flush();
  assert.match(f.statuses.at(-1), /unavailable/);
  f.setFetch(f.defaultFetch); f.map.emit("moveend"); await f.flush();
  assert.equal(f.map.markers.size, 1); assert.equal(f.statuses.at(-1), "");
});
