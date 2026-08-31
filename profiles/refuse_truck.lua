-- EcoRoute refuse-truck profile.
--
-- This deliberately layers the project constraints over the OSRM image's
-- version-matched car.lua instead of copying 500+ lines that would silently
-- drift from the backend used to preprocess the graph.

local Car = dofile('/opt/car.lua')

local forbidden_highways = Set {
  'path',
  'footway',
  'cycleway',
  'steps'
}

function setup()
  local profile = Car.setup()

  -- Representative 20-tonne municipal rear-loader envelope.
  profile.vehicle_weight = 20000 -- kilograms
  profile.vehicle_height = 3.6   -- metres
  profile.vehicle_width = 2.5    -- metres
  profile.vehicle_length = 9.0   -- metres
  profile.vehicle_max_speed = 80 -- km/h

  -- Prefer access rules written specifically for heavy vehicles, while still
  -- respecting the generic motor-vehicle and access restrictions handled by
  -- the upstream profile.
  profile.access_tags_hierarchy = Sequence {
    'hgv',
    'goods',
    'motor_vehicle',
    'vehicle',
    'access'
  }
  profile.restrictions = Sequence {
    'hgv',
    'goods',
    'motor_vehicle',
    'vehicle'
  }

  -- Courtyard access remains routable, but at realistic collection speeds.
  -- service=driveway is intentionally not forbidden; upstream service and
  -- access handlers still reject explicitly private/no access.
  profile.speeds.highway.residential = 18
  profile.speeds.highway.living_street = 8
  profile.speeds.highway.service = 10
  profile.service_penalties.driveway = 0.8
  profile.service_penalties.parking_aisle = 0.7
  profile.service_penalties.alley = 0.7

  return profile
end

function process_way(profile, way, result, relations)
  local highway = way:get_value_by_key('highway')
  if forbidden_highways[highway] then
    return
  end

  -- hgv=no is a hard prohibition even when a more general vehicle tag is
  -- permissive. Other hgv values flow through the upstream access handler.
  if way:get_value_by_key('hgv') == 'no' then
    return
  end

  return Car.process_way(profile, way, result, relations)
end

return {
  setup = setup,
  process_way = process_way,
  process_node = Car.process_node,
  process_turn = Car.process_turn
}
