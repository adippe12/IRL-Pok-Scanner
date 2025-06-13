import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv() 

app = Flask(__name__)
CORS(app) 

DATABASE_URL = os.getenv("DATABASE_URL")

# --- Database Helper ---
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

# --- Points Configuration ---
RARITY_POINTS = {
    1: 10,
    2: 25,
    3: 50,
    4: 100,
    5: 200,
}

# --- Player Helpers ---
def get_or_create_player(conn, trainer_name):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM players WHERE name = %s", (trainer_name,))
        player = cur.fetchone()
        if not player:
            cur.execute("INSERT INTO players (name, points) VALUES (%s, 0) RETURNING *", (trainer_name,))
            player = cur.fetchone()
            conn.commit() # Commit after creating a new player
    return player

def add_points_to_player(conn, trainer_name, points_to_add):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "UPDATE players SET points = points + %s WHERE name = %s RETURNING *",
            (points_to_add, trainer_name)
        )
        updated_player = cur.fetchone()
        conn.commit() # Commit after updating points
    return updated_player


# --- Player Endpoints ---
@app.route('/api/players/<name>', methods=['GET'])
def get_player_data(name):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        player = get_or_create_player(conn, name) # Ensures player exists, returns their data
        conn.close()
        return jsonify(player), 200
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

# --- Pokémon Endpoints ---
@app.route('/api/pokemon', methods=['GET'])
def get_all_pokemon():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Order by pokedex_number for consistency, then by creation if needed
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
    if not data:
        return jsonify({"error": "Invalid input"}), 400

    required_fields = [
        'id', 'name', 'pokedexNumber', 'species', 'types', 'description', 
        'height', 'weight', 'hp', 'maxHp', 'rarity', 'imageUrl', 'status',
        'trainerName' # New required field
    ]
    if not all(field in data for field in required_fields) or not data.get('trainerName'):
        missing = [field for field in required_fields if field not in data or (field == 'trainerName' and not data.get('trainerName'))]
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        # Check if Pokémon with this pokedexNumber already exists
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pokemon WHERE pokedex_number = %s", (data['pokedexNumber'],))
            existing_pokemon = cur.fetchone()

            if existing_pokemon:
                conn.close()
                return jsonify({
                    "status": "already_discovered",
                    "message": f"Pokemon {existing_pokemon['name']} (#{existing_pokemon['pokedex_number']}) was already discovered by {existing_pokemon['trainer_name']}.",
                    "pokemon": existing_pokemon, # Return existing data
                    "discovered_by_trainer": existing_pokemon['trainer_name']
                }), 200 # 200 OK, as it's not an error, but a game state

        # If not existing, proceed to add
        trainer_name = data['trainerName']
        player = get_or_create_player(conn, trainer_name) # Ensure player exists

        points_for_catch = RARITY_POINTS.get(int(data['rarity']), 0)
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur: # Re-open cursor if closed by previous helpers, or use a single cursor block
            cur.execute(
                """
                INSERT INTO pokemon (id, name, pokedex_number, species, types, description, height, weight, hp, max_hp, rarity, image_url, status, trainer_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *; 
                """,
                (
                    data['id'], data['name'], data['pokedexNumber'], data['species'], data['types'],
                    data['description'], data['height'], data['weight'], data['hp'], data['maxHp'],
                    data['rarity'], data['imageUrl'], data['status'], trainer_name
                )
            )
            new_pokemon_record = cur.fetchone()
            conn.commit() # Commit after Pokemon insertion
        
        updated_player_after_points = None
        if new_pokemon_record and points_for_catch > 0:
            updated_player_after_points = add_points_to_player(conn, trainer_name, points_for_catch)
        
        conn.close()
        return jsonify({
            "status": "new_discovery",
            "message": "Pokemon added successfully and points awarded!",
            "pokemon": new_pokemon_record,
            "player": updated_player_after_points or player # Return player data
        }), 201

    except psycopg2.IntegrityError as e:
        conn.rollback() # Rollback on integrity error
        # This might happen if pokedex_number UNIQUE constraint is violated due to a race condition
        # despite the initial check. Or if 'id' (UUID) collides.
        if "pokemon_pokedex_number_key" in str(e).lower():
             # Fetch the conflicting Pokémon to return its data
            with conn.cursor(cursor_factory=RealDictCursor) as cur_conflict:
                cur_conflict.execute("SELECT * FROM pokemon WHERE pokedex_number = %s", (data['pokedexNumber'],))
                conflicting_pokemon = cur_conflict.fetchone()
            conn.close()
            return jsonify({
                "status": "already_discovered",
                "message": f"Pokemon with Pokedex number {data['pokedexNumber']} already exists (discovered by {conflicting_pokemon['trainer_name'] if conflicting_pokemon else 'Unknown'}).",
                "pokemon": conflicting_pokemon,
                "discovered_by_trainer": conflicting_pokemon['trainer_name'] if conflicting_pokemon else None
            }), 409 # Conflict
        
        if conn: conn.close()
        return jsonify({"error": f"Database integrity error: {str(e)}"}), 409
    except Exception as e:
        if conn: conn.rollback(); conn.close()
        return jsonify({"error": str(e)}), 500


# --- Item Endpoints (largely unchanged, but ensure DB connection is handled) ---
@app.route('/api/items', methods=['GET'])
def get_all_items():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
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
    if not data:
        return jsonify({"error": "Invalid input"}), 400

    data.setdefault('quantity', 1)
    required_fields = ['id', 'name', 'description', 'category', 'rarity', 'quantity', 'imageUrl']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, quantity FROM items WHERE name = %s", (data['name'],))
            existing_item = cur.fetchone()

            if existing_item:
                new_quantity = existing_item['quantity'] + data['quantity']
                cur.execute(
                    "UPDATE items SET quantity = %s, description = %s, category = %s, rarity = %s, image_url = %s, use_button_text = %s WHERE id = %s RETURNING *",
                    (new_quantity, data.get('description'), data.get('category'), data.get('rarity'), data.get('imageUrl'), data.get('useButtonText'), existing_item['id'])
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
                        data['rarity'], data['quantity'], data['imageUrl'], data.get('useButtonText')
                    )
                )
                new_item_record = cur.fetchone()
                conn.commit()
                conn.close()
                return jsonify({"message": "Item added successfully", "item": new_item_record}), 201
    except Exception as e:
        if conn: conn.rollback(); conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/items/<item_id>/quantity', methods=['PUT'])
def update_item_qty(item_id):
    data = request.get_json()
    if not data or 'quantity' not in data:
        return jsonify({"error": "Missing quantity in request body"}), 400
    
    try:
        new_quantity = int(data['quantity'])
        if new_quantity < 0:
            return jsonify({"error": "Quantity cannot be negative"}), 400
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


if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", 5001))
