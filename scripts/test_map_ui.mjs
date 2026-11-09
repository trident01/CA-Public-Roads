#!/usr/bin/env node
// Run with node scripts/test_map_ui.mjs. No browser or npm packages required.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import test from "node:test";
import {setImmediate as settleAsyncWork} from "node:timers/promises";

const root = new URL("../", import.meta.url);
const html = readFileSync(new URL("index.html", root), "utf8");
const core = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1]).find((source) => source.includes("const FORESTS"));
const controls = readFileSync(new URL("assets/map-ui.js", root), "utf8");
assert.ok(core, "the map's inline application script must exist");

const plain = (value) => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => {resolve = done; reject = fail;});
  return {promise, resolve, reject};
}

function fixture(mode = "vector", url = "https://example.test/maps/") {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      id, checked: true, hidden: false, disabled: false, value: "", textContent: "", innerHTML: "",
      style: {}, dataset: {}, attributes: {}, children: [], listeners: new Map(),
      classList: {toggle() {}}, closest() {return {open: false};}, focus() {}, scrollIntoView() {},
      setAttribute(key, value) {this.attributes[key] = value;},
      appendChild(child) {this.children.push(child); return child;},
      addEventListener(event, callback) {
        const callbacks = this.listeners.get(event) || [];
        callbacks.push(callback); this.listeners.set(event, callbacks);
      },
    });
    return elements.get(id);
  };
  const document = {
    getElementById: element, createElement: (tag) => element(`created-${tag}-${elements.size}`),
    querySelector() {return null;}, addEventListener() {}, head: {appendChild() {}},
    body: element("body"),
  };
  element("forestList").dataset.ready = "1";
  let currentUrl = new URL(url);
  const location = Object.fromEntries(["href", "protocol", "pathname", "search", "hash"].map((key) => [key, currentUrl[key]]));
  const history = {replaceState(_state, _title, next) {
    currentUrl = new URL(next, currentUrl);
    for (const key of Object.keys(location)) location[key] = currentUrl[key];
  }};
  const layers = new Map();
  for (const symbol of ["1", "2", "3", "4"]) {
    for (const surface of ["paved", "unpaved"]) layers.set(`fr_${surface}_${symbol}`, {layout: {}});
  }
  layers.set("verified_public_roads", {layout: {}});
  const sources = new Map(), activeLayers = new Set(), mapEvents = new Map(), calls = [];
  let center = {lat: 33.56789, lng: -116.12345}, zoom = 12.25;
  const map = {
    __classicBindingsApplied: true, layers, sources, activeLayers, calls,
    getCenter: () => center, getZoom: () => zoom, getMaxZoom: () => 14,
    getLayer: (id) => layers.get(id), getSource: (id) => sources.get(id),
    getCanvas: () => ({style: {}}), isStyleLoaded: () => true,
    jumpTo(camera) {calls.push(["jumpTo", camera]); center = {lng: camera.center[0], lat: camera.center[1]}; zoom = camera.zoom;},
    setView(latlng, nextZoom) {calls.push(["setView", latlng, nextZoom]); center = {lat: latlng[0], lng: latlng[1]}; zoom = nextZoom;},
    fitBounds(...args) {calls.push(["fitBounds", ...args]);},
    setLayoutProperty(id, key, value) {layers.get(id).layout[key] = value;},
    setFilter(id, filter) {layers.get(id).filter = filter;},
    addSource(id, source) {assert.ok(!sources.has(id)); sources.set(id, source);},
    addLayer(layer, before) {
      if (typeof layer.id === "string") {
        if (before) assert.ok(layers.has(before), `missing layer insertion target ${before}`);
        layers.set(layer.id, {...layer, layout: {...layer.layout}});
      } else activeLayers.add(layer);
    },
    removeLayer(layer) {typeof layer === "string" ? layers.delete(layer) : activeLayers.delete(layer);},
    on(event, layerOrCallback, callback) {
      const listeners = mapEvents.get(event) || [];
      listeners.push(callback || layerOrCallback); mapEvents.set(event, listeners);
    },
    once(event, callback) {this.on(event, callback);},
    emit(event, payload) {for (const callback of mapEvents.get(event) || []) callback(payload);},
    queryRenderedFeatures() {return [];},
  };
  const timers = new Map();
  let nextTimer = 0;
  const context = vm.createContext({
    console: {log() {}, warn() {}, error() {}}, document, location, history,
    URL, URLSearchParams, AbortController, navigator: {},
    localStorage: {getItem() {return null;}, setItem() {}},
    initializeCameras(options) {
      calls.push(["initializeCameras", {mode: options.mode, enabled: options.enabled}]);
      return {setEnabled(value) {calls.push(["cameraSetEnabled", value]);}, destroy() {}};
    },
    setTimeout(callback) {timers.set(++nextTimer, callback); return nextTimer;},
    clearTimeout(id) {timers.delete(id);},
    fetch: async () => ({ok: true, json: async () => ({type: "FeatureCollection", features: []})}),
    L: {geoJSON(data, options) {
      return {data, options, addTo(target) {target.activeLayers.add(this); return this;}};
    }},
    __map: map, __calls: calls,
  });
  vm.runInContext(core, context, {filename: "index.html:inline"});
  vm.runInContext(controls, context, {filename: "assets/map-ui.js"});
  vm.runInContext(`map = __map; activeRenderMode = ${JSON.stringify(mode)};`, context);
  const forests = plain(vm.runInContext("FORESTS", context));
  for (const id of forests) element(`cb_${id}`).checked = true;
  const run = (source) => vm.runInContext(source, context);
  return {context, run, element, elements, forests, map, location, calls};
}

