"""
Age Plugin (Traveler-Enabled)
==============================
Version: 3.2.0-travel
Author: AlienMajik + travel system by MediaCutlet (Strato)
License: MIT

This is a drop‑in replacement for the Age plugin that adds a lightweight
"Traveler" progression system focused on *novelty* and *variety* while keeping
UI clutter minimal. Traveler XP is awarded for first-time encounters (new
ESSIDs, BSSIDs, OUIs, channels, bands) and for discovering new "places"
(GPS grid cells if available; otherwise a coarse Wi‑Fi fingerprint).

Config (add to /etc/pwnagotchi/config.toml):
-------------------------------------------
# Enable the Traveler system (default true)
main.plugins.age.enable_travel = true

# Grid size for GPS quantization (degrees). 0.01 ~ 1.1 km. Smaller = denser places
main.plugins.age.travel_grid = 0.01

# UI positions (optional)
main.plugins.age.traveler_x = 74
main.plugins.age.traveler_y = 190

# Show personality line (optional; default false to avoid clutter)
main.plugins.age.show_personality = false

# Optional: adjust decay behavior
main.plugins.age.decay_interval = 50
main.plugins.age.decay_amount = 5

Notes:
- If a simple GPS JSON with {"lat": <float>, "lon": <float>} is available at one
  of the candidate paths (see self.gps_candidate_paths), place discovery will
  use a quantized grid. Otherwise, it falls back to a coarse Wi‑Fi fingerprint
  (OUI+band+channel) so that travel progression can still advance across
  materially different environments.
- Traveler UI shows: "<title> L<level> · <places>pl"
"""

import os
import json
import logging
import time
import random
import threading

import pwnagotchi
import pwnagotchi.plugins as plugins
import pwnagotchi.ui.faces as faces
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK


