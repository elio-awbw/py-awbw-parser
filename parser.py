import json
import zipfile
import os
import gzip
import re
import math
import urllib.request

class AWBWReplayParser:
    def __init__(self):
        self.game_info = {}
        self.players = {}
        self.actions = []
        self.towers = {}
        #self.initial_units = {}

    def extract_embedded_json(self, text):
        extracted_data = []
        decoder = json.JSONDecoder()
        idx = 0
        current_day = 1 
        current_player = None 
        
        while True:
            next_idx = text.find('{', idx)
            if next_idx == -1: break
            chunk = text[idx:next_idx]
            
            day_matches = re.findall(r'd:(\d+);', chunk)
            if day_matches: current_day = int(day_matches[-1])
                
            player_matches = re.findall(r'p:(\d+);', chunk)
            if player_matches: current_player = str(player_matches[-1]) 

            try:
                obj, length = decoder.raw_decode(text[next_idx:])
                if isinstance(obj, dict) and "action" in obj:
                    obj['day'] = current_day
                    obj['players_id'] = current_player 
                    extracted_data.append(obj)
                idx = next_idx + length
            except json.JSONDecodeError:
                idx = next_idx + 1
        return extracted_data

    def _parse_action(self, action_data):
        action_type = action_data.get("action", "Unknown")
        parsed_action = {
            "day": action_data.get("day"),
            "action_type": action_type,
            "raw_data": action_data 
        }
        if action_type == "Fire":
            parsed_action["attacker"] = action_data.get("attacker", {})
            parsed_action["defender"] = action_data.get("defender", {})
        elif action_type == "Move":
            parsed_action["unit"] = action_data.get("unit", {})
            parsed_action["path"] = action_data.get("path", [])
        elif action_type == "Build":
            parsed_action["unit_built"] = action_data.get("newUnit", {})
        return parsed_action

    def find_php_value(self, key, src):
        match = re.search(rf'"{key}";s:\d+:"([^"]+)"', src)
        return match.group(1) if match else None

    def load_local_zip(self, zip_path):
        filename = os.path.basename(zip_path)
        # Extract usernames from the filename
        name_matches = re.findall(r'_\(\d+\)_([^_]+)_', filename)
        maps_id_found = None

        with zipfile.ZipFile(zip_path, 'r') as archive:
            for file_name in archive.namelist():
                if file_name.endswith('/'): continue
                with archive.open(file_name) as f:
                    raw_bytes = f.read()
                    if raw_bytes.startswith(b'\x1f\x8b'):
                        try: raw_bytes = gzip.decompress(raw_bytes)
                        except: continue
                    try:
                        content = raw_bytes.decode('utf-8-sig', errors='ignore')
                        
                        # --- ACTION LOG FILES ---
                        if file_name.startswith('a') or "_" in file_name:
                            for action in self.extract_embedded_json(content):
                                self.actions.append(self._parse_action(action))
                                
                        # --- METADATA FILE ---
                        elif file_name.isnumeric():
                            self.game_info['name'] = self.find_php_value("name", content)
                            self.game_info['maps_name'] = self.find_php_value("maps_name", content)
                            
                            # Capture Maps ID for downloading the grid
                            m_match = re.search(r'"maps_id";(?:i:(\d+);|s:\d+:"(\d+)";)', content)
                            if m_match:
                                maps_id_found = m_match.group(1) or m_match.group(2)
                            
                            # --- PLAYER & SLOT EXTRACTION ---
                            # We split by awbwPlayer to keep Sort, ID, and CO tied together
                            player_blocks = content.split('O:10:"awbwPlayer"')
                            for i, block in enumerate(player_blocks[1:]):
                                p_id_m = re.search(r's:2:"id";(?:i:(\d+);|s:\d+:"(\d+)";)', block)
                                co_id_m = re.search(r's:5:"co_id";(?:i:(\d+);|s:\d+:"(\d+)";)', block)
                                sort_m = re.search(r's:4:"sort";i:(\d+);', block)
                                
                                if p_id_m:
                                    p_id = p_id_m.group(1) or p_id_m.group(2)
                                    co_id = co_id_m.group(1) or co_id_m.group(2) if co_id_m else "0"
                                    slot = int(sort_m.group(1)) if sort_m else i # Fallback to loop index
                                    
                                    p_name = name_matches[i] if i < len(name_matches) else f"Player_{p_id}"
                                    co_name = CO_ID_TO_NAME.get(str(co_id), f"UnknownCO({co_id})")
                                    
                                    self.players[str(p_id)] = {
                                        "username": p_name, 
                                        "co_name": co_name,
                                        "slot": slot,
                                        "inherited_tower_id": SLOT_TO_TOWER_ID.get(slot),
                                        "towers": 0 # Will be populated after map download
                                    }

                            # --- PRE-DEPLOYED UNITS (Optional/Disabled per your snippet) ---
                            if False:
                                unit_blocks = content.split('O:8:"awbwUnit"') 
                                for block in unit_blocks[1:]:
                                    id_m = re.search(r's:2:"id";(?:i:(\d+);|s:\d+:"(\d+)";)', block)
                                    p_m = re.search(r's:10:"players_id";(?:i:(\d+);|s:\d+:"(\d+)";)', block)
                                    n_m = re.search(r's:4:"name";s:\d+:"([^"]+)"', block)
                                    hp_m = re.search(r's:10:"hit_points";(?:i:(\d+);|s:\d+:"(\d+)";|d:([0-9.]+);)', block)
                                    
                                    if id_m and p_m and n_m:
                                        u_id = id_m.group(1) or id_m.group(2)
                                        self.initial_units[str(u_id)] = {
                                            "name": n_m.group(1),
                                            "player_id": str(p_m.group(1) or p_m.group(2)),
                                            "hp": float(hp_m.group(1) or hp_m.group(2) or hp_m.group(3)) * 10 if hp_m else 100.0
                                        }
                                
                    except Exception as e:
                        print(f"DEBUG: Error in {file_name}: {e}")
                    
        # --- FETCH THE MAP GRID ---
        if True:
            print(f"Fetching terrain grid for Map {maps_id_found}...")
            url = f"https://awbw.amarriner.com/text_map.php?maps_id={maps_id_found}"
            req = urllib.request.Request(url, headers={'User-Agent': 'AWBW-Parser'})
            try:
                with urllib.request.urlopen(req) as response:
                    html = response.read().decode('utf-8')
                    grid = []
                    for line in html.split('\n'):
                        clean_line = re.sub(r'<[^>]+>', '', line).strip()
                        if clean_line and re.match(r'^[\d,]+$', clean_line):
                            grid.append([int(x) for x in clean_line.split(',') if x.strip()])
                    self.game_info['map_grid'] = grid
                    
                    # --- INITIAL TOWER COUNTING ---
                    # Now that we have the grid, look for each player's inherited towers
                    for row in grid:
                        for tile_id in row:
                            for p_id, p_data in self.players.items():
                                if tile_id == p_data.get("inherited_tower_id"):
                                    p_data["towers"] += 1
                                    
            except Exception as e:
                print(f"Warning: Failed to download map. ({e})")

            self.building_owners = {} # Reset/Initialize
            for y, row in enumerate(grid):
                for x, tile_id in enumerate(row):
                    # Check inside your map-scan loop:
                    if tile_id == 133: # Neutral Tower
                        self.building_owners[(x, y)] = None
                    elif tile_id in SLOT_TO_TOWER_ID.values(): # Faction Tower
                        # ... code to find which player owns this tower based on slot ...
                        self.building_owners[(x, y)] = p_id
                
        print(f"Loaded: {self.game_info.get('name', 'Unknown')}")

    def print_summary(self):
        print("-" * 40)
        print(f"Game Name: {self.game_info.get('name', 'N/A')}")
        print(f"Map:       {self.game_info.get('maps_name', 'N/A')}")
        print("-" * 40)
        print(f"Total Actions Logged: {len(self.actions)}")
        print("-" * 40)


