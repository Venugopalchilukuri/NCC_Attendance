import os
import csv
import json
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


# Custom Jinja2 filter: parse stored JSON string -> Python list
@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value) if value else []
    except Exception:
        return []

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

def upload_to_supabase_storage(file, filename, bucket_name=NOC_BUCKET):
    """Upload a file object to Supabase Storage and return its public URL."""
    try:
        file.seek(0)
        file_bytes = file.read()
        print(f"DEBUG: Uploading file to {bucket_name} as {filename}")
        
        # Simple upload call to capture any specific storage error
        res = supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=file_bytes,
            file_options={"content-type": file.content_type, "upsert": "true"}
        )
        
        # Check if the response indicates success
        if hasattr(res, 'error') and res.error:
            err_msg = str(res.error)
            print(f"DEBUG: STORAGE UPLOAD ERROR - {err_msg}")
            flash(f"Storage Upload Error: {err_msg}", "danger")
            return ""

        public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
        print(f"DEBUG: File uploaded successfully. URL: {public_url}")
        return public_url
    except Exception as e:
        err_msg = str(e)
        print(f"DEBUG: STORAGE EXCEPTION during upload for {filename}: {err_msg}")
        flash(f"Storage Upload Error: {err_msg}", "danger")
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
    students = supabase.table("students").select("id").execute().data
    total_students = len(students)

    # Fetch ALL attendance records in one call
    all_attendance = fetch_all_records(supabase.table("attendance").select("student_id, date, status"))

    today_str = datetime.today().strftime('%Y-%m-%d')
    today_attendance = sum(1 for a in all_attendance if a['date'] == today_str and a['status'] == 'Present')

    # Unique days
    distinct_dates = set(a['date'] for a in all_attendance)
    total_days = len(distinct_dates)

    # Count students below 75% — computed in memory, no extra API calls
    below_75_count = 0
    if total_days > 0:
        present_per_student = {}
        for a in all_attendance:
            if a['status'] == 'Present':
                present_per_student[a['student_id']] = present_per_student.get(a['student_id'], 0) + 1
        for student in students:
            present_days = present_per_student.get(student['id'], 0)
            if (present_days / total_days) * 100 < 75:
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
        students = supabase.table("students").select("*").ilike("reg_id", f"%{search_query}%").execute().data
    else:
        students = supabase.table("students").select("*").execute().data

    # Fetch ALL attendance in one call and compute in memory
    all_attendance = fetch_all_records(supabase.table("attendance").select("student_id, date, status"))
    distinct_dates = set(a['date'] for a in all_attendance)
    total_days = len(distinct_dates)

    # Build present-count per student in memory
    present_per_student = {}
    for a in all_attendance:
        if a['status'] == 'Present':
            present_per_student[a['student_id']] = present_per_student.get(a['student_id'], 0) + 1

    for s in students:
        s['_id'] = s['id']  # template compatibility
        present_days = present_per_student.get(s['id'], 0)
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
        blood_group = request.form.get('blood_group', '').strip()
        academic_year = request.form.get('academic_year', '').strip()
        
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
            if not file_path:
                flash('NOC upload failed, but student was added.', 'warning')
            
        student_doc = {
            "name": name,
            "reg_id": reg_id,
            "parent_name": parent_name,
            "mobile": mobile,
            "parent_mobile": parent_mobile,
            "noc_form": file_path,
            "blood_group": blood_group,
            "academicYear": academic_year if academic_year else None
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
        blood_group = request.form.get('blood_group', '').strip()
        academic_year = request.form.get('academic_year', '').strip()
        
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
            "parent_mobile": parent_mobile,
            "blood_group": blood_group,
            "academicYear": academic_year if academic_year else None
        }
        
        if noc_form and noc_form.filename != '':
            filename = secure_filename(noc_form.filename)
            filename = f"{reg_id}_{filename}"
            url = upload_to_supabase_storage(noc_form, filename)
            if url:
                update_data["noc_form"] = url
            else:
                flash('NOC upload failed, but student details were updated.', 'warning')
            
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
    academic_year = request.args.get('academic_year', '')
    
    if request.method == 'POST':
        selected_date = request.form.get('date')
        posted_academic_year = request.form.get('academic_year', '')
        
        # Iterate over all form fields to find attendance selections
        for key, value in request.form.items():
            if key.startswith('attendance_'):
                # value is "student_id:status"
                try:
                    stu_id_str, status = value.split(':', 1)
                    
                    # Upsert into attendance table
                    supabase.table("attendance").upsert({
                        "student_id": stu_id_str, 
                        "date": selected_date,
                        "status": status
                    }, on_conflict="student_id,date").execute()
                except ValueError:
                    continue
            
        flash(f'Attendance saved for {selected_date}', 'success')
        return redirect(url_for('mark_attendance', date=selected_date, academic_year=posted_academic_year))
        
    students = supabase.table("students").select("*").execute().data
    
    # Filter by academic year if specified (blank = All Students)
    if academic_year:
        students = [s for s in students if s.get('academicYear') == academic_year]
    
    # Get current attendance for the selected date to prepopulate form
    existing_attendance = supabase.table("attendance").select("*").eq("date", date_str).execute().data
    attendance_map = {str(record['student_id']): record['status'] for record in existing_attendance}
    
    for s in students:
        s['_id'] = s['id']
        s_id_str = str(s['id'])
        s['current_status'] = attendance_map.get(s_id_str, None)
        
    return render_template('attendance.html', students=students, date=date_str, academic_year=academic_year)

