# OSRM setup — refuse-truck roads for the Astana pilot

EcoRoute's committed production artifact is built from OSRM with
`profiles/refuse_truck.lua`. The profile represents a 20 t, 3.6 m high,
2.5 m wide, 9 m long municipal refuse truck. It respects physical and HGV
access restrictions, excludes pedestrian/cycle paths and steps, keeps
`highway=service + service=driveway` available when access tags allow it, and
uses reduced residential/service-road speeds.

The Streamlit V2 simulation requires the committed `data/road_cache/`; it does
not need a live routing server. Development helpers retain the labelled lookup
order `road cache -> live OSRM -> straight-line fallback`.

Requirements: Docker Desktop, about 4 GB free disk and 4 GB RAM. Run the
commands from the repository root.

## 1. Download the Kazakhstan extract

Keep the large extract and processed graphs outside the repository:

```bash
export ECOROUTE_OSRM_DATA="$HOME/osrm-data"
mkdir -p "$ECOROUTE_OSRM_DATA"
curl -L -o "$ECOROUTE_OSRM_DATA/kazakhstan-latest.osm.pbf" \
  https://download.geofabrik.de/asia/kazakhstan-latest.osm.pbf
```

## 2. Build the refuse-truck graph (MLD)

These commands mount the repository profile read-only and produce a separate
graph, leaving any passenger-car graph untouched:

```bash
export ECOROUTE_PROJECT_ROOT="$(pwd)"
export ECOROUTE_OSRM_DATA="$HOME/osrm-data"

docker run --rm -t \
  -v "$ECOROUTE_PROJECT_ROOT/profiles:/profiles:ro" \
  -v "$ECOROUTE_OSRM_DATA:/data" \
  ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /profiles/refuse_truck.lua \
  -o /data/kazakhstan-refuse-truck.osrm \
  /data/kazakhstan-latest.osm.pbf

docker run --rm -t -v "$ECOROUTE_OSRM_DATA:/data" \
  ghcr.io/project-osrm/osrm-backend \
  osrm-partition /data/kazakhstan-refuse-truck.osrm

docker run --rm -t -v "$ECOROUTE_OSRM_DATA:/data" \
  ghcr.io/project-osrm/osrm-backend \
  osrm-customize /data/kazakhstan-refuse-truck.osrm
```

Rebuild when the OSM extract or `profiles/refuse_truck.lua` changes.

## 3. Start and verify OSRM

```bash
export ECOROUTE_OSRM_DATA="$HOME/osrm-data"
docker run --rm -t -i --name ecoroute-osrm -p 5000:5000 \
  -v "$ECOROUTE_OSRM_DATA:/data" \
  ghcr.io/project-osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 500 \
  /data/kazakhstan-refuse-truck.osrm
```

In another terminal:

```bash
curl "http://localhost:5000/table/v1/refuse_truck/71.4491,51.1694;71.4704,51.1605?annotations=duration,distance"
```

Expect HTTP 200 and `"code":"Ok"`. OSRM coordinates are
`longitude,latitude`. `--max-table-size 500` is required for the 252-node
matrix (250 sites, depot, landfill).

**macOS gotcha:** AirPlay Receiver can occupy port 5000 and answer with
`403 Forbidden`. Disable it under System Settings -> General -> AirDrop &
Handoff before starting the container.

## 4. Rebuild the committed road cache

Do this only while the refuse-truck OSRM server above is healthy:

```bash
.venv/bin/python scripts/build_road_cache.py \
  --world data/world.csv \
  --output data/road_cache \
  --osrm http://127.0.0.1:5000 \
  --profile refuse_truck \
  --k 25 \
  --workers 16
```

The build aborts instead of writing an artifact when OSRM is unreachable,
returns an incomplete matrix, fails the geometry threshold, or cannot compute
the nearest-road courtyard audit. Verify the recorded profile and world hash:

```bash
jq '{world_hash, osrm_profile, courtyard_access, coverage_pct}' \
  data/road_cache/meta.json
```

Then regenerate the 30-day reports while OSRM is still available:

```bash
.venv/bin/python -m src.sim.run
```

Afterward OSRM may be stopped. The app reports the cached routing profile and
the share of sites more than 40 m from the nearest routable truck road.

Map data © OpenStreetMap contributors; extract by Geofabrik.