test("active road popups escape source text and never infer bike/foot permission", () => {
  const {context, forests} = fixture();
  const payload = `<img src=x onerror="alert('road')"> & <script>alert(1)</script>`;
  const feature = {properties: {
    name: payload, seasonal: payload, surfacetype: payload, forest_id: payload, symbol: payload,
    surface: payload, bicycle: payload, foot: payload, access: payload,
    county_road_number: payload, source_detail: payload,
  }};
  for (const popup of [context.mkPopup, context.mkVerifiedPublicRoadPopup, context.countyRoadPopup]) {
    const result = popup(feature);
    assert.ok(!result.includes(payload));
    assert.ok(!/<(?:img|script)\b/i.test(result));
    assert.ok(result.includes("&lt;img"));
  }
  assert.match(context.mkPopup({properties: {forest_id: forests[0]}}), /openForestMaps/);
  assert.match(context.countyRoadPopup({properties: {name: "County road", surface: "dirt"}}), /Not recorded/);
  assert.match(context.mkVerifiedPublicRoadPopup({properties: {surface: "dirt"}}), /public access is not confirmed/);
});

test("classic road popup content waits for a detailed zoom and is built once", () => {
  const f = fixture("classic");
  let click, htmlCalls = 0, popup = null, opens = 0;
  const layer = {
    on(event, listener) {assert.equal(event, "click"); click = listener;},
    getPopup: () => popup, bindPopup(html) {popup = html;}, openPopup() {opens++;},
  };
  f.context.bindLazyPopup(layer, () => {htmlCalls++; return "Road details";});
  f.map.setView([33, -116], 6);
  click();
  assert.equal(htmlCalls, 0);
  assert.equal(opens, 0);
  f.map.setView([33, -116], 12);
  click(); click();
  assert.equal(htmlCalls, 1);
  assert.equal(opens, 2);
});

function expressionValue(expression, props) {
  if (!Array.isArray(expression)) return expression;
  const [operator, ...args] = expression;
  const value = (item) => expressionValue(item, props);
  if (operator === "get") return props[args[0]];
  if (operator === "literal") return args[0];
  if (operator === "==") return value(args[0]) === value(args[1]);
  if (operator === "in") return value(args[1]).includes(value(args[0]));
  if (operator === "!") return !value(args[0]);
  if (operator === "all") return args.every(value);
  throw new Error(`Unsupported test expression ${operator}`);
}