@app.route('/reports', methods=['GET'])
@login_required
def reports():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    academic_year = request.args.get('academic_year', '')
    
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
    
    # Filter by academic year if specified
    if academic_year:
        students = [s for s in students if s.get('academicYear') == academic_year]
    
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
                           total_days=total_days,
                           academic_year=academic_year)

@app.route('/api/chart_data')
@login_required
def chart_data():
    academic_year = request.args.get('academic_year', '').strip()

    # ── 1. Determine the student pool ─────────────────────────────────
    all_students = fetch_all_records(supabase.table("students").select("id, academicYear"))

    if academic_year:
        # Filter to only students in the requested year
        scoped_students = [s for s in all_students if s.get('academicYear') == academic_year]
    else:
        scoped_students = all_students

    total_in_scope = len(scoped_students)

    # Return empty response if no students found for selected year
    if total_in_scope == 0:
        return jsonify({"labels": [], "data": [], "empty": True})

    scoped_ids = set(s['id'] for s in scoped_students)

    # ── 2. Fetch attendance for scoped students only ───────────────────
    all_attendance = fetch_all_records(
        supabase.table("attendance").select("student_id, date, status")
    )

    # Filter attendance to only records belonging to scoped students
    scoped_attendance = [a for a in all_attendance if a['student_id'] in scoped_ids]

    # ── 3. Determine last 30 unique dates with any scoped attendance ───
    dates = sorted(set(a['date'] for a in scoped_attendance))[-30:]

    if not dates:
        return jsonify({"labels": [], "data": [], "empty": True})

    # ── 4. Compute attendance percentage per date ──────────────────────
    present_per_date = {}
    for a in scoped_attendance:
        if a['status'] == 'Present':
            present_per_date[a['date']] = present_per_date.get(a['date'], 0) + 1

    percentages = [
        round((present_per_date.get(d, 0) / total_in_scope) * 100, 1)
        for d in dates
    ]

    return jsonify({
        "labels": dates,
        "data": percentages,
        "empty": False
    })

def fetch_all_records(query_builder, page_size=1000):
    """Fetch all rows from Supabase bypassing the default row limit using pagination."""
    all_data = []
    offset = 0
    while True:
        result = query_builder.range(offset, offset + page_size - 1).execute()
        batch = result.data
        if not batch:
            break
        all_data.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_data


