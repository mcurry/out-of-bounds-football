import requests
import json
import time
import random
import os
import pandas as pd
import zipfile
import urllib.parse
import sys

# Optional: pretty progress bars if installed (pip install tqdm)
try:
    from tqdm import tqdm
except Exception:
    tqdm = None

def iter_progress(seq, desc: str):
    """
    Wraps an iterable with a progress display.
    Uses tqdm if available; otherwise prints a single-line percentage.
    """
    if tqdm:
        return tqdm(seq, desc=desc, unit="img")
    else:
        total = len(seq)
        def gen():
            if total == 0:
                print(f"{desc}: 0% (0/0)")
                return
            for i, x in enumerate(seq, 1):
                pct = int(i * 100 / total)
                print(f"\r{desc}: {pct}% ({i}/{total})", end="", flush=True)
                yield x
            print()  # newline after finishing
        return gen()
        
# Prompt the user for parts of the URL
company = input("1) What are the initials for the company you want to get rosters from? (2 letters) ").strip().lower()
coach = input("2) Who is the coach the game is named after? (6 letters) ").strip().lower()
league = input("3) What are the 3 initials for the league you want the rosters from? (3 letters) ").strip().lower()

# Ask about avatars (default N)
include_avatars_in = input("4) Include player avatars (this takes much longer)? (Y/N) [N]: ").strip().lower()
include_avatars = include_avatars_in in ("y", "yes")

# Construct the base URL
base_url = f"https://drop-api.{company}.com/rating/{coach}-{league}"

# Set request headers to mimic a browser/API access
headers = {
    "Accept": "*/*",
    "Origin": f"https://www.{company}.com",
    "Referer": f"https://www.{company}.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Dest": "empty",
    "x-feature": '{"enable_next_ratings_release":true,"enable_college_football_ratings":true}'
}

def _ext_from_url(u: str) -> str:
    try:
        path = urllib.parse.urlparse(u).path
        ext = os.path.splitext(path)[1]
        return ext if ext else ".jpg"
    except Exception:
        return ".jpg"

all_items = []
offset = 0
limit = 100
downloaded_logos = set()
downloaded_avatars = set()  # track by player_id or url fallback
team_codes = {}
team_details = {}

# ensure asset folders exist
os.makedirs("logos", exist_ok=True)
if include_avatars:
    os.makedirs("avatars", exist_ok=True)

