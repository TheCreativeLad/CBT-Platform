# C:\Users\Agba Xchanger\cbt_platform\app.py

from flask import Flask, render_template, request, redirect, url_for, session, flash
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# --- NEW: Set the secret code for admin sign-up ---
ADMIN_SECRET_CODE = 'administrator'


# --- Initialize Firebase Admin SDK ---
try:
    cred = credentials.Certificate("cbt-platform-8910c-firebase-adminsdk-fbsvc-73e123dcd5.json")
    firebase_admin.initialize_app(cred)
except ValueError:
    pass

db = firestore.client()

# --- NEW: Reference to the settings document for the quiz timer ---
settings_doc_ref = db.collection('settings').document('quiz_timer')


# --- Route Definitions ---

@app.route('/')
def home():
    """Renders the welcome page."""
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """
    Handles user registration using Firestore, including a check for an admin code.
    """
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin_code = request.form.get('admin_code')
        
        users_ref = db.collection('users')
        user_doc = users_ref.document(username).get()
        
        if user_doc.exists:
            flash('Username already exists! Please choose a different one.', 'error')
            return redirect(url_for('signup'))
        
        # Check if the submitted admin code is correct
        is_admin = False
        if admin_code == ADMIN_SECRET_CODE:
            is_admin = True
            flash('Admin account created successfully!', 'success')
        else:
            flash('Student account created successfully!', 'success')
        
        users_ref.document(username).set({
            'password': password,
            'is_admin': is_admin,
            'results': []
        })
        
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Handles user login and checks for admin status.
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        users_ref = db.collection('users')
        user_doc = users_ref.document(username).get()
        
        if user_doc.exists and user_doc.to_dict()['password'] == password:
            session['username'] = username
            session['is_admin'] = user_doc.to_dict().get('is_admin', False)
            
            if session.get('is_admin'):
                return redirect(url_for('admin_dashboard'))
            else:
                # This is the corrected line.
                # Non-admin users should be redirected to the start_quiz route.
                return redirect(url_for('start_quiz'))
                
        # Use flash to show an error message
        flash('Invalid credentials! Please go back and try again.')
        return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/start_quiz')
def start_quiz():
    """Initializes session variables for a new quiz and redirects to the first question."""
    if 'username' not in session:
        return redirect(url_for('login'))
    
    session['current_question_index'] = 0
    session['user_answers'] = {}
    return redirect(url_for('quiz'))

@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    """
    Displays the quiz questions one by one. Fetches questions from Firestore.
    """
    if 'username' not in session:
        return redirect(url_for('login'))

    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    quiz_questions = [doc.to_dict() for doc in questions_docs]
    
    current_question_index = session.get('current_question_index', 0)
    user_answers = session.get('user_answers', {})
    
    if request.method == 'POST':
        question_id = request.form.get('question_id')
        user_answer = request.form.get(question_id)
        
        user_answers[question_id] = user_answer
        session['user_answers'] = user_answers
        
        session['current_question_index'] = current_question_index + 1
        
        return redirect(url_for('quiz'))

    if current_question_index < len(quiz_questions):
        current_question = quiz_questions[current_question_index]
        
        # NOTE: The timer is now fetched from the 'quiz_timer' document.
        # instructions are still fetched from 'quiz_settings'.
        timer_doc = db.collection('settings').document('quiz_timer').get()
        timer_minutes = timer_doc.to_dict().get('duration_minutes', 30) if timer_doc.exists else 30
        
        instructions_doc = db.collection('settings').document('quiz_settings').get()
        instructions = instructions_doc.to_dict().get('instructions', 'No instructions set.') if instructions_doc.exists else 'No instructions set.'
        
        return render_template('quiz.html', 
                                question=current_question, 
                                current_question_index=current_question_index,
                                total_questions=len(quiz_questions),
                                timer_minutes=timer_minutes,
                                instructions=instructions)
    else:
        return redirect(url_for('submit_quiz'))

@app.route('/submit_quiz')
def submit_quiz():
    """Calculates the final score, stores it, and redirects to the results page."""
    if 'username' not in session:
        return redirect(url_for('login'))

    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    quiz_questions = [doc.to_dict() for doc in questions_docs]

    score = 0
    user_answers = session.get('user_answers', {})
    
    for question in quiz_questions:
        user_answer = user_answers.get(question['id'])
        if user_answer == question['answer']:
            score += 1
            
    session['score'] = score
    
    username = session['username']
    users_ref = db.collection('users').document(username)
    users_ref.update({
        'results': firestore.ArrayUnion([score])
    })

    return redirect(url_for('result'))

@app.route('/result')
def result():
    """Displays the user's quiz score."""
    if 'score' not in session:
        return redirect(url_for('home'))
    
    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    quiz_questions = [doc.to_dict() for doc in questions_docs]
    
    return render_template('result.html', score=session['score'], total=len(quiz_questions))

@app.route('/answers')
def answers():
    """
    Displays the correct answers for the quiz and the user's submitted answers.
    """
    if 'username' not in session:
        return redirect(url_for('login'))

    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    quiz_questions = [doc.to_dict() for doc in questions_docs]
    user_answers = session.get('user_answers', {})
    
    return render_template('answers.html', questions=quiz_questions, user_answers=user_answers)

@app.route('/logout')
def logout():
    """Logs the user out by clearing the session."""
    session.clear()
    return redirect(url_for('home'))