@app.route('/export_csv')
@login_required
def export_csv():
    # ── 1. Get Filters from Request ────────────────────────────────────
    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')
    student_filter = request.args.get('student_name', '').strip().lower()
    academic_year  = request.args.get('academic_year', '').strip()

    # ── 2. Fetch students and apply name / academic year filters ───────
    students_res = fetch_all_records(supabase.table("students").select("*"))
    if student_filter:
        students_res = [s for s in students_res
                        if student_filter in s.get('name', '').lower()]
    if academic_year:
        students_res = [s for s in students_res
                        if s.get('academicYear') == academic_year]
    
    students_map = {s['id']: s for s in students_res}
    target_student_ids = set(students_map.keys())

    # ── 3. Fetch attendance records with date filters ──────────────────
    query = supabase.table("attendance").select("*").order("date", desc=False)
    if start_date:
        query = query.gte("date", start_date)
    if end_date:
        query = query.lte("date", end_date)

    records = fetch_all_records(query)

    # Filter records to only include students in our filtered list
    if student_filter:
        records = [r for r in records if r['student_id'] in target_student_ids]

    # Debugging: Log counts
    print(f"DEBUG: Exporting CSV - Students: {len(students_res)}, Records: {len(records)}")

    # ── 4. Build pivot: { student_id: { date_str: status } } ──────────────
    # Columns are all unique dates found in the (filtered) records
    all_dates = sorted(set(r['date'] for r in records))   

    pivot = {}   # { student_id: { "YYYY-MM-DD": "Present"/"Absent" } }
    for r in records:
        sid = r['student_id']
        pivot.setdefault(sid, {})[r['date']] = r['status']

    # ── 5. Write pivot CSV ────────────────────────────────────────────────
    output = io.StringIO()
    writer = csv.writer(output)

    # Header: Reg ID, Name, [Dates...]
    writer.writerow(['Reg ID', 'Name'] + all_dates)

    # Rows: Sorted by Reg ID
    for s in sorted(students_res, key=lambda x: x.get('reg_id', '')):
        sid = s['id']
        student_dates = pivot.get(sid, {})
        row = [s.get('reg_id', ''), s.get('name', '')]
        for date in all_dates:
            status = student_dates.get(date, 'NA')
            row.append(status)
        writer.writerow(row)

    output.seek(0)

    # ── 6. Filename ──────────────────────────────────────────────────────
    if start_date or end_date or student_filter:
        filename = "attendance_pivot_filtered.csv"
    else:
        filename = "attendance_pivot_full.csv"

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),  # BOM for Excel
        mimetype='text/csv',
        download_name=filename,
        as_attachment=True
    )


@app.route('/api/attendance_data')
@login_required
def attendance_data_api():
    """JSON API: returns all attendance records with student details for custom exports/filtering."""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    student_name = request.args.get('student_name', '').strip().lower()

    students_res = fetch_all_records(supabase.table("students").select("*"))
    students_map = {s['id']: s for s in students_res}

    query = supabase.table("attendance").select("*").order("date", desc=True)
    if start_date:
        query = query.gte("date", start_date)
    if end_date:
        query = query.lte("date", end_date)

    records = fetch_all_records(query)

    result = []
    for r in records:
        student = students_map.get(r['student_id'])
        if not student:
            continue
        if student_name and student_name not in student.get('name', '').lower():
            continue
        result.append({
            'date': r['date'],
            'reg_id': student.get('reg_id', ''),
            'name': student.get('name', ''),
            'status': r['status']
        })

    return jsonify({'total': len(result), 'records': result})

# ════════════════════════════════════════════════════════════
# CAMPS MODULE
# ════════════════════════════════════════════════════════════
CAMP_PHOTOS_BUCKET = "camp-photos"

def upload_camp_photo(file, camp_id):
    """Upload a camp photo to Supabase Storage and return its public URL."""
    try:
        filename = secure_filename(file.filename)
        path = f"{camp_id}/{filename}"
        
        # Ensure we are at the beginning of the file stream
        file.seek(0)
        file_bytes = file.read()
        
        print(f"DEBUG: Attempting to upload {filename} to bucket {CAMP_PHOTOS_BUCKET}")
        
        # Simple upload call to capture any specific storage error
        res = supabase.storage.from_(CAMP_PHOTOS_BUCKET).upload(
            path=path,
            file=file_bytes,
            file_options={"content-type": file.content_type, "upsert": "true"}
        )
        
        # Check if the response indicates success
        if hasattr(res, 'error') and res.error:
            print(f"DEBUG: STORAGE UPLOAD ERROR - {res.error}")
            return ""

        public_url = supabase.storage.from_(CAMP_PHOTOS_BUCKET).get_public_url(path)
        print(f"DEBUG: Photo uploaded successfully. URL: {public_url}")
        return public_url
    except Exception as e:
        print(f"DEBUG: EXCEPTION during upload for {file.filename}: {e}")
        return ""


