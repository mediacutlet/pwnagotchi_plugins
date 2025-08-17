"""
Age Plugin (No Traveler)
========================
Version: 3.3.1
Author: AlienMajik (tweaked by MediaCutlet/Strato)
License: MIT

This makes the progress bar length configurable.

Compatibility & Notes
---------------------
• Existing /root/age_strength.json files from traveler builds are still readable;
  any old Traveler keys will simply be ignored and dropped on the next save.
• Remove any traveler-related options from /etc/pwnagotchi/config.toml, e.g.:
    main.plugins.age.enable_travel
    main.plugins.age.travel_grid
    main.plugins.age.traveler_x
    main.plugins.age.traveler_y
• Optional UI toggles and positions for Age/Strength/Points/Progress/Personality
  remain supported. Personality line is off by default to keep the UI clean.

Config keys (examples)
----------------------
main.plugins.age.enabled = true
main.plugins.age.decay_interval = 50
main.plugins.age.decay_amount = 5
main.plugins.age.show_personality = false
# Optional UI positions
main.plugins.age.age_x = 10
main.plugins.age.age_y = 40
main.plugins.age.strength_x = 80
main.plugins.age.strength_y = 40
main.plugins.age.points_x = 10
main.plugins.age.points_y = 60
main.plugins.age.progress_x = 10
main.plugins.age.progress_y = 80
main.plugins.age.personality_x = 10
main.plugins.age.personality_y = 100
# New: Progress bar length (cells inside the bar, default 5, clamped 1–20)
main.plugins.age.progress_bar_length = 5
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
    __author__ = 'AlienMajik, MediaCutlet (Strato)'
    __version__ = '3.3.0'
    __license__ = 'MIT'
    __description__ = (
        'Enhanced Age plugin with frequent titles, dynamic quotes, progress bars, '
        'random events, handshake streaks, personality evolution, and secret achievements. '
        'Traveler/Nomadachi logic has been removed to live in its own plugin.'
    )

    DEFAULT_AGE_TITLES = {
      100:     "Hatchling",
      200:     "Pingling",
      275:     "Bootsprout",
      350:     "Fledgling",
      450:     "Bitling",
      600:     "Orbitling",
      750:     "Qubitling",
      900:     "Pingpunk",
      1_050:   "Telemetry Tween",
      1_200:   "Cipher Teen",
      1_350:   "Beacon Teen",
      1_500:   "Protocol Teen",
      2_000:   "Emergent Adult",
      3_000:   "Young Adult",
      4_000:   "Mature",
      5_000:   "Seasoned",
      7_000:   "Elder",
      10_000:  "Encrypted Sage",
      15_000:  "Ancient",
      20_000:  "Celestial Ancestor",
      30_000:  "Eon Ancestor",
      40_000:  "Binary Venerable",
      55_000:  "Quantum Elder",
      80_000:  "Primordial",
      100_000: "Galactic Root",
      111_111: "Singularity"
    }

    DEFAULT_STRENGTH_TITLES = {
        100: "Neophyte",
        250: "Cyber Trainee",
        400: "Bitbreaker",
        600: "Packet Slinger",
        900: "Kernel Keeper",
        1_200: "Deauth Cadet",
        1_600: "Packeteer", 
        2_000: "Hash Hunter",
        2_500: "Signalist",
        3_200: "Ethernaut",
        4_500: "WiFi Marauder",
        6_000: "Neural Saboteur",
        8_000: "Astral Admiral",
        12_000: "Signal Master",
        18_000: "Quantum Brawler",
        30_000: "Rootwave Titan",
        55_555: "Void Breaker",
        111_111: "Omega Cipherlord"
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
        # Progress bar (UI)
        self.progress_bar_length = 5   # default number of cells inside the bar

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

        # Progress bar length (clamped to avoid UI collisions)
        try:
            pbl = int(self.options.get('progress_bar_length', self.progress_bar_length))
        except (TypeError, ValueError):
            pbl = self.progress_bar_length
        self.progress_bar_length = max(1, min(20, pbl))

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
            color=BLACK, label='Age', value=self.render_progress_bar(0.0),
            position=positions['progress'], label_font=fonts.Bold, text_font=fonts.Medium))

        if self.show_personality:
            ui.add_element('Personality', LabeledValue(
                color=BLACK, label='Trait ', value="Neutral",
                position=positions['personality'], label_font=fonts.Bold, text_font=fonts.Medium))

    def on_ui_update(self, ui):
        """Update UI elements with current values."""
        ui.set('Age', self.get_age_title())
        ui.set('Strength', self.get_strength_title())
        ui.set('Points', self.abrev_number(self.network_points))

        # Progress bar for next age title
        next_threshold = self.get_next_age_threshold()
        if next_threshold:
            progress = max(0.0, min(1.0, self.epochs / float(next_threshold)))
            ui.set('Progress', self.render_progress_bar(progress))
        else:
            ui.set('Progress', '[MAX]')

        if self.show_personality:
            ui.set('Personality', self.get_dominant_personality())

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
        """Handle handshake events with streaks and achievements."""
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
        }
        with self.data_lock:
            try:
                with open(self.data_path, 'w') as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logging.error(f"[Age] Save error: {str(e)}")

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

    def render_progress_bar(self, progress: float) -> str:
        """
        Build the Age progress bar using the configured length.
        progress is 0.0–1.0.
        """
        length = int(self.progress_bar_length)
        if length < 1:
            length = 1
        pct = max(0.0, min(1.0, float(progress)))
        filled = int(pct * length)
        return '|' + ('▥' * filled) + (' ' * (length - filled)) + '|'
