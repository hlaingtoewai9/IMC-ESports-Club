from flask import Flask, request, jsonify, session, send_file, redirect, url_for, make_response, send_from_directory, Response
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import random
import requests
import urllib.parse
import os
import pandas as pd
import io
from datetime import datetime
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)
app.secret_key = "super_secret_esports_key"
CORS(app, supports_credentials=True)

# --- GOOGLE OAUTH & API CONFIGURATIONS ---
app.config['GOOGLE_CLIENT_ID'] = 'YOUR_GOOGLE_CLIENT_ID'
app.config['GOOGLE_CLIENT_SECRET'] = 'YOUR_GOOGLE_CLIENT_SECRET'
RESEND_API_KEY = "re_your_api_key_here"

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mlbb_stats.csv')

# --- FILE UPLOAD CONFIGURATION ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- DATA PROCESSING PIPELINE (DIRECT STATS SCHEMA) ---
def load_clean_df():
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        sample_data = """RecordType,EntityName,TeamName,Matches,Wins,Losses,Kills,Deaths,Assists,Points
Player,Faker_Wannabe,T1_Esports,10,7,3,45,12,30,150
Player,JungleDiff,T1_Esports,10,7,3,60,15,40,200
Team,T1_Esports,None,10,7,3,0,0,0,350
Team,Blacklist_Intl,None,12,9,3,0,0,0,450"""
        with open(CSV_FILE, 'w') as f:
            f.write(sample_data)
    try:
        df = pd.read_csv(CSV_FILE)
        df.columns = df.columns.str.strip()
        for col in ['Matches', 'Wins', 'Losses', 'Kills', 'Deaths', 'Assists', 'Points']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
        return df
    except Exception:
        return None

# --- DATABASE ENGINE DISPATCH ---
def init_db():
    conn_users = sqlite3.connect('users.db')
    c_users = conn_users.cursor()
    c_users.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT UNIQUE,
                  password TEXT, google_id TEXT UNIQUE, otp TEXT, role TEXT DEFAULT 'user')''')
    try: c_users.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except sqlite3.OperationalError: pass
    try: c_users.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: c_users.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
                  
    c_users.execute("SELECT * FROM users WHERE username = 'superuser'")
    if not c_users.fetchone():
        hashed_pw = generate_password_hash("Thisisadmin123!@#")
        c_users.execute("INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, ?)", 
                  ("superuser", "superuser@gmail.com", hashed_pw, "superuser"))
    else:
        c_users.execute("UPDATE users SET role = 'superuser' WHERE username = 'superuser'")
    
    conn_users.commit()
    conn_users.close()

def init_attendance_db():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checkin_type TEXT,
                    entity_name TEXT,
                    associated_info TEXT,
                    role_or_time TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )''')
    conn.commit()
    conn.close()

init_db()
init_attendance_db()

# --- UTILITIES ---
@app.route('/')
def home(): return send_file('index.html')

@app.route('/scrims')
def scrims_page(): return send_file('scrim.html')

@app.route('/checkin')
def checkin_page(): return send_file('checkin.html')

@app.route('/<path:filename>')
def serve_static(filename):
    safe_filename = urllib.parse.unquote(filename)
    if safe_filename.endswith(('.png', '.jpg', '.jpeg', '.gif')):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, safe_filename)
        if os.path.exists(file_path): return send_file(file_path)
    return "File not found", 404

def send_otp_email(receiver_email, otp):
    headers = {'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'}
    payload = {"from": "MLBB Hub <onboarding@resend.dev>", "to": [receiver_email],
               "subject": "Your MLBB Hub Password Reset Code", "html": f"<h2>MLBB Hub</h2><p>Your OTP code: <strong>{otp}</strong></p>"}
    try:
        res = requests.post('https://api.resend.com/emails', headers=headers, json=payload)
        return (True, "") if res.status_code == 200 else (False, res.text)
    except Exception as e: return False, str(e)

