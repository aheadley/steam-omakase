#!/usr/bin/env python2

import os
# import logging

import flask
import flask_bootstrap
import steamapi
import requests

import omakase_util

app = flask.Flask(__name__)
flask_bootstrap.Bootstrap(app)

# http://steamcommunity.com/dev/apikey
STEAM_API_KEY = os.environ.get('STEAM_API_KEY')

steamapi.core.APIConnection(api_key=STEAM_API_KEY)

@app.route('/')
def index():
    return flask.render_template('index.html')

@app.route('/user/search/<query_string>', methods='POST')
def select_user(query_string):
    steam_user = steamapi.user.SteamUser(userurl=query_string)
    return flask.redirect(flask.url_for('select_friends',
        user_id=steam_user.id))

@app.route('/user/<int:user_id>')
def select_friends(user_id):
    steam_user = steamapi.user.SteamUser(userid=user_id)

    return flask.render_template('select_friends.html',
        steam_user=steam_user, steam_friends=steam_user.friends)

@app.route('/games', methods='POST')
def find_games():
    return flask.render_template('find_games.html')

if __name__ == '__main__':
    app.run(debug=True)
