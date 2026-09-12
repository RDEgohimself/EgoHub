import os
import secrets
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

# CONFIGURATION (Loaded securely from Environment Variables)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://nmkccdbkvenifujfhyxi.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

# Validate environment variables before initializing client
if not SUPABASE_KEY:
    raise ValueError("CRITICAL ERROR: SUPABASE_KEY environment variable is missing in Render settings.")

# Initialize Supabase Client (Service Role Key bypasses RLS securely on backend)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============ AUTO-UPDATE CONFIG ============
VERSION_FILE = "current_version.txt"

def get_current_version():
    """Read version from file, default to 1.0.0 if missing."""
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return "1.0.0"


@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "ONLINE", "message": "EgoHub Licensing Server is running."}), 200


# ============ AUTO-UPDATE ENDPOINTS ============

@app.route('/version.json', methods=['GET'])
def version_json():
    """Auto-update: tells the client what the latest version is."""
    version = get_current_version()
    return jsonify({
        "version": version,
        "url": "https://github.com/santhemanunderthefan-gif/EgoHub/releases/latest/download/EGOHUB.exe",
        "notes": "Auto-update for EGOHUB"
    }), 200
    
# ============ OFFSETS ENDPOINTS ============

@app.route('/offsets.json', methods=['GET'])
def offsets_json():
    """Serves the latest Roblox offsets for EGOHUB clients."""
    try:
        response = (
            supabase.table('offsets')
            .select('version, data')
            .order('created_at', desc=True)
            .limit(1)
            .execute()
        )
        if not response.data:
            return jsonify({"status": "ERROR", "message": "No offsets available"}), 404

        record = response.data[0]
        data = record['data']
        if 'Roblox Version' not in data:
            data['Roblox Version'] = record['version']
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": f"Failed: {str(e)}"}), 500


@app.route('/api/upload_offsets', methods=['POST'])
def upload_offsets():
    """Admin-only: upload new offsets after running the dumper."""
    data = request.get_json() or {}
    if data.get('admin_token') != ADMIN_SECRET:
        return jsonify({"status": "UNAUTHORIZED"}), 401

    offsets_data = data.get('data')
    version = data.get('version', '').strip()

    if not offsets_data or not version:
        return jsonify({"status": "ERROR", "message": "Missing data or version"}), 400

    try:
        supabase.table('offsets').insert({
            'version': version,
            'data': offsets_data
        }).execute()
        return jsonify({"status": "SUCCESS", "version": version}), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 500

# ============ LICENSE ENDPOINTS ============

@app.route('/api/verify', methods=['GET', 'POST'])
def verify_key():
    """Endpoint called by user application to check key, validity, and lock HWID."""
    if request.method == 'GET':
        return jsonify({"status": "ONLINE", "message": "Send a POST request with key and hwid to verify."}), 200

    data = request.get_json() or {}
    key_code = data.get('key', '').strip()
    client_hwid = data.get('hwid', '').strip()

    if not key_code or not client_hwid:
        return jsonify({"status": "ERROR", "message": "Missing key or HWID."}), 400

    # Query key from Supabase table
    try:
        response = supabase.table('keys').select('*').eq('key_code', key_code).execute()
        records = response.data
    except Exception as e:
        return jsonify({"status": "ERROR", "message": f"Database query failed: {str(e)}"}), 500

    if not records:
        return jsonify({"status": "INVALID", "message": "License key does not exist."}), 404

    key_record = records[0]
    now = datetime.now(timezone.utc)
    days_valid = key_record.get('days_valid')

    # Case 1: First-time activation
    if key_record['status'] == 'UNUSED':
        supabase.table('keys').update({
            'hwid': client_hwid,
            'status': 'ACTIVATED',
            'activated_at': now.isoformat()
        }).eq('key_code', key_code).execute()

        duration_msg = "Lifetime access" if not days_valid or days_valid == 0 else f"{days_valid} days"
        return jsonify({
            "status": "SUCCESS",
            "message": f"Key activated! Valid for: {duration_msg}."
        }), 200

    # Case 2: Key already activated — check HWID and expiration logic
    if key_record['status'] == 'ACTIVATED':
        if key_record['hwid'] != client_hwid:
            return jsonify({
                "status": "DENIED",
                "message": "Key is locked to another computer!"
            }), 403

        # Expiration Check (Only triggers if days_valid > 0)
        if days_valid and days_valid > 0 and key_record.get('activated_at'):
            activated_at = datetime.fromisoformat(key_record['activated_at'].replace('Z', '+00:00'))
            expiration_date = activated_at + timedelta(days=days_valid)

            if now > expiration_date:
                # Mark key as EXPIRED in Supabase
                supabase.table('keys').update({'status': 'EXPIRED'}).eq('key_code', key_code).execute()
                return jsonify({
                    "status": "EXPIRED",
                    "message": "License key has expired."
                }), 403

        return jsonify({
            "status": "SUCCESS",
            "message": "License valid."
        }), 200

    return jsonify({"status": "DENIED", "message": f"Key status: {key_record['status']}."}), 403


@app.route('/api/generate', methods=['POST'])
def generate_keys():
    """Admin-only endpoint to generate new EgoHub license keys with customizable duration."""
    data = request.get_json() or {}
    admin_token = data.get('admin_token', '')
    count = data.get('count', 1)
    days = data.get('days', 0)  # Default: 0 (Lifetime)

    if not ADMIN_SECRET or admin_token != ADMIN_SECRET:
        return jsonify({"status": "UNAUTHORIZED", "message": "Invalid admin token."}), 401

    new_keys = []
    for _ in range(count):
        # Format: EgoHub-XXXX-XXXX-XXXX
        part1 = secrets.token_hex(2).upper()
        part2 = secrets.token_hex(2).upper()
        part3 = secrets.token_hex(2).upper()
        code = f"EgoHub-{part1}-{part2}-{part3}"
        new_keys.append({
            "key_code": code,
            "status": "UNUSED",
            "days_valid": days
        })

    try:
        supabase.table('keys').insert(new_keys).execute()
    except Exception as e:
        return jsonify({"status": "ERROR", "message": f"Failed to insert keys: {str(e)}"}), 500

    return jsonify({
        "status": "SUCCESS",
        "generated_count": count,
        "days_valid": days,
        "keys": [k["key_code"] for k in new_keys]
    }), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