@app.route('/camps')
@login_required
def camps_list():
    """Show all camps in a card grid."""
    camps = supabase.table("camps").select("*").order("start_date", desc=True).execute().data
    # Attach first photo for thumbnail
    for camp in camps:
        photos = supabase.table("camp_photos").select("photo_url").eq("camp_id", camp['id']).limit(1).execute().data
        camp['thumbnail'] = photos[0]['photo_url'] if photos else None
    return render_template('camps.html', camps=camps)


@app.route('/camps/new', methods=['GET', 'POST'])
@login_required
def new_camp():
    """Create a new camp with optional photo uploads."""
    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        location     = request.form.get('location', '').strip()
        start_date   = request.form.get('start_date', '').strip()
        end_date     = request.form.get('end_date', '').strip()
        description  = request.form.get('description', '').strip()
        total_cadets = request.form.get('total_cadets', '').strip()
        num_sds      = request.form.get('num_sds', '0').strip()
        num_sws      = request.form.get('num_sws', '0').strip()
        # Collect dynamic name fields into lists
        sds_count = int(num_sds) if num_sds.isdigit() else 0
        sws_count = int(num_sws) if num_sws.isdigit() else 0
        sds_names = [request.form.get(f'sds_name_{i}', '').strip() for i in range(1, sds_count + 1)]
        sws_names = [request.form.get(f'sws_name_{i}', '').strip() for i in range(1, sws_count + 1)]

        if not name:
            flash('Camp name is required.', 'danger')
            return redirect(url_for('new_camp'))

        # Insert camp record
        res = supabase.table("camps").insert({
            "camp_name": name,
            "location": location,
            "start_date": start_date or None,
            "end_date": end_date or None,
            "description": description,
            "total_cadets": int(total_cadets) if total_cadets.isdigit() else None,
            "num_sds": sds_count,
            "num_sws": sws_count,
            "sds_names": json.dumps(sds_names),
            "sws_names": json.dumps(sws_names),
        }).execute()

        print(f"DEBUG: Camp insert response data: {res.data}")

        if not res.data:
            print("DEBUG: Camp insertion failed or returned no data")
            flash('Failed to create camp record.', 'danger')
            return redirect(url_for('new_camp'))

        camp_id = str(res.data[0]['id'])
        print(f"DEBUG: Camp created with ID: {camp_id}")

        # Upload photos
        photos = request.files.getlist('photos')
        upload_errors = []
        for photo in photos:
            if photo and photo.filename:
                url = upload_camp_photo(photo, camp_id)
                if url:
                    # Log if the database insert fails
                    db_res = supabase.table("camp_photos").insert({
                        "camp_id": camp_id,
                        "photo_url": url
                    }).execute()
                    if not db_res.data:
                        upload_errors.append(f"Database error saving {photo.filename}")
                else:
                    upload_errors.append(f"Storage error uploading {photo.filename}")

        if upload_errors:
            for err in upload_errors:
                flash(err, 'warning')
            flash(f'Camp "{name}" created, but some photos failed to upload.', 'warning')
        else:
            flash(f'Camp "{name}" created successfully with photos!', 'success')
            
        return redirect(url_for('camps_list'))

    return render_template('new_camp.html')


@app.route('/camps/<string:camp_id>')
@login_required
def view_camp(camp_id):
    """View a single camp with its photo gallery."""
    print(f"DEBUG: Viewing camp with ID: {camp_id}")
    res = supabase.table("camps").select("*").eq("id", camp_id).execute()
    if not res.data:
        print(f"DEBUG: No camp found with ID: {camp_id}")
        flash('Camp not found.', 'danger')
        return redirect(url_for('camps_list'))
    camp = res.data[0]
    photos = supabase.table("camp_photos").select("*").eq("camp_id", camp_id).execute().data
    return render_template('view_camp.html', camp=camp, photos=photos)