while True:
    url = f"{base_url}?locale=en&limit={limit}&offset={offset}&iteration=1-base"
    print(f"Fetching offset {offset} from {url}")
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        break

    data = response.json()
    items = data.get("items", [])
    if not items:
        break

    all_items.extend(items)

    # Build team_codes dynamically
    for player in items:
        team = player.get("team", {})
        team_id = team.get("id")
        team_label = team.get("label")

        if not team_label or not team_id:
            continue

        if team_label not in team_codes:
            parts = team_label.split(" ")

            location = parts[0] if len(parts) > 0 else ""
            name = parts[1] if len(parts) > 1 else ""

            if len(parts) == 2:
                if parts[0].lower() == "ny":
                    team_code = ("ny" + parts[1][0]).lower()
                    location = "New York"
                else:
                    team_code = parts[0][:3].lower()
            elif len(parts) >= 3:
                first_two = " ".join(parts[:2])
                location = first_two
                name = parts[2]

                if first_two in ["Los Angeles", "New York"]:
                    team_code = (parts[0][0] + parts[1][0] + parts[2][0]).lower()
                else:
                    team_code = (parts[0][0] + parts[1][0]).lower()
            else:
                team_code = ''.join(filter(str.isalnum, team_label.lower()))[:3]

            team_codes[team_id] = team_code
            team_details[team_id] = (team_code, location, name)

    # Limit to 5 logo downloads per batch
    logos_downloaded_this_batch = 0

    for player in items:
        if logos_downloaded_this_batch >= 5:
            break

        team = player.get("team", {})
        team_id = team.get("id")
        team_label = team.get("label")
        logo_url = team.get("imageUrl")

        team_code = team_codes.get(team_id, f"team_{team_id}")

        if team_id and logo_url and team_code not in downloaded_logos:
            logo_filename = f"logos/{team_code}.png"

            if os.path.exists(logo_filename):
                print(f"Skipping {team_label} logo — already exists as {logo_filename}")
                downloaded_logos.add(team_code)
                continue

            try:
                print(f"Downloading logo for {team_label} as {logo_filename} from {logo_url}")
                logo_response = requests.get(logo_url, headers=headers, timeout=30)
                if logo_response.status_code == 200:
                    with open(logo_filename, "wb") as f:
                        f.write(logo_response.content)
                    downloaded_logos.add(team_code)
                    logos_downloaded_this_batch += 1
                else:
                    print(f"Failed to download logo for {team_label}: {logo_response.status_code}")
            except Exception as e:
                print(f"Error downloading {logo_url}: {e}")

            logo_delay = random.uniform(1.0, 2.5)
            print(f"Sleeping after logo download {logo_delay:.2f} seconds...")
            time.sleep(logo_delay)

    # === Avatars per batch (with progress) ===
    if include_avatars:
        # Build the list for this batch (count both new and existing so progress matches items seen)
        avatar_items = []
        for p in items:
            avatar_url = p.get("avatarUrl")
            pid = p.get("id")
            if not avatar_url:
                continue

            # same filename logic you already use
            ext = _ext_from_url(avatar_url)
            filename = f"avatars/{pid}{ext}" if pid is not None else f"avatars/{abs(hash(avatar_url))}{ext}"

            # Keep everything in the list so progress reflects all applicable players
            avatar_items.append((pid, avatar_url, filename))

        for pid, avatar_url, filename in iter_progress(avatar_items, f"Avatars (offset {offset})"):
            key = pid if pid is not None else avatar_url
            if key in downloaded_avatars:
                continue
            if os.path.exists(filename):
                downloaded_avatars.add(key)
                continue

            try:
                r = requests.get(avatar_url, headers=headers, timeout=30)
                if r.status_code == 200 and r.content:
                    with open(filename, "wb") as f:
                        f.write(r.content)
                    downloaded_avatars.add(key)
                else:
                    # Quiet failure (no skip spam); still advances progress
                    pass
            except Exception:
                # Quiet error; still advances progress
                pass

            # random pause 0.1–1.0s between avatar downloads
            time.sleep(random.uniform(0.1, 1.0))


    if len(items) < limit:
        break

    offset += limit
    delay = random.uniform(2.5, 10)
    print(f"Sleeping after batch {delay:.2f} seconds...")
    time.sleep(delay)

# Save all items into one big JSON
with open("players.json", "w") as f:
    json.dump({"items": all_items}, f, indent=2)

# Save teams
try:
    df = pd.read_csv("teams.csv", dtype=str)
except FileNotFoundError:
    df = pd.DataFrame({
        "code": ["" for _ in range(32)],
        "location": ["" for _ in range(32)],
        "name": ["" for _ in range(32)]
    })

df.index = range(1, len(df) + 1)

for team_id, (code, location, name) in team_details.items():
    df.loc[team_id, "code"] = code
    df.loc[team_id, "location"] = location
    df.loc[team_id, "name"] = name

df.to_csv("teams.csv", index=False)

print(f"Saved {len(all_items)} records to players.json")
print(f"Saved teams to teams.csv")
print(f"Downloaded {len(downloaded_logos)} team logos to logos/")
if include_avatars:
    print(f"Downloaded {len(downloaded_avatars)} player avatars to avatars/")

# Create league.zip with players.json, teams.csv, logos/, and (NEW) avatars/
with zipfile.ZipFile("league.zip", "w", zipfile.ZIP_DEFLATED) as zipf:
    # players.json
    if os.path.exists("players.json"):
        zipf.write("players.json")

    # teams.csv
    if os.path.exists("teams.csv"):
        zipf.write("teams.csv")

    # logos/
    if os.path.exists("logos"):
        zipf.write("logos", arcname="logos/")
        for root, _, files in os.walk("logos"):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, start=".")
                zipf.write(file_path, arcname)

    # avatars/ (only if chosen and exists)
    if include_avatars and os.path.exists("avatars"):
        zipf.write("avatars", arcname="avatars/")
        for root, _, files in os.walk("avatars"):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, start=".")
                zipf.write(file_path, arcname)

print("✅ league.zip created with players.json, teams.csv, logos/, and avatars/ (if selected)")