# C:\Users\Agba Xchanger\cbt_platform\app.py

from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, make_response, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json
import csv
import io
import datetime

# Initialize Flask app
app = Flask(__name__)
# IMPORTANT: Use a strong, unique secret key in a production environment
app.secret_key = 'your_secret_key_here'

# --- NEW: Set the secret code for admin sign-up ---
ADMIN_SECRET_CODE = 'administrator'

# --- Initialize Firebase Admin SDK ---
try:
    # Check for the environment variable first (for deployment environments like Render)
    firebase_credentials_json = os.environ.get('FIREBASE_CREDENTIALS')
    if firebase_credentials_json:
        cred = credentials.Certificate(json.loads(firebase_credentials_json))
        firebase_admin.initialize_app(cred)
    else:
        # Fallback to local file if not on a deployment environment
        cred = credentials.Certificate("cbt-platform-8910c-firebase-adminsdk-fbsvc-73e123dcd5.json")
        firebase_admin.initialize_app(cred)
except ValueError:
    # This handles the case where the app is reloaded without a fresh startup
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
        
        # --- NEW: Password length validation ---
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
            return redirect(url_for('signup'))
            
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
                return redirect(url_for('start_quiz'))
        
        # Use flash to show an error message
        flash('Invalid credentials! Please try again.', 'error')
        return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/start_quiz')
def start_quiz():
    """
    Initializes session variables and redirects to the first question.
    """
    if 'username' not in session:
        flash('Please log in to start a quiz.', 'error')
        return redirect(url_for('login'))
    
    session['current_question_index'] = 0
    session['user_answers'] = {}
    
    # IMPORTANT: Do not set quiz_start_time here. It will be set by the fetch call
    # when the user clicks 'Start Quiz' in the instructions modal.
    session.pop('quiz_start_time', None)
    
    return redirect(url_for('quiz'))

# NEW ROUTE to set the quiz start time in the session
@app.route('/set_quiz_start_time', methods=['POST'])
def set_quiz_start_time():
    """Sets the quiz start time in the session and returns quiz duration."""
    if 'username' not in session:
        return jsonify({'error': 'User not logged in'}), 401
    
    # Set the quiz start time
    session['quiz_start_time'] = datetime.datetime.now().timestamp()
    
    # Fetch total quiz duration from the database
    timer_doc = db.collection('settings').document('quiz_timer').get()
    timer_minutes = timer_doc.to_dict().get('duration_minutes', 30) if timer_doc.exists else 30
    total_duration_seconds = timer_minutes * 60
    
    return jsonify({'total_duration_seconds': total_duration_seconds}), 200


@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    """
    Displays the quiz questions one by one with a continuous timer.
    """
    if 'username' not in session:
        flash('Please log in to continue.', 'error')
        return redirect(url_for('login'))
    
    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    quiz_questions = [doc.to_dict() for doc in questions_docs]
    
    current_question_index = session.get('current_question_index', 0)
    user_answers = session.get('user_answers', {})
    
    # Handle quiz submission logic for POST requests
    if request.method == 'POST':
        question_id = request.form.get('question_id')
        user_answer = request.form.get(question_id)
        
        user_answers[question_id] = user_answer
        session['user_answers'] = user_answers
        
        session['current_question_index'] = current_question_index + 1
        
        # Check if it's the last question, submit if so
        if session['current_question_index'] >= len(quiz_questions):
            return redirect(url_for('submit_quiz'))
        else:
            return redirect(url_for('quiz'))
            
    # Handle GET requests (displaying the page)
    if current_question_index >= len(quiz_questions):
        return redirect(url_for('submit_quiz'))
        
    current_question = quiz_questions[current_question_index]
    
    # Fetch instructions and timer from Firestore
    timer_doc = db.collection('settings').document('quiz_timer').get()
    timer_minutes = timer_doc.to_dict().get('duration_minutes', 30) if timer_doc.exists else 30
    
    instructions_doc = db.collection('settings').document('quiz_settings').get()
    instructions = instructions_doc.to_dict().get('instructions', 'No instructions set.') if instructions_doc.exists else 'No instructions set.'
    
    # Calculate remaining time for the timer. This is the key part.
    remaining_time_seconds = -1  # Default value
    if 'quiz_start_time' in session:
        total_duration_seconds = timer_minutes * 60
        elapsed_time = datetime.datetime.now().timestamp() - session['quiz_start_time']
        remaining_time_seconds = int(total_duration_seconds - elapsed_time)
        
        # If time has run out, redirect to submission
        if remaining_time_seconds <= 0:
            flash('Time is up! Your quiz has been automatically submitted.', 'warning')
            return redirect(url_for('submit_quiz'))

    return render_template('quiz.html', 
                            question=current_question, 
                            current_question_index=current_question_index,
                            total_questions=len(quiz_questions),
                            instructions=instructions,
                            timer_minutes=timer_minutes,
                            remaining_time_seconds=remaining_time_seconds)
    
