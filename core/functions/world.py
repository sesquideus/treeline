from geographiclib.geodesic import Geodesic
from geopy import Point
from geopy.distance import geodesic

def distance(p1, p2):
    if p1 is not None and p2 is not None:
        return geodesic(Point(p1[0], p1[1]), Point(p2[0], p2[1]))
    return None

def azimuth(p1, p2):
    if p1 is not None and p2 is not None:
        return Geodesic.WGS84.Inverse(p1[0], p1[1], p2[0], p2[1])['azi1']
    return None


