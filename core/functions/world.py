from geopy import distance, Point


def distance(p1, p2):
    return distance(Point(p1[0], p1[1]), Point(p2[0], p2[1]))