# --- SCRIM CHECK-IN & ATTENDANCE SYSTEM ---
@app.route('/api/checkin', methods=['POST'])
def handle_checkin():
    data = request.json
    checkin_type = data.get('type')
    entity_name = data.get('name')
    associated_info = data.get('associated_info') 
    role_or_time = data.get('role_or_time') 
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO attendance (checkin_type, entity_name, associated_info, role_or_time) VALUES (?, ?, ?, ?)",
                  (checkin_type, entity_name, associated_info, role_or_time))
        conn.commit()
        return jsonify({"status": "success", "message": f"✅ {entity_name} successfully checked in!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/export_teams', methods=['GET'])
def export_teams():
    if session.get('role') not in ['superuser', 'admin', 'organizer']:
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    df = pd.read_sql_query("SELECT * FROM attendance WHERE checkin_type = 'Team' ORDER BY timestamp DESC", conn)
    conn.close()
    
    if not df.empty:
        df = df.drop(columns=['checkin_type'])
        df = df.rename(columns={
            'id': 'ID', 'entity_name': 'Team Name', 
            'associated_info': 'Captain IGN', 'role_or_time': 'Match Block', 'timestamp': 'Check-In Time'
        })
    
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=team_checkins.csv"}
    )

@app.route('/api/export_players', methods=['GET'])
def export_players():
    if session.get('role') not in ['superuser', 'admin', 'organizer']:
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    df = pd.read_sql_query("SELECT * FROM attendance WHERE checkin_type = 'Player' ORDER BY timestamp DESC", conn)
    conn.close()
    
    if not df.empty:
        df = df.drop(columns=['checkin_type'])
        df = df.rename(columns={
            'id': 'ID', 'entity_name': 'Player IGN', 
            'associated_info': 'Affiliated Team', 'role_or_time': 'Primary Role', 'timestamp': 'Check-In Time'
        })
    
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=player_checkins.csv"}
    )

@app.route('/api/scrim_checkins', methods=['GET'])
def get_scrim_checkins():
    conn = sqlite3.connect('attendance.db')
    conn.row_factory = sqlite3.Row  
    c = conn.cursor()
    
    try:
        c.execute("SELECT checkin_type, entity_name, associated_info, role_or_time, timestamp FROM attendance ORDER BY timestamp DESC")
        data = [dict(row) for row in c.fetchall()]
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

# --- ACCESS SECURITY SUBSYSTEM ---
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username, email, password = data.get('username', '').strip(), data.get('email', '').strip().lower(), data.get('password')
    if not email.endswith('@gmail.com'): return jsonify({"status": "error", "message": "You must use a valid @gmail.com address!"})
    hashed_pw = generate_password_hash(password)
    conn = sqlite3.connect('users.db')
    try:
        conn.cursor().execute("INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, 'user')", (username, email, hashed_pw))
        conn.commit()
        return jsonify({"status": "success", "message": "Account created!"})
    except sqlite3.IntegrityError: return jsonify({"status": "error", "message": "Username or Email already exists!"})
    finally: conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT password, role, id, avatar FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()
    
    if user and user[0] and check_password_hash(user[0], password):
        session['logged_in'] = True
        session['role'] = user[1] if user[1] else 'user'
        session['username'] = username
        session['user_id'] = user[2]
        session['avatar'] = user[3]
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid credentials!"})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success"})

@app.route('/api/session', methods=['GET'])
def check_session():
    if session.get('logged_in') and session.get('user_id'): 
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT role, username, avatar FROM users WHERE id = ?", (session.get('user_id'),))
        user = c.fetchone()
        conn.close()
        
        if user:
            session['role'] = user[0]
            session['username'] = user[1]
            session['avatar'] = user[2]
            
            return jsonify({
                "logged_in": True, 
                "role": user[0],
                "username": user[1],
                "avatar": user[2]
            })
            
    session.clear()
    return jsonify({"logged_in": False})

# --- PROFILE MANAGEMENT SUBSYSTEM ---
@app.route('/profile')
def profile_page():
    return send_file('profile.html')

@app.route('/api/profile', methods=['GET'])
def get_profile():
    if not session.get('logged_in') or not session.get('user_id'): 
        session.clear() 
        return jsonify({"status": "error", "message": "Stale session"}), 401
        
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT username, email, role, bio, avatar FROM users WHERE id = ?", (session.get('user_id'),))
    user = c.fetchone()
    conn.close()
    
    if user:
        return jsonify({"username": user[0], "email": user[1], "role": user[2], "bio": user[3] or "", "avatar": user[4] or ""})
    return jsonify({"status": "error"}), 404

