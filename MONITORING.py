"""
MONITORING.py: Folium-based remote sensing monitoring platform via Google Earth Engine.

Workflow:
1. Map + toggle button "Monitoring Tools" to show/hide side panel
2. Side panel: draw polygon on map
3. Once polygon drawn: show RGB/NDVI layer options
4. Load selected layer (tiles from /tiles endpoint)
5. Optional: plot NDVI timeseries for the polygon

Flask backend:
  /tiles         - POST tile URL for polygon + layer (RGB/NDVI)
  /timeseries    - POST NDVI timeseries for polygon
"""

import os
import json
from flask import Flask, request, jsonify, send_from_directory
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Earth Engine setup
EE_AVAILABLE = False
EE_INITIALIZED = False
EE_INIT_ERROR = None

try:
    import ee
    EE_AVAILABLE = True

    def init_ee_from_env():
        """Initialize Earth Engine via default creds or service account env vars."""
        global EE_INITIALIZED, EE_INIT_ERROR
        try:
            ee.Initialize()
            EE_INITIALIZED = True
            return True, "initialized with default credentials"
        except Exception as default_err:
            pass

        # Try service account
        service_account = os.environ.get('EE_SERVICE_ACCOUNT')
        key_path = os.environ.get('EE_CREDENTIALS_FILE')
        key_json = os.environ.get('EE_PRIVATE_KEY_JSON')

        if service_account:
            try:
                if key_json and not key_path:
                    import tempfile
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
                    tf.write(key_json.encode('utf-8'))
                    tf.close()
                    key_path = tf.name

                if not key_path:
                    return False, "EE_SERVICE_ACCOUNT set but no credentials file provided"

                credentials = ee.ServiceAccountCredentials(service_account, key_path)
                ee.Initialize(credentials)
                EE_INITIALIZED = True
                return True, f"initialized with service account {service_account}"
            except Exception as e:
                EE_INIT_ERROR = str(e)
                return False, f"service-account init failed: {e}"

        EE_INIT_ERROR = str(default_err)
        return False, f"default init failed: {default_err}"

    ok, msg = init_ee_from_env()
    EE_INITIALIZED = ok
    if not ok:
        EE_INIT_ERROR = msg
except Exception as e:
    EE_AVAILABLE = False


@app.route('/')
def index():
    return "MONITORING backend is running. Visit /monitor for the demo frontend."


@app.route('/monitor')
def monitor():
    # Serve the static monitor HTML created alongside this script
    here = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(here, 'monitor.html')


@app.route('/tiles', methods=['POST'])
def tiles():
    """
    POST JSON: {"geometry": <GeoJSON>, "layer": "RGB" or "NDVI", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    Returns: {status: 'ok', tile_url: '...'} or {status: 'error', error: '...'}
    
    Filters out cloudy images (>20% cloud cover) for cleaner composites.
    """
    if not EE_AVAILABLE or not EE_INITIALIZED:
        return jsonify({'status': 'error', 'error': f'EE not initialized: {EE_INIT_ERROR}'}), 500

    data = request.get_json() or {}
    geom = data.get('geometry')
    layer = data.get('layer', 'RGB')
    start = data.get('start')
    end = data.get('end')
    cloud_threshold = int(data.get('cloud_threshold', 20))  # Default 20% cloud cover
    buffer_meters = int(data.get('buffer', 500))

    if not geom:
        return jsonify({'status': 'error', 'error': 'No geometry provided'}), 400

    try:
        # Build ee.Geometry from GeoJSON
        geometry = ee.Geometry(geom)

        clip_geometry = geometry.buffer(buffer_meters).bounds()

        coll_id = os.environ.get('EE_S2_COLLECTION', 'COPERNICUS/S2_SR_HARMONIZED')
        coll = ee.ImageCollection(coll_id)

        if start and end:
            coll = coll.filterDate(start, end)
        coll = coll.filterBounds(geometry)
        
        # Filter by cloud cover
        coll = coll.filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', cloud_threshold)

        if layer.upper() == 'NDVI':
            img = coll.select(['B8', 'B4']).median().normalizedDifference(['B8', 'B4'])
            viz = {'min': 0, 'max': 1, 'palette': ['white', 'green']}
            img_viz = img.clip(clip_geometry).visualize(**viz)
        else:
            img = coll.select(['B4', 'B3', 'B2']).median()
            img_viz = img.clip(clip_geometry).visualize(min=0, max=3000)

        try:
            mapid = img_viz.getMapId()
            tile_url = mapid['tile_fetcher'].url_format
        except Exception:
            return jsonify({'status': 'error', 'error': 'Could not generate tile URL from EE'}), 500

        return jsonify({'status': 'ok', 'tile_url': tile_url})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


