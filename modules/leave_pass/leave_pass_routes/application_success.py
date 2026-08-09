from flask import Flask, Blueprint, request, render_template, redirect, flash, url_for, current_app, session
from datetime import datetime



application_success_routes = Blueprint('application_success_routes', __name__)


@application_success_routes.route('/application-success')
def application_success():
    # Get reference ID from URL parameter
    ref_id = request.args.get('ref_id')
    
    # Or get from session if not in URL
    if not ref_id:
        ref_id = session.get('last_reference_id')
    
    if not ref_id:
        flash("No application found.", "error")
        return redirect(url_for('index'))
    
    return render_template('application_success.html', 
                         reference_id=ref_id,
                         applicant=session.get('applicant'))


# def application_success():
    
#     ref = session.pop('last_application_id', None)
    
#     return render_template('application_success.html', ref=ref)