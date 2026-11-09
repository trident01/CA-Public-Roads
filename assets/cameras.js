/* Official public map feed: https://alertcalifornia.org/about/
 * Imagery: ALERTCalifornia | UC San Diego; https://alertcalifornia.org/terms/
 * Full images preserve the information bar and partner marks. No polling.
 */
(() => {
  "use strict";
  const FEED = "https://services8.arcgis.com/X84q166Srnyl4JMV/ArcGIS/rest/services/ALERTCalifornia_Camera_Feed/FeatureServer/0/query";
  const CREDIT = "ALERTCalifornia | UC San Diego";
  const FIELDS = "OBJECTID,cameraName,cameraURL,imageURL,isOnline,viewTime,siteId,isActive";
  const MIN_ZOOM = 6, IMAGE_ZOOM = 11, PAGE_SIZE = 50, MAX_CARDS = 8, MAX_MARKERS = 64;
  const CACHE_AGE = 5 * 60 * 1000, MAX_QUERY_CAMERAS = 2000;
  const CALIFORNIA = {west: -124.6, south: 32.3, east: -113.9, north: 42.1};

  function cameraUrl(value, image = false) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "cameras.alertcalifornia.org"
          || url.port || url.username || url.password) return null;
      if (image) {
        if (!/^\/public-camera-data\/[A-Za-z0-9_-]+\/latest-frame\.(?:jpg|jpeg|png)$/.test(url.pathname)) return null;
      } else if (url.pathname !== "/" || !/^[A-Za-z0-9_-]+$/.test(url.searchParams.get("id") || "")) return null;
      return url.href;
    } catch { return null; }
  }

  function normalizeCamera(feature) {
    const p = feature.attributes || {}, point = feature.geometry || {};
    const url = cameraUrl(p.cameraURL);
    if (!url || p.isActive !== "active" || !Number.isInteger(p.OBJECTID)
        || !Number.isFinite(point.x) || !Number.isFinite(point.y)
        || Math.abs(point.x) > 180 || Math.abs(point.y) > 85) return null;
    const reportedTime = typeof p.viewTime === "string" ? p.viewTime.trim().slice(0, 80) : "";
    // The published feed currently includes UTC offsets. Do not invent the
    // viewer's local zone if a future record provides only a wall-clock time.
    const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(reportedTime);
    const date = hasZone ? new Date(reportedTime.replace(" ", "T")) : null;
    const timestamp = date && Number.isFinite(date.getTime()) ? date.getTime() : null;
    const sourceTime = !hasZone && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(reportedTime)
      ? reportedTime.replace("T", " ") : "";
    return {
      id: p.OBJECTID, name: String(p.cameraName || "Landscape camera").slice(0, 120),
      lng: point.x, lat: point.y, url, image: cameraUrl(p.imageURL, true),
      online: p.isOnline === "online", timestamp, sourceTime,
      site: String(p.siteId || ""),
    };
  }

  function boundsOf(map) {
    const b = map.getBounds();
    const raw = {west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth()};
    if (!Object.values(raw).every(Number.isFinite) || raw.west < -180 || raw.east > 180
        || raw.west >= raw.east || raw.south >= raw.north || raw.east - raw.west >= 180
        || raw.south < -90 || raw.north > 90) return null;
    const bounds = {west: Math.max(CALIFORNIA.west, raw.west), south: Math.max(CALIFORNIA.south, raw.south),
      east: Math.min(CALIFORNIA.east, raw.east), north: Math.min(CALIFORNIA.north, raw.north)};
    return Object.values(bounds).every(Number.isFinite) && bounds.west < bounds.east && bounds.south < bounds.north ? bounds : null;
  }

  function contains(outer, inner) {
    return outer.west <= inner.west && outer.east >= inner.east && outer.south <= inner.south && outer.north >= inner.north;
  }

  function queryBounds(view) {
    const dx = (view.east - view.west) * 0.2, dy = (view.north - view.south) * 0.2;
    return {west: Math.max(CALIFORNIA.west, Math.floor((view.west - dx) * 20) / 20),
      south: Math.max(CALIFORNIA.south, Math.floor((view.south - dy) * 20) / 20),
      east: Math.min(CALIFORNIA.east, Math.ceil((view.east + dx) * 20) / 20),
      north: Math.min(CALIFORNIA.north, Math.ceil((view.north + dy) * 20) / 20)};
  }

  async function getJson(params, signal) {
    const response = await fetch(`${FEED}?${new URLSearchParams({f: "json", ...params})}`, {signal, credentials: "omit"});
    if (!response.ok) throw new Error(`Camera feed HTTP ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error("Camera feed query failed");
    return data;
  }

  async function fetchCameras(bounds, signal) {
    // returnIdsOnly is not subject to the layer's 50-feature page limit.
    const index = await getJson({where: "isActive = 'active'", returnIdsOnly: "true",
      geometry: `${bounds.west},${bounds.south},${bounds.east},${bounds.north}`,
      geometryType: "esriGeometryEnvelope", inSR: "4326", spatialRel: "esriSpatialRelIntersects"}, signal);
    if (index.exceededTransferLimit || !Array.isArray(index.objectIds)
        || index.objectIds.some((id) => !Number.isInteger(id))
        || new Set(index.objectIds).size !== index.objectIds.length) throw new Error("Camera index was incomplete");
    const ids = [...index.objectIds].sort((a, b) => a - b);
    if (ids.length > MAX_QUERY_CAMERAS) throw new Error("Too many cameras; zoom in");
    const cameras = [];
    let omitted = 0;
    for (let offset = 0; offset < ids.length; offset += PAGE_SIZE) {
      const batch = ids.slice(offset, offset + PAGE_SIZE);
      const page = await getJson({objectIds: batch.join(","), outFields: FIELDS, returnGeometry: "true",
        outSR: "4326", resultRecordCount: String(PAGE_SIZE), orderByFields: "OBJECTID ASC"}, signal);
      if (page.exceededTransferLimit || !Array.isArray(page.features)) throw new Error("Camera page was incomplete");
      const returned = page.features.map((feature) => feature.attributes?.OBJECTID);
      if (returned.length !== batch.length || new Set(returned).size !== batch.length
          || returned.some((id) => !batch.includes(id))) throw new Error("Camera page omitted records");
      for (const feature of page.features) {
        const camera = normalizeCamera(feature);
        if (camera) cameras.push(camera); else omitted++;
      }
    }
    return {cameras, omitted};
  }

  function groupsInView(cameras, bounds) {
    const groups = new Map();
    for (const camera of cameras) {
      if (camera.lng < bounds.west || camera.lng > bounds.east || camera.lat < bounds.south || camera.lat > bounds.north) continue;
      const key = `${camera.lng.toFixed(4)},${camera.lat.toFixed(4)}`;
      const group = groups.get(key) || [];
      group.push(camera); groups.set(key, group);
    }
    return [...groups.values()].map((group) => {
      group.sort((a, b) => Number(b.online) - Number(a.online) || (b.timestamp || 0) - (a.timestamp || 0) || a.id - b.id);
      return {...group[0], count: group.length};
    });
  }

  function markerElement(camera, card) {
    const link = document.createElement("a");
    link.className = `alert-camera-marker ${card ? "alert-camera-card" : "alert-camera-icon"}`;
    link.href = camera.url; link.target = "_blank"; link.rel = "noopener noreferrer";
    const time = camera.timestamp !== null ? new Date(camera.timestamp).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    }) : camera.sourceTime ? `${camera.sourceTime} (time zone not supplied)` : "Time not reported";
    link.title = `${camera.name}${camera.count > 1 ? ` (+${camera.count - 1} colocated views)` : ""} — ${camera.online ? "reported view: " + time : "camera offline"}. Open camera history. ${CREDIT}`;
    link.setAttribute("aria-label", link.title);
    for (const event of ["click", "dblclick", "mousedown", "pointerdown"]) link.addEventListener(event, (e) => e.stopPropagation());
    if (card) {
      const name = document.createElement("span"); name.className = "alert-camera-name"; name.textContent = camera.name;
      const preview = document.createElement("span"); preview.className = "alert-camera-preview";
      const placeholder = document.createElement("span"); placeholder.className = "alert-camera-placeholder";
      placeholder.textContent = camera.online ? "Preview unavailable" : "Camera offline";
      preview.appendChild(placeholder);
      if (camera.online && camera.image) {
        const image = document.createElement("img");
        // At most eight cards are created, already inside the viewport. Eager
        // loading also works while the image is hidden behind its placeholder.
        image.alt = `${camera.name} — ${CREDIT}`; image.loading = "eager"; image.decoding = "async";
        image.referrerPolicy = "no-referrer"; image.hidden = true;
        placeholder.textContent = "Loading preview";
        image.addEventListener("load", () => {placeholder.hidden = true; image.hidden = false;}, {once: true});
        image.addEventListener("error", () => {image.remove(); placeholder.hidden = false; placeholder.textContent = "Preview unavailable";}, {once: true});
        image.src = camera.image; preview.appendChild(image);
      }
      const timestamp = document.createElement("span"); timestamp.className = "alert-camera-time";
      timestamp.textContent = camera.sourceTime ? `View: ${camera.sourceTime.slice(11, 16)} · zone not supplied`
        : camera.timestamp !== null ? `View: ${time}` : time;
      timestamp.title = time;
      const credit = document.createElement("span"); credit.className = "alert-camera-image-credit"; credit.textContent = CREDIT;
      link.append(name, preview, timestamp, credit);
    } else {
      const icon = document.createElement("span"); icon.className = "alert-camera-symbol"; icon.setAttribute("aria-hidden", "true");
      // A static vector icon; no remote image is requested at overview zooms.
      icon.innerHTML = '<svg width="15" height="13" viewBox="0 0 20 16" fill="none"><path d="M2 4h4l1.5-2h5L14 4h4v10H2V4Z" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="9" r="3" stroke="currentColor" stroke-width="1.5"/></svg>';
      link.appendChild(icon);
      if (!camera.online) link.classList.add("is-offline");
    }
    if (camera.count > 1) {
      const count = document.createElement("span"); count.className = "alert-camera-count"; count.textContent = `+${camera.count - 1}`;
      link.appendChild(count);
    }
    return link;
  }

  function initializeCameras({map, mode, enabled = true, onStatus = () => {}}) {
    if (!map || !["classic", "vector"].includes(mode)) throw new Error("Camera layer needs a map and classic/vector mode");
    let destroyed = false, timer = null, controller = null, generation = 0, lastNotice = -Infinity, lastStatus = "";
    const markers = new Map(), cache = [];
    const notify = (message) => {if (message !== lastStatus || Date.now() - lastNotice > 60000) {lastStatus = message; lastNotice = Date.now(); onStatus(message);}};
    const clearStatus = () => {if (lastStatus) {lastStatus = ""; onStatus("");}};
    const remove = (entry) => mode === "vector" ? entry.marker.remove() : map.removeLayer(entry.marker);
    const clear = () => {for (const entry of markers.values()) remove(entry); markers.clear();};
    const cancel = () => {generation++; clearTimeout(timer); timer = null; controller?.abort(); controller = null;};
    const visible = () => !destroyed && enabled && map.getZoom() >= MIN_ZOOM;

    function render(cameras, bounds) {
      const withImages = map.getZoom() >= IMAGE_ZOOM;
      const container = map.getContainer(), rectangle = container.getBoundingClientRect();
      const width = container.clientWidth || rectangle.width, height = container.clientHeight || rectangle.height;
      const header = document.querySelector(".map-header")?.getBoundingClientRect();
      const headerBox = header ? {left: header.left - rectangle.left - 6, right: header.right - rectangle.left + 6,
        top: header.top - rectangle.top - 6, bottom: header.bottom - rectangle.top + 6} : null;
      const center = {lng: (bounds.west + bounds.east) / 2, lat: (bounds.south + bounds.north) / 2};
      const candidates = groupsInView(cameras, bounds).map((camera) => ({...camera,
        point: mode === "vector" ? map.project([camera.lng, camera.lat]) : map.latLngToContainerPoint([camera.lat, camera.lng]),
        distance: (camera.lng - center.lng) ** 2 + (camera.lat - center.lat) ** 2,
      })).sort((a, b) => Number(b.online) - Number(a.online) || a.distance - b.distance || a.id - b.id);
      const occupied = [], wanted = new Set();
      let cards = 0;
      const overlap = (box) => occupied.some((other) => box.left < other.right + 8 && box.right > other.left - 8 && box.top < other.bottom + 8 && box.bottom > other.top - 8);
      for (const camera of candidates) {
        if (wanted.size >= MAX_MARKERS) break;
        const {x, y} = camera.point;
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        let card = withImages && cards < MAX_CARDS;
        let box = card ? {left: x - 70, right: x + 70, top: y - 134, bottom: y - 8}
          : {left: x - 14, right: x + 14, top: y - 14, bottom: y + 14};
        const behindHeader = headerBox && box.left < headerBox.right && box.right > headerBox.left && box.top < headerBox.bottom && box.bottom > headerBox.top;
        if (card && (overlap(box) || box.left < 8 || box.right > width - 8 || box.top < 8 || box.bottom > height - 8 || behindHeader)) {
          card = false; box = {left: x - 14, right: x + 14, top: y - 14, bottom: y + 14};
        }
        if (overlap(box)) continue;
        occupied.push(box); if (card) cards++;
        wanted.add(camera.id);
        const signature = JSON.stringify([card, camera.name, camera.lat, camera.lng, camera.image, camera.url, camera.online, camera.timestamp, camera.sourceTime, camera.count]);
        const existing = markers.get(camera.id);
        if (existing?.signature === signature) {existing.element.hidden = false; continue;}
        if (existing) remove(existing);
        const element = markerElement(camera, card);
        const marker = mode === "vector"
          ? new maplibregl.Marker({element, anchor: card ? "bottom" : "center", offset: card ? [0, -8] : [0, 0]}).setLngLat([camera.lng, camera.lat]).addTo(map)
          : L.marker([camera.lat, camera.lng], {icon: L.divIcon({className: "alert-camera-shell", html: element,
            iconSize: card ? [140, 126] : [28, 28], iconAnchor: card ? [70, 134] : [14, 14]}),
            keyboard: false, bubblingMouseEvents: false, zIndexOffset: 500}).addTo(map);
        // MapLibre supplies a generic marker label; keep the actual link name.
        element.setAttribute("aria-label", element.title);
        markers.set(camera.id, {marker, element, signature});
      }
      for (const [id, entry] of markers) if (!wanted.has(id)) {remove(entry); markers.delete(id);}
    }

    async function update() {
      timer = null;
      if (!visible()) {clear(); clearStatus(); return;}
      const bounds = boundsOf(map);
      if (!bounds) {clear(); clearStatus(); return;}
      const token = ++generation;
      let timeout = null;
      try {
        const cached = cache.find((entry) => Date.now() - entry.time < CACHE_AGE && contains(entry.bounds, bounds));
        if (cached) {render(cached.cameras, bounds); clearStatus(); return;}
        controller = new AbortController();
        const requestController = controller;
        timeout = setTimeout(() => requestController.abort(), 20000);
        const expanded = queryBounds(bounds);
        const result = await fetchCameras(expanded, requestController.signal);
        if (token !== generation || !visible()) return;
        cache.unshift({bounds: expanded, cameras: result.cameras, time: Date.now()});
        if (cache.length > 24) cache.pop();
        render(result.cameras, bounds);
        if (result.omitted) notify("Some camera previews are unavailable."); else clearStatus();
      } catch (error) {
        if (token !== generation || !visible()) return;
        clear();
        notify(error.message === "Too many cameras; zoom in" ? "Zoom in to see cameras in this area." : "Camera previews are unavailable. Move the map later to retry.");
      } finally {
        clearTimeout(timeout);
        if (token === generation) controller = null;
      }
    }

    function schedule() {
      cancel();
      if (!visible()) {clear(); clearStatus(); return;}
      timer = setTimeout(update, 350);
    }
    function motionStart() {cancel(); for (const entry of markers.values()) entry.element.hidden = true;}
    function zooming() {if (!visible()) {cancel(); clear(); clearStatus();}}
    map.on("movestart", motionStart); map.on("zoomstart", motionStart);
    map.on("moveend", schedule); map.on("zoomend", schedule); map.on("zoom", zooming);
    schedule();
    return {
      setEnabled(value) {if (destroyed) return; enabled = Boolean(value); schedule();},
      destroy() {
        if (destroyed) return;
        destroyed = true; cancel(); clear(); cache.length = 0;
        map.off("movestart", motionStart); map.off("zoomstart", motionStart);
        map.off("moveend", schedule); map.off("zoomend", schedule); map.off("zoom", zooming);
      },
    };
  }

  globalThis.initializeCameras = initializeCameras;
})();
