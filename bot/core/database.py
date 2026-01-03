#######################################################
# Database management for the bot
#######################################################
import datetime
import random
import sqlite3
from contextlib import closing
from typing import Optional, List, Dict

# Set up a database to be used for the economy system
class EconomyDatabase:
    def __init__(self, db_path='data/economy.db'):
        self.db_path = db_path
        self._initialize_database()

    def _now_iso(self) -> str:
        """Return current time as ISO formatted string."""
        return datetime.datetime.now().isoformat()

    def _initialize_database(self):
        """Initializes the database and creates the users table if it doesn't exist."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        balance INTEGER DEFAULT 0,
                        last_daily_claim DATETIME,
                        last_work_claim DATETIME
                    )
                ''')
                conn.commit()

    def get_balance(self, user_id: int) -> int:
        """Retrieves the balance of a user by their user ID.
        Parameters:
            user_id (int): The ID of the user whose balance is to be retrieved.
        Returns:
            int: The balance of the user. Returns 0 if the user does not exist.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                return row[0] if row else 0

    def update_balance(self, user_id: int, amount: int) -> None:
        """Updates the balance of a user by adding a specified amount.
        Subtracts if the amount is negative.
        Parameters:
            user_id (int): The ID of the user whose balance is to be updated.
            amount (int): The amount to add to the user's balance. Can be negative to deduct.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
                cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
                conn.commit()

    def try_daily(self, user_id: int) -> bool:
        """Attempts to claim the daily reward for a user.
        Parameters:
            user_id (int): The ID of the user claiming the daily reward.
        Returns:
            bool: True if the daily reward was successfully claimed, False if already claimed today.
        """
        now_iso = self._now_iso()
        today_str = datetime.datetime.now().date().isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
                cursor.execute('SELECT last_daily_claim FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                last_claim = row[0] if row else None
                if last_claim:
                    # last_claim stored as ISO string; compare dates robustly
                    try:
                        last_dt = datetime.datetime.fromisoformat(last_claim)
                        if last_dt.date().isoformat() == today_str:
                            return False  # Already claimed today
                    except Exception:
                        # If parsing fails, fall back to string prefix check
                        if str(last_claim).startswith(today_str):
                            return False
                cursor.execute('UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE user_id = ?', (10, now_iso, user_id))
                conn.commit()
                return True

    def try_work(self, user_id: int) -> tuple[bool, int]:
        """Attempts to claim the work reward for a user.
        Parameters:
            user_id (int): The ID of the user claiming the work reward.
        Returns:
            (bool, int): A tuple where the first element is True if the work reward was successfully claimed,
                         False if already claimed within the last 2 hours, and the second element is the amount claimed.
        """
        now = datetime.datetime.now()
        now_iso = now.isoformat()
        two_hours_ago = now - datetime.timedelta(hours=2)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
                cursor.execute('SELECT last_work_claim FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                last_claim = row[0] if row else None
                if last_claim:
                    try:
                        last_claim_time = datetime.datetime.fromisoformat(last_claim)
                    except Exception:
                        try:
                            last_claim_time = datetime.datetime.fromtimestamp(float(last_claim))
                        except Exception:
                            last_claim_time = None
                    if last_claim_time and last_claim_time > two_hours_ago:
                        return False, 0  # Already claimed within the last 2 hours
                amount = random.randint(1, 5)
                # Store timestamp as ISO string for consistency
                cursor.execute('UPDATE users SET balance = balance + ?, last_work_claim = ? WHERE user_id = ?', (amount, now_iso, user_id))
                conn.commit()
                return True, amount

    def get_leaderboard(self, page: int, page_size: int = 10) -> List[tuple[int, int]]:
        """Retrieves a paginated leaderboard of users by balance.
        Parameters:
            page (int): The page number to retrieve (1-based).
            page_size (int): The number of users per page.
        Returns:
            List[tuple[int, int]]: A list of tuples containing user_id and balance.
        """
        offset = (page - 1) * page_size
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    SELECT user_id, balance FROM users
                    ORDER BY balance DESC
                    LIMIT ? OFFSET ?
                ''', (page_size, offset))
                rows = cursor.fetchall()
                return [(row[0], row[1]) for row in rows]

    def reset_balance(self, user_id: int) -> None:
        """Resets the balance of a user to zero."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
                cursor.execute('UPDATE users SET balance = 0 WHERE user_id = ?', (user_id,))
                conn.commit()

    def delete_user(self, user_id: int) -> None:
        """Deletes a user from the database."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
                conn.commit()


class ModerationDatabase:
    def __init__(self, db_path='data/moderation.db'):
        self.db_path = db_path
        self._initialize_database()

    def _initialize_database(self):
        """Initializes the database and creates the moderation_logs table if it doesn't exist."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                conn.commit()
                # Table for storing timed mutes so they survive restarts
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS mute_timers (
                    timer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    unmute_at DATETIME NOT NULL,
                    reason TEXT,
                    muted_by INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                conn.commit()

                # Table for storing staff strikes
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS staff_strikes (
                    strike_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    staff_id INTEGER NOT NULL,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                conn.commit()

    def add_warning(self, user_id: int, reason: str) -> None:
        """Adds a warning for a user.
        Parameters:
            user_id (int): The ID of the user being warned.
            reason (str): The reason for the warning.
        """
        timestamp_iso = datetime.datetime.now().isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO warnings (user_id, reason, timestamp)
                    VALUES (?, ?, ?)
                ''', (user_id, reason, timestamp_iso))
                conn.commit()

    # --- Timed mute persistence helpers ---
    def add_mute_timer(self, user_id: int, guild_id: int, unmute_at: str, reason: str | None = None, muted_by: int | None = None) -> int:
        """Adds a timed mute to the database.
        Parameters:
            user_id: ID of the muted user
            guild_id: ID of the guild
            unmute_at: ISO datetime string when the mute should be lifted
            reason: optional reason
            muted_by: user ID who performed the mute
        Returns: the timer_id inserted
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO mute_timers (user_id, guild_id, unmute_at, reason, muted_by)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, guild_id, unmute_at, reason, muted_by))
                conn.commit()
                return cursor.lastrowid

    def remove_mute_timer(self, user_id: int, guild_id: int) -> None:
        """Removes any mute timer for a given user in a guild."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM mute_timers WHERE user_id = ? AND guild_id = ?', (user_id, guild_id))
                conn.commit()

    def get_pending_mutes(self) -> list:
        """Returns a list of pending mute timers across guilds as dicts with keys:
           timer_id, user_id, guild_id, unmute_at, reason, muted_by, created_at
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT timer_id, user_id, guild_id, unmute_at, reason, muted_by, created_at FROM mute_timers')
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    result.append({
                        'timer_id': row[0],
                        'user_id': row[1],
                        'guild_id': row[2],
                        'unmute_at': row[3],
                        'reason': row[4],
                        'muted_by': row[5],
                        'created_at': row[6]
                    })
                return result

    def add_strike(self, staff_id: int, reason: str) -> None:
        """Adds a staff strike for a staff member.
        Parameters:
            staff_id (int): The ID of the staff member receiving the strike.
            reason (str): The reason for the strike.
        """
        timestamp_iso = datetime.datetime.now().isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO staff_strikes (staff_id, reason, timestamp)
                    VALUES (?, ?, ?)
                ''', (staff_id, reason, timestamp_iso))
                conn.commit()

    def get_strikes(self, staff_id: int) -> List[Dict]:
        """Retrieves all strikes for a given staff member.
        Parameters:
            staff_id (int): The ID of the staff member.
        Returns:
        List[Dict]: A list of strikes, each represented as a dictionary.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT strike_id, reason, timestamp FROM staff_strikes WHERE staff_id = ?', (staff_id,))
                rows = cursor.fetchall()
                strikes = []
                for row in rows:
                    strikes.append({
                        'strike_id': row[0],
                        'reason': row[1],
                        'timestamp': row[2]
                    })
                return strikes


class ApplicationsDatabase:
    def __init__(self, db_path='data/applications.db'):
        self.db_path = db_path
        self._initialize_database()

    def _now_iso(self) -> str:
        return datetime.datetime.now().isoformat()

    def _parse_datetime(self, value) -> Optional[datetime.datetime]:
        """Attempt to parse common datetime representations used in the DB.
        Returns a datetime on success or None on failure.
        """
        if value is None:
            return None
        # If already a datetime object
        if isinstance(value, datetime.datetime):
            return value
        # If numeric (timestamp)
        try:
            if isinstance(value, (int, float)):
                return datetime.datetime.fromtimestamp(value)
        except Exception:
            pass
        # If it's a string, try ISO then common formats
        try:
            if isinstance(value, str):
                return datetime.datetime.fromisoformat(value)
        except Exception:
            pass
        try:
            if isinstance(value, str):
                return datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
        # Fall back to None if unparseable
        return None

    def _position_from_row(self, row) -> Dict:
        """Convert a positions table row into a dict with parsed types."""
        # row layout: position_id, name, description, roles_given, questions, acceptance_message, rejection_message, open
        roles_raw = row[3] or ''
        # split and filter empty strings
        roles_given = [int(r) for r in (x for x in (roles_raw.split(',') if roles_raw else [])) if r and r.strip()]
        questions_raw = row[4] or ''
        questions = [q for q in questions_raw.split('\n') if q is not None and q != ''] if questions_raw else []
        return {
            'position_id': row[0],
            'name': row[1],
            'description': row[2],
            'roles_given': roles_given,
            'questions': questions,
            'acceptance_message': row[5],
            'rejection_message': row[6],
            'open': bool(row[7])
        }

    def _initialize_database(self):
        """Initializes the database and creates the applications table if it doesn't exist.
        position structure: {'name': str, 'description': str, 'roles_given': list[int], 'questions': list[str], 'acceptance_message': str, 'rejection_message': str, 'open': bool}
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                # Create the positions table
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    roles_given TEXT,
                    questions TEXT,
                    acceptance_message TEXT,
                    rejection_message TEXT,
                    open BOOLEAN DEFAULT 1
                )
                ''')
                conn.commit()

                # Create the applications channel table
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS applications_channel (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER
                )
                ''')
                conn.commit()

                # Create the applications table
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS applications (
                    application_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    position_id INTEGER NOT NULL,
                    answers TEXT,
                    status TEXT DEFAULT 'pending',
                    submission_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (position_id) REFERENCES positions(position_id)
                )
                ''')
                conn.commit()

                # Create the application flags table (for auto-pinging staff when flagged users re-apply)
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS application_flags (
                    user_id INTEGER PRIMARY KEY,
                    flagged_by INTEGER,
                    reason TEXT,
                    flagged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    guild_id INTEGER
                )
                ''')
                conn.commit()

                # Create the blacklisted users table
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS application_blacklist (
                    user_id INTEGER PRIMARY KEY,
                    blacklisted_by INTEGER,
                    reason TEXT,
                    blacklisted_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                conn.commit()

    def set_applications_channel(self, guild_id: int, channel_id: int) -> None:
        """Sets the application submissions channel for a guild.
        Parameters:
            guild_id (int): The ID of the guild.
            channel_id (int): The ID of the channel to set for application submissions.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO applications_channel (guild_id, channel_id)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id
                ''', (guild_id, channel_id))
                conn.commit()

    def get_applications_channel(self, guild_id: int) -> int | None:
        """Retrieves the application submissions channel for a guild.
        Parameters:
            guild_id (int): The ID of the guild.
        Returns:
            int | None: The ID of the application submissions channel, or None if not set.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT channel_id FROM applications_channel WHERE guild_id = ?', (guild_id,))
                row = cursor.fetchone()
                return row[0] if row else None

    def add_position(self, name: str) -> int:
        """Adds a new position to the database, with default values to be modified later.
        Parameters:
            name (str): The name of the position to be added.
        Returns:
            int: The ID of the newly created position.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO positions (name, description, roles_given, questions, acceptance_message, rejection_message, open)
                    VALUES (?, '', '', '', '', '', 1)
                ''', (name,))
                conn.commit()
                return cursor.lastrowid

    def remove_position(self, position_id: int) -> None:
        """Removes a position from the database.
        Parameters:
            position_id (int): The ID of the position to be removed.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM positions WHERE position_id = ?', (position_id,))
                conn.commit()

    def get_positions(self) -> List[Dict]:
        """Retrieves all positions from the database.
        Returns:
            list: A list of positions, each represented as a dictionary.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT * FROM positions')
                rows = cursor.fetchall()
                return [self._position_from_row(row) for row in rows]

    def get_positions_by_name(self, name: str) -> List[Dict]:
        """Retrieves all positions with a specific name from the database.
        Parameters:
            name (str): The name of the positions to be retrieved.
        Returns:
            list: A list of positions with the specified name, each represented as a dictionary.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT * FROM positions WHERE name = ?', (name,))
                rows = cursor.fetchall()
                return [self._position_from_row(row) for row in rows]

    def get_position(self, position_id: int) -> dict | None:
        """Retrieves a specific position by its ID.
        Parameters:
            position_id (int): The ID of the position to be retrieved.
        Returns:
            dict | None: The position represented as a dictionary, or None if not found.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT * FROM positions WHERE position_id = ?', (position_id,))
                row = cursor.fetchone()
                if row:
                    return self._position_from_row(row)
                return None

    def set_position_open(self, position_id: int, open: bool) -> None:
        """Sets whether a position is open for applications.
        Parameters:
            position_id (int): The ID of the position to be updated.
            open (bool): Whether the position is open.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE positions SET open = ? WHERE position_id = ?', (int(open), position_id))
                conn.commit()

    def modify(self, position_id: int, attribute: str, value) -> None:
        """Modifies an attribute of a position.
        Parameters:
            position_id (int): The ID of the position to be modified.
            attribute (str): The attribute to be modified (description, roles_given, questions, acceptance_message, rejection_message).
            value: The new value for the attribute.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                if attribute == 'name':
                    cursor.execute('UPDATE positions SET name = ? WHERE position_id = ?', (value, position_id))
                elif attribute == 'description':
                    cursor.execute('UPDATE positions SET description = ? WHERE position_id = ?', (value, position_id))
                elif attribute == 'roles_given':
                    roles_str = ','.join(map(str, value))
                    cursor.execute('UPDATE positions SET roles_given = ? WHERE position_id = ?', (roles_str, position_id))
                elif attribute == 'questions':
                    questions_str = '\n'.join(value)
                    cursor.execute('UPDATE positions SET questions = ? WHERE position_id = ?', (questions_str, position_id))
                elif attribute == 'acceptance_message':
                    cursor.execute('UPDATE positions SET acceptance_message = ? WHERE position_id = ?', (value, position_id))
                elif attribute == 'rejection_message':
                    cursor.execute('UPDATE positions SET rejection_message = ? WHERE position_id = ?', (value, position_id))
                conn.commit()

    # --- New methods for DM-based application flow ---
    def start_application(self, user_id: int, position_id: int) -> int:
        """Create or reset an in-progress application for a user. Returns the application_id."""
        now_iso = self._now_iso()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                # Remove any existing in-progress application for this user
                cursor.execute("DELETE FROM applications WHERE user_id = ? AND status = 'in_progress'", (user_id,))
                cursor.execute('''
                    INSERT INTO applications (user_id, position_id, answers, status, submission_date)
                    VALUES (?, ?, ?, 'in_progress', ?)
                ''', (user_id, position_id, '', now_iso))
                conn.commit()
                return cursor.lastrowid

    def get_in_progress_application(self, user_id: int) -> dict | None:
        """Return the in-progress application row for a user, or None."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT application_id, position_id, answers, status, submission_date FROM applications WHERE user_id = ? AND status = 'in_progress' ORDER BY application_id DESC LIMIT 1", (user_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    'application_id': row[0],
                    'position_id': row[1],
                    'answers': row[2],
                    'status': row[3],
                    'submission_date': row[4]
                }

    def submit_application(self, user_id: int, answers: str) -> tuple:
        """Submit the user's in-progress application.
        Returns (True, application_id, position_id) on success, or (False, reason) on failure.
        If the in-progress application is older than 24 hours it will be removed and the submission fails.
        """
        now = datetime.datetime.now()
        now_iso = now.isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT application_id, position_id, submission_date FROM applications WHERE user_id = ? AND status = 'in_progress' ORDER BY application_id DESC LIMIT 1", (user_id,))
                row = cursor.fetchone()
                if not row:
                    return (False, 'no_in_progress')
                application_id, position_id, submission_date = row
                # submission_date is stored as text; try parsing common formats
                started = self._parse_datetime(submission_date) or now
                if now - started > datetime.timedelta(hours=24):
                    # expired - remove the in-progress application
                    cursor.execute('DELETE FROM applications WHERE application_id = ?', (application_id,))
                    conn.commit()
                    return (False, 'expired')
                # update with answers and mark submitted
                cursor.execute("UPDATE applications SET answers = ?, status = 'submitted', submission_date = ? WHERE application_id = ?", (answers, now_iso, application_id))
                conn.commit()
                return (True, application_id, position_id)

    def get_application(self, application_id: int) -> dict | None:
        """Retrieve a single application row by its ID."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT application_id, user_id, position_id, answers, status, submission_date FROM applications WHERE application_id = ?', (application_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    'application_id': row[0],
                    'user_id': row[1],
                    'position_id': row[2],
                    'answers': row[3],
                    'status': row[4],
                    'submission_date': row[5]
                }

    def get_latest_submitted_application(self, user_id: int) -> dict | None:
        """Return the most recent submitted application for a user (status = 'submitted')."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT application_id, user_id, position_id, answers, status, submission_date FROM applications WHERE user_id = ? AND status = 'submitted' ORDER BY application_id DESC LIMIT 1", (user_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    'application_id': row[0],
                    'user_id': row[1],
                    'position_id': row[2],
                    'answers': row[3],
                    'status': row[4],
                    'submission_date': row[5]
                }

    def get_applications_count(self) -> int:
        """Return the total number of application rows in the database."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT COUNT(*) FROM applications')
                row = cursor.fetchone()
                return int(row[0]) if row else 0

    def get_applications(self, limit: int, offset: int) -> list:
        """Fetch a page of applications ordered by newest first.

        Returns a list of dicts with the same shape as `get_application`.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    'SELECT application_id, user_id, position_id, answers, status, submission_date '
                    'FROM applications '
                    'ORDER BY application_id DESC '
                    'LIMIT ? OFFSET ?',
                    (limit, offset)
                )
                rows = cursor.fetchall()
                apps = []
                for row in rows:
                    apps.append({
                        'application_id': row[0],
                        'user_id': row[1],
                        'position_id': row[2],
                        'answers': row[3],
                        'status': row[4],
                        'submission_date': row[5]
                    })
                return apps

    def withdraw_application(self, application_id: int) -> bool:
        """Mark an application as withdrawn. Returns True if a row was updated."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT status FROM applications WHERE application_id = ?", (application_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                current_status = row[0]
                if current_status == 'withdrawn':
                    return False
                cursor.execute("UPDATE applications SET status = 'withdrawn' WHERE application_id = ?", (application_id,))
                conn.commit()
                return cursor.rowcount > 0

    def set_application_status(self, application_id: int, status: str) -> bool:
        """Set the status of an application (e.g., 'accepted', 'rejected'). Returns True if a row was updated."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT status FROM applications WHERE application_id = ?", (application_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                current_status = row[0]
                if current_status == status:
                    # No change needed
                    return False
                cursor.execute("UPDATE applications SET status = ? WHERE application_id = ?", (status, application_id))
                conn.commit()
                return cursor.rowcount > 0

    # --- New: flagging helpers for applications ---
    def flag_user(self, user_id: int, flagged_by: int, reason: str | None = None, guild_id: int | None = None) -> None:
        """Flag a user so staff will be auto-pinged when they apply again. If a flag exists it will be replaced/updated."""
        now_iso = self._now_iso()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO application_flags (user_id, flagged_by, reason, flagged_at, guild_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET flagged_by=excluded.flagged_by, reason=excluded.reason, flagged_at=excluded.flagged_at, guild_id=excluded.guild_id
                ''', (user_id, flagged_by, reason, now_iso, guild_id))
                conn.commit()

    def unflag_user(self, user_id: int) -> bool:
        """Remove a flag for a user. Returns True if a row was removed."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM application_flags WHERE user_id = ?', (user_id,))
                conn.commit()
                return cursor.rowcount > 0

    def is_user_flagged(self, user_id: int, guild_id: int | None = None) -> bool:
        """Return True if the user is flagged. If guild_id is provided, returns True for either a guild-scoped flag or a global (NULL) flag."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                if guild_id is None:
                    cursor.execute('SELECT 1 FROM application_flags WHERE user_id = ? LIMIT 1', (user_id,))
                else:
                    cursor.execute('SELECT 1 FROM application_flags WHERE user_id = ? AND (guild_id IS NULL OR guild_id = ?) LIMIT 1', (user_id, guild_id))
                row = cursor.fetchone()
                return bool(row)

    def get_flag(self, user_id: int) -> dict | None:
        """Return the flag row for a user, or None if not flagged."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT user_id, flagged_by, reason, flagged_at, guild_id FROM application_flags WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    'user_id': row[0],
                    'flagged_by': row[1],
                    'reason': row[2],
                    'flagged_at': row[3],
                    'guild_id': row[4]
                }

    def blacklist_user(self, user_id: int, blacklisted_by: int, reason: str | None = None) -> None:
        """Blacklist a user from applying. If already blacklisted, updates the entry."""
        now_iso = self._now_iso()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('''
                    INSERT INTO application_blacklist (user_id, blacklisted_by, reason, blacklisted_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET blacklisted_by=excluded.blacklisted_by, reason=excluded.reason, blacklisted_at=excluded.blacklisted_at
                ''', (user_id, blacklisted_by, reason, now_iso))
                conn.commit()

    def unblacklist_user(self, user_id: int) -> bool:
        """Remove a user from the blacklist. Returns True if a row was removed."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM application_blacklist WHERE user_id = ?', (user_id,))
                conn.commit()
                return cursor.rowcount > 0

    def is_user_blacklisted(self, user_id: int) -> bool:
        """Return True if the user is blacklisted."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT 1 FROM application_blacklist WHERE user_id = ? LIMIT 1', (user_id,))
                row = cursor.fetchone()
                return bool(row)
