import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env

app = Flask(__name__)
CORS(app) # Enable CORS for all routes, adjust for production if needed

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

# --- Pokémon Endpoints ---
@app.route('/api/pokemon', methods=['GET'])
def get_all_pokemon():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pokemon ORDER BY created_at DESC")
            pokemons = cur.fetchall()
        conn.close()
        return jsonify(pokemons)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/pokemon', methods=['POST'])
def add_new_pokemon():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid input"}), 400

    required_fields = ['id', 'name', 'pokedexNumber', 'species', 'types', 'description', 'height', 'weight', 'hp', 'maxHp', 'rarity', 'imageUrl', 'status']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pokemon (id, name, pokedex_number, species, types, description, height, weight, hp, max_hp, rarity, image_url, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING; 
                """, # ON CONFLICT ensures if ID somehow exists, it doesn't error out, though IDs should be unique. pokedex_number is UNIQUE too.
                (
                    data['id'], data['name'], data['pokedexNumber'], data['species'], data['types'],
                    data['description'], data['height'], data['weight'], data['hp'], data['maxHp'],
                    data['rarity'], data['imageUrl'], data['status']
                )
            )
            conn.commit()
            
            # Check if a row was actually inserted (it might not if there was a conflict and we did NOTHING)
            if cur.rowcount == 0:
                 # Attempt to fetch the existing one if ID or PokedexNumber matched
                cur.execute("SELECT * FROM pokemon WHERE id = %s OR pokedex_number = %s", (data['id'], data['pokedexNumber']))
                existing_pokemon = cur.fetchone()
                if existing_pokemon:
                     conn.close()
                     # If we are here, it means the pokemon already exists by ID or Pokedex Number
                     # The frontend should ideally handle this by not trying to add duplicates of already known Pokedex numbers if that's the logic.
                     # For now, let's assume the frontend manages this and this POST is for genuinely new entries.
                     # If a conflict happened on `pokedex_number` which is UNIQUE, an error would have been raised by PG unless handled.
                     # The `ON CONFLICT (id) DO NOTHING` handles only ID conflicts.
                     # A more robust solution for pokedexNumber would be a try-catch for UNIQUE constraint violation.
                     return jsonify({"message": "Pokemon with this ID or Pokedex Number might already exist", "pokemon": data}), 200 # Or 409 Conflict
                # Fallback if no specific conflict reason identified by above query
                return jsonify({"message": "Pokemon might already exist or another issue occurred."}), 409


        conn.close()
        return jsonify({"message": "Pokemon added successfully", "pokemon": data}), 201
    except psycopg2.IntegrityError as e: # Catch unique constraint violations specifically (e.g. for pokedex_number)
        conn.rollback()
        conn.close()
        if "pokemon_pokedex_number_key" in str(e).lower(): # Check if it's the pokedex_number unique constraint
            return jsonify({"error": f"Pokemon with Pokedex number {data['pokedexNumber']} already exists."}), 409
        return jsonify({"error": f"Database integrity error: {str(e)}"}), 409
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500


# --- Item Endpoints ---
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
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/items', methods=['POST'])
def add_new_item():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid input"}), 400

    # Ensure quantity is present, default to 1 if not.
    data.setdefault('quantity', 1)

    required_fields = ['id', 'name', 'description', 'category', 'rarity', 'quantity', 'imageUrl']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check if item with the same NAME already exists. If so, update quantity.
            # This aligns with Gemini trying to identify existing items by name.
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
                # Item does not exist by name, insert new one
                cur.execute(
                    """
                    INSERT INTO items (id, name, description, category, rarity, quantity, image_url, use_button_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (
                        data['id'], data['name'], data['description'], data['category'],
                        data['rarity'], data['quantity'], data['imageUrl'], data.get('useButtonText') # useButtonText is optional
                    )
                )
                new_item_record = cur.fetchone()
                conn.commit()
                conn.close()
                return jsonify({"message": "Item added successfully", "item": new_item_record}), 201
    except Exception as e:
        conn.rollback()
        conn.close()
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
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
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
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500

# Endpoint specifically for incrementing, as used by CameraView for existing items
@app.route('/api/items/<item_id>/increment', methods=['POST'])
def increment_item_qty(item_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
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
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5001) # Runs on port 5001 to avoid conflict with potential frontend dev server