def write_monitor_html(out_path=None):
    """Write monitor.html with the new workflow: map + side panel + draw + imagery + timeseries."""
    here = os.path.dirname(os.path.abspath(__file__))
    if out_path is None:
        out_path = os.path.join(here, 'monitor.html')

    html = '''<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Remote Sensing Monitoring</title>
  <link href="https://fonts.googleapis.com/css2?family=Aller:wght@300;400&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css" />
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body, html { height: 100%; font-family: "Aller", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; font-weight: 300; }
    #map { height: 100%; width: 100%; }
    
    #toggleBtn {
      position: absolute;
      z-index: 500;
      top: 10px;
      right: 10px;
      padding: 8px 16px;
      background: white;
      border: 2px solid #ccc;
      border-radius: 4px;
      cursor: pointer;
      font-weight: 300;
    }
    #toggleBtn:hover { background: #f0f0f0; }
    
    #sidepanel {
      position: absolute;
      z-index: 400;
      top: 0;
      right: 0;
      width: 350px;
      height: 100%;
      background: white;
      border-left: 2px solid #ccc;
      overflow-y: auto;
      transform: translateX(100%);
      transition: transform 0.3s ease;
      padding: 20px;
    }
    #sidepanel.active { transform: translateX(0); }
    
    .close-btn {
      float: right;
      font-size: 24px;
      cursor: pointer;
      color: #999;
    }
    .close-btn:hover { color: #000; }
    
    .panel-section {
      margin-top: 20px;
      padding: 10px;
      border: 1px solid #ddd;
      border-radius: 4px;
    }
    
    .panel-section h3 {
      font-size: 14px;
      margin-bottom: 10px;
      color: #333;
      font-weight: 400;
    }
    
    .panel-section label {
      display: block;
      margin: 8px 0;
      font-size: 12px;
      font-weight: 300;
    }
    
    .panel-section input, .panel-section select {
      width: 100%;
      padding: 6px;
      margin-top: 4px;
      border: 1px solid #ccc;
      border-radius: 3px;
      font-weight: 300;
    }
    
    .panel-section button {
      width: 100%;
      padding: 8px;
      margin-top: 8px;
      background: #4CAF50;
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-weight: 400;
    }
    .panel-section button:hover { background: #45a049; }
    .panel-section button:disabled { background: #ccc; cursor: not-allowed; }
    
    #chartContainer {
      width: 100%;
      height: 300px;
      margin-top: 20px;
    }
    
    .status-text {
      font-size: 11px;
      color: #666;
      margin-top: 8px;
      font-weight: 300;
    }
    
    /* Modal for timeseries plot */
    #chartModal {
      display: none;
      position: fixed;
      z-index: 1001;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.5);
    }
    
    #chartModal.active {
      display: flex;
      align-items: center;
      justify-content: center;
    }
    
    .modal-content {
      background: white;
      padding: 20px;
      border-radius: 8px;
      width: 90%;
      height: 80%;
      max-width: 1200px;
      display: flex;
      flex-direction: column;
    }
    
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 15px;
    }
    
    .modal-header h2 {
      margin: 0;
      font-size: 18px;
      font-weight: 400;
    }
    
    .modal-close {
      font-size: 28px;
      cursor: pointer;
      color: #999;
      border: none;
      background: none;
    }
    
    .modal-close:hover {
      color: #000;
    }
    
    #chartModalCanvas {
      flex: 1;
      max-height: calc(100% - 50px);
    }
  </style>
</head>
<body>
  <div id="map"></div>
  
  <button id="toggleBtn">Monitoring Tools</button>
  
  <div id="sidepanel">
    <span class="close-btn" onclick="togglePanel()">&times;</span>
    <h2 style="font-size: 16px; margin-bottom: 20px; font-weight: 400;">Monitoring Tools</h2>
    
    <div class="panel-section">
      <h3>1. Draw Polygon</h3>
      <p style="font-size: 12px; color: #666; margin-bottom: 10px;">Draw a rectangle or polygon on the map. Press <strong>ESC</strong> to finish drawing.</p>
      <button id="drawAgainBtn" onclick="enableDrawing()" style="display: none;">Draw New Area</button>
    </div>
    
    <div class="panel-section" id="layerSection" style="display: none;">
      <h3>2. Select Layer</h3>
      <label>
        <input type="radio" name="layer" value="RGB" checked> RGB Composite
      </label>
      <label>
        <input type="radio" name="layer" value="NDVI"> NDVI
      </label>
      <button onclick="loadImagery()">Load Imagery</button>
      <div class="status-text" id="layerStatus"></div>
    </div>
    
    <div class="panel-section" id="timeseriesSection" style="display: none;">
      <h3>3. Time Series</h3>
      <label>Start Date: <input id="tsStart" type="date" /></label>
      <label>End Date: <input id="tsEnd" type="date" /></label>
      <button onclick="plotTimeseries()">Plot NDVI Time Series</button>
      <div class="status-text" id="tsStatus"></div>
    </div>
  </div>
  
  <!-- Modal for timeseries chart -->
  <div id="chartModal">
    <div class="modal-content">
      <div class="modal-header">
        <h2>NDVI Time Series</h2>
        <button class="modal-close" onclick="closeChartModal()">&times;</button>
      </div>
      <canvas id="chartModalCanvas"></canvas>
    </div>
  </div>
  
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  
  <script>
    // Global state
    let map, drawnItems, currentPolygon, geeLayer, timeseriesChart, drawControl;
    
    // Initialize map
    function initMap() {
      map = L.map('map').setView([0, 0], 2);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19}).addTo(map);
      
      drawnItems = new L.FeatureGroup().addTo(map);
      
      drawControl = new L.Control.Draw({
        edit: {featureGroup: drawnItems},
        draw: {
          rectangle: true,
          polygon: true,
          polyline: false,
          circle: false,
          marker: false,
          circlemarker: false
        }
      });
      map.addControl(drawControl);
      
      map.on(L.Draw.Event.CREATED, onDrawCreated);
      
      setTimeout(() => map.invalidateSize(), 200);
    }
    
    function onDrawCreated(e) {
      const layer = e.layer;
      drawnItems.clearLayers();
      drawnItems.addLayer(layer);
      currentPolygon = layer.toGeoJSON();
      
      // Just keep the draw tool available; user now knows to press Esc
      // Show layer selection
      document.getElementById('layerSection').style.display = 'block';
      document.getElementById('timeseriesSection').style.display = 'none';
      document.getElementById('chartContainer').style.display = 'none';
      document.getElementById('layerStatus').textContent = 'Polygon ready. Select a layer.';
    }
    
    function enableDrawing() {
      drawnItems.clearLayers();
      document.getElementById('layerSection').style.display = 'none';
      document.getElementById('timeseriesSection').style.display = 'none';
      document.getElementById('chartContainer').style.display = 'none';
      document.getElementById('layerStatus').textContent = '';
      document.getElementById('tsStatus').textContent = '';
    }
    
    function loadImagery() {
      if (!currentPolygon) {
        alert('Please draw a polygon first.');
        return;
      }
      
      const layer = document.querySelector('input[name="layer"]:checked').value;
      const start = document.getElementById('tsStart').value || '2024-01-01';
      const end = document.getElementById('tsEnd').value || '2024-12-31';
      
      document.getElementById('layerStatus').textContent = 'Loading imagery...';
      
      fetch('/tiles', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          geometry: currentPolygon.geometry || currentPolygon,
          layer: layer,
          start: start,
          end: end
        })
      })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'ok') {
          if (geeLayer) map.removeLayer(geeLayer);
          geeLayer = L.tileLayer(data.tile_url, {opacity: 0.8}).addTo(map);
          document.getElementById('layerStatus').textContent = `Showing ${layer} composite.`;
          document.getElementById('timeseriesSection').style.display = 'block';
        } else {
          alert('Error loading imagery: ' + (data.error || 'unknown'));
          document.getElementById('layerStatus').textContent = 'Error: ' + data.error;
        }
      })
      .catch(e => {
        alert('Request failed: ' + e.message);
        document.getElementById('layerStatus').textContent = 'Error: ' + e.message;
      });
    }
    
    function plotTimeseries() {
      if (!currentPolygon) {
        alert('Please draw a polygon first.');
        return;
      }
      
      const start = document.getElementById('tsStart').value;
      const end = document.getElementById('tsEnd').value;
      
      if (!start || !end) {
        alert('Please select start and end dates.');
        return;
      }
      
      document.getElementById('tsStatus').textContent = 'Computing time series...';
      
      fetch('/timeseries', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          geometry: currentPolygon.geometry || currentPolygon,
          start: start,
          end: end
        })
      })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'ok') {
          const labels = data.series.map(s => s.date);
          const values = data.series.map(s => s.value === null ? NaN : s.value);
          
          // Open modal
          document.getElementById('chartModal').classList.add('active');
          
          if (timeseriesChart) timeseriesChart.destroy();
          
          const ctx = document.getElementById('chartModalCanvas').getContext('2d');
          timeseriesChart = new Chart(ctx, {
            type: 'line',
            data: {
              labels: labels,
              datasets: [{
                label: 'NDVI',
                data: values,
                borderColor: '#4CAF50',
                backgroundColor: 'rgba(76, 175, 80, 0.1)',
                fill: true,
                tension: 0.1
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                y: {min: -0.5, max: 1}
              }
            }
          });
          
          document.getElementById('tsStatus').textContent = `Plotted ${data.series.length} observations.`;
        } else {
          alert('Error computing time series: ' + (data.error || 'unknown'));
          document.getElementById('tsStatus').textContent = 'Error: ' + data.error;
        }
      })
      .catch(e => {
        alert('Request failed: ' + e.message);
        document.getElementById('tsStatus').textContent = 'Error: ' + e.message;
      });
    }
    
    function closeChartModal() {
      document.getElementById('chartModal').classList.remove('active');
    }
    
    // Close modal when clicking outside
    document.getElementById('chartModal').addEventListener('click', function(e) {
      if (e.target === this) {
        closeChartModal();
      }
    });
    
    function togglePanel() {
      document.getElementById('sidepanel').classList.toggle('active');
    }
    
    document.getElementById('toggleBtn').onclick = togglePanel;
    
    // Set default dates to today and 1 year ago
    const today = new Date();
    const lastYear = new Date(today);
    lastYear.setFullYear(lastYear.getFullYear() - 1);
    document.getElementById('tsEnd').value = today.toISOString().split('T')[0];
    document.getElementById('tsStart').value = lastYear.toISOString().split('T')[0];
    
    // Initialize
    initMap();
  </script>
</body>
</html>'''

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return out_path


