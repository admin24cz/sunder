import { LngLatBounds, Map as MapLibreMap, NavigationControl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { type ReactElement, useEffect, useRef } from 'react';

interface Props {
  track: GeoJSON.LineString;
  /** Accessible description, e.g. "Trasa běhu 15. 1. 2026". */
  label: string;
  className?: string;
}

/**
 * Draws one activity's route on an OpenStreetMap base layer.
 *
 * MapLibre with raster OSM tiles: free, no API key, no account (spec section 3).
 * Imperative rather than declarative because MapLibre owns its own canvas and
 * lifecycle; a React wrapper around it would be a second source of truth for
 * state MapLibre already holds.
 */
export function ActivityMap({ track, label, className }: Props): ReactElement {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);

  useEffect(() => {
    if (container.current === null) return;

    const instance = new MapLibreMap({
      container: container.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            // Required by the OSM tile usage policy.
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
      // Overwritten by fitBounds below; set so the map never flashes at [0,0].
      center: [14.42, 50.08],
      zoom: 11,
      // The page scrolls on a phone. A map that swallowed one-finger drags
      // would trap the user inside it.
      cooperativeGestures: true,
    });

    instance.addControl(new NavigationControl(), 'top-right');

    instance.on('load', () => {
      instance.addSource('track', {
        type: 'geojson',
        data: { type: 'Feature', properties: {}, geometry: track },
      });
      instance.addLayer({
        id: 'track-line',
        type: 'line',
        source: 'track',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#2563eb', 'line-width': 4, 'line-opacity': 0.85 },
      });

      const bounds = track.coordinates.reduce(
        (acc, coordinate) => acc.extend(coordinate as [number, number]),
        new LngLatBounds(
          track.coordinates[0] as [number, number],
          track.coordinates[0] as [number, number],
        ),
      );
      instance.fitBounds(bounds, { padding: 40, animate: false });
    });

    map.current = instance;
    return () => {
      instance.remove();
      map.current = null;
    };
  }, [track]);

  return (
    <div
      ref={container}
      role="img"
      aria-label={label}
      className={className ?? 'h-80 w-full overflow-hidden rounded-lg'}
    />
  );
}
