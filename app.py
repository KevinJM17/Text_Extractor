from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from google.cloud import storage, bigquery, vision
from google.cloud.vision_v1 import types
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
import os
from PIL import Image
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
login_manager = LoginManager(app)
login_manager.login_view = 'login'
bcrypt = Bcrypt(app)

# Configure Google Cloud Storage client
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "C:/Users/shobh/Text_Extractorr/Text_Extractor/sa_key.json"
vision_client = vision.ImageAnnotatorClient()
storage_client = storage.Client()
bucket_name = 'kevintestingdata'
bucket = storage_client.get_bucket(bucket_name)

# Configure BigQuery client
bq_client = bigquery.Client()
dataset_id = 'newDataset'
table_id = 'users'


# Define the User class
class User(UserMixin):
    def __init__(self, username, password):
        self.username = username
        self.password = password

    def get_id(self):
        return self.username

    @staticmethod
    def get(username):
        user_data = bq_client.query(f"SELECT * FROM {dataset_id}.{table_id} WHERE username='{username}'").result()
        user = list(user_data)
        
        if user:
            return User(user[0]['username'], user[0]['password'])
        else:
            return None  # or raise an exception or handle the case in a way that fits your application logic

    
    
@login_manager.user_loader
def load_user(username):
    return User.get(username)

# Helper function to insert a new user into BigQuery
def insert_user(username, password):
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    query = f"INSERT INTO {dataset_id}.{table_id} (username, password) VALUES ('{username}', '{hashed_password}')"
    bq_client.query(query)

# Helper function to extract text from an image (replace this with your OCR logic)
def extract_text_from_image(file):
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(request.url)

    if file:
        # Upload image to Cloud Storage
        bucket = storage_client.bucket(bucket_name)
        filename = secure_filename(file.filename)
        image_path = bucket.blob(filename).public_url
        print(image_path)

        image = types.Image()
        image.source.image_uri = image_path
        # Perform text detection
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        extracted_text = texts[0].description if texts else "No text found."
        return extracted_text

def get_image_urls(images):
    return [{'filename': image.filename, 'url': bucket.blob(f'{image.filename}').public_url, 'extracted_text': image.extracted_text} for image in images]

# Routes
@app.route('/')
@login_required
def index():
    # Retrieve user's images from BigQuery
    images_query = f"SELECT * FROM {dataset_id}.images WHERE user_id='{current_user.username}'"
    images_data = bq_client.query(images_query).result()
    images = list(images_data)

    # Get image URLs from Cloud Storage
    image_urls = get_image_urls(images)

    return render_template('index.html', images=image_urls)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Retrieve user data from the database
        user_data = bq_client.query(f"SELECT * FROM {dataset_id}.{table_id} WHERE username='{username}'").result()
        user = list(user_data)
        
        if user and bcrypt.check_password_hash(user[0]['password'], password):
            login_user(User(user[0]['username'], user[0]['password']))
            return redirect(url_for('index'))
        else:
            # User not found in the database, prompt for registration
            flash('User does not exist. Please register.', 'warning')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Check if the username already exists
        query = f"SELECT COUNT(*) as count FROM {dataset_id}.{table_id} WHERE username = '{username}'"
        query_job = bq_client.query(query)
        result = query_job.result()

        for row in result:
            count = row['count']

        if count > 0:
            flash('Username already exists. Please choose a different username.', 'warning')
            return render_template('register.html')

        # If username is unique, proceed with registration
        insert_user(username, password)

        flash('Registration successful! You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        file = request.files['file']
        if file:
            # Upload image to cloud storage
            image_path = f'{file.filename}'
            blob = bucket.blob(image_path)
            blob.upload_from_string(file.read(), content_type=file.content_type)

            # extract text
            extracted_text = extract_text_from_image(file).replace('\n', ' ')
            print(extracted_text)
            # Save image information to BigQuery
            insert_image_query = f"INSERT INTO {dataset_id}.images (filename, extracted_text, user_id) " \
                                f"VALUES ('{file.filename}', '{extracted_text}', '{current_user.username}')"
            bq_client.query(insert_image_query)

            return redirect(url_for('index'))

    return render_template('upload.html')

@app.route('/view/<filename>')
@login_required
def view(filename):
    # Retrieve image details from BigQuery
    image_query = f"SELECT * FROM {dataset_id}.images WHERE filename='{filename}' AND user_id='{current_user.username}'"
    image_data = bq_client.query(image_query).result()
    image = list(image_data)[0]

    # Get the image URL from Cloud Storage
    bucket = storage_client.bucket(bucket_name)
    filename = secure_filename(filename)

    image_path = bucket.blob(f'{filename}').public_url
  
    return render_template('view.html', image={'filename': image.filename, 'url': image_path, 'extracted_text': image.extracted_text})


@app.route('/edit/<filename>', methods=['GET', 'POST'])
@login_required
def edit(filename):
    # Retrieve image details from BigQuery
    image_query = f"SELECT * FROM {dataset_id}.images WHERE filename='{filename}' AND user_id='{current_user.username}'"
    image_data = bq_client.query(image_query).result()
    image = list(image_data)[0]

    if request.method == 'POST':
        new_file = request.files['file']
        if new_file:
            # Save new image to cloud storage
            new_image_path = f'{new_file.filename}'
            new_blob = bucket.blob(new_image_path)
            new_blob.upload_from_string(new_file.read(), content_type=new_file.content_type)

            # Extract text using your preferred OCR library
            new_extracted_text = extract_text_from_image(new_file).replace('\n', ' ')

            # Update image information in BigQuery
            update_image_query = f"UPDATE {dataset_id}.images SET " \
                                f"filename='{new_file.filename}', extracted_text='{new_extracted_text}' " \
                                f"WHERE filename='{filename}' AND user_id='{current_user.username}'"
            bq_client.query(update_image_query)

            flash('Image updated successfully', 'success')
            return redirect(url_for('index'))

    return render_template('edit.html', image={'filename': image.filename, 'extracted_text': image.extracted_text})

@app.route('/delete/<filename>')
@login_required
def delete(filename):
    # Delete image information from BigQuery
    delete_image_query = f"DELETE FROM {dataset_id}.images WHERE filename='{filename}' AND user_id='{current_user.username}'"
    bq_client.query(delete_image_query)

    flash('Image deleted successfully', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)