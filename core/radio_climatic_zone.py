#!/usr/bin/env python3
"""
Automated Radio-Climatic Zone Calculator.
Extracts land/sea status, distance to coastline, and radio-climatic zone
for earth station locations.
"""
import geopandas as gpd
from shapely.geometry import Point
from pyproj import Geod
import rasterio
from rasterio.mask import mask
import numpy as np

def load_coastline(shapefile_path):
    """Load coastline shapefile."""
    print(f"Loading coastline data from {shapefile_path}...")
    coastline = gpd.read_file(shapefile_path)
    return coastline

def get_utm_zone(lon, lat):
    """Calculate UTM zone for a given longitude and latitude."""
    return int((lon + 180) / 6) + 1

def calculate_distance_to_coast(station_lon, station_lat, coastline_gdf):
    """
    Calculate distance from earth station to nearest coastline.
    Returns distance in km.
    """
    # Create point for earth station
    station_point = Point(station_lon, station_lat)
    station_gdf = gpd.GeoDataFrame([1], geometry=[station_point], crs="EPSG:4326")
    
    # Determine UTM zone for accurate distance calculation
    utm_zone = get_utm_zone(station_lon, station_lat)
    utm_crs = f"EPSG:{32600 + utm_zone}" if station_lat >= 0 else f"EPSG:{32700 + utm_zone}"
    
    # Transform to UTM for accurate distance calculation
    station_utm = station_gdf.to_crs(utm_crs)
    coastline_utm = coastline_gdf.to_crs(utm_crs)
    
    # Calculate distance to nearest coastline
    distance_m = coastline_utm.geometry.distance(station_utm.geometry.iloc[0]).min()
    distance_km = distance_m / 1000.0
    
    return distance_km

def is_over_water(station_lon, station_lat, coastline_gdf):
    """
    Determine if station is over water or land.
    Uses a simple point-in-polygon check with land polygons.
    For more accuracy, use a land mask shapefile.
    """
    # For now, we'll use a simple heuristic:
    # If distance to coast is 0, it's likely on coast
    # For a proper check, you'd need a land polygon shapefile
    distance = calculate_distance_to_coast(station_lon, station_lat, coastline_gdf)
    
    # If very close to coast (< 0.1 km), check DEM elevation
    # Very low elevation near coast might indicate water
    return distance < 0.1  # Simplified - ideally use land polygon

def determine_radio_climatic_zone(station_lon, station_lat, station_alt, 
                                   coastline_gdf, dem_path=None):
    """
    Determine radio-climatic zone according to ITU Appendix 7.
    
    Zones:
    - A1: Coastal land (altitude < 100m AND distance to coast < 100km)
    - A2: Inland land (altitude >= 100m OR distance to coast >= 100km)
    - B: Cold sea
    - C: Warm sea (Mediterranean, Black Sea, Red Sea, etc.)
    
    Returns: zone_code (string), distance_to_coast (km)
    """
    # Calculate distance to coast
    dist_to_coast_km = calculate_distance_to_coast(station_lon, station_lat, coastline_gdf)
    
    # Check if over water (simplified - ideally use land/water mask)
    over_water = is_over_water(station_lon, station_lat, coastline_gdf)
    
    if over_water:
        # Check if warm sea (Zone C)
        # Warm seas: Mediterranean, Black Sea, Red Sea, Persian Gulf, Arabian Sea
        # You'd need a sea name shapefile for this
        # For now, assume cold sea (Zone B)
        zone = "B"
        zone_name = "Cold Sea"
    else:
        # Land station
        if station_alt < 100 and dist_to_coast_km < 100:
            zone = "A1"
            zone_name = "Coastal Land"
        else:
            zone = "A2"
            zone_name = "Inland Land"
    
    return zone, zone_name, dist_to_coast_km

def process_earth_stations(stations, coastline_path, dem_path=None):
    """
    Process multiple earth stations and extract radio-climatic info.
    
    stations: list of dicts with keys:
        - 'name': station name
        - 'lon': longitude (degrees)
        - 'lat': latitude (degrees)
        - 'alt': altitude (meters)
    """
    print("Loading coastline data...")
    coastline = load_coastline(coastline_path)
    
    results = []
    
    for station in stations:
        print(f"\nProcessing {station['name']}...")
        
        zone, zone_name, dist_to_coast = determine_radio_climatic_zone(
            station['lon'], station['lat'], station['alt'],
            coastline, dem_path
        )
        
        result = {
            'name': station['name'],
            'latitude': station['lat'],
            'longitude': station['lon'],
            'altitude_m': station['alt'],
            'zone_code': zone,
            'zone_name': zone_name,
            'distance_to_coast_km': dist_to_coast
        }
        
        results.append(result)
        
        print(f"  Zone: {zone} ({zone_name})")
        print(f"  Distance to coast: {dist_to_coast:.2f} km")
    
    return results

def main():
    # Example earth stations (replace with your actual data)
    earth_stations = [
        {
            'name': 'BAKU',
            'lon': 49.4797,
            'lat': 40.4678,
            'alt': 210
        },
        {
            'name': 'Test_Coastal',
            'lon': 49.5,
            'lat': 40.4,
            'alt': 50
        }
    ]
    
    # Path to Natural Earth coastline shapefile
    coastline_path = "ne_10m_coastline/ne_10m_coastline.shp"
    
    # Optional: path to DEM (for more accurate land/water detection)
    dem_path = "your_dem_file.tif"
    
    # Process stations
    results = process_earth_stations(earth_stations, coastline_path, dem_path)
    
    # Print summary table
    print("\n" + "="*80)
    print("RADIO-CLIMATIC ZONE SUMMARY")
    print("="*80)
    print(f"{'Station':<20} {'Zone':<10} {'Distance to Coast':<20}")
    print("-"*80)
    for r in results:
        print(f"{r['name']:<20} {r['zone_code']:<10} {r['distance_to_coast_km']:<20.2f} km")
    print("="*80)

if __name__ == "__main__":
    main()