@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.json
    new_username = data.get('username', '').strip()
    bio = data.get('bio', '').strip()
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    try:
        c.execute("UPDATE users SET username = ?, bio = ? WHERE id = ?", (new_username, bio, session.get('user_id')))
        conn.commit()
        session['username'] = new_username
        return jsonify({"status": "success", "message": "Profile updated!"})
    except sqlite3.IntegrityError:
        return jsonify({"status": "error", "message": "Username already taken."})
    finally:
        conn.close()

@app.route('/api/profile/password', methods=['POST'])
def update_profile_password():
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.json
    new_password = data.get('password')
    if not new_password: return jsonify({"status": "error", "message": "Password cannot be empty"})
    
    hashed_pw = generate_password_hash(new_password)
    conn = sqlite3.connect('users.db')
    conn.cursor().execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pw, session.get('user_id')))
    conn.commit(); conn.close()
    return jsonify({"status": "success", "message": "Password updated securely!"})

@app.route('/api/profile/avatar', methods=['POST'])
def update_avatar():
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    if 'avatar' not in request.files: return jsonify({"status": "error", "message": "No image uploaded"})
    
    file = request.files['avatar']
    if file.filename == '': return jsonify({"status": "error", "message": "No file selected"})
    
    if file:
        filename = secure_filename(f"user_{session.get('user_id')}_{file.filename}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        conn = sqlite3.connect('users.db')
        conn.cursor().execute("UPDATE users SET avatar = ? WHERE id = ?", (filename, session.get('user_id')))
        conn.commit(); conn.close()
        session['avatar'] = filename
        return jsonify({"status": "success", "message": "Avatar updated!", "avatar": filename})

# --- CORE LEADERBOARD SYSTEM ---
@app.route('/api/player_leaderboard', methods=['GET'])
def player_leaderboard():
    df = load_clean_df()
    if df is None or df.empty: return jsonify([])
    
    players = df[df['RecordType'] == 'Player'].copy()
    if players.empty: return jsonify([])
    
    def calc_kda(row):
        denom = row['Deaths'] if row['Deaths'] > 0 else 1
        val = (row['Kills'] + row['Assists']) / denom
        return f"{row['Kills']}/{row['Deaths']}/{row['Assists']} ({val:.1f})"

    players['kda'] = players.apply(calc_kda, axis=1)
    players['winrate'] = players.apply(lambda r: f"{(r['Wins']/r['Matches']*100):.1f}%" if r['Matches'] > 0 else "0%", axis=1)
    
    players = players.rename(columns={
        'EntityName': 'player_name', 'TeamName': 'team_name', 
        'Matches': 'matches_played', 'Points': 'points',
        'Wins': 'wins', 'Losses': 'losses', 'Kills': 'kills', 'Deaths': 'deaths', 'Assists': 'assists'
    })
    
    return players.sort_values(by='points', ascending=False).to_json(orient='records'), 200, {'Content-Type': 'application/json'}

@app.route('/api/team_leaderboard', methods=['GET'])
def team_leaderboard():
    df = load_clean_df()
    if df is None or df.empty: return jsonify([])
    
    teams = df[df['RecordType'] == 'Team'].copy()
    if teams.empty: return jsonify([])
    
    teams['winrate'] = teams.apply(lambda r: f"{(r['Wins']/r['Matches']*100):.1f}%" if r['Matches'] > 0 else "0%", axis=1)
    
    teams = teams.rename(columns={
        'EntityName': 'team_name', 'Matches': 'matches_played', 
        'Wins': 'wins', 'Losses': 'losses', 'Points': 'points'
    })
    
    return teams.sort_values(by='points', ascending=False).to_json(orient='records'), 200, {'Content-Type': 'application/json'}

def safe_int(val):
    if val == "" or val is None: return 0
    try: return int(val)
    except (ValueError, TypeError): return 0

# --- NEW DIRECT DATA CONTROL SUBSYSTEM (Superuser/Admin/Organizer Only) ---
@app.route('/api/add_player', methods=['POST'])
def add_player():
    if session.get('role') not in ['superuser', 'admin', 'organizer']: 
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    data = request.json
    name = data.get('name', '').strip()
    if not name: return jsonify({"status": "error", "message": "Player name is required!"})

    df = load_clean_df()
    new_row = {
        "RecordType": "Player", "EntityName": name, "TeamName": data.get('team', '').strip(),
        "Matches": safe_int(data.get('matches')), "Wins": safe_int(data.get('wins')),
        "Losses": safe_int(data.get('losses')), "Kills": safe_int(data.get('kills')),
        "Deaths": safe_int(data.get('deaths')), "Assists": safe_int(data.get('assists')),
        "Points": safe_int(data.get('points'))
    }
    
    df = df[~((df['RecordType'] == 'Player') & (df['EntityName'].str.lower() == new_row['EntityName'].lower()))]
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_csv(CSV_FILE, index=False)
    return jsonify({"status": "success", "message": "Player stats added/updated!"})


@app.route('/api/add_team', methods=['POST'])
def add_team():
    if session.get('role') not in ['superuser', 'admin', 'organizer']: 
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    data = request.json
    name = data.get('name', '').strip()
    if not name: return jsonify({"status": "error", "message": "Team name is required!"})

    df = load_clean_df()
    new_row = {
        "RecordType": "Team", "EntityName": name, "TeamName": "None",
        "Matches": safe_int(data.get('matches')), "Wins": safe_int(data.get('wins')),
        "Losses": safe_int(data.get('losses')), "Kills": 0, "Deaths": 0, "Assists": 0,
        "Points": safe_int(data.get('points'))
    }
    
    df = df[~((df['RecordType'] == 'Team') & (df['EntityName'].str.lower() == new_row['EntityName'].lower()))]
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_csv(CSV_FILE, index=False)
    return jsonify({"status": "success", "message": "Team stats added/updated!"})


@app.route('/api/modify_player', methods=['POST'])
def modify_player():
    if session.get('role') not in ['superuser', 'admin', 'organizer']: 
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    data = request.json
    df = load_clean_df()
    target = data.get('target_name', '').strip()
    idx = df[(df['RecordType'] == 'Player') & (df['EntityName'].str.lower().str.strip() == target.lower())].index
    
    if not idx.empty:
        if data.get('team'): df.loc[idx, 'TeamName'] = data.get('team').strip()
        for field, col in [('matches', 'Matches'), ('wins', 'Wins'), ('losses', 'Losses'), 
                           ('kills', 'Kills'), ('deaths', 'Deaths'), ('assists', 'Assists'), ('points', 'Points')]:
            val = data.get(field)
            if val != "" and val is not None: df.loc[idx, col] = safe_int(val)
        
        df.to_csv(CSV_FILE, index=False)
        return jsonify({"status": "success", "message": f"Successfully updated stats for {target}."})
    return jsonify({"status": "error", "message": "Player not found. Check spelling."}), 404


@app.route('/api/modify_team', methods=['POST'])
def modify_team():
    if session.get('role') not in ['superuser', 'admin', 'organizer']: 
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    data = request.json
    df = load_clean_df()
    target = data.get('target_name', '').strip()
    idx = df[(df['RecordType'] == 'Team') & (df['EntityName'].str.lower().str.strip() == target.lower())].index
    
    if not idx.empty:
        for field, col in [('matches', 'Matches'), ('wins', 'Wins'), ('losses', 'Losses'), ('points', 'Points')]:
            val = data.get(field)
            if val != "" and val is not None: df.loc[idx, col] = safe_int(val)
        
        df.to_csv(CSV_FILE, index=False)
        return jsonify({"status": "success", "message": f"Successfully updated stats for {target}."})
    return jsonify({"status": "error", "message": "Team not found. Check spelling."}), 404

@app.route('/api/delete_player', methods=['POST'])
def delete_player():
    if session.get('role') not in ['superuser', 'admin', 'organizer']: return jsonify({"status": "error"}), 403
    player_name = request.json.get('player_name', '').strip()
    df = load_clean_df()
    df = df[~((df['RecordType'] == 'Player') & (df['EntityName'].str.lower() == player_name.lower()))]
    df.to_csv(CSV_FILE, index=False)
    return jsonify({"status": "success", "message": f"Purged player records."})

@app.route('/api/delete_team', methods=['POST'])
def delete_team():
    if session.get('role') not in ['superuser', 'admin', 'organizer']: return jsonify({"status": "error"}), 403
    team_name = request.json.get('team_name', '').strip()
    df = load_clean_df()
    df = df[~((df['RecordType'] == 'Team') & (df['EntityName'].str.lower() == team_name.lower()))]
    df.to_csv(CSV_FILE, index=False)
    return jsonify({"status": "success", "message": f"Purged team records."})

# --- USER ROLES & HIERARCHICAL ADMINISTRATIVE SERVICES ---
@app.route('/api/users', methods=['GET'])
def get_users():
    current_role = session.get('role')
    if current_role not in ['superuser', 'admin']: 
        return jsonify({"status": "error"}), 403
        
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    if current_role == 'superuser':
        c.execute("SELECT id, username, email, role FROM users WHERE username != 'superuser'")
    else:
        c.execute("SELECT id, username, email, role FROM users WHERE role IN ('user', 'player', 'organizer')")
        
    users = [{"id": r[0], "username": r[1], "email": r[2], "role": r[3]} for r in c.fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/api/update_role', methods=['POST'])
def update_role():
    current_role = session.get('role')
    if current_role not in ['superuser', 'admin']: 
        return jsonify({"status": "error"}), 403
        
    data = request.json
    target_id = data.get('id')
    new_role = data.get('role')
    
    if current_role == 'admin' and new_role not in ['user', 'player', 'organizer']:
        return jsonify({"status": "error", "message": "Admins cannot assign superuser or admin roles"}), 403
        
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute("SELECT role FROM users WHERE id = ?", (target_id,))
    target_user = c.fetchone()
    if not target_user:
        return jsonify({"status": "error", "message": "User not found"}), 404
        
    if current_role == 'admin' and target_user[0] in ['superuser', 'admin']:
        return jsonify({"status": "error", "message": "Insufficient permissions to modify this user"}), 403
        
    c.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, target_id))
    conn.commit(); conn.close()
    return jsonify({"status": "success"})