# --- CONSTANTS & HELPERS ---

# AWBW standard mapping: Slot Index -> Default Map Maker Tower Tile ID
SLOT_TO_TOWER_ID = {
    0: 134,  # Slot 1 (Orange Star)
    1: 129,  # Slot 2 (Blue Moon)
    2: 131,  # Slot 3 (Green Earth)
    3: 136,  # Slot 4 (Yellow Comet)
    4: 128,  # Slot 5 (Black Hole)
    5: 135,  # Slot 6 (Red Fire)
    6: 137,  # Slot 7 (Grey Sky)
    7: 130,  # Slot 8 (Brown Desert)
}

CO_ID_TO_NAME = {
    '1': 'Andy', '2': 'Colin', '3': 'Drake', '4': 'Eagle', '5': 'Flak',
    '6': 'Grit', '7': 'Max', '8': 'Nell', '9': 'Olaf', '10': 'Sami',
    '11': 'Sensei', '12': 'Sturm', '13': 'Adder', '14': 'Hawke', '15': 'Lash',
    '16': 'Jess', '17': 'Sonja', '18': 'Kanbei', '19': 'Grimm', '20': 'Koal',
    '21': 'Sasha', '22': 'Javier', '23': 'Kindle', '24': 'Jugger', '25': 'Jake',
    '26': 'Rachel', '27': 'Von Bolt', '28': 'Hachi'
}