test("classic and vector apply the same forest, surface and visibility filters", () => {
  const f = fixture();
  const features = [];
  for (const forest of f.forests.slice(0, 2)) {
    for (const symbol of ["1", "2", "3", "4"]) {
      for (const surface of ["NATIVE MATERIAL", "GRAVEL", "ASPHALT", "UNKNOWN"]) {
        features.push({type: "Feature", id: features.length, properties: {forest_id: forest, symbol, surfacetype: surface}});
      }
    }
  }
  f.context.__features = features;
  for (const enabled of [true, false]) {
    for (const surface of ["all", "unpaved"]) {
      for (const selected of [[], [f.forests[0]], f.forests.slice(0, 2)]) {
        for (const id of f.forests) f.element(`cb_${id}`).checked = selected.includes(id);
        f.run(`forestRoadsEnabled = ${enabled}; surfaceFilter = ${JSON.stringify(surface)}; applyForestFilters();`);
        const classic = plain(f.run("filterTileGeoJson({features:__features}).features.map((f)=>f.id)"));
        const vector = features.filter((feature) => [...f.map.layers].some(([id, layer]) =>
          id.startsWith("fr_") && layer.layout.visibility !== "none" && expressionValue(layer.filter, feature.properties)
        )).map((feature) => feature.id);
        assert.deepEqual(vector, classic, JSON.stringify({enabled, surface, selected}));
      }
    }
  }
});

test("shared URL state roundtrips camera, disabled layers, surface and empty/partial forest selections", () => {
  for (const mode of ["classic", "vector"]) {
    for (const selection of [[], [0, 2]]) {
      const original = fixture(mode, "https://example.test/maps/?mode=" + mode);
      for (const [index, id] of original.forests.entries()) original.element(`cb_${id}`).checked = selection.includes(index);
      original.run("camerasEnabled=false; countyRoadsEnabled=false; verifiedPublicRoadsEnabled=false; forestRoadsEnabled=false; boundariesEnabled=false; surfaceFilter='unpaved'; writeMapState();");
      const restored = fixture(mode, original.location.href);
      restored.run("readMapState();");
      assert.deepEqual(plain(restored.run("[camerasEnabled,countyRoadsEnabled,verifiedPublicRoadsEnabled,forestRoadsEnabled,boundariesEnabled,surfaceFilter]")), [false, false, false, false, false, "unpaved"]);
      assert.deepEqual(plain(restored.run("Array.from(selectedForests())")), selection.map((index) => restored.forests[index]));
      for (const id of ["camerasToggle", "countyRoadsToggle", "verifiedPublicRoadsToggle", "forestRoadsToggle", "boundariesToggle"]) assert.equal(restored.element(id).checked, false);
      assert.equal(restored.element("surfaceFilter").value, "unpaved");
      assert.deepEqual(restored.map.getCenter(), {lat: 33.56789, lng: -116.12345});
      assert.equal(restored.map.getZoom(), 12.25);
      assert.equal(new URL(restored.location.href).searchParams.get("mode"), mode);
    }
  }
});

test("invalid shared camera positions are ignored and vector zoom is bounded", () => {
  for (const camera of ["NaN,33,-116", "12,91,-116", "12,33,181", "99,33,-116"]) {
    const f = fixture("vector", "https://example.test/#map=" + camera);
    f.run("readMapState();");
    assert.equal(f.calls.length, 0, camera);
  }
  const f = fixture("vector", "https://example.test/#map=19,33,-116");
  f.run("readMapState();");
  assert.equal(f.map.getZoom(), 14);
});

test("restored vector filter controls affect layers after initialization", async () => {
  const f = fixture("vector", "https://example.test/#map=12,33,-116&forest=0&candidates=0&county=0&land=0&surface=unpaved&forests=");
  await f.context.initializeMapUI();
  await settleAsyncWork();
  for (const [id, layer] of f.map.layers) {
    if (id.startsWith("fr_") || ["county-roads", "verified_public_roads", "forest-boundary-fill", "forest-boundary-line"].includes(id)) {
      assert.equal(layer.layout.visibility, "none", id);
    }
  }
  assert.deepEqual(plain(f.run("Array.from(selectedForests())")), []);
});