@app.route('/timeseries', methods=['POST'])
def timeseries():
    """
    POST JSON: {"geometry": <GeoJSON>, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    Returns: {status: 'ok', series: [{date, value}, ...]} or {status: 'error', error: '...'}
    
    Uses server-side aggregation for speed. Filters out cloudy images (>20% cloud cover).
    """
    if not EE_AVAILABLE or not EE_INITIALIZED:
        return jsonify({'status': 'error', 'error': f'EE not initialized: {EE_INIT_ERROR}'}), 500

    data = request.get_json() or {}
    geom = data.get('geometry')
    start = data.get('start')
    end = data.get('end')
    scale = int(data.get('scale', 10))
    cloud_threshold = int(data.get('cloud_threshold', 20))  # Default 20% cloud cover

    if not geom:
        return jsonify({'status': 'error', 'error': 'No geometry provided'}), 400

    try:
        geometry = ee.Geometry(geom)
        coll_id = os.environ.get('EE_S2_COLLECTION', 'COPERNICUS/S2_SR_HARMONIZED')
        coll = ee.ImageCollection(coll_id)

        if start and end:
            coll = coll.filterDate(start, end)
        coll = coll.filterBounds(geometry)
        
        # Filter by cloud cover
        coll = coll.filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', cloud_threshold)

        # Server-side function: compute NDVI mean and add date as property
        def add_ndvi_stats(img):
            nd = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
            mean_ndvi = nd.reduceRegion(ee.Reducer.mean(), geometry, scale).get('NDVI')
            date = ee.Date(img.get('system:time_start')).format('YYYY-MM-dd')
            return img.set('mean_ndvi', mean_ndvi).set('date', date)

        stats_coll = coll.map(add_ndvi_stats).sort('system:time_start').limit(256)

        # Extract all dates and values in one call (much faster than per-image getInfo)
        dates = stats_coll.aggregate_array('date').getInfo()
        values = stats_coll.aggregate_array('mean_ndvi').getInfo()

        # Zip them together
        series = [{'date': d, 'value': v} for d, v in zip(dates, values)]

        return jsonify({'status': 'ok', 'series': series})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# --- ADD THIS: Ensure HTML exists on import ---
# This runs immediately when Gunicorn loads the file
if not os.path.exists(os.path.join(os.path.dirname(__file__), 'monitor.html')):
    write_monitor_html()
# ----------------------------------------------

if __name__ == '__main__':
    # You can keep this for local testing, or remove the write call since we did it above
    # write_monitor_html() 
    
    print('Wrote monitor.html next to MONITORING.py')
    
    # Use environment variable for host/port (Render compatibility)
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    app.run(debug=debug, host='0.0.0.0', port=port)



