from datetime import date
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from urllib.parse import urlparse 

# --- Cloudinary Imports ---
import cloudinary
import cloudinary.uploader
import cloudinary.api

# --- Load Environment Variables ---
# This will load DATABASE_URL and CLOUDINARY_URL from your .env file
load_dotenv() 

# --- App & Configurations ---
app = Flask(__name__)
CORS(app) 

# --- NEW: Unpack the CLOUDINARY_URL and configure the library ---
cloudinary_url_string = os.getenv("CLOUDINARY_URL")
if cloudinary_url_string:
    print("Found CLOUDINARY_URL, configuring...")
    # Use urlparse to break the URL into its components
    parsed_url = urlparse(cloudinary_url_string)
    
    # Explicitly configure Cloudinary with the parsed components
    cloudinary.config(
        cloud_name = parsed_url.hostname,
        api_key = parsed_url.username,
        api_secret = parsed_url.password,
        secure = True # Always use HTTPS
    )
    print("Cloudinary configured successfully.")
else:
    print("WARNING: CLOUDINARY_URL not found in environment. Image uploads will fail.")

# Explicitly get the database URL from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

# --- Database Helper ---
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

# --- Image Upload Helper (no changes needed here) ---
def upload_image_if_base64(image_data_string):
    if image_data_string and image_data_string.startswith('data:image'):
        try:
            upload_result = cloudinary.uploader.upload(
                image_data_string,
                folder="pokemon_app_assets", 
                overwrite=True
            )
            print(f"Successfully uploaded to Cloudinary. URL: {upload_result['secure_url']}")
            return upload_result['secure_url']
        except Exception as e:
            print(f"Error uploading to Cloudinary: {e}")
            return "https://via.placeholder.com/150/FF0000/FFFFFF?Text=Upload+Error"
    
    return image_data_string

# --- Points Configuration ---
RARITY_POINTS = {
    1: 10, 2: 25, 3: 50, 4: 100, 5: 200,
}

# --- Player Helpers ---
def get_or_create_player(conn, trainer_name):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM players WHERE name = %s", (trainer_name,))
        player = cur.fetchone()
        if not player:
            cur.execute("INSERT INTO players (name, points) VALUES (%s, 0) RETURNING *", (trainer_name,))
            player = cur.fetchone()
            conn.commit()
    return player

def add_points_to_player(conn, trainer_name, points_to_add):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "UPDATE players SET points = points + %s WHERE name = %s RETURNING *",
            (points_to_add, trainer_name)
        )
        updated_player = cur.fetchone()
        conn.commit()
    return updated_player

