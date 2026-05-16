function addRiversToMap(map, riverData) {
    const vectorSource = new ol.source.Vector();

    riverData.forEach(river => {
        if (river.waypoints.length < 2) return;

        const coords = river.waypoints.map(([lon, lat]) =>
            ol.proj.fromLonLat([lon, lat])
        );

        const feature = new ol.Feature({
            geometry: new ol.geom.LineString(coords),
            name: river.name,
            pk: river.pk,
        });

        vectorSource.addFeature(feature);
    });

    const vectorLayer = new ol.layer.Vector({
        source: vectorSource,
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({
                color: '#4a90d9',
                width: 2,
            }),
        }),
    });

    map.addLayer(vectorLayer);
    return vectorLayer;  // return so caller can remove/style it later
}