class Age(plugins.Plugin):
    __author__ = 'AlienMajik'
    __version__ = '3.2.0-travel'
    __license__ = 'MIT'
    __description__ = (
        'An enhanced plugin with frequent titles, dynamic quotes, progress bars, '
        'random events, handshake streaks, personality evolution, secret achievements, '
        'and a Traveler system that rewards novelty (new ESSIDs/BSSIDs/OUIs/channels/bands/places). '
        'UI is optimized to avoid clutter.'
    )

    DEFAULT_AGE_TITLES = {
        100: "Cosmic Hatchling",
        200: "Byte Starling",
        275: "Signal Spark",
        350: "Signal Seeker",
        450: "Ping Nomad",
        600: "Packet Voyager",
        750: "Orbitling",
        900: "Neural Apprentice",
        1_050: "Pulse Pioneer",
        1_200: "Quantum Wanderer",
        1_350: "Script Satellite",
        1_500: "Code Comet",
        2_000: "Wavelength Nomad",
        3_000: "Protocol Pioneer",
        4_000: "Data Asteroid",
        5_000: "Stellar Coder",
        7_000: "Encrypted Voyager",
        10_000: "WiFi Starborn",
        15_000: "Nebula Navigator",
        20_000: "Celestial Hacker",
        30_000: "Ethernaut",
        40_000: "Binary Constellation",
        55_000: "Quantum Overlord",
        80_000: "Dark Matter Diver",
        100_000: "Galactic Root",
        111_111: "Singularity Sentinel"
    }

    DEFAULT_STRENGTH_TITLES = {
        100: "Circuit Initiate",
        250: "Pulse Drifter",
        400: "Bitbreaker",
        600: "Packet Slinger",
        900: "Firewall Skipper",
        1_200: "Deauth Cadet",
        1_600: "Hash Harvester",
        2_000: "Spectral Scrambler",
        2_500: "Protocol Predator",
        3_200: "Cipher Crusher",
        4_500: "WiFi Marauder",
        6_000: "Neural Nullifier",
        8_000: "Signal Saboteur",
        12_000: "Astral Sniffer",
        18_000: "Quantum Brawler",
        30_000: "Rootwave Ronin",
        55_555: "Void Breaker",
        111_111: "Omega Cipherlord"
    }

    DEFAULT_TRAVEL_TITLES = {
        0: "Homebody",
        50: "Wanderling",
        150: "City Stroller",
        300: "Road Warrior",
        600: "Jetsetter",
        1200: "Globetrotter",
    }

    def __init__(self):
        # Default UI positions (x, y)
        self.default_positions = {
            'age': (10, 40),
            'strength': (80, 40),
            'points': (10, 60),
            'progress': (10, 80),
            'personality': (10, 100),
        }

        # Core metrics
        self.epochs = 0
        self.train_epochs = 0
        self.network_points = 0
        self.handshake_count = 0
        self.last_active_epoch = 0
        self.data_path = '/root/age_strength.json'
        self.log_path = '/root/network_points.log'
        self.handshake_dir = '/home/pi/handshakes'

        # Configurable settings
        self.decay_interval = 50
        self.decay_amount = 10
        self.age_titles = self.DEFAULT_AGE_TITLES
        self.strength_titles = self.DEFAULT_STRENGTH_TITLES
        self.points_map = {
            'wpa3': 10,
            'wpa2': 5,
            'wep': 2,
            'wpa': 2
        }
        self.motivational_quotes = [
            "Keep going, you're crushing it!",
            "You're a WiFi wizard in the making!",
            "More handshakes, more power!",
            "Don't stop now, you're almost there!",
            "Keep evolving, don't let decay catch you!"
        ]
        self.show_personality = False  # default False to avoid clutter

        # Achievement tracking
        self.prev_age_title = "Unborn"
        self.prev_strength_title = "Untrained"

        # Runtime state
        self.last_handshake_enc = None
        self.last_decay_points = 0
        self.streak = 0
        self.active_event = None
        self.event_handshakes_left = 0
        self.event_multiplier = 1.0
        self.personality_points = {'aggro': 0, 'stealth': 0, 'scholar': 0}
        self.night_owl_handshakes = 0
        self.enc_types_captured = set()

        # Thread safety for persistence
        self.data_lock = threading.Lock()

        # --- Traveler system ---
        self.enable_travel = True
        self.travel_xp = 0
        self.travel_level = 0
        self.travel_titles = dict(self.DEFAULT_TRAVEL_TITLES)
        self.unique_essids = set()
        self.unique_bssids = set()
        self.unique_ouis = set()
        self.unique_channels = set()
        self.unique_bands = set()
        self.place_hashes = set()
        self.last_place_hash = None

        # Optional GPS integration: simple JSON {"lat": float, "lon": float}
        self.gps_candidate_paths = [
            "/tmp/pwnagotchi-gps.json",
            "/tmp/gps.json",
            "/root/.pwnagotchi-gps.json",
            "/var/run/pwnagotchi/gps.json",
        ]
        # Grid size in degrees (0.01 ≈ 1.1 km)
        self.travel_grid = 0.01

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_loaded(self):
        # Load configuration options with fallbacks
        self.decay_interval = self.options.get('decay_interval', self.decay_interval)
        self.decay_amount = self.options.get('decay_amount', self.decay_amount)
        self.age_titles = self.options.get('age_titles', self.age_titles)
        self.strength_titles = self.options.get('strength_titles', self.strength_titles)
        self.points_map = self.options.get('points_map', self.points_map)
        self.motivational_quotes = self.options.get('motivational_quotes', self.motivational_quotes)
        self.show_personality = self.options.get('show_personality', self.show_personality)

        # Traveler-specific
        self.enable_travel = self.options.get('enable_travel', self.enable_travel)
        try:
            self.travel_grid = float(self.options.get('travel_grid', self.travel_grid))
        except Exception:
            pass
        self.travel_titles = self.options.get('travel_titles', self.travel_titles)

        self.load_data()
        self.initialize_handshakes()

    def initialize_handshakes(self):
        """Initialize handshake count based on existing .pcap files."""
        try:
            if self.handshake_count == 0 and os.path.isdir(self.handshake_dir):
                existing = [f for f in os.listdir(self.handshake_dir) if f.endswith('.pcap')]
                self.handshake_count = len(existing)
                logging.info(f"[Age] Initialized with {self.handshake_count} handshakes")
                self.save_data()
        except Exception as e:
            logging.error(f"[Age] initialize_handshakes error: {e}")

    # ------------------------------------------------------------------
    # Titles & UI
    # ------------------------------------------------------------------
    def get_age_title(self):
        """Determine age title based on epochs."""
        thresholds = sorted(self.age_titles.keys(), reverse=True)
        for t in thresholds:
            if self.epochs >= t:
                return self.age_titles[t]
        return "Unborn"

    def get_strength_title(self):
        """Determine strength title based on train_epochs."""
        thresholds = sorted(self.strength_titles.keys(), reverse=True)
        for t in thresholds:
            if self.train_epochs >= t:
                return self.strength_titles[t]
        return "Untrained"

    def on_ui_setup(self, ui):
        """Set up UI elements with configurable positions."""
        def get_position(element):
            x = self.options.get(f"{element}_x", self.default_positions[element][0])
            y = self.options.get(f"{element}_y", self.default_positions[element][1])
            return (int(x), int(y))

        positions = {key: get_position(key) for key in self.default_positions if key != 'stars'}

        ui.add_element('Age', LabeledValue(
            color=BLACK, label='Age', value="Newborn",
            position=positions['age'], label_font=fonts.Bold, text_font=fonts.Medium))

        ui.add_element('Strength', LabeledValue(
            color=BLACK, label='Str', value="Rookie",
            position=positions['strength'], label_font=fonts.Bold, text_font=fonts.Medium))

        ui.add_element('Points', LabeledValue(
            color=BLACK, label='Pts', value="0",
            position=positions['points'], label_font=fonts.Bold, text_font=fonts.Medium))

        ui.add_element('Progress', LabeledValue(
            color=BLACK, label='Next Age ', value="|     |",
            position=positions['progress'], label_font=fonts.Bold, text_font=fonts.Medium))

        if self.show_personality:
            ui.add_element('Personality', LabeledValue(
                color=BLACK, label='Trait ', value="Neutral",
                position=positions['personality'], label_font=fonts.Bold, text_font=fonts.Medium))

        # Traveler (compact single line)
        if self.enable_travel:
            tx = int(self.options.get('traveler_x', 10))
            ty = int(self.options.get('traveler_y', 118))
            ui.add_element('Traveler', LabeledValue(
                color=BLACK, label='Trav ', value="",
                position=(tx, ty), label_font=fonts.Bold, text_font=fonts.Medium))

    def on_ui_update(self, ui):
        """Update UI elements with current values."""
        ui.set('Age', self.get_age_title())
        ui.set('Strength', self.get_strength_title())
        ui.set('Points', self.abrev_number(self.network_points))

        # Progress bar for next age title
        next_threshold = self.get_next_age_threshold()
        if next_threshold:
            progress = max(0.0, min(1.0, self.epochs / float(next_threshold)))
            bar_length = 5
            filled = int(progress * bar_length)
            bar = '|' + '▥' * filled + ' ' * (bar_length - filled) + '|'
            ui.set('Progress', bar)
        else:
            ui.set('Progress', '[MAX]')

        if self.show_personality:
            ui.set('Personality', self.get_dominant_personality())

        if self.enable_travel:
            ttitle = self.get_travel_title()
            places = len(self.place_hashes)
            ui.set('Traveler', f"{ttitle} ({places}pl)")

    def get_next_age_threshold(self):
        """Get the next age title threshold."""
        thresholds = sorted(self.age_titles.keys())
        for t in thresholds:
            if self.epochs < t:
                return t
        return None  # Max level reached

    # ------------------------------------------------------------------
    # Epoch / Events / Decay
    # ------------------------------------------------------------------
    def random_motivational_quote(self):
        """Return a context-aware motivational quote."""
        if self.last_handshake_enc:
            quote = f"Boom! That {self.last_handshake_enc.upper()} never saw you coming."
            self.last_handshake_enc = None
            return quote
        elif self.last_decay_points > 0:
            quote = f"Decay stung for {self.last_decay_points}. Time to fight back!"
            self.last_decay_points = 0
            return quote
        else:
            return random.choice(self.motivational_quotes)

    def random_inactivity_message(self, points_lost):
        """Return a random inactivity message with points lost."""
        messages = [
            f"Time to wake up, lost {points_lost} to rust!",
            f"Decayed by {points_lost}, keep it active!",
            "Stale, but you can still revive!",
            "Don't let inactivity hold you back!",
            "Keep moving, no room for decay!",
        ]
        return random.choice(messages)

    def check_achievements(self, agent):
        """Check and announce new age or strength achievements."""
        current_age = self.get_age_title()
        current_strength = self.get_strength_title()

        if current_age != self.prev_age_title:
            agent.view().set('face', faces.HAPPY)
            agent.view().set('status', f"🎉 {current_age} Achieved! {self.random_motivational_quote()}")
            logging.info(f"[Age] New age title: {current_age}")
            self.prev_age_title = current_age

        if current_strength != self.prev_strength_title:
            agent.view().set('face', faces.MOTIVATED)
            agent.view().set('status', f"💪 Evolved to {current_strength}!")
            logging.info(f"[Age] New strength title: {current_strength}")
            self.prev_strength_title = current_strength

    def apply_decay(self, agent):
        """Apply decay to network points based on inactivity."""
        inactive_epochs = self.epochs - self.last_active_epoch
        if inactive_epochs >= self.decay_interval:
            decay_factor = inactive_epochs / float(self.decay_interval)
            points_lost = int(decay_factor * self.decay_amount)
            self.network_points = max(0, self.network_points - points_lost)

            if points_lost > 0:
                self.last_decay_points = points_lost
                self.streak = 0  # Reset streak on decay
                agent.view().set('face', faces.SAD)
                agent.view().set('status', self.random_inactivity_message(points_lost))
                logging.info(f"[Age] Applied decay: lost {points_lost} points")
                self.last_active_epoch = self.epochs
                self.save_data()

    def on_epoch(self, agent, epoch, epoch_data):
        """Handle epoch events."""
        self.epochs += 1
        if self.epochs % 10 == 0:
            self.train_epochs += 1
            self.personality_points['scholar'] += 1

        logging.debug(f"[Age] Epoch {self.epochs}, Points: {self.network_points}")

        self.apply_decay(agent)
        self.check_achievements(agent)

        if self.epochs % 100 == 0:
            self.handle_random_event(agent)
            self.age_checkpoint(agent)

        self.save_data()

    def handle_random_event(self, agent):
        """Trigger a random event with 5% chance every 100 epochs."""
        try:
            if random.random() < 0.05:
                events = [
                    {"description": "Lucky Break: Double points for next 5 handshakes!", "multiplier": 2.0, "handshakes": 5},
                    {"description": "Signal Noise: Next handshake worth half points.", "multiplier": 0.5, "handshakes": 1},
                ]
                self.active_event = random.choice(events)
                self.event_handshakes_left = self.active_event["handshakes"]
                self.event_multiplier = self.active_event["multiplier"]
                agent.view().set('status', self.active_event["description"])
                logging.info(f"[Age] Random event: {self.active_event['description']}")
        except Exception as e:
            logging.error(f"[Age] handle_random_event error: {e}")

    def age_checkpoint(self, agent):
        """Display milestone message every 100 epochs."""
        try:
            view = agent.view()
            view.set('face', faces.HAPPY)
            view.set('status', f"Epoch milestone: {self.epochs} epochs!")
            view.update(force=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Handshakes
    # ------------------------------------------------------------------
    def on_handshake(self, agent, *args):
        """Handle handshake events with streaks, achievements, and Traveler XP."""
        try:
            if len(args) < 3:
                logging.warning("[Age] Insufficient arguments in on_handshake")
                return

            ap = args[2]
            if not isinstance(ap, dict):
                logging.warning(f"[Age] AP is not a dict: {type(ap)}")
                return

            enc = (ap.get('encryption', '') or '').lower()
            essid = ap.get('essid', 'unknown')
            bssid = (ap.get('bssid') or '').lower()
            channel = ap.get('channel', '0')
            band = self.channel_to_band(channel)
            oui = ':'.join(bssid.split(':')[:3]) if bssid and ':' in bssid else None

            # Base points
            points = self.points_map.get(enc, 1)

            # Streak bonus
            self.streak += 1
            streak_threshold = 5
            streak_bonus = 1.2
            if self.streak >= streak_threshold:
                points = int(points * streak_bonus)
                agent.view().set('status', f"Streak bonus! +{int((streak_bonus - 1) * 100)}% points")

            # Random event multiplier
            if self.active_event and self.event_handshakes_left > 0:
                points = int(points * self.event_multiplier)
                self.event_handshakes_left -= 1
                if self.event_handshakes_left == 0:
                    self.active_event = None
                    self.event_multiplier = 1.0

            # Apply points & core bookkeeping
            self.network_points += int(points)
            self.handshake_count += 1
            self.last_active_epoch = self.epochs
            self.last_handshake_enc = enc
            self.personality_points['aggro'] += 1

            # Secret achievements
            current_hour = time.localtime().tm_hour
            if 2 <= current_hour < 4:
                self.night_owl_handshakes += 1
                if self.night_owl_handshakes == 10:
                    agent.view().set('status', "Achievement Unlocked: Night Owl!")
                    self.network_points += 50

            self.enc_types_captured.add(enc)
            if self.enc_types_captured == set(self.points_map.keys()):
                agent.view().set('status', "Achievement Unlocked: Crypto King!")
                self.network_points += 100

            # Traveler XP (novelty & places)
            if self.enable_travel:
                gained = 0
                try:
                    if essid not in self.unique_essids:
                        self.unique_essids.add(essid)
                        gained += 5
                    if bssid and bssid not in self.unique_bssids:
                        self.unique_bssids.add(bssid)
                        gained += 2
                    if oui and oui not in self.unique_ouis:
                        self.unique_ouis.add(oui)
                        gained += 3
                    if channel and channel not in self.unique_channels:
                        self.unique_channels.add(channel)
                        gained += 1
                    if band and band not in self.unique_bands:
                        self.unique_bands.add(band)
                        gained += 3
                        agent.view().set('status', f"New band discovered: {band} GHz")

                    place = self.compute_place_hash(ap)
                    if place not in self.place_hashes:
                        self.place_hashes.add(place)
                        self.last_place_hash = place
                        gained += 10
                        agent.view().set('status', "New place discovered!")

                    if gained > 0:
                        self.add_travel_xp(gained)
                        logging.info(f"[Age] Traveler XP +{gained} (xp={self.travel_xp}, lvl={self.travel_level})")
                except Exception as e:
                    logging.error(f"[Age] Traveler XP error: {e}")

            # Log handshake
            try:
                with open(self.log_path, 'a') as f:
                    f.write(f"{time.time()},{essid},{enc},{points}\n")
            except Exception:
                pass

            logging.info(f"[Age] Handshake: {essid}, enc: {enc}, points: {points}, streak: {self.streak}")
            self.save_data()

        except Exception as e:
            logging.error(f"[Age] Handshake error: {str(e)}")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load_data(self):
        """Load saved data from JSON file."""
        try:
            if os.path.exists(self.data_path):
                with open(self.data_path, 'r') as f:
                    data = json.load(f)
                    self.epochs = data.get('epochs', 0)
                    self.train_epochs = data.get('train_epochs', 0)
                    self.network_points = data.get('points', 0)
                    self.handshake_count = data.get('handshakes', 0)
                    self.last_active_epoch = data.get('last_active', 0)
                    self.prev_age_title = data.get('prev_age', self.get_age_title())
                    self.prev_strength_title = data.get('prev_strength', self.get_strength_title())
                    self.streak = data.get('streak', 0)
                    self.night_owl_handshakes = data.get('night_owl_handshakes', 0)
                    self.enc_types_captured = set(data.get('enc_types_captured', []))
                    for trait in ['aggro', 'stealth', 'scholar']:
                        self.personality_points[trait] = data.get(f'personality_{trait}', 0)

                    # Traveler
                    self.travel_xp = data.get('travel_xp', 0)
                    self.travel_level = data.get('travel_level', 0)
                    self.unique_essids = set(data.get('unique_essids', []))
                    self.unique_bssids = set(data.get('unique_bssids', []))
                    self.unique_ouis = set(data.get('unique_ouis', []))
                    self.unique_channels = set(data.get('unique_channels', []))
                    self.unique_bands = set(data.get('unique_bands', []))
                    self.place_hashes = set(data.get('place_hashes', []))
                    self.last_place_hash = data.get('last_place_hash', None)
        except Exception as e:
            logging.error(f"[Age] Load error: {str(e)}")

    def save_data(self):
        """Save current data to JSON file with thread safety."""
        data = {
            'epochs': self.epochs,
            'train_epochs': self.train_epochs,
            'points': self.network_points,
            'handshakes': self.handshake_count,
            'last_active': self.last_active_epoch,
            'prev_age': self.get_age_title(),
            'prev_strength': self.get_strength_title(),
            'streak': self.streak,
            'night_owl_handshakes': self.night_owl_handshakes,
            'enc_types_captured': list(self.enc_types_captured),
            'personality_aggro': self.personality_points['aggro'],
            'personality_stealth': self.personality_points['stealth'],
            'personality_scholar': self.personality_points['scholar'],

            # Traveler
            'travel_xp': self.travel_xp,
            'travel_level': self.travel_level,
            'unique_essids': list(self.unique_essids),
            'unique_bssids': list(self.unique_bssids),
            'unique_ouis': list(self.unique_ouis),
            'unique_channels': list(self.unique_channels),
            'unique_bands': list(self.unique_bands),
            'place_hashes': list(self.place_hashes),
            'last_place_hash': self.last_place_hash,
        }
        with self.data_lock:
            try:
                with open(self.data_path, 'w') as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logging.error(f"[Age] Save error: {str(e)}")

    # ------------------------------------------------------------------
    # Traveler helpers
    # ------------------------------------------------------------------
    def channel_to_band(self, channel):
        try:
            ch = int(channel)
        except Exception:
            return 'unk'
        if 1 <= ch <= 14:
            return '2.4'
        # Common 5 GHz range (not exhaustive; adequate for gamification)
        if (32 <= ch <= 173) or ch in (36, 40, 44, 48, 149, 153, 157, 161, 165):
            return '5'
        # Rough 6 GHz: some stacks map 6G channels starting ~191
        if 1 <= ch - 191 <= 59:
            return '6'
        return 'unk'

    def try_read_gps(self):
        """Try to read a simple JSON with lat/lon from common paths. Return (lat, lon) or None."""
        for p in self.gps_candidate_paths:
            try:
                if os.path.exists(p):
                    with open(p, 'r') as f:
                        j = json.load(f)
                        lat = j.get('lat', None)
                        lon = j.get('lon', None)
                        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                            return (lat, lon)
            except Exception:
                continue
        return None

    def quantize_ll(self, lat, lon):
        g = self.travel_grid
        # round to nearest grid point to avoid leaking precise location
        qlat = round(lat / g) * g
        qlon = round(lon / g) * g
        return f"{qlat:.4f}:{qlon:.4f}"

    def compute_place_hash(self, ap):
        """
        Prefer GPS (grid). Fallback: OUI + band + channel to create a coarse
        fingerprint so travel can still advance without GPS.
        """
        gps = self.try_read_gps()
        if gps is not None:
            return self.quantize_ll(gps[0], gps[1])

        bssid = (ap.get('bssid') or '').lower()
        oui = ':'.join(bssid.split(':')[:3]) if bssid and ':' in bssid else 'no:ou:i'
        ch = ap.get('channel', '0')
        band = self.channel_to_band(ch)
        return f"{oui}-{band}-{ch}"

    def get_travel_title(self):
        thresholds = sorted(self.travel_titles.keys(), reverse=True)
        for t in thresholds:
            if self.travel_xp >= t:
                return self.travel_titles[t]
        return self.travel_titles.get(0, "Homebody")

    def next_travel_threshold(self):
        thresholds = sorted(self.travel_titles.keys())
        for t in thresholds:
            if self.travel_xp < t:
                return t
        return None

    def bump_travel_level(self):
        lvl = 0
        for t in sorted(self.travel_titles.keys()):
            if self.travel_xp >= t:
                lvl += 1
        self.travel_level = max(0, lvl - 1)

    def add_travel_xp(self, xp):
        if xp <= 0:
            return
        self.travel_xp += int(xp)
        old_level = self.travel_level
        self.bump_travel_level()
        if self.travel_level > old_level:
            try:
                ptitle = self.get_travel_title()
                logging.info(f"[Age] Traveler level up: {ptitle} (L{self.travel_level})")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------
    def get_dominant_personality(self):
        if not any(self.personality_points.values()):
            return "Neutral"
        dominant = max(self.personality_points, key=self.personality_points.get)
        return dominant.capitalize()

    def abrev_number(self, num):
        for unit in ['', 'K', 'M', 'B']:
            if abs(num) < 1000:
                try:
                    return f"{num:.1f}{unit}".rstrip('.0')
                except Exception:
                    return f"{num}{unit}"
            num /= 1000.0
        return f"{num:.1f}T"  # trillions
