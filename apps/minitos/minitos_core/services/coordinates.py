import math


EARTH_RADIUS_M = 6378137.0


def _origin(location):
    latitude = location.map_origin_latitude
    longitude = location.map_origin_longitude
    if latitude is None:
        latitude = location.latitude
    if longitude is None:
        longitude = location.longitude
    if latitude is None or longitude is None:
        raise ValueError('The location requires a map origin latitude and longitude.')
    return float(latitude), float(longitude)


def local_to_geographic(location, x_m, y_m):
    """Convert terminal-local east/north meters to (latitude, longitude)."""
    origin_lat, origin_lon = _origin(location)
    angle = math.radians(float(location.angle or 0.0))
    east_m = float(x_m) * math.cos(angle) + float(y_m) * math.sin(angle)
    north_m = -float(x_m) * math.sin(angle) + float(y_m) * math.cos(angle)
    latitude = origin_lat + math.degrees(north_m / EARTH_RADIUS_M)
    longitude = origin_lon + math.degrees(
        east_m / (EARTH_RADIUS_M * math.cos(math.radians(origin_lat)))
    )
    return latitude, longitude


def geographic_to_local(location, latitude, longitude):
    """Convert (latitude, longitude) to terminal-local east/north meters."""
    origin_lat, origin_lon = _origin(location)
    north_m = math.radians(float(latitude) - origin_lat) * EARTH_RADIUS_M
    east_m = (
        math.radians(float(longitude) - origin_lon)
        * EARTH_RADIUS_M
        * math.cos(math.radians(origin_lat))
    )
    angle = math.radians(float(location.angle or 0.0))
    x_m = east_m * math.cos(angle) - north_m * math.sin(angle)
    y_m = east_m * math.sin(angle) + north_m * math.cos(angle)
    return x_m, y_m


def offset_geographic(latitude, longitude, east_m, north_m):
    """Move a geographic point by true east/north meter offsets."""
    latitude = float(latitude)
    result_latitude = latitude + math.degrees(float(north_m) / EARTH_RADIUS_M)
    result_longitude = float(longitude) + math.degrees(
        float(east_m) / (EARTH_RADIUS_M * math.cos(math.radians(latitude)))
    )
    return result_latitude, result_longitude