# --- Player Endpoints ---
# (No changes needed in any of the endpoints below, they are included for completeness)
@app.route('/api/players', methods=['GET'])
def get_all_players():
    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM players ORDER BY points DESC")
            players = cur.fetchall()
        conn.close()
        return jsonify(players if players else []), 200
    except Exception as e:
        if conn: conn.close()
        print(f"Error fetching all players: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/players/<name>', methods=['GET'])
def get_player_data(name):
    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    try:
        player = get_or_create_player(conn, name)
        conn.close()
        return jsonify(player), 200
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

# --- Pokémon Endpoints ---
@app.route('/api/pokemon', methods=['GET'])
def get_all_pokemon():
    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pokemon ORDER BY pokedex_number ASC, created_at DESC")
            pokemons = cur.fetchall()
        conn.close()
        return jsonify(pokemons)
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/pokemon', methods=['POST'])
def add_new_pokemon():
    data = request.get_json()
    if not data: return jsonify({"error": "Invalid input"}), 400

    required_fields = ['id', 'name', 'pokedexNumber', 'species', 'types', 'description', 'height', 'weight', 'hp', 'maxHp', 'rarity', 'imageUrl', 'status', 'trainerName']
    if not all(field in data for field in required_fields) or not data.get('trainerName'):
        missing = [field for field in required_fields if field not in data or (field == 'trainerName' and not data.get('trainerName'))]
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    final_image_url = upload_image_if_base64(data.get('imageUrl'))

    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500

    try:
        # ... (rest of the endpoint logic is unchanged)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pokemon WHERE pokedex_number = %s", (data['pokedexNumber'],))
            existing_pokemon = cur.fetchone()
            if existing_pokemon:
                conn.close()
                return jsonify({
                    "status": "already_discovered", "message": f"Pokemon {existing_pokemon['name']} was already discovered by {existing_pokemon['trainer_name']}.",
                    "pokemon": existing_pokemon, "discovered_by_trainer": existing_pokemon['trainer_name']
                }), 200

        trainer_name = data['trainerName']
        player = get_or_create_player(conn, trainer_name)
        points_for_catch = RARITY_POINTS.get(int(data['rarity']), 0)
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO pokemon (id, name, pokedex_number, species, types, description, height, weight, hp, max_hp, rarity, image_url, status, trainer_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *; 
                """,
                (
                    data['id'], data['name'], data['pokedexNumber'], data['species'], data['types'],
                    data['description'], data['height'], data['weight'], data['hp'], data['maxHp'],
                    data['rarity'], final_image_url, data['status'], trainer_name
                )
            )
            new_pokemon_record = cur.fetchone()
            conn.commit()
        
        updated_player_after_points = None
        if new_pokemon_record and points_for_catch > 0:
            updated_player_after_points = add_points_to_player(conn, trainer_name, points_for_catch)
        
        conn.close()
        return jsonify({
            "status": "new_discovery", "message": "Pokemon added successfully and points awarded!",
            "pokemon": new_pokemon_record, "player": updated_player_after_points or player
        }), 201

    except psycopg2.IntegrityError as e:
        if conn: conn.rollback()
        if "pokemon_pokedex_number_key" in str(e).lower():
            with conn.cursor(cursor_factory=RealDictCursor) as cur_conflict:
                cur_conflict.execute("SELECT * FROM pokemon WHERE pokedex_number = %s", (data['pokedexNumber'],))
                conflicting_pokemon = cur_conflict.fetchone()
            conn.close()
            return jsonify({ "status": "already_discovered", "pokemon": conflicting_pokemon }), 409
        if conn: conn.close()
        return jsonify({"error": f"Database integrity error: {str(e)}"}), 409
    except Exception as e:
        if conn: conn.rollback(); conn.close()
        return jsonify({"error": str(e)}), 500

# --- Item Endpoints (Unchanged) ---
@app.route('/api/items', methods=['GET'])
def get_all_items():
    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM items ORDER BY created_at DESC")
            items = cur.fetchall()
        conn.close()
        return jsonify(items)
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/items', methods=['POST'])
def add_new_item():
    data = request.get_json()
    if not data: return jsonify({"error": "Invalid input"}), 400

    data.setdefault('quantity', 1)
    required_fields = ['id', 'name', 'description', 'category', 'rarity', 'quantity', 'imageUrl']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    final_image_url = upload_image_if_base64(data.get('imageUrl'))

    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, quantity FROM items WHERE name = %s", (data['name'],))
            existing_item = cur.fetchone()

            if existing_item:
                new_quantity = existing_item['quantity'] + data['quantity']
                cur.execute(
                    "UPDATE items SET quantity = %s, description = %s, category = %s, rarity = %s, image_url = %s, use_button_text = %s WHERE id = %s RETURNING *",
                    (new_quantity, data.get('description'), data.get('category'), data.get('rarity'), final_image_url, data.get('useButtonText'), existing_item['id'])
                )
                updated_item = cur.fetchone()
                conn.commit()
                conn.close()
                return jsonify({"message": "Item quantity updated", "item": updated_item}), 200
            else:
                cur.execute(
                    """
                    INSERT INTO items (id, name, description, category, rarity, quantity, image_url, use_button_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (
                        data['id'], data['name'], data['description'], data['category'],
                        data['rarity'], data['quantity'], final_image_url, data.get('useButtonText')
                    )
                )
                new_item_record = cur.fetchone()
                conn.commit()
                conn.close()
                return jsonify({"message": "Item added successfully", "item": new_item_record}), 201
    except Exception as e:
        if conn: conn.rollback(); conn.close()
        return jsonify({"error": str(e)}), 500

# --- Other Item Endpoints (Unchanged) ---
@app.route('/api/items/<item_id>/quantity', methods=['PUT'])
def update_item_qty(item_id):
    data = request.get_json()
    if not data or 'quantity' not in data: return jsonify({"error": "Missing quantity"}), 400
    try:
        new_quantity = int(data['quantity'])
        if new_quantity < 0: return jsonify({"error": "Quantity cannot be negative"}), 400
    except ValueError:
        return jsonify({"error": "Invalid quantity format"}), 400

    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("UPDATE items SET quantity = %s WHERE id = %s RETURNING *", (new_quantity, item_id))
            updated_item = cur.fetchone()
            conn.commit()
        conn.close()
        if updated_item:
            return jsonify({"message": "Item quantity updated", "item": updated_item}), 200
        else:
            return jsonify({"error": "Item not found"}), 404
    except Exception as e:
        if conn: conn.rollback(); conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/items/<item_id>/increment', methods=['POST'])
def increment_item_qty(item_id):
    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("UPDATE items SET quantity = quantity + 1 WHERE id = %s RETURNING *", (item_id,))
            updated_item = cur.fetchone()
            conn.commit()
        conn.close()
        if updated_item:
            return jsonify({"message": "Item quantity incremented", "item": updated_item}), 200
        else:
            return jsonify({"error": "Item not found to increment"}), 404
    except Exception as e:
        if conn: conn.rollback(); conn.close()
        return jsonify({"error": str(e)}), 500


@app.route('/api/pokemon/<pokemon_id>', methods=['DELETE'])
def release_pokemon(pokemon_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM pokemon WHERE id = %s", (pokemon_id,))
            pokemon_exists = cur.fetchone()
            if not pokemon_exists:
                conn.close()
                return jsonify({"error": "Pokemon not found"}), 404

            cur.execute("DELETE FROM pokemon WHERE id = %s RETURNING *", (pokemon_id,))
            deleted_pokemon = cur.fetchone() # Optional: check if deletion was successful
            conn.commit()
        conn.close()
        
        if deleted_pokemon:
            return jsonify({"message": f"Pokemon {deleted_pokemon['name']} released successfully"}), 200
        else:
            # This case should ideally not be reached if the SELECT found it and DELETE ran.
            # But as a safeguard:
            return jsonify({"error": "Pokemon found but could not be deleted"}), 500


    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error releasing pokemon {pokemon_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/items/<item_id>', methods=['DELETE'])
def discard_item(item_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM items WHERE id = %s", (item_id,))
            item_exists = cur.fetchone()
            if not item_exists:
                conn.close()
                return jsonify({"error": "Item not found"}), 404

            cur.execute("DELETE FROM items WHERE id = %s RETURNING *", (item_id,))
            deleted_item = cur.fetchone() # Optional: check if deletion was successful
            conn.commit()
        conn.close()

        if deleted_item:
            return jsonify({"message": f"Item '{deleted_item['name']}' discarded successfully"}), 200
        else:
            return jsonify({"error": "Item found but could not be discarded"}), 500

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error discarding item {item_id}: {e}")
        return jsonify({"error": str(e)}), 500

# --- Daily Quest Endpoints ---
@app.route('/api/daily-quests/today', methods=['GET'])
def get_daily_quest_today():
    today_str = date.today().isoformat()
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM daily_quests WHERE quest_date = %s", (today_str,))
            quest = cur.fetchone()
        conn.close()
        if quest:
            return jsonify(quest), 200
        else:
            return jsonify({"message": "No quest found for today"}), 404
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching daily quest for {today_str}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/daily-quests', methods=['POST'])
def create_daily_quest():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid input"}), 400

    required_fields = ['title', 'description', 'summarizedQuest']
    if not all(field in data for field in required_fields):
        missing = [field for field in required_fields if field not in data]
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    today_str = date.today().isoformat()
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO daily_quests (quest_title, quest_description, quest_summary, quest_date, suggested_reward)
                VALUES (%s, %s, %s, %s, %s) RETURNING *
                """,
                (data['title'], data['description'], data['summarizedQuest'], today_str, data.get('suggestedReward'))
            )
            new_quest = cur.fetchone()
            conn.commit()
        conn.close()
        return jsonify(new_quest), 201
    except psycopg2.IntegrityError as e:
        if conn:
            conn.rollback()
            conn.close()
        # It's good practice to log the actual error on the server for debugging
        print(f"IntegrityError creating daily quest: {e}")
        return jsonify({"error": "A quest for today might already exist or another integrity constraint was violated."}), 409
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error creating daily quest: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/daily-quests/summaries', methods=['GET'])
def get_daily_quest_summaries():
    limit = request.args.get('limit', default=10, type=int)
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT quest_summary FROM daily_quests ORDER BY quest_date DESC LIMIT %s", (limit,))
            results = cur.fetchall()
        conn.close()
        summaries = [row['quest_summary'] for row in results]
        return jsonify(summaries), 200
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching daily quest summaries: {e}")
        return jsonify({"error": str(e)}), 500
        
if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", 5001))