let countyRoadsEnabled = true;
let countyRoadsLayer = null;
let camerasEnabled = true;
let cameraController = null;
function countyRoadPopup(feature) {
  const p = feature.properties || {};
  return `<div class="road-popup"><span class="popup-source">COUNTY MAINTAINED ROAD</span><h3>${escapeHtml(p.name || "Unnamed county road")}</h3><p class="surface-badge">${escapeHtml(humanizeKey(p.surface))}</p><dl><dt>Source</dt><dd>Riverside County</dd><dt>Road ID</dt><dd>${escapeHtml(p.county_road_number || p.road_id)}</dd><dt>Bike / foot</dt><dd>Not recorded</dd></dl><p class="popup-note">Surface and maintenance come directly from county records. Check current signs and restrictions.</p><a href="https://gis.countyofriverside.us/arcgis_mapping/rest/services/Transportation/04_Online_Road_Book_Mapping_v2/MapServer/1" target="_blank" rel="noreferrer">County road inventory ↗</a></div>`;
}
async function loadCountyRoads() {
  try {
    const response = await fetch("_county_roads/riverside.geojson");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (activeRenderMode === "vector") {
      map.addSource("county-roads", {type:"geojson",data});
      map.addLayer({id:"county-roads",type:"line",source:"county-roads",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#995127","line-width":["interpolate",["linear"],["zoom"],5,0.7,10,2,14,3.2]}});
      map.on("mouseenter", "county-roads", () => {map.getCanvas().style.cursor = "pointer";});
      map.on("mouseleave", "county-roads", () => {map.getCanvas().style.cursor = "";});
    } else {
      countyRoadsLayer = L.geoJSON(data,{style:{color:"#995127",weight:2.5,opacity:0.9},renderer:publicRoadsRenderer,onEachFeature:(feature,layer) => bindLazyPopup(layer,()=>countyRoadPopup(feature))});
    }
    applyCountyVisibility();
  } catch (error) { console.error("County roads could not load",error); showActionStatus("County road data could not load. Reload to try again."); }
}
function applyCountyVisibility() {
  if (activeRenderMode === "vector") {
    if (map.getLayer("county-roads")) map.setLayoutProperty("county-roads","visibility",countyRoadsEnabled ? "visible" : "none");
  } else if (countyRoadsLayer) {
    if (countyRoadsEnabled) countyRoadsLayer.addTo(map); else map.removeLayer(countyRoadsLayer);
  }
}
/* Small, shared controls for both map renderers. */
function setLayersOpen(open) {
  document.getElementById("layersPanel").hidden = !open;
  document.getElementById("layersButton").setAttribute("aria-expanded", String(open));
}
function openForestMaps(id) {
  setLayersOpen(true);
  const list = document.getElementById("forestList");
  list.closest("details").open = true;
  const item = document.getElementById(`pdfs_${id}`);
  if (item) { item.open = true; item.scrollIntoView({ block: "nearest" }); }
}
function applyForestFilters() {
  if (!map) return;
  if (activeRenderMode === "vector") {
    const selected = Array.from(selectedForests());
    ["1", "2", "3", "4"].forEach((symbol) => {
      ["paved", "unpaved"].forEach((surface) => {
        const id = `fr_${surface}_${symbol}`;
        if (!map.getLayer(id)) return;
        map.setLayoutProperty(id, "visibility", forestRoadsEnabled && (surfaceFilter !== "unpaved" || surface === "unpaved") ? "visible" : "none");
        map.setFilter(id, ["all", ["==", ["get", "symbol"], symbol], vectorSurfaceFilter(surface === "unpaved"), ["in", ["get", "forest_id"], ["literal", selected]]]);
      });
    });
  } else syncRoadTiles();
}
function applyBoundariesVisibility() {
  if (activeRenderMode === "vector") {
    ["forest-boundary-fill", "forest-boundary-line"].forEach((id) => {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", boundariesEnabled ? "visible" : "none");
    });
  } else if (publicLandsLayer) {
    if (boundariesEnabled) publicLandsLayer.addTo(map);
    else map.removeLayer(publicLandsLayer);
  }
}
async function addVectorBoundaries() {
  if (map.getSource("forest-boundaries")) return;
  map.addSource("forest-boundaries", {type:"geojson", data:"_public_lands/ca_forest_boundaries.geojson"});
  map.addLayer({id:"forest-boundary-fill", type:"fill", source:"forest-boundaries", paint:{"fill-color":"#789768","fill-opacity":0.09}}, "fr_paved_1");
  map.addLayer({id:"forest-boundary-line", type:"line", source:"forest-boundaries", paint:{"line-color":"#789768","line-width":0.7,"line-opacity":0.3}}, "fr_paved_1");
  applyBoundariesVisibility();
}
let actionStatusTimer;
function showActionStatus(message) {
  const element = document.getElementById("actionStatus");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(actionStatusTimer);
  actionStatusTimer = setTimeout(() => { element.hidden = true; }, 4500);
}
function readMapState() {
  const params = new URLSearchParams(location.hash.slice(1));
  camerasEnabled = params.get("cameras") !== "0";
  document.getElementById("camerasToggle").checked = camerasEnabled;
  const parts = (params.get("map") || "").split(",").map(Number);
  if (parts.length === 3 && parts.every(Number.isFinite) && parts[0] >= 2 && parts[0] <= 19 && Math.abs(parts[1]) <= 85 && Math.abs(parts[2]) <= 180) {
    if (activeRenderMode === "vector") map.jumpTo({center:[parts[2],parts[1]], zoom:Math.min(parts[0],map.getMaxZoom())});
    else map.setView([parts[1],parts[2]],parts[0]);
  }
  if (params.has("county")) countyRoadsEnabled = params.get("county") !== "0";
  document.getElementById("countyRoadsToggle").checked = countyRoadsEnabled;
  if (params.has("candidates")) verifiedPublicRoadsEnabled = params.get("candidates") !== "0";
  if (params.has("forest")) forestRoadsEnabled = params.get("forest") !== "0";
  if (params.has("land")) boundariesEnabled = params.get("land") !== "0";
  surfaceFilter = params.get("surface") === "unpaved" ? "unpaved" : "all";
  document.getElementById("verifiedPublicRoadsToggle").checked = verifiedPublicRoadsEnabled;
  document.getElementById("forestRoadsToggle").checked = forestRoadsEnabled;
  document.getElementById("boundariesToggle").checked = boundariesEnabled;
  document.getElementById("surfaceFilter").value = surfaceFilter;
  if (params.has("forests")) {
    const selected = new Set(params.get("forests").split(","));
    FORESTS.forEach((id) => { document.getElementById(`cb_${id}`).checked = selected.has(id); });
  }
}
function writeMapState() {
  const center = map.getCenter();
  const params = new URLSearchParams({map:`${map.getZoom().toFixed(2)},${center.lat.toFixed(5)},${center.lng.toFixed(5)}`});
  if (!countyRoadsEnabled) params.set("county","0");
  if (!camerasEnabled) params.set("cameras","0");
  if (!verifiedPublicRoadsEnabled) params.set("candidates","0");
  if (!forestRoadsEnabled) params.set("forest","0");
  if (!boundariesEnabled) params.set("land","0");
  if (surfaceFilter !== "all") params.set("surface",surfaceFilter);
  const forests = Array.from(selectedForests());
  if (forests.length !== FORESTS.length) params.set("forests",forests.join(","));
  history.replaceState(null,"",`${location.pathname}${location.search}#${params}`);
}
async function initializeMapUI() {
  // Vector mode uses the same forest list and PDF links as the classic map.
  const list = document.getElementById("forestList");
  if (!list.dataset.ready) {
    FORESTS.forEach((id) => {
      list.appendChild(buildForestItem(id));
      document.getElementById(`cb_${id}`).checked = true;
      renderForestMeta(id); renderPdfLinks(id);
    });
    list.dataset.ready = "1";
  }
  document.getElementById("searchButton").addEventListener("click", runSearch);
  document.getElementById("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); runSearch(); }
  });
  readMapState();
  const ready = () => {
    if (!new URLSearchParams(location.hash.slice(1)).has("map")) {
      if (activeRenderMode === "vector") map.fitBounds([[-124.48,32.53],[-114.13,42.01]], {padding:{top:110,bottom:100,left:35,right:35},maxZoom:6.5,duration:0});
      else map.fitBounds([[32.53,-124.48],[42.01,-114.13]],{paddingTopLeft:[35,130],paddingBottomRight:[35,100],maxZoom:7});
    }
    loadCountyRoads();
    if (typeof initializeCameras === "function") {
      cameraController = initializeCameras({
        map, mode: activeRenderMode, enabled: camerasEnabled,
        onStatus: (message) => { document.getElementById("cameraStatus").textContent = message || "Images appear when you zoom in."; }
      });
    } else {
      document.getElementById("camerasToggle").disabled = true;
      document.getElementById("cameraStatus").textContent = "Camera layer unavailable. Reload to retry.";
    }
    if (activeRenderMode === "vector") {
      addVectorBoundaries();
      map.on("click", (event) => {
        if (map.getZoom() < FOREST_INTERACTIVE_MIN_ZOOM) return;
        const layers = ["county-roads", ...["1","2","3","4"].flatMap((symbol) => [`fr_paved_${symbol}`, `fr_unpaved_${symbol}`]), VERIFIED_PUBLIC_VECTOR_LAYER_ID].filter((id) => map.getLayer(id));
        const features = map.queryRenderedFeatures([[event.point.x-6,event.point.y-6],[event.point.x+6,event.point.y+6]], {layers});
        const feature = features.find((f) => f.layer.id === "county-roads") || features.find((f) => f.layer.id.startsWith("fr_")) || features[0];
        if (!feature) return;
        const html = feature.layer.id === "county-roads" ? countyRoadPopup(feature) : feature.layer.id.startsWith("fr_") ? mkPopup(feature) : mkVerifiedPublicRoadPopup(feature);
        new maplibregl.Popup({offset:8}).setLngLat(event.lngLat).setHTML(html).addTo(map);
      });
    }
    applyForestFilters();
    applyBoundariesVisibility();
    setVerifiedPublicRoadsEnabled(verifiedPublicRoadsEnabled);
    updateInteractivityHint();
  };
  if (activeRenderMode === "vector" && !map.isStyleLoaded()) map.once("load",ready);
  else ready();
  document.getElementById("layersButton").addEventListener("click", () => setLayersOpen(document.getElementById("layersPanel").hidden));
  document.getElementById("closeLayers").addEventListener("click", () => {setLayersOpen(false);document.getElementById("layersButton").focus();});
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {setLayersOpen(false);cancelSearch();renderSearchResults([]);setSearchStatus("");}
  });
  document.getElementById("countyRoadsToggle").addEventListener("change", (event) => {countyRoadsEnabled = event.target.checked;applyCountyVisibility();});
  document.getElementById("camerasToggle").addEventListener("change", (event) => {camerasEnabled = event.target.checked;cameraController?.setEnabled(camerasEnabled);});
  document.getElementById("forestRoadsToggle").addEventListener("change", (event) => {forestRoadsEnabled = event.target.checked;applyForestFilters();});
  document.getElementById("boundariesToggle").addEventListener("change", (event) => {boundariesEnabled = event.target.checked;applyBoundariesVisibility();});
  document.getElementById("surfaceFilter").addEventListener("change", (event) => {surfaceFilter = event.target.value;applyForestFilters();});
  document.getElementById("layersPanel").addEventListener("change", writeMapState);
  map.on("moveend", writeMapState);
  document.getElementById("shareButton").addEventListener("click", async () => {
    writeMapState();
    try {await navigator.clipboard.writeText(location.href);showActionStatus("Map link copied");}
    catch {showActionStatus("Copy the address from your browser to share this view.");}
  });
  document.getElementById("locateButton").addEventListener("click", () => {
    if (!navigator.geolocation) return showActionStatus("Location is not supported in this browser.");
    showActionStatus("Finding your location…");
    navigator.geolocation.getCurrentPosition((position) => {
      const {latitude,longitude} = position.coords;
      focusSearchResult({lat:latitude,lon:longitude,display_name:"Your location"});
      showActionStatus("Showing your location");
    }, () => showActionStatus("Location unavailable. You can search for a nearby place."), {timeout:10000,maximumAge:60000});
  });
}
