# NCC Attendance Management System

A Flask-based web application for managing NCC (National Cadet Corps) student attendance, reports, and NOC form uploads, integrated with Supabase for database and storage.

## Features

- **Admin Dashboard**: Overview of total students and today's attendance.
- **Student Management**: Add, edit, and view student details.
- **Attendance Tracking**: Mark and track daily attendance for cadets.
- **Reports**: Generate and view attendance reports, including identifying students with low attendance.
- **NOC Form Uploads**: Securely upload and store NOC (No Objection Certificate) forms using Supabase Storage.
- **Authentication**: Secure login system for administrators.

## Tech Stack

- **Backend**: Python, Flask
- **Database & Storage**: Supabase (PostgreSQL & Storage)
- **Environment Management**: python-dotenv
- **Frontend**: HTML, Jinja2 Templates

## Getting Started

### Prerequisites

- Python 3.x
- A Supabase account and project

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Venugopalchilukuri/NCC_Attendance.git
   cd NCC_Attendance
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   Create a `.env` file in the root directory and add your Supabase credentials:
   ```env
   SUPABASE_URL=your_supabase_project_url
   SUPABASE_KEY=your_supabase_anon_key
   ```

5. **Initialize the database**:
   The application automatically creates a default admin user (`Nagateja`) on the first run.

### Running the App

```bash
python app.py
```
The application will be available at `http://127.0.0.1:5000`.

## Project Structure

- `app.py`: Main application logic and routes.
- `templates/`: HTML templates for the web interface.
- `requirements.txt`: List of Python dependencies.
- `.gitignore`: Specifies files to be ignored by Git.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