test("restored camera visibility initializes the controller and its toggle updates it", async () => {
  for (const mode of ["classic", "vector"]) {
    const f = fixture(mode, "https://example.test/#map=12,33,-116&cameras=0");
    f.run("syncRoadTiles=async()=>{}; syncVerifiedPublicRoadTiles=async()=>{};");
    await f.context.initializeMapUI();
    assert.deepEqual(plain(f.calls.find((call) => call[0] === "initializeCameras")), ["initializeCameras", {mode, enabled: false}]);
    const toggle = f.element("camerasToggle");
    assert.equal(toggle.checked, false);
    for (const callback of toggle.listeners.get("change")) callback({target: {checked: true}});
    assert.deepEqual(plain(f.calls.find((call) => call[0] === "cameraSetEnabled")), ["cameraSetEnabled", true]);
    assert.equal(f.run("camerasEnabled"), true);
  }
});

test("shared vector filters also survive a deferred map load", async () => {
  const f = fixture("vector", "https://example.test/#map=12,33,-116&forest=0&candidates=0&county=0&land=0");
  f.map.isStyleLoaded = () => false;
  await f.context.initializeMapUI();
  assert.equal(f.map.sources.size, 0, "layer setup waits for the style");
  f.map.emit("load");
  await settleAsyncWork();
  assert.equal(f.map.layers.get("fr_unpaved_1").layout.visibility, "none");
  assert.equal(f.map.layers.get("verified_public_roads").layout.visibility, "none");
  assert.equal(f.map.layers.get("county-roads").layout.visibility, "none");
});

test("vector library/archive failure switches controls and runtime to classic", async () => {
  for (const failure of ["library", "archive"]) {
    const f = fixture("vector");
    f.run(`
      map=null;
      createClassicMap=()=>{map=__map;return map;};
      loadExternalCss=()=>{};
      loadExternalScript=async()=>${failure === "library" ? "{throw new Error('Unavailable');}" : "{}"};
      loadRoadTileManifest=async()=>{};
      loadVerifiedPublicRoadTileManifest=async()=>{};
      syncRoadTiles=async()=>{};
      syncVerifiedPublicRoadTiles=async()=>{};
      loadPublicLands=async()=>{};
    `);
    f.context.fetch = async () => ({ok: false, status: 404});
    await f.context.initVectorMode();
    assert.equal(f.run("activeRenderMode"), "classic", failure);
    assert.equal(f.element("modeSelect").value, "classic", failure);
  }
});

test("classic restores shared filters before its first tile synchronization", async () => {
  const f = fixture("classic", "https://example.test/#map=12,33,-116&forest=0&candidates=0&forests=");
  f.run(`
    loadRoadTileManifest = async () => {};
    loadVerifiedPublicRoadTileManifest = async () => {};
    syncRoadTiles = async () => __calls.push(['initial-forest-sync', forestRoadsEnabled, Array.from(selectedForests())]);
    syncVerifiedPublicRoadTiles = async () => __calls.push(['initial-candidate-sync']);
    loadPublicLands = async () => {};
  `);
  await f.context.initClassicMode();
  const sync = f.calls.find((call) => call[0] === "initial-forest-sync");
  if (sync) assert.deepEqual(plain(sync), ["initial-forest-sync", false, []]);
  assert.ok(!f.calls.some((call) => call[0] === "initial-candidate-sync"));
});

test("classic overview does not request statewide forest/candidate GeoJSON tiles", () => {
  const f = fixture("classic");
  f.map.setView([37, -119], 6);
  f.run("roadTileManifest={tiles:{}}; verifiedPublicRoadsManifest={tiles:{}};");
  assert.deepEqual(plain(f.run("getWantedRoadTileKeys()")), []);
  assert.deepEqual(plain(f.run("getWantedVerifiedPublicRoadTileKeys()")), []);
});

