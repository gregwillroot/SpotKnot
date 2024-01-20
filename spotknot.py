import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json
import requests
import csv
import re
import os
import threading
import time
import random
import tempfile
import shutil

track_counts = {}
mutex = threading.Lock()

# Load client IDs and secrets from ids.csv
client_credentials = []

with open("ids.csv", "r", encoding="utf-8") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        client_credentials.append({
            'client_id': row["client_id"],
            'client_secret': row["client_secret"]
        })

# Function to get a list of queries from the user and convert them to lowercase
def get_user_queries():
    queries = input("Enter sentences separated by a comma (e.g., 'Pink Floyd music, Beatles songs'): ")
    query_list = [query.strip().lower() for query in queries.split(',')]
    return query_list

# Function to get the release year range from the user
def get_release_year_range():
    release_year_range = input("Enter the release year range (e.g., 1915-2018), or press Enter to skip: ")
    if release_year_range:
        start_year, end_year = map(int, release_year_range.split('-'))
        return start_year, end_year
    else:
        return None, None  # Return None if no range is provided

def process_playlist(which, total, item, all_tracks, playlist_progress, track_counts, user_id, query, start_year, end_year, sp):
    playlist_id = item['id']
    playlist_name = item['name']
    
    if playlist_id not in playlist_progress:
        playlist_progress[playlist_id] = {'offset': 0, 'track_info': []}

    retry_count = 0  # Initialize the retry counter
    max_retries = 3  # Set the maximum number of retries

    # Check if the query is an exact word match in the playlist name, case-insensitive
    if re.search(rf'\b{re.escape(query.lower())}\b', playlist_name.lower(), re.IGNORECASE):
        while retry_count < max_retries:
            offset = playlist_progress[playlist_id]['offset']
            
            try:
                tracks = sp.playlist_tracks(playlist_id, offset=offset)
                time.sleep(4)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400 and "Bad request" in str(e):
                    print(f"Bad request for {playlist_name}, skipping this playlist.")
                    break  # Skip this playlist and continue with the next one
                else:
                    print(f"HTTP Error {e.response.status_code} occurred for {playlist_name}. Waiting and retrying.")
                    retry_count += 1
                    time.sleep(10)  # Sleep for 10 seconds before retrying
                    continue
            except Exception as e:
                print(f"An error occurred for {playlist_name}: {str(e)}. Retrying...")
                retry_count += 1
                time.sleep(10)  # Sleep for 10 seconds before retrying
                continue


            for item in tracks['items']:
                try:
                    track = item['track']
                    if track:
                        artist_name = track['artists'][0]['name']
                        track_name = track['name']
                        if track.get('album') and track['album'].get('release_date') is not None:
                            release_date = track['album']['release_date']
                            release_year = release_date[:4] if release_date else 0
                        else:
                            release_year = 0

                        # Exclude year range from the query
                        query = f'artist:"{artist_name}" track:{track_name.split("-")[0]}'

                        print(f"Processing {playlist_name}: {artist_name} - {track_name} ({which}/{total})")

                        # Save track information in progress JSON
                        track_info = {
                            'artist': artist_name,
                            'track_name': track_name,
                            'release_year': release_year,
                            'release_date': release_date,  # Added release_date
                            'track_id': track['id']
                        }

                        # Check if the track is already in the playlist progress
                        existing_track_info = next((t for t in playlist_progress[playlist_id]['track_info'] if t['artist'] == artist_name and t['track_name'] == track_name), None)
                        if existing_track_info:
                            # Update release year if the current track has an earlier release date
                            existing_release_date = existing_track_info['release_date']
                            if existing_release_date and release_date and existing_release_date > release_date:
                                existing_track_info['release_year'] = release_date[:4]

                        with mutex:
                            playlist_progress[playlist_id]['track_info'].append(track_info)

                        # Track counts for all tracks, even those beyond the year range
                        with mutex:
                            # Exclude year range from the key when updating track counts
                            track_counts[query] = track_counts.get(query, 0) + 1

                        if (start_year is None or int(start_year) <= int(release_year)) and (end_year is None or int(release_year) <= int(end_year)):
                            with mutex:
                                if query not in all_tracks:
                                    all_tracks.add(query) if isinstance(all_tracks, set) else all_tracks.append(query)

                except UnicodeEncodeError as e:
                    print(f"UnicodeEncodeError occurred for {artist_name} - {track_name}. Skipping this track.")
                    continue  # Skip to the next iteration
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400 and "Bad request" in str(e):
                        print(f"Bad request for {playlist_name}, skipping this track.")
                        continue  # Skip this track and continue with the next one
                    else:
                        print(f"HTTP Error {e.response.status_code} occurred for {playlist_name}. Waiting and retrying.")
                        time.sleep(10)  # Sleep for 10 seconds before retrying
                        continue
                except Exception as e:
                    print(f"An error occurred while processing {artist_name} - {track_name}: {str(e)}. Skipping this track.")
                    continue  # Skip to the next iteration

            if tracks['next']:
                with mutex:
                    playlist_progress[playlist_id]['offset'] += len(tracks['items'])
            else:
                break
            # Introduce a time delay to avoid hitting API limits
            time.sleep(4)  # Sleep for 1 second
        with mutex:
            playlist_progress[playlist_id]['offset'] = 0