INDIRECT_UNITS = ["Artillery", "Rocket", "Missile", "Piperunner", "Battleship", "Carrier"]

BASE_DAMAGE = {
    "Infantry": {
        "Infantry": 55, "Mech": 45, "Recon": 12, "Tank": 5, "Md.Tank": 1, 
        "Neotank": 1, "Mega Tank": 1, "APC": 14, "Anti-Air": 5, "Artillery": 15, 
        "Rocket": 25, "Missile": 25, "Piperunner": 5, "B-Copter": 7, "T-Copter": 30
    },
    "Mech": {
        "Infantry": 65, "Mech": 55, "Recon": 85, "Tank": 55, "Md.Tank": 15, 
        "Neotank": 15, "Mega Tank": 5, "APC": 75, "Anti-Air": 65, "Artillery": 70, 
        "Rocket": 85, "Missile": 85, "Piperunner": 55, "B-Copter": 9, "T-Copter": 35
    },
    "Recon": {
        "Infantry": 70, "Mech": 65, "Recon": 35, "Tank": 6, "Md.Tank": 1, 
        "Neotank": 1, "Mega Tank": 1, "APC": 45, "Anti-Air": 4, "Artillery": 45, 
        "Rocket": 55, "Missile": 28, "Piperunner": 6, "B-Copter": 10, "T-Copter": 35
    },
    "Tank": {
        "Infantry": 75, "Mech": 70, "Recon": 85, "Tank": 55, "Md.Tank": 15, 
        "Neotank": 15, "Mega Tank": 10, "APC": 75, "Anti-Air": 65, "Artillery": 70, 
        "Rocket": 85, "Missile": 85, "Piperunner": 55, "B-Copter": 10, "T-Copter": 40, 
        "Lander": 10, "Cruiser": 5, "Battleship": 1, "Sub": 1
    },
    "Md.Tank": {
        "Infantry": 105, "Mech": 95, "Recon": 105, "Tank": 85, "Md.Tank": 55, 
        "Neotank": 45, "Mega Tank": 25, "APC": 105, "Anti-Air": 105, "Artillery": 105, 
        "Rocket": 105, "Missile": 105, "Piperunner": 105, "B-Copter": 12, "T-Copter": 45, 
        "Lander": 35, "Cruiser": 45, "Battleship": 10, "Sub": 10
    },
    "Neotank": {
        "Infantry": 125, "Mech": 115, "Recon": 125, "Tank": 105, "Md.Tank": 75, 
        "Neotank": 55, "Mega Tank": 35, "APC": 125, "Anti-Air": 115, "Artillery": 115, 
        "Rocket": 125, "Missile": 125, "Piperunner": 105, "B-Copter": 22, "T-Copter": 55, 
        "Lander": 40, "Cruiser": 50, "Battleship": 15, "Sub": 15
    },
    "Mega Tank": {
        "Infantry": 135, "Mech": 125, "Recon": 195, "Tank": 180, "Md.Tank": 125, 
        "Neotank": 115, "Mega Tank": 65, "APC": 195, "Anti-Air": 195, "Artillery": 195, 
        "Rocket": 195, "Missile": 195, "Piperunner": 180, "B-Copter": 22, "T-Copter": 55, 
        "Lander": 105, "Cruiser": 65, "Battleship": 45, "Sub": 45
    },
    "Anti-Air": {
        "Infantry": 105, "Mech": 105, "Recon": 60, "Tank": 25, "Md.Tank": 10, 
        "Neotank": 10, "Mega Tank": 1, "APC": 50, "Anti-Air": 45, "Artillery": 50, 
        "Rocket": 55, "Missile": 55, "Piperunner": 25, "B-Copter": 120, "T-Copter": 120, 
        "Fighter": 65, "Bomber": 75, "Stealth": 75
    },
    "Artillery": {
        "Infantry": 90, "Mech": 85, "Recon": 80, "Tank": 70, "Md.Tank": 45, 
        "Neotank": 40, "Mega Tank": 15, "APC": 70, "Anti-Air": 75, "Artillery": 75, 
        "Rocket": 80, "Missile": 80, "Piperunner": 70, "Lander": 55, "Cruiser": 65, 
        "Battleship": 40, "Sub": 60
    },
    "Rocket": {
        "Infantry": 95, "Mech": 90, "Recon": 90, "Tank": 80, "Md.Tank": 55, 
        "Neotank": 50, "Mega Tank": 25, "APC": 80, "Anti-Air": 85, "Artillery": 80, 
        "Rocket": 85, "Missile": 85, "Piperunner": 80, "Lander": 60, "Cruiser": 85, 
        "Battleship": 55, "Sub": 85
    },
    "Missile": {
        "B-Copter": 120, "T-Copter": 120, "Fighter": 100, "Bomber": 100, "Stealth": 100
    },
    "Piperunner": {
        "Infantry": 95, "Mech": 90, "Recon": 90, "Tank": 80, "Md.Tank": 55, 
        "Neotank": 50, "Mega Tank": 25, "APC": 80, "Anti-Air": 85, "Artillery": 80, 
        "Rocket": 85, "Missile": 85, "Piperunner": 80, "B-Copter": 105, "T-Copter": 105, 
        "Fighter": 65, "Bomber": 75, "Stealth": 75, "Lander": 60, "Cruiser": 60, 
        "Battleship": 55, "Sub": 60
    },
    "B-Copter": {
        "Infantry": 75, "Mech": 75, "Recon": 55, "Tank": 55, "Md.Tank": 25, 
        "Neotank": 20, "Mega Tank": 10, "APC": 60, "Anti-Air": 25, "Artillery": 65, 
        "Rocket": 65, "Missile": 65, "Piperunner": 55, "B-Copter": 65, "T-Copter": 95, 
        "Lander": 25, "Cruiser": 55, "Battleship": 25, "Sub": 25
    },
    "Fighter": {
        "B-Copter": 100, "T-Copter": 100, "Fighter": 55, "Bomber": 100, "Stealth": 85
    },
    "Bomber": {
        "Infantry": 110, "Mech": 110, "Recon": 105, "Tank": 105, "Md.Tank": 95, 
        "Neotank": 90, "Mega Tank": 35, "APC": 105, "Anti-Air": 95, "Artillery": 105, 
        "Rocket": 105, "Missile": 105, "Piperunner": 105, "Lander": 95, "Cruiser": 85, 
        "Battleship": 75, "Sub": 95
    },
    "Stealth": {
        "Infantry": 90, "Mech": 90, "Recon": 85, "Tank": 75, "Md.Tank": 70, 
        "Neotank": 60, "Mega Tank": 15, "APC": 85, "Anti-Air": 50, "Artillery": 75, 
        "Rocket": 85, "Missile": 85, "Piperunner": 80, "B-Copter": 120, "T-Copter": 120, 
        "Fighter": 45, "Bomber": 70, "Stealth": 55, "Lander": 65, "Cruiser": 35, 
        "Battleship": 45, "Sub": 55
    },
    "Cruiser": {
        "B-Copter": 115, "T-Copter": 115, "Fighter": 55, "Bomber": 65, "Stealth": 100, "Sub": 90
    },
    "Battleship": {
        "Infantry": 95, "Mech": 90, "Recon": 90, "Tank": 80, "Md.Tank": 55, 
        "Neotank": 50, "Mega Tank": 25, "APC": 80, "Anti-Air": 85, "Artillery": 80, 
        "Rocket": 85, "Missile": 85, "Piperunner": 80, "Lander": 95, "Cruiser": 95, 
        "Battleship": 50, "Sub": 95
    },
    "Sub": {
        "Lander": 95, "Cruiser": 25, "Battleship": 55, "Sub": 55
    },
    "Carrier": {
        "B-Copter": 115, "T-Copter": 120, "Fighter": 100, "Bomber": 100, "Stealth": 100
    }
}

