import csv
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response
from models import db, CuttingSession, PaperLotUsageLog
from zoho_integration import fetch_work_orders_from_zoho, create_zoho_coil_cutting_report_entry
import os
from dotenv import load_dotenv
from datetime import datetime
import io
from generate_pdf import generate_session_pdf

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_default_fallback_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///production_tracking.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def get_current_active_lot_log(session_id, lot_number):
    return PaperLotUsageLog.query.filter_by(
        cutting_session_id=session_id,
        paper_lot_number=lot_number,
        end_time_for_lot=None
    ).order_by(PaperLotUsageLog.start_time_for_lot.desc()).first()

@app.route('/')
def index():
    active_sessions = CuttingSession.query.filter(CuttingSession.status != 'Completed').order_by(
        CuttingSession.updated_at.desc()).all()
    return render_template('index.html', active_sessions=active_sessions)

@app.route('/start_work')
def start_work_form():
    work_orders_from_api = []
    try:
        work_orders_from_api = fetch_work_orders_from_zoho()
        if not work_orders_from_api:
            flash("Could not fetch work orders from Zoho or no work orders available. Displaying fallback/manual options.", "warning")
    except Exception as e:
        flash(f"Error fetching work orders: {str(e)}", "danger")

    if work_orders_from_api is None:
        work_orders_from_api = []

    return render_template('start_work_form.html', work_orders=work_orders_from_api)

@app.route('/session/create', methods=['POST'])
def create_session():
    work_order_code = request.form.get('work_order_code')
    model_no = request.form.get('model_no')
    initial_paper_lot = request.form.get('initial_paper_lot_number')

    if work_order_code == 'MANUAL-INPUT-WO':
        manual_wo_code = request.form.get('manual_work_order_code_input_field')
        manual_wo_name = request.form.get('manual_work_order_name_input_field')
        if not manual_wo_code:
            flash("Manual Work Order Code is required.", "danger")
            return redirect(url_for('start_work_form'))
        work_order_code = manual_wo_code

    if not work_order_code or not model_no or not initial_paper_lot:
        flash("Work Order, Model No, and Initial Paper Lot are required.", "danger")
        return redirect(url_for('start_work_form'))

    # Set cutting plan based on model
    if model_no == '4500':
        total_cuts = 68
        cuts_step1 = 34
        cuts_step2 = 34
    elif model_no == '6000':
        total_cuts = 83  # Keep total as 83 (68 + 15)
        cuts_step1 = 68  # Main cuts
        cuts_step2 = 15  # Aux cuts
    else:
        flash(f"Invalid model number selected: {model_no}. Please choose 4500 or 6000.", "danger")
        return redirect(url_for('start_work_form'))

    existing_session = CuttingSession.query.filter_by(work_order_code=work_order_code, status='Ongoing').first()
    if existing_session:
        flash(f"An active session for Work Order {work_order_code} already exists. Please complete it first.", "warning")
        return redirect(url_for('work_interface', session_id=existing_session.id))

    session = CuttingSession(
        work_order_code=work_order_code,
        model_no=model_no,
        active_paper_lot_number=initial_paper_lot,
        total_cuts_planned=total_cuts,
        cuts_planned_step1=cuts_step1,
        cuts_planned_step2=cuts_step2,
        current_step=1,
        status='Ongoing'
    )
    db.session.add(session)
    db.session.commit()

    lot_log = PaperLotUsageLog(
        cutting_session_id=session.id,
        paper_lot_number=initial_paper_lot
    )
    db.session.add(lot_log)
    db.session.commit()

    flash(f"Session for Model {model_no} / WO {work_order_code} started with Lot {initial_paper_lot}.", "success")
    return redirect(url_for('work_interface', session_id=session.id))

@app.route('/session/<int:session_id>')
def work_interface(session_id):
    session = db.session.get(CuttingSession, session_id)
    if not session:
        flash("Session not found.", "danger")
        return redirect(url_for('index'))

    if session.status == 'Completed':
        flash(f"Session **{session.work_order_code}** is already completed.", "info")

    lot_history = PaperLotUsageLog.query.filter_by(cutting_session_id=session.id).order_by(
        PaperLotUsageLog.start_time_for_lot.desc()).all()
    return render_template('work_session.html', session=session, lot_history=lot_history)

