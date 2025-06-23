from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()  # Initialize SQLAlchemy


class CuttingSession(db.Model):
    __tablename__ = 'cutting_session'
    id = db.Column(db.Integer, primary_key=True)
    work_order_code = db.Column(db.String(100), nullable=False)
    model_no = db.Column(db.String(100), nullable=False)

    total_cuts_planned = db.Column(db.Integer, default=68)
    cuts_planned_step1 = db.Column(db.Integer, default=34)
    cuts_planned_step2 = db.Column(db.Integer, default=34)



    current_step = db.Column(db.Integer, default=1)
    cuts_completed_in_current_step = db.Column(db.Integer, default=0)
    total_cuts_completed_for_session = db.Column(db.Integer, default=0)

    active_paper_lot_number = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(50), default='Ongoing')  # e.g., Ongoing, Paused, Completed

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship: A cutting session can have multiple paper lot usages
    lot_usages = db.relationship('PaperLotUsageLog', backref='cutting_session', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<CuttingSession {self.id} - WO: {self.work_order_code}, Model: {self.model_no}>"


class PaperLotUsageLog(db.Model):
    __tablename__ = 'paper_lot_usage_log'
    id = db.Column(db.Integer, primary_key=True)
    cutting_session_id = db.Column(db.Integer, db.ForeignKey('cutting_session.id'), nullable=False)
    paper_lot_number = db.Column(db.String(100), nullable=False)
    cuts_made_with_this_lot = db.Column(db.Integer, default=0)
    start_time_for_lot = db.Column(db.DateTime, default=datetime.utcnow)
    end_time_for_lot = db.Column(db.DateTime, nullable=True)  # Set when this lot is finished or changed

    def __repr__(self):
        return f"<PaperLotUsageLog {self.id} - Session: {self.cutting_session_id}, Lot: {self.paper_lot_number}, Cuts: {self.cuts_made_with_this_lot}>"



