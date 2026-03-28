import os
import csv
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import io
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = 'ncc_attendance_secret_key'

# Setup MongoDB
# Setup Supabase
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# Create default admin if not exists
def init_db():
    check = supabase.table("admins").select("*").eq("username", "Nagateja").execute()
    if len(check.data) == 0:
        supabase.table("admins").insert({
            "username": "Nagateja",
            "password": generate_password_hash("Nagateja@123")
        }).execute()

init_db()

# Supabase Storage bucket name for NOC forms
NOC_BUCKET = "noc-forms"

def upload_to_supabase_storage(file, filename):
    """Upload a file object to Supabase Storage and return its public URL."""
    try:
        file_bytes = file.read()
        supabase.storage.from_(NOC_BUCKET).upload(
            path=filename,
            file=file_bytes,
            file_options={"content-type": file.content_type, "upsert": "true"}
        )
        public_url = supabase.storage.from_(NOC_BUCKET).get_public_url(filename)
        return public_url
    except Exception as e:
        print(f"Storage upload error: {e}")
        return ""

# Login decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in first.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if 'admin_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        response = supabase.table("admins").select("*").eq("username", username).execute()
        if response.data and check_password_hash(response.data[0]['password'], password):
            session['admin_id'] = str(response.data[0]['id'])
            session['username'] = response.data[0]['username']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    total_students = len(supabase.table("students").select("id").execute().data)
    
    today_str = datetime.today().strftime('%Y-%m-%d')
    today_attendance = len(supabase.table("attendance").select("id").eq("date", today_str).eq("status", "Present").execute().data)
    
    # Calculate students below 75%
    # First, get total unique days attendance was marked
    distinct_dates_res = supabase.table("attendance").select("date").execute()
    distinct_dates = list(set([d['date'] for d in distinct_dates_res.data]))
    total_days = len(distinct_dates)
    
    below_75_count = 0
    if total_days > 0:
        students = supabase.table("students").select("id").execute().data
        for student in students:
            # Calculate their present days
            present_days = len(supabase.table("attendance").select("id").eq("student_id", student['id']).eq("status", "Present").execute().data)
            percentage = (present_days / total_days) * 100
            if percentage < 75:
                below_75_count += 1
                
    return render_template('dashboard.html', 
                           total_students=total_students, 
                           today_attendance=today_attendance,
                           below_75_count=below_75_count)

@app.route('/students')
@login_required
def students_list():
    search_query = request.args.get('search', '')
    
    if search_query:
        # Supabase ilike is case insensitive
        students = supabase.table("students").select("*").ilike("reg_id", f"%{search_query}%").execute().data
    else:
        students = supabase.table("students").select("*").execute().data
        
    distinct_dates_res = supabase.table("attendance").select("date").execute()
    distinct_dates = list(set([d['date'] for d in distinct_dates_res.data]))
    total_days = len(distinct_dates)
    
    for s in students:
        s['_id'] = s['id'] # template compatibility
        present_days = len(supabase.table("attendance").select("id").eq("student_id", s['id']).eq("status", "Present").execute().data)
        if total_days > 0:
            s['attendance_percentage'] = round((present_days / total_days) * 100, 2)
        else:
            s['attendance_percentage'] = 0.0

    return render_template('students.html', students=students, search_query=search_query)

@app.route('/student/add', methods=['GET', 'POST'])
@login_required
def add_student():
    if request.method == 'POST':
        name = request.form.get('name')
        reg_id = request.form.get('reg_id')
        parent_name = request.form.get('parent_name')
        mobile = request.form.get('mobile')
        parent_mobile = request.form.get('parent_mobile')
        noc_form = request.files.get('noc_form')
        
        # Check duplicate reg_id
        check = supabase.table("students").select("id").eq("reg_id", reg_id).execute()
        if len(check.data) > 0:
            flash('A student with this Registration ID already exists!', 'danger')
            return redirect(url_for('add_student'))
            
        file_path = ""
        if noc_form and noc_form.filename != '':
            filename = secure_filename(noc_form.filename)
            filename = f"{reg_id}_{filename}"
            file_path = upload_to_supabase_storage(noc_form, filename)
            
        student_doc = {
            "name": name,
            "reg_id": reg_id,
            "parent_name": parent_name,
            "mobile": mobile,
            "parent_mobile": parent_mobile,
            "noc_form": file_path
        }
        supabase.table("students").insert(student_doc).execute()
        flash('Student added successfully!', 'success')
        return redirect(url_for('students_list'))
        
    return render_template('add_student.html')