@app.route('/session/<int:session_id>/log_cut', methods=['POST'])
def log_cut(session_id):
    session = db.session.get(CuttingSession, session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    if session.status == 'Completed':
        flash("This session is already completed. Cannot log more cuts.", "warning")
        return redirect(url_for('work_interface', session_id=session.id))

    if not session.active_paper_lot_number:
        flash("Error: No active paper lot set for this session. Please change/set lot.", "danger")
        return redirect(url_for('work_interface', session_id=session.id))

    session.total_cuts_completed_for_session += 1
    session.cuts_completed_in_current_step += 1

    current_lot_log = get_current_active_lot_log(session.id, session.active_paper_lot_number)
    if not current_lot_log:
        current_lot_log = PaperLotUsageLog(
            cutting_session_id=session.id,
            paper_lot_number=session.active_paper_lot_number,
            cuts_made_with_this_lot=0
        )
        db.session.add(current_lot_log)
    current_lot_log.cuts_made_with_this_lot += 1

    step_completed_message = None
    session_completed_message = None

    if session.model_no == '4500':
        if session.current_step == 1 and session.cuts_completed_in_current_step >= session.cuts_planned_step1:
            if session.cuts_planned_step2 > 0:
                session.current_step = 2
                session.cuts_completed_in_current_step = 0
                step_completed_message = f"Step 1 completed! Moving to Step 2 for WO **{session.work_order_code}**."
            else:
                session.status = 'Completed'
                session_completed_message = f"Work Order **{session.work_order_code}** completed after Step 1!"
        elif session.current_step == 2 and session.cuts_completed_in_current_step >= session.cuts_planned_step2:
            session.status = 'Completed'
            session_completed_message = f"Step 2 and Work Order **{session.work_order_code}** completed!"
    elif session.model_no == '6000':
        if session.current_step == 1 and session.cuts_completed_in_current_step >= session.cuts_planned_step1:
            if session.cuts_planned_step2 > 0:
                session.current_step = 2
                session.cuts_completed_in_current_step = 0
                step_completed_message = f"Main cuts completed! Moving to Aux cuts for WO **{session.work_order_code}**."
            else:
                session.status = 'Completed'
                session_completed_message = f"Work Order **{session.work_order_code}** completed after Main cuts!"
        elif session.current_step == 2 and session.cuts_completed_in_current_step >= session.cuts_planned_step2:
            session.status = 'Completed'
            session_completed_message = f"Aux cuts and Work Order **{session.work_order_code}** completed!"

    if session.total_cuts_completed_for_session >= session.total_cuts_planned and session.status != 'Completed':
        session.status = 'Completed'
        session_completed_message = session_completed_message or f"Work Order **{session.work_order_code}** reached total planned cuts and is completed!"

    if session.status == 'Completed' and current_lot_log and not current_lot_log.end_time_for_lot:
        current_lot_log.end_time_for_lot = datetime.utcnow()

    db.session.commit()

    if session.status == 'Completed':
        report_dir = os.path.join('static', 'reports')
        os.makedirs(report_dir, exist_ok=True)
        filename = f"report_session_{session.id}.pdf"
        output_path = os.path.join(report_dir, filename)

        print(f"\n--- Debugging Data before Zoho Upload (Session: {session.id}) ---")
        print(f"Session Work Order Code: {session.work_order_code}")
        print(f"Session Model No: {session.model_no}")
        print(f"Session Total Cuts Completed: {session.total_cuts_completed_for_session}")
        print(f"Session Status: {session.status}")
        print(f"Session Current Step: {session.current_step}")

        lot_history = PaperLotUsageLog.query.filter_by(cutting_session_id=session.id).order_by(
            PaperLotUsageLog.start_time_for_lot).all()
        print(f"Lot History (count): {len(lot_history)}")
        for i, lot in enumerate(lot_history):
            print(
                f"  Lot {i + 1}: Lot Number={lot.paper_lot_number}, Cuts={lot.cuts_made_with_this_lot}, Start Time={lot.start_time_for_lot}, End Time={lot.end_time_for_lot}")
        print(f"--- Debugging Data End ---")

        generate_session_pdf(app, session, lot_history, output_path)

        wo_display_name = session.work_order_code

        success = create_zoho_coil_cutting_report_entry(
            session.work_order_code,
            wo_display_name,
            session,
            lot_history
        )
        if success:
            flash("Session completed and **combined report uploaded to Zoho Creator**.", "success")
        else:
            flash(
                "Session completed, but **failed to upload combined report to Zoho Creator**. Check server logs for details.",
                "warning")

    if step_completed_message:
        flash(step_completed_message, "info")
    if session_completed_message:
        flash(session_completed_message, "success")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        response_data = {
            "message": "Cut logged successfully!",
            "total_cuts_session": session.total_cuts_completed_for_session,
            "cuts_in_step": session.cuts_completed_in_current_step,
            "current_step": session.current_step,
            "status": session.status,
            "active_lot": session.active_paper_lot_number,
            "lot_cuts": current_lot_log.cuts_made_with_this_lot if current_lot_log else 0,
            "step_completed_message": step_completed_message,
            "session_completed_message": session_completed_message,
            "model_no": session.model_no
        }
        if session.model_no == '4500' or session.model_no == '6000':
            response_data["cuts_planned_step1"] = session.cuts_planned_step1
            response_data["cuts_planned_step2"] = session.cuts_planned_step2
        return jsonify(response_data)

    if session.status == 'Completed':
        return redirect(url_for('index'))
    return redirect(url_for('work_interface', session_id=session_id))

@app.route('/session/<int:session_id>/change_lot', methods=['POST'])
def change_lot(session_id):
    session = db.session.get(CuttingSession, session_id)
    if not session:
        flash("Session not found.", "danger")
        return redirect(url_for('index'))

    new_lot_number = request.form.get('new_paper_lot_number', '').strip()

    if not new_lot_number:
        flash("New paper lot number cannot be empty.", "danger")
        return redirect(url_for('work_interface', session_id=session_id))

    if session.status == 'Completed':
        flash("Cannot change lot for a completed session.", "warning")
        return redirect(url_for('work_interface', session_id=session.id))

    if session.active_paper_lot_number:
        old_lot_log = get_current_active_lot_log(session.id, session.active_paper_lot_number)
        if old_lot_log:
            old_lot_log.end_time_for_lot = datetime.utcnow()
            db.session.add(old_lot_log)

    session.active_paper_lot_number = new_lot_number

    new_lot_log = PaperLotUsageLog(
        cutting_session_id=session.id,
        paper_lot_number=new_lot_number,
        cuts_made_with_this_lot=0
    )
    db.session.add(session)
    db.session.add(new_lot_log)
    db.session.commit()

    flash(f"Paper lot changed to '*{new_lot_number}*' for WO **{session.work_order_code}**.", "success")
    return redirect(url_for('work_interface', session_id=session_id))

@app.route('/session/<int:session_id>/complete_manually', methods=['POST'])
def complete_session_manually(session_id):
    session = db.session.get(CuttingSession, session_id)
    if not session:
        flash("Session not found.", "danger")
        return redirect(url_for('index'))

    session.status = 'Completed'

    if session.active_paper_lot_number:
        current_lot_log = get_current_active_lot_log(session.id, session.active_paper_lot_number)
        if current_lot_log and not current_lot_log.end_time_for_lot:
            current_lot_log.end_time_for_lot = datetime.utcnow()
            db.session.add(current_lot_log)

    db.session.add(session)
    db.session.commit()

    report_dir = os.path.join('static', 'reports')
    os.makedirs(report_dir, exist_ok=True)
    filename = f"report_session_{session.id}.pdf"
    output_path = os.path.join(report_dir, filename)

    print(f"\n--- Debugging Data before Zoho Upload (Manual Completion, Session: {session.id}) ---")
    print(f"Session Work Order Code: {session.work_order_code}")
    print(f"Session Model No: {session.model_no}")
    print(f"Session Total Cuts Completed: {session.total_cuts_completed_for_session}")
    print(f"Session Status: {session.status}")
    print(f"Session Current Step: {session.current_step}")

    lot_history = PaperLotUsageLog.query.filter_by(cutting_session_id=session.id).order_by(
        PaperLotUsageLog.start_time_for_lot).all()
    print(f"Lot History (count): {len(lot_history)}")
    for i, lot in enumerate(lot_history):
        print(
            f"  Lot {i + 1}: Lot Number={lot.paper_lot_number}, Cuts={lot.cuts_made_with_this_lot}, Start Time={lot.start_time_for_lot}, End Time={lot.end_time_for_lot}")
    print(f"--- Debugging Data End ---")

    generate_session_pdf(app, session, lot_history, output_path)

    wo_display_name = session.work_order_code

    success = create_zoho_coil_cutting_report_entry(
        session.work_order_code,
        wo_display_name,
        session,
        lot_history
    )
    if success:
        flash(
            f"Session for Work Order **{session.work_order_code}** manually marked as completed and **combined report uploaded to Zoho Creator**.",
            "success")
    else:
        flash(
            f"Session for Work Order **{session.work_order_code}** manually marked as completed, but **failed to upload combined report to Zoho Creator**. Check server logs for details.",
            "warning")

    return redirect(url_for('index'))

@app.route('/completed_sessions')
def completed_sessions():
    completed_sessions = CuttingSession.query.filter_by(status='Completed').order_by(
        CuttingSession.updated_at.desc()).all()
    return render_template('completed_sessions.html', sessions=completed_sessions)

@app.route('/clear_completed_sessions', methods=['POST'])
def clear_completed_sessions():
    try:
        completed_sessions_to_delete = CuttingSession.query.filter_by(status='Completed').all()
        deleted_count = len(completed_sessions_to_delete)

        if deleted_count > 0:
            for session in completed_sessions_to_delete:
                db.session.delete(session)
            db.session.commit()
            flash(f"Successfully cleared {deleted_count} completed sessions and their associated data.", "success")
        else:
            flash("No completed sessions found to clear.", "info")

    except Exception as e:
        db.session.rollback()
        flash(f"Error clearing completed sessions: {str(e)}", "danger")
        print(f"Error clearing completed sessions: {e}")

    return redirect(url_for('completed_sessions'))

@app.route('/session/<int:session_id>/download_csv')
def download_csv(session_id):
    session = db.session.get(CuttingSession, session_id)
    if not session:
        flash("Session not found.", "danger")
        return redirect(url_for('completed_sessions'))

    lot_history = PaperLotUsageLog.query.filter_by(cutting_session_id=session.id).order_by(
        PaperLotUsageLog.start_time_for_lot).all()

    def generate():
        data = [
            ['Work Order', 'Model No', 'Total Cuts', 'Status', 'Step', 'Lot', 'Cuts With Lot', 'Start Time', 'End Time']
        ]
        for lot in lot_history:
            data.append([
                session.work_order_code,
                session.model_no,
                session.total_cuts_completed_for_session,
                session.status,
                session.current_step,
                lot.paper_lot_number,
                lot.cuts_made_with_this_lot,
                lot.start_time_for_lot.strftime('%Y-%m-%d'),
                lot.end_time_for_lot.strftime('%Y-%m-%d ') if lot.end_time_for_lot else 'Active'
            ])
        output = io.StringIO()
        writer = csv.writer(output)
        for row in data:
            writer.writerow(row)
        output.seek(0)
        yield output.getvalue()

    response = Response(generate(), mimetype='text/csv')
    response.headers["Content-Disposition"] = f"attachment;filename=session_{session.id}_report.csv"
    return response

@app.cli.command("init-db")
def init_db_command():
    with app.app_context():
        db.create_all()
    print("Initialized the database and created tables.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)