TERRAIN_STARS = {
    '1': 0, 'Plain': 1, 'Mountain': 4, 'Forest': 2, 'City': 3, 'Base': 3, 
    'Airport': 3, 'Port': 3, 'HQ': 4, 'Road': 0, 'River': 0, 'Sea': 0, 'Reef': 1
}

def get_terrain_stars(terrain_id):
    """Maps AWBW internal terrain IDs to their defense star values."""
    tid = int(terrain_id)
    
    # 1 Star: Plains, Reefs
    if tid in [1, 9, 28]: 
        return 1
    # 2 Stars: Woods
    elif tid == 3: 
        return 2
    # 4 Stars: Mountains & HQs (Various faction HQ IDs)
    elif tid == 2 or tid in [43, 44, 45, 46, 83, 84, 85, 86, 123, 124, 134, 143, 144, 145, 146]: 
        return 4
    # 0 Stars: Rivers(4), Roads(5), Bridges(6), Sea(7), Shoal(8), Pipes(111-116), plus all corner/junction variants(15-32)
    elif tid in [4, 5, 6, 7, 8] or (15 <= tid <= 32) or (111 <= tid <= 116): 
        return 0
    # 3 Stars: Catch-all for Cities, Bases, Airports, Ports, Com Towers, Labs, Silos (>10)
    elif tid > 10: 
        return 3
        
    return 0

