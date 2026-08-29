# OSRM setup — road distances for the Astana demo

`src/optimize/distances.py` asks a local OSRM server for road-distance and
travel-time matrices via the `/table` API on `http://localhost:5000`. When the
server is not running, the app automatically falls back to straight-line
(haversine × 1.4) estimates and shows the "straight-line est." badge — so
nothing here is required just to run the demo.

Requirements: Docker Desktop running, ~3 GB free disk, ~4 GB RAM for the
preprocessing step.

## 1. One-time preprocessing (~10–20 min)

Keep the OSM data outside the repo so it never gets committed:

```bash
mkdir -p ~/osrm-data && cd ~/osrm-data

# Kazakhstan OSM extract (~600 MB download)
curl -L -O https://download.geofabrik.de/asia/kazakhstan-latest.osm.pbf

# Build the routing graph (car profile, MLD pipeline)
docker run --rm -t -v "$(pwd):/data" ghcr.io/project-osrm/osrm-backend \
    osrm-extract -p /opt/car.lua /data/kazakhstan-latest.osm.pbf
docker run --rm -t -v "$(pwd):/data" ghcr.io/project-osrm/osrm-backend \
    osrm-partition /data/kazakhstan-latest.osrm
docker run --rm -t -v "$(pwd):/data" ghcr.io/project-osrm/osrm-backend \
    osrm-customize /data/kazakhstan-latest.osrm
```

Re-run this section only when you want fresher OSM map data.

## 2. Run the server

```bash
cd ~/osrm-data
docker run --rm -t -i -p 5000:5000 -v "$(pwd):/data" ghcr.io/project-osrm/osrm-backend \
    osrm-routed --algorithm mld --max-table-size 500 /data/kazakhstan-latest.osrm
```

`--max-table-size 500` matters: OSRM's default cap is 100 locations per
`/table` request, and the current demo asks for a 252×252 matrix (250 sites,
depot, and landfill).

**macOS gotcha:** AirPlay Receiver occupies port 5000 (it answers with
`403 Forbidden`, which the app safely treats as "OSRM down"). Before starting
the container, free the port via System Settings → General → AirDrop & Handoff
→ turn off "AirPlay Receiver", or the `docker run -p 5000:5000` bind will fail.

## 3. Verify

```bash
curl "http://localhost:5000/table/v1/driving/71.4491,51.1694;71.4704,51.1605?annotations=duration,distance"
```

Expect a JSON response with `"code":"Ok"` plus `durations` and `distances`
matrices. Note OSRM's coordinate order is `longitude,latitude`.

Then restart the Streamlit app — the map panel badge should switch from
"straight-line est." to "road distances (OSRM)".

## Notes

- Fetched matrices are cached as JSON in `data/cache/` (keyed by the
  coordinate set). Safe to delete anytime; they are regenerated on demand.
- Only successful OSRM responses are cached, so a fallback answer never
  masks a later successful run.
- Map data © OpenStreetMap contributors, extract by Geofabrik.
