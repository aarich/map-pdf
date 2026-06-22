import os
import gpxpy
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString
import matplotlib.pyplot as plt
import contextily as ctx
import argparse
import sys
import io
from PIL import Image
from pypdf import PdfWriter

OUTPUT_DIR = 'out'

PROVIDERS = {
    "OpenTopo": ctx.providers.OpenTopoMap,
    "USGS": ctx.providers.USGS.USTopo,
    'ESRIWorld': ctx.providers.Esri.WorldTopoMap,
    'OpenStreet': ctx.providers.OpenStreetMap.Mapnik
}

def parse_gpx_to_dataframe(filepath):
    """Reads the GPX file and converts it into a clean Pandas DataFrame."""
    print(f"Parsing {filepath}...")
    with open(filepath, 'r') as gpx_file:
        gpx = gpxpy.parse(gpx_file)

    points = []
    cum_dist_m = 0.0
    prev_point = None

    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                # Calculate distance from the last point
                if prev_point:
                    dist_m = pt.distance_2d(prev_point)
                    cum_dist_m += dist_m
                
                # Handle missing elevation data safely
                ele = pt.elevation if pt.elevation is not None else 0.0

                points.append({
                    'lat': pt.latitude,
                    'lon': pt.longitude,
                    'ele_ft': ele * 3.28084,          # Convert meters to feet
                    'dist_mi': cum_dist_m / 1609.34   # Convert meters to miles
                })
                prev_point = pt

    df = pd.DataFrame(points)
    
    print(f"Total miles: {df['dist_mi'].max():.1f} miles")
    return df

def calc_stats(day_df):
    """Calculates statistics for a given day."""
    start_mi = day_df['dist_mi'].min()
    end_mi = day_df['dist_mi'].max()
    total_mi = end_mi - start_mi
    total_gain_ft = 0
    for j in day_df['ele_ft'].diff():
        if j > 0:
            total_gain_ft += j
    elev_diff_ft = day_df['ele_ft'].diff().sum()
    return start_mi, end_mi, total_mi, total_gain_ft, elev_diff_ft

def assign_days(df, cutoffs: list[float]):
    """Splits the dataframe into days based on the mileage cutoffs."""
    df['day'] = 1 # Default everything to Day 1
    
    for i, cutoff in enumerate(cutoffs):
        # If the mileage is higher than the cutoff, bump it to the next day
        df.loc[df['dist_mi'] > cutoff, 'day'] = i + 2

    start, end, total, gain, diff = calc_stats(df)
    print(f"Total: {start:.2f}-{end:.2f} mi\tTotal: {total:.2f} mi\tGain: {gain:.0f} ft\tDiff: {diff:.0f} ft")

    for i in range(1, len(cutoffs) + 2):
        day_df = df[df['day'] == i]
        start, end, total, gain, diff = calc_stats(day_df)
        
        print(f"Day {i}: {start:.2f}-{end:.2f} mi\tTotal: {total:.2f} mi\tGain: {gain:.0f} ft\tDiff: {diff:.0f} ft")
    return df