test("tiles finishing after pan or disable do not restore stale forest/candidate layers", async () => {
  for (const [sync, wanted, ensure, render] of [
    ["syncRoadTiles", "getWantedRoadTileKeys", "ensureRoadTileLoaded", "renderRoadTile"],
    ["syncVerifiedPublicRoadTiles", "getWantedVerifiedPublicRoadTileKeys", "ensureVerifiedPublicRoadTileLoaded", "renderVerifiedPublicRoadTile"],
  ]) {
    const f = fixture("classic"), request = deferred();
    f.context.__request = request.promise;
    f.context.__wanted = ["10/160/380"];
    f.run(`${wanted} = () => __wanted; ${ensure} = () => __request; ${render} = (key) => __calls.push(['render',key]);`);
    const pending = f.context[sync]();
    f.context.__wanted = [];
    await f.context[sync]();
    request.resolve({type: "FeatureCollection", features: []});
    await pending;
    assert.ok(!f.calls.some((call) => call[0] === "render"), sync);
  }
});

test("failed forest/candidate fetches leave no in-flight entry and can retry", async () => {
  for (const [ensure, inflight, cache] of [
    ["ensureRoadTileLoaded", "roadTileInflight", "roadTileCache"],
    ["ensureVerifiedPublicRoadTileLoaded", "verifiedPublicRoadTileInflight", "verifiedPublicRoadTileCache"],
  ]) {
    const f = fixture("classic");
    f.context.fetch = async () => {throw new Error("Offline");};
    await assert.rejects(f.context[ensure]("10/160/380"), /Offline/);
    assert.equal(f.run(`${inflight}.size`), 0);
    f.context.fetch = async () => ({ok: true, json: async () => ({type: "FeatureCollection", features: []})});
    await f.context[ensure]("10/160/380");
    assert.equal(f.run(`${cache}.has('10/160/380')`), true);
  }
});

test("county toggle changed during a fetch remains off when data arrives", async () => {
  for (const mode of ["classic", "vector"]) {
    const f = fixture(mode), request = deferred();
    f.context.fetch = () => request.promise;
    const pending = f.context.loadCountyRoads();
    f.run("countyRoadsEnabled=false;");
    request.resolve({ok: true, json: async () => ({type: "FeatureCollection", features: []})});
    await pending;
    if (mode === "classic") assert.equal(f.map.activeLayers.size, 0);
    else assert.equal(f.map.layers.get("county-roads").layout.visibility, "none");
  }
});

test("classic boundaries toggled off during a fetch stay off when data arrives", async () => {
  const f = fixture("classic"), request = deferred();
  f.context.fetch = () => request.promise;
  const pending = f.context.loadPublicLands();
  f.run("boundariesEnabled=false;");
  request.resolve({ok: true, json: async () => ({type: "FeatureCollection", features: []})});
  await pending;
  assert.equal(f.map.activeLayers.size, 0);
});

test("aborted prior searches cannot enable the button while a later search is pending", async () => {
  const f = fixture(), first = deferred(), second = deferred();
  f.run("getSearchViewbox = () => null;");
  let requests = 0;
  f.context.fetch = () => (++requests === 1 ? first.promise : second.promise);
  f.element("searchInput").value = "First place";
  const oldSearch = f.context.runSearch();
  f.element("searchInput").value = "Second place";
  const newSearch = f.context.runSearch();
  first.reject(Object.assign(new Error("aborted"), {name: "AbortError"}));
  await oldSearch;
  assert.equal(f.element("searchButton").disabled, true);
  second.resolve({ok: true, json: async () => []});
  await newSearch;
  assert.equal(f.element("searchButton").disabled, false);
  assert.match(f.element("searchStatus").textContent, /No matches/);
});

test("clearing a pending search prevents its late results from returning", async () => {
  const f = fixture(), request = deferred();
  f.run("getSearchViewbox = () => null;");
  f.context.fetch = () => request.promise;
  f.element("searchInput").value = "Place";
  const pending = f.context.runSearch();
  f.element("searchInput").value = "";
  await f.context.runSearch();
  request.resolve({ok: true, json: async () => [{display_name: "Old place", lat: "33", lon: "-116"}]});
  await pending;
  assert.match(f.element("searchStatus").textContent, /Enter a place/);
  assert.equal(f.element("searchResults").children.length, 0);
  assert.equal(f.element("searchButton").disabled, false);
});
