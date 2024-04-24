import glob
import logging
import os
import re
import shutil
import uuid
from dotenv import load_dotenv
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
import openai
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, logout_user

uri = "bolt://graphdb:7687"

files_path = "files"
images_path = "uploaded_images"
load_dotenv()

app = Flask(__name__, static_folder=images_path, static_url_path="/uploaded_images")
app.config['SECRET_KEY'] = 'a-very-secret-key-that-should-be-changed'
CORS(app)

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///insightmap.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        """True, as all users are active."""
        return True

    def get_id(self):
        return str(self.id)

if not os.path.exists(images_path):
    os.makedirs(images_path)

try:
    openai.api_key = os.environ["OPENAI_KEY"]
except:
    logging.warning("Unable to set OpenAI API Key and Organization")

"""
-----------------------
File helper
-----------------------
"""


def get_node(nodename):
    # Define the path of the .md file
    file_path = os.path.join(files_path, f"{nodename}.md")

    # Check if the file exists
    if os.path.exists(file_path):
        # Read the content of the file
        with open(file_path, "r") as f:
            content = f.read()
            tags_content = content.split("-----\n")
            tags = []
            if len(tags_content) > 1:
                tags = tags_content[0].split("\n")
                tags = [
                    tag.replace("[tag:", "").replace("]", "")
                    for tag in tags
                    if tag.startswith("[tag:")
                ]
                content = tags_content[1]
            else:
                content = tags_content[0]

            return {"id": nodename, "label": nodename, "tags": tags, "content": content}
    else:
        return None


def get_edges(node):
    edges = []

    # Extract linked nodes from the content using regex
    linked_nodes = re.findall(r"\[([^\]]+)]\(\<node:([^\>]+)\>\)", node["content"])
    unique_linked_nodes = list(set(node[1] for node in linked_nodes))

    # Add an edge for each linked node
    for linked_node in unique_linked_nodes:
        edges.append(
            {
                "from": node["label"],
                "to": linked_node,
                "label": "LINKS_TO",
            }
        )

    return edges


def validate_edges(edges, nodes):
    validated_edges = []

    for edge in edges:
        if edge["to"] in [node["label"] for node in nodes]:
            validated_edges.append(edge)

    return validated_edges


"""
-----------------------
File endpoints
-----------------------
"""


@app.route("/graph/saveNode/<nodename>", methods=["POST"])
def save_node(nodename):
    content = request.json.get("content")

    # Get the existing node data
    node = get_node(nodename)

    # Define the path of the .md file
    file_path = os.path.join(files_path, f"{nodename}.md")

    # Save the content of the node to a .md file
    with open(file_path, "w") as f:
        # Write tags at the beginning of the file
        if node and node["tags"]:
            f.write("\n".join(f"[tag:{tag}]" for tag in node["tags"]) + "\n-----\n")
        f.write(content)

    return jsonify(success=True, nodename=nodename)


@app.route("/graph/<nodename>", methods=["DELETE"])
def delete_node(nodename):
    # Define the path of the .md file
    file_path = os.path.join(files_path, f"{nodename}.md")

    # Check if the file exists
    if os.path.exists(file_path):
        # Delete the file
        os.remove(file_path)
        return jsonify(success=True)
    else:
        return jsonify(success=False, error="File not found"), 404


@app.route("/graph", methods=["GET"])
def fetch_graph():
    nodes = []
    edges = []

    # Get all .md files in the nodes directory
    files = glob.glob(os.path.join(files_path, "*.md"))

    for file in files:
        # Extract the node name from the filename
        node_name = os.path.splitext(os.path.basename(file))[0]

        node = get_node(node_name)
        nodes.append(node)

        edges += validate_edges(get_edges(node), nodes)

    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/graph/<nodename>", methods=["GET"])
def read_node(nodename):
    node = get_node(nodename)

    # Check if the file exists
    if node:
        return jsonify(success=True, content=node["content"])
    else:
        return jsonify(success=False, error="Node not found"), 404