@app.route('/api/delete_user', methods=['POST'])
def delete_user():
    current_role = session.get('role')
    if current_role not in ['superuser', 'admin']: 
        return jsonify({"status": "error"}), 403
        
    user_id = request.json.get('id')
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute("SELECT role, username FROM users WHERE id = ?", (user_id,))
    target_user = c.fetchone()
    
    if not target_user: return jsonify({"status": "error", "message": "User not found"}), 404
    if target_user[1] == 'superuser': return jsonify({"status": "error", "message": "Cannot delete master superuser"}), 403
    if current_role == 'admin' and target_user[0] in ['superuser', 'admin']:
        return jsonify({"status": "error", "message": "Insufficient permissions to delete this user"}), 403

    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit(); conn.close()
    return jsonify({"status": "success"})

@app.route('/api/admin_reset_password', methods=['POST'])
def admin_reset_password():
    current_role = session.get('role')
    if current_role not in ['superuser', 'admin']: 
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    data = request.json
    user_id = data.get('id')
    new_password = data.get('new_password')
    
    if not user_id or not new_password:
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
        
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT role, username FROM users WHERE id = ?", (user_id,))
    target_user = c.fetchone()
    
    if not target_user: return jsonify({"status": "error", "message": "User not found"}), 404
    if target_user[1] == 'superuser': return jsonify({"status": "error", "message": "Cannot modify master superuser"}), 403
    if current_role == 'admin' and target_user[0] in ['superuser', 'admin']:
        return jsonify({"status": "error", "message": "Insufficient permissions to modify this user"}), 403

    hashed_pw = generate_password_hash(new_password)
    c.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pw, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success", "message": "Password reset successfully!"})

# --- OTP/OAUTH ---
@app.route('/api/send_otp', methods=['POST'])
def send_otp():
    email = request.json.get('email').strip().lower()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email = ?", (email,))
    if c.fetchone():
        otp = str(random.randint(100000, 999999))
        c.execute("UPDATE users SET otp = ? WHERE email = ?", (otp, email))
        conn.commit()
        success, _ = send_otp_email(email, otp)
        if not success: print(f"[DEV BYPASS] OTP: {otp}")
        conn.close()
        return jsonify({"status": "success", "message": "OTP Dispatched."})
    conn.close()
    return jsonify({"status": "error", "message": "Identity Not Found."})

@app.route('/api/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT otp FROM users WHERE email = ?", (data.get('email').lower().strip(),))
    if c.fetchone()[0] == data.get('otp').strip():
        c.execute("UPDATE users SET password = ?, otp = NULL WHERE email = ?", (generate_password_hash(data.get('new_password')), data.get('email').lower().strip()))
        conn.commit(); conn.close()
        return jsonify({"status": "success"})
    conn.close()
    return jsonify({"status": "error"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)