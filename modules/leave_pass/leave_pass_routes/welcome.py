from flask import Flask, Blueprint, render_template


welcome_routes = Blueprint('welcome_routes', __name__)

@welcome_routes.route('/', methods=['GET'])
def welcome():
    return render_template('welcome.html')