@app.route("/graph/renameNode", methods=["POST"])
def rename_node():
    data = request.json
    old_node_name = data.get("oldNodeName")
    new_node_name = data.get("newNodeName")

    old_file_path = os.path.join(files_path, f"{old_node_name}.md")
    new_file_path = os.path.join(files_path, f"{new_node_name}.md")

    # Check if the old file exists
    if os.path.exists(old_file_path):
        # Rename the file
        shutil.move(old_file_path, new_file_path)
        return jsonify({"status": "Node renamed"})
    else:
        return jsonify({"status": "Error", "message": "Node not found"}), 404


"""
-----------------------
Tagging endpoints
-----------------------
"""


@app.route("/graph/tagNode", methods=["POST"])
def tag_node():
    data = request.json
    nodeName = data.get("nodeName")
    new_tags = data.get("tags")

    # Get the existing node data
    node = get_node(nodeName)

    # Add new tags to the existing tags
    tags = list(set(node["tags"] + new_tags))

    # Define the path of the .md file
    file_path = os.path.join(files_path, f"{nodeName}.md")

    # Save the tags and content of the node to a .md file
    with open(file_path, "w") as f:
        # Write tags at the beginning of the file
        if tags:
            f.write("\n".join(f"[tag:{tag}]" for tag in tags) + "\n-----\n")
        f.write(node["content"])

    return jsonify({"status": "Tag added"})


@app.route("/graph/removeTag", methods=["POST"])
def remove_tag():
    data = request.json
    nodeName = data.get("nodeName")
    tag_to_remove = data.get("tag")

    # Get the existing node data
    node = get_node(nodeName)

    # Remove the tag from the existing tags
    tags = [tag for tag in node["tags"] if tag != tag_to_remove]

    # Define the path of the .md file
    file_path = os.path.join(files_path, f"{nodeName}.md")

    # Save the tags and content of the node to a .md file
    with open(file_path, "w") as f:
        # Write tags at the beginning of the file
        if tags:
            f.write("\n".join(f"[tag:{tag}]" for tag in tags) + "\n-----\n")
        f.write(node["content"])

    return jsonify({"status": "Tag removed"})


"""
-----------------------
Image endpoints
-----------------------
"""


@app.route("/uploadImage", methods=["POST"])
def upload_image():
    uploaded_file = request.files["image"]

    if uploaded_file.filename != "":
        # Generate a unique filename
        file_ext = os.path.splitext(uploaded_file.filename)[1]
        filename = f"{uuid.uuid4().hex}{file_ext}"

        filepath = os.path.join(images_path, secure_filename(filename))
        uploaded_file.save(filepath)

        return jsonify(success=True, filename=filename)


"""
-----------------------
Generative AI endpoints
-----------------------
"""

session_store = {}


def generate_unique_session_id():
    return str(uuid.uuid4())


def generate_tree_func(selection):
    prompt = f"Perform a multi-dimensional analysis of {selection} by constructing a Tree of Abstraction. Start from the immediate, tangible actions and delve into deeper layers of complexity, adapting your approach as needed to capture the unique aspects of this activity. Your analysis may include, but is not limited to, the biological, psychological, social, technological, economic, and philosophical dimensions. Provide a comprehensive and insightful exploration, and identify intersections between different layers of abstraction where relevant. Return in markdown."

    def generate():
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )

        for chunk in response:
            try:
                text_chunk = chunk["choices"][0]["delta"]["content"]
                finish_reason = chunk["choices"][0]["finish_reason"]

                if finish_reason == "stop":
                    yield "data: __complete__\n\n"

                text_chunk = text_chunk.replace("\n", "<br>")
                yield f"data: {text_chunk}\n\n"

            except KeyError:
                logging.info(f"Debug: Skipping incomplete chunk {chunk}")  # Debug line
                continue

    return generate


def generate_answer_func(question, system_prompt):
    prompt = f"Answer the following question: {question}. Return in markdown with latex support ($)."

    if system_prompt:
        initial_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    else:
        initial_messages = [
            {"role": "user", "content": prompt},
        ]

    def generate():
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=initial_messages,
            stream=True,
            max_tokens=4096,
            temperature=0.0,
        )

        for chunk in response:
            try:
                text_chunk = chunk["choices"][0]["delta"].get("content", "")
                finish_reason = chunk["choices"][0]["finish_reason"]

                if finish_reason == "stop":
                    yield "data: __complete__\n\n"

                text_chunk = text_chunk.replace("\n", "<br>")
                yield f"data: {text_chunk}\n\n"

            except KeyError:
                logging.info(f"Debug: Skipping incomplete chunk {chunk}")  # Debug line
                continue

    return generate