def save_progress(data, filename):
    temp_filename = f"{filename}.tmp"
    if 'all_tracks' in data:
        data['all_tracks'] = list(data['all_tracks'])
    else:
        data['all_tracks'] = []
    
    max_retries = 3  # Define maximum number of retries
    retry_count = 0

    while retry_count < max_retries:
        try:
            with open(temp_filename, 'w', encoding='utf-8') as f:  
                json.dump(data, f, ensure_ascii=False, indent=4)  

            # Use shutil.move to ensure atomicity
            shutil.move(temp_filename, filename)
            break  # Break the loop if successful

        except PermissionError as e:
            retry_count += 1
            print(f"Permission denied error: {str(e)}. Retrying ({retry_count}/{max_retries})...")
            time.sleep(5)  # Wait for a few seconds before retrying
            continue

        except Exception as e:
            print(f"An error occurred: {str(e)}")
            break  # Break the loop for other types of errors


# Function to load progress
def load_progress(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:  # Specify utf-8 encoding
            data = json.load(f)
            if 'all_tracks' in data and isinstance(data['all_tracks'], list):
                data['all_tracks'] = set(data['all_tracks'])
            if 'which' not in data:
                data['which'] = 0
            if 'processed_playlists' not in data:
                data['processed_playlists'] = []
            if 'playlist_progress' not in data:
                data['playlist_progress'] = {}
            return data
    except FileNotFoundError:
        return {
            'which': 0,
            'processed_playlists': [],
            'all_tracks': set(),
            'playlist_progress': {},
        }

def create_playlist(track_counts_all_queries, query_list, threshold, start_year, end_year, sp):
    combined_track_counts = {}
    all_tracks = set()  # Store all tracks from all queries

    # Accumulate all tracks from all queries
    for track_counts_this_query in track_counts_all_queries:
        for query, count in track_counts_this_query.items():
            combined_track_counts[query] = combined_track_counts.get(query, 0) + count
            all_tracks.add(query)

    # Sort tracks by count in descending order globally
    sorted_tracks = sorted(combined_track_counts.items(), key=lambda x: x[1], reverse=True)

    # Apply the threshold to all tracks
    filtered_tracks = {query: count for query, count in combined_track_counts.items() if count >= threshold}

    if filtered_tracks:
        user_id = sp.current_user()['id']

        # Include years ranges and threshold in the playlist name
        playlist_name = f"generated: {', '.join(query_list)}"

        if start_year is not None and end_year is not None:
            playlist_name += f" [{start_year}-{end_year}]"

        

        new_playlist = sp.user_playlist_create(user_id, playlist_name, public=False)
        new_playlist_id = new_playlist['id']

        added_track_ids = set()
        # Set the batch size
        batch_size = 100

        # Add tracks in batches of 100
        tracks_to_add = []
        for query, count in sorted_tracks:
            if query in filtered_tracks:
                track_name = query.split(" track:")[1]
                artist_name = query.split(" track:")[0].split("artist:")[1]

                try:
                    search_results = sp.search(q=query, type='track', limit=1)
                    track_items = search_results['tracks']['items']

                    if track_items:
                        track = track_items[0]
                        track_id = track['id']

                        # Check if the track's release year is within the specified range
                        release_year = int(track['album']['release_date'].split('-')[0])
                        if (start_year is None or start_year <= release_year) and (end_year is None or release_year <= end_year):

                            if track_id not in added_track_ids:
                                added_track_ids.add(track_id)
                                tracks_to_add.append(track_id)

                                print(f"'{playlist_name}': {artist_name} - {track_name} ({release_year}) "
                                      f"({len(added_track_ids)}/{len(filtered_tracks)}) - Count: {count}")

                                

                                if len(tracks_to_add) == batch_size:
                                    sp.playlist_add_items(new_playlist_id, tracks_to_add)
                                    
                                    tracks_to_add = []

                except Exception as e:
                    # Handle exceptions as needed
                    print(f"An error occurred: {str(e)}")

        # Add the remaining tracks
        if tracks_to_add:
            sp.playlist_add_items(new_playlist_id, tracks_to_add)
            print(f"Added a batch of {len(tracks_to_add)} tracks to the playlist")

        print(f"Playlist created: {new_playlist['external_urls']['spotify']}")
    else:
        print(f"No tracks meet the count threshold ({threshold} or more) to create a playlist.")







# Start the main program
if __name__ == "__main__":
    try:
        queries = get_user_queries()
        # Ask user for threshold
        threshold_input = input("Enter the threshold value for track count (press Enter for default 3): ")
        threshold = int(threshold_input) if threshold_input else 3
        track_counts_all_queries = []

        # Initialize track counts list
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=client_credentials[0]['client_id'],
                                                       client_secret=client_credentials[0]['client_secret'],
                                                       redirect_uri="http://localhost:8000/callback/",
                                                       scope="playlist-modify-private"))
        start_year, end_year = get_release_year_range()

        for query in queries:
            current_state = load_progress(f'progress_{sp.current_user()["id"]}_{query}.json')

            if current_state and current_state.get('which', 0) > 0:
                # Filter tracks based on the threshold for JSON progress
                track_counts_all_queries.append({query: count for query, count in current_state['track_counts'].items() if count >= threshold})
            else:
                current_state = {
                    'which': 0,
                    'processed_playlists': [],
                    'all_tracks': set(),
                    'playlist_progress': {},
                    'track_counts': {}
                }

            playlist_id = None
            limit = 50

            max_playlists_per_query = 850
            unique_processed_playlists_count = 0

            while True:
                if unique_processed_playlists_count >= max_playlists_per_query:
                    break  # Break the loop if the max playlists per query are processed

                if len(current_state['processed_playlists']) >= max_playlists_per_query:
                    print(f"Max playlists per query reached ({max_playlists_per_query}). Skipping the query.")
                    break  # Skip the query

                try:
                    results = sp.search(f'*{query}*', limit=limit, offset=current_state['playlist_progress'].get(playlist_id, {'offset': 0})['offset'], type='playlist')
                    time.sleep(4)
                    playlist = results['playlists']
                    total = playlist['total']
                    for item in playlist['items']:
                        if item['owner']['id'] != sp.current_user()['id']:
                            playlist_id = item['id']
                            if playlist_id not in current_state['processed_playlists']:
                                process_playlist(len(current_state['processed_playlists']) + 1, total, item, current_state['all_tracks'], current_state['playlist_progress'], current_state['track_counts'], sp.current_user()["id"], query, start_year, end_year, sp)
                                current_state['processed_playlists'].append(playlist_id)
                                unique_processed_playlists_count += 1
                                save_progress(current_state, f'progress_{sp.current_user()["id"]}_{query}.json')  # Save progress after each playlist
                            current_state['which'] += 1

                    if playlist['next']:
                        current_state['playlist_progress'][playlist_id]['offset'] += limit
                        save_progress(current_state, f'progress_{sp.current_user()["id"]}_{query}.json')  # Save progress after each offset update
                    else:
                        break
                except requests.exceptions.ReadTimeout:
                    print("Read timed out. Retrying...")
                    time.sleep(10)
                    continue


        track_counts_all_queries.append(current_state['track_counts'])

        # Count all the tracks from the queries
        all_tracks_counts = {}
        for track_counts_query in track_counts_all_queries:
            for query, count in track_counts_query.items():
                all_tracks_counts[query] = all_tracks_counts.get(query, 0) + count

        # Print the counts
        for query, count in all_tracks_counts.items():
            print(f"{query}: {count} tracks")

        create_playlist(track_counts_all_queries, queries, threshold, start_year, end_year, sp)

    except Exception as e:
        print(f"An error occurred: {str(e)}")
    finally:
        if os.path.exists('.cache'):
            os.remove('.cache')