@app.route('/submit_quiz')
def submit_quiz():
    """Calculates the final score, stores it, and redirects to the results page."""
    if 'username' not in session:
        flash('Please log in to submit a quiz.', 'error')
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
    
    # NEW: Clear the quiz start time from the session
    session.pop('quiz_start_time', None)

    return redirect(url_for('result'))

@app.route('/result')
def result():
    """Displays the user's quiz score."""
    if 'score' not in session:
        flash('You must complete a quiz to see results.', 'error')
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
        flash('Please log in to view answers.', 'error')
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
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('home'))

# --- Admin Routes ---
def check_admin():
    """Helper function to check if the current user is an admin."""
    return session.get('is_admin', False)

@app.route('/admin')
@app.route('/admin/dashboard')
def admin_dashboard():
    """Renders the main admin dashboard."""
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))
    return render_template('admin_dashboard.html')

@app.route('/admin/manage_questions', methods=['GET', 'POST'])
def manage_questions():
    """Handles adding questions."""
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))

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
        flash('Question added successfully!', 'success')
        return redirect(url_for('manage_questions'))
    
    questions_ref = db.collection('questions').order_by('id')
    questions_docs = questions_ref.stream()
    questions = [doc.to_dict() for doc in questions_docs]
    return render_template('manage_questions.html', questions=questions)

@app.route('/admin/edit_question/<question_id>', methods=['GET', 'POST'])
def edit_question(question_id):
    """Edits an existing question in Firestore."""
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))

    question_doc = db.collection('questions').document(question_id).get()
    if not question_doc.exists:
        flash('Question not found.', 'error')
        return redirect(url_for('manage_questions'))
    
    if request.method == 'POST':
        updated_data = {
            'question': request.form['question'],
            'choices': [request.form['choice_A'], request.form['choice_B'], request.form['choice_C'], request.form['choice_D']],
            'answer': request.form['answer']
        }
        db.collection('questions').document(question_id).update(updated_data)
        flash('Question updated successfully!', 'success')
        return redirect(url_for('manage_questions'))
    
    question = question_doc.to_dict()
    return render_template('edit_question.html', question=question)

@app.route('/admin/delete_question/<question_id>', methods=['POST'])
def delete_question(question_id):
    """Deletes a question from Firestore."""
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))

    db.collection('questions').document(question_id).delete()
    flash('Question deleted successfully!', 'success')
    return redirect(url_for('manage_questions'))

@app.route('/admin/manage_students')
def manage_students():
    """Renders the student management page."""
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))
    
    users_ref = db.collection('users').stream()
    students = {}
    for doc in users_ref:
        user_data = doc.to_dict()
        if not user_data.get('is_admin', False):
            students[doc.id] = user_data
    return render_template('manage_students.html', students=students)

@app.route('/admin/set_instructions', methods=['GET', 'POST'])
def set_instructions():
    """Handles setting the quiz instructions."""
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))

    settings_ref = db.collection('settings').document('quiz_settings')
    settings_doc = settings_ref.get()
    settings = settings_doc.to_dict() if settings_doc.exists else {'instructions': 'No instructions set.'}

    if request.method == 'POST':
        new_instructions = request.form['instructions']
        settings_ref.set({'instructions': new_instructions}, merge=True)
        flash('Instructions updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('set_instructions.html', instructions=settings['instructions'])

# --- CORRECTED set_quiz_timer function ---
@app.route('/admin/set_quiz_timer', methods=['GET', 'POST'])
def set_quiz_timer():
    """Handles setting the quiz timer."""
    # Only allow authenticated users (e.g., admin)
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))

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
        
        # Redirect back to the dashboard after a successful update
        return redirect(url_for('admin_dashboard'))

    # Pass the correctly retrieved timer value to the template
    return render_template('set_quiz_timer.html', timer=current_timer_minutes)


# --- UPDATED: Route to export student results as CSV ---
@app.route('/admin/export_results')
def export_results():
    if not check_admin():
        flash('Access Denied: You do not have administrator privileges.', 'error')
        return redirect(url_for('login'))
    
    # Fetch all non-admin users
    users_ref = db.collection('users').stream()
    students_data = []
    for doc in users_ref:
        user_data = doc.to_dict()
        if not user_data.get('is_admin'):
            students_data.append({
                'username': doc.id,
                'results': user_data.get('results', [])
            })
    
    # Create an in-memory text buffer for the CSV data
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Write the CSV header
    cw.writerow(['Student', 'Scores'])
    
    # Write each student's data
    for student in students_data:
        # The username is now correctly part of our student dictionary
        username = student['username']
        scores = student['results']
        # Format the scores array into a single string for the CSV cell
        scores_str = ', '.join(map(str, scores))
        cw.writerow([username, scores_str])
        
    # Create and return the response
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=student_results.csv"
    output.headers["Content-type"] = "text/csv"
    return output

if __name__ == '__main__':
    app.run(debug=True)
