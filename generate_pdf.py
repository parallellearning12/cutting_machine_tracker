# generate_pdf.py
from xhtml2pdf import pisa
from flask import render_template
import os

def generate_session_pdf(app, session, lot_history, output_path):
    with app.app_context():
        html = render_template("pdf_template.html", session=session, lot_history=lot_history)
        with open(output_path, "wb") as pdf_file:
            pisa.CreatePDF(html, dest=pdf_file)