def get_co_modifiers(co_name, power_state, unit_name):
    atk, dfn = 100, 100
    if power_state in ["COP", "SCOP"]: atk += 10; dfn += 10
    
    is_foot = unit_name in ["Infantry", "Mech"]
    is_indirect = unit_name in INDIRECT_UNITS
    is_copter = unit_name in ["B-Copter", "T-Copter"]
    is_air = unit_name in ["Fighter", "Bomber", "Stealth", "B-Copter", "T-Copter", "Black Bomb"]
    is_naval = unit_name in ["Battleship", "Cruiser", "Sub", "Lander", "Black Boat", "Carrier"]
    is_direct = not is_indirect
    
    if co_name == "Max":
        if is_indirect: atk -= 10 
        elif is_direct and not is_foot: 
            if power_state == "D2D": atk = 120
            elif power_state == "COP": atk = 160 
            elif power_state == "SCOP": atk = 190
    elif co_name == "Sami":
        if is_foot:
            if power_state == "D2D": atk = 130
            elif power_state == "COP": atk = 160
            elif power_state == "SCOP": atk = 180
        elif is_direct and not is_foot: atk -= 10
    elif co_name == "Grit":
        if is_indirect:
            if power_state == "D2D": atk = 120
            elif power_state == "COP": atk = 150
            elif power_state == "SCOP": atk = 150
        elif is_direct and not is_foot: atk -= 20
    elif co_name == "Sensei":
        if is_copter:
            if power_state == "D2D": atk = 150
            elif power_state == "COP": atk = 175
            elif power_state == "SCOP": atk = 175
        elif is_foot: atk = 140 if power_state == "D2D" else 150
        elif not is_air and not is_naval: atk -= 10
    elif co_name == "Eagle":
        if is_air: atk += 15; dfn += 10
        elif is_naval: atk -= 30
    elif co_name == "Kanbei":
        atk = 130 if power_state == "D2D" else 150
        dfn = 130 if power_state == "D2D" else (140 if power_state == "COP" else 160)
    elif co_name == "Grimm":
        atk += 30; dfn -= 20
    elif co_name == "Colin":
        atk -= 10
    return atk, dfn