def generate_recommendations_func(current_node_name, current_node_content):
    # Fetch the current node's content and the names of linked nodes from the .md files
    node = {"label": current_node_name, "content": current_node_content}
    first_degree_nodes = get_edges(node)
    unique_first_degree_nodes = list(set(edge["to"] for edge in first_degree_nodes))

    # Get second degree nodes
    second_degree_nodes = []
    for first_degree_node in unique_first_degree_nodes:
        node = get_node(first_degree_node)
        if node:
            second_degree_nodes += get_edges(node)
    unique_second_degree_nodes = list(set(edge["to"] for edge in second_degree_nodes))

    context = f'Given the context about {current_node_name}, which includes this information: "{current_node_content}", and has information about related topics such as {unique_first_degree_nodes} and extended connections including {unique_second_degree_nodes}, suggest new topics or areas that could expand on this knowledge or provide deeper insight into related areas. Return as a list [topic_name1, topic_name2, ...]'

    def generate():
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[{"role": "user", "content": context}],
            stream=True,
        )

        for chunk in response:
            try:
                text_chunk = chunk["choices"][0]["delta"].get("content", "")
                finish_reason = chunk["choices"][0]["finish_reason"]

                if finish_reason == "stop":
                    yield "data: __complete__\n\n"

                text_chunk = text_chunk.replace("\n", "<br>")
                yield f"data: {text_chunk}\n\n"

            except KeyError:
                logging.info(f"Debug: Skipping incomplete chunk {chunk}")  # Debug line
                continue

    # Send the recommendations back to the frontend
    return generate


@app.route("/generate_tree", methods=["POST"])
def generate_tree_endpoint():
    selection = request.json.get("selection")
    session_id = generate_unique_session_id()  # Assume you have this function defined
    session_store[session_id] = generate_tree_func(selection)
    return jsonify({"session_id": session_id, "selection": selection})


@app.route("/generate_answer", methods=["POST"])
def answer_endpoint():
    question = request.json.get("question")
    system_prompt = request.json.get("system_prompt")
    session_id = generate_unique_session_id()
    session_store[session_id] = generate_answer_func(question, system_prompt)
    return jsonify({"session_id": session_id, "question": question})


@app.route("/generate_recommendations", methods=["POST"])
def generate_recommendations_endpoint():
    node_name = request.json.get("node_name")
    node_content = request.json.get("node_content")
    session_id = generate_unique_session_id()  # Assume you have this function defined
    session_store[session_id] = generate_recommendations_func(node_name, node_content)
    return jsonify({"session_id": session_id, "node_name": node_name})


@app.route("/request_sse")
def request_sse():
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    session_id = request.args.get("session_id")
    generator_function = session_store.get(session_id)
    if generator_function:
        return Response(generator_function(), headers=headers)
    else:
        return "Session not found", 404


@app.route("/register", methods=["POST"])
def register():
    # Get data from request
    username = request.json.get("username")
    email = request.json.get("email")
    password = request.json.get("password")

    # Check if user already exists
    user_exists = User.query.filter((User.username == username) | (User.email == email)).first()
    if user_exists:
        return jsonify({"error": "User already exists"}), 409

    # Create new user and set password
    new_user = User(username=username, email=email)
    new_user.set_password(password)

    # Add new user to the database
    db.session.add(new_user)
    db.session.commit()

    return jsonify({"success": "User registered successfully"}), 201

@app.route("/login", methods=["POST"])
def login():
    # Get data from request
    username = request.json.get("username")
    password = request.json.get("password")

    # Check if user exists
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user)
        return jsonify({"success": "Logged in successfully"}), 200
    else:
        return jsonify({"error": "Invalid username or password"}), 401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