# --- Admin Routes ---
def check_admin():
    """Helper function to check if the current user is an admin."""
    return session.get('is_admin', False)

@app.route('/admin')
def admin_dashboard():
    """Renders the main admin dashboard."""
    if not check_admin():
        return "Access Denied", 403
    return render_template('admin_dashboard.html')

@app.route('/admin/manage_questions', methods=['GET', 'POST'])
def manage_questions():
    """Handles adding questions."""
    if not check_admin():
        return "Access Denied", 403

    if request.method == 'POST':
        question_text = request.form['question']
        choice_a = request.form['choice_A']
        choice_b = request.form['choice_B']
        choice_c = request.form['choice_C']
        choice_d = request.form['choice_D']
        answer = request.form['answer']

        # Determine the next question ID
        questions_ref = db.collection('questions').order_by('id')
        questions_docs = questions_ref.stream()
        questions_count = len(list(questions_docs))
        new_id = str(questions_count + 1)
        
        new_question = {
            'id': new_id,
            'question': question_text,
            'choices': [choice_a, choice_b, choice_c, choice_d],
            'answer': answer
        }
        db.collection('questions').document(new_id).set(new_question)
        
        return redirect(url_for('manage_questions'))
    
    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    questions = [doc.to_dict() for doc in questions_docs]
    return render_template('manage_questions.html', questions=questions)

@app.route('/admin/edit_question/<question_id>', methods=['GET', 'POST'])
def edit_question(question_id):
    """Edits an existing question in Firestore."""
    if not check_admin():
        return "Access Denied", 403

    question_doc = db.collection('questions').document(question_id).get()
    if not question_doc.exists:
        return "Question not found", 404
    
    if request.method == 'POST':
        updated_data = {
            'question': request.form['question'],
            'choices': [request.form['choice_A'], request.form['choice_B'], request.form['choice_C'], request.form['choice_D']],
            'answer': request.form['answer']
        }
        db.collection('questions').document(question_id).update(updated_data)
        return redirect(url_for('manage_questions'))
    
    question = question_doc.to_dict()
    return render_template('edit_question.html', question=question)

@app.route('/admin/delete_question/<question_id>', methods=['POST'])
def delete_question(question_id):
    """Deletes a question from Firestore."""
    if not check_admin():
        return "Access Denied", 403

    db.collection('questions').document(question_id).delete()
    return redirect(url_for('manage_questions'))

@app.route('/admin/manage_students')
def manage_students():
    """Renders the student management page."""
    if not check_admin():
        return "Access Denied", 403
    
    users_ref = db.collection('users').stream()
    students = [doc.to_dict() for doc in users_ref if not doc.to_dict().get('is_admin')]
    return render_template('manage_students.html', students=students)

@app.route('/admin/set_instructions', methods=['GET', 'POST'])
def set_instructions():
    """Handles setting the quiz instructions."""
    if not check_admin():
        return "Access Denied", 403

    settings_ref = db.collection('settings').document('quiz_settings')
    settings_doc = settings_ref.get()
    settings = settings_doc.to_dict() if settings_doc.exists else {'instructions': 'No instructions set.'}

    if request.method == 'POST':
        new_instructions = request.form['instructions']
        settings_ref.set({'instructions': new_instructions}, merge=True)
        return redirect(url_for('admin_dashboard'))

    return render_template('set_instructions.html', instructions=settings['instructions'])

# --- CORRECTED set_quiz_timer function ---
@app.route('/admin/set_quiz_timer', methods=['GET', 'POST'])
def set_quiz_timer():
    """Handles setting the quiz timer."""
    # Only allow authenticated users (e.g., admin)
    if not check_admin():
        return "Access Denied", 403

    # Fetch the current timer value from the dedicated 'quiz_timer' document
    settings_doc = settings_doc_ref.get()
    
    if settings_doc.exists:
        current_timer_minutes = settings_doc.to_dict().get('duration_minutes', 30) # Default to 30 minutes if field doesn't exist
    else:
        # If the document doesn't exist, create it with a default value
        settings_doc_ref.set({'duration_minutes': 30})
        current_timer_minutes = 30
        
    if request.method == 'POST':
        try:
            # Get the new duration from the form
            new_duration = int(request.form['duration_minutes'])
            if new_duration > 0:
                # Update the timer in Firestore
                settings_doc_ref.update({'duration_minutes': new_duration})
                flash('Quiz timer updated successfully!', 'success')
            else:
                flash('Please enter a valid duration greater than 0.', 'error')
        except (ValueError, KeyError):
            flash('Invalid input. Please enter a number.', 'error')
        
        return redirect(url_for('admin_dashboard'))

    # Pass the correctly retrieved timer value to the template
    return render_template('set_quiz_timer.html', timer=current_timer_minutes)

if __name__ == '__main__':
    app.run(debug=True)


# --- Initialize Firebase Admin SDK ---
try:
    # Check for the environment variable first
    firebase_credentials_json = os.environ.get('FIREBASE_CREDENTIALS')
    if firebase_credentials_json:
        cred = credentials.Certificate(json.loads(firebase_credentials_json))
        firebase_admin.initialize_app(cred)
    else:
        # Fallback to local file if not on Render
        cred = credentials.Certificate("cbt-platform-8910c-firebase-adminsdk-fbsvc-73e123dcd5.json")
        firebase_admin.initialize_app(cred)
except ValueError:
    pass