def generate_sheet(title, df, zoom, buffer, provider, full_gdf=None):
    """Generates an 8.5x11 PDF with a map and elevation profile."""
    print(f"Generating {title if title else 'Full Route'} Map...")

    if len(df) < 2:
        return None # Need at least 2 points to draw a line!

    # --- 1. DETERMINE ORIENTATION ---
    line = LineString(zip(df.lon, df.lat))
    gdf = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326")
    gdf = gdf.to_crs(epsg=3857) # Web Mercator projection required for map tiles

    minx, miny, maxx, maxy = gdf.total_bounds
    buffer_meters = buffer * 1609.34
    width = (maxx - minx) + 2 * buffer_meters
    height = (maxy - miny) + 2 * buffer_meters
    # rotate if the ratio is > 1.1 (to make the map fill the page better)
    is_landscape = (width / height) > 1.1

    # --- SETUP MAIN PORTRAIT PAGE ---
    fig = plt.figure(figsize=(8.5, 11))
    ax_map = fig.add_axes([0.05, 0.2, 0.9, 0.75])
    ax_elev = fig.add_axes([0.12, 0.08, 0.83, 0.1])
    
    # Calculate stats for the title
    _, _, total_dist, total_gain, total_elev = calc_stats(df)
    map_title = f"{title + ': ' if title else ''}{total_dist:.1f} mi, {total_gain:.0f} ft up ({total_elev:.0f} ft net)"

    if is_landscape:
        # Create a temporary landscape figure to plot onto
        temp_fig = plt.figure(figsize=(10, 7.5))
        target_ax = temp_fig.add_axes([0.05, 0.05, 0.9, 0.85])
    else:
        target_ax = ax_map

    # --- PLOT THE MAP ---
    # Plot full trail (faint) and today's trail (bold red)
    if full_gdf is not None:
        full_gdf.plot(ax=target_ax, color='red', linewidth=2, linestyle=':', alpha=0.4, label='Full Trail')
    gdf.plot(ax=target_ax, color='red', linewidth=3, label=f'{title + " " if title else ""}Route')

    # Add Start/End Dots
    start_pt = gdf.geometry.iloc[0].coords[0]
    end_pt = gdf.geometry.iloc[0].coords[-1]
    target_ax.plot(start_pt[0], start_pt[1], marker='o', color='green', markersize=10, zorder=5)
    target_ax.plot(end_pt[0], end_pt[1], marker='s', color='blue', markersize=10, zorder=5)

    target_ax.set_xlim(minx - buffer_meters, maxx + buffer_meters)
    target_ax.set_ylim(miny - buffer_meters, maxy + buffer_meters)

    zoom_level = zoom if zoom else 'auto'
    ctx.add_basemap(target_ax, source=PROVIDERS.get(provider), zoom=zoom_level, attribution=False)
    target_ax.axis('off')

    if is_landscape:
        # Save the landscape map to a BytesIO buffer and rotate it
        buf = io.BytesIO()
        temp_fig.savefig(buf, format='png', bbox_inches='tight', dpi=400)
        buf.seek(0)
        img = Image.open(buf)
        rotated_img = img.rotate(270, expand=True)
        plt.close(temp_fig)

        # Draw the rotated image on the main portrait map axis
        ax_map.imshow(rotated_img)
        ax_map.axis('off')

    # --- 2. PLOT THE ELEVATION PROFILE ---
    dist = df.dist_mi
    elev = df.ele_ft
    
    ax_elev.plot(dist, elev, color='darkred', linewidth=1)
    ax_elev.set_title(map_title, fontsize=14, fontweight='bold')
    
    y_min = elev.min()
    y_max = elev.max()
   
    ax_elev.set_ylim(bottom=y_min)
    ax_elev.set_xlim(dist.min(), dist.max())
    
    # set ticks to be every X ft starting at a multiple of X near y_min
    if y_max - y_min > 1500:
        tick_interval = 500
    else:
        tick_interval = 250
    start_tick = int(y_min) - (int(y_min) % tick_interval)
    ax_elev.set_yticks(range(start_tick, int(elev.max()) + tick_interval, tick_interval))

    # Profile styling
    ax_elev.set_xlabel("Cumulative Distance (mi)", fontsize=12)
    ax_elev.set_ylabel("Elevation (ft)", fontsize=12)
    ax_elev.grid(True, linestyle='--', alpha=0.6)

    # Save as a printable PDF
    output_file = os.path.join(OUTPUT_DIR, f"{title if title else 'Full'}.pdf")
    plt.savefig(output_file, format='pdf', dpi=400)
    plt.close()
    
    print(f"  -> Saved {output_file}")
    return output_file

def generate_sheets(df, zoom, buffer, provider):
    """Generates an 8.5x11 PDF for each day with a map and elevation profile"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    days = df['day'].unique()
    
    # We want to show the full route as a faint background line on every map
    full_line = LineString(zip(df.lon, df.lat))
    full_gdf = gpd.GeoDataFrame(geometry=[full_line], crs="EPSG:4326").to_crs(epsg=3857)

    files = []

    if len(days) > 1:

        for day in days:
            # Grab data for just this day
            day_df = df[df['day'] == day].copy()
            
            # To make the map continuous, we grab the very last 
            # point of yesterday and attach it to the beginning of today.
            if day > 1:
                prev_day_last_point = df[df['day'] == day - 1].iloc[-1:]
                day_df = pd.concat([prev_day_last_point, day_df])

            if len(day_df) < 2:
                continue # Need at least 2 points to draw a line!

            file = generate_sheet(f"Day {day}", day_df, zoom, buffer, provider, full_gdf)
            if file:
                files.append(file)
    
    # generate a file for the whole route
    file = generate_sheet(None, df, zoom, buffer, provider)
    if file:
        files.append(file)

    return files
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate daily maps from a GPX file.')
    parser.add_argument('-f', '--file', help='Path to the GPX file', required=True)
    parser.add_argument('-c', '--cutoffs', help='Comma-separated list of camp mile markers (e.g., "9.5,22.4,45.1")')
    parser.add_argument('-b', '--buffer', type=int, help='Buffer distance in miles', default=10)
    parser.add_argument('-p', '--provider', help='Map provider', default='USGS')
    parser.add_argument('-z', '--zoom', type=int, help='Zoom level. If not specified, automatically calculated.')
    parser.add_argument('-k', '--keep', type=bool, help='Keep intermediate individual PDFs after merging', default=False)

    try:
        args = parser.parse_args()
        if args.cutoffs:
            cutoffs = [float(c) for c in args.cutoffs.split(",")] 
        else:
            cutoffs = []
    except SystemExit:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.file):
        print(f"ERROR: Could not find '{args.file}'. Please put a GPX file in the folder.")
    else:
        df = parse_gpx_to_dataframe(args.file)
        df = assign_days(df, cutoffs)

        zoom = args.zoom if args.zoom else 'auto'

        files = generate_sheets(df, zoom, args.buffer, args.provider)

        # Merge all the PDFs
        merger = PdfWriter()
        for file in files:
            merger.append(file)

        input_file_name = args.file.split('/')[-1]
        output_file_name = input_file_name.lower().replace('.gpx', '').replace('.GPX', '')
        merger.write(os.path.join(OUTPUT_DIR, f"{output_file_name}.pdf"))
        merger.close()
        
        # cleanup
        if not args.keep:
            for file in files:
                os.remove(file)
                
        print("🎉 All maps generated successfully!")