def calculate_damage(base_dmg, atk_hp, def_hp, atk_mod=100, def_mod=100, terr_stars=0, luck=0):
    raw_dmg = (base_dmg * atk_mod / 100 + luck) * atk_hp / 10 * ((200 - (def_mod + terr_stars * def_hp)) / 100)
    if raw_dmg <= 0:
        return 0
    else:
        return math.floor(math.ceil(raw_dmg * 20) / 20)

def get_luck_rolls(base_dmg, atk_hp, def_hp, atk_val=100, def_val=100, terr_stars=0):
    return [calculate_damage(base_dmg, atk_hp, def_hp, atk_val, def_val, terr_stars, luck=i) for i in range(10)]

def get_base_dmg(attacker_name, defender_name):
    return BASE_DAMAGE.get(attacker_name, {}).get(defender_name)

# --- CORE LOGIC FUNCTIONS ---
def process_combat_log(parser, days=None):
    if not parser.players:
        print("!!! ERROR: parser.players is EMPTY. The meta-file was not parsed correctly.")
        return
    else:
        print(f"--- SUCCESS: Found {len(parser.players)} players: {list(parser.players.keys())} ---")

    player_state = {}
    for i, (pid, p_data) in enumerate(parser.players.items()):
        player_state[str(pid)] = {
            "name": p_data.get("username"), 
            "co": p_data.get("co_name"), 
            "power": "D2D",
            "turn_index": i
        }

    unit_memory = {}

    print(f"--- Combat Log (List-Safe) ---")

    for action in parser.actions:
        day = action["day"]
        raw = action["raw_data"]
        action_type = action["action_type"]
        
        p_id = str(raw["players_id"])
        p_info = player_state[p_id]
        p_name = p_info["name"]
        p_idx = p_info["turn_index"]

        for key in player_state:
            if key != p_id:
                other_p_id = key

        if action_type == "Resign":
            pass
        elif action_type == "End":
            if (day) and ((days is not None) and (day in days)):
                print(f"Day {day}.{p_idx} | -- {p_name} ended turn --")
            # deal with COP going off if it is your
            for u_repair in raw["updatedInfo"]["repaired"]["global"]:
                uid = u_repair["units_id"]
                if uid not in unit_memory:
                    hp = u_repair["units_hit_points"]
                    if hp != 10:
                        raise ValueError
                    unit_memory[uid] = {"name": u_build["units_name"], "min_hp": 100.0, "max_hp": 100.0, "hp": u_repair["units_hit_points"]}
                new_hp = u_repair["units_hit_points"]
                if new_hp != unit_memory[uid]["hp"]:
                    healed_amount = (new_hp - unit_memory[uid]["hp"]) * 10
                    unit_memory[uid]["min_hp"] = min(100, unit_memory[uid]["min_hp"] + healed_amount)
                    unit_memory[uid]["max_hp"] = min(100, unit_memory[uid]["max_hp"] + healed_amount)
                    unit_memory[uid]["hp"] = new_hp
            player_state[other_p_id]["power"] = "D2D"
        elif action_type == "Power":
            if raw["coPower"] == "S":
                player_state[p_id]["power"] = "SCOP"
            elif raw["coPower"] == "Y":
                player_state[p_id]["power"] = "COP"
            else:
                raise ValueError
        elif action_type == "Join":
            uid1 = raw["Join"]["joinID"]["global"]
            uid2 = raw["Join"]["unit"]["global"]["units_id"]
            hp1 = unit_memory[uid1]["hp"]
            hp2 = unit_memory[uid2]["hp"]
            new_hp = hp1 + hp2
            del unit_memory[uid1]
            unit_memory[uid2]["hp"] = new_hp
            unit_memory[uid2]["min_hp"] = new_hp * 10
            unit_memory[uid2]["max_hp"] = new_hp * 10
        elif action_type == "Build":
            u_build = raw["newUnit"]["global"] #[p_id]
            unit_memory[u_build["units_id"]] = {"name": u_build["units_name"], "min_hp": 100.0, "max_hp": 100.0, "hp": u_build["units_hit_points"]}
        elif action_type == "Move":
            u_unit = raw["unit"][p_id]
            uid = u_unit["units_id"]
            if uid not in unit_memory: # Pre-deployed units! Let's assume they always move before being part of an attack.
                unit_memory[uid] = {"name": u_unit["units_name"], "min_hp": 100.0, "max_hp": 100.0, "hp": u_unit["units_hit_points"]}
        elif action_type == "Join":
            pass #need to deal with this too
        elif action_type == "Fire":
            f_data = raw["Fire"]
            combat = f_data["combatInfoVision"]["global"]["combatInfo"]
            cv = f_data["copValues"]

            a_pid, d_pid = str(cv["attacker"]["playerId"]), str(cv["defender"]["playerId"])
            a_co, a_pow = player_state[a_pid]["co"], player_state[a_pid]["power"]
            d_co, d_pow = player_state[d_pid]["co"], player_state[d_pid]["power"]
            
            a_id, d_id = combat["attacker"]["units_id"], combat["defender"]["units_id"]
            a_name = unit_memory[a_id]["name"]
            d_name = unit_memory[d_id]["name"]
            
            a_hp, d_hp = unit_memory[a_id]["hp"], unit_memory[d_id]["hp"]
            a_min_hp, a_max_hp = unit_memory[a_id]["min_hp"], unit_memory[a_id]["max_hp"]
            d_min_hp, d_max_hp = unit_memory[d_id]["min_hp"], unit_memory[d_id]["max_hp"]

            a_hp_end, d_hp_end = float(combat["attacker"]["units_hit_points"]), float(combat["defender"]["units_hit_points"])

            a_atk_mod, a_def_mod = get_co_modifiers(a_co, a_pow, a_name)
            a_atk_mod += 10 * parser.players[p_id]["towers"]
            d_atk_mod, d_def_mod = get_co_modifiers(d_co, d_pow, d_name)
            d_atk_mod += 10 * parser.players[other_p_id]["towers"]
            a_base_dmg = get_base_dmg(a_name, d_name)

            d_x, d_y = combat["defender"]["units_x"], combat["defender"]["units_y"]
            d_terr_stars = get_terrain_stars(parser.game_info["map_grid"][d_y][d_x])
            if d_name in ["Fighter", "Bomber", "Stealth", "B-Copter", "T-Copter", "Black Bomb"]:
                    d_terr_stars = 0

            if a_base_dmg is None:
                print(a_name, d_name)
                print(BASE_DAMAGE[a_name])
                print(BASE_DAMAGE[a_name][d_name])
            a_possible_dmgs = get_luck_rolls(a_base_dmg, a_hp, d_hp, a_atk_mod, d_def_mod, terr_stars=d_terr_stars)
            a_min_dmg = min(a_possible_dmgs)
            a_max_dmg = max(a_possible_dmgs)

            d_min_hp_possible, d_max_hp_possible = max(0, d_min_hp - a_max_dmg), max(0, d_max_hp - a_min_dmg)
            d_min_hp_end, d_max_hp_end = max(d_min_hp_possible, 10 * d_hp_end - 9), min(d_max_hp_possible, 10 * d_hp_end)

            unit_memory[d_id]["min_hp"] = d_min_hp_end
            unit_memory[d_id]["max_hp"] = d_max_hp_end
            unit_memory[d_id]["hp"] = d_hp_end
            
            if (day) and ((days is not None) and (day in days)):
                    print(f"Day {day}.{p_idx} | {p_name} FIRED: {a_name} ({a_hp}) vs {d_name} ({d_hp} [{d_min_hp}-{d_max_hp}%]->{d_hp_end} [{d_min_hp_end}-{d_max_hp_end}%])")
                    print(f"         ├─> Theoretical Dmg: {a_min_dmg}-{a_max_dmg}%")

            if d_hp_end > 0:
                d_base_dmg = get_base_dmg(d_name, a_name)

                if (d_base_dmg is not None) and (d_base_dmg > 0):

                    a_x, a_y = combat["attacker"]["units_x"], combat["attacker"]["units_y"]
                    a_terr_stars = get_terrain_stars(parser.game_info["map_grid"][a_y][a_x])
                    if a_name in ["Fighter", "Bomber", "Stealth", "B-Copter", "T-Copter", "Black Bomb"]:
                            a_terr_stars = 0

                    d_possible_dmgs = get_luck_rolls(d_base_dmg, d_hp_end, a_hp, d_atk_mod, a_def_mod, terr_stars=a_terr_stars)
                    d_min_dmg = min(d_possible_dmgs)
                    d_max_dmg = max(d_possible_dmgs)

                    a_min_hp_possible, a_max_hp_possible = max(0, a_min_hp - d_max_dmg), max(0, a_max_hp - d_min_dmg)
                    a_min_hp_end, a_max_hp_end = max(a_min_hp_possible, 10 * a_hp_end - 9), min(a_max_hp_possible, 10 * a_hp_end)

                    unit_memory[a_id]["min_hp"] = a_min_hp_end
                    unit_memory[a_id]["max_hp"] = a_max_hp_end
                    unit_memory[a_id]["hp"] = a_hp_end
                    
                    if (day) and ((days is not None) and (day in days)):
                            print(f"Day {day}.{p_idx} | {p_name} COUNTERED: {d_name} ({d_hp_end}) vs {a_name} ({a_hp} [{a_min_hp}-{a_max_hp}%]->{a_hp_end} [{a_min_hp_end}-{a_max_hp_end}%])")
                            print(f"         ├─> Theoretical Dmg: {d_min_dmg}-{d_max_dmg}%")

        elif action_type in ["Capt"]:
            capt_data = raw.get("Capt", {})
            b_info = capt_data.get("buildingInfo", {})
            
            # 1. Safely get coordinates and current capture health
            bx = b_info.get("buildings_x")
            by = b_info.get("buildings_y")
            cap_hp = b_info.get("buildings_capture")
            
            # 2. Check if this is a FINISHED capture
            # Logic: If HP is back to 20 and there is a Player ID assigned, it's done.
            new_owner_id = b_info.get("buildings_players_id") or b_info.get("buildings_team")
            
            if cap_hp == 20 and new_owner_id is not None:
                new_owner_id = str(new_owner_id)
                
                # 3. Verify if this coordinate is a known COM Tower
                if (bx, by) in parser.building_owners:
                    old_owner_id = parser.building_owners[(bx, by)]
                    
                    # 4. Perform the swap
                    if old_owner_id != new_owner_id:
                        # DECREMENT OLD OWNER
                        if old_owner_id and old_owner_id in parser.players:
                            parser.players[old_owner_id]["towers"] = max(0, parser.players[old_owner_id]["towers"] - 1)
                            print(f"Day {day} | [TOWER LOSS] {parser.players[old_owner_id]['username']} at ({bx}, {by})")

                        # INCREMENT NEW OWNER
                        if new_owner_id in parser.players:
                            parser.players[new_owner_id]["towers"] += 1
                            print(f"Day {day} | [TOWER GAIN] {parser.players[new_owner_id]['username']} at ({bx}, {by})")

                        # 5. Update registry for the next person who tries to steal it
                        parser.building_owners[(bx, by)] = new_owner_id
                        pass
        else:
            raise ValueError

def main(zip_path, days=None):
    parser = AWBWReplayParser()
    parser.load_local_zip(zip_path)
    parser.print_summary()
    process_combat_log(parser, days=days)
    return parser

if __name__ == "__main__":
    fp = "/Users/elio/Downloads"
    file = os.path.join(fp, "replay_1598704_AC6_D1_[F]_R1G08_-_(11)_SonjaTheSuperior_vs._(118)_elio_2026-03-16.zip")
    parser = main(file, days=[4, 11, 12, 13, 14])