@app.route('/camps/<string:camp_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_camp(camp_id):
    """Edit an existing camp and upload more photos."""
    res = supabase.table("camps").select("*").eq("id", camp_id).execute()
    if not res.data:
        flash('Camp not found.', 'danger')
        return redirect(url_for('camps_list'))
    camp = res.data[0]

    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        location     = request.form.get('location', '').strip()
        start_date   = request.form.get('start_date', '').strip()
        end_date     = request.form.get('end_date', '').strip()
        description  = request.form.get('description', '').strip()
        total_cadets = request.form.get('total_cadets', '').strip()
        num_sds      = request.form.get('num_sds', '0').strip()
        num_sws      = request.form.get('num_sws', '0').strip()
        
        sds_count = int(num_sds) if num_sds.isdigit() else 0
        sws_count = int(num_sws) if num_sws.isdigit() else 0
        sds_names = [request.form.get(f'sds_name_{i}', '').strip() for i in range(1, sds_count + 1)]
        sws_names = [request.form.get(f'sws_name_{i}', '').strip() for i in range(1, sws_count + 1)]

        if not name:
            flash('Camp name is required.', 'danger')
            return redirect(url_for('edit_camp', camp_id=camp_id))

        # Update camp record
        supabase.table("camps").update({
            "camp_name": name,
            "location": location,
            "start_date": start_date or None,
            "end_date": end_date or None,
            "description": description,
            "total_cadets": int(total_cadets) if total_cadets.isdigit() else None,
            "num_sds": sds_count,
            "num_sws": sws_count,
            "sds_names": json.dumps(sds_names),
            "sws_names": json.dumps(sws_names),
        }).eq("id", camp_id).execute()

        # Upload new photos
        photos = request.files.getlist('photos')
        for photo in photos:
            if photo and photo.filename:
                url = upload_camp_photo(photo, camp_id)
                if url:
                    supabase.table("camp_photos").insert({
                        "camp_id": camp_id,
                        "photo_url": url
                    }).execute()

        flash(f'Camp "{name}" updated successfully!', 'success')
        return redirect(url_for('view_camp', camp_id=camp_id))

    photos = supabase.table("camp_photos").select("*").eq("camp_id", camp_id).execute().data
    return render_template('edit_camp.html', camp=camp, photos=photos)


@app.route('/camps/photo/<string:photo_id>/delete', methods=['POST'])
@login_required
def delete_camp_photo(photo_id):
    """Delete a single photo from a camp."""
    res = supabase.table("camp_photos").select("*").eq("id", photo_id).execute()
    if not res.data:
        return jsonify({"success": False, "message": "Photo not found"}), 404
    
    photo = res.data[0]
    camp_id = photo['camp_id']
    
    # Optional: Delete from storage as well (requires parsing filename from URL)
    # For now, just delete from database
    supabase.table("camp_photos").delete().eq("id", photo_id).execute()
    
    return redirect(url_for('edit_camp', camp_id=camp_id))


@app.route('/camps/<string:camp_id>/delete', methods=['POST'])
@login_required
def delete_camp(camp_id):
    """Delete a camp and all its photos."""
    try:
        print(f"DEBUG: Deleting camp with ID: {camp_id}")
        supabase.table("camp_photos").delete().eq("camp_id", camp_id).execute()
        supabase.table("camps").delete().eq("id", camp_id).execute()
        flash('Camp deleted successfully.', 'success')
    except Exception as e:
        print(f"DEBUG: Error deleting camp {camp_id}: {e}")
        flash(f'Error deleting camp: {str(e)}', 'danger')
    return redirect(url_for('camps_list'))


# ── Feedback Routes ───────────────────────────────────────────────

@app.route('/feedback', methods=['GET', 'POST'])
def student_feedback():
    """Public page for students to submit anonymous feedback."""
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        rating  = request.form.get('rating')

        if not message:
            flash('Please enter a message.', 'danger')
            return redirect(url_for('student_feedback'))

        try:
            supabase.table("feedback").insert({
                "message": message,
                "rating": int(rating) if rating and rating.isdigit() else None
            }).execute()
            flash('Thank you for your feedback!', 'success')
            return redirect(url_for('student_feedback'))
        except Exception as e:
            print(f"Feedback error: {e}")
            flash('Error submitting feedback. Please try again.', 'danger')

    return render_template('feedback.html')

@app.route('/api/feedback', methods=['GET'])
@login_required
def get_feedback():
    """Admin-only API to fetch all feedback."""
    try:
        res = supabase.table("feedback").select("*").order("created_at", desc=True).execute()
        return jsonify(res.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/feedback/<string:fid>/delete', methods=['POST'])
@login_required
def delete_feedback(fid):
    """Admin-only route to delete feedback."""
    try:
        supabase.table("feedback").delete().eq("id", fid).execute()
        flash('Feedback deleted.', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('dashboard'))


# ── Resources Routes ───────────────────────────────────────────────

@app.route('/resources')
@login_required
def view_resources():
    resources = supabase.table("resources").select("*").order("uploaded_at", desc=True).execute().data
    for resource in resources:
        if 'uploaded_at' in resource and resource['uploaded_at']:
            resource['uploaded_at'] = resource['uploaded_at'].split('T')[0]
    
    subjects = list(set([r['subject'] for r in resources if r.get('subject')]))
    
    return render_template('resources.html', resources=resources, subjects=subjects)


@app.route('/resources/upload', methods=['GET', 'POST'])
@login_required
def upload_resource():
    if request.method == 'POST':
        title = request.form.get('title')
        subject = request.form.get('subject')
        description = request.form.get('description', '')
        year_semester = request.form.get('year_semester', '')
        file = request.files.get('file')
        
        if not file or file.filename == '':
            flash('Please select a file to upload.', 'danger')
            return redirect(url_for('upload_resource'))
        
        allowed_extensions = {'pdf', 'docx', 'ppt', 'png', 'jpg', 'jpeg'}
        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if file_ext not in allowed_extensions:
            flash('Invalid file type. Allowed types: PDF, DOCX, PPT, PNG, JPG.', 'danger')
            return redirect(url_for('upload_resource'))
        
        filename = f"{datetime.now().timestamp()}_{secure_filename(file.filename)}"
        file_url = upload_to_supabase_storage(file, filename, bucket_name='resources')
        
        if not file_url:
            flash('Error uploading file to storage.', 'danger')
            return redirect(url_for('upload_resource'))
        
        resource_doc = {
            "title": title,
            "subject": subject,
            "description": description,
            "year_semester": year_semester,
            "filename": filename,
            "file_url": file_url,
            "file_type": file_ext.upper()
        }
        
        try:
            supabase.table("resources").insert(resource_doc).execute()
            flash('Resource uploaded successfully!', 'success')
        except Exception as e:
            flash(f'Error saving resource: {str(e)}', 'danger')
            
        return redirect(url_for('view_resources'))
        
    return render_template('upload_resource.html')


@app.route('/resources/delete/<resource_id>', methods=['POST'])
@login_required
def delete_resource(resource_id):
    try:
        supabase.table("resources").delete().eq("id", resource_id).execute()
        flash('Resource deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting resource: {str(e)}', 'danger')
    return redirect(url_for('view_resources'))


@app.route('/api/resources', methods=['GET'])
@login_required
def api_get_resources():
    resources = supabase.table("resources").select("*").order("uploaded_at", desc=True).execute().data
    return jsonify(resources)


@app.route('/api/resources', methods=['POST'])
@login_required
def api_upload_resource():
    data = request.form
    file = request.files.get('file')
    
    filename = f"{datetime.now().timestamp()}_{secure_filename(file.filename)}"
    file_url = upload_to_supabase_storage(file, filename, bucket_name='resources')
    
    resource_doc = {
        "title": data.get('title'),
        "subject": data.get('subject'),
        "description": data.get('description', ''),
        "year_semester": data.get('year_semester', ''),
        "filename": filename,
        "file_url": file_url,
        "file_type": file.filename.rsplit('.', 1)[1].upper() if '.' in file.filename else ''
    }
    
    supabase.table("resources").insert(resource_doc).execute()
    return jsonify({"success": True, "resource": resource_doc})


@app.route('/api/resources/<resource_id>', methods=['DELETE'])
@login_required
def api_delete_resource(resource_id):
    try:
        supabase.table("resources").delete().eq("id", resource_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