@app.route('/student/edit/<student_id>', methods=['GET', 'POST'])
@login_required
def edit_student(student_id):
    res = supabase.table("students").select("*").eq("id", student_id).execute()
    if not res.data:
        flash('Student not found.', 'danger')
        return redirect(url_for('students_list'))
    
    student = res.data[0]
        
    if request.method == 'POST':
        name = request.form.get('name')
        reg_id = request.form.get('reg_id')
        parent_name = request.form.get('parent_name')
        mobile = request.form.get('mobile')
        parent_mobile = request.form.get('parent_mobile')
        noc_form = request.files.get('noc_form')
        
        # Check duplicate reg_id (if changed)
        if reg_id != student.get('reg_id'):
            check = supabase.table("students").select("id").eq("reg_id", reg_id).execute()
            if len(check.data) > 0:
                flash('Registration ID already in use!', 'danger')
                return redirect(url_for('edit_student', student_id=student_id))
            
        update_data = {
            "name": name,
            "reg_id": reg_id,
            "parent_name": parent_name,
            "mobile": mobile,
            "parent_mobile": parent_mobile
        }
        
        if noc_form and noc_form.filename != '':
            filename = secure_filename(noc_form.filename)
            filename = f"{reg_id}_{filename}"
            update_data["noc_form"] = upload_to_supabase_storage(noc_form, filename)
            
        supabase.table("students").update(update_data).eq("id", student_id).execute()
        flash('Student updated successfully!', 'success')
        return redirect(url_for('students_list'))
        
    return render_template('edit_student.html', student=student)

@app.route('/student/delete/<student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    try:
        supabase.table("students").delete().eq("id", student_id).execute()
        flash('Student deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting student: {str(e)}', 'danger')
    return redirect(url_for('students_list'))

@app.route('/attendance', methods=['GET', 'POST'])
@login_required
def mark_attendance():
    date_str = request.args.get('date') or datetime.today().strftime('%Y-%m-%d')
    
    if request.method == 'POST':
        # Retrieve form data
        selected_date = request.form.get('date')
        attendance_data = request.form.getlist('attendance_status') # List of "student_id:status"
        
        for item in attendance_data:
            stu_id_str, status = item.split(':', 1)
            
            # Upsert into attendance collection
            supabase.table("attendance").upsert({
                "student_id": stu_id_str, 
                "date": selected_date,
                "status": status
            }, on_conflict="student_id,date").execute()
            
        flash(f'Attendance saved for {selected_date}', 'success')
        return redirect(url_for('mark_attendance', date=selected_date))
        
    students = supabase.table("students").select("*").execute().data
    
    # Get current attendance for the selected date to prepopulate form
    existing_attendance = supabase.table("attendance").select("*").eq("date", date_str).execute().data
    attendance_map = {str(record['student_id']): record['status'] for record in existing_attendance}
    
    for s in students:
        s['_id'] = s['id']
        s_id_str = str(s['id'])
        s['current_status'] = attendance_map.get(s_id_str, None)
        
    return render_template('attendance.html', students=students, date=date_str)

@app.route('/reports', methods=['GET'])
@login_required
def reports():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = supabase.table("attendance").select("date, student_id, status")
    if start_date:
        query = query.gte("date", start_date)
    if end_date:
        query = query.lte("date", end_date)
        
    all_attendance = query.execute().data
        
    # Get unique dates in this range
    dates_in_range = sorted(list(set([d['date'] for d in all_attendance])))
    total_days = len(dates_in_range)
    
    students = supabase.table("students").select("*").execute().data
    report_data = []
    
    for s in students:
        # Count presents in this date range manually from memory to save API calls
        present_count = sum(1 for a in all_attendance if a['student_id'] == s['id'] and a['status'] == 'Present')
        percentage = round((present_count / total_days * 100), 2) if total_days > 0 else 0
        
        report_data.append({
            "student": s,
            "present_count": present_count,
            "percentage": percentage
        })
        
    return render_template('reports.html', 
                           report_data=report_data, 
                           start_date=start_date, 
                           end_date=end_date,
                           total_days=total_days)

@app.route('/api/chart_data')
@login_required
def chart_data():
    # Last 30 days attendance trends
    distinct_dates_res = supabase.table("attendance").select("date").execute()
    dates = sorted(list(set([d['date'] for d in distinct_dates_res.data])))[-30:]
    present_counts = []
    
    for d in dates:
        count = len(supabase.table("attendance").select("id").eq("date", d).eq("status", "Present").execute().data)
        present_counts.append(count)
        
    return jsonify({
        "labels": dates,
        "data": present_counts
    })

@app.route('/export_csv')
@login_required
def export_csv():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = supabase.table("attendance").select("*").order("date")
    if start_date:
        query = query.gte("date", start_date)
    if end_date:
        query = query.lte("date", end_date)
        
    records = query.execute().data
    
    # output in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Reg ID', 'Name', 'Status'])
    
    # Optimize by fetching all students into a map
    students_res = supabase.table("students").select("*").execute().data
    students = {s['id']: s for s in students_res}
    
    for r in records:
        student = students.get(r['student_id'])
        if student:
            writer.writerow([r['date'], student.get('reg_id', ''), student.get('name', ''), r['status']])
            
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        download_name='attendance_sheet.csv',
        as_attachment=True
    )

if __name__ == '__main__':
    app.run(debug=True, port=5000)
    
